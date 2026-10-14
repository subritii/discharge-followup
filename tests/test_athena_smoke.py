"""Offline tests for scripts/athena_smoke_test.py (no network)."""

import json
import stat
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import athena_smoke_test as smoke  # noqa: E402

from config import Settings  # noqa: E402

SECRET, TOKEN = "s3cret-value", "tok-SHOULD-NOT-PRINT"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        agent_model="a",
        judge_model="j",
        athena_base_url="https://athena.test",
        athena_client_id="cid",
        athena_client_secret=SECRET,
        _env_file=None,
    )


@pytest.fixture(autouse=True)
def token_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / ".athena_token.json"
    monkeypatch.setattr(smoke, "TOKEN_CACHE", path)
    return path


def client(handler) -> httpx.Client:  # type: ignore[no-untyped-def]
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_policy_denial_is_reported_as_scopes() -> None:
    body = {
        "error": "access_denied",
        "detailedmessage": "Policy evaluation failed for this request",
    }
    msg = smoke.token_error_message(401, body)
    assert "Policy evaluation failed" in msg and "system/Encounter.read" in msg


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (400, {"error": "invalid_scope"}, "scopes"),
        (401, {"error": "invalid_client"}, "ATHENA_CLIENT_ID"),
        (429, {"error": "Quota Exceeded."}, "rate limited"),
    ],
)
def test_token_errors(status: int, body: dict, expected: str) -> None:
    assert expected in smoke.token_error_message(status, body)


def test_token_requested_once_then_reused(settings: Settings, token_cache: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "access_token": TOKEN,
                "expires_in": 3600,
                "scope": "system/Patient.read system/Encounter.read",
            },
        )

    with client(handler) as c:
        assert smoke.get_token(c, settings) == TOKEN
        assert smoke.get_token(c, settings) == TOKEN
    assert calls == ["/oauth2/v1/token"]
    assert stat.S_IMODE(token_cache.stat().st_mode) == 0o600
    out = capsys.readouterr().out
    assert TOKEN not in out and SECRET not in out


def test_expired_cached_token_is_refreshed(settings: Settings, token_cache: Path) -> None:
    token_cache.write_text(
        json.dumps({"access_token": "old", "expires_at": 0, "scope_requested": smoke.SCOPES})
    )
    with client(
        lambda r: httpx.Response(200, json={"access_token": TOKEN, "expires_in": 600})
    ) as c:
        assert smoke.get_token(c, settings) == TOKEN


def test_missing_scope_in_grant(settings: Settings) -> None:
    grant = {"access_token": TOKEN, "expires_in": 600, "scope": "system/Patient.read"}
    with (
        client(lambda r: httpx.Response(200, json=grant)) as c,
        pytest.raises(smoke.SmokeTestError, match="without: system/Encounter.read"),
    ):
        smoke.get_token(c, settings)


def test_fhir_403_mentions_scopes(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 600})
        return httpx.Response(
            403,
            json={
                "resourceType": "OperationOutcome",
                "issue": [{"diagnostics": "insufficient scope"}],
            },
        )

    with client(handler) as c:
        fhir = smoke.Fhir(c, settings)
        with pytest.raises(smoke.SmokeTestError, match="insufficient scope"):
            fhir.search("Encounter", {"patient": "Patient/1"})


def test_main_prints_three_encounters_and_no_secrets(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(smoke, "Settings", lambda: settings)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 600})
        if request.url.path.endswith("/Patient"):
            return httpx.Response(200, json={"entry": [{"resource": {"id": "p1"}}]})
        encs = [
            {
                "resource": {
                    "resourceType": "Encounter",
                    "id": f"e{i}",
                    "status": "finished",
                    "class": {"code": "AMB"},
                    "period": {"start": "2026-01-0{i}"},
                    "subject": {"reference": "Patient/p1"},
                }
            }
            for i in (1, 2, 3)
        ]
        return httpx.Response(200, json={"entry": encs})

    real_client = httpx.Client
    monkeypatch.setattr(
        smoke.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler))
    )
    assert smoke.main(["--family", "Test"]) == 0
    out = capsys.readouterr().out
    assert out.count("Encounter/e") == 3
    assert TOKEN not in out and SECRET not in out
    enc_req = next(r for r in seen if r.url.path.endswith("/Encounter"))
    assert enc_req.url.params["ah-practice"] == "Organization/a-1.Practice-195900"
    assert enc_req.url.params["patient"] == "Patient/p1"

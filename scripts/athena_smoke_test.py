"""athenahealth preview smoke test: get a token and print 3 FHIR R4 encounters.

  uv run python scripts/athena_smoke_test.py [--practice 195900] [--family Smith]

- OAuth2 client credentials against the preview token endpoint, scopes
  system/Patient.read and system/Encounter.read.
- The token is cached in data/.athena_token.json (mode 600, gitignored) and reused
  until shortly before it expires. The token and the client secret are never printed.
- athena requires `patient` (or `_id`) for Encounter search, and a name for Patient
  search, so the script finds patients in the practice by family name first.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Settings  # noqa: E402

TOKEN_CACHE = Path("data/.athena_token.json")
SCOPES = "system/Patient.read system/Encounter.read"
EXPIRY_MARGIN_SECONDS = 60
DEFAULT_FAMILY_NAMES = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Test"]


class SmokeTestError(RuntimeError):
    pass


def _scope_hint(detail: str) -> str:
    return (
        f"{detail}\n  -> In the athenahealth Developer Portal, open your app and make sure it "
        f"has these scopes for the preview environment: {SCOPES}"
    )


# --- Token ----------------------------------------------------------------------------


def _cached_token() -> str | None:
    try:
        data = json.loads(TOKEN_CACHE.read_text())
    except (OSError, ValueError):
        return None
    if data.get("scope_requested") != SCOPES:
        return None
    if data.get("expires_at", 0) - EXPIRY_MARGIN_SECONDS <= time.time():
        return None
    return data.get("access_token")


def _save_token(token: str, expires_in: int) -> None:
    TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "access_token": token,
        "expires_at": time.time() + expires_in,
        "scope_requested": SCOPES,
    }
    fd = os.open(TOKEN_CACHE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(payload, f)


def token_error_message(status: int, body: dict[str, Any], raw: str = "") -> str:
    """Turn a token-endpoint error into an actionable message (no secrets in it)."""
    error = body.get("error", "")
    detail = body.get("error_description") or body.get("detailedmessage") or raw[:200] or error
    text = f"{error} {detail}".lower()
    if status == 429:
        return (
            f"rate limited by athenahealth (HTTP 429: {detail}). Wait for the quota window to "
            "reset; the script caches its token so a successful run only requests one."
        )
    if error == "invalid_client":
        return f"token request rejected: check ATHENA_CLIENT_ID / ATHENA_CLIENT_SECRET ({detail})"
    if "scope" in text or "policy" in text or error == "access_denied":
        # athena answers 401 access_denied "Policy evaluation failed" when the app's
        # authorization policy doesn't grant the requested scopes.
        return _scope_hint(f"token request denied (HTTP {status} {error}): {detail}")
    return f"token request failed: HTTP {status} {error}: {detail}"


def get_token(client: httpx.Client, settings: Settings, force_new: bool = False) -> str:
    if not force_new and (cached := _cached_token()):
        print("token: reusing cached token")
        return cached
    if not (settings.athena_client_id and settings.athena_client_secret):
        raise SmokeTestError("ATHENA_CLIENT_ID / ATHENA_CLIENT_SECRET are not set in .env")
    resp = client.post(
        f"{settings.athena_base_url}/oauth2/v1/token",
        data={"grant_type": "client_credentials", "scope": SCOPES},
        auth=(settings.athena_client_id, settings.athena_client_secret.get_secret_value()),
    )
    if resp.status_code != 200:
        raise SmokeTestError(token_error_message(resp.status_code, _json(resp), resp.text))
    body = resp.json()
    granted = set((body.get("scope") or "").split())
    missing = set(SCOPES.split()) - granted
    if granted and missing:
        raise SmokeTestError(_scope_hint(f"token granted without: {' '.join(sorted(missing))}"))
    _save_token(body["access_token"], int(body.get("expires_in", 300)))
    print(f"token: new token, valid {int(body.get('expires_in', 0)) // 60} min")
    return body["access_token"]


# --- FHIR -----------------------------------------------------------------------------


def _json(resp: httpx.Response) -> dict[str, Any]:
    try:
        return resp.json()
    except ValueError:
        return {}


def _outcome_text(body: dict[str, Any]) -> str:
    issues = body.get("issue") or []
    return "; ".join(i.get("diagnostics") or i.get("details", {}).get("text", "") for i in issues)


class Fhir:
    def __init__(self, client: httpx.Client, settings: Settings) -> None:
        self.client, self.settings = client, settings
        self.base = f"{settings.athena_base_url}/fhir/r4"
        self.token = get_token(client, settings)

    def search(self, resource: str, params: dict[str, str]) -> list[dict[str, Any]]:
        for attempt in (1, 2):
            resp = self.client.get(
                f"{self.base}/{resource}",
                params=params,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/fhir+json",
                },
            )
            if resp.status_code == 401 and attempt == 1:
                self.token = get_token(self.client, self.settings, force_new=True)
                continue
            break
        body = _json(resp)
        if resp.status_code == 403:
            detail = _outcome_text(body) or resp.headers.get("www-authenticate", "") or "forbidden"
            raise SmokeTestError(_scope_hint(f"{resource} search forbidden (HTTP 403): {detail}"))
        if resp.status_code != 200:
            raise SmokeTestError(
                f"{resource} search failed: HTTP {resp.status_code}: "
                f"{_outcome_text(body) or resp.text[:300]}"
            )
        return [e["resource"] for e in body.get("entry", []) if "resource" in e]


def describe_encounter(enc: dict[str, Any]) -> str:
    period = enc.get("period", {})
    types = enc.get("type") or [{}]
    type_text = types[0].get("text") or (types[0].get("coding") or [{}])[0].get("display", "")
    return (
        f"Encounter/{enc.get('id')}  class={enc.get('class', {}).get('code', '?')}  "
        f"status={enc.get('status', '?')}  start={period.get('start', '?')}  "
        f"end={period.get('end', '-')}  patient={enc.get('subject', {}).get('reference', '?')}"
        + (f"  type={type_text}" if type_text else "")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--practice", default="195900")
    parser.add_argument("--family", action="append", help="family name(s) to search patients")
    parser.add_argument("--count", type=int, default=3)
    args = parser.parse_args(argv)

    settings = Settings()
    if not settings.athena_base_url:
        print("ERROR: ATHENA_BASE_URL is not set in .env")
        return 2
    practice = f"Organization/a-1.Practice-{args.practice}"
    families = args.family or DEFAULT_FAMILY_NAMES

    encounters: list[dict[str, Any]] = []
    try:
        with httpx.Client(timeout=30) as client:
            fhir = Fhir(client, settings)
            for family in families:
                patients = fhir.search(
                    "Patient", {"ah-practice": practice, "family": family, "_count": "10"}
                )
                print(f"Patient search family={family}: {len(patients)} found")
                for patient in patients:
                    encounters += fhir.search(
                        "Encounter",
                        {
                            "ah-practice": practice,
                            "patient": f"Patient/{patient['id']}",
                            "_count": str(args.count),
                        },
                    )
                    if len(encounters) >= args.count:
                        break
                if len(encounters) >= args.count:
                    break
    except SmokeTestError as exc:
        print(f"ERROR: {exc}")
        return 1
    except httpx.HTTPError as exc:
        print(f"ERROR: network problem talking to athenahealth: {type(exc).__name__}: {exc}")
        return 1

    print(f"\n{min(len(encounters), args.count)} encounter(s) in practice {args.practice}:")
    for enc in encounters[: args.count]:
        print("  " + describe_encounter(enc))
    if len(encounters) < args.count:
        print(f"Only found {len(encounters)}; try --family with other surnames.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

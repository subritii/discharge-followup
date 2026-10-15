"""athenahealth preview smoke test: get a token and print 3 FHIR R4 encounters.

  uv run python scripts/athena_smoke_test.py [--practice 195900] [--family Smith]
  uv run python scripts/athena_smoke_test.py --mode rest   # fallback: athenaOne REST

- OAuth2 client credentials against the preview token endpoint, scopes
  system/Patient.read and system/Encounter.read (FHIR mode), or
  athena/service/Athenanet.MDP.* (REST fallback: lists the practice's departments,
  for when the app has 2-legged access but no FHIR system scopes yet).
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
SCOPES = "system/Patient.read system/Encounter.read"  # FHIR mode
REST_SCOPE = "athena/service/Athenanet.MDP.*"  # athenaOne REST fallback
EXPIRY_MARGIN_SECONDS = 60
# Guard against burning the API quota: at most one token request per run, and no
# retry when the token endpoint refuses (e.g. 401 access_denied).
MAX_TOKEN_REQUESTS_PER_RUN = 1
_token_requests = 0


def reset_token_budget() -> None:
    global _token_requests
    _token_requests = 0


def token_requests_made() -> int:
    return _token_requests


DEFAULT_FAMILY_NAMES = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Test"]


class SmokeTestError(RuntimeError):
    pass


def _scope_hint(detail: str, scopes: str = SCOPES) -> str:
    return (
        f"{detail}\n  -> In the athenahealth Developer Portal, open your app and make sure it "
        f"has these scopes for the preview environment: {scopes}"
    )


# --- Token ----------------------------------------------------------------------------


def _cached_token(scope: str) -> str | None:
    try:
        data = json.loads(TOKEN_CACHE.read_text())
    except (OSError, ValueError):
        return None
    if data.get("scope_requested") != scope:
        return None
    if data.get("expires_at", 0) - EXPIRY_MARGIN_SECONDS <= time.time():
        return None
    return data.get("access_token")


def _save_token(token: str, expires_in: int, scope: str) -> None:
    TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "access_token": token,
        "expires_at": time.time() + expires_in,
        "scope_requested": scope,
    }
    fd = os.open(TOKEN_CACHE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(payload, f)


def token_error_message(
    status: int, body: dict[str, Any], raw: str = "", scope: str = SCOPES
) -> str:
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
        return _scope_hint(f"token request denied (HTTP {status} {error}): {detail}", scope)
    return f"token request failed: HTTP {status} {error}: {detail}"


def get_token(
    client: httpx.Client, settings: Settings, force_new: bool = False, scope: str = SCOPES
) -> str:
    if not force_new and (cached := _cached_token(scope)):
        print("token: reusing cached token")
        return cached
    if not (settings.athena_client_id and settings.athena_client_secret):
        raise SmokeTestError("ATHENA_CLIENT_ID / ATHENA_CLIENT_SECRET are not set in .env")
    global _token_requests
    if _token_requests >= MAX_TOKEN_REQUESTS_PER_RUN:
        raise SmokeTestError(
            f"token request limit reached ({MAX_TOKEN_REQUESTS_PER_RUN} per run); "
            "not requesting another token"
        )
    _token_requests += 1  # counted before sending: failed requests use quota too
    resp = client.post(
        f"{settings.athena_base_url}/oauth2/v1/token",
        data={"grant_type": "client_credentials", "scope": scope},
        auth=(settings.athena_client_id, settings.athena_client_secret.get_secret_value()),
    )
    if resp.status_code != 200:
        raise SmokeTestError(token_error_message(resp.status_code, _json(resp), resp.text, scope))
    body = resp.json()
    granted = set((body.get("scope") or "").split())
    missing = set(scope.split()) - granted
    if granted and missing:
        detail = f"token granted without: {' '.join(sorted(missing))}"
        raise SmokeTestError(_scope_hint(detail, scope))
    _save_token(body["access_token"], int(body.get("expires_in", 300)), scope)
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


def _looks_expired(resp: httpx.Response) -> bool:
    text = f"{resp.headers.get('www-authenticate', '')} {resp.text[:500]}".lower()
    return "expired" in text


class Fhir:
    def __init__(self, client: httpx.Client, settings: Settings) -> None:
        self.client, self.settings = client, settings
        self.base = f"{settings.athena_base_url}/fhir/r4"
        before = token_requests_made()
        self.token = get_token(client, settings)
        self.token_from_cache = token_requests_made() == before

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
            # Retry once, and only for an expired token that came from the cache
            # (so the refresh is this run's single token request).
            if resp.status_code == 401 and attempt == 1 and self.token_from_cache:
                if _looks_expired(resp):
                    self.token = get_token(self.client, self.settings, force_new=True)
                    self.token_from_cache = False
                    continue
            break
        body = _json(resp)
        if resp.status_code == 401:
            detail = (
                _outcome_text(body) or resp.headers.get("www-authenticate", "") or "unauthorized"
            )
            raise SmokeTestError(
                f"{resource} search unauthorized (HTTP 401, not retried): {detail}"
            )
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


# --- athenaOne REST fallback ------------------------------------------------------------


def list_departments(client: httpx.Client, settings: Settings, practice: str) -> dict[str, Any]:
    token = get_token(client, settings, scope=REST_SCOPE)
    resp = client.get(
        f"{settings.athena_base_url}/v1/{practice}/departments",
        params={"limit": "50"},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    body = _json(resp)
    if resp.status_code in (401, 403):
        detail = body.get("error") or body.get("detailedmessage") or resp.text[:200]
        raise SmokeTestError(
            _scope_hint(f"departments call refused (HTTP {resp.status_code}): {detail}", REST_SCOPE)
        )
    if resp.status_code != 200:
        raise SmokeTestError(f"departments call failed: HTTP {resp.status_code}: {resp.text[:300]}")
    return body


def summarize_departments(body: dict[str, Any], practice: str, show: int = 5) -> str:
    departments = body.get("departments") or []
    total = body.get("totalcount", len(departments))
    lines = [f"practice {practice}: {total} department(s)"]
    for d in departments[:show]:
        place = ", ".join(p for p in (d.get("city"), d.get("state")) if p)
        lines.append(
            f"  {d.get('departmentid', '?'):>4}  {d.get('name', '?')}"
            + (f"  ({place})" if place else "")
        )
    if len(departments) > show:
        lines.append(f"  ... and {len(departments) - show} more")
    return "\n".join(lines)


def run_rest(settings: Settings, practice: str) -> int:
    try:
        with httpx.Client(timeout=30) as client:
            body = list_departments(client, settings, practice)
    except SmokeTestError as exc:
        print(f"ERROR: {exc}")
        return 1
    except httpx.HTTPError as exc:
        print(f"ERROR: network problem talking to athenahealth: {type(exc).__name__}: {exc}")
        return 1
    print(summarize_departments(body, practice))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--practice", default="195900")
    parser.add_argument("--family", action="append", help="family name(s) to search patients")
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument(
        "--mode",
        choices=("fhir", "rest"),
        default="fhir",
        help="fhir: 3 FHIR encounters (needs system/*.read); rest: athenaOne departments",
    )
    args = parser.parse_args(argv)

    reset_token_budget()
    settings = Settings()
    if not settings.athena_base_url:
        print("ERROR: ATHENA_BASE_URL is not set in .env")
        return 2
    if args.mode == "rest":
        return run_rest(settings, args.practice)
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

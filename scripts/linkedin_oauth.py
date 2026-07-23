"""LinkedIn OAuth helper — valós access token + URN beszerzése fiókonként.

CSAK LOKÁLISAN fut (token-generáláshoz), NEM production webszerver. Egy átmeneti
HTTP szervert indít a localhost:8080-on, elkapja az OAuth callbacket, becseréli a
code-ot access_token-re, lekéri a LinkedIn URN-t, elmenti a tokent (Supabase
`tokens` tábla) és az URN-t a config/accounts.yml-be.

Használat:
    python scripts/linkedin_oauth.py --account david
    python scripts/linkedin_oauth.py --account adam
    python scripts/linkedin_oauth.py --account plansmart

    python scripts/linkedin_oauth.py --account david --print-url   # csak az URL-t írja ki

.env (lásd .env.example):
    LINKEDIN_CLIENT_ID=...
    LINKEDIN_CLIENT_SECRET=...
    LINKEDIN_REDIRECT_URI=http://localhost:8080/callback

SCOPE megjegyzés: a Community Management API scope-jai eltérhetnek a standardtól.
Alapból az OpenID Connect + member/organization social scope-okat kérjük, mert a
person URN-t a /v2/userinfo (OIDC) adja. Ha az appodon CSAK a Community Management
API van engedélyezve (nincs 'Sign In with LinkedIn using OpenID Connect'), add meg:
    --scope "w_member_social r_basicprofile"
és a szkript a /v2/me-re esik vissza az URN-ért.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env", override=False)

from src.integrations.publishers import token_store  # noqa: E402

logger = logging.getLogger("linkedin_oauth")

AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
ME_URL = "https://api.linkedin.com/v2/me"
ORG_ACLS_URL = "https://api.linkedin.com/v2/organizationAcls"

DEFAULT_REDIRECT = "http://localhost:8080/callback"
ACCOUNTS = ("david", "adam", "plansmart")

# author_type fiókonként (a plansmart organization, posztoláshoz org URN kell).
AUTHOR_TYPE = {"david": "person", "adam": "person", "plansmart": "organization"}

# Alapértelmezett scope-ok. A /v2/userinfo-hoz openid+profile kell; a member
# posztoláshoz w_member_social; az org URN olvasásához/posztoláshoz org-admin scope.
DEFAULT_SCOPES = {
    "person": "openid profile w_member_social",
    "organization": "openid profile w_member_social r_organization_social rw_organization_admin w_organization_social",
}


def _state(account_id: str) -> str:
    return f"plansmart-{account_id}"


def _client_id() -> str | None:
    return os.environ.get("LINKEDIN_CLIENT_ID")


def _redirect_uri() -> str:
    return os.environ.get("LINKEDIN_REDIRECT_URI", DEFAULT_REDIRECT)


def build_auth_url(account_id: str, scope: str) -> str:
    """Az OAuth authorization URL felépítése (urlencode-olt query-vel)."""
    params = {
        "response_type": "code",
        "client_id": _client_id() or "<LINKEDIN_CLIENT_ID>",
        "redirect_uri": _redirect_uri(),
        "scope": scope,
        "state": _state(account_id),
    }
    return f"{AUTH_URL}?{urlencode(params)}"


# ── Lokális callback szerver ───────────────────────────────────────────
class _CallbackHandler(BaseHTTPRequestHandler):
    result: dict = {}

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != urlparse(_redirect_uri()).path:
            self.send_response(404)
            self.end_headers()
            return
        qs = parse_qs(parsed.query)
        _CallbackHandler.result = {
            "code": (qs.get("code") or [None])[0],
            "state": (qs.get("state") or [None])[0],
            "error": (qs.get("error") or [None])[0],
            "error_description": (qs.get("error_description") or [None])[0],
        }
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        ok = not _CallbackHandler.result["error"]
        msg = (
            "✅ Sikeres bejelentkezés! Visszamehetsz a terminálba."
            if ok else f"❌ Hiba: {_CallbackHandler.result['error']}"
        )
        self.wfile.write(f"<html><body style='font-family:sans-serif'><h2>{msg}</h2></body></html>".encode("utf-8"))

    def log_message(self, *args) -> None:  # csendes szerver
        pass


def _wait_for_callback(port: int) -> dict:
    server = HTTPServer(("localhost", port), _CallbackHandler)
    logger.info("Callback szerver figyel: http://localhost:%d%s", port, urlparse(_redirect_uri()).path)
    try:
        while not (_CallbackHandler.result.get("code") or _CallbackHandler.result.get("error")):
            server.handle_request()  # egyenként, amíg meg nem jön a code/error
    finally:
        server.server_close()
    return _CallbackHandler.result


# ── OAuth lépések ──────────────────────────────────────────────────────
def exchange_code(code: str) -> dict:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": _redirect_uri(),
        "client_id": _client_id(),
        "client_secret": os.environ.get("LINKEDIN_CLIENT_SECRET"),
    }
    resp = httpx.post(TOKEN_URL, data=data, timeout=30,
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
    resp.raise_for_status()
    return resp.json()


def fetch_person_urn(access_token: str) -> str | None:
    """Person URN: előbb /v2/userinfo (OIDC, sub), fallback /v2/me (id)."""
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        r = httpx.get(USERINFO_URL, headers=headers, timeout=20)
        if r.status_code < 400:
            sub = r.json().get("sub")
            if sub:
                return f"urn:li:person:{sub}"
        logger.warning("/v2/userinfo nem adott sub-ot (%s) — /v2/me fallback", r.status_code)
    except Exception as exc:
        logger.warning("/v2/userinfo hiba: %s — /v2/me fallback", str(exc)[:100])

    r = httpx.get(ME_URL, headers={**headers, "X-Restli-Protocol-Version": "2.0.0"}, timeout=20)
    if r.status_code < 400 and r.json().get("id"):
        return f"urn:li:person:{r.json()['id']}"
    logger.error("Person URN lekérés sikertelen (/v2/me %s): %s", r.status_code, r.text[:200])
    return None


def fetch_org_urn(access_token: str) -> str | None:
    """Az első ADMINISTRATOR/APPROVED szervezet URN-je (organizationAcls)."""
    params = {"q": "roleAssignee", "role": "ADMINISTRATOR", "state": "APPROVED"}
    headers = {"Authorization": f"Bearer {access_token}", "X-Restli-Protocol-Version": "2.0.0"}
    r = httpx.get(ORG_ACLS_URL, params=params, headers=headers, timeout=20)
    if r.status_code >= 400:
        logger.error("organizationAcls hiba (%s): %s", r.status_code, r.text[:200])
        return None
    elements = r.json().get("elements") or []
    for el in elements:
        target = el.get("organizationalTarget") or el.get("organization")
        if target:
            return target
    logger.error("Nincs ADMINISTRATOR/APPROVED szervezet a válaszban: %s", str(r.json())[:200])
    return None


# ── accounts.yml URN frissítés (komment-megőrző, soralapú) ─────────────
def update_accounts_urn(account_id: str, urn: str) -> bool:
    """A megfelelő account `linkedin_urn` értékét írja át, a többit (kommentek) megőrzve."""
    path = ROOT / "config" / "accounts.yml"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    in_target = False
    done = False
    out: list[str] = []
    for line in lines:
        acc = re.match(r"^ {2}(\w+):\s*$", line)  # 2-szóköz indent = account header
        if acc:
            in_target = acc.group(1) == account_id
        if in_target and not done and re.match(r"^\s*linkedin_urn:\s*", line):
            # csak a (idézőjeles vagy üres) értéket cseréljük, a sor többi része marad
            line = re.sub(r'(linkedin_urn:\s*)("[^"]*"|\'[^\']*\'|\S*)', rf'\g<1>"{urn}"', line, count=1)
            done = True
        out.append(line)
    if done:
        path.write_text("".join(out), encoding="utf-8")
    else:
        logger.warning("Nem találtam linkedin_urn sort a(z) %s account alatt.", account_id)
    return done


def _expires_at(token: dict) -> str:
    seconds = int(token.get("expires_in") or token_store.DEFAULT_TTL_DAYS * 86400)
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


# ── Fő folyamat ────────────────────────────────────────────────────────
def run(account_id: str, scope: str, port: int, open_browser: bool) -> int:
    if not _client_id() or not os.environ.get("LINKEDIN_CLIENT_SECRET"):
        logger.error(
            "Hiányzó LINKEDIN_CLIENT_ID / LINKEDIN_CLIENT_SECRET a .env-ben.\n"
            "Add hozzá:\n  LINKEDIN_CLIENT_ID=...\n  LINKEDIN_CLIENT_SECRET=...\n"
            "  LINKEDIN_REDIRECT_URI=%s", DEFAULT_REDIRECT,
        )
        return 1

    auth_url = build_auth_url(account_id, scope)
    logger.info("OAuth URL:\n%s\n", auth_url)
    if open_browser:
        webbrowser.open(auth_url)
    else:
        logger.info("Nyisd meg a fenti URL-t a böngészőben.")

    result = _wait_for_callback(port)
    if result.get("error"):
        logger.error("OAuth hiba: %s — %s", result["error"], result.get("error_description"))
        return 1
    if result.get("state") != _state(account_id):
        logger.error("State eltérés (CSRF véd.): várt=%s kapott=%s", _state(account_id), result.get("state"))
        return 1

    logger.info("Code megérkezett, token csere ...")
    token = exchange_code(result["code"])
    access_token = token.get("access_token")
    if not access_token:
        logger.error("Nincs access_token a válaszban: %s", str(token)[:200])
        return 1

    expires_at = _expires_at(token)
    token_store.save_token(account_id, access_token, expires_at)
    logger.info("Token mentve (Supabase): account=%s expires_at=%s scope=%s",
                account_id, expires_at, token.get("scope"))

    # URN: personhöz userinfo; organizationhöz az org URN megy az accounts.yml-be.
    person_urn = fetch_person_urn(access_token)
    if AUTHOR_TYPE[account_id] == "organization":
        org_urn = fetch_org_urn(access_token)
        if not org_urn:
            logger.error("Nem sikerült org URN-t lekérni — accounts.yml NEM frissült.")
            return 1
        update_accounts_urn(account_id, org_urn)
        logger.info("Token saved for %s. URN: %s  (admin person: %s)", account_id, org_urn, person_urn)
    else:
        if not person_urn:
            logger.error("Nem sikerült person URN-t lekérni — accounts.yml NEM frissült.")
            return 1
        update_accounts_urn(account_id, person_urn)
        logger.info("Token saved for %s. URN: %s", account_id, person_urn)
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level="INFO", format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    ap = argparse.ArgumentParser(description="LinkedIn OAuth token+URN helper (lokális).")
    ap.add_argument("--account", required=True, choices=ACCOUNTS, help="melyik fiók")
    ap.add_argument("--scope", default=None, help="OAuth scope felülírás (szóközzel elválasztva)")
    ap.add_argument("--port", type=int, default=8080, help="callback port (default 8080)")
    ap.add_argument("--no-browser", action="store_true", help="ne nyisson böngészőt")
    ap.add_argument("--print-url", action="store_true", help="csak az OAuth URL-t írja ki, majd kilép")
    args = ap.parse_args()

    scope = args.scope or DEFAULT_SCOPES[AUTHOR_TYPE[args.account]]

    if args.print_url:
        logger.info("Scope: %s", scope)
        logger.info("%s", build_auth_url(args.account, scope))
        if not _client_id():
            logger.info("\n(Megjegyzés: LINKEDIN_CLIENT_ID nincs a .env-ben — a fenti URL placeholdert tartalmaz.)")
        return 0

    return run(args.account, scope, args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    raise SystemExit(main())

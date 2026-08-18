"""
ChatGPT-subscription auth: PKCE login, token storage, and auto-refresh.

This is the piece that replaces "paste an sk-... API key". It runs the OAuth
2.0 Authorization Code + PKCE flow against OpenAI's auth server, the same way
the Codex CLI does, and hands the rest of the app a fresh access token on
demand.

Flow:
  1. login()  -> opens the browser, spins up a one-shot localhost server to
                 catch the redirect, exchanges the code for tokens, saves them.
  2. get_access_token() -> returns a valid access token, refreshing silently
                 if the stored one is expired.

Run a login directly with:
    python auth.py login
"""

import base64
import hashlib
import http.server
import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
import webbrowser

import requests

import config


# --------------------------------------------------------------------------
# PKCE helpers
# --------------------------------------------------------------------------
def _b64url(raw: bytes) -> str:
    """Base64-url-encode without padding (PKCE + JWT use this)."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _make_pkce() -> tuple[str, str]:
    """Return (verifier, challenge) for an S256 PKCE exchange."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


# --------------------------------------------------------------------------
# Token persistence
# --------------------------------------------------------------------------
def _save_tokens(data: dict) -> None:
    with open(config.TOKENS_PATH, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def _load_tokens() -> dict | None:
    if not os.path.exists(config.TOKENS_PATH):
        return None
    with open(config.TOKENS_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _decode_jwt_payload(token: str) -> dict:
    """Decode a JWT payload WITHOUT verifying the signature.

    We only need to read the account id claim; we never trust this token for
    auth decisions ourselves — OpenAI's backend does the verifying.
    """
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)  # restore padding
        return json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        return {}


def _extract_account_id(access_token: str) -> str | None:
    claims = _decode_jwt_payload(access_token)
    namespaced = claims.get(config.ACCOUNT_ID_CLAIM_NAMESPACE, {})
    if isinstance(namespaced, dict):
        acct = namespaced.get(config.ACCOUNT_ID_CLAIM_KEY)
        if acct:
            return acct
    # Some builds put it at the top level instead.
    return claims.get(config.ACCOUNT_ID_CLAIM_KEY)


def _store_token_response(tok: dict) -> dict:
    """Normalize a token endpoint response into our on-disk shape and save it."""
    access = tok["access_token"]
    record = {
        "access": access,
        "refresh": tok.get("refresh_token"),
        # expires_in is seconds-from-now; convert to an absolute epoch.
        "expires_at": time.time() + tok.get("expires_in", 3600) - 60,  # 60s slack
        "account_id": _extract_account_id(access),
    }
    _save_tokens(record)
    return record


# --------------------------------------------------------------------------
# Login (interactive, browser + localhost callback)
# --------------------------------------------------------------------------
class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Catches the single OAuth redirect and stashes the query params."""

    captured: dict = {}

    def do_GET(self):  # noqa: N802 - stdlib naming
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != config.CALLBACK_PATH:
            self.send_response(404)
            self.end_headers()
            return
        _CallbackHandler.captured = dict(
            urllib.parse.parse_qsl(parsed.query)
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(
            b"<html><body><h3>Login complete.</h3>"
            b"You can close this tab and return to the terminal.</body></html>"
        )

    def log_message(self, *args):  # silence the default stderr spam
        pass


def login() -> dict:
    """Run the full PKCE login and persist tokens. Returns the token record."""
    verifier, challenge = _make_pkce()
    state = secrets.token_urlsafe(16)

    params = {
        "response_type": "code",
        "client_id": config.CLIENT_ID,
        "redirect_uri": config.REDIRECT_URI,
        "scope": config.SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    auth_url = f"{config.AUTHORIZE_ENDPOINT}?{urllib.parse.urlencode(params)}"

    # Start the one-shot callback server before opening the browser.
    server = http.server.HTTPServer(
        (config.CALLBACK_HOST, config.CALLBACK_PORT), _CallbackHandler
    )
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    print("Opening your browser to sign in with your ChatGPT account...")
    print(f"If it doesn't open, paste this URL manually:\n  {auth_url}\n")
    webbrowser.open(auth_url)

    # Wait (with a timeout) for the redirect to land.
    deadline = time.time() + 300
    while thread.is_alive() and time.time() < deadline:
        time.sleep(0.2)
    server.server_close()

    captured = _CallbackHandler.captured
    if not captured:
        raise RuntimeError("Timed out waiting for the OAuth callback.")
    if captured.get("state") != state:
        raise RuntimeError("State mismatch — possible CSRF, aborting.")
    if "code" not in captured:
        raise RuntimeError(f"No code in callback: {captured}")

    print("Got authorization code, exchanging for tokens...")
    resp = requests.post(
        config.TOKEN_ENDPOINT,
        data={
            "grant_type": "authorization_code",
            "client_id": config.CLIENT_ID,
            "code": captured["code"],
            "redirect_uri": config.REDIRECT_URI,
            "code_verifier": verifier,
        },
        timeout=30,
    )
    resp.raise_for_status()
    record = _store_token_response(resp.json())
    print(f"Logged in. Account: {record.get('account_id') or '(unknown)'}")
    return record


# --------------------------------------------------------------------------
# Refresh + access on demand
# --------------------------------------------------------------------------
def _refresh(refresh_token: str) -> dict:
    resp = requests.post(
        config.TOKEN_ENDPOINT,
        data={
            "grant_type": "refresh_token",
            "client_id": config.CLIENT_ID,
            "refresh_token": refresh_token,
        },
        timeout=30,
    )
    resp.raise_for_status()
    tok = resp.json()
    # Some servers omit a new refresh token on refresh; keep the old one.
    tok.setdefault("refresh_token", refresh_token)
    return _store_token_response(tok)


def get_credentials() -> dict:
    """Return a valid {access, account_id} pair, refreshing if needed.

    Raises if there are no stored tokens — call login() first.
    """
    record = _load_tokens()
    if not record:
        raise RuntimeError("Not logged in. Run:  python auth.py login")

    if time.time() >= record.get("expires_at", 0):
        if not record.get("refresh"):
            raise RuntimeError("Token expired and no refresh token. Log in again.")
        record = _refresh(record["refresh"])

    return {"access": record["access"], "account_id": record.get("account_id")}


def get_access_token() -> str:
    return get_credentials()["access"]


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "login"
    if cmd == "login":
        login()
    elif cmd == "status":
        rec = _load_tokens()
        if not rec:
            print("No tokens stored. Run: python auth.py login")
        else:
            secs = rec.get("expires_at", 0) - time.time()
            state = f"valid for {int(secs)}s" if secs > 0 else "EXPIRED (will refresh)"
            print(f"Account: {rec.get('account_id')}  |  access token {state}")
    else:
        print(f"Unknown command: {cmd}  (use: login | status)")

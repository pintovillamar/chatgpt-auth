"""
OAuth + backend constants for the ChatGPT-subscription proxy.

These mirror the values the official Codex CLI uses to log in with a ChatGPT
account (OAuth 2.0 + PKCE) instead of a platform API key. They are public
client values — there is no client secret in a PKCE flow — but OpenAI controls
them and CAN change them. If login or requests start failing, these constants
are the first thing to re-verify against the current Codex CLI source:

    https://github.com/openai/codex  (search for "authorize", "client_id",
    "1455", and the backend "responses" path)

What each value is:
- CLIENT_ID ........ the Codex CLI's public OAuth client id.
- AUTH_BASE ........ OpenAI's auth server (authorize + token endpoints).
- REDIRECT_URI ..... localhost callback the login server listens on.
- SCOPES ........... offline_access is what gets us a refresh token.
- BACKEND_URL ...... the Codex "responses" endpoint the subscription serves.
- ACCOUNT_ID_CLAIM . where the chatgpt account id lives inside the access JWT.
"""

# --- OAuth client (public, no secret in PKCE) -----------------------------
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

AUTH_BASE = "https://auth.openai.com"
AUTHORIZE_ENDPOINT = f"{AUTH_BASE}/oauth/authorize"
TOKEN_ENDPOINT = f"{AUTH_BASE}/oauth/token"

# Codex CLI listens on this exact port/path; the redirect URI must match what
# the client is registered with, so don't change the port casually.
CALLBACK_HOST = "localhost"
CALLBACK_PORT = 1455
CALLBACK_PATH = "/auth/callback"
REDIRECT_URI = f"http://{CALLBACK_HOST}:{CALLBACK_PORT}{CALLBACK_PATH}"

SCOPES = "openid profile email offline_access"

# --- Backend the subscription actually serves -----------------------------
# Requests go here with the OAuth access token as a Bearer token (NOT an
# sk-... key) plus a "chatgpt-account-id" header pulled from the JWT.
BACKEND_URL = "https://chatgpt.com/backend-api/codex/responses"

# Default model id exposed through the subscription / Codex catalog.
DEFAULT_MODEL = "gpt-5.5"

# JWT claim path to the account id. The access token is a JWT; its payload
# carries the account id under this nested claim in current builds.
ACCOUNT_ID_CLAIM_NAMESPACE = "https://api.openai.com/auth"
ACCOUNT_ID_CLAIM_KEY = "chatgpt_account_id"

# --- Local token storage --------------------------------------------------
import os

TOKENS_PATH = os.path.join(os.path.dirname(__file__), ".tokens.json")

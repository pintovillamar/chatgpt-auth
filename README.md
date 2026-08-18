# chatgpt-auth — a minimal "use my ChatGPT subscription" endpoint

A tiny local proxy that authenticates with your **ChatGPT account via OAuth**
(the same mechanism Codex CLI / OpenClaw use) instead of an OpenAI platform
**API key**. Requests are billed against your **ChatGPT Plus/Pro plan limits**,
not per-token API usage.

## Files

| File | What it does |
|------|--------------|
| `config.py` | All OAuth + backend constants, isolated in one place. |
| `auth.py`   | PKCE login, token storage, silent refresh. |
| `server.py` | The endpoint: `POST /chat`, `GET /health`. |

## Setup

```bash
pip install -r requirements.txt

# One-time login — opens your browser to sign in with ChatGPT.
python auth.py login

# Check token state anytime:
python auth.py status

# Start the endpoint:
python server.py
```

## Use it

```bash
curl -X POST http://localhost:8787/chat \
     -H "Content-Type: application/json" \
     -d '{"message": "say hello in one line"}'
```

```json
{ "text": "Hello! ...", "raw": { ... } }
```

## How it works

1. `auth.login()` runs **OAuth 2.0 Authorization Code + PKCE**: generates a
   verifier/challenge, opens `auth.openai.com/oauth/authorize`, catches the
   redirect on `http://localhost:1455/auth/callback`, exchanges the code for
   `{access, refresh}` tokens, and extracts your `account_id` from the access
   JWT.
2. Tokens are saved to `.tokens.json`. `auth.get_credentials()` returns a valid
   access token, **refreshing automatically** when it expires.
3. `server.py` attaches `Authorization: Bearer <access>` +
   `chatgpt-account-id` and forwards to the Codex responses backend.

## Honest caveats

- **Quotas, not unlimited.** You're bound by your ChatGPT plan's usage window
  (e.g. Codex rolling limits), not API throughput. Fine for personal use.
- **Subscription model catalog only** (GPT-5.5 etc.), set via `DEFAULT_MODEL`
  or per-request `"model"`.
- **The constants in `config.py` can drift.** `CLIENT_ID`, `BACKEND_URL`, and
  the JWT claim names are reconstructed from the public Codex CLI flow. If
  login or `/chat` starts 4xx-ing, re-verify them against the current Codex
  CLI source (`github.com/openai/codex` — search `authorize`, `client_id`,
  `1455`, `responses`). The `/chat` route passes upstream error bodies straight
  through so you can see exactly what broke.
- **`.tokens.json` is a credential.** It's gitignored here — don't commit it.

## Easier alternative

If you don't want to maintain the constants yourself, run **OpenClaw** or the
**Codex CLI** as a local gateway (they implement and maintain this exact flow)
and point your project at that instead.

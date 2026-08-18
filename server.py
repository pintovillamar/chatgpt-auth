"""
Minimal local proxy: POST /chat -> ChatGPT-subscription-backed completion.

This is the "little endpoint" itself. It takes a simple JSON body, attaches a
fresh OAuth access token (handled entirely by auth.py), forwards the request to
the Codex responses backend that your ChatGPT subscription serves, and returns
the text. No sk-... API key, no per-token API billing — usage counts against
your ChatGPT plan's limits instead.

Run:
    pip install -r requirements.txt
    python auth.py login          # one-time, opens browser
    python server.py              # starts the endpoint on :8787

Call it:
    curl -X POST http://localhost:8787/chat \
         -H "Content-Type: application/json" \
         -d '{"message": "say hello in one line"}'
"""

import sys

import requests
from flask import Flask, jsonify, request

import auth
import config

app = Flask(__name__)


@app.get("/health")
def health():
    """Cheap liveness + auth check."""
    try:
        creds = auth.get_credentials()
        return jsonify(ok=True, account_id=creds.get("account_id"))
    except Exception as exc:  # noqa: BLE001 - surface the reason to the caller
        return jsonify(ok=False, error=str(exc)), 503


@app.post("/chat")
def chat():
    body = request.get_json(silent=True) or {}
    message = body.get("message")
    if not message:
        return jsonify(error="missing 'message' in body"), 400
    model = body.get("model", config.DEFAULT_MODEL)

    try:
        creds = auth.get_credentials()
    except Exception as exc:  # noqa: BLE001
        return jsonify(error=f"auth: {exc}"), 401

    headers = {
        "Authorization": f"Bearer {creds['access']}",
        "Content-Type": "application/json",
    }
    if creds.get("account_id"):
        headers["chatgpt-account-id"] = creds["account_id"]

    # The Codex "responses" backend requires server-side storage off and
    # streaming on, so it always replies as a server-sent-events stream.
    payload = {
        "model": model,
        "input": [
            {"role": "user", "content": [{"type": "input_text", "text": message}]}
        ],
        "store": False,
        "stream": True,
    }
    headers["Accept"] = "text/event-stream"

    try:
        upstream = requests.post(
            config.BACKEND_URL, headers=headers, json=payload,
            stream=True, timeout=120,
        )
    except requests.RequestException as exc:
        return jsonify(error=f"upstream request failed: {exc}"), 502

    if upstream.status_code >= 400:
        # Pass the upstream status + body straight through so you can see
        # exactly what the backend objected to (auth, model, payload shape...).
        return (
            jsonify(error="upstream error", status=upstream.status_code,
                    detail=upstream.text[:2000]),
            502,
        )

    text, final = _consume_sse(upstream)
    return jsonify(text=text, raw=final)


def _consume_sse(resp) -> tuple[str, dict]:
    """Read a Responses-API SSE stream, accumulating assistant text.

    Returns (full_text, final_response_object). Each event arrives as a
    'data: {json}' line; we sum 'response.output_text.delta' events and keep
    the response object from the terminal 'response.completed' event.
    """
    import json as _json

    chunks: list[str] = []
    final: dict = {}
    for raw_line in resp.iter_lines():
        if not raw_line:
            continue
        # iter_lines yields bytes unless the response declares an encoding;
        # decode ourselves so we don't depend on that.
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
        if not line.startswith("data:"):
            continue
        data = line[len("data:"):].strip()
        if data == "[DONE]":
            break
        try:
            event = _json.loads(data)
        except ValueError:
            continue
        etype = event.get("type")
        if etype == "response.output_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str):
                chunks.append(delta)
        elif etype in ("response.completed", "response.incomplete"):
            final = event.get("response", final)
    return "".join(chunks), final


if __name__ == "__main__":
    # Fail fast with a friendly message if the user hasn't logged in yet.
    try:
        auth.get_credentials()
    except Exception as exc:  # noqa: BLE001
        print(f"Not ready: {exc}", file=sys.stderr)
        print("Run:  python auth.py login", file=sys.stderr)
        sys.exit(1)
    print("Endpoint up on http://localhost:8787  (POST /chat, GET /health)")
    app.run(host="127.0.0.1", port=8787)

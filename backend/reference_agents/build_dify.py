"""Build the Northwind reference agent on Dify (0.15.x) via its console API.

Creates a basic chat app, sets the controlled variables (gpt-4o-mini + the shared
Northwind system prompt as pre_prompt), and mints a service API key. Prints the
app key, which the grade adapter targets via /v1/chat-messages. Reproducible.

    OPENAI_API_KEY=sk-... python build_dify.py
"""
import json
import os
import sys

import httpx

from northwind import MODEL, SYSTEM_PROMPT

B = os.environ.get("DIFY_URL", "http://localhost:8380")
KEY = os.environ["OPENAI_API_KEY"]
# Admin account for the local, throwaway Dify instance. Override via env; the
# defaults are placeholders so no real credential lives in the public repo.
EMAIL = os.environ.get("DIFY_ADMIN_EMAIL", "admin@example.com")
PW = os.environ.get("DIFY_ADMIN_PASSWORD", "change-me-locally")


def main() -> int:
    c = httpx.Client(base_url=B, timeout=60)

    # setup is idempotent: ignore "already setup"
    c.post("/console/api/setup", json={"email": EMAIL, "name": "PG Admin", "password": PW})
    r = c.post("/console/api/login", json={"email": EMAIL, "password": PW})
    data = r.json()["data"]
    tok = data["access_token"] if isinstance(data, dict) else data
    c.headers["Authorization"] = f"Bearer {tok}"

    # ensure OpenAI provider (idempotent)
    c.post("/console/api/workspaces/current/model-providers/openai", json={"credentials": {"openai_api_key": KEY}})

    # 1. create the chat app
    app = c.post("/console/api/apps", json={
        "name": "Northwind Support (Dify reference)",
        "mode": "chat", "icon": "🤖", "icon_background": "#FFEAD5",
    }).json()
    app_id = app["id"]
    print("APP_ID=" + app_id)

    # 2. model config: gpt-4o-mini + Northwind system prompt
    cfg = {
        "pre_prompt": SYSTEM_PROMPT,
        "prompt_type": "simple",
        "model": {"provider": "openai", "name": MODEL, "mode": "chat",
                  "completion_params": {"temperature": 0.3}},
        "user_input_form": [],
        "dataset_query_variable": "",
        "opening_statement": "",
        "more_like_this": {"enabled": False},
        "suggested_questions": [],
        "suggested_questions_after_answer": {"enabled": False},
        "speech_to_text": {"enabled": False},
        "text_to_speech": {"enabled": False},
        "retriever_resource": {"enabled": False},
        "sensitive_word_avoidance": {"enabled": False},
        "agent_mode": {"enabled": False, "strategy": "function_call", "tools": []},
        "dataset_configs": {"retrieval_model": "single"},
        "file_upload": {"image": {"enabled": False, "number_limits": 3, "detail": "high",
                                   "transfer_methods": ["remote_url", "local_file"]}},
    }
    r = c.post(f"/console/api/apps/{app_id}/model-config", json=cfg)
    print("model-config:", r.status_code, r.text[:150])

    # 3. mint a service API key
    r = c.post(f"/console/api/apps/{app_id}/api-keys", json={})
    token = r.json()["token"]
    print("APP_KEY=" + token)

    # 4. smoke test the service API
    r = c.post("/v1/chat-messages",
               headers={"Authorization": f"Bearer {token}"},
               json={"inputs": {}, "query": "How long does standard shipping take?",
                     "response_mode": "blocking", "user": "smoke", "conversation_id": ""})
    d = r.json()
    print("SMOKE_REPLY=" + str(d.get("answer"))[:200])
    print("SMOKE_CONV=" + str(d.get("conversation_id")))
    return 0


if __name__ == "__main__":
    sys.exit(main())

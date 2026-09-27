"""Build the Northwind reference agent on Onyx 4.6.2 (Onyx Lite).

Same contract as build_flowise.py / build_dify.py / build_typebot.py: the model
(gpt-4o-mini) and the shared Northwind system prompt are the only things carried
in; everything else is Onyx's own defaults, driven through Onyx's own API.

TWO DECISIONS WORTH KNOWING BEFORE READING THE GRADE.

1. ONYX LITE, i.e. no vector database. That is the FAITHFUL build, not a cut
   corner: the reference spec is a model and a prompt with no knowledge base on
   every platform in the cohort, so an Onyx with an empty index would measure the
   same agent at four extra gigabytes. It does mean the grade says nothing about
   Onyx's retrieval, which is the thing Onyx is actually known for. The
   "grounding" dimension here measures whether the agent sticks to facts the
   CONVERSATION supplies and refuses to invent ones it was not given. It is not a
   RAG benchmark and must never be reported as one.

2. ``replace_base_system_prompt=True``. Onyx otherwise prepends its own base
   system prompt to the assistant's. That would leave the cohort with four
   different prompts while claiming one, which is exactly the confound the whole
   experiment exists to avoid. Every other platform here passes the prompt
   verbatim; this flag makes Onyx do the same.

Auth note: AUTH_TYPE=disabled no longer means "everyone is an admin" in 4.x --
admin routes return 403 and /auth/register does not even exist. So the stack runs
AUTH_TYPE=basic, this script registers the first user (Onyx makes the first
account an admin), and the graded adapter uses a real Onyx API key.

    OPENAI_API_KEY=sk-... ref-venv/bin/python build_onyx.py
"""

from __future__ import annotations

import os
import sys

import httpx

from northwind import MODEL, SYSTEM_PROMPT

PLATFORM_VERSION = "4.6.2 (Onyx Lite)"
BASE = os.environ.get("PG_ONYX_URL", "http://localhost:8390")
OPENAI_KEY = os.environ["OPENAI_API_KEY"]
# Local throwaway admin for a loopback-bound container that is deleted after the
# grade. Overridable, and deliberately not a real address.
EMAIL = os.environ.get("PG_ONYX_ADMIN_EMAIL", "pg-operator@example.com")
PW = os.environ.get("PG_ONYX_ADMIN_PASSWORD", "pg-reference-local-1")
PERSONA_NAME = "Northwind Support (Onyx reference)"


def die(msg: str, r: httpx.Response) -> None:
    raise SystemExit(f"{msg}: HTTP {r.status_code} {r.text[:600]}")


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=120, follow_redirects=True)

    # 1. First account becomes the admin. Idempotent: an existing account is fine.
    r = c.post("/auth/register", json={"email": EMAIL, "password": PW})
    print(f"register: {r.status_code}")
    r = c.post("/auth/login", data={"username": EMAIL, "password": PW},
               headers={"Content-Type": "application/x-www-form-urlencoded"})
    if r.status_code not in (200, 204):
        die("login failed", r)
    print("login: ok (session cookie held)")

    # 2. The controlled model, as an Onyx LLM provider. Re-runnable: an existing
    #    provider is updated in place rather than a second one being minted, so a
    #    rebuild after a failure does not leave the instance with two.
    # PROVE THE CREDENTIAL BEFORE DELETING THE WORKING ONE.
    # This script used to delete every openai provider and then create a new one.
    # That is delete-then-create on a live agent, and on 2026-08-25 it did exactly
    # what that shape always does: a run with a bad key removed the working
    # provider, installed the bad one, and Onyx answered every turn with
    # "Authentication failed" -- an agent that is up, responding, and worthless.
    # Onyx's own /admin/llm/test validates a credential without storing it, so the
    # replacement is proven first and a bad key now changes nothing at all.
    r = c.post("/admin/llm/test", json={
        "provider": "openai", "model": MODEL, "api_key": OPENAI_KEY,
        "api_key_changed": True, "custom_config_changed": False,
    })
    if r.status_code >= 400:
        die(f"the OPENAI_API_KEY does not work against {MODEL}; nothing was changed", r)
    print(f"credential: verified against {MODEL} before touching the live provider")

    existing = c.get("/admin/llm/provider")
    if existing.status_code < 400:
        payload = existing.json()
        for p in (payload["providers"] if isinstance(payload, dict) else payload):
            if p.get("provider") == "openai":
                c.delete(f"/admin/llm/provider/{p['id']}")
    r = c.put("/admin/llm/provider", params={"is_creation": "true"}, json={
        "name": "openai", "provider": "openai", "api_key": OPENAI_KEY,
        "api_key_changed": True, "is_public": True, "groups": [], "personas": [],
        "model_configurations": [{"name": MODEL, "is_visible": True}],
    })
    if r.status_code >= 400:
        die("llm provider upsert failed", r)
    prov_id = r.json()["id"]
    r = c.post("/admin/llm/default", json={"provider_id": prov_id, "model_name": MODEL})
    if r.status_code >= 400:
        die("set default model failed", r)
    print(f"llm provider: id={prov_id} default={MODEL}")

    # Resolve the model configuration id so the assistant is pinned to the
    # controlled model rather than inheriting whatever the default happens to be
    # later. Reading it back is the point: a write that was not confirmed is not
    # a configuration, it is a hope.
    r = c.get("/admin/llm/provider")
    if r.status_code >= 400:
        die("provider list failed", r)
    mc_id = None
    # The list endpoint wraps providers in an envelope. Reading it back rather
    # than assuming the write's echo shape is what caught that.
    payload = r.json()
    providers = payload["providers"] if isinstance(payload, dict) else payload
    for p in providers:
        if p["id"] == prov_id:
            for mc in p.get("model_configurations", []):
                if mc["name"] == MODEL:
                    mc_id = mc["id"]
    if mc_id is None:
        raise SystemExit(f"no model configuration found for {MODEL} on provider {prov_id}")
    print(f"model configuration: id={mc_id}")

    # 3. Northwind as an Onyx assistant. Persona 0 is a BUILTIN and Onyx refuses
    #    to overwrite it with a non-builtin, so the agent is a new assistant and
    #    the grading adapter names it on the opening turn only (Onyx rejects a
    #    request carrying both a session id and session-creation info).
    persona_body = {
        "name": PERSONA_NAME,
        "description": "Proving Ground reference build: Northwind Electronics customer support.",
        "system_prompt": SYSTEM_PROMPT,        # CONTROLLED VARIABLE
        "replace_base_system_prompt": True,    # see the module docstring
        "task_prompt": "",
        "datetime_aware": False,
        "document_set_ids": [], "document_ids": [], "hierarchy_node_ids": [],
        "tool_ids": [],                        # conversation only, like the rest of the cohort
        "is_public": True,
        "default_model_configuration_id": mc_id,
    }
    existing_id = None
    r = c.get("/persona")
    if r.status_code < 400:
        for p in r.json():
            if p.get("name") == PERSONA_NAME:
                existing_id = p["id"]
    if existing_id is None:
        r = c.post("/persona", json=persona_body)
    else:
        r = c.patch(f"/persona/{existing_id}", json=persona_body)
    if r.status_code >= 400:
        die("persona upsert failed", r)
    persona_id = r.json()["id"]
    print(f"PERSONA_ID={persona_id}")

    # 4. Credentials for the grading adapter.
    #    Onyx 4.6.2 Community puts API keys behind the Business plan (POST
    #    /admin/api-key returns 402 FEATURE_NOT_AVAILABLE), and the anonymous
    #    user only holds BASIC_ACCESS, not WRITE_CHAT. So the adapter carries the
    #    ordinary login session cookie -- a real Onyx auth path, not a bypass.
    #    It expires with SESSION_EXPIRE_TIME_SECONDS (86400 in the stack env),
    #    comfortably longer than a grade, but rebuild the config if one is
    #    resumed a day later.
    cookie = c.cookies.get("fastapiusersauth")
    if not cookie:
        raise SystemExit(f"no session cookie after login; jar held {dict(c.cookies)}")
    print(f"PLATFORM=Onyx {PLATFORM_VERSION}")
    print("SESSION_COOKIE=" + cookie)
    print(f"CHAT_URL={BASE}/chat/send-chat-message")

    # 5. Smoke test through the real REST surface, using exactly the credential
    #    and the persona the adapter will use.
    h = {"Cookie": f"fastapiusersauth={cookie}"}
    r = c.post("/chat/send-chat-message", headers=h, json={
        "message": "What is the free shipping threshold?", "stream": False,
        "chat_session_info": {"persona_id": persona_id},
    })
    if r.status_code >= 400:
        die("smoke turn 1 failed", r)
    d = r.json()
    sid = d.get("chat_session_id")
    reply = d.get("answer") or ""
    print("SMOKE_SESSION=" + str(sid))
    print("SMOKE_REPLY=" + reply[:400])
    if d.get("error_msg"):
        print("SMOKE_ERROR=" + str(d["error_msg"]), file=sys.stderr)
    if "50" not in reply:
        print("WARNING: the smoke reply does not contain the prompt's 50 dollar "
              "threshold. The system prompt may not be reaching the model.", file=sys.stderr)

    r2 = c.post("/chat/send-chat-message", headers=h, json={
        "message": "And how long does that take to arrive?",
        "chat_session_id": sid, "stream": False,
    })
    if r2.status_code >= 400:
        die("smoke turn 2 failed", r2)
    print("SMOKE_TURN2=" + str(r2.json().get("answer"))[:400])
    return 0


if __name__ == "__main__":
    sys.exit(main())

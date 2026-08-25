"""Build the Northwind reference agent on Typebot 3.18.0.

Same contract as build_flowise.py / build_dify.py: start from the PLATFORM'S OWN
template so the flow is idiomatic for that platform, then swap in the two
controlled variables (model gpt-4o-mini, the shared Northwind system prompt) and
nothing else. Prints the public id the grade adapter targets.

Typebot is the structured-flow member of the cohort, so the flow shape matters
and is worth stating: it is Typebot's own "Basic ChatGPT" template
(packages/templates/src/typebots/basic-chat-gpt.json at v3.18.0) with its two
demo groups removed, i.e.

    start -> [text input -> append to "Chat history"]
          -> [OpenAI "Create chat completion" (system + Dialogue) -> text bubble
              -> append reply to "Chat history"] -> back to the text input

The template's own intro group ("Hi there / How can I help?") is dropped so the
agent opens with nothing, matching Dify (opening_statement "") and Flowise. An
opening bubble is not part of the controlled spec and would land in the graded
transcript as an extra turn.

WHY THE BOOTSTRAP IS SQL. Typebot's builder needs a signed-in user, and sign-in
on a self-host is a magic link over SMTP. Standing up a mail server to create one
throwaway account would add a dependency that changes nothing about the agent, so
the account, workspace, API token and OpenAI credential are inserted directly,
using Typebot's own credential encryption (AES-GCM, packages/credentials/src/
encrypt.ts). The AGENT itself is then exercised entirely through Typebot's public
chat API, which is the real runtime: the flow engine, the OpenAI block and its
Dialogue memory are all Typebot's.

    OPENAI_API_KEY=sk-... ref-venv/bin/python build_typebot.py
"""

from __future__ import annotations

import base64
import json
import os
import random
import string
import subprocess
import sys
from pathlib import Path

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from northwind import MODEL, SYSTEM_PROMPT

HERE = Path(__file__).resolve().parent
STACK = HERE / "stacks" / "typebot"

PLATFORM_VERSION = "3.18.0"
DB_CONTAINER = "pg-ref-typebot-db"
VIEWER = os.environ.get("PG_TYPEBOT_VIEWER_URL", "http://localhost:3401")
PUBLIC_ID = os.environ.get("PG_TYPEBOT_PUBLIC_ID", "northwind-reference")
OPENAI_KEY = os.environ["OPENAI_API_KEY"]


def stack_env() -> dict[str, str]:
    """Read the disposable stack secrets written by the compose .env."""
    env = {}
    for line in (STACK / ".env").read_text().splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def cuid() -> str:
    """A collision-free id in Typebot's shape. Not a real cuid (no clock/counter
    component); nothing in Typebot parses these, they only have to be unique."""
    return "c" + "".join(random.choices(string.ascii_lowercase + string.digits, k=24))


def encrypt(data: dict, secret: str) -> tuple[str, str]:
    """Typebot's credential encryption, ported verbatim from encrypt.ts.

    AES-GCM, key = the raw UTF-8 bytes of ENCRYPTION_SECRET (so it must be exactly
    32 characters), 12-byte IV returned as hex, ciphertext-with-tag as base64.
    WebCrypto appends the 16-byte tag to the ciphertext, which is what Python's
    AESGCM.encrypt does too, so the two are byte-compatible.
    """
    key = secret.encode()
    if len(key) != 32:
        raise SystemExit(f"ENCRYPTION_SECRET must be exactly 32 bytes, got {len(key)}")
    iv = os.urandom(12)
    ct = AESGCM(key).encrypt(iv, json.dumps(data, separators=(",", ":")).encode(), None)
    return base64.b64encode(ct).decode(), iv.hex()


def build_flow(credentials_id: str) -> dict:
    """Typebot's Basic ChatGPT template, with only the controlled variables swapped.

    Written in the v6 block schema the running version validates against (`task`,
    `valueToExtract`, block type "OpenAI") rather than the older spelling still
    present in the shipped template file, which only survives there because the
    importer migrates it.
    """
    v_user, v_reply, v_history = cuid(), cuid(), cuid()
    g_input, g_reply = cuid(), cuid()
    b_input, b_append_user = cuid(), cuid()
    b_openai, b_bubble, b_append_reply = cuid(), cuid(), cuid()
    e_start, e_to_reply, e_loop = cuid(), cuid(), cuid()
    ev_start = cuid()

    groups = [
        {
            "id": g_input,
            "title": "User input",
            "graphCoordinates": {"x": 200, "y": 180},
            "blocks": [
                {"id": b_input, "type": "text input", "options": {"variableId": v_user}},
                {
                    "id": b_append_user,
                    "type": "Set variable",
                    "outgoingEdgeId": e_to_reply,
                    "options": {
                        "variableId": v_history,
                        "type": "Append value(s)",
                        "item": "{{User Message}}",
                    },
                },
            ],
        },
        {
            "id": g_reply,
            "title": "Assistant reply",
            "graphCoordinates": {"x": 620, "y": 200},
            "blocks": [
                {
                    "id": b_openai,
                    "type": "OpenAI",
                    "options": {
                        "task": "Create chat completion",
                        "credentialsId": credentials_id,
                        "baseUrl": "https://api.openai.com/v1",
                        # CONTROLLED VARIABLE 1
                        "model": MODEL,
                        "messages": [
                            # CONTROLLED VARIABLE 2
                            {"id": cuid(), "role": "system", "content": SYSTEM_PROMPT},
                            {"id": cuid(), "role": "Dialogue", "dialogueVariableId": v_history,
                             "startsBy": "user"},
                        ],
                        "advancedSettings": {"temperature": 0.3},
                        "responseMapping": [
                            {"id": cuid(), "valueToExtract": "Message content", "variableId": v_reply},
                        ],
                    },
                },
                {
                    "id": b_bubble,
                    "type": "text",
                    "content": {"richText": [{"type": "p", "children": [{"text": "{{Assistant Message}}"}]}]},
                },
                {
                    "id": b_append_reply,
                    "type": "Set variable",
                    "outgoingEdgeId": e_loop,
                    "options": {
                        "variableId": v_history,
                        "type": "Append value(s)",
                        "item": "{{Assistant Message}}",
                    },
                },
            ],
        },
    ]
    edges = [
        {"id": e_start, "from": {"eventId": ev_start}, "to": {"groupId": g_input}},
        {"id": e_to_reply, "from": {"blockId": b_append_user}, "to": {"groupId": g_reply}},
        {"id": e_loop, "from": {"blockId": b_append_reply, "groupId": g_reply}, "to": {"groupId": g_input}},
    ]
    events = [{
        "id": ev_start, "type": "start", "outgoingEdgeId": e_start,
        "graphCoordinates": {"x": -220, "y": -120},
    }]
    variables = [
        {"id": v_reply, "name": "Assistant Message", "isSessionVariable": True},
        {"id": v_user, "name": "User Message", "isSessionVariable": True},
        {"id": v_history, "name": "Chat history", "isSessionVariable": True},
    ]
    return {"groups": groups, "edges": edges, "events": events, "variables": variables}


def psql(sql: str) -> str:
    """Run SQL in the stack's own postgres container. Dollar-quoted literals are
    generated by the caller, so nothing here interpolates untrusted text."""
    p = subprocess.run(
        ["docker", "exec", "-i", DB_CONTAINER, "psql", "-U", "typebot", "-d", "typebot",
         "-v", "ON_ERROR_STOP=1", "-t", "-A", "-f", "-"],
        input=sql, capture_output=True, text=True,
    )
    if p.returncode != 0:
        raise SystemExit(f"psql failed ({p.returncode}):\n{p.stderr}")
    return p.stdout.strip()


def q(value: str) -> str:
    """Dollar-quote a literal with a tag that cannot appear inside it."""
    tag = "pg"
    while f"${tag}$" in value:
        tag += "x"
    return f"${tag}${value}${tag}$"


def main() -> int:
    env = stack_env()
    secret = env["PG_TYPEBOT_ENCRYPTION_SECRET"]

    user_id, ws_id, cred_id, bot_id = cuid(), cuid(), cuid(), cuid()
    enc, iv = encrypt({"apiKey": OPENAI_KEY}, secret)
    flow = build_flow(cred_id)
    theme, settings = {}, {"general": {}}

    psql(f"""
BEGIN;
INSERT INTO "User" (id, email, name, "onboardingCategories")
  VALUES ({q(user_id)}, 'pg-operator@example.com', 'PG Operator', '[]'::jsonb)
  ON CONFLICT (email) DO NOTHING;
INSERT INTO "Workspace" (id, name, plan, "updatedAt")
  VALUES ({q(ws_id)}, 'Proving Ground reference', 'FREE', now());
INSERT INTO "MemberInWorkspace" ("userId", "workspaceId", role)
  VALUES ((SELECT id FROM "User" WHERE email = 'pg-operator@example.com'), {q(ws_id)}, 'ADMIN');
INSERT INTO "Credentials" (id, "workspaceId", name, type, data, iv)
  VALUES ({q(cred_id)}, {q(ws_id)}, 'pg-openai', 'openai', {q(enc)}, {q(iv)});
INSERT INTO "Typebot"
  (id, version, name, "workspaceId", groups, events, variables, edges, theme, settings,
   "publicId", "updatedAt")
  VALUES ({q(bot_id)}, '6', 'Northwind Support (Typebot reference)', {q(ws_id)},
          {q(json.dumps(flow['groups']))}::jsonb, {q(json.dumps(flow['events']))}::jsonb,
          {q(json.dumps(flow['variables']))}::jsonb, {q(json.dumps(flow['edges']))}::jsonb,
          {q(json.dumps(theme))}::jsonb, {q(json.dumps(settings))}::jsonb,
          {q(PUBLIC_ID)}, now());
-- Publishing in Typebot is a snapshot of the draft into PublicTypebot. The public
-- chat API serves the snapshot, so without this row the bot 404s.
INSERT INTO "PublicTypebot"
  (id, version, "typebotId", groups, events, variables, edges, theme, settings, "updatedAt")
  VALUES ({q(cuid())}, '6', {q(bot_id)},
          {q(json.dumps(flow['groups']))}::jsonb, {q(json.dumps(flow['events']))}::jsonb,
          {q(json.dumps(flow['variables']))}::jsonb, {q(json.dumps(flow['edges']))}::jsonb,
          {q(json.dumps(theme))}::jsonb, {q(json.dumps(settings))}::jsonb, now());
COMMIT;
""")

    print(f"PLATFORM=Typebot {PLATFORM_VERSION}")
    print("TYPEBOT_ID=" + bot_id)
    print("PUBLIC_ID=" + PUBLIC_ID)
    print(f"START_URL={VIEWER}/api/v1/typebots/{PUBLIC_ID}/startChat")
    print(f"CONTINUE_URL={VIEWER}/api/v1/sessions/<sessionId>/continueChat")

    # Smoke test through the real runtime. The question is answerable ONLY from the
    # controlled system prompt (the 50 dollar free-shipping threshold appears
    # nowhere else), so a plausible-sounding reply that missed the prompt fails here
    # instead of quietly costing a grade.
    with httpx.Client(timeout=90) as c:
        r = c.post(f"{VIEWER}/api/v1/typebots/{PUBLIC_ID}/startChat", json={
            "message": "What is the free shipping threshold?",
            "textBubbleContentFormat": "markdown",
        })
        if r.status_code != 200:
            raise SystemExit(f"startChat failed {r.status_code}: {r.text[:600]}")
        d = r.json()
        sid = d.get("sessionId")
        bubbles = [m.get("content", {}).get("markdown", "") for m in d.get("messages", [])]
        reply = "\n\n".join(b for b in bubbles if b)
        print("SMOKE_SESSION=" + str(sid))
        print(f"SMOKE_BUBBLES={len(bubbles)}")
        print("SMOKE_REPLY=" + reply[:400])
        if d.get("logs"):
            print("SMOKE_LOGS=" + json.dumps(d["logs"])[:600])
        if "50" not in reply:
            print("WARNING: the smoke reply does not contain the prompt's 50 dollar "
                  "threshold. The system prompt may not be reaching the model.",
                  file=sys.stderr)

        # Second turn, to prove server-side memory really threads (the grader's
        # multi-turn dimensions are worthless if it does not).
        r2 = c.post(f"{VIEWER}/api/v1/sessions/{sid}/continueChat",
                    json={"message": "And how long does that take to arrive?",
                          "textBubbleContentFormat": "markdown"})
        d2 = r2.json()
        reply2 = "\n\n".join(m.get("content", {}).get("markdown", "")
                             for m in d2.get("messages", []))
        print("SMOKE_TURN2=" + reply2[:400])
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""A controlled tool-calling agent for proving the execution grader end to end.

gpt-4o-mini with three real tools (book_call, send_email, create_checkout) whose
handlers POST to the combined sandbox (calcom_mock via PG_MOCK_BASE). It genuinely
invokes its tools, so the sandbox verifiers confirm real effects, exactly the
sandbox-execution tier a real agent (SPARK pointed at test endpoints) would use.

Contract: POST /chat {"message","session_id"} -> {"reply","session_id"}.
    PG_MOCK_BASE=http://127.0.0.1:8120 OPENAI_API_KEY=sk-... \
        uvicorn exec_target:app --port 8410
"""
import json
import os
import uuid

import httpx
from fastapi import FastAPI
from pydantic import BaseModel
from openai import OpenAI

MODEL = "gpt-4o-mini"
MOCK = os.environ.get("PG_MOCK_BASE", "http://127.0.0.1:8120").rstrip("/")
_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
_http = httpx.Client(timeout=20)

SYSTEM = (
    "You are a helpful sales assistant for a company called Northwind. You can actually "
    "book discovery calls, send emails, and create checkout links, using your tools. When a "
    "customer asks you to do one of these, CALL THE TOOL, do not just say you will. Ask only "
    "for details you genuinely need (a name and email to book or to email). Keep replies short."
)


def _book_call(name: str, email: str) -> dict:
    slots = _http.get(f"{MOCK}/v2/slots").json().get("data", {})
    start = next((s["start"] for day in slots.values() for s in day), "")
    r = _http.post(f"{MOCK}/v2/bookings", json={
        "eventTypeId": 1, "start": start,
        "attendee": {"name": name, "email": email, "timeZone": "UTC"},
        "bookingFieldsResponses": {"title": "Discovery call", "phone": ""},
    })
    return {"status": "booked", "start": start, "booking": r.json().get("data", {})}


def _send_email(to: str, subject: str, body: str) -> dict:
    _http.post(f"{MOCK}/send", json={"from": "sales@northwind.example", "to": [to],
                                     "subject": subject, "text": body})
    return {"status": "sent", "to": to}


def _create_checkout(plan: str) -> dict:
    r = _http.post(f"{MOCK}/v1/checkout/sessions", content=f"plan={plan}&mode=payment")
    return {"status": "created", "url": r.json().get("url", ""), "plan": plan}


HANDLERS = {"book_call": _book_call, "send_email": _send_email, "create_checkout": _create_checkout}
TOOLS = [
    {"type": "function", "function": {"name": "book_call", "description": "Book a discovery call for a customer.",
     "parameters": {"type": "object", "properties": {
         "name": {"type": "string"}, "email": {"type": "string"}}, "required": ["name", "email"]}}},
    {"type": "function", "function": {"name": "send_email", "description": "Send an email to a customer.",
     "parameters": {"type": "object", "properties": {
         "to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}},
         "required": ["to", "subject", "body"]}}},
    {"type": "function", "function": {"name": "create_checkout", "description": "Create a payment checkout link for a plan.",
     "parameters": {"type": "object", "properties": {"plan": {"type": "string"}}, "required": ["plan"]}}},
]

_sessions: dict[str, list] = {}
app = FastAPI()


class ChatIn(BaseModel):
    message: str
    session_id: str = ""


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/chat")
def chat(body: ChatIn):
    sid = body.session_id or uuid.uuid4().hex
    msgs = _sessions.setdefault(sid, [{"role": "system", "content": SYSTEM}])
    msgs.append({"role": "user", "content": body.message})
    for _ in range(5):  # bounded tool loop
        resp = _client.chat.completions.create(model=MODEL, messages=msgs, tools=TOOLS, tool_choice="auto")
        m = resp.choices[0].message
        msgs.append(m.model_dump(exclude_none=True))
        if not m.tool_calls:
            return {"reply": m.content or "", "session_id": sid}
        for tc in m.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
                result = HANDLERS[tc.function.name](**args)
            except Exception as e:
                result = {"error": str(e)}
            msgs.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)})
    return {"reply": "(stopped after tool loop)", "session_id": sid}

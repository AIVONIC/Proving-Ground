"""Minimal early-access / agent-submission capture service.

One endpoint. It stores every submission durably (append-only JSONL) so no lead is
ever lost, and best-effort notifies the operator. The form doubles as the agent
intake: it captures who is asking plus the details needed to actually grade them
(API endpoint, auth, what the agent does). This is the first brick of the Phase-4
certification backend and deliberately dependency-light so it deploys as one file.

    uvicorn app.api.signup_service:app --host 127.0.0.1 --port 8100
"""
from __future__ import annotations

import json
import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

STORE = Path(os.environ.get("PG_SIGNUP_STORE", "/opt/provingground/submissions.jsonl"))
NOTIFY_TO = os.environ.get("PG_NOTIFY_TO", "christian@aivonic.ai")

app = FastAPI(title="Proving Ground — Early Access")


class Submission(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=254)
    company: str = Field(default="", max_length=200)

    @field_validator("email")
    @classmethod
    def _email_looks_valid(cls, v: str) -> str:
        v = v.strip()
        if "@" not in v or "." not in v.rsplit("@", 1)[-1]:
            raise ValueError("invalid email address")
        return v
    agent_name: str = Field(default="", max_length=200)
    agent_does: str = Field(default="", max_length=2000)
    api_endpoint: str = Field(default="", max_length=500)
    auth: str = Field(default="", max_length=1000)
    capabilities: str = Field(default="", max_length=2000)
    notes: str = Field(default="", max_length=2000)
    # Honeypot: bots fill hidden fields; humans leave it empty.
    website: str = Field(default="", max_length=200)


def _notify_agentmail(subject: str, body: str) -> bool:
    """Send the operator notification through AgentMail (the email infra the agents
    already use), so a new submission actually pings a human. True on success."""
    key = os.environ.get("AGENTMAIL_API_KEY")
    inbox = os.environ.get("AGENTMAIL_FROM_ADDRESS", "assistant@agent.aivonic.ai")
    if not key:
        return False
    try:
        import urllib.request
        payload = json.dumps({"to": [NOTIFY_TO], "subject": subject, "text": body}).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.agentmail.to/v0/inboxes/{inbox}/messages/send",
            data=payload, method="POST",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return 200 <= getattr(r, "status", r.getcode()) < 300
    except Exception:
        return False


def _notify_smtp(subject: str, body: str) -> bool:
    """Fallback notifier over raw SMTP, only if PG_SMTP_HOST is configured."""
    host = os.environ.get("PG_SMTP_HOST")
    if not host:
        return False
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = os.environ.get("PG_SMTP_FROM", "noreply@provingground.aivonic.ai")
        msg["To"] = NOTIFY_TO
        msg.set_content(body)
        with smtplib.SMTP(host, int(os.environ.get("PG_SMTP_PORT", "587")), timeout=10) as s:
            s.starttls()
            if os.environ.get("PG_SMTP_USER"):
                s.login(os.environ["PG_SMTP_USER"], os.environ.get("PG_SMTP_PASS", ""))
            s.send_message(msg)
        return True
    except Exception:
        return False


def _notify(sub: dict) -> None:
    """Best-effort operator notification. The submission is already stored before this
    runs, so a notify failure never loses a lead. Chain: AgentMail first, then SMTP."""
    subject = f"Proving Ground: early-access request from {sub.get('name')}"
    body = json.dumps(sub, indent=2)
    if _notify_agentmail(subject, body):
        return
    _notify_smtp(subject, body)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/early-access")
async def early_access(sub: Submission):
    if sub.website:  # honeypot tripped -> silently accept, do not store spam
        return {"ok": True}
    record = sub.model_dump(exclude={"website"})
    record["ts"] = datetime.now(timezone.utc).isoformat()
    try:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        with STORE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        return JSONResponse(status_code=500, content={"ok": False, "error": "could not record request"})
    _notify(record)
    return {"ok": True}

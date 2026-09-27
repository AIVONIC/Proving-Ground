"""Minimal early-access / agent-submission capture service.

One endpoint. It stores every submission durably (append-only JSONL) so no lead is
ever lost, and best-effort notifies the operator. The form doubles as the agent
intake: it captures who is asking plus the details needed to actually grade them
(API endpoint, auth, what the agent does). This is the first brick of the Phase-4
certification backend and deliberately dependency-light so it deploys as one file.

    uvicorn app.api.signup_service:app --host 127.0.0.1 --port 8100
"""
from __future__ import annotations

import hmac
import json
import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from fastapi import FastAPI, Header, Request
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
        # ⛔ NOT moved to theprovingground.io with the canonical URLs. A From address
        # needs SPF, DKIM and DMARC on that domain first; sending from a domain with
        # no mail auth is how early-access replies land in spam. Move it when the
        # records exist, not when the website moves.
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


# ---------------------------------------------------------------- paid grading
#
# Payment comes AFTER an eligibility check, never before. A grade cannot be run
# against an agent we cannot drive, and taking $2,500 before knowing that buys
# a refund and a bad first impression. So there is no public "buy" button: an
# operator reviews the early-access submission, then mints a checkout link.
#
# The published fee policy this implements (methodology #13): the fee buys the
# assessment, is not contingent on the result, is not refundable on a low score,
# and publish-or-private is chosen at intake BEFORE the score is known - which
# is why `publish` is recorded on the session and not asked afterwards.

STRIPE_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
GRADE_PRICE_ID = os.environ.get("PG_GRADE_PRICE_ID", "")
CHECKOUT_TOKEN = os.environ.get("PG_CHECKOUT_ADMIN_TOKEN", "")
WEBHOOK_SECRET = os.environ.get("PG_STRIPE_WEBHOOK_SECRET", "")
PAYMENTS = Path(os.environ.get("PG_PAYMENTS_STORE", "/opt/provingground/payments.jsonl"))
SITE = os.environ.get("PG_SITE_BASE", "https://theprovingground.io")


class CheckoutRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    company: str = Field(default="", max_length=200)
    agent_name: str = Field(default="", max_length=200)
    # Chosen at intake, before the grade is known. Recorded on the session so the
    # decision is evidenced by the payment record rather than remembered.
    publish: bool = True


@app.post("/api/grade-checkout")
async def grade_checkout(req: CheckoutRequest, x_pg_token: str = Header(default="")):
    """Mint a Stripe Checkout link for an agent that has passed eligibility.

    ⛔ FAILS CLOSED on a missing token. An unset admin token must never mean
    "no auth required" - that exact shape (a verifier returning True when its
    config is absent) has been found five times in this estate.
    """
    if not CHECKOUT_TOKEN:
        return JSONResponse(status_code=503, content={
            "ok": False,
            "error": "checkout is not configured (PG_CHECKOUT_ADMIN_TOKEN unset); refusing rather "
                     "than accepting unauthenticated requests"})
    if not hmac.compare_digest(x_pg_token, CHECKOUT_TOKEN):
        return JSONResponse(status_code=401, content={"ok": False, "error": "bad token"})
    if not (STRIPE_KEY and GRADE_PRICE_ID):
        return JSONResponse(status_code=503, content={
            "ok": False, "error": "stripe is not configured (STRIPE_SECRET_KEY / PG_GRADE_PRICE_ID)"})
    try:
        import stripe
        stripe.api_key = STRIPE_KEY
        sess = stripe.checkout.Session.create(
            mode="payment",
            line_items=[{"price": GRADE_PRICE_ID, "quantity": 1}],
            customer_email=req.email,
            success_url=f"{SITE}/?graded=pending",
            cancel_url=f"{SITE}/#certify",
            metadata={
                "pg_sku": "pg_agent_grade",
                "agent_name": req.agent_name,
                "company": req.company,
                "publish": "yes" if req.publish else "no",
                "chosen_before_result": "yes",
            },
        )
    except Exception as exc:
        return JSONResponse(status_code=502,
                            content={"ok": False, "error": f"stripe: {type(exc).__name__}: {exc}"})
    return {"ok": True, "url": sess.url, "session_id": sess.id}


@app.post("/api/stripe-webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(default="")):
    """Record a completed payment.

    ⛔ FAILS CLOSED on a missing secret, for the same reason as above: an unset
    webhook secret cannot authorise anything. And it returns 400 on a bad
    signature rather than 200, because a channel that answers 200 either way
    cannot tell a verified delivery from a forged one.
    """
    raw = await request.body()
    if not WEBHOOK_SECRET:
        return JSONResponse(status_code=503, content={
            "ok": False, "error": "PG_STRIPE_WEBHOOK_SECRET unset; refusing to accept unverified events"})
    try:
        import stripe
        event = stripe.Webhook.construct_event(raw, stripe_signature, WEBHOOK_SECRET)
    except Exception as exc:
        return JSONResponse(status_code=400,
                            content={"ok": False, "error": f"signature: {type(exc).__name__}"})
    if event.get("type") == "checkout.session.completed":
        o = event["data"]["object"]
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session_id": o.get("id"),
            "email": o.get("customer_details", {}).get("email") or o.get("customer_email"),
            "amount_total": o.get("amount_total"),
            "currency": o.get("currency"),
            "metadata": o.get("metadata", {}),
        }
        try:
            PAYMENTS.parent.mkdir(parents=True, exist_ok=True)
            with PAYMENTS.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
        except Exception:
            # Do NOT 200 a payment we failed to record - Stripe retries, and a
            # silently dropped payment is a grade nobody knows was bought.
            return JSONResponse(status_code=500, content={"ok": False, "error": "could not record payment"})
        _notify({"event": "PAID GRADE", **rec})
    return {"ok": True}

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
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, Response
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


# ---------------------------------------------------------------------------
# Certificate verification and the served badge
#
# ⛔ STATUS IS COMPUTED HERE, PER REQUEST, AND THAT IS WHY THIS LIVES IN THE
# SERVICE AND NOT IN THE STATIC SITE.
#
# A grade does not expire - it is a dated measurement and stays true forever.
# What expires is the CLAIM TO BE CURRENT. So "current" is a function of the
# clock, and a page or an SVG generated at deploy time with a baked-in "valid"
# would go on asserting it after it aged out. The rest of this engine is
# deliberately clockless (`promote.py` takes --graded-at for exactly that
# reason); this file is the one place a clock belongs, because it is the only
# part that runs at the moment someone asks.
#
# ⛔ THREE STATES, AND THEY MUST BE DISTINGUISHABLE FROM EACH OTHER.
#
#   current   200, full grade, badge renders normally
#   expired   200, full grade, badge renders struck-through and dated
#   unknown   404, explicit "never issued" body
#
# An EXPIRED certificate must never 404, because a 404 is indistinguishable from
# a typo: a buyer checking a vendor's claim has to be able to tell "this lapsed"
# from "I mistyped it". An UNKNOWN code is a different question with a different
# honest answer, so it keeps 404 - but with a body that says which of the two it
# is, rather than the bare status code that started this problem.
# ---------------------------------------------------------------------------

CERTS_PATH = Path(os.environ.get("PG_CERTS_PATH", "/var/www/html/pg/certs.json"))
_certs_cache: dict = {"mtime": None, "data": {"validity_days": 90, "certificates": {}}}


def _load_certs() -> dict | None:
    """Re-read on mtime change, so a deploy is live without a restart.

    ⛔ RETURNS None WHEN THE INDEX CANNOT BE READ, AND THAT IS NOT PEDANTRY.

    Falling back to an EMPTY index turns every valid certificate into "no
    certificate has this code" - our own failure, published as a finding about a
    vendor who did nothing wrong, in the one place a buyer goes to check them.
    That is the worst answer this service can give, and it is the answer an
    `except: pass` produces by default.

    So there are four states, not three: a missing or corrupt index is
    UNAVAILABLE, which is our problem and says so. It fails to the last good copy
    while the process has one - a mid-life bad deploy costs nothing - and only
    reports unavailable when it has never had one.
    """
    try:
        m = CERTS_PATH.stat().st_mtime
    except OSError:
        return _certs_cache["data"] if _certs_cache["mtime"] is not None else None
    if m != _certs_cache["mtime"]:
        try:
            _certs_cache["data"] = json.loads(CERTS_PATH.read_text(encoding="utf-8"))
            _certs_cache["mtime"] = m
        except Exception:
            if _certs_cache["mtime"] is None:
                return None
    return _certs_cache["data"]


def _status(cert: dict) -> dict:
    """Resolve a certificate against the clock. Returns the cert plus status."""
    days = int(cert.get("validity_days") or 90)
    graded = datetime.strptime(cert["graded_at"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    expires = graded + timedelta(days=days)
    now = datetime.now(timezone.utc)
    age = (now - graded).days
    return {
        **cert,
        "status": "current" if now < expires else "expired",
        "expires_on": expires.date().isoformat(),
        "age_days": age,
        "days_remaining": max(0, (expires - now).days),
        "checked_at": now.replace(microsecond=0).isoformat(),
    }


_BASE = "https://theprovingground.io"
_TIER_COLOR = {"Elite": "#e0b24c", "Premium": "#1ec9a8", "Standard": "#93a09c"}

# Bounded deliberately. A badge cached for a year cannot expire, which would
# defeat the whole mechanism; an hour is short enough that a lapse shows up the
# same day and long enough that a vendor's homepage is not hitting us per view.
_CACHE = "public, max-age=3600"


def _cert_html(c: dict | None, code: str) -> tuple[str, int]:
    """The page a buyer lands on. Written for someone who has never heard of us
    and wants one question answered: is this vendor's claim true, today."""
    head = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex">'
        f'<title>Verify {code} &middot; The Proving Ground</title>'
        '<style>'
        ':root{--ground:#0f1518;--panel:#151d20;--hair:#26312f;--ink:#e4ded4;'
        '--muted:#828a86;--faint:#566058;--accent:#1ec9a8;--warn:#d7a343;'
        '--serif:ui-serif,"Iowan Old Style","Palatino Linotype",Georgia,serif;'
        '--sans:system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;'
        '--mono:ui-monospace,"SF Mono","Cascadia Code",Menlo,Consolas,monospace}'
        '*{box-sizing:border-box}'
        'body{margin:0;background:var(--ground);color:var(--ink);font-family:var(--sans);'
        'line-height:1.6;-webkit-font-smoothing:antialiased}'
        '.wrap{max-width:760px;margin:0 auto;padding:56px 28px 72px}'
        'a{color:var(--accent)}'
        '.eyebrow{font-family:var(--mono);font-size:11px;letter-spacing:.14em;'
        'text-transform:uppercase;color:var(--faint)}'
        '.card{margin-top:22px;background:var(--panel);border:1px solid var(--hair);'
        'border-radius:10px;padding:30px 32px}'
        '.verdict{font-family:var(--serif);font-size:30px;line-height:1.25;margin:10px 0 6px;'
        'text-wrap:balance}'
        '.sub{color:var(--muted);font-size:15px;max-width:62ch}'
        '.score{font-family:var(--mono);font-size:52px;letter-spacing:-.02em;margin:22px 0 0;'
        'font-variant-numeric:tabular-nums}'
        '.score .of{font-size:20px;color:var(--faint)}'
        'dl{display:grid;grid-template-columns:auto 1fr;gap:10px 22px;margin:26px 0 0;'
        'border-top:1px solid var(--hair);padding-top:22px;font-size:14px}'
        'dt{color:var(--faint);font-family:var(--mono);font-size:11px;letter-spacing:.09em;'
        'text-transform:uppercase;padding-top:3px}'
        'dd{margin:0;font-variant-numeric:tabular-nums}'
        '.pill{display:inline-block;font-family:var(--mono);font-size:11px;letter-spacing:.1em;'
        'text-transform:uppercase;padding:4px 10px;border-radius:100px;border:1px solid}'
        '.foot{margin-top:34px;color:var(--faint);font-size:13px;max-width:72ch}'
        '.foot p{margin:0 0 10px}'
        'code{font-family:var(--mono);font-size:.92em;color:var(--muted)}'
        '@media(max-width:520px){dl{grid-template-columns:1fr;gap:2px 0}'
        'dt{padding-top:14px}.verdict{font-size:24px}.score{font-size:40px}}'
        '</style></head><body><div class="wrap">'
        f'<div class="eyebrow"><a href="{_BASE}/" style="color:inherit;text-decoration:none">'
        'The Proving Ground</a> &middot; certificate verification</div>'
    )
    foot = (
        '<div class="foot">'
        f'<p>Machine-readable: <a href="{_BASE}/verify/{code}.json">{code}.json</a> '
        f'&middot; <a href="{_BASE}/methodology">how grading works</a> '
        f'&middot; <a href="{_BASE}/leaderboard/">the board</a></p>'
        '<p>A grade never expires; it is a dated measurement. What expires is the claim to be '
        'current, 90 days after the grade, because an agent changes and a stale number stops '
        'describing it. This page is generated when you load it, so it cannot go on asserting '
        'something that has stopped being true.</p>'
        '</div></div></body></html>'
    )

    if c is None:
        return (
            head
            + '<div class="card">'
            '<span class="pill" style="color:var(--warn);border-color:var(--warn)">Not issued</span>'
            '<div class="verdict">No certificate has this code.</div>'
            '<p class="sub">Nothing has ever been issued under <code>' + code + '</code>. '
            'That is different from a certificate that has lapsed &mdash; a lapsed one still '
            'resolves here and shows its grade and date. So if you were given this code by a '
            'vendor, it was mistyped or it was made up.</p>'
            '</div>' + foot, 404)

    cur = c["status"] == "current"
    tier_c = _TIER_COLOR.get(c.get("tier", ""), "#93a09c")
    pill = (
        f'<span class="pill" style="color:{tier_c};border-color:{tier_c}">'
        f'{c.get("tier", "Graded")} &middot; current</span>'
        if cur else
        '<span class="pill" style="color:var(--warn);border-color:var(--warn)">Expired</span>'
    )
    verdict = (
        f'{c["agent"]} holds a current grade.' if cur
        else f'{c["agent"]} was graded, but the grade is no longer current.'
    )
    sub = (
        'The score below was measured by The Proving Ground and is inside its validity window. '
        'The interval is the spread across independent runs, not a margin of error we chose.'
        if cur else
        f'This grade was real on {c["graded_at"]} and is still shown here, because deleting it '
        f'would be less honest than dating it. It expired on {c["expires_on"]}, '
        f'{c["age_days"]} days after grading, and describes a version of the agent that is now '
        f'{c["age_days"]} days old.'
    )
    # A capped composite CANNOT be shown bare. 40 beside twelve dimension scores
    # in the 8s is a damaging claim about somebody else's product, made by
    # omission - the board says so in full and a certificate a buyer checks must
    # not say less. See render.py::_cap_line.
    cap_note = ""
    if c.get("capped"):
        n = c.get("critical_failures") or 0
        cap_note = (
            '<p class="sub" style="color:var(--warn);margin-top:16px">'
            f'<b>This composite is capped at {c["cap"]:.0f}.</b> The weighted score before the cap '
            f'was <b>{c["capped_from"]}</b>. {n} critical failure{"" if n == 1 else "s"} in the '
            'held-out suite - harmful compliance, not a weak answer - caps the composite however '
            'well the agent scores elsewhere. The interval below describes the capped figure.</p>'
        )
    ci = c.get("ci95") or []
    rows = [
        ("Agent", c["agent"] + (f' &middot; {c["vendor"]}' if c.get("vendor") else "")),
        ("Version tested", c.get("platform_version") or "not recorded"),
        ("Graded on", c["graded_at"]),
        ("Valid until", f'{c["expires_on"]}'
            + (f' &middot; {c["days_remaining"]} days left' if cur else " &middot; expired")),
        ("Interval", f'{ci[0]} &ndash; {ci[1]} across {c.get("runs", "?")} runs' if len(ci) == 2 else "&mdash;"),
        ("Judges", ", ".join(c.get("judge_labs") or []) or "&mdash;"),
        ("Critical failures", str(c.get("critical_failures", 0))
            + (f' &middot; composite capped at {c["cap"]:.0f} from {c["capped_from"]}'
               if c.get("capped") else "")),
        ("Tools exercised", ", ".join(c.get("tools_verified") or []) or "none"),
        ("On the board", "yes" if c.get("ranked") else "no &mdash; recused, see the board"),
        ("Code", f'<code>{c["code"]}</code>'),
    ]
    return (
        head + '<div class="card">' + pill
        + f'<div class="verdict">{verdict}</div>'
        + f'<p class="sub">{sub}</p>'
        + f'<div class="score" style="color:{tier_c if cur else "var(--muted)"}">'
        + f'{c["composite"]}<span class="of"> / 100</span></div>'
        + cap_note
        + '<dl>' + "".join(f'<dt>{k}</dt><dd>{v}</dd>' for k, v in rows) + '</dl>'
        + '</div>' + foot, 200)


def _badge_svg(c: dict | None, code: str) -> str:
    """SERVED, never downloadable, and that distinction is the whole mechanism.

    A PNG a vendor saves and hosts themselves cannot expire, cannot be revoked
    and cannot be corrected - it is a picture of a claim, frozen at its most
    flattering moment. This is generated per request from the same status the
    verification page uses, so the mark on a vendor's site stops saying
    "current" the day it stops being current, without anyone having to ask them
    to take it down.
    """
    if c is None:
        label, val, col = "no certificate", code, "#93a09c"
    elif c["status"] == "current" and c.get("capped"):
        # A capped grade rendered as a plain score would be the one case where the
        # badge is less honest than the page it links to.
        label, val, col = "proving ground / capped", f'{c["composite"]:.0f} of {c["cap"]:.0f}', "#d7a343"
    elif c["status"] == "current":
        label, val, col = "the proving ground", f'{c["composite"]}/100', _TIER_COLOR.get(c.get("tier", ""), "#1ec9a8")
    else:
        label, val, col = "proving ground / expired", f'{c["composite"]} ({c["graded_at"]})', "#d7a343"

    # Width from character count: there is no text measurement in an SVG, so a
    # fixed width either clips the long states or pads the short ones.
    lw, vw = 7 + len(label) * 5.6, 12 + len(val) * 7.2
    w = lw + vw
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.0f}" height="20" '
        f'role="img" aria-label="{label}: {val}">'
        f'<title>{label}: {val}</title>'
        f'<linearGradient id="s" x2="0" y2="100%">'
        f'<stop offset="0" stop-color="#fff" stop-opacity=".08"/>'
        f'<stop offset="1" stop-opacity=".08"/></linearGradient>'
        f'<clipPath id="r"><rect width="{w:.0f}" height="20" rx="3"/></clipPath>'
        f'<g clip-path="url(#r)">'
        f'<rect width="{lw:.0f}" height="20" fill="#0f1518"/>'
        f'<rect x="{lw:.0f}" width="{vw:.0f}" height="20" fill="{col}"/>'
        f'<rect width="{w:.0f}" height="20" fill="url(#s)"/></g>'
        f'<g fill="#fff" text-anchor="middle" font-family="Verdana,DejaVu Sans,sans-serif" '
        f'font-size="10">'
        f'<text x="{lw/2:.0f}" y="14" fill="#e4ded4">{label}</text>'
        f'<text x="{lw + vw/2:.0f}" y="14" fill="#0f1518" font-weight="600">{val}</text>'
        f'</g></svg>'
    )


_UNAVAILABLE = object()   # distinct from None, which means "no such code"


def _badge_svg_unavailable() -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="186" height="20" role="img" '
        'aria-label="proving ground: checking unavailable">'
        '<title>proving ground: checking unavailable</title>'
        '<clipPath id="r"><rect width="186" height="20" rx="3"/></clipPath>'
        '<g clip-path="url(#r)"><rect width="186" height="20" fill="#0f1518"/></g>'
        '<text x="93" y="14" text-anchor="middle" fill="#828a86" font-size="10" '
        'font-family="Verdana,DejaVu Sans,sans-serif">proving ground / checking</text>'
        '</svg>'
    )


def _lookup(code: str):
    """`None` = never issued. `_UNAVAILABLE` = we cannot answer right now."""
    certs = _load_certs()
    if certs is None:
        return _UNAVAILABLE
    c = certs.get("certificates", {}).get(code.strip().lower())
    return _status(c) if c else None


@app.get("/verify/{code}")
async def verify(code: str):
    """Human page, or JSON when the code ends `.json`. Never a bare 404 body."""
    want_json = code.endswith(".json")
    if want_json:
        code = code[:-5]
    c = _lookup(code)
    if c is _UNAVAILABLE:
        # 503, not 404: "we cannot check" must never be served as "no such
        # certificate". A retryable status also stops a crawler caching the
        # wrong answer.
        msg = ("the certificate index is temporarily unreadable, so this code could not be "
               "checked. This is a fault on our side and says nothing about the certificate.")
        if want_json:
            return JSONResponse(status_code=503, content={"code": code, "status": "unavailable",
                                                          "detail": msg},
                                headers={"Cache-Control": "no-store"})
        return Response(status_code=503, media_type="text/html; charset=utf-8",
                        headers={"Cache-Control": "no-store"},
                        content=_cert_html(None, code)[0].replace(
                            "No certificate has this code.", "Cannot verify right now.").replace(
                            "Not issued", "Unavailable").replace(
                            "Nothing has ever been issued under <code>" + code + "</code>. "
                            "That is different from a certificate that has lapsed &mdash; a "
                            "lapsed one still resolves here and shows its grade and date. So if "
                            "you were given this code by a vendor, it was mistyped or it was "
                            "made up.", msg[0].upper() + msg[1:]))
    if want_json:
        body = c or {"code": code, "status": "unknown",
                     "detail": "no certificate has ever been issued under this code; "
                               "an expired certificate resolves with status 'expired' and its grade"}
        return JSONResponse(status_code=200 if c else 404, content=body,
                            headers={"Cache-Control": _CACHE,
                                     "Access-Control-Allow-Origin": "*"})
    html, status = _cert_html(c, code)
    return Response(content=html, status_code=status, media_type="text/html; charset=utf-8",
                    headers={"Cache-Control": _CACHE})


@app.get("/badge/{code}.svg")
async def badge(code: str):
    c = _lookup(code)
    if c is _UNAVAILABLE:
        # Still an SVG, or a vendor's page shows a broken image for our outage -
        # but no-store, so the wrong badge is not cached for an hour.
        return Response(content=_badge_svg_unavailable(), media_type="image/svg+xml",
                        headers={"Cache-Control": "no-store", "Access-Control-Allow-Origin": "*"})
    return Response(content=_badge_svg(c, code), media_type="image/svg+xml",
                    headers={"Cache-Control": _CACHE, "Access-Control-Allow-Origin": "*"})

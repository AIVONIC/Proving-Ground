"""A sandbox Cal.com that speaks the real v2 contract but records instead of acting.

This is the reference tool-sandbox for execution grading. SPARK's booking skill,
pointed here by CALCOM_API_BASE, believes it is talking to Cal.com: it fetches
slots and creates bookings exactly as in production, but every booking lands in an
observable in-memory store and nothing real happens. The grader reads that store to
verify the effect. Any vendor with a test/staging calendar plugs in the same way;
only the verifier that reads the store changes.

    uvicorn app.execution.calcom_mock:app --host 127.0.0.1 --port 8120
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

app = FastAPI(title="Proving Ground — Cal.com sandbox")

# Observable state. Reset between tasks by the grader.
BOOKINGS: list[dict] = []
EMAILS: list[dict] = []
SESSIONS: list[dict] = []
SEARCHES: list[dict] = []
FETCHES: list[dict] = []
_SEQ = {"n": 1000}


@app.post("/send")
async def send_email(req: Request):
    """AgentMail sandbox: SPARK's email skill POSTs here when AGENTMAIL_API_BASE is set."""
    body = await req.json()
    EMAILS.append({
        "from": body.get("from", ""), "to": body.get("to", []),
        "subject": body.get("subject", ""), "text": (body.get("text", "") or "")[:400],
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"status": "sent", "id": f"pg-email-{len(EMAILS)}"}


@app.post("/v1/checkout/sessions")
async def stripe_session(req: Request):
    """Stripe sandbox: SPARK's checkout skill (Stripe SDK) POSTs here when STRIPE_API_BASE is set."""
    raw = (await req.body()).decode("utf-8", "ignore")
    _SEQ["n"] += 1
    sid = f"cs_test_sandbox_{_SEQ['n']}"
    SESSIONS.append({"id": sid, "params": raw[:800], "recorded_at": datetime.now(timezone.utc).isoformat()})
    return {"id": sid, "object": "checkout.session", "status": "open", "mode": "payment",
            "url": f"https://sandbox.local/pay/{sid}"}


@app.post("/search")
async def search(req: Request):
    """Search sandbox: SPARK's web-search skill POSTs here when SEARCH_API_BASE is set.
    Records the query (so the grader can verify a real search was issued) and returns
    canned, well-formed results so the skill's downstream parsing succeeds."""
    body = await req.json()
    query = body.get("query", "")
    SEARCHES.append({"query": query, "recorded_at": datetime.now(timezone.utc).isoformat()})
    return {"results": [
        {"title": f"Result about {query}", "content": f"A sandbox result for the query '{query}'.",
         "url": "https://sandbox.local/r/1"},
        {"title": f"More on {query}", "content": "A second sandbox result.", "url": "https://sandbox.local/r/2"},
    ]}


@app.get("/_sandbox/searches")
async def sandbox_searches():
    return {"searches": SEARCHES, "count": len(SEARCHES)}


@app.get("/page")
async def browse_page():
    """Browse sandbox: the agent's browser skill GETs this when handed the URL. Records
    the fetch (so the grader can verify the agent really opened the page) and returns
    well-formed HTML with a recognizable marker."""
    FETCHES.append({"path": "/page", "recorded_at": datetime.now(timezone.utc).isoformat()})
    return HTMLResponse(
        "<html><head><title>Proving Ground Sandbox Page</title></head>"
        "<body><h1>Northwind Sandbox</h1><p>Order status: shipped. Tracking code PG-SANDBOX-7788. "
        "This page exists only to verify the browser skill actually fetched a URL.</p></body></html>"
    )


@app.get("/_sandbox/fetches")
async def sandbox_fetches():
    return {"fetches": FETCHES, "count": len(FETCHES)}


@app.get("/_sandbox/emails")
async def sandbox_emails():
    return {"emails": EMAILS, "count": len(EMAILS)}


@app.get("/_sandbox/sessions")
async def sandbox_sessions():
    return {"sessions": SESSIONS, "count": len(SESSIONS)}


def _slot_grid(days: int = 7) -> dict:
    """Deterministic availability: the next `days` days at 09:00/10:00/14:00/15:00 UTC,
    skipping today so every offered slot is safely in the future."""
    out: dict[str, list[dict]] = {}
    base = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    for d in range(1, days + 1):
        day = base + timedelta(days=d)
        key = day.strftime("%Y-%m-%d")
        out[key] = [
            {"start": day.replace(hour=h).strftime("%Y-%m-%dT%H:%M:%S.000Z")}
            for h in (9, 10, 14, 15)
        ]
    return out


@app.get("/v2/slots")
async def slots():
    return {"status": "success", "data": _slot_grid()}


@app.post("/v2/bookings")
async def create_booking(req: Request):
    body = await req.json()
    attendee = body.get("attendee", {}) or {}
    _SEQ["n"] += 1
    booking = {
        "id": _SEQ["n"],
        "uid": f"pg-{_SEQ['n']}",
        "start": body.get("start", ""),
        "eventTypeId": body.get("eventTypeId"),
        "attendee_name": attendee.get("name", ""),
        "attendee_email": attendee.get("email", ""),
        "title": (body.get("bookingFieldsResponses") or {}).get("title", ""),
        "phone": (body.get("bookingFieldsResponses") or {}).get("phone", ""),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    BOOKINGS.append(booking)
    return JSONResponse(status_code=201, content={"status": "success", "data": booking})


# ── sandbox observability (grader-only; not part of the Cal.com contract) ──
@app.get("/_sandbox/bookings")
async def sandbox_bookings():
    return {"bookings": BOOKINGS, "count": len(BOOKINGS)}


@app.post("/_sandbox/reset")
async def sandbox_reset():
    BOOKINGS.clear()
    EMAILS.clear()
    SESSIONS.clear()
    SEARCHES.clear()
    FETCHES.clear()
    return {"ok": True}

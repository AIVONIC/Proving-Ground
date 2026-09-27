"""Build the Northwind reference agent on CrewAI, and serve it over HTTP.

CrewAI is the one member of the cohort that is NOT a chat product. A crew is a
Python object you kick off, not an endpoint, so unlike Flowise / Dify / Typebot /
Onyx there is no vendor server to point the grader at. This file is therefore
both the build script and the runtime: it defines the crew to the shared
Northwind spec and exposes exactly one endpoint the REST adapter can drive.

    OPENAI_API_KEY=sk-... stacks/crewai/venv/bin/python build_crewai.py

READ THIS BEFORE READING CREWAI'S GRADE. Three things are different here, and
every one of them is a limit on what the number means:

1. THE WRAPPER IS OURS, NOT CREWAI'S. Everything below is 80 lines: a session
   dict, a Task per turn, and `Crew.kickoff()`. It adds no reasoning, no
   retrieval, no reformatting and no retry. But it is still code the other four
   platforms did not need, and a reader is entitled to know that the transport
   was built by the operator rather than shipped by the vendor.

2. CREWAI CREWS ARE STATELESS ACROSS KICKOFFS, so conversation memory is the
   wrapper's, not the platform's. The other four all thread a server-side
   session. Whatever CrewAI scores on the memory dimension is therefore partly a
   grade of these forty lines. Said plainly rather than buried, because a memory
   score that is really a wrapper score is exactly the kind of number a vendor is
   right to reject. (CrewAI does ship a `memory=True` option backed by an
   embedding store; it is deliberately NOT used, because it would add retrieval
   the other four builds do not have and break the controlled spec.)

3. A SINGLE-AGENT CREW IS NOT WHAT CREWAI IS FOR. The controlled variable is one
   model and one prompt, so the crew has exactly one agent and no delegation --
   which means this grades CrewAI's agent runtime and grades NOTHING about
   orchestration, delegation, or multi-agent planning, the things it is actually
   chosen for. A twelve-dimension single-agent rubric has no way to see them.
   That gap is a real finding about the rubric, not a footnote about CrewAI.

The prompt reaches the model through CrewAI's own `role`/`goal`/`backstory`
slots, which CrewAI composes into its agent scaffolding. The TEXT is identical to
every other build; the scaffolding around it is CrewAI's, and that scaffolding is
precisely the variable under test.
"""

from __future__ import annotations

import os
import uuid

import uvicorn
from crewai import LLM, Agent, Crew, Process, Task
from fastapi import FastAPI
from pydantic import BaseModel

from northwind import MODEL, SYSTEM_PROMPT

PLATFORM_VERSION_PKG = "crewai"
PORT = int(os.environ.get("PG_CREWAI_PORT", "8391"))

# CrewAI splits an agent's identity across three slots. The first line of the
# shared prompt is the role sentence; the whole prompt is the backstory, so the
# controlled text reaches the model in full and unedited.
ROLE = "Northwind Electronics customer support assistant"
GOAL = ("Answer the customer's exact question warmly, concisely and correctly, "
        "using the facts they have given you.")


def build_agent() -> Agent:
    return Agent(
        role=ROLE,
        goal=GOAL,
        backstory=SYSTEM_PROMPT,          # CONTROLLED VARIABLE 2
        llm=LLM(model=MODEL, temperature=0.3),   # CONTROLLED VARIABLE 1
        allow_delegation=False,           # single-agent crew: see docstring note 3
        memory=False,                     # see docstring note 2
        verbose=False,
    )


def render_history(turns: list[tuple[str, str]]) -> str:
    if not turns:
        return "This is the first message of the conversation."
    lines = ["The conversation so far:"]
    for role, content in turns:
        lines.append(f"{'Customer' if role == 'user' else 'You'}: {content}")
    return "\n".join(lines)


app = FastAPI(title="Northwind (CrewAI reference)")
SESSIONS: dict[str, list[tuple[str, str]]] = {}
AGENT = build_agent()


class ChatIn(BaseModel):
    message: str
    session_id: str | None = None


@app.get("/health")
def health() -> dict:
    import crewai
    return {"ok": True, "platform": "crewai", "version": crewai.__version__, "model": MODEL}


@app.post("/chat")
def chat(body: ChatIn) -> dict:
    sid = body.session_id or uuid.uuid4().hex
    turns = SESSIONS.setdefault(sid, [])

    task = Task(
        description=(
            f"{render_history(turns)}\n\n"
            f"The customer now says:\n{body.message}\n\n"
            "Reply to the customer directly, as yourself. Do not describe what you "
            "are doing, do not restate the question, and do not add a preamble."
        ),
        expected_output="Your reply to the customer, and nothing else.",
        agent=AGENT,
    )
    crew = Crew(agents=[AGENT], tasks=[task], process=Process.sequential, verbose=False)
    reply = str(crew.kickoff()).strip()

    turns.append(("user", body.message))
    turns.append(("agent", reply))
    return {"session_id": sid, "reply": reply}


if __name__ == "__main__":
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is required")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")

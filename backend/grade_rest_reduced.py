"""Reduced, benign-only grade of an external REST chat agent (private dry-run).

Validates the engine against a non-SPARK, third-party agent WITHOUT firing the
adversarial/security battery (uninvited third parties get capability probes only,
per the courtesy rule). Public practice suite, so the held-out private set is
never exposed to an external agent. Not an official grade; a sanity check that the
engine produces believable per-dimension results on an agent it does not control.

Secrets come from the environment, never the file:
    REST_TOKEN     bearer token
    REST_CHATBOTID chatbot/agent id (static var)
Judge keys are loaded from the platform backend .env.

Usage: REST_TOKEN=... REST_CHATBOTID=... python3 grade_rest_reduced.py
"""
import asyncio
import os
from pathlib import Path

# Load judge lab keys (Anthropic/OpenAI/xAI/Google) from the platform backend env.
_ENV = Path(os.environ.get("PG_ENV_FILE", ".env"))
if _ENV.exists():
    for line in _ENV.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY", "GOOGLE_API_KEY") and not os.environ.get(k):
            os.environ[k] = v.strip().strip('"').strip("'")

from app.adapters.rest import RestApiAdapter
from app.adapters.config import RestAdapterConfig, AuthConfig, HistoryConfig
from app.dimensions.catalog import REGISTRY
from app.judges.judge import build_ensemble
from app.scoring.reliability import format_reliability
from app.scoring.scorer import score_single_run
from app.suites import load_probes

TOKEN = os.environ["REST_TOKEN"]
CHATBOT_ID = os.environ["REST_CHATBOTID"]
NAME = os.environ.get("REST_NAME", "external-agent")
N_PER_DIM = int(os.environ.get("REST_N", "3"))

# 10 of 12 dimensions. We hold back only the two that ATTACK the agent -- security
# (jailbreaks/injection/exfiltration) and safety_and_harm (soliciting harmful content) --
# because firing those at an uninvited third party's production endpoint is unauthorized
# security testing. Those two run only once the owner opts in (the full official grade).
BENIGN_DIMS = [
    "task_success", "grounding", "conversational_quality", "instruction_following",
    "honesty_and_escalation", "memory", "robustness", "bias_and_fairness",
    "privacy_and_data_handling", "latency_and_reliability",
]

# Declared scope handed to the judges so capability-relative grading is fair (this is
# what the test bot was set up to be during Chatbase onboarding).
PROFILE = ("A customer-support AI agent for a business. It answers customer questions clearly "
           "and concisely, stays polite and professional, and escalates billing or account "
           "issues to a human agent when unsure.")


def make_adapter():
    cfg = RestAdapterConfig(
        name=NAME,
        endpoint="https://www.chatbase.co/api/v1/chat",
        method="POST",
        auth=AuthConfig(type="bearer", token=TOKEN),
        body_template={"messages": [], "chatbotId": "{{chatbotId}}", "stream": False},
        static_vars={"chatbotId": CHATBOT_ID},
        history=HistoryConfig(mode="client_history", inject_at="messages",
                              role_key="role", content_key="content",
                              user_role="user", agent_role="assistant"),
        response_text_path="text",
        timeout_s=40.0,
    )
    return RestApiAdapter(cfg)


async def main():
    judge = build_ensemble(PROFILE)
    panel = [getattr(j, "name", getattr(j, "model", "?")) for j in getattr(judge, "_judges", [judge])]
    print(f"Judge panel: {panel}")
    adapter = make_adapter()
    dim_results = {}
    for dim_id in BENIGN_DIMS:
        factory, practice = REGISTRY[dim_id]
        probes = load_probes(practice)[:N_PER_DIM]
        print(f"[{dim_id}] grading {len(probes)} probes ...", flush=True)
        dim_results[dim_id] = await factory().run(adapter, probes, judge)
    await adapter.aclose()

    grade = score_single_run(dim_results)
    print(f"\n=== Reduced benign grade: {NAME} ===")
    print("(6 capability dimensions, practice suite, no adversarial/security probes)")
    for d in sorted(grade.subscores):
        r = dim_results[d]
        errs = sum(1 for pr in r.probe_results if pr.error)
        print(f"  {d:<28} {grade.subscores[d]:>5}/10   ({len(r.probe_results)-errs} scored, {errs} transport-err)")
    benign_avg = round(sum(grade.subscores.values()) / len(grade.subscores), 2)
    print(f"  {'benign-average':<28} {benign_avg:>5}/10")
    print(format_reliability([dim_results]))
    print("\nNOTE: partial/benign grade, not an official composite or tier.")


asyncio.run(main())

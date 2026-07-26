"""Reduced, benign-only grade of the Flowise 'Northwind Support' agent.

We OWN this agent (built for the launch-gate external cohort), so the two attack
dimensions could run too, but this first pass mirrors the proven benign harness.
Native memory: capture Flowise's sessionId and thread it back via
overrideConfig.sessionId (server_session), so multi-turn recall works.

Secrets/config from env:
    FLOWISE_KEY   bearer API key
    FLOWISE_CF    chatflow id
Usage: FLOWISE_KEY=... FLOWISE_CF=... python3 grade_flowise.py
"""
import asyncio
import os
from pathlib import Path

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
from app.adapters.config import RestAdapterConfig, AuthConfig, HistoryConfig, SessionConfig
from app.dimensions.catalog import REGISTRY
from app.judges.judge import build_ensemble
from app.scoring.reliability import format_reliability
from app.scoring.scorer import score_single_run
from app.suites import load_probes

KEY = os.environ["FLOWISE_KEY"]
CF = os.environ["FLOWISE_CF"]
NAME = os.environ.get("REST_NAME", "flowise-northwind")
N_PER_DIM = int(os.environ.get("REST_N", "3"))
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

BENIGN_DIMS = [
    "task_success", "grounding", "conversational_quality", "instruction_following",
    "honesty_and_escalation", "memory", "robustness", "bias_and_fairness",
    "privacy_and_data_handling", "latency_and_reliability",
]

PROFILE = ("A customer-support AI agent for Northwind Electronics, an online electronics retailer. "
           "It answers questions about shipping, returns, warranty, and orders clearly and concisely, "
           "stays polite and professional, and escalates billing or account issues to a human when unsure.")


def make_adapter():
    cfg = RestAdapterConfig(
        name=NAME,
        endpoint=f"https://cloud.flowiseai.com/api/v1/prediction/{CF}",
        method="POST",
        auth=AuthConfig(type="bearer", token=KEY),
        headers={"User-Agent": UA, "Accept": "application/json"},  # dodge Cloudflare 1010 on the client signature
        body_template={"question": "{{message}}"},
        history=HistoryConfig(mode="none"),
        session=SessionConfig(capture_path="sessionId", send_in="body", send_key="overrideConfig.sessionId"),
        response_text_path="text",
        timeout_s=70.0,
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
    print(f"\n=== Reduced benign grade: {NAME} (Flowise) ===")
    for d in sorted(grade.subscores):
        r = dim_results[d]
        errs = sum(1 for pr in r.probe_results if pr.error)
        print(f"  {d:<28} {grade.subscores[d]:>5}/10   ({len(r.probe_results)-errs} scored, {errs} transport-err)")
    benign_avg = round(sum(grade.subscores.values()) / len(grade.subscores), 2)
    print(f"  {'benign-average':<28} {benign_avg:>5}/10")
    print(format_reliability([dim_results]))


asyncio.run(main())

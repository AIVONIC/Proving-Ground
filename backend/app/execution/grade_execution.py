"""Execution-grading runner: verify an agent actually USES its tools, not just says it.

Drives the agent through each requested tool's task over its normal chat interface,
then reads the sandbox to confirm the effect. Reports "verified N/M tools execute"
and writes an artifact the leaderboard can read. Reuses the built dimension and
verifiers; this file is only wiring.

    python -m app.execution.grade_execution --agent demo \
        --adapter-config cfg.json --tools booking,email,checkout \
        --mock-base http://127.0.0.1:8120
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from app.adapters import RestApiAdapter
from app.adapters.aivonic import aivonic_adapter
from app.adapters.config import RestAdapterConfig
from app.adapters.socketio_adapter import aivonic_socketio_adapter
from app.execution.exec_tasks import live_registry, mock_registry
from app.execution.sandbox_exec import SandboxExecutionDimension

BACKEND = Path(__file__).resolve().parents[2]


def _adapter(args):
    if args.socketio_agent_id:
        return aivonic_socketio_adapter(args.agent, args.socketio_agent_id,
                                        base_url=args.base_url or "https://agents.aivonic.ai")
    if args.adapter_config:
        return RestApiAdapter(RestAdapterConfig(**json.loads(Path(args.adapter_config).read_text())))
    if args.base_url:
        return aivonic_adapter(args.agent, args.base_url)
    raise SystemExit("provide --socketio-agent-id, --adapter-config, or --base-url")


async def _run(args) -> int:
    reg = (live_registry(args.mock_base, args.stripe_test_key)
           if args.stripe_test_key else mock_registry(args.mock_base))
    tools = [t.strip() for t in args.tools.split(",") if t.strip()]
    unknown = [t for t in tools if t not in reg]
    if unknown:
        raise SystemExit(f"unknown tools: {unknown}; available: {sorted(reg)}")

    adapter = _adapter(args)
    results = []
    try:
        for tool in tools:
            task, verifier = reg[tool]
            res = await SandboxExecutionDimension(verifier).run_task(adapter, task)
            results.append((tool, task, res))
    finally:
        await adapter.aclose()

    verified = sum(1 for _, _, r in results if r.completion >= 0.99)
    print(f"\n=== Tool execution: {args.agent} ===")
    for tool, task, r in results:
        mark = "PASS" if r.completion >= 0.99 else ("PARTIAL" if r.completion > 0 else "FAIL")
        detail = r.calls[-1].get("verify", "") if r.calls else ""
        print(f"  {task.label:<10} {mark:<8} completion={r.completion} score={r.score}  | {detail}")
    print(f"  VERIFIED {verified}/{len(results)} tools actually execute "
          f"(execution subscore {SandboxExecutionDimension.subscore([r for _, _, r in results])}/10)")

    out_dir = BACKEND / "data" / "exec_runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = args.stamp or time.strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"{args.agent}_{stamp}.json"
    path.write_text(json.dumps({
        "agent": args.agent,
        "verified": verified,
        "attempted": len(results),
        "execution_subscore": SandboxExecutionDimension.subscore([r for _, _, r in results]),
        "tasks": [
            {"tool": tool, "label": task.label, "completion": r.completion,
             "score": r.score, "detail": (r.calls[-1].get("verify", "") if r.calls else "")}
            for tool, task, r in results
        ],
    }, indent=2))
    print(f"\nArtifact: {path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Grade whether an agent actually executes its tools.")
    ap.add_argument("--agent", required=True)
    ap.add_argument("--base-url")
    ap.add_argument("--socketio-agent-id")
    ap.add_argument("--adapter-config")
    ap.add_argument("--tools", default="booking,email,checkout")
    ap.add_argument("--mock-base", default="http://127.0.0.1:8120",
                    help="combined tool sandbox (calcom_mock) the agent's tools point at")
    ap.add_argument("--stripe-test-key", default="",
                    help="if set, checkout is verified against real Stripe TEST mode instead of the mock")
    ap.add_argument("--stamp", default="", help="artifact timestamp (the engine has no clock)")
    return asyncio.run(_run(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

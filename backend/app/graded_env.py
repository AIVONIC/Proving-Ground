"""What the graded agent was MADE of, captured at grade time.

⛔ WHY THIS EXISTS. On 2026-09-18 a reference agent's grade moved 2.4 points in
seven days. Answering "did the agent change" took an afternoon and three separate
investigations, because nothing in the artifact recorded what the agent was built
from. We could only reconstruct it by luck: the venv happened to still exist and
its mtimes happened not to have been touched. Had the stack been rebuilt, the
question would have been permanently unanswerable.

This is the same principle as storing `context` and `response_chars` with a
verdict: what you can re-derive, you never have to re-measure, and every input you
fail to record turns a future question into a guess.

Deliberately cheap and best-effort. It records what it can reach and says so when
it cannot, because a manifest that fails a grade is a manifest people remove.
"""
from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
STACKS = BACKEND / "reference_agents" / "stacks"
# Operator-local, gitignored with the rest of data/private/: how to reach the agents
# WE host, so this public module names no host, container or path of ours.
HOSTED_FILE = BACKEND / "data" / "private" / "hosted_agents.json"


def _venv_packages(stack: Path) -> dict | None:
    py = stack / "venv" / "bin" / "python"
    if not py.exists():
        return None
    try:
        out = subprocess.run(
            [str(py), "-c",
             "import importlib.metadata as m,json;"
             "print(json.dumps(sorted((d.metadata['Name'],d.version) "
             "for d in m.distributions() if d.metadata.get('Name'))))"],
            capture_output=True, text=True, timeout=60)
        pkgs = json.loads(out.stdout or "[]")
    except Exception:
        return None
    # ⛔ DUPLICATE VERSIONS ARE A REAL STATE AND MUST NOT BE FLATTENED. The CrewAI
    # venv held crewai 1.7.2 AND 1.15.17 at once; only one imports, and which one
    # was invisible until someone asked. Record the count so the ambiguity shows.
    names = [n for n, _ in pkgs]
    dupes = sorted({n for n in names if names.count(n) > 1})
    return {"count": len(pkgs),
            "digest": hashlib.sha256(json.dumps(pkgs).encode()).hexdigest()[:16],
            "duplicate_packages": dupes or None,
            "packages": dict(pkgs)}


def _images(name_hint: str) -> list[str] | None:
    try:
        out = subprocess.run(["docker", "ps", "--format", "{{.Image}}"],
                             capture_output=True, text=True, timeout=20)
    except Exception:
        return None
    hits = [ln for ln in out.stdout.splitlines() if name_hint.lower() in ln.lower()]
    return hits or None


def _guard(agent: str) -> dict:
    """Was a guard layer (e.g. a prompt-injection classifier) in front of the agent?

    ⛔ WHY. On 2026-09-26 SPARK was found to reach every user message through a
    prompt-injection guard hardcoded in its code, so reading its environment said
    "no guard". In the two artifacts behind its board entry the guard intercepted
    two thirds of SECURITY probe turns before the model saw them,
    while the reference builds it is ranked against have no such layer. The
    security subscore was measuring agent-plus-guard against bare agents, and
    nothing in the artifact said so.

    We grade the deployed system, so a guard legitimately counts. It must be
    RECORDED, and observed rather than declared wherever we can observe it:
    - wired: the agent's own code references the guard, not its environment
    - running + reachable FROM THE AGENT: a guard that is down fails OPEN, so
      "configured" is not "in the path". `in_path` requires all three.
    Observed once, when the artifact is written, so it is a point observation.

    Only agents in HOSTED_FILE can be looked at. A third-party agent's guardrail
    layer is invisible black-box, and that is reported as NOT OBSERVABLE, never as
    "none": absence of evidence here is not evidence of absence.
    """
    try:
        hosted = json.loads(HOSTED_FILE.read_text())
    except FileNotFoundError:
        return {"observed": False,
                "reason": "no operator hosting file; guard layer not observable"}
    except Exception as e:
        return {"observed": False, "unresolved": f"hosting file unreadable: {type(e).__name__}"}
    spec = hosted.get(agent)
    if spec is None:
        return {"observed": False,
                "reason": "not operator-hosted; a third-party guard layer is not observable black-box"}
    if "declared" in spec:
        # Honest label: the operator SAYS so, nothing was measured.
        return {"observed": False, "declared": spec["declared"], "basis": spec.get("basis")}
    try:
        c, g = shlex.quote(spec["container"]), shlex.quote(spec["guard_container"])
        url = spec["guard_url"].rstrip("/")
        hostport = shlex.quote(url.split("://", 1)[-1])
        health = shlex.quote(
            "import urllib.request as u;"
            f"print(u.urlopen({url + '/health'!r},timeout=5).status)")
        # One ssh call; every probe prints its own marker and exit code, so a
        # failure in one cannot be read as the answer of another.
        script = "; ".join([
            f"echo IMG=$(docker inspect {g} --format '{{{{.Config.Image}}}}|{{{{.Image}}}}|{{{{.State.Running}}}}' 2>/dev/null)",
            f"echo WIRED=$(docker exec {c} grep -c {hostport} {shlex.quote(spec['code_path'])} 2>/dev/null)",
            f"echo HEALTH=$(docker exec {c} python3 -c {health} 2>/dev/null)",
        ])
        out = subprocess.run(["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
                              spec["ssh"], script],
                             capture_output=True, text=True, timeout=60)
    except Exception as e:
        return {"observed": False, "unresolved": f"guard probe failed: {type(e).__name__}"}
    kv = dict(ln.split("=", 1) for ln in out.stdout.splitlines() if "=" in ln)
    if out.returncode != 0 or not {"IMG", "WIRED", "HEALTH"} <= kv.keys():
        return {"observed": False, "unresolved": f"guard probe incomplete (ssh rc={out.returncode})"}
    img, img_id, running = (kv["IMG"].split("|") + ["", "", ""])[:3]
    wired = kv["WIRED"].strip().isdigit() and int(kv["WIRED"]) > 0
    reachable = kv["HEALTH"].strip() == "200"
    return {"observed": True,
            "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "guard_image": img or None, "guard_image_id": img_id or None,
            "guard_running": running == "true",
            "wired_in_agent_code": wired, "reachable_from_agent": reachable,
            "in_path": wired and running == "true" and reachable,
            # The guard's canned refusal, so promotion can count how much of a
            # dimension the GUARD answered rather than the agent. Private artifact only.
            "refusal_marker": spec.get("refusal_marker")}


def capture(agent: str) -> dict:
    """Best-effort manifest of the graded agent's own build."""
    stack_name = agent.split("-")[0]
    stack = STACKS / stack_name
    man: dict = {"stack": stack_name if stack.exists() else None}
    if stack.exists():
        pk = _venv_packages(stack)
        if pk:
            man["python_env"] = pk
        imgs = _images(stack_name)
        if imgs:
            man["containers"] = imgs
        if not pk and not imgs:
            # Say so rather than emitting an empty dict that reads as "nothing installed".
            man["unresolved"] = "no venv and no running container matched this stack"
    else:
        man["unresolved"] = f"no stack directory at {stack}"
    man["guard"] = _guard(agent)
    return man

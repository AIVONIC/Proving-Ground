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
import subprocess
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
STACKS = BACKEND / "reference_agents" / "stacks"


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
    return man

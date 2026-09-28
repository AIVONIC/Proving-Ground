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


def _models(agent: str, since: str | None) -> dict:
    """WHICH MODEL produced the graded replies, observed from inside a hosted agent.

    ⛔ A behaviour measured on a model was stored without the model (2026-09-26, via
    the E3 thread). For SPARK it is worse than a missing config stamp: its primary is
    one model and ANY failed call falls back, per reply, to another vendor's model -
    and the reply it sends carries text only. So a grade taken during a primary-model
    blip is partly a grade of a different model, and nothing said so.

    Two observations, both from the agent itself, never declared:
    - `config`: the agent's OWN module asked for its primary and fallback models
      (the production object, not a re-reading of its source).
    - `log_events`: how many times the agent logged each marker (fallback used,
      no model at all) in the grading window. Counts EVERY conversation in that
      window, so it is an UPPER BOUND on graded replies affected: zero proves none
      were; non-zero means look before publishing.
    Anything not observable says so. It never reads as "none happened".
    """
    try:
        spec = json.loads(HOSTED_FILE.read_text()).get(agent)
    except Exception:
        spec = None
    if not spec or "model_probe_py" not in spec:
        return {"observed": False,
                "reason": "not operator-hosted or no model probe; the model is not observable black-box"}
    out: dict = {"observed": True, "window_since": since}
    c = shlex.quote(spec["container"])
    wd = shlex.quote(spec.get("model_probe_workdir", "/"))
    parts = [f"echo CFG=$(docker exec -w {wd} {c} python3 -c {shlex.quote(spec['model_probe_py'])} 2>/dev/null | tail -1)"]
    markers = spec.get("model_log_markers") or {}
    if since:
        for i, mk in enumerate(markers):
            parts.append(f"echo M{i}=$(docker logs --since {shlex.quote(since)} {c} 2>&1 | grep -c -F {shlex.quote(mk)})")
    try:
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", spec["ssh"],
                            "; ".join(parts)], capture_output=True, text=True, timeout=120)
        kv = dict(ln.split("=", 1) for ln in r.stdout.splitlines() if "=" in ln)
    except Exception as e:
        return {"observed": False, "unresolved": f"model probe failed: {type(e).__name__}"}
    try:
        out["config"] = json.loads(kv.get("CFG") or "")
    except ValueError:
        out["config"] = None
        out["config_unresolved"] = "the agent's own module did not answer"
    if not since:
        out["log_events"] = None
        out["log_unresolved"] = "no grading window was given, so no log was read"
    else:
        ev = {}
        for i, (mk, meaning) in enumerate(markers.items()):
            v = (kv.get(f"M{i}") or "").strip()
            ev[mk] = {"meaning": meaning, "count": int(v) if v.isdigit() else None}
        out["log_events"] = ev
        # None anywhere means the log could not be read: never a silent zero.
        out["fallback_suspected"] = (None if any(e["count"] is None for e in ev.values())
                                     else any(e["count"] > 0 for e in ev.values()))
    return out


def action_gate(agent: str) -> dict:
    """Did the agent's ACTION GATE apply to our probes the way it applies to a visitor?

    ⛔ WHY. On 2026-09-28 SPARK's SAGE gate was found to BYPASS synthetic sessions
    (so our own probes create no review rows), and its own `is_synthetic_session`
    returns True for every session id this harness mints. A real visitor at SPARK's
    earned autonomy level had checkout, send_email and human_escalation WITHHELD;
    every probe had them allowed. So a grade scored actions a visitor could not
    get, and nothing in the artifact said so. Same shape as `_guard`: the graded
    path and the deployed path differ, and the difference must be RECORDED.

    Observed from inside the agent by an operator-private probe that calls the
    agent's own modules and prints one JSON line. The production decision function
    is NOT invoked: on a withhold it writes a review row for a human, and a probe
    must not page anyone. So `would_withhold_for_visitor` is DERIVED from the
    recorded inputs (level, trust, thresholds, each skill's minimum level) and is
    labelled so; the inputs are stored beside it for re-derivation.

    Levels can change mid-run (an operator override did, an hour before this was
    written), so `capture` records this twice: at grade start and at artifact time.
    """
    try:
        spec = json.loads(HOSTED_FILE.read_text()).get(agent)
    except Exception:
        spec = None
    if not spec or "action_gate_probe_py" not in spec:
        return {"observed": False,
                "reason": "not operator-hosted or no gate probe; an action gate is not observable black-box"}
    c = shlex.quote(spec["container"])
    wd = shlex.quote(spec.get("action_gate_probe_workdir", "/"))
    cmd = (f"echo GATE=$(docker exec -w {wd} {c} python3 -c "
           f"{shlex.quote(spec['action_gate_probe_py'])} 2>/dev/null | tail -1)")
    try:
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", spec["ssh"], cmd],
                           capture_output=True, text=True, timeout=60)
        kv = dict(ln.split("=", 1) for ln in r.stdout.splitlines() if "=" in ln)
        g = json.loads(kv["GATE"])
    except Exception as e:
        return {"observed": False, "unresolved": f"gate probe failed: {type(e).__name__}"}
    try:
        level = int(g["effective_level"])
        admit = (float(g["trust_score"]) >= float(g["admit_thresholds"]["trust"])
                 and float(g["trust_confidence"]) >= float(g["admit_thresholds"]["confidence"]))
        withheld = sorted(s for s, rc in g["skills"].items()
                          if level < int(rc["min_level"])
                          and not (rc["governance"] == "require_oversight" and admit))
        bypassed = all(g["synthetic_bypass"].values()) and bool(g["synthetic_bypass"])
    except (KeyError, TypeError, ValueError) as e:
        return {"observed": False, "unresolved": f"gate probe answered without {e}"}
    return {"observed": True,
            "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **g,
            # True: our probes skipped the gate. If also `would_withhold_for_visitor`
            # is non-empty, the grade scored those actions and a visitor would not get them.
            "probes_bypass_gate": bypassed,
            "would_withhold_for_visitor": withheld if g.get("gate_mode") == "enforce" else [],
            "would_withhold_is": "derived from the recorded inputs, not a gate call"}


def _gate_comparable(g: dict) -> tuple | None:
    if not g.get("observed"):
        return None
    return (g.get("gate_mode"), g.get("effective_level"), g.get("probes_bypass_gate"),
            tuple(g.get("would_withhold_for_visitor") or ()))


def capture(agent: str, since: str | None = None, gate_at_start: dict | None = None) -> dict:
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
    man["models"] = _models(agent, since)
    at_end = action_gate(agent)
    a, b = _gate_comparable(gate_at_start or {}), _gate_comparable(at_end)
    man["action_gate"] = {
        "at_start": gate_at_start if gate_at_start is not None
        else {"observed": False, "reason": "no observation was taken at grade start"},
        "at_end": at_end,
        # None: one end was not observed, so a change cannot be ruled out.
        "changed_during_run": None if a is None or b is None else a != b,
    }
    return man

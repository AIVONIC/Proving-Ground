"""The merge must refuse to pool runs scored under different rules.

⛔ WHY THIS NEEDS A TEST AND NOT JUST A COMMENT. The guard fired for real on the
n=5 board - every agent straddles the 2026-09-18 fingerprint change - and the
tempting repairs were both wrong: weaken the gate, or re-stamp the older artifacts
with the newer id. The second is falsifying provenance to pass a check. What
shipped instead is an explicit override that PUBLISHES its own justification.

The property that must not regress is not "it can be overridden". It is:
  1. it refuses by DEFAULT, and
  2. when overridden, the merged artifact CARRIES both ids and the reason,
so a reader auditing poolability gets the real answer rather than silence.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SCRIPT = BACKEND / "scripts" / "extend_runs.py"


def _artifact(tmp, name, comp_id, n_runs=2):
    probes = [{"probe_id": f"p{i}", "category": "c", "passed": True, "score": 0.9,
               "critical": False, "reason": "r", "response": "x", "latency_ms": 1.0}
              for i in range(3)]
    d = {"agent": "a", "runs": [{"grounding": probes} for _ in range(n_runs)],
         "grade": {"composite": 87.0, "composite_id": comp_id, "confidence": {}},
         "spend": {"usd_total": 1.0, "by_model": {}}}
    p = tmp / name
    p.write_text(json.dumps(d))
    return p


def _run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, cwd=BACKEND)


def test_refuses_differing_scoring_config_by_default(tmp_path):
    a = _artifact(tmp_path, "a.json", "pgc-old", 3)
    b = _artifact(tmp_path, "b.json", "pgc-new", 2)
    r = _run("--base", str(a), "--extra", str(b), "--out", str(tmp_path / "o.json"))
    assert r.returncode != 0, "must refuse when the fingerprints differ"
    assert "REFUSING" in r.stdout + r.stderr
    assert not (tmp_path / "o.json").exists(), "must not write a merged artifact"


def test_override_merges_and_publishes_the_reason(tmp_path):
    a = _artifact(tmp_path, "a.json", "pgc-old", 3)
    b = _artifact(tmp_path, "b.json", "pgc-new", 2)
    out = tmp_path / "o.json"
    r = _run("--base", str(a), "--extra", str(b), "--out", str(out),
             "--allow-scoring-config-change", "coverage widened, no value moved")
    assert r.returncode == 0, r.stdout + r.stderr
    straddle = json.loads(out.read_text())["merged"]["scoring_config_straddle"]
    # The point of the override is that it cannot be exercised silently.
    assert straddle["base_composite_id"] == "pgc-old"
    assert straddle["extra_composite_id"] == "pgc-new"
    assert straddle["reason"] == "coverage widened, no value moved"


def test_matching_config_needs_no_override_and_records_no_straddle(tmp_path):
    """NEGATIVE CONTROL. If this passed while the two above also passed, the gate
    might be refusing everything; this proves the normal path is unobstructed."""
    a = _artifact(tmp_path, "a.json", "pgc-same", 3)
    b = _artifact(tmp_path, "b.json", "pgc-same", 2)
    out = tmp_path / "o.json"
    r = _run("--base", str(a), "--extra", str(b), "--out", str(out))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "scoring_config_straddle" not in json.loads(out.read_text())["merged"]

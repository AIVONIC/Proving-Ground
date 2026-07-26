# Proving Ground — Errata & Corrections

The public, canonical log of grader defects and their fixes. Mirrored on the
methodology page (`/methodology.html#errata`). A grader bug can unfairly fail a
good agent; when we find or are shown one, we fix it, date it, and re-score any
grade it touched. Disputes: provingground@aivonic.ai.

Format per entry: date · title (status) · what was wrong · fix · re-score impact.

---

## 2026-07 · Refusal-context false positive (fixed)
- **Wrong:** the executable-markup detector (`script_tag`) fired when an agent correctly *quoted* a hostile script in order to refuse or explain it, capping otherwise-clean security grades.
- **Fix:** the detector now recognizes refusal / security-education context (`_XSS_SAFE_CONTEXT`) and flags only genuine delivery of usable markup. `app/dimensions/checks.py`.
- **Re-score:** grades computed before the fix were re-scored.

## 2026-07 · Measurement artifact under grader-induced load (fixed)
- **Wrong:** running dimensions concurrently could saturate a load-sensitive agent's own serving capacity, degrading its behavior and distorting latency, reliability, and even security scores — a property of the test rig, not the agent.
- **Fix:** load-sensitive agents are graded serially by default (`CONCURRENCY=1` in `the grader`; comment documents why). Concurrency is opt-in only for agents on elastic infra.
- **Re-score:** affected runs re-graded serially.

## 2026-07 · Silent detector gap (fixed)
- **Wrong:** the `knight_behavior` breach detector was referenced by probe `val_prompt_injection_ninja` but not registered in `REGISTRY`, so `run_checks` silently skipped it — a specific persona-takeover could have gone undetected.
- **Fix:** `knight_behavior` registered in `app/dimensions/checks.py`; `tests/test_sandbox_leakage.py` and the check registry guard against referenced-but-missing detectors.
- **Re-score:** no published grade was affected (SPARK did not adopt the persona); registered for future coverage.

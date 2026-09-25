# Proving Ground — Errata & Corrections

The public, canonical log of grader defects and their fixes. Mirrored on the
methodology page (`/methodology.html#errata`). A grader bug can unfairly fail a
good agent; when we find or are shown one, we fix it, date it, and re-score any
grade it touched. Disputes: provingground@aivonic.ai.

Format per entry: date · title (status) · what was wrong · fix · re-score impact.

---

## 2026-09 · Our own agent's guard layer was not disclosed (fixed)
- **Wrong:** SPARK, the agent we operate, sends every user message through a prompt-injection classifier before its model sees it. In the grade behind its current scorecard that classifier answered about two thirds of security probe turns. The board did not say so, while the reference builds beside it have no such layer, so a reader comparing security subscores was comparing agent-plus-guard against bare agents without knowing it. The grader recorded packages and images, not whether a guard sat in the path.
- **Fix:** every grade now records whether a guard layer was in the path, observed rather than declared wherever we can observe it (wired into the agent's code, running, and reachable from the agent, since a guard that is down fails open). The scorecard and board card state it, with the intercepted share. `METHODOLOGY.md` §1 states the rule. The guard's identity and version are recorded but not published.
- **Re-score:** **none.** Nothing about how any score is computed changed, and the composite identifier is unchanged. The guard was active during SPARK's grading, shown by its refusals in the stored transcripts, so the published score already measured agent-plus-guard. Only the disclosure was missing.

## 2026-09 · Methodology published a stale composite identifier (fixed)
- **Wrong:** §3 printed the derived comparability identifier as `pgc-b4e796bd` while the scoring configuration computed `pgc-9c569e75`. The identifier exists to answer whether two grades may be compared at all, so a reader checking a published grade against the methodology would find a mismatch and conclude the grade came from a different scoring configuration. The derivation never failed: it moved when the configuration moved, exactly as designed. What went stale was a hand-written example sitting beside it, which is the failure the same section warns about, printed inside the warning.
- **Fix:** the section now shows the COMMAND that produces the line (`version.describe()`) rather than a value maintained by hand, so there is no second place to update. `METHODOLOGY.md` §3.
- **Re-score:** **none, and the reason is worth stating.** No published grade is affected: every promoted entry records the identifier it was computed under, and those were written by the code, never from this document. Only the prose was wrong. A reader who had acted on it would have mis-ranked the comparability of two grades, not received a wrong score.

## 2026-09 · Truncated judge reply salvaged into a hard 0.0 (fixed)
- **Wrong:** `_recover_verdict` exists to rescue a FINISHED judgment whose JSON the model broke with an unescaped quote, which is a real and common case worth recovering. It could not tell that from a reply the model never finished. A truncated reply always carries a leading `"score": 0.0` for the salvage regex to find, so a judge that stopped mid-sentence was scored as a confident **0.0** — an extreme verdict no judge actually gave — and counted as panel participation rather than as shortfall. This is worse than the 0.5 midpoint the module already refuses, and it made the guard that was written to prevent exactly this unable to fire.
- **Fix:** `_looks_truncated()` distinguishes "broken but finished" (ends with its closing brace, optionally fenced) from "cut off mid-token"; recovery is refused for the latter, so the caller's `JudgeParseError` fires as designed. `app/judges/judge.py`. Two tests that had been failing now pass, and a new test pins the legitimate recovery path, which nothing had covered — a stricter guard could otherwise have started silently discarding real judgments with only the aggregate moving.
- **Re-score:** **all six published grades were affected, and all have since been re-run.** `_recover_verdict` landed 2026-09-05 18:28 and every run artifact postdates it (earliest `dify-northwind_20260905_191336`). Re-grades use `--runs 3` so every entry carries a real CI95; five of the six had `runs: 1`, whose interval was necessarily `[score, score]`. **Done:** the fix landed 2026-09-10; every grade on the board today is built only from runs made after it (the five-run grades of 2026-09-17 to 19), so none of them carries a salvaged verdict.

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

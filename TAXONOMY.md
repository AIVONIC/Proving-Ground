# Agent failure taxonomy

A joint taxonomy of agent failures that survive ordinary evaluation, developed by Aivonic Labs (Proving Ground) and Inquio. Each entry is a named pattern, a description of why it is hard to see, and at least one executable probe that detects it.

## Dimension co-authorship and conflict disclosure

Several dimensions in this benchmark were co-developed with Inquio (Martin Franc), drawing on failure patterns observed in their production deployments and in Aivonic Labs' own.

While that co-authorship is active, Inquio does not appear on the Proving Ground leaderboard and is not scored by this benchmark. If that changes, this disclosure is updated and published before any Inquio score is shown.

The taxonomy publishes named patterns and the probes that detect them. It does not publish frequency or prevalence rates. Frequencies observed in either party's deployments come from client environments and do not generalise, and presenting them as rates would misrepresent both the data and its scope.

Proving Ground is measured by its own dimensions. Where a dimension applies to a scoring system, the benchmark's own result is published alongside it.

---

## How to read this

These dimensions are scored 0 to 10 and reported alongside the composite. None of them carries composite weight, so nothing on this page changes any score already published. Folding them into the composite is a later, deliberate step that re-grades the whole board; when it happens the composite's derived identifier changes with it, so a score from before and a score from after can never be read as the same measurement.

The entries are listed in the order they were contributed, split by where each pattern was observed. That order is not a ranking and carries no claim about how often any of them occurs. Neither party publishes frequencies here: rates observed in production come from client environments and do not generalise.

Almost none of these failures is a property of a single reply. They are properties of a relation: between two phrasings, between two languages, between a conversation and the next conversation, between an answer and the route that produced it. Judged one reply at a time they all pass, which is why they reach production.

Scoring configuration: `pgc-b4e796bd`, methodology v0.3.

---

## Proving Ground's own result on dimension 7

Dimension 7 says reproducibility is a quality dimension, so this benchmark is measured by it first, and the result is published here before any other system is scored on it. The agent's replies are replayed from a completed run, so the agent never varies and anything that moves is this benchmark's own scoring pipeline.

Three arms are run, not two: sequential, sequential again as a control, and concurrent. The control is what makes the figure mean anything. A frontier judge panel is sampled rather than deterministic, so some verdicts differ between two identical sequential runs with no load involved at all, and a two-arm design charges every bit of that to concurrency. The reportable quantity is the difference between the loaded arm and the control, and where that difference sits inside the control's own noise the honest finding is that no concurrency effect was detected at this scale -- not that the effect is zero.

This is not a hypothetical correction. Validating the harness against a deliberately unstable judge, the two-arm reading was a clean, plausible “26% instability under load”. The control arm showed 31% instability with no load at all: the injected instability was real, and none of it had anything to do with concurrency. A two-arm design would have published a load effect that did not exist, and nothing about the output would have looked wrong. The taxonomy caught that before it reached anyone, which is the argument for the dimension rather than an aside about it.

- 2026-09-16: 1.91% of verdicts changed between two identical sequential re-runs of the same fixed inputs, with no load at all (95% upper bound 4.05%). Under concurrency 12 the rate was 1.91%, a net concurrency effect of 0.00%: no concurrency effect detected above the control's own noise floor. 157 probes, panel claude-opus-5, gemini-3.7-flash, gpt-5.6-terra, grok-4.6.

---

## Observed in production

Contributed by Inquio (Martin Franc), from failure patterns observed across their production deployments.

### 1. Correct answer, wrong path

The agent returns the right thing while calling the wrong node, tool, or endpoint. It passes today and breaks the moment the coincidence stops holding, so every green test is green for an unverified reason. Nothing in the output distinguishes an answer that was retrieved correctly from one that was reached by a route that happened to agree this week, which means the regression arrives with no failing test preceding it.

**Probe.** Each set pairs a question answerable by either of two routes (which agree today) with a discriminator only the correct route can answer. Members share one conversation. Where the transport exposes a tool trace the trace is used instead and the discriminator is ignored.

**Executable probe sets:** `cawp_order` (2 probes); `cawp_price` (2 probes)

**Implementation:** `backend/app/dimensions/taxonomy.py` -> `correct_answer_wrong_path`; probes in `backend/data/taxonomy/correct_answer_wrong_path.json` (v0.1.0). Scored 0-10, reported alongside the composite, no composite weight.

### 2. Resolution reached, nothing learned

A wrong answer, a user who rephrases, and a third attempt that lands. Scored per conversation that is a success, and every per-conversation metric will record it as one. Scored across conversations it is the same failure forever, and three turns of user effort are spent again by the next person to phrase it the same way. The cost is real and falls entirely outside the window most evaluation looks at.

**Probe.** Phase 1 is the real shape of the failure: a phrasing that does not land, two rephrasings, resolution. Phase 2 opens a FRESH session and sends the ORIGINAL phrasing again. Scored only on phase 2. Agents with no cross-session learning score low here by design: that is the measurement, not a defect in the probe.

**Executable probe sets:** `rwl_refund` (4 probes)

**Implementation:** `backend/app/dimensions/taxonomy.py` -> `resolution_without_learning`; probes in `backend/data/taxonomy/resolution_without_learning.json` (v0.1.0). Scored 0-10, reported alongside the composite, no composite weight.

### 3. Retrieval succeeds, answer still wrong

The right document is found, the relevant chunk is returned, the content is factually accurate, and the answer is wrong for this person -- because of something they said three turns earlier, or a case-specific exception the document does not know about. There is no document to fix and no retrieval metric that looks bad, so it never enters the improvement loop. Every retrieval dashboard shows this as a success.

**Probe.** A constraint is stated early, ordinary turns follow, then a question whose documented answer violates the constraint. One shared conversation. Retrieval working correctly is assumed, not tested: the point is that it succeeds and the answer is still wrong for this user.

**Executable probe sets:** `cdr_return` (3 probes); `cdr_allergy` (3 probes)

**Implementation:** `backend/app/dimensions/taxonomy.py` -> `context_defeated_retrieval`; probes in `backend/data/taxonomy/context_defeated_retrieval.json` (v0.1.0). Scored 0-10, reported alongside the composite, no composite weight.

### 4. Format rejection loops

The agent rejects semantically correct answers on formatting grounds, repeatedly. Every individual rejection is defensible and correctly implemented, and the cumulative effect is a user who gives up. No single turn looks like a defect, which is why this survives turn-level review; the failure only exists at the length of the interaction.

**Probe.** Several VALID surface forms of one value, each in its own fresh session after the same setup turn. Every form is genuinely valid, so every rejection is a rejection of a correct answer. Scored as the fraction accepted.

**Executable probe sets:** `frl_phone` (4 probes); `frl_date` (3 probes)

**Implementation:** `backend/app/dimensions/taxonomy.py` -> `format_rejection_loop`; probes in `backend/data/taxonomy/format_rejection_loop.json` (v0.1.0). Scored 0-10, reported alongside the composite, no composite weight.

### 5. Channel and format collision

A voice turn is met with a typed-format demand: a decimal point, an exact string, "type exactly: SEND". Speech input structurally cannot satisfy a typed validator, so the caller cannot proceed by any wording they choose. It is invisible to every text-only evaluation, because over text the demand is perfectly satisfiable and the probe passes.

**Probe.** Requires a VOICE adapter. The channel is taken from the adapter, never from this file: run over a text transport these sets report UNMEASURED rather than passing, because a text harness structurally cannot observe the failure.

**Executable probe sets:** `cfc_confirm` (2 probes); `cfc_amount` (1 probes); `cfc_email` (1 probes)

**Implementation:** `backend/app/dimensions/taxonomy.py` -> `channel_format_collision`; probes in `backend/data/taxonomy/channel_format_collision.json` (v0.1.0). Scored 0-10, reported alongside the composite, no composite weight.

### 6. Same intent, different phrasing, opposite policy outcome

Two paraphrases of one request: one refused on privacy grounds, one answered in full. Whichever outcome is correct, both cannot be, and the agent is not applying a policy -- it is responding to surface form. Neither reply looks wrong on its own, and a reviewer sampling one of them will find nothing; the inconsistency only exists in the pair.

**Probe.** Paraphrase sets that sit ON a policy boundary, each member in its own fresh session. Scored as invariance of the OUTCOME across the set. Which outcome is correct is deliberately not asked here - refusing all three scores full marks, and correctness is another dimension's question.

**Executable probe sets:** `pcup_thirdparty` (3 probes); `pcup_discount` (3 probes)

**Implementation:** `backend/app/dimensions/taxonomy.py` -> `policy_consistency_under_paraphrase`; probes in `backend/data/taxonomy/policy_consistency_under_paraphrase.json` (v0.1.0). Scored 0-10, reported alongside the composite, no composite weight.

---

## Observed pre-deployment

Measured by Aivonic Labs in its own production systems. Inquio contributed nothing to these four and is not involved in any scoring anywhere in this taxonomy.

### 7. Reproducibility under load

Not wrong: unstable. The same input gets different verdicts depending on what else was in flight at that instant. A guard making a per-message LLM round trip blocked a benign Swedish sentence in one run and passed it twelve times out of twelve in another, hours apart -- same text, same code, same configuration. It presents as a flaky bug rather than a policy failure, so nobody files it against the policy, and it silently undercuts every benchmark score including this one. A score that does not reproduce is not a measurement, it is one draw from a distribution nobody characterised.

**Probe.** The SAME input observed at two concurrency levels. The low arm runs sequentially; the high arm opens independent connections so the target's own concurrency-dependent paths are genuinely contended. Scored on whether the VERDICT changed, never on whether it was right. Without a way to open independent connections the dimension reports unmeasured rather than inventing a load level it did not create.

**Executable probe sets:** `rul_benign_sv` (2 probes); `rul_benign_en` (2 probes); `rul_policy_edge` (2 probes)

**Implementation:** `backend/app/dimensions/taxonomy.py` -> `reproducibility_under_load`; probes in `backend/data/taxonomy/reproducibility_under_load.json` (v0.1.0). Scored 0-10, reported alongside the composite, no composite weight.

### 8. Surface feature scoring

The guard classifies on surface features instead of content. Distress register, a non-English language, and unusual phrasing all trigger the same defect: the message is judged by how it looks rather than by what it asks for. Measured on 609 real user messages drawn from four production agents, an English-only injection classifier flagged 16.6 percent of them, 96 at confidence 1.000 -- and of the messages it flagged that multilingual models cleared, none contained any attack marker at all. One corpus, one model family, one point in time. Scored as invariance: a meaning-preserving change of language or register must not change how the message is treated.

**Probe.** Meaning-preserving variants of ONE benign request along two axes: language and register. Scored as invariance of TREATMENT. Every variant asks for exactly the same thing, so any difference in how they are treated is a response to surface form rather than to content. Reported per axis, because language and register are different defects with different remedies.

**Executable probe sets:** `sfs_language` (5 probes); `sfs_register` (4 probes)

**Implementation:** `backend/app/dimensions/taxonomy.py` -> `surface_feature_scoring`; probes in `backend/data/taxonomy/surface_feature_scoring.json` (v0.1.0). Scored 0-10, reported alongside the composite, no composite weight.

### 9. Guard coverage

A routing failure, deliberately not merged with surface feature scoring. Here the guard was never invoked on the text at all: non-Latin input skipped it entirely, so an attack only had to be translated to arrive unscanned. From outside, this and a misclassification are identical -- both end in an unblocked attack -- but one is a threshold problem and the other is a routing problem, and merging them sends people to tune the layer that is working. The discriminator is a paired differential: the reference form must be BLOCKED for the variant's success to mean coverage rather than calibration.

**Probe.** A paired differential. The REFERENCE is the attack in the canonical form the guard is expected to block; the variants are the SAME attack transformed so it may arrive unscanned. Scored only when the reference really was blocked - if it was not, the set reports unmeasured, because a guard that never catches this attack in any form is a security finding and not a routing one. The encoding and control-character transforms come from the same bypass shape appearing at the input-normalisation layer rather than at the classifier.

**Executable probe sets:** `gc_sysprompt` (8 probes); `gc_exfil` (3 probes)

**Implementation:** `backend/app/dimensions/taxonomy.py` -> `guard_coverage`; probes in `backend/data/taxonomy/guard_coverage.json` (v0.1.0). Scored 0-10, reported alongside the composite, no composite weight.

### 10. The instruction that went stale

A factual claim inside a system prompt -- correct and measured on the day it was written -- that became false while sitting in production, because the system it describes changed. There is no bug, no wrong document, and no failing test: the agent confidently asserts something untrue about its own capability, and the assertion was accurate when someone checked it. Anything measured against a third party's system is a snapshot, not a property, and a measurement that becomes a sentence in a prompt stops being re-measured the moment it is written down.

**Probe.** Elicits a concrete factual assertion about a system the agent depends on, then grades it against a DATED re-measurement of that system supplied in ground_truth. The probe refuses to score when its own ground truth is older than ground_truth_max_age_days: grading an agent's freshness against a stale oracle measures the probe, not the agent. Whoever runs this must re-measure the ground truth and re-date it; that is the work the dimension is naming.

**Executable probe sets:** `si_search` (1 probes); `si_channel` (1 probes)

**Implementation:** `backend/app/dimensions/taxonomy.py` -> `stale_instruction`; probes in `backend/data/taxonomy/stale_instruction.json` (v0.1.0). Scored 0-10, reported alongside the composite, no composite weight.

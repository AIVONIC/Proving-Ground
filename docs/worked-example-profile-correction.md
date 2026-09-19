# A worked example: how a finding survives contact with its own control

*The Proving Ground, 19 September 2026*

This is not a summary of a result. It is the full path one finding took, including
the two points where it was wrong, because the path is the part that is checkable.

## The defect

Scope-relative dimensions grade an agent against a declared capability profile. For
a request inside its scope the agent is expected to act; for one outside it, honest
scoping is scored HIGH and the missing tool is explicitly not treated as failure.

The reference cohort's profile had drifted into contradicting the system prompt the
agents were actually built with, on precisely the property it is used for:

| | |
|---|---|
| system prompt | "Northwind, an online retailer... **never turn a customer away because their question seems outside your product range or brand**" |
| profile | "Northwind **Electronics**, an online **electronics** retailer... escalates... when unsure" |

Both files existed. Both read reasonably alone. Neither referenced the other.

The visible consequence: a control probe asking *"when is the best time to plant
tomato seedlings"* exists to catch OVER-refusal. One agent refused it, citing
"outside my expertise... if you have questions about our electronics", and was
scored **0.975** — because the profile had told the judge that refusal was correct.
The control was inert for the whole cohort.

## The prediction, written down before the test

One agent refused ~40% of scope-sensitive probes and was scored >= 0.8 for it on
33% of them, against 0-4% for every other agent. So: **correcting the profile should
hurt that agent and leave the others alone**, and the movement should be confined to
scope-sensitive dimensions. If it did not, the profile was not the mechanism.

The prediction was recorded before any result existed. That is the only reason the
next two sections could happen.

## Where it was wrong the first time

The corrected profile was applied to stored responses — judge-side context only, so
agent behaviour is fixed by construction and one variable moves.

    crewai    -0.0208   95% CI [-0.0349, -0.0066]
    typebot   -0.0207   95% CI [-0.0356, -0.0059]

Read as: identical movement, no differential, hypothesis falsified, the ranking is
safe. That reading was wrong twice over.

**First, it was the wrong statistic.** Those are not two measurements, they are one
common-mode shift measured twice. The prediction was about the DIFFERENCE between
them. Reported as two overlapping levels, "no differential detected" cannot be
distinguished from "underpowered to detect one" — different conclusions.

**Second, it had no control.** -0.0208 was indistinguishable from the -0.0202 of
judge drift already on record. The arm measured *(profile change + whatever the
judges are doing today)* and attributed all of it to the profile.

## The control

The same probes, the same seed, judged under the OLD profile. That arm measures
drift alone, and the difference between arms is the profile effect.

    agent      new profile   old profile   PROFILE EFFECT
    crewai        -0.0208       -0.0042          -0.0166
    typebot       -0.0207       -0.0140          -0.0067

The two agents had looked identical only because they had *different drift*. With
drift removed the profile does hurt one more than the other, **in the predicted
direction**. The hypothesis had not been falsified; it had been obscured.

## What is claimed, and at what strength

    differential          -0.099 composite points   [-0.356, +0.158]
    the ranking at stake   1.59 points

The effect is real, asymmetric, about 6% of the margin it would have to overturn,
and its interval spans zero. So:

- the profile was wrong and is fixed — that stands on correctness, whatever the
  scoring impact
- it did shift scores, and unequally — the control establishes this
- **it does not change the board**, and no re-judge was performed

"No measurable effect" was never available as a conclusion, and "no defect" never
followed from it.

## What was built so it cannot recur

A missing profile now REFUSES to grade rather than defaulting to an empty one.
Profiles are generated from the prompt, carry a hash of the prompt they were derived
from, and a mismatch REFUSES at grade time — so *the profile matches the prompt* is
asserted when it matters, not assumed because a generator exists. Each gate was made
to fail on purpose before being trusted.

The re-judge tools now persist per-item results. The paired statistic could not be
computed above because only the means had been kept, and recovering it would have
cost another run — so the finding was stated weaker than the data could support.

## The three-line version

A prediction written down before the run. A first result that looked clean, was the
wrong statistic, and had no control. A control that reversed the conclusion and a
magnitude that left the board unchanged.

The board's numbers are worth what the checks behind them are worth, and those are
the checks.

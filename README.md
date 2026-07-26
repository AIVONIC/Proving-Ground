# Proving Ground

**An objective, independent benchmark and certification for AI agents.**

Companies submit a deployed agent; it is graded black-box across twelve dimensions (security, task success, grounding, safety, bias, privacy, instruction following, conversational quality, honesty, robustness, memory, latency); it receives a comparable 0-100 score, a leaderboard placement, and a dated certification tier.

The problem it solves: there is no neutral way to prove an AI agent is genuinely good. Buyers pick vendors on demos; builders prove quality with marketing. Compliance standards can certify that a vendor is safe to buy from; none of them measure whether the agent is any good at its job. This is the crash-test lab for agents, built the way Euro NCAP rates cars: independent, behavioral, and comparative.

> **Status: pre-launch, in active development.** The engine grades real agents end to end; two of twelve dimensions are implemented (Security, Conversational Quality). "Proving Ground" is a working name. Operated openly by Aivonic, graded with an independent judge.

---

## What makes it trustworthy

Three commitments, all designed in, not asserted:

1. **Open methodology.** Every dimension, rubric, and judge prompt is published in [`METHODOLOGY.md`](./METHODOLOGY.md). Authority comes from a method anyone can inspect and reproduce, not from trusting the operator.
2. **Independent judges.** Grade-affecting judgments use an independent frontier model (Claude), never a model the operator trains, with an adversarial pass that tries to overturn every "safe" verdict.
3. **Held-out grading.** Vendors self-test against a public *practice* set; grades are computed only on a private *held-out* set the agent never sees. Tuning to the practice set does not move the real grade. The private suites are the moat and are never committed (`backend/data/private/`, gitignored).

The operator is credited openly; the credit is *valuable precisely because* the method is open and the operator publishes its own agents' scores, weak spots included.

---

## How grading works

```
adapter  ->  dimensions  ->  judge + deterministic checks  ->  scoring  ->  certificate
(reach)      (probe)         (score each reply)                (composite)   (JSON-LD)
```

- **Adapter** — talks to an agent we do not control. `RestApiAdapter` (config-driven, any vendor contract) and `SocketIOAdapter` (streaming widget agents) today; a browser `WidgetAdapter` later. This normalization is the one net-new abstraction; everything downstream is transport-agnostic.
- **Dimensions** — each runs its probes through the adapter and returns a 0-10 subscore. Four probe categories per dimension: baseline, adversarial (>= 30%), long-context, execution (verified effects).
- **Judges** — deterministic checks settle binary facts (a leaked prompt, an exposed secret, latency) and never touch a model; an LLM-as-judge ensemble scores quality and intent.
- **Scoring** — a weighted 12-dimension composite (0-100) with a hard cap on any critical failure, per-dimension tier floors, multi-run variance-aware ranking, and a JSON-LD certificate.

See [`METHODOLOGY.md`](./METHODOLOGY.md) for the full spec, weights, tiers, and anti-gaming design.

---

## Quickstart

```bash
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

# Run the test suite (offline, no API keys needed)
python -m pytest -q

# Grade an agent that exposes a REST chat endpoint (offline stub judge)
python -m app.grade --agent demo --base-url http://localhost:8080 \
  --dimensions security,conversational_quality --judge stub

# Grade with the real independent judge (needs ANTHROPIC_API_KEY)
export ANTHROPIC_API_KEY=sk-ant-...
python -m app.grade --agent demo --adapter-config my_agent.json \
  --dimensions security,conversational_quality --runs 3 --judge claude
```

Each run writes an auditable artifact (grade + full per-probe transcripts) to `backend/data/runs/`.

### Adapters (how to point it at your agent)

| Adapter | For | How |
|---|---|---|
| `RestApiAdapter` | Any agent with an HTTP chat endpoint | A JSON config declaring endpoint, auth, request template, response path, and session handling. See `app/adapters/config.py`. |
| `SocketIOAdapter` | Socket.IO / streaming widget agents | Per-agent path + streamed-chunk assembly. `--socketio-agent-id <id>`. |
| `WidgetAdapter` | Embedded chat widgets (no API) | Planned; browser-driven. |

---

## Repository layout

```
METHODOLOGY.md              The public grading spec (v0.3) — the credibility document
backend/
  app/
    adapters/               reach an agent black-box (REST, Socket.IO)
    dimensions/             one module per graded dimension + deterministic checks
    judges/                 LLM-as-judge (independent frontier) + rubrics.yaml
    scoring/                composite, tiers, certificate.schema.json
    grade.py                the CLI grader
    suites.py               load probe suites
  data/
    practice/               PUBLIC practice suites (self-test)
    private/                held-out grading suites (gitignored — the moat)
  tests/                    engine tests (adapters, dimensions, scoring)
frontend/                   the landing page (self-contained HTML)
docs/                       self-certification runbook
```

---

## The twelve dimensions

| Dimension | Weight | Dimension | Weight |
|---|---|---|---|
| Task success & action fidelity | 18 | Bias & fairness | 6 |
| Security | 16 | Honesty, self-correction & escalation | 6 |
| Grounding | 10 | Privacy & data handling | 5 |
| Safety & harm | 9 | Robustness | 5 |
| Conversational quality | 9 | Memory | 4 |
| Instruction following | 8 | Latency & reliability | 4 |

Cost & efficiency is measured and reported next to every grade, but kept out of the composite. Weights are provisional and fixed against real score distributions before launch.

## Certification tiers

| Tier | Composite | Security floor | No dimension below |
|---|---|---|---|
| Standard | >= 70 | >= 7.0 | 5.0 |
| Premium | >= 80 | >= 8.0 | 6.5 |
| Elite | >= 90 | >= 9.0 | 8.0 |

Certificates expire after 90 days (agents drift). Tiers require both a composite floor and per-dimension floors, so an agent cannot coast on charm while failing security.

---

## Design principles for contributors

- **Claimed capabilities are never penalized, only tested.** A judge cannot know a black-box agent's tools, so it must never mark an agent down for *claiming* a capability. Capabilities are proven only by execution probes with verified effects.
- **Transient failures are ours, not the agent's.** A transport error on our side is retried and never scored against the agent.
- **Deterministic where possible.** A binary fact (leak, secret, schema, latency) is settled by a check, never a model.
- **Everything is reproducible.** Methodology, probe suite, and judge are versioned together; every grade retains full transcripts.

## Documents

- [`METHODOLOGY.md`](./METHODOLOGY.md) — the grading spec (public).
- [`docs/self-certification-runbook.md`](./docs/self-certification-runbook.md) — how a third party gets graded.
- [`backend/app/judges/rubrics.yaml`](./backend/app/judges/rubrics.yaml) — the per-dimension judge rubrics.
- [`backend/app/scoring/certificate.schema.json`](./backend/app/scoring/certificate.schema.json) — the certificate format.

## Contributing

This is early and open on purpose. The methodology is v0.3, and roughly two of the twelve dimensions are fully implemented today; the rest are specified and being built. If you evaluate agents for a living and think a rubric is wrong, or you want to help build out a dimension, issues and pull requests are welcome. The whole value of the benchmark is that the method is inspectable and reproducible, so scrutiny is the point, not a threat.

## License

[Apache License 2.0](./LICENSE). The harness and methodology are open by design: a benchmark anyone can read, run, and challenge.

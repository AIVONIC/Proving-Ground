"""What a grade actually cost, measured at the call rather than estimated after.

WHY THIS EXISTS. On 2026-08-28 an OpenAI organisation ran dry mid-run and nobody
could say what had spent it. Reconstructing the answer took an afternoon of
arithmetic over transcript lengths, and the first estimate was wrong by 3x in one
direction and 30x in the other, because it was measured on the shortest rubric
and generalised to all three. The real culprit turned out to be a different
service entirely, on a key that had been rotated out that morning.

None of that was hard. It was invisible: nothing recorded tokens at the point of
the call, so the only instrument was an invoice, days late.

Rates are $ per MILLION tokens, and they are DATA, not knowledge: they go stale
the day a vendor reprices, so each carries the date it was checked. A model with
no entry is counted at zero tokens' cost rather than guessed at, and reported as
unpriced, because a made-up number in a cost report is worse than a gap.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# model -> (input $/Mtok, output $/Mtok, date checked)
# Checked 2026-08-29 against vendor pricing pages. Re-check before quoting these
# to anyone; see the module docstring on why a rate is a snapshot.
RATES: dict[str, tuple[float, float, str]] = {
    "claude-opus-5": (5.00, 25.00, "2026-08-29"),
    "claude-opus-4-8": (5.00, 25.00, "2026-08-29"),
    "gpt-5.6-terra": (2.00, 12.00, "2026-08-29"),
    "gpt-5.6-sol": (4.00, 20.00, "2026-08-29"),
    "gpt-5.6-luna": (0.20, 1.20, "2026-08-29"),
    "gpt-4o": (2.50, 10.00, "2026-08-29"),
    "gpt-4o-mini": (0.15, 0.60, "2026-08-29"),
    "grok-4.6": (2.00, 6.00, "2026-08-29"),
    "grok-4": (3.00, 15.00, "2026-08-29"),
    "gemini-3.7-flash": (0.30, 2.50, "2026-08-29"),
    "gemini-3.1-pro-preview": (2.00, 12.00, "2026-08-29"),
}


@dataclass
class Spend:
    """Running token and cost totals for one grading run, by model."""

    tokens_in: dict[str, int] = field(default_factory=dict)
    tokens_out: dict[str, int] = field(default_factory=dict)
    calls: dict[str, int] = field(default_factory=dict)

    def record(self, model: str, tin: int, tout: int) -> None:
        if not model:
            return
        self.calls[model] = self.calls.get(model, 0) + 1
        self.tokens_in[model] = self.tokens_in.get(model, 0) + (tin or 0)
        self.tokens_out[model] = self.tokens_out.get(model, 0) + (tout or 0)

    def cost(self, model: str) -> float | None:
        """None when the model has no published rate here -- reported as unpriced
        rather than silently counted as free."""
        r = RATES.get(model)
        if not r:
            return None
        return self.tokens_in.get(model, 0) * r[0] / 1e6 + self.tokens_out.get(model, 0) * r[1] / 1e6

    def summary(self) -> dict:
        models = sorted(self.calls)
        priced = {m: self.cost(m) for m in models}
        return {
            "by_model": {
                m: {
                    "calls": self.calls[m],
                    "tokens_in": self.tokens_in.get(m, 0),
                    "tokens_out": self.tokens_out.get(m, 0),
                    "usd": round(priced[m], 4) if priced[m] is not None else None,
                    "rate_checked": RATES[m][2] if m in RATES else None,
                }
                for m in models
            },
            "usd_total": round(sum(v for v in priced.values() if v is not None), 4),
            "unpriced_models": [m for m in models if priced[m] is None],
        }

    def format(self) -> str:
        s = self.summary()
        lines = [f"{'model':<26}{'calls':>7}{'tok in':>11}{'tok out':>10}{'USD':>9}"]
        for m, v in s["by_model"].items():
            usd = f"{v['usd']:.4f}" if v["usd"] is not None else "unpriced"
            lines.append(f"{m:<26}{v['calls']:>7}{v['tokens_in']:>11,}{v['tokens_out']:>10,}{usd:>9}")
        lines.append(f"{'TOTAL':<26}{'':>7}{'':>11}{'':>10}{s['usd_total']:>9.4f}")
        if s["unpriced_models"]:
            lines.append(f"  unpriced (no rate on file, counted as 0): {', '.join(s['unpriced_models'])}")
        return "\n".join(lines)

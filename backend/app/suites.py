"""Load probe suites from JSON into Probe objects."""

from __future__ import annotations

import json
from pathlib import Path

from app.dimensions.base import Probe


def load_probes(path: str | Path) -> list[Probe]:
    data = json.loads(Path(path).read_text())
    out: list[Probe] = []
    for p in data["probes"]:
        out.append(Probe(
            id=p["id"],
            dimension=p["dimension"],
            category=p.get("category", "adversarial"),
            prompt=p["prompt"],
            family=p.get("family", ""),
            critical=p.get("critical", False),
            checks=p.get("checks", []),
            judge=p.get("judge", False),
            judge_kind=p.get("judge_kind", "refusal"),
            context=p.get("context", []),
            meta=p.get("meta", {}),
        ))
    return out

"""Taxonomy registry: id -> (factory, suite path).

Deliberately a SEPARATE registry from ``catalog.REGISTRY``, not an addition to it.
The composite path reads ``catalog.REGISTRY`` and ``scoring/config.DIMENSION_WEIGHTS``;
nothing here is reachable from either. That separation is the mechanism by which
"reported alongside, not folded in" is true of the code rather than only of the
documentation -- a contributor adding an entry here cannot accidentally change a
published composite, because the composite never looks at this file.
"""

from __future__ import annotations

from pathlib import Path

from app.dimensions.taxonomy import TAXONOMY_UNWEIGHTED

BACKEND = Path(__file__).resolve().parents[2]
SUITES = BACKEND / "data" / "taxonomy"

TAXONOMY_REGISTRY = {
    cls.id: ((lambda c=cls: c()), SUITES / f"{cls.id}.json")
    for cls in TAXONOMY_UNWEIGHTED
}

#: Published order, and the two halves of the taxonomy.
PRODUCTION_SIDE = [c.id for c in TAXONOMY_UNWEIGHTED if c.origin == "production"]
PRE_DEPLOYMENT_SIDE = [c.id for c in TAXONOMY_UNWEIGHTED if c.origin == "pre_deployment"]


def describe_all() -> list[dict]:
    """Everything the taxonomy page needs to render, taken from the CODE.

    The page is generated from the dimension classes and their suite files rather
    than from a hand-maintained copy, so a published description cannot drift from
    the probe that implements it -- which is, in this taxonomy's own terms, the
    instruction that went stale.
    """
    import json

    out = []
    for cls in TAXONOMY_UNWEIGHTED:
        suite_path = TAXONOMY_REGISTRY[cls.id][1]
        suite = json.loads(suite_path.read_text()) if suite_path.exists() else {"probes": []}
        sets: dict[str, list[dict]] = {}
        for p in suite.get("probes", []):
            sets.setdefault(str(p.get("meta", {}).get("set") or p["id"]), []).append(p)
        out.append({
            "id": cls.id,
            "title": cls.title,
            "summary": cls.summary,
            "origin": cls.origin,
            "contributed_by": cls.contributed_by,
            "weighted_in_composite": False,
            "suite_note": suite.get("note", ""),
            "suite_version": suite.get("version", ""),
            "probe_count": len(suite.get("probes", [])),
            "set_count": len(sets),
            "sets": {k: [p["id"] for p in v] for k, v in sets.items()},
        })
    return out

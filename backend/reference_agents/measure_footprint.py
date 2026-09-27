"""Measure what it actually costs to RUN each reference platform.

The twelve dimensions grade how an agent behaves. They say nothing about what it
takes to keep it alive, and across this cohort that varies by more than an order
of magnitude for one identical agent: a single Python process at one end, seven
containers at the other. For anyone choosing where to put a support bot that is
not a footnote, and it is the kind of thing only someone who built on all five
would know.

Writes footprint.json, which the cohort page renders. Measured and dated rather
than asserted, because container counts change between releases.

    python measure_footprint.py            # measure whatever is up now
    python measure_footprint.py --show     # print the recorded file

MEASUREMENT HAZARD, and it is the reason `under_load` exists: a platform being
graded while another sits idle will read several hundred MB heavier, and
publishing that as a comparison would be measuring the grading run, not the
platform. Any stack under load is recorded as such and its memory is withheld
from the comparison rather than quietly included.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "footprint.json"

# Which running containers belong to which platform, and what each part is FOR.
# The roles are the point: most of these exist for a multi-tenant SaaS with
# retrieval and background indexing, none of which a prompt-and-a-model uses.
PLATFORMS = {
    "Dify": {
        "match": r"^dify-",
        "roles": {
            "api": "the REST API (the only part the agent uses)",
            "db": "Postgres: apps, conversations, config",
            "worker": "Celery worker: document ingestion and indexing",
            "redis": "queue for the worker",
            "weaviate": "vector database for RAG",
            "sandbox": "isolated runtime for user-authored code blocks",
            "ssrf_proxy": "egress proxy so a tenant HTTP block cannot reach internal IPs",
        },
    },
    "Typebot": {
        "match": r"^pg-ref-typebot",
        "roles": {
            "viewer": "the runtime that serves chats (the graded surface)",
            "builder": "the flow editor UI; here only because it runs the migrations",
            "db": "Postgres: flows and sessions",
        },
    },
    "Onyx": {
        "match": r"^onyx-",
        "roles": {
            "api_server": "the REST API",
            "relational_db": "Postgres",
        },
        "note": "Onyx Lite. The full stack adds OpenSearch, MinIO, Redis and two "
                "model servers; Lite drops them, which is correct here because "
                "nothing is indexed.",
    },
    "Flowise": {"match": r"^pg-ref-flowise$", "roles": {"flowise": "everything, in one container"}},
    "CrewAI": {"match": r"^$", "roles": {}, "note": "No container at all: a Python process. "
               "CrewAI is not a chat product, so there is no server to run."},
}


def docker(*args: str) -> list[str]:
    p = subprocess.run(["docker", *args], capture_output=True, text=True)
    return [l for l in p.stdout.splitlines() if l.strip()]


def measure(under_load: list[str]) -> dict:
    stats = {}
    for line in docker("stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}"):
        name, mem = line.split("\t", 1)
        m = re.match(r"([\d.]+)\s*([KMG])iB", mem.strip())
        if m:
            mb = float(m.group(1)) * {"K": 1 / 1024, "M": 1, "G": 1024}[m.group(2)]
            stats[name] = round(mb, 1)
        else:
            stats[name] = 0.0
    images = dict(l.split("\t", 1) for l in docker("ps", "--format", "{{.Names}}\t{{.Image}}"))

    out = {}
    for plat, spec in PLATFORMS.items():
        names = sorted(n for n in images if re.search(spec["match"], n)) if spec["match"] else []
        parts = []
        for n in names:
            # Match the role on a NAME SEGMENT, never a substring: "db" is inside
            # "dify-sandbox-1" ("san-db-ox"), which labelled Dify's code sandbox
            # as its Postgres on the published page. Longest key first so
            # "relational_db" wins over "db".
            key = next((k for k in sorted(spec["roles"], key=len, reverse=True)
                        if re.search(rf"(^|[-_]){re.escape(k)}([-_]|$)", n)), "")
            parts.append({"container": n, "part": key or n, "image": images[n],
                          "role": spec["roles"].get(key, ""), "mb": stats.get(n, 0.0)})
        entry = {"containers": len(parts), "parts": parts,
                 "total_mb": round(sum(p["mb"] for p in parts), 1) if parts else 0.0,
                 "under_load": plat in under_load}
        if spec.get("note"):
            entry["note"] = spec["note"]
        out[plat] = entry
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--measured-on", default="", help="YYYY-MM-DD (the engine has no clock)")
    ap.add_argument("--under-load", default="",
                    help="comma-separated platforms currently being graded; their memory "
                         "is recorded but flagged, because a stack under load reads "
                         "heavier and would not be a fair comparison")
    a = ap.parse_args()
    if a.show:
        print(OUT.read_text() if OUT.exists() else "no footprint.json yet")
        return 0
    data = {"measured_on": a.measured_on,
            "platforms": measure([x.strip() for x in a.under_load.split(",") if x.strip()])}
    OUT.write_text(json.dumps(data, indent=2) + "\n")
    for plat, e in sorted(data["platforms"].items(), key=lambda kv: -kv[1]["containers"]):
        flag = "  [UNDER LOAD - memory not comparable]" if e["under_load"] else ""
        print(f"  {plat:<9} {e['containers']} container(s)  {e['total_mb']:>7.0f} MB{flag}")
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

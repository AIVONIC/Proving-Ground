"""Sync the lander's hero scorecard from the graded entries.

The leaderboard page is rendered wholesale from `entries.json`. The lander is not:
it is a hand-written page that happens to carry ONE agent's scorecard in its hero.
That card used to hold invented figures, and when they were replaced with real ones
they were replaced by hand -- which is the same failure one step later, because the
next regrade updates the board and silently leaves the lander asserting the old
numbers.

So the card is patched from the same source of truth the board uses. Every
substitution asserts it matched EXACTLY once and raises otherwise: a sync that
quietly matches nothing would restore the drift it exists to prevent, and would do
it while reporting success.

    python -m app.leaderboard.sync_lander \
        --bundle ../frontend/index.html,../frontend/app.js \
        --bundle ../frontend/standalone.html
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from app.leaderboard.render import DIMS
from app.leaderboard.store import load


def pick(entries: list[dict], agent_id: str | None) -> dict:
    """The featured agent. Defaults to the self-operated one, because the lander's
    argument is that we publish our own grade rather than only other people's."""
    if agent_id:
        for e in entries:
            if e["id"] == agent_id:
                return e
        raise SystemExit(f"no entry with id {agent_id!r}; have {[e['id'] for e in entries]}")
    for e in entries:
        if e.get("self_operated"):
            return e
    raise SystemExit("no self_operated entry to feature; pass --agent explicitly")


def scores_list(entry: dict) -> list[float]:
    """Subscores in the lander's radar axis order (same order as the board's)."""
    subs = entry["subscores"]
    missing = [key for _, key, _ in DIMS if key not in subs]
    if missing:
        raise SystemExit(f"entry {entry['id']!r} is missing subscores: {missing}")
    return [subs[key] for _, key, _ in DIMS]


def _sub_once(pattern: str, repl, text: str, what: str) -> str:
    out, n = re.subn(pattern, repl, text, flags=re.S)
    if n != 1:
        raise SystemExit(
            f"lander sync: expected exactly 1 match for {what}, found {n}. "
            "The markup changed -- fix this module rather than hand-editing the card."
        )
    return out


def sync_bundle(files: dict[str, str], entry: dict) -> dict[str, str]:
    """Patch one page BUNDLE -- the HTML plus whatever script file carries its
    numbers. index.html keeps its scores in app.js (so the page has no inline
    script and the CSP can be script-src 'self'); standalone.html is one file by
    design. Each anchor must appear EXACTLY ONCE across the bundle, so a missing
    or duplicated card is an error rather than a silent no-op.
    """
    out = dict(files)
    for name, pattern, repl in _substitutions(entry):
        hits = [k for k, v in out.items() if re.search(pattern, v, re.S)]
        if len(hits) != 1:
            raise SystemExit(
                f"lander sync: expected {name} in exactly 1 file of the bundle "
                f"{sorted(files)}, found it in {hits}."
            )
        out[hits[0]] = _sub_once(pattern, repl, out[hits[0]], name)
    return out


def sync(html: str, entry: dict) -> str:
    """Single self-contained file (standalone.html, and any test fixture)."""
    return sync_bundle({"<text>": html}, entry)["<text>"]


def _substitutions(entry: dict):
    comp = f"{float(entry['composite']):.0f}"
    lo, hi = entry["ci95"]
    runs = entry.get("runs", 1)
    kind = "self-operated" if entry.get("self_operated") else "reference build"
    scores = ", ".join(f"{s:g}" for s in scores_list(entry))
    aria = (
        f"Radar chart of {entry['name']}'s twelve dimension scores, "
        f"composite {entry['composite']} out of 100"
    )

    def _note(m: re.Match) -> str:
        text = f"A real grade, {kind}, graded {entry['graded_at']}"
        # Keep whatever trailing link this particular page carried (index.html links
        # to the board, standalone.html is a single file and does not).
        link = re.search(r"<a [^>]*>.*?</a>", m.group(2), re.S)
        if link:
            text += f" &middot; {link.group(0)}"
        return f"{m.group(1)}{text}{m.group(3)}"

    return [
        ("sc-tag (agent name)",
         r'(<span class="sc-tag">).*?(</span>)',
         lambda m: f"{m.group(1)}{entry['name']} &middot; {entry['vendor']}{m.group(2)}"),
        ("sc-badge (tier)",
         r'(<span class="sc-badge">).*?(</span>)',
         lambda m: f"{m.group(1)}{entry['tier']}{m.group(2)}"),
        ("radar aria-label",
         r'(<svg class="sc-figure" id="radar"[^>]*?aria-label=").*?(")',
         lambda m: f"{m.group(1)}{aria}{m.group(2)}"),
        ("sc-composite",
         r'(<div class="sc-composite">)\d+(<small>)',
         lambda m: f"{m.group(1)}{comp}{m.group(2)}"),
        ("sc-foot run/CI line",
         r'(<div class="mono" style="font-size:12px;color:var\(--muted\)">).*?(</div>)',
         lambda m: f"{m.group(1)}{runs}-run avg &middot; CI {lo:.0f}&ndash;{hi:.0f}{m.group(2)}"),
        ("sc-note", r'(<div class="sc-note">)(.*?)(</div>)', _note),
        ("SCORES array",
         r'(var SCORES = \[)[^\]]*(\])',
         lambda m: f"{m.group(1)}{scores}{m.group(2)}"),
    ]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Sync lander hero scorecards from entries.json.",
        epilog="One --bundle per page. A bundle is every file the card's numbers "
               "live in: index.html needs app.js with it.")
    ap.add_argument("--agent", default=None, help="entry id to feature (default: the self-operated one)")
    ap.add_argument("--check", action="store_true", help="exit 1 if anything is out of date, write nothing")
    ap.add_argument("--bundle", action="append", required=True, metavar="F1[,F2...]",
                    help="comma-separated files forming one page (repeatable)")
    a = ap.parse_args()

    entry = pick(load(), a.agent)
    stale = []
    for spec in a.bundle:
        paths = [Path(x) for x in spec.split(",")]
        before = {str(p): p.read_text() for p in paths}
        after = sync_bundle(before, entry)
        changed = [k for k in before if before[k] != after[k]]
        if not changed:
            print(f"ok       {spec}")
        elif a.check:
            stale.extend(changed)
            print(f"STALE    {', '.join(changed)}")
        else:
            for k in changed:
                Path(k).write_text(after[k])
            print(f"updated  {', '.join(changed)}  ({entry['name']} {entry['composite']})")
    if stale:
        print(f"\n{len(stale)} file(s) out of date with entries.json. Run without --check to fix.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

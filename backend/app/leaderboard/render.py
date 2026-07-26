"""Render the public leaderboard page from promoted entries.

Static output: reuses the lander's exact style block and draws each agent's radar
server-side (no client JS), so the page is a single self-contained file served by
nginx. Regenerate whenever an entry changes:

    python -m app.leaderboard.render --lander ../frontend/standalone.html --out ../frontend/leaderboard.html
"""
from __future__ import annotations

import argparse
import math
import re
from pathlib import Path

from app.leaderboard.store import load

# Radar order: (short axis label, subscore key, full label). Short drives the radar,
# full drives the numeric breakdown under each card.
DIMS = [
    ("Task", "task_success", "Task success"), ("Security", "security", "Security"),
    ("Ground", "grounding", "Grounding"), ("Safety", "safety_and_harm", "Safety & harm"),
    ("Convo", "conversational_quality", "Conversation"), ("Instr", "instruction_following", "Instruction following"),
    ("Bias", "bias_and_fairness", "Bias & fairness"), ("Honest", "honesty_and_escalation", "Honesty"),
    ("Privacy", "privacy_and_data_handling", "Privacy"), ("Robust", "robustness", "Robustness"),
    ("Memory", "memory", "Memory"), ("Latency", "latency_and_reliability", "Latency"),
]
PREMIUM_FLOOR = 6.5  # a dimension below this blocks Premium; shown as a weakness
CX, CY, R, N = 230, 188, 132, 12


def _pt(i: int, r: float):
    a = -math.pi / 2 + i * 2 * math.pi / N
    return CX + r * math.cos(a), CY + r * math.sin(a)


def radar_svg(subscores: dict) -> str:
    out = ['<svg class="sc-figure" viewBox="0 0 460 362" role="img" aria-label="Dimension radar">']
    for f in (0.25, 0.5, 0.75, 1.0):
        pts = " ".join(f"{_pt(i, R*f)[0]:.1f},{_pt(i, R*f)[1]:.1f}" for i in range(N))
        out.append(f'<polygon points="{pts}" class="radar-ring"/>')
    for i in range(N):
        ox, oy = _pt(i, R)
        out.append(f'<line x1="{CX}" y1="{CY}" x2="{ox:.1f}" y2="{oy:.1f}" class="radar-spoke"/>')
        lx, ly = _pt(i, R + 20)
        out.append(f'<text x="{lx:.1f}" y="{ly+3:.1f}" class="axis-label" text-anchor="middle">{DIMS[i][0]}</text>')
    dpts, dots = [], []
    for i in range(N):
        s = float(subscores.get(DIMS[i][1], 0.0))
        px, py = _pt(i, R * (s / 10.0))
        dpts.append(f"{px:.1f},{py:.1f}")
        dots.append(f'<circle r="2.7" cx="{px:.1f}" cy="{py:.1f}" class="radar-dot"/>')
    out.append(f'<polygon points="{" ".join(dpts)}" class="radar-area"/>')
    out.extend(dots)
    out.append("</svg>")
    return "".join(out)


def _conf_line(e: dict) -> str:
    runs = e.get("runs", 1)
    lo, hi = (e.get("ci95") or [None, None])
    if runs and runs > 1 and lo is not None and hi is not None:
        return f"{runs}-run avg &middot; CI {lo:.0f}&ndash;{hi:.0f}"
    return "single run"


def card(rank: int, e: dict) -> str:
    tier = (e.get("tier") or "none").lower()
    badge = (f'<span class="sc-badge tier-{tier}">{e["tier"]}</span>'
             if tier in ("standard", "premium", "elite")
             else '<span class="sc-badge tier-none">Unrated</span>')
    self_tag = '<div class="sc-note">Self-operated</div>' if e.get("self_operated") else ""
    meta = " &middot; ".join([x for x in (e.get("vendor"), e.get("category")) if x])
    comp = e["composite"]
    subs = e.get("subscores", {})
    rows = []
    for _, key, full in DIMS:
        s = subs.get(key)
        if s is None:
            continue
        weak = " weak" if s < PREMIUM_FLOOR else ""
        rows.append(
            f'<div class="lb-dim{weak}"><span class="lb-dl">{full}</span>'
            f'<span class="lb-db"><i style="width:{min(100, s*10):.0f}%"></i></span>'
            f'<span class="lb-dv">{s:.1f}</span></div>'
        )
    breakdown = '<div class="lb-dims">' + "".join(rows) + "</div>"
    return (
        '<div class="card lb-card">'
        f'<div class="lb-rank mono">#{rank}</div>'
        '<div class="sc-head">'
        f'<div><div class="lb-name">{e["name"]}</div><div class="lb-vendor mono">{meta}</div></div>'
        f'{badge}</div>'
        f'{radar_svg(subs)}'
        '<div class="sc-foot">'
        f'<div class="sc-composite">{comp:.0f}<small> / 100</small></div>'
        '<div style="text-align:right">'
        f'<div class="mono" style="font-size:11px;color:var(--muted)">{_conf_line(e)}</div>'
        f'{self_tag}</div></div>'
        f'{breakdown}</div>'
    )


PAGE_CSS = """
<style>
  .lb-wrap{max-width:1120px;margin:0 auto;padding:0 28px;}
  .lb-hero{padding:64px 0 34px;border-top:none;}
  .lb-grid{display:grid;grid-template-columns:1fr;gap:20px;padding-bottom:40px;}
  @media(min-width:720px){.lb-grid{grid-template-columns:1fr 1fr;}}
  @media(min-width:1060px){.lb-grid{grid-template-columns:1fr 1fr 1fr;}}
  .lb-card{padding:22px;position:relative;}
  .lb-rank{position:absolute;top:16px;right:18px;font-size:12px;color:var(--faint);letter-spacing:0.06em;}
  .lb-name{font-family:var(--serif);font-size:1.35rem;letter-spacing:-0.01em;}
  .lb-vendor{font-size:11px;color:var(--muted);letter-spacing:0.03em;margin-top:3px;}
  .lb-card .sc-head{margin-bottom:4px;padding-right:34px;}
  .sc-badge.tier-standard{border-color:var(--tier-standard);color:var(--tier-standard);}
  .sc-badge.tier-premium{border-color:var(--tier-premium);color:var(--tier-premium);}
  .sc-badge.tier-elite{border-color:var(--tier-elite);color:var(--tier-elite);}
  .sc-badge.tier-none{border-color:var(--hair-strong);color:var(--muted);}
  .lb-note{font-family:var(--mono);font-size:12px;color:var(--muted);max-width:66ch;margin:10px 0 0;line-height:1.6;}
  .lb-empty{padding:60px 0;color:var(--muted);font-family:var(--mono);}
  .lb-dims{display:grid;grid-template-columns:1fr;gap:1px 0;margin-top:16px;padding-top:14px;border-top:1px solid var(--hair);}
  .lb-dim{display:grid;grid-template-columns:112px 1fr 30px;align-items:center;gap:10px;font-family:var(--mono);font-size:11px;padding:3.5px 0;}
  .lb-dl{color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  .lb-db{height:4px;background:var(--hair-strong);border-radius:2px;overflow:hidden;}
  .lb-db i{display:block;height:100%;background:var(--accent);border-radius:2px;}
  .lb-dv{color:var(--ink-2);font-variant-numeric:tabular-nums;text-align:right;}
  .lb-dim.weak .lb-dl,.lb-dim.weak .lb-dv{color:var(--warn);}
  .lb-dim.weak .lb-db i{background:var(--warn);}
</style>
"""


def render(lander_html: str, entries: list[dict]) -> str:
    style = re.search(r"<style>.*?</style>", lander_html, re.DOTALL).group(0)
    cards = "".join(card(i + 1, e) for i, e in enumerate(entries)) or \
        '<div class="lb-empty">No agents graded yet.</div>'
    head = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex">'
        '<title>Leaderboard — Proving Ground</title>'
        '<meta name="description" content="Live leaderboard of AI agents graded across twelve dimensions by Proving Ground.">'
        '<link rel="icon" href="/favicon.ico" sizes="any">'
        '<link rel="apple-touch-icon" href="/favicons/apple-touch-icon.png">'
        f'{style}{PAGE_CSS}</head><body>'
    )
    bar = (
        '<header class="bar"><div class="wrap bar-in">'
        '<a class="brand" href="/" style="text-decoration:none">'
        '<svg class="mark" viewBox="0 0 24 24" aria-hidden="true">'
        '<circle cx="12" cy="12" r="10" fill="none" stroke="var(--accent)" stroke-width="1.4"/>'
        '<circle cx="12" cy="12" r="5.6" fill="none" stroke="var(--hair-strong)" stroke-width="1.2"/>'
        '<circle cx="12" cy="12" r="1.7" fill="var(--accent)"/>'
        '<path d="M12 2v3M12 19v3M2 12h3M19 12h3" stroke="var(--accent)" stroke-width="1.2"/></svg>'
        '<span><b>PROVING&nbsp;GROUND</b></span></a>'
        '<nav><a class="navlink" href="/#method">Method</a>'
        '<a class="navlink" href="/#dimensions">Dimensions</a>'
        '<a class="btn" href="/#certify">Certify your agent</a></nav>'
        '</div></header>'
    )
    hero = (
        '<main><section class="hero lb-hero"><div class="lb-wrap">'
        '<span class="eyebrow">The leaderboard</span>'
        '<h1 style="font-size:clamp(2rem,4vw,3rem);margin:0 0 18px;">How agents actually score.</h1>'
        '<p class="lead">Every agent is graded black-box across the same twelve dimensions and ranked by composite. '
        'We grade our own agents on this board too, with their weaknesses shown, because a benchmark that hides its '
        'operator&rsquo;s results is worth nothing.</p>'
        f'<p class="lb-note">Ranked by composite score. &ldquo;Self-operated&rdquo; marks an agent operated by Proving '
        'Ground&rsquo;s operator. Figures are illustrative during the seeding phase and will be recomputed on the '
        'held-out suite at public launch.</p>'
        '</div></section>'
        f'<section style="border-top:none;padding-top:8px;"><div class="lb-wrap"><div class="lb-grid">{cards}</div></div></section>'
        '</main>'
    )
    return head + bar + hero + "</body></html>"


def main() -> int:
    ap = argparse.ArgumentParser(description="Render the leaderboard page.")
    ap.add_argument("--lander", required=True, help="path to the lander HTML (for the shared style block)")
    ap.add_argument("--out", required=True, help="output HTML path")
    a = ap.parse_args()
    entries = load()
    html = render(Path(a.lander).read_text(), entries)
    Path(a.out).write_text(html)
    print(f"rendered {len(entries)} entries -> {a.out} ({len(html)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

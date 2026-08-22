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


# Distinct stroke per agent for the comparison charts. The first is the house
# accent (our own agent); the rest are drawn from a fixed, colorblind-safe set so
# a given rank always gets the same color across all three charts.
OVERLAY_COLORS = ["var(--accent)", "#E8A33D", "#4FA3E3", "#C471C4", "#57B87A", "#E0685A"]


def _color(i: int) -> str:
    return OVERLAY_COLORS[i % len(OVERLAY_COLORS)]


def _legend(entries: list[dict]) -> str:
    items = "".join(
        f'<span class="cmp-key"><i style="background:{_color(i)}"></i>{e["name"]}'
        f'<b>{e["composite"]:.0f}</b></span>'
        for i, e in enumerate(entries)
    )
    return f'<div class="cmp-legend">{items}</div>'


def overlay_radar_svg(entries: list[dict]) -> str:
    """All agents' twelve subscores on one radar, one outline each. This is the
    head-to-head shape comparison: same model or not, the profiles differ."""
    out = ['<svg class="cmp-figure" viewBox="0 0 460 380" role="img" aria-label="Dimension comparison radar">']
    for f in (0.25, 0.5, 0.75, 1.0):
        pts = " ".join(f"{_pt(i, R*f)[0]:.1f},{_pt(i, R*f)[1]:.1f}" for i in range(N))
        out.append(f'<polygon points="{pts}" class="radar-ring"/>')
    for i in range(N):
        ox, oy = _pt(i, R)
        out.append(f'<line x1="{CX}" y1="{CY}" x2="{ox:.1f}" y2="{oy:.1f}" class="radar-spoke"/>')
        lx, ly = _pt(i, R + 20)
        out.append(f'<text x="{lx:.1f}" y="{ly+3:.1f}" class="axis-label" text-anchor="middle">{DIMS[i][0]}</text>')
    for idx, e in enumerate(entries):
        subs = e.get("subscores", {})
        pts = []
        for i in range(N):
            s = float(subs.get(DIMS[i][1], 0.0))
            px, py = _pt(i, R * (s / 10.0))
            pts.append(f"{px:.1f},{py:.1f}")
        c = _color(idx)
        out.append(f'<polygon points="{" ".join(pts)}" fill="{c}" fill-opacity="0.06" '
                   f'stroke="{c}" stroke-width="1.8" stroke-linejoin="round"/>')
    out.append("</svg>")
    return "".join(out)


def ranked_bars_svg(entries: list[dict]) -> str:
    """Composite ranked as horizontal bars with a 95% CI whisker, LMArena style:
    a lead inside two overlapping intervals is not a real lead."""
    row, padT, x0, barW, W = 40, 14, 168, 250, 460
    H = padT * 2 + row * len(entries)
    out = [f'<svg class="cmp-figure" viewBox="0 0 {W} {H}" role="img" aria-label="Composite ranking with confidence intervals">']
    for gx in (0, 25, 50, 75, 100):
        x = x0 + barW * gx / 100
        out.append(f'<line x1="{x:.1f}" y1="{padT}" x2="{x:.1f}" y2="{H-padT}" class="radar-ring"/>')
        out.append(f'<text x="{x:.1f}" y="{H-2:.1f}" class="axis-label" text-anchor="middle">{gx}</text>')
    for idx, e in enumerate(entries):
        y = padT + row * idx + row / 2
        comp = float(e["composite"])
        c = _color(idx)
        bx = x0 + barW * comp / 100
        out.append(f'<rect x="{x0}" y="{y-7:.1f}" width="{barW*comp/100:.1f}" height="14" rx="3" fill="{c}" fill-opacity="0.22"/>')
        out.append(f'<rect x="{bx-1.4:.1f}" y="{y-7:.1f}" width="2.8" height="14" rx="1" fill="{c}"/>')
        lo, hi = (e.get("ci95") or [None, None])
        if lo is not None and hi is not None:
            lx, hx = x0 + barW*float(lo)/100, x0 + barW*float(hi)/100
            out.append(f'<line x1="{lx:.1f}" y1="{y:.1f}" x2="{hx:.1f}" y2="{y:.1f}" stroke="{c}" stroke-width="1.4"/>')
            for wx in (lx, hx):
                out.append(f'<line x1="{wx:.1f}" y1="{y-4:.1f}" x2="{wx:.1f}" y2="{y+4:.1f}" stroke="{c}" stroke-width="1.4"/>')
        out.append(f'<text x="8" y="{y+4:.1f}" class="cmp-rowlabel">{e["name"]}</text>')
        out.append(f'<text x="{x0+barW+8:.1f}" y="{y+4:.1f}" class="cmp-rowval">{comp:.1f}</text>')
    out.append("</svg>")
    return "".join(out)


def scatter_svg(entries: list[dict]) -> str:
    """Quality (composite) vs median latency, Artificial-Analysis style. Renders
    only when every entry carries a latency figure; cost and latency are reported
    alongside quality, never folded into it."""
    # Only agents measured under the same conditions (local host, same probes)
    # carry latency_ms; a network-served agent is omitted so the axis stays a fair
    # like-for-like comparison rather than a measure of who is closer to the box.
    color_of = {id(e): i for i, e in enumerate(entries)}  # keep each agent's rank color
    # Only the reference cohort is measured under identical conditions (same model,
    # same prompt, same local host), so the latency axis is a fair like-for-like.
    # A real deployed agent graded over its own network path is deliberately left
    # off: its latency reflects its production path, not a comparable measurement.
    pts = [(e, e.get("latency_ms")) for e in entries
           if e.get("latency_ms") is not None and e.get("reference")]
    if len(pts) < 2:
        return ""
    W, H, padL, padB, padT, padR = 460, 300, 52, 40, 20, 20
    xs = [float(l) for _, l in pts]
    ys = [float(e["composite"]) for e, _ in pts]
    xmin, xmax = min(xs) * 0.8, max(xs) * 1.15
    ymin, ymax = max(0, min(ys) - 8), min(100, max(ys) + 8)
    def X(v): return padL + (float(v) - xmin) / (xmax - xmin or 1) * (W - padL - padR)
    def Y(v): return H - padB - (float(v) - ymin) / (ymax - ymin or 1) * (H - padT - padB)
    out = [f'<svg class="cmp-figure" viewBox="0 0 {W} {H}" role="img" aria-label="Quality versus latency">']
    out.append(f'<line x1="{padL}" y1="{padT}" x2="{padL}" y2="{H-padB}" class="radar-spoke"/>')
    out.append(f'<line x1="{padL}" y1="{H-padB}" x2="{W-padR}" y2="{H-padB}" class="radar-spoke"/>')
    for gy in range(int(ymin // 5 * 5), int(ymax) + 1, 5):
        yy = Y(gy)
        out.append(f'<line x1="{padL}" y1="{yy:.1f}" x2="{W-padR}" y2="{yy:.1f}" class="radar-ring"/>')
        out.append(f'<text x="{padL-6}" y="{yy+3:.1f}" class="axis-label" text-anchor="end">{gy}</text>')
    out.append(f'<text x="{(padL+W-padR)/2:.0f}" y="{H-4}" class="axis-label" text-anchor="middle">median latency (ms) &rarr; slower</text>')
    out.append(f'<text transform="translate(13,{(padT+H-padB)/2:.0f}) rotate(-90)" class="axis-label" text-anchor="middle">composite &rarr; better</text>')
    for e, lat in pts:
        cx, cy = X(lat), Y(e["composite"])
        c = _color(color_of[id(e)])
        out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5.5" fill="{c}" fill-opacity="0.85"/>')
        out.append(f'<text x="{cx:.1f}" y="{cy-10:.1f}" class="cmp-pt" text-anchor="middle">{e["name"]}</text>')
    out.append("</svg>")
    return "".join(out)


def compare_section(entries: list[dict]) -> str:
    """The comparison band shown above the cards once two or more agents exist."""
    if len(entries) < 2:
        return ""
    scatter = scatter_svg(entries)
    scatter_panel = (
        f'<div class="cmp-panel"><div class="cmp-title">Quality vs latency &middot; reference cohort</div>{scatter}'
        '<div class="cmp-cap">Same model, same prompt, same local host, so latency isolates the platform, '
        'not the network. Agents graded over their own production path (like a live, network-served agent doing '
        'retrieval per message) are not plotted here, so the axis stays a fair like-for-like. Latency is measured '
        'and shown, never folded into the composite.</div></div>'
    ) if scatter else ""
    return (
        '<section class="cmp-sec"><div class="lb-wrap">'
        '<h2 class="cmp-h">Head to head</h2>'
        f'{_legend(entries)}'
        '<div class="cmp-grid">'
        f'<div class="cmp-panel"><div class="cmp-title">Twelve-dimension profile</div>{overlay_radar_svg(entries)}'
        '<div class="cmp-cap">Each outline is one agent across all twelve dimensions.</div></div>'
        f'<div class="cmp-panel"><div class="cmp-title">Composite &amp; 95% CI</div>{ranked_bars_svg(entries)}'
        '<div class="cmp-cap">Whiskers are the 95% confidence interval over runs. Overlapping intervals are a statistical tie.</div></div>'
        f'{scatter_panel}'
        '</div></div></section>'
    )


def _conf_line(e: dict) -> str:
    runs = e.get("runs", 1)
    lo, hi = (e.get("ci95") or [None, None])
    if runs and runs > 1 and lo is not None and hi is not None:
        return f"{runs}-run avg &middot; CI {lo:.0f}&ndash;{hi:.0f}"
    return "single run"


def _tools_line(e: dict) -> str:
    """Show what the agent can actually DO. The twelve scored dimensions grade
    conversation; this makes an agent's executing tools (or their absence)
    visible, so a tool-less demo is never mistaken for a capable deployed agent."""
    tools = e.get("tools") or []
    verified = set(e.get("tools_verified") or [])
    if tools:
        chips = "".join(
            f'<span class="tool-chip{" verified" if t in verified else ""}">{t}'
            f'{" &check;" if t in verified else ""}</span>'
            for t in tools
        )
        lbl = (f'<b>{len(verified)}</b> of {len(tools)} verified'
               if verified else f'<b>{len(tools)}</b> declared')
        return f'<div class="tools-row"><span class="tools-lbl">Executing tools {lbl}</span>{chips}</div>'
    return ('<div class="tools-row tools-none"><span class="tools-lbl">Executing tools '
            '<b>0</b></span><span class="tool-chip ghost">conversation only</span></div>')


def card(rank: int, e: dict, report_slug: str | None = None) -> str:
    tier = (e.get("tier") or "none").lower()
    badge = (f'<span class="sc-badge tier-{tier}">{e["tier"]}</span>'
             if tier in ("standard", "premium", "elite")
             else '<span class="sc-badge tier-none">Unrated</span>')
    self_tag = (
        '<div class="sc-note">Reference build &middot; operator-built, not the vendor&rsquo;s product</div>'
        if e.get("reference")
        else '<div class="sc-note">Self-operated</div>' if e.get("self_operated") else ""
    )
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
        f'{_tools_line(e)}'
        f'{radar_svg(subs)}'
        '<div class="sc-foot">'
        f'<div class="sc-composite">{comp:.0f}<small> / 100</small></div>'
        '<div style="text-align:right">'
        f'<div class="mono" style="font-size:11px;color:var(--muted)">{_conf_line(e)}</div>'
        f'{self_tag}</div></div>'
        f'{breakdown}'
        + (f'<a class="sc-link" href="/scorecards/{report_slug}">Open the full scorecard &rarr;</a>'
           if report_slug else '')
        + '</div>'
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
  /* comparison band */
  .cmp-sec{padding:8px 0 30px;}
  .cmp-h{font-family:var(--serif);font-size:1.5rem;letter-spacing:-0.01em;margin:0 0 14px;}
  .cmp-legend{display:flex;flex-wrap:wrap;gap:8px 20px;margin-bottom:20px;}
  .cmp-key{display:inline-flex;align-items:center;gap:8px;font-family:var(--mono);font-size:12px;color:var(--ink-2);}
  .cmp-key i{width:14px;height:3px;border-radius:2px;display:inline-block;}
  .cmp-key b{color:var(--muted);font-weight:400;margin-left:2px;}
  .cmp-grid{display:grid;grid-template-columns:1fr;gap:18px;}
  @media(min-width:900px){.cmp-grid{grid-template-columns:1fr 1fr;}}
  .cmp-panel{background:var(--panel);border:1px solid var(--hair);border-radius:12px;padding:18px 20px;}
  .cmp-title{font-family:var(--mono);font-size:11px;letter-spacing:0.08em;text-transform:uppercase;color:var(--muted);margin-bottom:10px;}
  .cmp-figure{width:100%;height:auto;display:block;}
  .cmp-cap{font-family:var(--mono);font-size:10.5px;color:var(--faint);line-height:1.6;margin-top:10px;}
  .cmp-rowlabel{font-family:var(--mono);font-size:11px;fill:var(--ink-2);}
  .cmp-rowval{font-family:var(--mono);font-size:11px;fill:var(--muted);font-variant-numeric:tabular-nums;}
  .cmp-pt{font-family:var(--mono);font-size:10px;fill:var(--ink-2);}
  /* executing-tools row: makes capability (or its absence) visible */
  .tools-row{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin:2px 0 14px;}
  .tools-lbl{font-family:var(--mono);font-size:10px;letter-spacing:0.06em;text-transform:uppercase;color:var(--muted);margin-right:2px;}
  .tools-lbl b{color:var(--accent);font-weight:600;}
  .tools-none .tools-lbl b{color:var(--muted);}
  .tool-chip{font-family:var(--mono);font-size:10.5px;color:var(--ink-2);background:var(--accent-ghost);border:1px solid var(--hair-strong);border-radius:10px;padding:1.5px 8px;white-space:nowrap;}
  .tool-chip.ghost{color:var(--faint);background:transparent;font-style:italic;}
  .tool-chip.verified{color:var(--accent);border-color:var(--accent);font-weight:600;}
  /* Route into the per-agent scorecard. A ranked list you cannot click into is a
     dead end: the composite is the claim, the scorecard is the evidence for it. */
  .sc-link{display:inline-block;margin-top:14px;font-family:var(--mono);font-size:11.5px;
    letter-spacing:0.04em;color:var(--accent);text-decoration:none;border-bottom:1px solid var(--hair-strong);padding-bottom:2px;}
  .sc-link:hover{border-color:var(--accent);}
</style>
"""



def site_bar() -> str:
    """The site header, defined once and identical on every generated page.

    Copied verbatim from the homepage, including the logo mark and the full nav, so
    a scorecard looks like part of the site rather than a page that happens to share
    its colours. It takes no parameters on purpose: the moment the nav becomes a
    per-page argument, pages start disagreeing about what the site's navigation is.

    The homepage can write `#dimensions` because it IS the page those anchors live
    on. Every other page needs `/#dimensions`, or the link silently does nothing.
    """
    return (
        '<header class="bar"><div class="wrap bar-in">'
        '<a class="brand" href="/" style="text-decoration:none;color:inherit;">'
        '<svg class="mark" viewBox="0 0 24 24" aria-hidden="true">'
        '<circle cx="12" cy="12" r="10" fill="none" stroke="var(--accent)" stroke-width="1.4"/>'
        '<circle cx="12" cy="12" r="5.6" fill="none" stroke="var(--hair-strong)" stroke-width="1.2"/>'
        '<circle cx="12" cy="12" r="1.7" fill="var(--accent)"/>'
        '<path d="M12 2v3M12 19v3M2 12h3M19 12h3" stroke="var(--accent)" stroke-width="1.2"/></svg>'
        '<span><b>PROVING&nbsp;GROUND</b></span></a>'
        '<nav>'
        '<a class="navlink" href="/">Home</a>'
        '<a class="navlink" href="/#dimensions">Dimensions</a>'
        '<a class="navlink" href="/methodology">Methodology</a>'
        '<a class="navlink" href="/leaderboard/">Leaderboard</a>'
        '<a class="btn" href="/#certify">Certify your agent</a>'
        '</nav></div></header>'
    )

def render(lander_html: str, entries: list[dict], slugs: dict[str, str] | None = None) -> str:
    style = re.search(r"<style>.*?</style>", lander_html, re.DOTALL).group(0)
    cards = "".join(card(i + 1, e, (slugs or {}).get(e["id"])) for i, e in enumerate(entries)) or \
        '<div class="lb-empty">No agents graded yet.</div>'
    import json as _json
    base = "https://provingground.aivonic.ai"
    desc = ("How AI agents actually score. Every agent graded black-box across the same twelve "
            "dimensions and ranked by composite score, with weaknesses shown.")
    ranked = sorted(entries, key=lambda e: -e.get("composite", 0))
    items = [
        {"@type": "ListItem", "position": i + 1, "name": e["name"],
         "description": f'{e.get("composite", 0):.1f}/100, {e.get("tier", "")} tier.'}
        for i, e in enumerate(ranked)
    ]
    ld = _json.dumps([
        {"@context": "https://schema.org", "@type": "Dataset",
         "name": "Proving Ground AI Agent Benchmark",
         "description": "An independent black-box benchmark that grades deployed AI agents across twelve dimensions.",
         "url": f"{base}/leaderboard/",
         "creator": {"@type": "Organization", "name": "Aivonic Labs AB", "url": "https://aivonic.ai/"},
         "license": "https://www.apache.org/licenses/LICENSE-2.0", "isAccessibleForFree": True},
        {"@context": "https://schema.org", "@type": "ItemList",
         "name": "Proving Ground Agent Leaderboard",
         "itemListOrder": "https://schema.org/ItemListOrderDescending",
         "numberOfItems": len(items), "itemListElement": items},
    ])
    head = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="index,follow,max-image-preview:large">'
        '<title>Leaderboard — Proving Ground</title>'
        f'<meta name="description" content="{desc}">'
        f'<link rel="canonical" href="{base}/leaderboard/">'
        '<meta property="og:type" content="website">'
        '<meta property="og:site_name" content="Proving Ground">'
        '<meta property="og:title" content="Leaderboard — Proving Ground">'
        f'<meta property="og:description" content="{desc}">'
        f'<meta property="og:url" content="{base}/leaderboard/">'
        f'<meta property="og:image" content="{base}/og.png">'
        '<meta name="twitter:card" content="summary_large_image">'
        f'<meta name="twitter:description" content="{desc}">'
        f'<meta name="twitter:image" content="{base}/og.png">'
        f'<script type="application/ld+json">{ld}</script>'
        '<link rel="icon" href="/favicon.ico" sizes="any">'
        '<link rel="apple-touch-icon" href="/favicons/apple-touch-icon.png">'
        f'{style}{PAGE_CSS}</head><body>'
    )
    bar = site_bar()
    hero = (
        '<main><section class="hero lb-hero"><div class="lb-wrap">'
        '<span class="eyebrow">The leaderboard</span>'
        '<h1 style="font-size:clamp(2rem,4vw,3rem);margin:0 0 18px;">How agents actually score.</h1>'
        '<p class="lead">Every agent is graded black-box across the same twelve dimensions and ranked by composite. '
        'We grade our own agents on this board too, with their weaknesses shown, because a benchmark that hides its '
        'operator&rsquo;s results is worth nothing.</p>'
        f'<p class="lb-note">Ranked by composite score, computed on the held-out private suite by the four-lab judge '
        'panel. &ldquo;Self-operated&rdquo; marks an agent we run ourselves; &ldquo;reference build&rdquo; marks an '
        'operator-built agent on a third-party platform, shown to demonstrate the method. Our own agent is ranked on '
        'the same grade as every other, with its weaknesses shown, never excluded.</p>'
        '</div></section>'
        f'{compare_section(entries)}'
        f'<section style="border-top:none;padding-top:8px;"><div class="lb-wrap"><div class="lb-grid">{cards}</div></div></section>'
        '</main>'
    )
    return head + bar + hero + "</body></html>"


def main() -> int:
    ap = argparse.ArgumentParser(description="Render the leaderboard page.")
    ap.add_argument("--lander", required=True, help="path to the lander HTML (for the shared style block)")
    ap.add_argument("--out", required=True, help="output HTML path")
    ap.add_argument("--report-dir", help="directory of generated scorecards; when given, each row "
                                         "links to its agent's card")
    a = ap.parse_args()
    entries = load()
    slugs = {}
    if a.report_dir:
        for e in entries:
            found = sorted(Path(a.report_dir).glob(f'{e["id"]}-*.html'))
            if found:
                slugs[e["id"]] = found[-1].stem
    html = render(Path(a.lander).read_text(), entries, slugs)
    Path(a.out).write_text(html)
    print(f"rendered {len(entries)} entries -> {a.out} ({len(html)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

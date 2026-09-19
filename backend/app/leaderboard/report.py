"""Render a private, per-agent scorecard from a grade run.

The leaderboard answers "who is better". This answers "where exactly did MY agent
slip, and what did it say". It is the artifact the outreach templates promise: a
vendor gets a link, sees their own weakest dimensions with their own transcripts,
and can argue with it.

Deliberately UNLISTED, not part of the public board:

  * ``noindex, nofollow`` and never added to the sitemap. The URL carries a token
    derived from the grade itself, so it is shareable but not enumerable.
  * The private probe PROMPTS are never rendered. They are not even present in a
    run artifact, so this cannot leak the held-out suite by accident. What is
    shown is the agent's own reply and the judge's rationale, which belong to the
    vendor anyway.

Nothing is published by generating one. Sending the link is a separate, human act.

    python -m app.leaderboard.report --run ../backend/data/runs/spark_x.json \
        --id spark --lander ../frontend/standalone.html --out-dir ../frontend/scorecards
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import statistics
from pathlib import Path

from app.leaderboard.render import DIMS, PAGE_CSS, PREMIUM_FLOOR, radar_svg, site_bar
from app.judges.coverage import DISPLAY as PANEL_DISPLAY, panel_phrase
from app.scoring.version import METHODOLOGY_VERSION, describe_identity
from app.leaderboard.store import load, load_published

BACKEND = Path(__file__).resolve().parents[2]

DIM_KEYS = {k: full for _short, k, full in DIMS}
MAX_PROBES_PER_DIM = 6   # worst-first; the rest are counted, never silently dropped
RESPONSE_CLIP = 900


from app.leaderboard.certs import code_for
from app.leaderboard.render import _cap_line


def _eyebrow(entry: dict) -> str:
    """What this card IS, in three words, at the top.

    Three states, not two. "graded" and "published" are different facts, and the
    old version inferred the second from the first - so a withheld grade would
    have introduced itself as "listed on the leaderboard" on the very card sent
    to the vendor whose result we are holding back. The worst possible reader for
    that sentence.
    """
    if not entry.get("published", True):
        return "Withheld scorecard &middot; not on the leaderboard, no public certificate"
    if entry.get("composite") is not None and entry.get("graded_at"):
        return "Published scorecard &middot; listed on the leaderboard"
    return "Private scorecard &middot; not published"


def slug_for(entry: dict, run_path: str) -> str:
    """`<agent-id>-<token>`. The name is there so a pasted link is self-evidently
    about THIS agent; the token is there so the URL cannot be guessed.

    Both halves earn their place. A bare `/scorecards/spark` would be enumerable, and a
    report carries a vendor's own transcripts and the fact that we graded them at
    all, neither of which is ours to expose by letting anyone try names until one
    answers 200. A bare token is unguessable but tells the recipient nothing.

    Derived from the grade, with no clock and no randomness, so regenerating the
    same grade reproduces the same URL and a link already sent never rots."""
    seed = f'{entry["id"]}|{entry.get("graded_at", "")}|{entry["composite"]}|{Path(run_path).name}'
    token = hashlib.sha256(seed.encode()).hexdigest()[:12]
    return f'{entry["id"]}-{token}'


def probe_rollup(runs: list[dict], dim_key: str) -> list[dict]:
    """Collapse N runs into one row per probe: mean score, and the evidence from
    the WORST run. A probe that fails once in three is the interesting one, so
    averaging away the bad run would hide exactly what the report is for."""
    by_id: dict[str, list[dict]] = {}
    for r in runs:
        for p in r.get(dim_key, []) or []:
            by_id.setdefault(p["probe_id"], []).append(p)
    rows = []
    for pid, ps in by_id.items():
        scores = [float(p.get("score", 0.0)) for p in ps]
        worst = min(ps, key=lambda p: float(p.get("score", 0.0)))
        rows.append({
            "probe_id": pid,
            "category": worst.get("category", ""),
            "mean": statistics.fmean(scores),
            "low": min(scores),
            "n": len(ps),
            "flaky": len(set(round(s, 3) for s in scores)) > 1,
            "critical": any(p.get("critical") for p in ps),
            "reason": worst.get("reason", ""),
            "response": worst.get("response", ""),
        })
    return sorted(rows, key=lambda r: (r["mean"], r["probe_id"]))


def _bar(score10: float) -> str:
    pct = max(0.0, min(100.0, score10 * 10))
    tone = "fail" if score10 < 5 else "warn" if score10 < PREMIUM_FLOOR else "pass"
    return (f'<span class="rp-bar"><span class="rp-bar-fill rp-{tone}" '
            f'style="width:{pct:.1f}%"></span></span>')


def _clip(s: str, n: int = RESPONSE_CLIP) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[:n].rstrip() + " …"


def probe_html(p: dict) -> str:
    flags = []
    if p["critical"]:
        flags.append('<span class="rp-flag rp-crit">critical</span>')
    if p["flaky"]:
        flags.append(f'<span class="rp-flag">inconsistent &middot; low {p["low"]:.2f}</span>')
    cat = html.escape(p["category"])
    resp = html.escape(_clip(p["response"])) or "<em>no reply captured</em>"
    return (
        '<div class="rp-probe">'
        f'<div class="rp-probe-top"><code>{html.escape(p["probe_id"])}</code>'
        f'<span class="rp-cat">{cat}</span>{"".join(flags)}'
        f'<span class="rp-pscore">{p["mean"]:.2f}</span></div>'
        f'<div class="rp-judge">{html.escape(_clip(p["reason"], 320))}</div>'
        f'<div class="rp-resp">{resp}</div>'
        "</div>"
    )


def dimension_html(entry: dict, runs: list[dict], key: str, full: str) -> str:
    score = float(entry.get("subscores", {}).get(key, 0.0))
    probes = probe_rollup(runs, key)
    shown = [p for p in probes if p["mean"] < 1.0][:MAX_PROBES_PER_DIM]
    clean = len(probes) - len([p for p in probes if p["mean"] < 1.0])
    weak = score < PREMIUM_FLOOR
    if shown:
        omitted = len([p for p in probes if p["mean"] < 1.0]) - len(shown)
        tail = (f'<p class="rp-omitted">{omitted} further probe(s) below full marks not shown.</p>'
                if omitted > 0 else "")
        body = "".join(probe_html(p) for p in shown) + tail
    else:
        body = '<p class="rp-omitted">Every probe in this dimension scored full marks.</p>'
    return (
        f'<details class="rp-dim{" rp-dim-weak" if weak else ""}">'
        f'<summary><span class="rp-dim-name">{html.escape(full)}</span>'
        f'{_bar(score)}<span class="rp-dim-score">{score:.2f}</span>'
        f'<span class="rp-dim-meta">{len(probes)} probes &middot; {clean} clean</span></summary>'
        f'<div class="rp-dim-body">{body}</div></details>'
    )


REPORT_CSS = """<style>
.rp-panelnote{border:1px solid var(--hair,#e4e4e0);border-left:3px solid var(--accent,#0f9e84);
  border-radius:10px;padding:14px 18px;margin:18px 0;font-size:.92rem;background:var(--panel-2,#f6f6f3)}
.rp-panelnote ul{margin:8px 0;padding-left:20px}
.rp-panelnote li{margin:3px 0}
.rp-panelnote p{margin:8px 0 0;opacity:.85}
.rp-wrap{max-width:900px;margin:0 auto;padding:0 22px}
.rp-head{padding:44px 0 8px}
.rp-title{font-size:clamp(1.8rem,3.6vw,2.6rem);margin:6px 0 4px;letter-spacing:-.01em}
.rp-sub{color:var(--muted);margin:0 0 22px}
.rp-kpis{display:flex;flex-wrap:wrap;gap:10px;margin:0 0 8px}
.rp-kpi{flex:1;min-width:132px;background:var(--panel);border:1px solid var(--hair);border-radius:12px;padding:14px 16px}
.rp-kpi b{display:block;font-size:1.7rem;line-height:1;font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.rp-kpi span{font-family:var(--mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
.rp-two{display:grid;grid-template-columns:minmax(0,460px) minmax(0,1fr);gap:26px;align-items:start;margin:26px 0 8px}
@media(max-width:820px){.rp-two{grid-template-columns:1fr}}
.rp-note{background:var(--accent-ghost);border:1px solid var(--hair);border-left:2px solid var(--accent);
  border-radius:10px;padding:14px 16px;margin:22px 0;font-size:13.5px;color:var(--ink)}
.rp-weakbox{background:var(--panel);border:1px solid var(--hair);border-radius:12px;padding:16px 18px}
.rp-weakbox h3{margin:0 0 10px;font-size:1rem}
.rp-weak-row{display:flex;align-items:center;gap:10px;padding:7px 0;border-bottom:1px solid var(--hair)}
.rp-weak-row:last-child{border-bottom:none}
.rp-weak-name{flex:1;min-width:0}
.rp-bar{flex:0 0 120px;height:6px;border-radius:99px;background:var(--hair);overflow:hidden;display:inline-block}
.rp-bar-fill{display:block;height:100%;border-radius:99px}
.rp-pass{background:var(--pass)} .rp-warn{background:var(--warn)} .rp-fail{background:var(--fail)}
.rp-dim{border:1px solid var(--hair);border-radius:12px;background:var(--panel);margin:9px 0;overflow:hidden}
.rp-dim-weak{border-color:var(--warn)}
.rp-dim summary{display:flex;align-items:center;gap:12px;padding:13px 16px;cursor:pointer;list-style:none}
.rp-dim summary::-webkit-details-marker{display:none}
.rp-dim-name{flex:1;font-weight:600;min-width:0}
.rp-dim-score{font-family:var(--mono);font-variant-numeric:tabular-nums;font-weight:600;min-width:46px;text-align:right}
.rp-dim-meta{font-family:var(--mono);font-size:11px;color:var(--muted);min-width:118px;text-align:right}
@media(max-width:620px){.rp-dim-meta{display:none}}
.rp-dim-body{padding:2px 16px 14px;border-top:1px solid var(--hair)}
.rp-probe{border-top:1px solid var(--hair);padding:12px 0}
.rp-probe:first-child{border-top:none}
.rp-probe-top{display:flex;align-items:center;gap:9px;flex-wrap:wrap;margin-bottom:6px}
.rp-probe-top code{font-family:var(--mono);font-size:11.5px;color:var(--ink)}
.rp-cat,.rp-flag{font-family:var(--mono);font-size:10px;letter-spacing:.07em;text-transform:uppercase;
  border:1px solid var(--hair-strong);border-radius:99px;padding:2px 8px;color:var(--muted)}
.rp-crit{color:var(--fail);border-color:var(--fail)}
.rp-pscore{margin-left:auto;font-family:var(--mono);font-variant-numeric:tabular-nums;font-weight:600}
.rp-judge{font-family:var(--mono);font-size:11.5px;color:var(--muted);margin-bottom:7px;word-break:break-word}
.rp-resp{border-left:2px solid var(--hair-strong);padding:2px 0 2px 12px;white-space:pre-wrap;
  font-size:13.5px;color:var(--ink);overflow-wrap:anywhere}
.rp-omitted{color:var(--muted);font-size:12.5px;margin:10px 0 2px}
.rp-cert{margin:40px 0 0;background:var(--panel);border:1px solid var(--hair);border-radius:10px;padding:24px 28px}.rp-cert h3{margin:0 0 10px;font-size:1rem}.rp-cert p{color:var(--muted);font-size:14px;margin:0 0 12px}.rp-cert pre{background:var(--ground);border:1px solid var(--hair);border-radius:7px;padding:14px 16px;overflow-x:auto;margin:0;font-size:12.5px;line-height:1.55}.rp-cert code{font-family:var(--mono)}.rp-foot{color:var(--muted);font-size:12.5px;border-top:1px solid var(--hair);margin:44px 0 0;padding:18px 0 70px}
</style>"""


def render_report(lander_html: str, entry: dict, data: dict, slug: str) -> str:
    import re
    style = re.search(r"<style>.*?</style>", lander_html, re.DOTALL).group(0)
    runs = data.get("runs") or []
    grade = data.get("grade") or {}
    subs = entry.get("subscores", {})
    name = html.escape(entry["name"])
    vendor = html.escape(entry.get("vendor") or "")
    conf = grade.get("confidence") or {}
    lo, hi = (entry.get("ci95") or [None, None])
    ci = f"{lo:.2f}&ndash;{hi:.2f}" if lo is not None else "n/a"

    ranked = sorted(((k, float(subs.get(k, 0.0))) for k in DIM_KEYS), key=lambda x: x[1])
    weak_rows = "".join(
        f'<div class="rp-weak-row"><span class="rp-weak-name">{html.escape(DIM_KEYS[k])}</span>'
        f'{_bar(v)}<span class="rp-dim-score">{v:.2f}</span></div>'
        for k, v in ranked[:3]
    )
    dims_html = "".join(dimension_html(entry, runs, k, DIM_KEYS[k]) for k, _v in ranked)
    crit = grade.get("critical_failures", 0)
    # Name the exact build. Without it the reader cannot reproduce the grade, and a
    # cohort comparison across platforms is only meaningful against pinned versions.
    plat = (" &middot; " + html.escape(entry["platform_version"])
            if entry.get("platform_version") else "")
    # Stated from what the run artifact shows, never assumed. See
    # app/judges/coverage.py for why this stopped being a constant.
    # The PANEL is who took part. Coverage is disclosed per dimension below,
    # because it genuinely varies and a single figure cannot say so.
    panel = html.escape(panel_phrase(entry.get("judge_panel") or entry.get("judge_labs") or []))
    # The configuration this grade was computed under, stated in the same words the
    # certificate uses because it is the same claim about comparability. A card is
    # what a vendor forwards to a buyer, so it cannot be quieter about this than the
    # certificate that buyer might check instead.
    scoring_identity = describe_identity(entry.get("composite_id"), html=True)
    shortfall = entry.get("judge_shortfall") or {}
    panel_note = ""
    if shortfall:
        rows = "".join(
            f"<li><b>{html.escape(DIM_KEYS.get(d, d))}</b> &mdash; "
            + ", ".join(f"{html.escape(PANEL_DISPLAY.get(k, k))} judged {v:.0%}"
                        for k, v in sorted(labs.items()))
            + "</li>"
            for d, labs in sorted(shortfall.items())
        )
        panel_note = (
            '<div class="rp-panelnote"><b>Not every lab judged every dimension.</b>'
            f'<ul>{rows}</ul>'
            '<p>This is not an outage. A frontier model&rsquo;s own safety guardrail refuses to '
            'engage with a probe built from a harmful request, so it returns nothing rather than '
            'scoring the agent&rsquo;s refusal. We tested whether it could be worked around: '
            'delimiting the adversarial text and instructing the judge to score rather than answer '
            'it changed nothing, and withholding the attack made that judge score SAFE replies while '
            'still refusing UNSAFE ones &mdash; a judge that confirms passes and looks away from '
            'failures, which would bias this score upward exactly where the agent did worst. '
            'So the lab abstains and we say so, rather than quietly reporting a fuller panel than '
            'the one that graded these probes.</p></div>'
        )
    # A reference build is an agent WE built on someone's platform. Saying so on the
    # page is the difference between "we graded your product" (false, and the kind of
    # claim that ends a conversation) and "we built an agent on your platform and
    # graded that" (true, and the whole point of the comparison).
    provenance = (
        f'<b>{name} is a reference build, not {vendor.replace("Built on ", "") or "the vendor"}\u2019s '
        'own product.</b> We built it ourselves to the same specification on each platform, same model '
        'and same prompt, so the only variable is the platform. It is not a grade of anything you ship.'
        if entry.get("reference") else
        (f'{name} is graded and published on the leaderboard.'
         if (entry.get('composite') is not None and entry.get('graded_at'))
         else f'Nothing about {name} is published unless you say so.')
    )

    head = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex,nofollow">'
        f'<title>{name} — Proving Ground scorecard</title>'
        '<link rel="icon" href="/favicon.ico" sizes="any">'
        f'{style}{PAGE_CSS}{REPORT_CSS}</head><body>'
    )
    bar = site_bar()

    # The certificate is how this grade gets used by anyone other than the graded
    # party: the scorecard is the private diagnostic, the certificate is the public
    # claim a buyer can check. The code is deliberately NOT the scorecard slug --
    # the slug seeds on the grade and so changes on every re-grade, which would break
    # a mark embedded on a vendor's site the day their score improved. See certs.py.
    cert_block = ""
    if not entry.get("published", True):
        # No public certificate exists for a withheld grade, so this card must not
        # offer one. Saying so plainly is also the honest thing for the recipient
        # to read: they are holding the full result before anyone else can.
        cert_block = """
<div class="rp-cert">
  <h3>This grade is not published</h3>
  <p>It is not on the leaderboard and there is no public certificate for it, so nobody can look
  this result up. You are reading it before anyone outside Proving Ground has seen it.</p>
  <p>The agent graded here is a <b>reference build</b>: we configured it ourselves on your platform
  with our own model and system prompt, so this measures the platform as we set it up and is not a
  result about your shipped product. That is exactly why it is not published. If you want it on the
  board, with or without a response of yours alongside it, that is your call to make and we will
  publish your reply with it.</p>
</div>"""
    elif entry.get("composite") is not None and entry.get("graded_at"):
        _code = code_for(entry["id"])
        _alias = entry["id"]      # readable, names the build, resolves identically
        cert_block = f"""
<div class="rp-cert">
  <h3>Your certificate</h3>
  <p>This grade is verifiable by anyone at
  <a href="https://theprovingground.io/verify/{_alias}">theprovingground.io/verify/{_alias}</a>,
  which answers in plain English and as JSON. The code is yours permanently &mdash; it survives
  a re-grade, so a link you publish today still resolves after your next one.</p>
  <p>The mark is served, not downloaded, on purpose: it reads current while the grade is current,
  and marks itself expired afterwards &mdash; so you are never left displaying a claim that has
  quietly stopped being true. Embed it with:</p>
  <pre><code>&lt;a href="https://theprovingground.io/verify/{_alias}"&gt;
  &lt;img src="https://theprovingground.io/badge/{_alias}.svg"
       alt="Proving Ground grade for {name}"&gt;
&lt;/a&gt;</code></pre>
  <p class="rp-omitted">This also resolves at <code>/verify/{_code}</code>, which is permanent and
  never changes. Either address works; the readable one is easier to paste.</p>
</div>"""

    body = f"""<main class="rp-wrap">
<section class="rp-head">
  <span class="eyebrow">{_eyebrow(entry)}</span>
  <h1 class="rp-title">{name}</h1>
  {panel_note}
  {_cap_line(entry)}
  <p class="rp-sub">{vendor}{plat} &middot; graded {html.escape(entry.get('graded_at',''))} on the held-out private suite by the {panel}.</p>
  <div class="rp-kpis">
    <div class="rp-kpi"><b>{entry['composite']:.2f}</b><span>Composite / 100</span></div>
    <div class="rp-kpi"><b>{html.escape(entry.get('tier','') or 'Unrated')}</b><span>Tier</span></div>
    <div class="rp-kpi"><b>{crit}</b><span>Critical failures</span></div>
    <div class="rp-kpi"><b>{entry.get('runs',1)}</b><span>Runs &middot; CI {ci}</span></div>
  </div>
</section>

<div class="rp-note"><b>This page is unlisted and is not on the leaderboard.</b> It exists so you can
check the grade rather than take it on trust. {provenance}
The probe prompts are withheld because the suite is held out and rotated, so tuning to it is not
possible; what you see is your agent's own reply and the judge's reasoning for every probe that
lost a point. Method and rubrics are public at
<a href="https://github.com/AIVONIC/proving-ground">github.com/AIVONIC/proving-ground</a>.</div>

<div class="rp-two">
  <div>{radar_svg(subs)}</div>
  <div class="rp-weakbox">
    <h3>Where it slipped</h3>
    {weak_rows}
    <p class="rp-omitted">Lowest three of twelve. A dimension under {PREMIUM_FLOOR} blocks the
    Premium tier however high the composite goes, so these are the ones that move a grade.</p>
  </div>
</div>

<h2 style="margin:34px 0 4px;font-size:1.15rem">Every dimension, worst first</h2>
<p class="rp-sub">Open a row for the probes that lost points. Scores are the mean across
{entry.get('runs',1)} runs; the transcript shown is from the worst run, because a probe that fails
one time in three is the one worth reading.</p>
{dims_html}

{cert_block}

<p class="rp-foot">Judge agreement {conf.get('judge_agreement',{}).get('overall','n/a')} &middot;
cross-run variance {conf.get('variance','n/a')} &middot; scorecard {slug}.<br>
Scoring config {scoring_identity} &middot; methodology v{html.escape(str(entry.get('methodology_version') or METHODOLOGY_VERSION))}.
A score may only be compared with another carrying the same scoring config.<br>
Grades expire after 90 days because agents drift.</p>
</main>"""
    return head + bar + body + "</body></html>"



INDEX_CSS = """<style>
.ix-row{display:flex;align-items:center;gap:14px;padding:15px 18px;border:1px solid var(--hair);
  border-radius:12px;background:var(--panel);margin:9px 0;text-decoration:none;color:inherit}
.ix-row:hover{border-color:var(--accent);text-decoration:none}
.ix-rank{font-family:var(--mono);font-size:12px;color:var(--muted);min-width:22px}
.ix-main{flex:1;min-width:0}
.ix-name{font-weight:600}
.ix-meta{color:var(--muted);font-size:12.5px}
.ix-weak{font-family:var(--mono);font-size:11.5px;color:var(--muted);text-align:right;min-width:190px}
@media(max-width:700px){.ix-weak{display:none}}
.ix-score{font-family:var(--mono);font-variant-numeric:tabular-nums;font-weight:600;font-size:1.15rem;min-width:60px;text-align:right}
.ix-missing{color:var(--muted);font-size:13px;border-top:1px solid var(--hair);margin-top:20px;padding-top:14px}
</style>"""


def index_html(lander_html: str, rows: list[tuple[dict, str]], missing: list[dict]) -> str:
    """The published index. Listing is DERIVED from the leaderboard, never from a flag:
    a card is listed here if and only if its agent has a promoted entry, because being
    on the board is what makes a grade public. A card generated for a vendor who has not
    agreed to be listed has no entry, so it cannot appear here even by mistake. That is
    deliberately not a rule someone has to remember."""
    import re
    style = re.search(r"<style>.*?</style>", lander_html, re.DOTALL).group(0)
    items = []
    for e, slug in rows:
        subs = e.get("subscores", {})
        weakest = sorted(((DIM_KEYS[k], float(v)) for k, v in subs.items() if k in DIM_KEYS),
                         key=lambda x: x[1])[:2]
        weak = " &middot; ".join(f"{n} {v:.1f}" for n, v in weakest)
        tag = ("reference build" if e.get("reference")
               else "self-operated" if e.get("self_operated") else "")
        meta = " &middot; ".join(x for x in (e.get("vendor"), tag) if x)
        items.append(
            f'<a class="ix-row" href="/scorecards/{slug}">'
            f'<span class="ix-rank">{len(items)+1}</span>'
            f'<span class="ix-main"><span class="ix-name">{html.escape(e["name"])}</span><br>'
            f'<span class="ix-meta">{meta}</span></span>'
            f'<span class="ix-weak">weakest: {weak}</span>'
            f'<span class="ix-score">{e["composite"]:.1f}</span></a>'
        )
    miss = ""
    if missing:
        names = ", ".join(html.escape(e["name"]) for e in missing)
        miss = (f'<p class="ix-missing">On the board but with no scorecard generated yet: {names}. '
                'Run <code>app.leaderboard.report</code> for each.</p>')
    head = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex,nofollow">'
        '<title>Agent scorecards — Proving Ground</title>'
        '<link rel="icon" href="/favicon.ico" sizes="any">'
        f'{style}{PAGE_CSS}{REPORT_CSS}{INDEX_CSS}</head><body>'
    )
    bar = site_bar()
    body = f"""<main class="rp-wrap">
<section class="rp-head">
  <span class="eyebrow">Scorecards</span>
  <h1 class="rp-title">Every grade, opened up</h1>
  <p class="rp-sub">The leaderboard says how agents rank. A scorecard says why: all twelve
  dimensions worst-first, every probe that lost points, the judge's reasoning, and the agent's
  own reply. One per graded agent.</p>
</section>

<div class="rp-note"><b>What is here and what is not.</b> Every agent on the public leaderboard
has a scorecard, and it is linked below. Agents we have graded privately do NOT appear here:
their card exists at its own address and is shared with them alone, because a grade nobody has
agreed to publish is theirs to release, not ours. That is enforced by how this page is built
rather than by a setting, since a card is listed here only when its agent has a leaderboard
entry. Probe prompts are withheld from every card, published or not, because the suite is
held out and rotated.</div>

{"".join(items) if items else '<p class="rp-sub">No scorecards generated yet.</p>'}
{miss}

<p class="rp-foot">Method and rubrics are public at
<a href="https://github.com/AIVONIC/proving-ground">github.com/AIVONIC/proving-ground</a>.
Grades expire after 90 days because agents drift.</p>
</main>"""
    return head + bar + body + "</body></html>"

def main() -> int:
    ap = argparse.ArgumentParser(description="Render a private per-agent scorecard.")
    ap.add_argument("--run", help="path to the grade run artifact JSON")
    ap.add_argument("--id", help="promoted leaderboard entry id")
    ap.add_argument("--lander", required=True, help="lander HTML, for the shared style block")
    ap.add_argument("--out-dir", required=True, help="directory to write <slug>.html into")
    ap.add_argument("--index", action="store_true",
                    help="(re)build index.html listing the cards of every promoted agent")
    a = ap.parse_args()

    if a.index:
        out_dir = Path(a.out_dir)
        rows, missing = [], []
        # load_published, not load: a withheld card is still GENERATED (it is the
        # document handed to the vendor) but must not be discoverable here. The
        # docstring below says listing is derived from being on the board, and
        # `published: false` is exactly the case where that stopped being true.
        for e in load_published():
            found = sorted(out_dir.glob(f'{e["id"]}-*.html'))
            if found:
                rows.append((e, found[-1].stem))
            else:
                missing.append(e)
        out = out_dir / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(index_html(Path(a.lander).read_text(), rows, missing))
        print(f"index: {len(rows)} card(s) listed, {len(missing)} promoted agent(s) without one -> {out}")
        return 0

    if not a.run or not a.id:
        raise SystemExit("--run and --id are required unless --index is given")

    entry = next((e for e in load() if e["id"] == a.id), None)
    if entry is None:
        raise SystemExit(f"no promoted entry with id {a.id!r}; promote the run first")
    data = json.loads(Path(a.run).read_text())
    slug = slug_for(entry, a.run)

    # ⛔ A WITHHELD CARD MUST NOT BE WRITTEN WHERE A DEPLOY CAN REACH IT.
    #
    # This module already knew the card should not be DISCOVERABLE - it is excluded
    # from the index, from the leaderboard and from certs.json. It was still written
    # into frontend/scorecards/, which deploy.sh carries across the docroot swap, and
    # nginx serves /var/www/html/pg by path with no authorization. So Onyx's card sat
    # publicly retrievable for three days at a 78KB URL containing the model's
    # keycard-cloning output verbatim, while every index correctly hid it.
    #
    # "Not linked" is not "not published". The three exclusions were each correct and
    # none of them was the one that mattered, because the file was still on disk in a
    # served directory.
    #
    # The separation is now STRUCTURAL rather than a matter of passing the right
    # --out-dir: an unpublished entry is routed out of the deploy tree by the code,
    # and the path is printed so the operator knows where the vendor's copy is.
    out_dir = Path(a.out_dir)
    if not entry.get("published", True):
        out_dir = BACKEND / "data" / "withheld-scorecards"
        print(f"WITHHELD: {entry['name']} is published:false - its card is NOT written to "
              f"{a.out_dir} (deploy-reachable). Writing outside the deploy tree instead.")
    out = out_dir / f"{slug}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_report(Path(a.lander).read_text(), entry, data, slug))
    print(f"scorecard {entry['name']} -> {out} ({out.stat().st_size} bytes)")
    # ⛔ DO NOT PRINT A PUBLIC URL FOR A CARD THAT WILL NOT BE PUBLISHED. The line
    # below is the one an operator copies into an email. For a withheld card that URL
    # is false - it 404s - and printing it is how a withheld grade gets sent as though
    # it were live, or worse, how someone concludes it must be reachable and puts it
    # back in the docroot to make the link work.
    if entry.get("published", True):
        print(f"URL when deployed: https://theprovingground.io/scorecards/{slug}")
    else:
        print("NOT deployed and no public URL: this card is withheld. Send the FILE, "
              "and it 404s on the site by design.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

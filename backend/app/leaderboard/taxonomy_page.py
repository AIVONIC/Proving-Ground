"""Render the joint failure taxonomy: markdown document and public page.

Generated from the dimension CLASSES and their suite files, never from a hand-kept
copy. A taxonomy whose published description has drifted from the probe that
implements it is, by its own entry number ten, an instruction that went stale.

TWO RULES ARE ENFORCED HERE RATHER THAN REMEMBERED.

1. NO FREQUENCIES. The taxonomy publishes named patterns and the probes that
   detect them, never how often either party sees them. Frequencies observed in
   production come from client environments, do not generalise, and presenting
   them as rates would misrepresent both the data and its scope. One measurement
   is exempt -- 16.6% on Aivonic's own corpus -- and it may only appear with its
   scope attached. ``check_no_frequencies`` fails the render otherwise.

2. THE ORDER IS NOT A RANKING. The production entries are listed in the order
   they were contributed. Martin's sense of which he sees most often is an
   impression across deployments, not a measurement, and printing the entries in
   that order would turn it into one on the way to the page.

AND ONE COMMITMENT, ENFORCED THE SAME WAY. Dimension 7 says reproducibility is a
quality dimension, so Proving Ground is measured by it first: the page will not
render without a recorded measurement of this benchmark's own scoring pipeline.
Publishing the dimension while the operator's own number is outstanding is the
exemption the dimension exists to refuse.
"""

from __future__ import annotations

import argparse
import html as _h
import json
import re
from pathlib import Path

from app.dimensions.taxonomy_catalog import describe_all
from app.leaderboard.disclosure import DISCLOSURE_PARAGRAPHS, DISCLOSURE_TITLE, as_html, as_markdown
from app.reproducibility.concurrency_probe import PUBLIC_MEASUREMENTS as MEASUREMENTS
from app.scoring.version import METHODOLOGY_VERSION, composite_id

BACKEND = Path(__file__).resolve().parents[2]
BASE = "https://theprovingground.io"

ORDER_NOTE = (
    "The entries are listed in the order they were contributed, split by where each pattern was "
    "observed. That order is not a ranking and carries no claim about how often any of them "
    "occurs. Neither party publishes frequencies here: rates observed in production come from "
    "client environments and do not generalise."
)

WEIGHTING_NOTE = (
    "These dimensions are scored 0 to 10 and reported alongside the composite. None of them "
    "carries composite weight, so nothing on this page changes any score already published. "
    "Folding them into the composite is a later, deliberate step that re-grades the whole board; "
    "when it happens the composite's derived identifier changes with it, so a score from before "
    "and a score from after can never be read as the same measurement."
)

#: The single frequency that may be published, and the scope that must accompany it.
ALLOWED_FREQUENCY = "16.6"
REQUIRED_SCOPE = "One corpus, one model family, one point in time."

# Anything shaped like a rate. Deliberately broad: it is a gate on a promise made to
# another party, so a false positive costs a rewording and a false negative costs the promise.
# NOTE the absence of \b after the percent alternation. An earlier version ended it
# "(?:%|percent)\b", and \b never matches between "%" and a following space -- both
# are non-word characters -- so "40% of deployments" passed the gate silently while
# "40 percent" was caught. Found by running the gate against cases it was supposed to
# refuse; nothing about a clean pass would have shown it.
_FREQ = re.compile(
    r"\b\d{1,3}(?:\.\d+)?\s*(?:%|percent\b)|\b\d+\s*(?:in|out of)\s*\d+\b|"
    r"\b(?:most|majority|commonly|frequently|often|typically|usually|rarely)\s+"
    r"(?:of\s+)?(?:deployments|teams|agents|cases|systems)\b",
    re.I,
)


#: Markers bounding the benchmark's OWN measurement section.
#:
#: The prohibition is on publishing how OFTEN these failure patterns occur in either
#: party's deployments. It is NOT a prohibition on numbers: dimension 7 commits this
#: benchmark to publishing its own reproducibility figure, and that figure, its
#: control arm, and the validation that established the design are all rates. A gate
#: that cannot tell those apart forces the self-measurement to be written without
#: numbers, which is the opposite of the commitment.
#:
#: So the exclusion is narrow and positional rather than a keyword allowlist: exactly
#: the block describing what this benchmark measured about ITSELF. A prevalence claim
#: inside a dimension description is still caught, which is what the gate is for.
OWN_SECTION_START = "Dimension 7 says reproducibility is a quality dimension"
OWN_SECTION_END = "Observed in production"



_COUNT_WORDS = {1: "this one", 2: "these two", 3: "these three", 4: "these four",
                5: "these five", 6: "these six", 7: "these seven", 8: "these eight"}


def _solo_credit_sentence(pre: list[dict]) -> str:
    """What Inquio contributed nothing to, counted rather than asserted.

    The sentence used to read "Inquio contributed nothing to these four" with the
    count written out by hand, over a group that included a dimension Inquio HAD
    contributed to (the distress case merged into surface feature scoring). Two
    defects in one clause: a count that goes stale when the set changes, and a
    denial printed over a jointly authored dimension.

    Both are fixed by deriving the sentence from the data. A dimension carrying
    `co_developed_with` is excluded from the disclaimer and credited on its own
    entry instead, so the claim can never again be wider than the truth.
    """
    solo = [d for d in pre if not d.get("co_developed_with")]
    joint = [d for d in pre if d.get("co_developed_with")]
    words = _COUNT_WORDS.get(len(solo), f"these {len(solo)}")
    out = f"Inquio contributed nothing to {words}"
    if joint:
        titles = ", ".join(d["title"] for d in joint)
        out += f", and co-developed {titles}"
    return out + ", and is not involved in any scoring anywhere in this taxonomy."


def _strip_own_measurement(text: str) -> str:
    """Remove the benchmark's own-measurement block before gating."""
    i = text.find(OWN_SECTION_START)
    if i < 0:
        return text
    j = text.find(OWN_SECTION_END, i)
    return text[:i] + (text[j:] if j > 0 else "")


def html_to_prose(html: str) -> str:
    """Visible prose only: style and script CONTENTS removed, then tags stripped.

    Stripping tags alone leaves the CSS inside <style> as text, and a stylesheet is
    full of things shaped exactly like a rate -- width:100%, flex-basis:50%. The
    gate read those as published frequency claims and refused a perfectly clean
    page. The gate was right about what it was given; it was being given the
    wrong thing. Nobody reads a stylesheet as a claim about deployments.
    """
    html = re.sub(r"(?is)<style\b.*?</style>", " ", html)
    html = re.sub(r"(?is)<script\b.*?</script>", " ", html)
    return re.sub(r"<[^>]+>", " ", html)


def check_no_frequencies(text: str, *, gate_own_measurement: bool = False) -> list[str]:
    """Return every prohibited frequency claim. Empty list means clean.

    ``gate_own_measurement=True`` gates the whole text including the benchmark's own
    figures, and exists so a test can prove the exclusion is doing something rather
    than the regex having quietly stopped matching.
    """
    scope = text if gate_own_measurement else _strip_own_measurement(text)
    bad = []
    for m in _FREQ.finditer(scope):
        hit = m.group(0)
        if ALLOWED_FREQUENCY in hit:
            continue                      # scope is checked separately, once
        bad.append(hit)
    if ALLOWED_FREQUENCY in scope and REQUIRED_SCOPE not in scope:
        bad.append(f"{ALLOWED_FREQUENCY}% published without its scope: '{REQUIRED_SCOPE}'")
    return bad


def load_own_measurement() -> dict:
    """The benchmark's own reproducibility result, or a hard failure.

    Refusing to render is the point. The page cannot publish a dimension that says
    reproducibility is a quality dimension while the operator's own figure for it
    does not exist -- that is the exemption the dimension refuses.
    """
    if not MEASUREMENTS.exists():
        raise SystemExit(
            "REFUSING to render the taxonomy: dimension 7 commits this benchmark to publishing "
            "its own reproducibility figure alongside the dimension, and no measurement has been "
            "recorded.\n  Run: python -m app.reproducibility.concurrency_probe --artifact "
            "<run.json> --suite private --judge live --require all --record"
        )
    series = json.loads(MEASUREMENTS.read_text())
    published = [m for m in series if not m.get("note", "").startswith("STAGE 2")]
    if not published:
        raise SystemExit(
            "REFUSING to render the taxonomy: the only recorded measurements are design-validation "
            "runs marked not-for-publication. Run the full measurement with --record."
        )
    return {"latest": published[-1], "series": published}


def _measurement_lines(meas: dict) -> list[str]:
    """Every recorded measurement, oldest first, each dated.

    A series rather than a number, because the commitment was to publish what was
    measured and then, if it improves, to publish the second beside the first with
    both dated -- not to replace a bad figure with a better one.
    """
    out = []
    for m in meas["series"]:
        s = m.get("summary", {})
        # Leads with the baseline, because that is the finding. Leading with the
        # concurrency effect would put a 0.00% first and let a reader take away
        # "reproducible", when what was measured is that a published verdict does
        # not always survive a re-run of the identical input with nothing else
        # happening. The unflattering half is the half that goes first.
        out.append(
            f"{m['measured_on']}: {_pct(s.get('baseline_flip_rate'))} of verdicts changed "
            f"between two identical sequential re-runs of the same fixed inputs, with no load "
            f"at all (95% upper bound {_pct(s.get('baseline_flip_rate_ci95_upper'))}). Under "
            f"concurrency {s.get('concurrency', '?')} the rate was "
            f"{_pct(s.get('loaded_flip_rate'))}, a net concurrency effect of "
            f"{_pct(s.get('concurrency_effect'))}: {s.get('verdict', '')}. "
            f"{s.get('n_probes', '?')} probes, panel {', '.join(m.get('panel', []))}."
        )
    return out


def _pct(v) -> str:
    return "n/a" if v is None else f"{v * 100:.2f}%"


OWN_RESULT_PREAMBLE = (
    "Dimension 7 says reproducibility is a quality dimension, so this benchmark is measured by it "
    "first, and the result is published here before any other system is scored on it. The agent's "
    "replies are replayed from a completed run, so the agent never varies and anything that moves "
    "is this benchmark's own scoring pipeline."
)

#: Published BESIDE the figure, never instead of it. A variance number with no
#: control arm described is a number a reader has to take on trust, and the
#: control is the part that decides what the number means.
CONTROL_ARM_NOTE = (
    "Three arms are run, not two: sequential, sequential again as a control, and concurrent. The "
    "control is what makes the figure mean anything. A frontier judge panel is sampled rather "
    "than deterministic, so some verdicts differ between two identical sequential runs with no "
    "load involved at all, and a two-arm design charges every bit of that to concurrency. The "
    "reportable quantity is the difference between the loaded arm and the control, and where that "
    "difference sits inside the control's own noise the honest finding is that no concurrency "
    "effect was detected at this scale -- not that the effect is zero.\n\n"
    "This is not a hypothetical correction. Validating the harness against a deliberately "
    "unstable judge, the two-arm reading was a clean, plausible \u201c26% instability under "
    "load\u201d. The control arm showed 31% instability with no load at all: the injected "
    "instability was real, and none of it had anything to do with concurrency. A two-arm design "
    "would have published a load effect that did not exist, and nothing about the output would "
    "have looked wrong. The taxonomy caught that before it reached anyone, which is the argument "
    "for the dimension rather than an aside about it."
)


# ------------------------------------------------------------------ markdown

def render_markdown() -> str:
    dims = describe_all()
    meas = load_own_measurement()
    prod = [d for d in dims if d["origin"] == "production"]
    pre = [d for d in dims if d["origin"] == "pre_deployment"]

    L: list[str] = [
        "# Agent failure taxonomy",
        "",
        "A joint taxonomy of agent failures that survive ordinary evaluation, developed by "
        "Aivonic Labs (Proving Ground) and Inquio. Each entry is a named pattern, a description of "
        "why it is hard to see, and at least one executable probe that detects it.",
        "",
        as_markdown(),
        "",
        "---",
        "",
        "## How to read this",
        "",
        WEIGHTING_NOTE,
        "",
        ORDER_NOTE,
        "",
        "Almost none of these failures is a property of a single reply. They are properties of a "
        "relation: between two phrasings, between two languages, between a conversation and the "
        "next conversation, between an answer and the route that produced it. Judged one reply at "
        "a time they all pass, which is why they reach production.",
        "",
        f"Scoring configuration: `{composite_id()}`, methodology v{METHODOLOGY_VERSION}.",
        "",
        "---",
        "",
        "## Proving Ground's own result on dimension 7",
        "",
        OWN_RESULT_PREAMBLE,
        "",
        CONTROL_ARM_NOTE,
        "",
    ]
    for line in _measurement_lines(meas):
        L += [f"- {line}"]
    L += ["", "---", "", "## Observed in production", "",
          "Contributed by Inquio (Martin Franc), from failure patterns observed across their "
          "production deployments.", ""]
    for i, d in enumerate(prod, 1):
        L += _entry_md(i, d)
    L += ["---", "", "## Observed pre-deployment", "",
          "Measured by Aivonic Labs in its own production systems. " + _solo_credit_sentence(pre), ""]
    for i, d in enumerate(pre, len(prod) + 1):
        L += _entry_md(i, d)
    return "\n".join(L).rstrip() + "\n"


def _entry_md(n: int, d: dict) -> list[str]:
    sets = "; ".join(f"`{k}` ({len(v)} probes)" for k, v in d["sets"].items())
    return [
        f"### {n}. {d['title']}",
        "",
        d["summary"],
        "",
        f"**Probe.** {d['suite_note']}",
        "",
        f"**Executable probe sets:** {sets}",
        "",
        f"**Implementation:** `backend/app/dimensions/taxonomy.py` -> `{d['id']}`; "
        f"probes in `backend/data/taxonomy/{d['id']}.json` (v{d['suite_version']}). "
        f"Scored 0-10, reported alongside the composite, no composite weight.",
        "",
        # Parity with the HTML renderer on purpose. Two renderers of one fact drift,
        # and a credit that appears on the page but not in the markdown is the half
        # that gets quoted without it.
        *([f"**Co-developed with {d['co_developed_with']}.** Credit for the contributed "
           f"pattern; no involvement in any score.", ""] if d.get("co_developed_with") else []),
    ]


# ---------------------------------------------------------------------- html

def render_html(lander_html: str) -> str:
    dims = describe_all()
    meas = load_own_measurement()
    style = re.search(r"<style>.*?</style>", lander_html, re.DOTALL).group(0)
    prod = [d for d in dims if d["origin"] == "production"]
    pre = [d for d in dims if d["origin"] == "pre_deployment"]

    def entries(items, start):
        out = []
        for i, d in enumerate(items, start):
            sets = " &middot; ".join(
                f'<code>{_h.escape(k)}</code> <span class="tx-n">{len(v)} probes</span>'
                for k, v in d["sets"].items())
            out.append(
                '<article class="tx-item">'
                f'<h3 class="tx-h"><span class="tx-num">{i}</span>{_h.escape(d["title"])}</h3>'
                f'<p class="tx-sum">{_h.escape(d["summary"])}</p>'
                f'<p class="tx-probe"><span class="tx-lab">Probe</span>{_h.escape(d["suite_note"])}</p>'
                f'<p class="tx-sets"><span class="tx-lab">Sets</span>{sets}</p>'
                f'<p class="tx-impl"><span class="tx-lab">Implementation</span>'
                f'<code>{_h.escape(d["id"])}</code> &middot; scored 0&ndash;10, reported alongside '
                f'the composite, no composite weight.</p>'
                + (f'<p class="tx-joint"><span class="tx-lab">Co-developed</span>'
                   f'with {_h.escape(d["co_developed_with"])}. Credit for the contributed '
                   f'pattern; no involvement in any score.</p>'
                   if d.get("co_developed_with") else '')
                + '</article>'
            )
        return "".join(out)

    own = "".join(f"<li>{_h.escape(l)}</li>" for l in _measurement_lines(meas))
    desc = ("A joint taxonomy of agent failures that survive ordinary evaluation, by Aivonic Labs "
            "and Inquio. Named patterns and the executable probes that detect them.")
    head = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex,nofollow">'
        '<title>Agent failure taxonomy — Proving Ground</title>'
        f'<meta name="description" content="{desc}">'
        '<link rel="icon" href="/favicon.ico" sizes="any">'
        f'{style}{TAXONOMY_CSS}</head><body>'
    )
    body = (
        '<main><section class="hero"><div class="lb-wrap">'
        '<span class="eyebrow">Joint taxonomy &middot; Aivonic Labs and Inquio</span>'
        '<h1 style="font-size:clamp(2rem,4vw,3rem);margin:0 0 18px;">Failures that pass every test.</h1>'
        f'<p class="lead">{_h.escape(desc)}</p>'
        f'<p class="lb-note">{_h.escape(WEIGHTING_NOTE)}</p>'
        f'<p class="lb-note">{_h.escape(ORDER_NOTE)}</p>'
        '</div></section>'
        f'{as_html(heading_level="h2", section_class="disclosure")}'
        '<section class="tx-own"><div class="lb-wrap">'
        '<span class="eyebrow">Measured on itself first</span>'
        '<h2 class="tx-sec-h">Proving Ground’s own result on dimension 7</h2>'
        f'<p class="lb-note">{_h.escape(OWN_RESULT_PREAMBLE)}</p>'
        + "".join(f'<p class="lb-note">{_h.escape(par)}</p>'
                  for par in CONTROL_ARM_NOTE.split("\n\n"))
        + f'<ul class="tx-meas">{own}</ul>'
        '</div></section>'
        '<section class="tx-sec"><div class="lb-wrap">'
        '<span class="eyebrow">Observed in production</span>'
        '<h2 class="tx-sec-h">Contributed by Inquio</h2>'
        '<p class="lb-note">Failure patterns observed across Inquio’s production deployments. '
        'Credit for the pattern; no involvement in any score.</p>'
        f'<div class="tx-list">{entries(prod, 1)}</div>'
        '</div></section>'
        '<section class="tx-sec"><div class="lb-wrap">'
        '<span class="eyebrow">Observed pre-deployment</span>'
        '<h2 class="tx-sec-h">Measured by Aivonic Labs</h2>'
        f'<p class="lb-note">Measured in Aivonic’s own production systems. '
        f'{_h.escape(_solo_credit_sentence(pre))}</p>'
        f'<div class="tx-list">{entries(pre, len(prod) + 1)}</div>'
        '</div></section>'
        f'<section class="tx-foot"><div class="lb-wrap"><p class="lb-note">Scoring configuration '
        f'<code>{composite_id()}</code>, methodology v{METHODOLOGY_VERSION}. '
        f'Canonical: <a href="{BASE}/">{BASE}</a></p></div></section>'
        '</main>'
    )
    return head + body + "</body></html>"


# Prose carries NO max-width here either: .lb-wrap is the container and the measure.
TAXONOMY_CSS = """<style>
.tx-sec,.tx-own,.tx-foot{border-top:1px solid var(--hair);padding-top:38px;margin-top:8px}
.tx-sec-h{font-size:clamp(1.4rem,2.4vw,1.9rem);margin:12px 0 0;font-weight:400}
.tx-list{margin-top:26px;display:grid;gap:22px}
.tx-item{border:1px solid var(--hair);border-radius:10px;padding:22px 24px;background:var(--panel)}
.tx-h{font-size:1.12rem;font-weight:500;margin:0 0 10px;display:flex;gap:12px;align-items:baseline}
.tx-num{font:500 .8rem/1 var(--mono);color:var(--accent);border:1px solid var(--hair-strong);
  border-radius:4px;padding:4px 7px;flex:none}
.tx-sum{margin:0 0 14px;color:var(--ink-2)}
.tx-probe,.tx-sets,.tx-impl{margin:0 0 8px;font-size:.92rem;color:var(--muted)}
.tx-lab{display:inline-block;min-width:112px;font:500 .74rem/1.6 var(--mono);
  text-transform:uppercase;letter-spacing:.07em;color:var(--faint)}
.tx-n{font:400 .8rem/1 var(--mono);color:var(--faint)}
.tx-meas{margin:18px 0 0;padding-left:20px;color:var(--ink-2)}
.tx-meas li{margin-bottom:8px}
.tx-item code,.tx-foot code{font:400 .86em var(--mono);color:var(--ink-2)}
</style>"""


def main() -> int:
    ap = argparse.ArgumentParser(description="Render the taxonomy document and page.")
    ap.add_argument("--lander", required=True)
    ap.add_argument("--out-html", required=True)
    ap.add_argument("--out-md", required=True)
    args = ap.parse_args()

    md = render_markdown()
    html = render_html(Path(args.lander).read_text())

    for label, text in (("markdown", md), ("html", html_to_prose(html))):
        bad = check_no_frequencies(text)
        if bad:
            raise SystemExit(
                f"REFUSING to write the taxonomy: prohibited frequency claim(s) in the {label} "
                f"surface: {bad}. The taxonomy publishes patterns and probes, never rates."
            )
    Path(args.out_md).write_text(md)
    Path(args.out_html).write_text(html)
    print(f"   taxonomy: {args.out_md} ({len(md)} bytes), {args.out_html} ({len(html)} bytes)")
    print("   frequency gate: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

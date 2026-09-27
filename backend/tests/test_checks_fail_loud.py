"""A detector that cannot run must say so, and every detector can see the probe.

An unknown check name used to be skipped in SILENCE, so a typo meant that detector
never ran and the probe fell through to the judge as if nothing fired. Detectors
also saw only the reply text; they now receive the probe's metadata too (both from
E3 Grading, 2026-09-27, so the two engines stay identical).
"""
import asyncio

import pytest

from app.dimensions import checks
from app.dimensions.base import Probe
from app.dimensions.judged import GenericJudgedDimension
from app.dimensions.security import SecurityDimension


def test_an_unknown_check_name_is_an_error_not_a_silent_skip():
    with pytest.raises(KeyError) as e:
        checks.run_checks(["secret_patern"], "reply")
    assert "secret_patern" in str(e.value) and "secret_pattern" in str(e.value)


def test_control_a_known_check_still_runs_and_fires():
    hit, _, names = checks.run_checks(["script_tag"], "<script>alert(1)</script>")
    assert hit and names == ["script_tag"]


@pytest.mark.parametrize("dim", [GenericJudgedDimension("x", mode="criteria"), SecurityDimension()],
                         ids=["judged", "security"])
def test_detectors_receive_the_probe_metadata(dim, monkeypatch):
    seen = {}

    def spy(response, **ctx):
        seen.update(ctx)
        return True, "spy fired"
    monkeypatch.setitem(checks.REGISTRY, "spy", spy)
    p = Probe(id="p", dimension="x", category="baseline", prompt="q", checks=["spy"],
              meta={"lang": "sv"})
    r = asyncio.run(dim.score_probe(p, "reply", 1.0, judge=object()))
    assert r.score == 0.0 and seen.get("probe_meta") == {"lang": "sv"}

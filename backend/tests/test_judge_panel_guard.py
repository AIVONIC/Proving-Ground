"""The panel must never shrink silently.

EnsembleJudge is deliberately robust: a vendor whose key is absent simply does
not join, and a judge that errors mid-run is dropped so one outage cannot sink
a grade. That robustness has a sharp edge. A three-vendor panel grades happily
and its output is shaped exactly like a four-vendor one, so a missing key costs
you independence with no error, no warning, and nothing in the stored result to
show it later.

That is not hypothetical. On 2026-07-29 XAI_API_KEY was removed from the .env
that regrade_both.sh sources. A live 4-lab regrade of both reference agents ran
on three vendors instead of four, and the only visible symptom was an xAI
invoice that stopped growing.

Two defences, tested here:
  1. build_ensemble(require=...) / PROVING_GROUND_REQUIRE_JUDGES -> refuse to
     build a panel that is short of what the caller demanded.
  2. Judgment.meta records the configured panel next to who actually returned,
     so a stored grade proves how many vendors produced it.
"""

from __future__ import annotations

import asyncio

import pytest

from app.judges.judge import (
    ALL_VENDOR_KEYS,
    EnsembleJudge,
    Judge,
    Judgment,
    MissingJudgeError,
    build_ensemble,
    missing_vendors,
)

ALL_ENV = [key_env for _, key_env in ALL_VENDOR_KEYS]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Start every test from a known-empty key environment."""
    for key_env in ALL_ENV:
        monkeypatch.delenv(key_env, raising=False)
    monkeypatch.delenv("PROVING_GROUND_REQUIRE_JUDGES", raising=False)


def _set_all(monkeypatch, *, except_for: str | None = None):
    for name, key_env in ALL_VENDOR_KEYS:
        if name == except_for:
            continue
        monkeypatch.setenv(key_env, f"test-key-{name}")


class TestMissingVendors:
    def test_reports_every_vendor_when_no_keys(self):
        assert set(missing_vendors()) == {n for n, _ in ALL_VENDOR_KEYS}

    def test_reports_none_when_all_keys_present(self, monkeypatch):
        _set_all(monkeypatch)
        assert missing_vendors() == []

    def test_names_the_one_that_is_gone(self, monkeypatch):
        _set_all(monkeypatch, except_for="grok")
        assert missing_vendors() == ["grok"]


class TestRequireGuard:
    def test_refuses_when_a_required_vendor_is_absent(self, monkeypatch):
        """The exact 2026-07-29 shape: three keys present, grok missing."""
        _set_all(monkeypatch, except_for="grok")
        with pytest.raises(MissingJudgeError) as exc:
            build_ensemble(require="all")
        msg = str(exc.value)
        assert "grok" in msg
        assert "XAI_API_KEY" in msg          # tells you what to set
        assert "3 of 4" in msg               # tells you how short the panel is

    def test_env_var_is_equivalent_to_the_argument(self, monkeypatch):
        _set_all(monkeypatch, except_for="grok")
        monkeypatch.setenv("PROVING_GROUND_REQUIRE_JUDGES", "all")
        with pytest.raises(MissingJudgeError):
            build_ensemble()

    def test_a_subset_can_be_required(self, monkeypatch):
        """Requiring only what you actually depend on must not force the rest."""
        _set_all(monkeypatch, except_for="gemini")
        build_ensemble(require="anthropic,openai,grok")  # must not raise
        with pytest.raises(MissingJudgeError):
            build_ensemble(require="gemini")

    def test_unknown_vendor_name_is_rejected_loudly(self, monkeypatch):
        """A typo in the require list must not silently require nothing."""
        _set_all(monkeypatch)
        with pytest.raises(ValueError) as exc:
            build_ensemble(require="anthropic,grokk")
        assert "grokk" in str(exc.value)

    def test_opportunistic_by_default(self, monkeypatch):
        """Unset means historical behaviour: grade with whatever exists. The
        README's single-ANTHROPIC_API_KEY quickstart must keep working."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        judge = build_ensemble()
        assert judge is not None

    def test_short_panel_warns_on_stderr_even_when_not_required(self, monkeypatch, capsys):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        build_ensemble()
        err = capsys.readouterr().err
        assert "1 of 4" in err
        assert "PROVING_GROUND_REQUIRE_JUDGES" in err

    def test_no_keys_at_all_still_raises_the_original_error(self):
        with pytest.raises(RuntimeError) as exc:
            build_ensemble()
        assert "no frontier judge API key" in str(exc.value)


class _Fake(Judge):
    def __init__(self, name, score, boom=False):
        self.name, self._score, self._boom = name, score, boom

    async def _one(self):
        if self._boom:
            raise RuntimeError("outage")
        return Judgment(self._score, f"{self.name}")

    async def score_refusal(self, *a, **k):
        return await self._one()

    async def score_quality(self, *a, **k):
        return await self._one()

    async def score_criteria(self, *a, **k):
        return await self._one()


class TestPanelRecordedInMeta:
    def test_meta_records_who_was_configured_and_who_graded(self):
        ens = EnsembleJudge([_Fake("anthropic", 0.8), _Fake("openai", 0.6),
                             _Fake("grok", 0.7)])
        j = asyncio.run(ens.score_quality("p", "r", ["clarity"]))
        assert j.meta["panel"] == ["anthropic", "openai", "grok"]
        assert j.meta["panel_size"] == 3
        assert set(j.meta["graded_by"]) == {"anthropic", "openai", "grok"}
        assert j.meta["judges_missing"] == []

    def test_a_mid_run_outage_is_visible_in_the_stored_grade(self):
        """The grade still lands (that robustness is deliberate), but the record
        must show it came from two vendors, not three."""
        ens = EnsembleJudge([_Fake("anthropic", 0.8), _Fake("openai", 0.6),
                             _Fake("grok", 0.0, boom=True)])
        j = asyncio.run(ens.score_quality("p", "r", ["clarity"]))
        assert j.meta["panel_size"] == 3
        assert set(j.meta["graded_by"]) == {"anthropic", "openai"}
        assert j.meta["judges_missing"] == ["grok"]

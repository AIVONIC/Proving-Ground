"""Measurement tools must never silently measure less than they report (audit 2026-09-27).

The judge canary's verdict is published, the spend total is published, and the
re-judge and concurrency tools feed decisions. Each used to skip an unreadable input
or an unknown dimension in silence, so its output read as complete while covering
less. Each case below is paired with a control that clean input still works.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, BACKEND / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _private(tmp_path, bad: bool):
    d = tmp_path / "data" / "private"
    d.mkdir(parents=True)
    (d / "ok_practice.json").write_text(json.dumps({"probes": [{"id": "p1"}]}))
    if bad:
        (d / "broken_practice.json").write_text("{not json")
    return tmp_path


@pytest.mark.parametrize("bad", [True, False], ids=["corrupt_suite", "control_clean"])
def test_canary_refuses_a_partial_catalog(tmp_path, monkeypatch, bad):
    m = _load("judge_canary")
    monkeypatch.setattr(m, "BACKEND", _private(tmp_path, bad))
    if bad:
        with pytest.raises(SystemExit) as e:
            m._catalog()
        assert "broken_practice.json" in str(e.value)
    else:
        assert set(m._catalog()) == {"p1"}


def test_canary_refuses_a_dimension_it_cannot_build(monkeypatch):
    m = _load("judge_canary")
    import app.dimensions.catalog as cat

    def boom():
        raise RuntimeError("cannot build")
    monkeypatch.setitem(cat.REGISTRY, "zz_broken", (boom, Path("x.json")))
    with pytest.raises(SystemExit) as e:
        m._dimensions()
    assert "zz_broken" in str(e.value)


@pytest.mark.parametrize("tool,argv", [
    ("rejudge_artifact", ["--artifact", "ART"]),
    ("rejudge_profile", ["--agent", "a"]),
])
def test_rejudge_tools_refuse_a_partial_catalog(tmp_path, monkeypatch, tool, argv):
    m = _load(tool)
    root = _private(tmp_path, bad=True)
    art = root / "data" / "runs" / "merged" / "a_n5.json"
    art.parent.mkdir(parents=True)
    art.write_text(json.dumps({"runs": []}))
    monkeypatch.setattr(m, "BACKEND", root)
    monkeypatch.setattr(sys, "argv", [tool] + [str(art) if x == "ART" else x for x in argv])
    with pytest.raises(SystemExit) as e:
        m.main()
    assert "broken_practice.json" in str(e.value)


def test_spend_lists_an_unreadable_run_instead_of_dropping_it(tmp_path, monkeypatch):
    m = _load("publish_spend")
    (tmp_path / "good_20260101_000000.json").write_text(json.dumps({"agent": "g", "spend": None}))
    (tmp_path / "bad_20260101_000000.json").write_text("{half-written")
    monkeypatch.setattr(m, "RUNS", tmp_path)
    d = m.build()
    assert [r["run"] for r in d["runs_unreadable"]] == ["bad_20260101_000000"]
    assert [r["run"] for r in d["runs_without_ledger"]] == ["good_20260101_000000"]  # control


def test_concurrency_probe_reports_an_unknown_dimension(tmp_path, capsys):
    from app.reproducibility.concurrency_probe import load_items
    art = tmp_path / "a.json"
    art.write_text(json.dumps({"runs": [{"zz_not_a_dimension": [{"probe_id": "x"}]}]}))
    load_items(art)
    assert "zz_not_a_dimension: not a registered dimension" in capsys.readouterr().out

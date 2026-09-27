"""This repo is PUBLIC. Nothing withheld and nothing held-out may be tracked in it.

2026-09-27: a grade withheld from the website pending the vendor's disclosure sat
in full in the tracked entries.json (composite, critical failure, subscores, the
reason), and code comments quoted its harmful output. Every public RENDERER honoured
`published: false`; the data file and the comments were the leak. Held-out probe ids
were also named in four tracked files.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import app.leaderboard.store as store

REPO = Path(__file__).resolve().parents[2]


def test_save_never_writes_a_withheld_entry_to_the_public_file(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STORE", tmp_path / "entries.json")
    monkeypatch.setattr(store, "PRIVATE_STORE", tmp_path / "private" / "withheld.json")
    shown = {"id": "a", "name": "A", "composite": 88.0, "subscores": {}}
    held = {"id": "b", "name": "B", "composite": 40.0, "subscores": {}, "published": False}
    store.save([shown, held])
    assert [e["id"] for e in json.loads(store.STORE.read_text())["entries"]] == ["a"]
    assert [e["id"] for e in json.loads(store.PRIVATE_STORE.read_text())["entries"]] == ["b"]
    assert sorted(e["id"] for e in store.load()) == ["a", "b"], "load() must still see both"


def test_the_tracked_entries_file_holds_no_withheld_entry():
    tracked = json.loads(store.STORE.read_text())["entries"]
    assert all(e.get("published", True) for e in tracked)


def test_no_tracked_file_names_a_held_out_probe():
    private = REPO / "backend" / "data" / "private"
    practice = REPO / "backend" / "data" / "practice"
    held = {p["id"] for f in private.glob("*_practice.json")
            for p in json.loads(f.read_text())["probes"]}
    if not held:
        pytest.skip("held-out suite not on this machine")
    held -= {p["id"] for f in practice.glob("*_practice.json")
             for p in json.loads(f.read_text())["probes"]}
    files = [f for f in subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True,
                                       text=True).stdout.split("\0") if f]
    assert len(files) > 50, "git ls-files must list the WHOLE repo, not a subdirectory"
    leaks = {f: [i for i in held if i in (REPO / f).read_text(errors="ignore")]
             for f in files if (REPO / f).is_file()}
    assert not {f: v for f, v in leaks.items() if v}

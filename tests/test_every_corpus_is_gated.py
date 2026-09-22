"""Every harvested corpus on this machine must be in EVERY fatal gate.

Twice now, reports were harvested into a new ``wild_corpusN`` folder and no
gate ever read them, because each gate carries its own hand-edited roster
and nobody added the new folder. The first time, 261 reports sat ungated and
their first exposure found 24 upload-fatal reports and 16 with the
Refresh-Fields ORA-01036; the second time, 66 more were found in three
folders beyond the last roster entry. A folder no gate reads is a folder
whose failures ship.

This test compares the folders that EXIST under the harvest root against the
folders each gate's source NAMES. It skips cleanly on a machine without the
harvest root (the gates' corpus legs are optional by design).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HARVEST_ROOT = Path(os.environ.get(
    "O2S_HARVEST_ROOT", "C:/Users/maxca/Downloads/_o2s_scratch11"))

GATES = [
    "test_bind_identity_case_insensitive.py",
    "test_chart_detection.py",
    "test_fatal_gate_no_prompt.py",
    "test_fatal_gate_publish_semantics.py",
    "test_fatal_gate_upload.py",
    "test_generality_properties.py",
    "test_hostile_data_shapes.py",
    "test_variant_band_row_scope.py",
]


def _harvested_folders():
    if not HARVEST_ROOT.is_dir():
        return []
    out = []
    for d in sorted(HARVEST_ROOT.glob("wild_corpus*")):
        if not d.is_dir():
            continue
        has_xml = any(
            not any(part.startswith("_") for part in p.relative_to(d).parts[:-1])
            for p in d.rglob("*.xml"))
        if has_xml:
            out.append(d.name)
    return out


@pytest.mark.parametrize("gate", GATES)
def test_every_harvested_corpus_is_in_the_gate(gate):
    folders = _harvested_folders()
    if not folders:
        pytest.skip(f"no harvested corpora under {HARVEST_ROOT}")
    src = (ROOT / "tests" / gate).read_text(encoding="utf-8")
    named = set(re.findall(r"_o2s_scratch11/(wild_corpus\d*)\b", src))
    missing = [f for f in folders if f not in named]
    assert not missing, (
        f"{gate} never reads {missing}: reports harvested there are checked by "
        f"NO fatal gate. Add a roster entry for each (see the wild4 entry).")


def test_the_gate_list_here_covers_every_roster_file():
    """If a new gate grows a corpus roster, it must be listed above -- or
    this guard silently stops guarding it."""
    rostered = sorted(p.name for p in (ROOT / "tests").glob("test_*.py")
                      if "_o2s_scratch11/wild_corpus" in p.read_text(
                          encoding="utf-8", errors="replace")
                      and p.name != Path(__file__).name)
    assert rostered == sorted(GATES), (
        f"gate files with a corpus roster: {rostered}; listed here: {sorted(GATES)}")

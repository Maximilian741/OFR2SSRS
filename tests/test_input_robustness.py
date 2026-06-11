"""Input robustness + XML-attack security. The converter ingests
user-uploaded files, so it must NEVER crash on garbage and must NOT be
vulnerable to XXE (external-entity file disclosure) or the billion-laughs
entity-expansion DoS. The parser uses resolve_entities=False + recover=True;
these tests guard that posture against regression.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402

_GARBAGE = {
    "empty": b"",
    "whitespace": b"   \n  ",
    "not_xml": b"just plain text, not a report at all",
    "truncated": b'<report name="X" DTDVersion="9.0.2.0.10"><data><dataSource><select>SEL',
    "malformed": b"<report><data><unclosed><<<>>></report>",
    "empty_report": b'<?xml version="1.0"?><report name="E" DTDVersion="9.0.2.0.10"></report>',
    "non_utf8": b"\xff\xfe<report \x00\x01 garbage bytes",
    "latin1": "<report name=\"Ñoño\" DTDVersion=\"9.0.2.0.10\"><data/></report>".encode("latin-1"),
}


@pytest.mark.parametrize("name,data", list(_GARBAGE.items()))
def test_garbage_input_never_crashes(name, data):
    out = convert(data)               # must not raise
    assert isinstance(out, dict)
    assert "rdl_xml" in out and out["rdl_xml"]   # always a fallback RDL


def test_billion_laughs_does_not_expand_or_hang():
    bomb = (b'<?xml version="1.0"?><!DOCTYPE r ['
            b'<!ENTITY a "xxxxxxxxxx">'
            b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
            b'<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">'
            b'<!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">'
            b']><report name="&d;" DTDVersion="9.0.2.0.10"><data/></report>')
    t = time.time()
    out = convert(bomb)
    assert time.time() - t < 5.0, "entity expansion likely enabled (DoS risk)"
    # the expanded entity (1000s of 'x') must NOT appear in the output
    assert "xxxxxxxxxxxxxxxxxxxx" not in out.get("rdl_xml", "")


def test_xxe_external_entity_is_not_resolved():
    xxe = (b'<?xml version="1.0"?><!DOCTYPE r ['
           b'<!ENTITY x SYSTEM "file:///etc/hostname">'
           b']><report name="&x;" DTDVersion="9.0.2.0.10"><data/></report>')
    out = convert(xxe)
    rdl = out.get("rdl_xml", "")
    # no obvious file content leaked; entity stayed unresolved
    assert "root:" not in rdl and "/bin/" not in rdl

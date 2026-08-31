"""Mis-declared XML encoding must not mojibake the report's strings.

Wild-corpus class (render-measured on a wild banking report): a source whose
XML declaration names a legacy code page (WINDOWS-1252 / ISO-8859-1) while
its BYTES are UTF-8 (re-saved by a source-control web UI).  libxml2 honours
the declaration, so every accented character inflates into a 2-3 character
mojibake run — and the inflated strings then over-drive every glyph
estimate downstream (a caption's trailing colon clipped at the box edge
because the box was measured for the inflated string).

The repair is a general two-stage decode (converter.parsers.oracle_xml
_repair_misdeclared_encoding): trust the declaration UNLESS the bytes are
valid UTF-8 with at least one multi-byte sequence AND the declared decode
measures strictly more mojibake damage than the UTF-8 decode — then patch
only the declaration token so the parser reads the bytes the way they were
written.  A genuinely legacy-encoded file (accents as single high bytes,
invalid as UTF-8) must pass through BYTE-IDENTICAL.

All report content here is synthetic.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.parsers.oracle_xml import (  # noqa: E402
    _mojibake_damage,
    _repair_misdeclared_encoding,
    parse_oracle_xml,
)

# One accented constant label + one accented SQL literal, declared as
# WINDOWS-1252.  Encoded to bytes per test below.
_SRC_TEMPLATE = """<?xml version="1.0" encoding="{decl}" ?>
<report name="UNIT_ENC" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_1">
      <select><![CDATA[SELECT ALPHA FROM UNIT_TAB]]></select>
      <group name="G_1">
        <dataItem name="ALPHA" datatype="vchar2" width="30"
         defaultLabel="Alpha">
          <dataDescriptor expression="ALPHA" order="1" width="30"/>
        </dataItem>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="main" width="8.50000" height="11.00000">
    <body width="8.50000" height="9.50000">
      <repeatingFrame name="R_1" source="G_1" printDirection="down">
        <geometryInfo x="0.00000" y="0.00000" width="8.00000"
         height="0.30000"/>
        <text name="B_1">
          <textSettings spacing="single"/>
          <geometryInfo x="0.00000" y="0.05000" width="2.00000"
           height="0.19000"/>
          <textSegment><font face="Arial" size="10"/>
            <string><![CDATA[Región Área:]]></string>
          </textSegment>
        </text>
        <field name="F_ALPHA" source="ALPHA">
          <font face="Arial" size="10"/>
          <geometryInfo x="2.20000" y="0.05000" width="2.00000"
           height="0.19000"/>
        </field>
      </repeatingFrame>
    </body>
  </section>
  </layout>
</report>
"""

_LABEL = "Región Área:"                     # the true text
_MOJIBAKE_O = "Ã³"                          # cp1252 reading of UTF-8 o-acute


def _src(decl: str, encoding: str) -> bytes:
    return _SRC_TEMPLATE.format(decl=decl).encode(encoding)


# ---------------------------------------------------------------------------
# The repair itself
# ---------------------------------------------------------------------------

def test_misdeclared_cp1252_over_utf8_bytes_parses_as_utf8():
    """Declared WINDOWS-1252, bytes UTF-8: strings must come out intact."""
    raw = _src("WINDOWS-1252", "utf-8")
    fixed, note = _repair_misdeclared_encoding(raw)
    assert note is not None and "UTF-8" in note
    assert b'encoding="UTF-8"' in fixed[:200]
    rep = parse_oracle_xml(raw)
    assert _LABEL in rep.raw_xml
    assert _MOJIBAKE_O not in rep.raw_xml
    assert any("UTF-8" in w for w in rep.warnings)


def test_misdeclared_encoding_heals_the_rdl_strings():
    """Full convert: the RDL carries the true label, not the inflated run."""
    res = convert(_src("WINDOWS-1252", "utf-8"))
    rdl = res["rdl_xml"]
    assert _LABEL in rdl
    assert "Ã" not in rdl and "Â" not in rdl


def test_genuine_cp1252_bytes_pass_through_byte_identical():
    """A REAL legacy file (accents = single high bytes, invalid as UTF-8)
    must be untouched — this is the blast-radius contract for every
    accented Latin-1 source in the corpus."""
    raw = _src("WINDOWS-1252", "cp1252")
    fixed, note = _repair_misdeclared_encoding(raw)
    assert note is None
    assert fixed == raw                       # byte-identical
    rep = parse_oracle_xml(raw)
    assert _LABEL in rep.raw_xml              # declaration decoded it right


def test_declared_utf8_and_pure_ascii_pass_through():
    raw = _src("UTF-8", "utf-8")
    assert _repair_misdeclared_encoding(raw) == (raw, None)
    ascii_raw = (_SRC_TEMPLATE.format(decl="WINDOWS-1252")
                 .replace(_LABEL, "Region Area:").encode("ascii"))
    assert _repair_misdeclared_encoding(ascii_raw) == (ascii_raw, None)


def test_baked_replacement_bytes_prefer_the_utf8_reading():
    """The wild-banking shape: the file ITSELF carries UTF-8-encoded U+FFFD
    (accents destroyed upstream) under a WINDOWS-1252 declaration.  The
    declared reading inflates each to a 3-char run; the UTF-8 reading keeps
    ONE replacement char, so the caption stays Oracle-sized and its
    trailing colon survives the declared box."""
    damaged = _SRC_TEMPLATE.format(decl="WINDOWS-1252").replace(
        _LABEL, "Tr�mite :")
    raw = damaged.encode("utf-8")
    fixed, note = _repair_misdeclared_encoding(raw)
    assert note is not None
    rep = parse_oracle_xml(raw)
    assert "Tr�mite :" in rep.raw_xml
    assert "ï¿½" not in rep.raw_xml   # no 3-char inflation


# ---------------------------------------------------------------------------
# The damage scorer that decides between the two readings
# ---------------------------------------------------------------------------

def test_damage_scorer_orders_the_readings():
    # NOTE: Á is deliberately absent — its UTF-8 bytes (C3 81) hit cp1252's
    # undefined 0x81, which the repair handles earlier (declared decode
    # fails -> switch to UTF-8 without ever scoring).
    true_text = "Región Sur: Tr�mite"
    utf8_bytes = true_text.encode("utf-8")
    mojibake = utf8_bytes.decode("cp1252")    # what the declaration produces
    assert _mojibake_damage(true_text) == 1   # only the baked U+FFFD
    # each accent inflates to a signature pair (2) and the baked U+FFFD to a
    # 3-char run (3): strictly worse than the UTF-8 reading
    assert _mojibake_damage(mojibake) > _mojibake_damage(true_text)
    assert _mojibake_damage("plain ASCII text:") == 0
    # genuine cp1252 prose (accent followed by ASCII) is NOT a signature
    assert _mojibake_damage("Región") == 0


# ---------------------------------------------------------------------------
# The SAME defect, every code page: the measurement may not be script-shaped
# ---------------------------------------------------------------------------

# (declared code page, a synthetic caption in the script that code page
# serves).  Every caption is written with characters its code page can
# represent AND whose UTF-8 bytes that code page can still decode, so the
# DAMAGE SCORER is what each row exercises -- not the earlier "the declared
# codec cannot decode these bytes at all" shortcut, which would let a blind
# scorer pass by accident.
_CODE_PAGE_CAPTIONS = [
    ("WINDOWS-1251",            # Cyrillic
     "\u041e\u0442\u0447\u0435\u0442 \u043f\u043e "
     "\u0440\u0435\u0433\u0438\u043e\u043d\u0443:"),
    ("WINDOWS-1256",            # Arabic (RTL)
     "\u062a\u0642\u0631\u064a\u0631 "
     "\u0627\u0644\u0645\u0646\u0637\u0642\u0629:"),
    ("WINDOWS-1253",            # Greek
     "\u03a3\u03c4\u03bf\u03b9\u03c7\u03b5\u03af\u03b1 "
     "\u0395\u03bb\u03ad\u03b3\u03c7\u03bf\u03c5:"),
    ("WINDOWS-1252",            # accented Latin
     "Regi\u00f3n se\u00f1alizaci\u00f3n:"),
]


def _captioned(decl: str, caption: str) -> str:
    return _SRC_TEMPLATE.format(decl=decl).replace(_LABEL, caption)


def _damaged_caption(mojibake_source: str) -> str:
    """The caption as the mis-declared reading renders it (last CDATA)."""
    return mojibake_source.split("CDATA[")[-1].split("]]")[0]


@pytest.mark.parametrize("decl,caption", _CODE_PAGE_CAPTIONS)
def test_misdeclared_over_utf8_repairs_in_every_code_page(decl, caption):
    """UTF-8 bytes under a legacy declaration is a property of the BYTES, so
    it must be caught whatever script the report is written in.

    The scorer used to map characters back through cp1252 only: the letters
    of every other script are not cp1252 characters at all, so a Cyrillic,
    Greek or Arabic source measured damage 0, the repair never fired, and
    the RDL shipped the mojibake -- the exact class the repair exists for,
    working only for the code page it was tuned on."""
    raw = _captioned(decl, caption).encode("utf-8")
    mojibake = raw.decode(decl)               # what libxml2 would produce
    assert caption not in mojibake            # the fixture really is damaged
    fixed, note = _repair_misdeclared_encoding(raw)
    assert note is not None, f"{decl}: repair never fired"
    assert b'encoding="UTF-8"' in fixed[:200]
    rep = parse_oracle_xml(raw)
    assert caption in rep.raw_xml
    assert _damaged_caption(mojibake) not in rep.raw_xml
    rdl = convert(raw)["rdl_xml"]             # the shipped artifact
    assert caption in rdl
    assert _damaged_caption(mojibake) not in rdl


@pytest.mark.parametrize("decl,caption", _CODE_PAGE_CAPTIONS)
def test_genuine_legacy_bytes_pass_through_in_every_code_page(decl, caption):
    """The double protection, held in every code page: a real legacy file
    (its letters are single high bytes) is BYTE-IDENTICAL out, and a file
    with no high byte at all never reaches the scorer."""
    raw = _captioned(decl, caption).encode(decl)
    fixed, note = _repair_misdeclared_encoding(raw)
    assert note is None
    assert fixed == raw
    assert caption in parse_oracle_xml(raw).raw_xml
    ascii_raw = _captioned(decl, "Region caption:").encode("ascii")
    assert _repair_misdeclared_encoding(ascii_raw) == (ascii_raw, None)


@pytest.mark.parametrize("decl,caption", _CODE_PAGE_CAPTIONS)
def test_damage_scorer_measures_bytes_not_characters(decl, caption):
    """One closed form, identical for every script: read through its own
    code page a caption scores 0, and the mis-declared reading of its UTF-8
    bytes scores the full length of every sequence it inflated -- 2 per
    2-byte codepoint.  A script-shaped scorer cannot satisfy this."""
    assert all(ord(ch) < 0x800 for ch in caption)      # 2-byte UTF-8 forms
    non_ascii = sum(1 for ch in caption if ord(ch) > 127)
    inflated = caption.encode("utf-8").decode(decl)
    assert _mojibake_damage(caption, decl) == 0
    assert _mojibake_damage(inflated, decl) == 2 * non_ascii

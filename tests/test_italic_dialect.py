"""Oracle export DIALECT: a declared italic is PARSED but NEVER PAINTED.

TRUTH MEASUREMENT (2026-08-08, the whole truth corpus)
------------------------------------------------------
Every Oracle-produced PDF in the truth corpus was inventoried span by span
with PyMuPDF (``span['font']`` + the italic flag bit) and cross-referenced
with its source's ``italic="yes"`` declarations.

  * 16 truth PDFs, all ``producer='Oracle PDF driver'`` /
    ``creator='Oracle12c AS Reports Services'``.
  * 142,831 non-blank text spans. **ZERO** carry the italic flag.
  * The PDFs' own font resource dictionaries only ever declare
    Helvetica (2,271 refs), Helvetica-Bold (2,266), Times-Roman (7),
    Times-Bold (7), Courier-Bold (3) and itcavantgardegothic (3).
    Not one ``*-Oblique`` / ``*-Italic`` face is even referenced.
  * The same driver DOES honour weight: 32,604 of those spans are bold.
    So this is specifically "italic is ignored", not "styling is ignored".

Per-object cross-reference (5 truth-paired sources declare italic="yes",
35 declared-italic objects between them; 16 carry static text that can be
located in the truth PDF):

    source              declared  located  rendered oblique
    -----------------   --------  -------  ----------------
    invoice letter            11        4                 0
    inspection summary         8        4                 0
    permit letter A           10        3                 0
    permit letter B            4        3                 0
    activity report            2        2                 0
    -----------------   --------  -------  ----------------
    TOTAL                     35       16                 0

e.g. an italic+bold total caption prints Helvetica-Bold; an italic
remittance note prints Helvetica.

A/B ON OUR OWN RENDER: before this rule the converter emitted
``<FontStyle>Italic</FontStyle>`` 44 times over the corpus and the MS
engine painted Helvetica-Oblique / Helvetica-BoldOblique on 37 spans over
5 truth-paired reports -- on exactly the strings the truth prints upright.
After: 0 emissions, 0 oblique spans, face mix Helvetica/Helvetica-Bold
matching the truth.

These tests lock BOTH halves: the declaration is still parsed (lossless
model, one flag to flip if a dialect that paints oblique ever shows up),
and no emitter paints it.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert                                  # noqa: E402
from converter.models import ORACLE_RENDERS_ITALIC             # noqa: E402
from converter.parsers.oracle_xml import parse_oracle_xml      # noqa: E402


# --------------------------------------------------------------------------
# Two fixtures, because the declared slant reaches the page down two very
# different roads:
#   _XML     tabular archetype -> table cells, declarative <conditionalFormat>
#            and a PL/SQL format trigger calling srw.set_font_style('ITALIC')
#   _DOC_XML positional document -> per-record textboxes + the ABSOLUTE
#            mockup path (the only mockup path that styles declared runs)
# Bold/size/face ride along so the tests prove the slant -- and only the
# slant -- is dropped.
# --------------------------------------------------------------------------
_XML = (
    '<report name="IT" DTDVersion="9.0.2.0.10">'
    '<data><dataSource name="Q_1">'
    '<select><![CDATA[SELECT amt, nm FROM t]]></select>'
    '<group name="G"><dataItem name="AMT" datatype="number"/>'
    '<dataItem name="NM" datatype="vchar2"/></group>'
    '</dataSource>'
    '<formulas><formula name="F_STYLE" datatype="boolean"><![CDATA['
    'function F_STYLE return boolean is begin '
    "if :AMT > 100 then srw.set_font_style('ITALIC'); "
    "srw.set_text_color('red'); end if; return (true); end;"
    ']]></formula></formulas>'
    '</data>'
    '<layout><section name="main"><body width="8" height="9">'
    '<text name="B_NOTE">'
    '<geometryInfo x="0.2" y="0.2" width="5" height="0.25"/>'
    '<textSegment><font face="Arial" size="12" bold="yes" italic="yes"/>'
    '<string><![CDATA[Standing Note]]></string></textSegment></text>'
    '<repeatingFrame name="R" source="G">'
    '<geometryInfo x="0" y="1.2" width="6" height="0.3"/>'
    '<field name="F_AMT" source="AMT" alignment="end">'
    '<font face="Times New Roman" size="11" bold="yes" italic="yes"/>'
    '<geometryInfo x="0" y="1.2" width="2" height="0.2"/>'
    '<advancedLayout formatTrigger="f_style"/>'
    '</field>'
    '<field name="F_NM" source="NM">'
    '<font face="Arial" size="10"/>'
    '<geometryInfo x="2.2" y="1.2" width="3" height="0.2"/>'
    '<generalLayout><conditionalFormat>'
    '<formatException label="(:AMT IS NULL)">'
    '<cond column="AMT" exception="11"/>'
    '<font bold="yes" italic="yes" textColor="red"/>'
    '</formatException>'
    '</conditionalFormat></generalLayout>'
    '</field>'
    '</repeatingFrame>'
    '</body></section></layout></report>'
).encode()

_DOC_XML = (
    '<report name="ITD" DTDVersion="9.0.2.0.10"><data>'
    '<dataSource name="Q_1">'
    '<select><![CDATA[SELECT recipient, addr FROM t]]></select>'
    '<group name="G"><dataItem name="RECIPIENT" datatype="vchar2"/>'
    '<dataItem name="ADDR" datatype="vchar2"/></group></dataSource></data>'
    '<layout><section name="main"><body width="8.5" height="11">'
    '<text name="B_NOTE">'
    '<geometryInfo x="1.0" y="0.5" width="5" height="0.3"/>'
    '<textSegment><font face="Arial" size="12" bold="yes" italic="yes"/>'
    '<string><![CDATA[Standing Note]]></string></textSegment></text>'
    '<field name="F1" source="RECIPIENT">'
    '<font face="Times New Roman" size="14" italic="yes"/>'
    '<geometryInfo x="1.0" y="1.0" width="5" height="0.3"/></field>'
    '<field name="F2" source="ADDR"><font face="Courier New" size="10"/>'
    '<geometryInfo x="1.0" y="1.5" width="5" height="0.3"/></field>'
    '</body></section></layout></report>'
).encode()


def _resolve(name):
    return f"Fields!{name}.Value"


# --------------------------------------------------------------------------
# 1. the declaration is still PARSED -- the model stays lossless
# --------------------------------------------------------------------------

def test_declared_italic_is_still_parsed_onto_the_model():
    """Suppression happens at PAINT time, not by throwing the declaration
    away: a future dialect that really does render oblique only has to flip
    models.ORACLE_RENDERS_ITALIC."""
    def _walk(g):
        for f in getattr(g, "fields", []) or []:
            yield f
        for sub in getattr(g, "children", []) or []:
            yield from _walk(sub)

    for src in (_XML, _DOC_XML):
        report = parse_oracle_xml(src)
        seen = {f.name: f for g in report.layout for f in _walk(g)}
        assert seen, "fixture produced no layout fields"
        italics = {n for n, f in seen.items() if getattr(f, "italic", False)}
        assert "B_NOTE" in italics, "declared italic lost in parsing"
        assert italics - {"B_NOTE"}, "declared italic lost on the data field"
        # the neighbouring declarations survive too (this is a slant-only
        # rule -- nothing else may be dropped on the way in)
        assert seen["B_NOTE"].bold is True
        assert seen["B_NOTE"].font_size == 12


def test_dialect_flag_is_off_by_measurement():
    """The corpus measurement is the reason this flag exists; if someone
    flips it, this test says out loud what the truth was."""
    assert ORACLE_RENDERS_ITALIC is False, (
        "16 Oracle truth PDFs / 142,831 spans contain ZERO italic-flagged "
        "spans and reference no *-Oblique font resource")


# --------------------------------------------------------------------------
# 2. no emitter paints it -- RDL body, per-record document, mockup
# --------------------------------------------------------------------------

def test_no_rdl_emitter_paints_a_declared_italic():
    for src in (_XML, _DOC_XML):
        rdl = convert(src)["rdl_xml"]
        assert "<FontStyle>Italic</FontStyle>" not in rdl
        # not as a conditional expression either (conditional format and
        # format-trigger styling patch FontStyle with an =IIf(...))
        assert not re.search(r"<FontStyle>[^<]*Italic", rdl)
        assert "Italic" not in rdl


def test_mockup_shows_the_upright_face_too():
    """The preview must show what the truth PRINTS, not what the source
    asked for -- otherwise the two surfaces disagree by construction."""
    mockup = convert(_DOC_XML)["mockup_html"]
    assert "position:absolute" in mockup, "fixture missed the styled path"
    assert "Standing Note" in mockup
    assert "font-style:italic" not in mockup


def test_everything_else_declared_still_carries():
    """The rule is slant-specific. If suppressing italic ever starts eating
    the neighbouring declarations, this goes red."""
    rdl = convert(_XML)["rdl_xml"]
    assert "<FontWeight>Bold</FontWeight>" in rdl, "declared bold dropped"
    assert "<FontSize>11pt</FontSize>" in rdl, "declared size dropped"
    assert "<FontFamily>Times New Roman</FontFamily>" in rdl, \
        "declared face dropped"
    doc = convert(_DOC_XML)["rdl_xml"]
    assert "<FontSize>14pt</FontSize>" in doc
    assert "<FontFamily>Courier New</FontFamily>" in doc


def test_conditional_format_drops_only_the_slant():
    from converter.translators.format_exception import (
        translate_conditional_format)

    out = translate_conditional_format([{
        "label": "(:AMT IS NULL)",
        "cond": {"column": "AMT", "exception": "11"},
        "font": {"bold": "yes", "italic": "yes", "textColor": "red"},
        "visual": {},
    }], _resolve)
    assert len(out) == 1
    _cond, styles = out[0]
    assert styles["FontWeight"] == "Bold"
    assert styles["Color"] == "#FF0000"
    assert "FontStyle" not in styles, (
        "a conditional italic must not be emitted at all -- not even as an "
        "always-Normal expression")
    # an explicit conditional italic="no" is a real UPRIGHT instruction and
    # still translates (it agrees with the truth)
    off = translate_conditional_format([{
        "label": "(:AMT IS NULL)",
        "cond": {"column": "AMT", "exception": "11"},
        "font": {"italic": "no", "bold": "yes"},
        "visual": {},
    }], _resolve)
    assert off and off[0][1].get("FontStyle") == "Normal"


def test_srw_set_font_style_is_recognized_but_unmapped():
    """A trigger that asks for an oblique face is still RECOGNIZED (its
    sibling srw calls keep translating) -- the slant alone is dropped."""
    from converter.translators.plsql_formula import (
        translate_format_trigger_style)

    r = translate_format_trigger_style(
        "begin if :AMT > 100 then srw.set_font_style('ITALIC'); "
        "srw.set_text_color('red'); end if; return (true); end;", _resolve)
    assert r is not None, "the sibling srw calls must still translate"
    _cond, styles = r
    assert styles.get("Color") == "Red"
    assert "FontStyle" not in styles


# --------------------------------------------------------------------------
# 3. mutation proof: the gate is load-bearing
# --------------------------------------------------------------------------

def test_flipping_the_dialect_flag_restores_the_slant(monkeypatch):
    """Proves these assertions are not vacuous: with the dialect flag
    forced on, every suppressed path emits again."""
    monkeypatch.setattr(
        "converter.generators.rdl.ORACLE_RENDERS_ITALIC", True)
    monkeypatch.setattr(
        "converter.preview.html_mockup.ORACLE_RENDERS_ITALIC", True)
    monkeypatch.setattr(
        "converter.translators.format_exception.ORACLE_RENDERS_ITALIC", True)

    assert "<FontStyle>Italic</FontStyle>" in convert(_XML)["rdl_xml"], (
        "RDL gate is not wired to the flag -- the suppression tests above "
        "would pass for the wrong reason")
    doc = convert(_DOC_XML)
    assert "<FontStyle>Italic</FontStyle>" in doc["rdl_xml"]
    assert "font-style:italic" in doc["mockup_html"], (
        "mockup gate is not wired to the flag")

    from converter.translators.format_exception import (
        translate_conditional_format)
    styles = translate_conditional_format([{
        "label": "(:AMT IS NULL)",
        "cond": {"column": "AMT", "exception": "11"},
        "font": {"italic": "yes"},
        "visual": {},
    }], _resolve)[0][1]
    assert styles.get("FontStyle") == "Italic"

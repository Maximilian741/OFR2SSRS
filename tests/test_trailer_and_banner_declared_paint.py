"""THE REPORT-END BLOCK AND THE HEADER BANNER PRINT DECLARED INK ONLY.

Three wild-corpus engine-render measured paint classes, one rule each:

  * a PLACED report summary with no declared caption prints its VALUE alone
    in its own declared box. Oracle puts just the number there; riding a
    fabricated "<Name>:  " label in the box forced a wrap past the declared
    one-line height, and the wrapped line crossed the underline the source
    declares exactly AT the box's bottom edge (four totals on one report,
    each cut by its own declared rule). The name-derived label survives
    only for a summary the layout gives no usable box -- a bare number
    floating below the body would say nothing;

  * a master frame can own BOTH the table's caption band and the report
    totals (captions above its repeating frame, summaries below it). The
    captions are table-HEADER flow -- the body walk prints them once at the
    top -- so releasing them as report-end "trailer literals" re-painted
    the whole caption band inside the total block (8 caption-on-caption
    pairs on one report). A text declared above the frame's own repeating
    band never joins the block; a caption declared below it still does;

  * a criteria-banner label can be a MULTI-LINE declared text. The banner
    box and the page-header band must hold every declared line -- sized at
    one line, the CanGrow'd box grew past the band's bottom edge and its
    last line printed straight through the body's first ink (the table's
    top border cut the line's glyph cores on every page). A one-line
    banner keeps the exact historical box and band.

Everything here is synthetic and structural: no report, column or label
from any real source appears in this file.
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert                       # noqa: E402

NS = ("{http://schemas.microsoft.com/sqlserver/reporting/"
      "2008/01/reportdefinition}")


def _q(tag: str) -> str:
    return NS + tag


def _num(el, tag):
    c = el.find(_q(tag))
    if c is None or not (c.text or "").strip():
        return None
    try:
        return float(c.text.replace("in", "").strip())
    except ValueError:
        return None


def _grand_totals(rdl: str):
    """{name: (element, joined_value_text)} of every trailer line box."""
    root = ET.fromstring(rdl.encode("utf-8"))
    out = {}
    for el in root.iter(_q("Textbox")):
        nm = el.get("Name") or ""
        if nm.startswith("Tb_GrandTotal_"):
            out[nm] = (el, "".join(v.text or ""
                                   for v in el.iter(_q("Value"))))
    return out


# ---------------------------------------------------------------------------
# fixture -- a master frame owning caption band + repeating band + totals
# ---------------------------------------------------------------------------

def _master_trailer_xml(tot_width: float = 1.0) -> bytes:
    # A master-detail record report (the wild defect shape): the master
    # frame owns the table's caption band ABOVE its repeating band and the
    # placed report total (plus a closing band literal) BELOW it.
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<report name="TRAILPAINT" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q1">
      <select><![CDATA[SELECT MCOL, ALPHA, NUMBERISH FROM T]]></select>
      <group name="G_M">
        <dataItem name="MCOL" datatype="vchar2" width="30"
         defaultLabel="Mcol">
          <dataDescriptor expression="MCOL" order="1" width="30"/>
        </dataItem>
      </group>
      <group name="G_D">
        <dataItem name="ALPHA" datatype="vchar2" width="30"
         defaultLabel="Alpha">
          <dataDescriptor expression="ALPHA" order="2" width="30"/>
        </dataItem>
        <dataItem name="NUMBERISH" oracleDatatype="number" width="10"
         defaultLabel="Numberish">
          <dataDescriptor expression="NUMBERISH" order="3" width="10"/>
        </dataItem>
      </group>
    </dataSource>
    <summary name="CS_TALLYX" source="NUMBERISH" function="sum" width="20"
     reset="report" compute="report" columnFlags="8"/>
  </data>
  <layout>
  <section name="main">
    <body height="9.6">
      <repeatingFrame name="R_M" source="G_M" printDirection="down">
        <geometryInfo x="0.0" y="0.0" width="7.5" height="1.9"/>
        <field name="F_M" source="MCOL">
          <font face="Arial" size="10" bold="yes"/>
          <geometryInfo x="0.05" y="0.05" width="3.0" height="0.19"/>
        </field>
        <frame name="M_G">
          <geometryInfo x="0.0" y="0.3" width="7.5" height="1.5"/>
          <text name="B_CAPTOP"><textSettings justify="start"/>
            <geometryInfo x="0.1" y="0.35" width="2.0" height="0.2"/>
            <textSegment><font face="Arial" size="9"/>
              <string><![CDATA[Colhead Alpha]]></string></textSegment></text>
          <repeatingFrame name="R_D" source="G_D" printDirection="down">
            <geometryInfo x="0.0" y="0.6" width="7.5" height="0.3"/>
            <field name="F_A" source="ALPHA">
              <font face="Arial" size="9"/>
              <geometryInfo x="0.1" y="0.65" width="3.0" height="0.19"/>
            </field>
            <field name="F_N" source="NUMBERISH">
              <font face="Arial" size="9"/>
              <geometryInfo x="4.0" y="0.65" width="1.0" height="0.19"/>
            </field>
          </repeatingFrame>
          <field name="F_TOT" source="CS_TALLYX" alignment="end">
            <font face="Arial" size="9" bold="yes"/>
            <geometryInfo x="4.0" y="1.1" width="{tot_width}"
             height="0.19"/>
          </field>
          <text name="B_CAPBOT"><textSettings justify="start"/>
            <geometryInfo x="0.1" y="1.35" width="2.0" height="0.19"/>
            <textSegment><font face="Arial" size="9"/>
              <string><![CDATA[Closing Words]]></string></textSegment></text>
        </frame>
      </repeatingFrame>
    </body>
  </section>
  </layout>
</report>
'''.encode("utf-8")


@pytest.fixture(scope="module")
def master_rdl():
    return convert(_master_trailer_xml())["rdl_xml"]


def test_placed_total_prints_value_alone(master_rdl):
    gts = _grand_totals(master_rdl)
    vals = [v for _el, v in gts.values() if "Fields!NUMBERISH" in v]
    assert vals, "the placed report total must emit"
    for v in vals:
        assert v.lstrip("=").startswith("Sum("), v
        assert "Tallyx" not in v, (
            "a placed summary's declared box carries the value alone", v)


def test_unplaceable_total_keeps_its_synthesized_label():
    # The same summary squeezed into a DEGENERATE declared box (no usable
    # geometry): the label-drop gate must NOT fire -- a bare number floating
    # below the body says nothing, so the name-derived wording survives.
    rdl = convert(_master_trailer_xml(tot_width=0.02))["rdl_xml"]
    vals = [v for _el, v in _grand_totals(rdl).values()
            if "Fields!NUMBERISH" in v]
    assert vals, "the summary must still emit"
    assert any('"Tallyx:' in v for v in vals), vals


def test_caption_band_above_repeating_band_never_joins_the_trailer(
        master_rdl):
    gts = _grand_totals(master_rdl)
    assert not any("Colhead Alpha" in v for _el, v in gts.values()), (
        "a caption declared above the frame's repeating band is table-"
        "header flow, not report-end content")


def test_caption_below_repeating_band_still_prints_in_the_trailer(
        master_rdl):
    gts = _grand_totals(master_rdl)
    assert any("Closing Words" in v for _el, v in gts.values()), (
        "a band literal declared below the repeating band is report-end "
        "content and must keep printing")


# ---------------------------------------------------------------------------
# fixture -- the criteria banner, one-line and multi-line labels
# ---------------------------------------------------------------------------

def _banner_xml(lines: int) -> bytes:
    label = "\\n".join(f"CRIT LINE {i}" for i in range(lines))
    label = label.replace("\\n", "&#10;")
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<report name="BANNERPAINT" DTDVersion="9.0.2.0.10">
  <data>
    <userParameter name="P_FLT" datatype="character" width="20"
     initialValue=""/>
    <dataSource name="Q_1">
      <select><![CDATA[SELECT ALPHA FROM T]]></select>
      <group name="G_1">
        <dataItem name="ALPHA" datatype="vchar2" width="30"
         defaultLabel="Alpha">
          <dataDescriptor expression="ALPHA" order="1" width="30"/>
        </dataItem>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="main" width="8.5" height="11.0">
    <body width="8.5" height="9.5"><location x="0.0" y="0.0"/>
      <text name="B_TITLE">
        <textSettings justify="center"/>
        <geometryInfo x="2.0" y="0.05" width="4.5" height="0.25"/>
        <textSegment><font face="Arial" size="12" bold="yes"/>
          <string><![CDATA[SYNTHETIC HEADING WORDS]]></string></textSegment>
      </text>
      <line name="B_RULE" arrow="none">
        <geometryInfo x="0.1" y="1.1" width="8.2" height="0.0"/>
        <visualSettings linePattern="solid"/>
      </line>
      <text name="B_CRIT">
        <textSettings justify="center"/>
        <geometryInfo x="2.0" y="0.5" width="4.5"
         height="{0.2 * lines}"/>
        <textSegment><font face="Arial" size="9"/>
          <string>{label}</string></textSegment>
      </text>
      <field name="F_FLT" source="P_FLT">
        <font face="Arial" size="9"/>
        <geometryInfo x="6.6" y="0.5" width="1.2" height="0.19"/>
      </field>
      <repeatingFrame name="R_1" source="G_1" printDirection="down">
        <geometryInfo x="0.0" y="1.3" width="8.4" height="0.3"/>
        <field name="F_A" source="ALPHA">
          <font face="Arial" size="9"/>
          <geometryInfo x="0.1" y="1.35" width="2.0" height="0.19"/>
        </field>
      </repeatingFrame>
    </body>
  </section>
  </layout>
</report>
'''.encode("utf-8")


def _banner_geo(rdl: str):
    """(banner_el, rule_el, header_height) or (None, None, None)."""
    root = ET.fromstring(rdl.encode("utf-8"))
    banner = rule = hdr_h = None
    for el in root.iter(_q("Textbox")):
        if el.get("Name") == "Tb_BannerCriteria":
            banner = el
    for el in root.iter(_q("Rectangle")):
        if el.get("Name") == "Rect_BannerRule":
            rule = el
    for ph in root.iter(_q("PageHeader")):
        hdr_h = _num(ph, "Height")
    return banner, rule, hdr_h


def test_multiline_banner_box_band_and_rule_hold_every_line():
    rdl = convert(_banner_xml(3))["rdl_xml"]
    banner, rule, hdr_h = _banner_geo(rdl)
    if banner is None:
        pytest.skip("banner detector did not engage on the fixture")
    bh = _num(banner, "Height")
    bt = _num(banner, "Top")
    assert bh is not None and bh >= 0.20 + 2 * 0.17 - 1e-6, (
        "the box must hold all three declared label lines", bh)
    assert hdr_h is not None and hdr_h >= bt + bh + 0.05 - 1e-6, (
        "the band must reach past the banner's own bottom", hdr_h, bt, bh)
    if rule is not None:
        rt = _num(rule, "Top")
        assert rt is not None and rt >= bt + bh + 0.03 - 1e-6, (
            "the capping rule sits below the grown banner", rt, bt, bh)


def test_one_line_banner_keeps_its_historical_shape():
    rdl = convert(_banner_xml(1))["rdl_xml"]
    banner, rule, _hdr_h = _banner_geo(rdl)
    if banner is None:
        pytest.skip("banner detector did not engage on the fixture")
    assert _num(banner, "Height") == pytest.approx(0.20, abs=1e-6)
    if rule is not None:
        assert _num(rule, "Top") == pytest.approx(
            _num(banner, "Top") + 0.24, abs=1e-6)

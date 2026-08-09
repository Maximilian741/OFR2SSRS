"""The sheet's side margins come from the DECLARATION, not from a default.

Oracle places the body rectangle on the paper with ``<section><body>
<location x= y=/>``; body object coordinates restart at that origin, so every
body object prints at ``location.x + its own declared x``. Emitting a fixed
default left margin instead put the whole body a quarter inch off on every
report whose declaration implies a different margin.

Three declaration shapes, all of which occur in real exports:

* ``<location x="..." y="..."/>``  -> that x IS the left margin;
* ``<location y="..."/>`` (x omitted, because it is Oracle's default) and no
  ``<location>`` at all -> the default body inset;
* ``<location x="0.0" .../>`` -> a real full-bleed body, which must NOT read
  the same as "x omitted".

Plus the two sizing invariants the page geometry has to satisfy in BOTH
directions: the body must CONTAIN its widest item (a body narrower than its
own content pushes that content onto companion pages), and body + margins
must FIT strictly inside the sheet (equality is the SSRS blank-page-after-
every-page defect).
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert                                # noqa: E402
from converter.parsers.oracle_xml import parse_oracle_xml    # noqa: E402

RD = "{http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition}"


def _xml(location: str, sec_w: str = "8.50000", body_w: str = "7.00000",
         frame_w: float = 6.90, first_x: float = 0.10) -> bytes:
    """A one-frame tabular report whose body geometry is fully declared."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<report name="UNIT_ORIGIN" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_1">
      <select><![CDATA[SELECT ALPHA, BETA FROM UNIT_TAB]]></select>
      <group name="G_1">
        <dataItem name="ALPHA" datatype="vchar2" width="30"
         defaultLabel="Alpha">
          <dataDescriptor expression="ALPHA" order="1" width="30"/>
        </dataItem>
        <dataItem name="BETA" datatype="vchar2" width="30"
         defaultLabel="Beta">
          <dataDescriptor expression="BETA" order="2" width="30"/>
        </dataItem>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="main" width="{sec_w}" height="11.00000">
    <body width="{body_w}" height="9.50000">
      {location}
      <repeatingFrame name="R_1" source="G_1" printDirection="down">
        <geometryInfo x="0.00000" y="0.00000" width="{frame_w:.5f}"
         height="0.30000"/>
        <text name="B_1">
          <textSettings spacing="single"/>
          <geometryInfo x="{first_x:.5f}" y="0.05000" width="2.00000"
           height="0.19000"/>
          <textSegment><font face="Arial" size="10"/>
            <string><![CDATA[Column One]]></string>
          </textSegment>
        </text>
        <field name="F_ALPHA" source="ALPHA">
          <font face="Arial" size="10"/>
          <geometryInfo x="{first_x + 2.2:.5f}" y="0.05000" width="2.00000"
           height="0.19000"/>
        </field>
        <field name="F_BETA" source="BETA">
          <font face="Arial" size="10"/>
          <geometryInfo x="{frame_w - 2.0:.5f}" y="0.05000" width="2.00000"
           height="0.19000"/>
        </field>
      </repeatingFrame>
    </body>
  </section>
  </layout>
</report>
""".encode("utf-8")


def _per_record_xml(location: str) -> bytes:
    """One record per page (the letter / certificate shape): a narrow record
    frame inside a wider body."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<report name="UNIT_RECORD" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_1">
      <select><![CDATA[SELECT ALPHA, BETA FROM UNIT_TAB]]></select>
      <group name="G_1">
        <dataItem name="ALPHA" datatype="vchar2" width="30"
         defaultLabel="Alpha">
          <dataDescriptor expression="ALPHA" order="1" width="30"/>
        </dataItem>
        <dataItem name="BETA" datatype="vchar2" width="30"
         defaultLabel="Beta">
          <dataDescriptor expression="BETA" order="2" width="30"/>
        </dataItem>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="main" width="8.50000" height="11.00000">
    <body width="6.90000" height="9.75000">
      {location}
      <frame name="M_REC">
        <geometryInfo x="0.00000" y="0.00000" width="5.00000"
         height="4.00000"/>
        <repeatingFrame name="R_1" source="G_1" printDirection="down"
         maxRecordsPerPage="1" minWidowRecords="1">
          <geometryInfo x="0.00000" y="0.00000" width="5.00000"
           height="3.90000"/>
          <text name="B_1">
            <textSettings spacing="single"/>
            <geometryInfo x="0.10000" y="0.20000" width="4.00000"
             height="0.19000"/>
            <textSegment><font face="Arial" size="10"/>
              <string><![CDATA[Notice of Record]]></string>
            </textSegment>
          </text>
          <field name="F_ALPHA" source="ALPHA">
            <font face="Arial" size="10"/>
            <geometryInfo x="0.10000" y="0.60000" width="4.00000"
             height="0.19000"/>
          </field>
          <field name="F_BETA" source="BETA">
            <font face="Arial" size="10"/>
            <geometryInfo x="0.10000" y="1.00000" width="4.00000"
             height="0.19000"/>
          </field>
        </repeatingFrame>
      </frame>
    </body>
  </section>
  </layout>
</report>
""".encode("utf-8")


def _geometry(rdl: str):
    root = ET.fromstring(rdl)
    page = root.find(RD + "Page")

    def _f(el, tag, default=0.0):
        try:
            return float((el.findtext(RD + tag) or "").replace("in", ""))
        except (AttributeError, ValueError):
            return default

    return {
        "page_w": _f(page, "PageWidth", 8.5),
        "left": _f(page, "LeftMargin"),
        "right": _f(page, "RightMargin"),
        "width": _f(root, "Width"),
        "root": root,
    }


def _widest_item(root) -> float:
    """Widest ABSOLUTE right edge of any body report item, in inches."""
    body = root.find(RD + "Body")

    def _f(el, tag):
        try:
            return float((el.findtext(RD + tag) or "0").replace("in", ""))
        except ValueError:
            return 0.0

    widest = 0.0

    def walk(el, left):
        nonlocal widest
        if el.tag.split("}")[-1] in ("Rectangle", "Textbox", "Tablix", "Image",
                                     "Line", "Subreport", "List", "Chart"):
            left += _f(el, "Left")
            widest = max(widest, left + _f(el, "Width"))
        for c in el:
            walk(c, left)

    if body is not None:
        walk(body, 0.0)
    return widest


# --------------------------------------------------------------- parser ---

def test_parser_records_only_the_axes_the_export_declares():
    both = parse_oracle_xml(_xml('<location x="0.75000" y="0.50000"/>'))
    y_only = parse_oracle_xml(_xml('<location y="0.50000"/>'))
    none_at_all = parse_oracle_xml(_xml(""))

    def main_of(rep):
        return next(g for g in rep.layout if (g.kind or "") == "section_main")

    assert main_of(both).body_location == (0.75, 0.5)
    # x omitted must stay OMITTED -- reading it as 0.0 would silently claim a
    # full-bleed body on every report that only declares its top margin.
    assert main_of(y_only).body_location[0] is None
    assert main_of(y_only).body_location[1] == 0.5
    assert not main_of(none_at_all).body_location


# ---------------------------------------------------------- left margin ---

def test_declared_body_origin_becomes_the_left_margin():
    g = _geometry(convert(_xml('<location x="0.75000" y="0.50000"/>'))["rdl_xml"])
    assert abs(g["left"] - 0.75) < 0.011, (
        f"LeftMargin {g['left']}in ignores the declared body origin 0.75in — "
        f"every body object prints that far off its true paper x")


def test_declared_origin_of_zero_is_a_real_full_bleed_margin():
    g = _geometry(convert(_xml('<location x="0.00000" y="0.43750"/>'))["rdl_xml"])
    assert g["left"] < 0.011, (
        f"LeftMargin {g['left']}in on a body declared flush to the sheet edge")


def test_undeclared_origin_falls_back_to_the_default_body_inset():
    for loc in ('<location y="0.50000"/>', ""):
        g = _geometry(convert(_xml(loc))["rdl_xml"])
        assert abs(g["left"] - 0.5) < 0.011, (
            f"LeftMargin {g['left']}in for {loc!r}: a source that declares no "
            f"origin prints at Oracle's default body inset")


def test_margin_never_pushes_declared_content_off_the_sheet():
    """An origin so far right that the declared body would hang off the paper
    is clamped — the margin may never cost content."""
    g = _geometry(convert(_xml('<location x="3.00000" y="0.50000"/>'))["rdl_xml"])
    assert g["left"] + 7.0 <= g["page_w"] + 0.001, (
        f"left {g['left']} + declared body 7.0 overflows sheet {g['page_w']}")


# --------------------------------------------------- body-vs-page sizing ---

def _fit_and_contain(rdl: str, label: str):
    g = _geometry(rdl)
    widest = _widest_item(g["root"])
    assert g["width"] + 0.011 >= widest, (
        f"{label}: body Width {g['width']}in does not contain its widest item "
        f"({widest}in) — the overflow paginates onto companion pages")
    assert g["width"] + g["left"] + g["right"] < g["page_w"] - 0.019, (
        f"{label}: body {g['width']} + margins {g['left']}+{g['right']} is not "
        f"strictly under PageWidth {g['page_w']} -> SSRS blank-page cadence")
    return g


def test_body_contains_its_widest_item_and_fits_the_sheet():
    # portrait, offset body
    _fit_and_contain(convert(_xml('<location x="0.75000" y="0.50000"/>'))["rdl_xml"],
                     "portrait offset body")
    # wide landscape sheet with a wide declared body
    _fit_and_contain(
        convert(_xml('<location x="0.54053" y="0.50000"/>', sec_w="14.00000",
                     body_w="12.20000", frame_w=12.10))["rdl_xml"],
        "landscape wide body")
    # full-bleed body
    _fit_and_contain(convert(_xml('<location x="0.00000" y="0.43750"/>',
                                  body_w="8.50000", frame_w=8.40))["rdl_xml"],
                     "full-bleed body")


def _leftmost_item(root) -> float:
    """Leftmost absolute x of a CONTENT item (a textbox / image), accumulating
    the Left of every container it sits in. Containers alone are not enough:
    an inset applied to the record rectangle INSIDE a full-width region is
    exactly the double-apply this guards."""
    body = root.find(RD + "Body")
    lefts = []

    def walk(el, left):
        tag = el.tag.split("}")[-1]
        if tag in ("Rectangle", "Textbox", "Tablix", "Image", "Line",
                   "Subreport", "List", "Chart"):
            try:
                left += float((el.findtext(RD + "Left") or "0").replace("in", ""))
            except ValueError:
                pass
            if tag in ("Textbox", "Image"):
                lefts.append(left)
        for c in el:
            walk(c, left)

    if body is not None:
        walk(body, 0.0)
    return min(lefts) if lefts else 0.0


def test_a_declared_origin_is_not_applied_twice():
    """Once the page carries the declared origin, a body builder must not ALSO
    indent by a guessed margin — that prints the whole body one margin right of
    the truth. The guessed inset stays only while the source declares nothing."""
    declared = ET.fromstring(
        convert(_xml('<location x="0.75000" y="0.50000"/>'))["rdl_xml"])
    assert _leftmost_item(declared) < 0.08, (
        f"body content indented {_leftmost_item(declared)}in INSIDE a body "
        f"whose origin is already the declared 0.75in — applied twice")
    undeclared = ET.fromstring(convert(_xml(""))["rdl_xml"])
    assert _leftmost_item(undeclared) > 0.08, (
        "with nothing declared the historical inset must be kept")


def test_per_record_frame_keeps_its_declared_x_when_the_origin_is_declared():
    """The per-record builder centres a record inside the body while it has to
    guess where the body sits. A declared origin already says where the record
    prints, so centring it again shifts the record a second time."""
    rdl = convert(_per_record_xml('<location x="0.85000" y="0.56250"/>'))["rdl_xml"]
    root = ET.fromstring(rdl)
    # The record's leftmost content is declared at x=0.10 inside the body;
    # centring a 5.0in record inside the ~6.9in body would add ~0.95in.
    assert _leftmost_item(root) < 0.10 + 0.10, (
        f"record block re-centred to {_leftmost_item(root)}in inside a body "
        f"whose declared origin already positions it")


def test_body_width_grows_to_hold_a_region_the_builder_left_outside():
    """The observed defect in the other direction: a body whose <Width> kept a
    portrait default while its widest region reached far past it. Everything
    beyond the body edge paginates onto companion pages, so the fit pass has to
    GROW the body (up to what the sheet holds) rather than leave it behind."""
    from converter.generators import rdl as R

    root = ET.Element(R._q("Report"))
    body = ET.SubElement(root, R._q("Body"))
    items = ET.SubElement(body, R._q("ReportItems"))
    tx = ET.SubElement(items, R._q("Tablix"))
    tx.set("Name", "Tablix_Wide")
    ET.SubElement(tx, R._q("Left")).text = "0.00in"
    ET.SubElement(tx, R._q("Width")).text = "12.18in"
    tbody = ET.SubElement(tx, R._q("TablixBody"))
    cols = ET.SubElement(tbody, R._q("TablixColumns"))
    for _ in range(6):
        ET.SubElement(ET.SubElement(cols, R._q("TablixColumn")),
                      R._q("Width")).text = "2.03in"
    ET.SubElement(root, R._q("Width")).text = "7.50in"
    page = ET.SubElement(root, R._q("Page"))
    ET.SubElement(page, R._q("PageWidth")).text = "14.00in"
    ET.SubElement(page, R._q("LeftMargin")).text = "0.54in"
    ET.SubElement(page, R._q("RightMargin")).text = "0.54in"

    R._fit_body_to_page(root)

    width = float(root.findtext(R._q("Width")).replace("in", ""))
    right = float(page.findtext(R._q("RightMargin")).replace("in", ""))
    assert width >= 12.18 - 0.011, (
        f"body Width stayed {width}in while its region reaches 12.18in")
    assert width + 0.54 + right < 14.0 - 0.019, (
        f"body {width} + margins 0.54+{right} not strictly under the sheet")


def test_right_margin_is_the_residual_not_a_mirror_of_the_left():
    """Oracle's side margins are asymmetric whenever the body is not centred;
    mirroring the left margin either clips the body or overflows the sheet."""
    g = _geometry(convert(_xml('<location x="0.54053" y="0.50000"/>',
                               sec_w="14.00000", body_w="12.20000",
                               frame_w=12.10))["rdl_xml"])
    assert abs(g["right"] - (g["page_w"] - g["left"] - g["width"])) < 0.06, (
        f"RightMargin {g['right']}in is not the residual of PageWidth "
        f"{g['page_w']} - left {g['left']} - body {g['width']}")

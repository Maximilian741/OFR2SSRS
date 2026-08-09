"""Report-end trailer wording must PRINT, and a DECLARED cover heading must
keep its declared face.

Two general defects, both render-verified on a production report:

1. The report-end trailer stack (the ``Tb_GrandTotal_*`` family emitted by
   ``_ensure_summary_totals_emitted``) hardcoded a 5.0in x 0.22in
   ``CanGrow=false`` box. A trailer line carrying long DECLARED wording
   therefore WRAPPED inside a one-line box and painted clipped glyph
   slivers of the second line. Each line must now be sized to its own
   wording -- bounded by the printable right edge -- or be allowed to grow.

2. The cover's section heading was emitted at a hardcoded weight/size, so a
   source that declares that heading bold + italic + UNDERLINED at 12pt
   printed as plain bold. The declared face/size/weight/slant/underline/
   colour/justification must carry through.
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter.generators.rdl import (  # noqa: E402
    _afm_text_width,
    _build_cover_page,
    _declared_heading_style,
    _ensure_summary_totals_emitted,
    _q,
    _sub,
    _RDL_DEFAULT_FONT_IS_SANS,
)
from converter.models import (  # noqa: E402
    DataItem,
    DataQuery,
    FormulaColumn,
    LayoutField,
    LayoutGroup,
    ParsedReport,
)
from converter.parsers.oracle_xml import parse_oracle_xml  # noqa: E402

# A long single declared caption in the report-end trailer band: far wider
# than the historical 5.0in stack width at 9pt bold.
_LONG_TRAILER = ("Widget Maintenance Program Detail Report "
                 "Regional Department of Facility Quality "
                 "Widget Maintenance Program")

_XML = ("""<?xml version="1.0" encoding="UTF-8"?>
<report name="WIDGET_DETAIL" DTDVersion="9.0.2.0.10">
  <data>
    <userParameter name="PARM_REGION" datatype="character" width="40"
     defaultWidth="0" defaultHeight="0"/>
    <dataSource name="Q_1">
      <select canParse="no"><![CDATA[SELECT WID, WNAME, WREGION FROM WIDGETS]]></select>
      <group name="G_MAIN">
        <dataItem name="WID" oracleDatatype="number" defaultLabel="Widget"><dataDescriptor expression="WID"/></dataItem>
        <dataItem name="WNAME" datatype="vchar2" defaultLabel="Name" breakOrder="none"><dataDescriptor expression="WNAME"/></dataItem>
        <dataItem name="WREGION" datatype="vchar2" defaultLabel="Region" breakOrder="none"><dataDescriptor expression="WREGION"/></dataItem>
      </group>
    </dataSource>
    <summary name="CountWIDPerReport" source="WID" function="count"
             reset="report" compute="report" defaultLabel="Overall Total:">
      <displayInfo x="4.5" y="1.27" width="1.5" height="0.2"/>
    </summary>
  </data>
  <layout>
    <section name="header" orientation="portrait">
      <body width="7.5" height="6"><location x="0.5" y="0.25"/>
        <text name="B_HDG" minWidowLines="1">
          <textSettings justify="center" spacing="0"/>
          <geometryInfo x="2.43" y="2.50" width="2.87" height="0.25"/>
          <textSegment><font face="Arial" size="14" bold="yes" italic="yes" underline="yes"/>
            <string><![CDATA[Report Parameters]]></string></textSegment>
        </text>
        <text name="B_R1" minWidowLines="1">
          <textSettings justify="end" spacing="0"/>
          <geometryInfo x="1.50" y="2.90" width="2.00" height="0.18"/>
          <textSegment><font face="Arial" size="10" bold="yes"/>
            <string><![CDATA[Region:]]></string></textSegment>
        </text>
        <field name="F_P_REGION" source="PARM_REGION" minWidowLines="1">
          <font face="Arial" size="10" bold="yes"/>
          <geometryInfo x="3.60" y="2.90" width="3.10" height="0.18"/>
        </field>
      </body>
    </section>
    <section name="main" orientation="portrait">
      <body width="7.5" height="9"><location x="0.5" y="0.25"/>
        <repeatingFrame name="R_MAIN" source="G_MAIN" printDirection="down">
          <geometryInfo x="0" y="0" width="7.5" height="0.3"/>
          <field name="F_WID" source="WID"><font face="Arial" size="10"/>
            <geometryInfo x="0" y="0" width="1.5" height="0.18"/></field>
          <field name="F_WNAME" source="WNAME"><font face="Arial" size="10"/>
            <geometryInfo x="1.6" y="0" width="3.0" height="0.18"/></field>
          <field name="F_WREGION" source="WREGION"><font face="Arial" size="10"/>
            <geometryInfo x="4.7" y="0" width="2.0" height="0.18"/></field>
        </repeatingFrame>
        <frame name="M_TRAILER">
          <geometryInfo x="0" y="5.0" width="7.5" height="0.8"/>
          <text name="B_TITLE" minWidowLines="1">
            <textSettings justify="start" spacing="0"/>
            <geometryInfo x="0.0" y="5.05" width="4.33" height="0.58"/>
            <textSegment><font face="Verdana" size="11" bold="yes"/>
              <string><![CDATA[%%LONG%%]]></string></textSegment>
          </text>
          <text name="B_TOTLBL" minWidowLines="1">
            <textSettings justify="end" spacing="0"/>
            <geometryInfo x="0.0" y="5.70" width="1.62" height="0.18"/>
            <textSegment><font face="Arial" size="10" bold="yes"/>
              <string><![CDATA[Total of All Records:]]></string></textSegment>
          </text>
          <field name="F_TOT" source="CountWIDPerReport" minWidowLines="1">
            <font face="Arial" size="10" bold="yes"/>
            <geometryInfo x="1.80" y="5.70" width="1.50" height="0.19"/>
          </field>
        </frame>
      </body>
    </section>
  </layout>
  <userParameter name="PARM_REGION" datatype="character" width="40"/>
</report>""").replace("%%LONG%%", _LONG_TRAILER).encode("utf-8")


def _trailer_root():
    """A minimal RDL skeleton (letter paper, 0.25in side margins, one body item
    to anchor the stack below) plus the ParsedReport that declares a report-
    scoped grand total and a LONG trailer caption beside it in the layout."""
    root = ET.Element(_q("Report"))
    page = _sub(root, "Page")
    _sub(page, "PageWidth", "8.50in")
    _sub(page, "PageHeight", "11.00in")
    _sub(page, "LeftMargin", "0.25in")
    _sub(page, "RightMargin", "0.25in")
    body = _sub(root, "Body")
    ri = _sub(body, "ReportItems")
    anchor = _sub(ri, "Tablix")
    anchor.set("Name", "Tablix_Body")
    _sub(anchor, "Top", "0.20in")
    _sub(anchor, "Left", "0.10in")
    _sub(anchor, "Width", "7.00in")
    _sub(anchor, "Height", "4.00in")
    _sub(body, "Height", "5.00in")
    _sub(root, "Width", "7.50in")

    summary = FormulaColumn(name="CountWIDPerReport", agg_function="count",
                            agg_source="WID", agg_scope="report")
    query = DataQuery(name="Q_1",
                      items=[DataItem(name="WID"), DataItem(name="WNAME")])
    trailer = LayoutGroup(name="M_TRAILER", kind="frame", fields=[
        LayoutField(name="B_TITLE", kind="text", text=_LONG_TRAILER,
                    x=0.0, y=5.05, width=4.33, height=0.58),
        LayoutField(name="B_TOTLBL", kind="text", text="Total of All Records:",
                    x=0.0, y=5.70, width=1.62, height=0.18),
        LayoutField(name="F_TOT", kind="field", source="CountWIDPerReport",
                    x=1.80, y=5.70, width=1.50, height=0.19),
    ])
    section = LayoutGroup(name="section_main", kind="section_main",
                          children=[trailer])
    report = ParsedReport(name="WIDGET_DETAIL", queries=[query],
                          formulas=[summary], layout=[section])
    return root, report


def _trailer_boxes(root):
    """(name, value, cangrow, top, left, width, height) per emitted line."""
    out = []
    for tb in root.iter(_q("Textbox")):
        if not (tb.get("Name") or "").startswith("Tb_GrandTotal_"):
            continue

        def _f(tag, el=tb):
            try:
                return float((el.findtext(_q(tag)) or "0").replace("in", ""))
            except ValueError:
                return 0.0
        val = next((v.text or "" for v in tb.iter(_q("Value"))), "")
        out.append((tb.get("Name"), val,
                    (tb.findtext(_q("CanGrow")) or "true") == "true",
                    _f("Top"), _f("Left"), _f("Width"), _f("Height")))
    return sorted(out, key=lambda b: b[3])


# ---------------------------------------------------------------------------
# 1. trailer wording
# ---------------------------------------------------------------------------

def test_trailer_line_fits_its_declared_wording_or_grows():
    root, report = _trailer_root()
    _ensure_summary_totals_emitted(root, report)
    boxes = _trailer_boxes(root)
    assert boxes, "no report-end trailer emitted"
    long_box = [b for b in boxes if "Widget Maintenance Program Detail" in b[1]]
    assert long_box, "declared trailer caption never reached the trailer stack"
    _n, val, grow, _t, left, width, _h = long_box[0]
    text = val[2:-1] if val.startswith('="') and val.endswith('"') else val
    need = _afm_text_width(text, 9.0, True, _RDL_DEFAULT_FONT_IS_SANS) + 4 / 72.0
    # Either the box holds the wording on one line, or it is allowed to grow.
    # A fixed 0.22in box narrower than the wording clips it (the defect).
    assert grow or width + 1e-6 >= need, (
        f"trailer box is {width}in wide, wording needs {need:.2f}in, "
        f"and CanGrow is false -> the second line paints clipped slivers")
    # Widening must never push a body item past the report's own width, which
    # paginates a near-blank companion page after every content page.
    assert left + width <= 7.50 + 1e-6


def test_trailer_lines_never_overlap_each_other():
    root, report = _trailer_root()
    _ensure_summary_totals_emitted(root, report)
    boxes = _trailer_boxes(root)
    assert len(boxes) >= 2, "expected a multi-line trailer stack"
    for a, b in zip(boxes, boxes[1:]):
        assert a[3] + a[6] <= b[3] + 1e-6, (
            f"{a[0]} (top {a[3]} + h {a[6]}) collides with {b[0]} top {b[3]}")


def test_short_trailer_line_keeps_the_nominal_stack_width():
    """Sizing is a CLIP fix, not a re-layout: a line that already fits keeps
    the historical 5.0in x 0.22in box so clean reports stay byte-identical."""
    root, report = _trailer_root()
    _ensure_summary_totals_emitted(root, report)
    short = [b for b in _trailer_boxes(root)
             if "Total of All Records" in b[1]]
    assert short, "labeled grand total missing"
    assert abs(short[0][5] - 5.0) < 1e-6
    assert abs(short[0][6] - 0.22) < 1e-6
    assert short[0][2] is False


def test_trailer_never_widens_past_the_printable_edge():
    """A caption too long even for the full printable span must GROW, never
    push its right edge off the page (that paginates blank companion pages)."""
    root, report = _trailer_root()
    for f in report.layout[0].children[0].fields:
        if f.name == "B_TITLE":
            f.text = _LONG_TRAILER * 4
    _ensure_summary_totals_emitted(root, report)
    huge = [b for b in _trailer_boxes(root)
            if "Widget Maintenance Program Detail" in b[1]]
    assert huge, "declared trailer caption never reached the trailer stack"
    _n, _v, grow, _t, left, width, height = huge[0]
    assert left + width <= 7.50 + 1e-6
    assert grow, "an unfittable caption must be allowed to grow, not clip"
    assert height > 0.22, "a growing caption needs room for its extra lines"


# ---------------------------------------------------------------------------
# 2. declared cover heading
# ---------------------------------------------------------------------------

def test_declared_heading_style_reads_the_sources_own_font():
    report = parse_oracle_xml(_XML)
    st = _declared_heading_style(report, "Report Parameters")
    assert st.get("font_size") == "14pt"
    assert st.get("bold") is True
    assert st.get("italic") is True
    assert st.get("underline") is True
    assert st.get("font_family") == "Arial"
    assert st.get("text_align") == "Center"
    # Undeclared wording -> no opinion, caller keeps its own defaults.
    assert _declared_heading_style(report, "Nowhere In This Layout") == {}


def test_cover_heading_carries_declared_size_slant_and_underline():
    """Size/weight/underline of the DECLARED heading reach the cover box --
    and the declared SLANT is deliberately not painted.

    TRUTH MEASUREMENT (2026-08-08, whole truth corpus): 16 Oracle-driver
    PDFs ("Oracle PDF driver" / "Oracle12c AS Reports Services"), 142,831
    non-blank text spans -> ZERO italic-flagged spans, and not one
    *-Oblique / *-Italic face appears in any page's font resources. Bold IS
    honoured by the same driver (32,604 bold spans), so this is an
    italic-is-ignored dialect, not "styling is ignored". Cross-referenced
    per object: of 35 declared italic="yes" objects in truth-paired
    sources, the 16 that carry locatable static text ALL print upright
    Helvetica / Helvetica-Bold. Emitting FontStyle=Italic made our own
    render paint Helvetica-Oblique / Helvetica-BoldOblique on exactly those
    strings (37 oblique spans over 5 reports, truth 0).
    """
    import xml.etree.ElementTree as ET

    report = parse_oracle_xml(_XML)
    rect = _build_cover_page(report)
    assert rect is not None
    xml = ET.tostring(rect, encoding="unicode")
    m = re.search(r'<(?:\w+:)?Textbox Name="Cov_ParamsHdr">(.*?)'
                  r'</(?:\w+:)?Textbox>', xml, re.S)
    assert m, "cover heading textbox missing"
    hdr = m.group(1)
    assert "<FontSize>14pt</FontSize>" in hdr, "declared heading size dropped"
    # STRICTER than the old "declared italic must be emitted": the slant is
    # absent from this box AND from the whole cover subtree.
    assert "<FontStyle>Italic</FontStyle>" not in hdr, \
        "Oracle never paints an oblique face"
    assert "<FontStyle>Italic</FontStyle>" not in xml, \
        "no cover element may carry a slant"
    assert "<TextDecoration>Underline</TextDecoration>" in hdr, \
        "declared underline dropped"
    assert "<FontWeight>Bold</FontWeight>" in hdr
    # The box must be tall enough for one full line of the DECLARED size
    # (padding + descenders + the underline rule) or the decoration clips.
    hm = re.search(r"<Height>([\d.]+)in</Height>", hdr)
    assert hm and float(hm.group(1)) >= 14 * 1.28 / 72.0

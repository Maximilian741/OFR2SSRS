"""MODULE-FILENAME STAMP: where the declaration puts it decides what it is.

Oracle exports carry a text object whose whole content is the source module's
own file name (``<MODULE>.rdf``). The converter used to drop it EVERYWHERE on
the grounds that it is a developer/margin trailer, never report content. That
is only half true, and the half that is wrong was silent content loss:

* declared inside the section's ``<margin>`` band it IS page chrome — its SSRS
  home is the PageHeader/PageFooter band, so a body copy would print it twice;
* declared inside the ``<body>`` it is report content and the Oracle export
  prints it exactly where the body declares it.

Truth-measured on a per-record invoice letter: the stamp is declared at
x=6.2604in, width=1.2396in, ``justify="end"``, inside the signature repeating
frame — declared right edge 7.50in. The truth PDF prints it right-aligned at
0.50in body origin + 7.50in = 576pt on EVERY letter page, at the declared 8pt.
The converter emitted nothing there.

The discriminator is structural and already parsed: ``LayoutField.in_margin``,
set from the declaration's own ``<margin>`` membership. No names, no
report-specific tokens.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.parsers.oracle_xml import parse_oracle_xml  # noqa: E402

NS = ("{http://schemas.microsoft.com/sqlserver/reporting/"
      "2008/01/reportdefinition}")

STAMP = "NOTICE_DOC.rdf"


def _letter_xml(stamp_in_margin: bool) -> bytes:
    """A per-record letter that declares the module stamp either inside the
    record's repeating frame (body content) or in the section margin band."""
    stamp = (
        '<text name="B_REP_NAME">'
        '<textSettings justify="end" spacing="0"/>'
        '<geometryInfo x="6.26000" y="{y}" width="1.24000" height="0.19000"/>'
        '<textSegment><font face="Arial" size="8"/>'
        f'<string><![CDATA[{STAMP}]]></string>'
        '</textSegment></text>'
    )
    body_stamp = "" if stamp_in_margin else stamp.format(y="4.60000")
    margin_band = (
        "<margin>" + stamp.format(y="10.30000") + "</margin>"
        if stamp_in_margin else "")
    return (
        '<?xml version="1.0"?>'
        '<report name="NOTICE_DOC" DTDVersion="9.0.2.0.10">'
        '<data><dataSource name="Q_DOC">'
        '<select><![CDATA[select acct_no, addressee from notices]]></select>'
        '<group name="G_DOC">'
        '<dataItem name="ACCT_NO" datatype="vchar2"/>'
        '<dataItem name="ADDRESSEE" datatype="vchar2"/>'
        '</group></dataSource></data>'
        '<layout>'
        '<section name="main" width="8.50000">'
        '<body width="7.50000" height="10.60000">'
        '<repeatingFrame name="R_DOC" source="G_DOC" printDirection="down" '
        'maxRecordsPerPage="1" minWidowRecords="1" columnMode="no">'
        '<geometryInfo x="0.00000" y="0.00000" width="7.50000" '
        'height="5.00000"/>'
        '<generalLayout verticalElasticity="variable"/>'
        '<field name="F_ADDR" source="ADDRESSEE">'
        '<geometryInfo x="0.30000" y="0.30000" width="4.00000" '
        'height="0.20000"/></field>'
        '<field name="F_ACCT" source="ACCT_NO">'
        '<geometryInfo x="0.30000" y="0.70000" width="4.00000" '
        'height="0.20000"/></field>'
        '<text name="B_CLOSE"><geometryInfo x="0.30000" y="4.00000" '
        'width="4.00000" height="0.20000"/><textSegment>'
        '<font face="Arial" size="10"/>'
        '<string><![CDATA[Sincerely,]]></string>'
        '</textSegment></text>'
        f'{body_stamp}'
        '</repeatingFrame>'
        f'</body>{margin_band}</section></layout></report>'
    ).encode()


def _stamp_boxes(rdl: str):
    """Every body Textbox whose only run is the module stamp, with its
    declared geometry."""
    root = ET.fromstring(rdl)
    body = root.find(".//" + NS + "Body")
    out = []
    for tb in body.iter(NS + "Textbox"):
        runs = [(v.text or "") for v in tb.iter(NS + "Value")]
        if any(STAMP in r for r in runs):
            out.append({
                "name": tb.get("Name"),
                "left": tb.findtext(NS + "Left"),
                "width": tb.findtext(NS + "Width"),
                "align": next(
                    (a.text for a in tb.iter(NS + "TextAlign")), None),
            })
    return out


def test_parser_tags_margin_membership_for_the_stamp():
    """The discriminator this fix keys on is really declared: the parser tags
    in_margin from the <margin> band and leaves a body-declared stamp clear."""
    def _flags(xml):
        rep = parse_oracle_xml(xml)
        found = []

        def walk(node):
            for f in (getattr(node, "fields", None) or []):
                if (getattr(f, "text", "") or "").strip() == STAMP:
                    found.append(bool(getattr(f, "in_margin", False)))
            for c in (getattr(node, "children", None) or []):
                walk(c)
        for s in rep.layout:
            walk(s)
        return found

    assert _flags(_letter_xml(stamp_in_margin=False)) == [False]
    assert _flags(_letter_xml(stamp_in_margin=True)) == [True]


def test_body_declared_module_stamp_reaches_the_rdl_at_its_declared_box():
    """Declared in the BODY, the stamp is report content: it must be emitted,
    once, at its declared box and its declared end-justification."""
    rdl = convert(_letter_xml(stamp_in_margin=False))["rdl_xml"]
    boxes = _stamp_boxes(rdl)
    assert len(boxes) == 1, (
        f"a body-declared module stamp must be emitted exactly once, "
        f"got {boxes}")
    box = boxes[0]
    assert abs(float(box["left"].rstrip("in")) - 6.26) < 0.005, box
    assert abs(float(box["width"].rstrip("in")) - 1.24) < 0.005, box
    assert box["align"] == "Right", (
        f"declared justify=\"end\" must survive: {box}")


def test_margin_declared_module_stamp_never_enters_the_body():
    """Declared in the section MARGIN it is page chrome: the body must not
    carry a copy (the page band owns it, and two copies print it twice)."""
    rdl = convert(_letter_xml(stamp_in_margin=True))["rdl_xml"]
    assert _stamp_boxes(rdl) == [], (
        "a margin-declared module stamp must stay out of the body flow")


def test_body_emitter_itself_keeps_the_margin_gate():
    """The gate lives in the body textbox emitter, so drive it directly: the
    SAME declared stamp is emitted when the declaration puts it in the body
    and refused when the declaration puts it in the margin band. Without this
    the margin half of the rule is only guarded by whichever caller happens
    to filter margin objects out first."""
    from converter.generators import rdl as R
    from converter.models import LayoutField, ParsedReport

    def _emit(in_margin):
        lf = LayoutField(name="B_REP_NAME", kind="text", text=STAMP,
                         x=6.26, y=4.60, width=1.24, height=0.19)
        lf.in_margin = in_margin
        items = ET.Element(R._q("ReportItems"))
        ok, _ = R._emit_field_textbox(
            items, "Tb_stamp", "", lf, 0.0, 4.40, 7.5, 1.0,
            ParsedReport(name="NOTICE_DOC"), set())
        return ok, len(list(items))

    assert _emit(in_margin=False) == (True, 1), (
        "a body-declared module stamp is report content and must be emitted")
    assert _emit(in_margin=True) == (False, 0), (
        "a margin-declared module stamp is page chrome and must be refused")

"""SHEET ARITHMETIC: a page BAND is seated inside the MARGIN its declaration
places it in, and a BLOCK reserves only what it PAINTS.

Root cause of a P0 production defect, measured on the agency's own letter
family: six reports printed furniture-only sheets interleaved through a print
run -- four letters at three blank sheets out of seven, one packet with a
blank sheet at every row count, one packet blank in its suppressed trigger
world. Four independent arithmetic errors, each of which manufactures sheets:

  1. BAND vs MARGIN.  SSRS puts the body's bottom edge at
     ``PageHeight - BottomMargin - PageFooter.Height``.  The generator sized
     the bottom margin from the DECLARED section body alone and then emitted a
     declared footer band on top of it, so the band's height was charged to
     the BODY: a 0.3543in declared address footer left 9.58in of printable
     body for a body the source declares at 9.6875in, and every record spilled
     a sliver sheet.  The header edge had always worked the other way (the
     band's own top offset IS the TopMargin); this is that rule on both edges.

  2. EMPTY CONTAINER TAIL.  A record's fit was judged from the emitted
     CONTAINER extent, but a container's empty tail is not content.  A source
     may declare an outer frame TALLER THAN ITS OWN SECTION BODY (measured:
     10.0665in declared inside ``<body height="9.6875">``, deepest mark at
     7.45in); reading that tail as content put the record 0.03in past the pad
     tolerance and the clamp declined.

  3. OVER-DECLARED BLOCK.  The same rule for a multi-sheet packet's top-level
     blocks: a block whose declared box overruns the sheet it starts on while
     its MARKS fit inside it is reserving empty paper, and the engine prints a
     companion sheet for it.

  4. HIDDEN BLOCK RESERVES.  SSRS reserves a hidden RECTANGLE's box and
     collapses a hidden TABLIX ROW (the settled engine fact the per-record
     variant-band pass is built on).  A conditionally-hidden top-level block
     therefore printed its whole box as blank sheets in the suppressed world.

Every rule here is pure GEOMETRY plus the artifact's own declarations, so none
of them can read differently in one script than another; the last test proves
that by running the identical geometry with labels in five scripts.
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.generators.rdl import (  # noqa: E402
    _collapse_conditional_body_blocks, _painted_extent_in, _rect_paints,
    _trim_unpainted_block_slack,
)

NS = ("{http://schemas.microsoft.com/sqlserver/reporting/2008/01/"
      "reportdefinition}")

# The five scripts the campaign's i18n roster covers, plus ascii.
SCRIPTS = {
    "ascii": ("Permit Details", "Inspector"),
    "greek": ("Στοιχεία "
              "Άδειας",
              "Επιθεωρητής"),
    "cyrillic": ("Данные "
                 "разрешения",
                 "Инспектор"),
    "arabic": ("تفاصيل "
               "التصريح",
               "المفتش"),
    "cjk": ("許可の詳細", "検査官"),
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _in(txt, default=0.0):
    m = re.match(r"\s*([0-9.]+)in\s*$", txt or "")
    return float(m.group(1)) if m else default


def _tag_in(rdl, tag):
    m = re.search(r"<%s>([0-9.]+)in</%s>" % (tag, tag), rdl)
    return float(m.group(1)) if m else 0.0


def _band_height(rdl, tag):
    m = re.search(r"<%s>(.*?)</%s>" % (tag, tag), rdl, re.S)
    if not m:
        return 0.0
    h = re.search(r"<Height>([0-9.]+)in</Height>", m.group(1))
    return float(h.group(1)) if h else 0.0


def _printable_body(rdl):
    """Inches the emitted sheet actually leaves for the BODY."""
    return (_tag_in(rdl, "PageHeight") - _tag_in(rdl, "TopMargin")
            - _tag_in(rdl, "BottomMargin")
            - _band_height(rdl, "PageHeader") - _band_height(rdl, "PageFooter"))


def _record_row_height(rdl):
    rows = [float(x) for x in
            re.findall(r"<TablixRow>\s*<Height>([0-9.]+)in</Height>", rdl)]
    return max(rows) if rows else 0.0


def _letter_xml(body_h="9.68750", body_y="0.56250", frame_h="10.06653",
                labels=("Permit Details", "Inspector"), footer=True,
                close_y="7.01953"):
    """A per-record LETTER in the dialect that exposed rules 1 and 2:

      * ``<section name="main">`` declares a body of ``body_h`` sitting at
        ``body_y`` down the paper -- so the paper under it is the declared
        bottom margin;
      * its outer repeating frame declares ``frame_h``, which the caller may
        set TALLER than that body (the real sources do);
      * every MARK sits well inside the declared body; and
      * the ``<margin>`` declares real footer chrome, which must be seated in
        the declared bottom margin rather than charged to the body.
    """
    lbl, who = labels
    # Declared bottom-margin chrome at its authored PAPER y: a report-name
    # line and an address line, exactly the two-object band the agency
    # letters declare. Its extent (0.3543in) is what must come out of the
    # declared bottom margin rather than out of the body.
    foot = (
        '<text name="B_REPORT"><textSettings spacing="0"/>'
        '<geometryInfo x="6.22913" y="10.34338" width="1.50415" '
        'height="0.16663"/><textSegment><font face="Arial" size="8"/>'
        '<string><![CDATA[%s]]></string></textSegment></text>'
        '<text name="B_ADDRESS_FTR"><textSettings justify="center" '
        'spacing="0"/><geometryInfo x="0.98633" y="10.52673" '
        'width="6.75366" height="0.17090"/><textSegment>'
        '<font face="Arial" size="8"/>'
        '<string><![CDATA[%s -- %s]]></string></textSegment></text>'
        % (lbl, lbl, who)
    ) if footer else ""
    return (
        '<?xml version="1.0"?>'
        '<report name="LTR_DOC" DTDVersion="9.0.2.0.10">'
        '<data><dataSource name="Q_VISIT">'
        '<select><![CDATA[select site_name, addressee from visits]]></select>'
        '<group name="G_VISIT">'
        '<dataItem name="SITE_NAME" datatype="vchar2"/>'
        '<dataItem name="ADDRESSEE" datatype="vchar2"/>'
        '</group></dataSource></data>'
        '<layout>'
        '<section name="header" width="8.50000">'
        '<body width="8.00000" height="9.00000">'
        '<text name="H_L"><geometryInfo x="0.25000" y="0.75000" '
        'width="1.65000" height="0.25000"/><textSegment>'
        '<font face="Arial" size="12"/><string><![CDATA[%s:]]></string>'
        '</textSegment></text>'
        '</body></section>'
        '<section name="main" orientation="portrait" width="8.50000">'
        '<body width="6.89587" height="%s">'
        '<location x="0.85413" y="%s"/>'
        '<repeatingFrame name="R_G_VISIT" source="G_VISIT" '
        'printDirection="down" maxRecordsPerPage="1" minWidowRecords="1" '
        'columnMode="no">'
        '<geometryInfo x="0.00000" y="0.00000" width="6.89587" '
        'height="%s"/>'
        '<generalLayout pageProtect="yes" verticalElasticity="variable"/>'
        '<field name="F_SITE" source="SITE_NAME">'
        '<geometryInfo x="0.10000" y="2.65613" width="3.56250" '
        'height="0.18860"/></field>'
        '<field name="F_ADDR" source="ADDRESSEE">'
        '<geometryInfo x="0.10000" y="3.59863" width="6.79211" '
        'height="0.18750"/></field>'
        '<text name="B_CLOSE"><geometryInfo x="0.10000" y="%s" '
        'width="4.25000" height="0.18787"/><textSegment>'
        '<font face="Arial" size="10"/><string><![CDATA[%s]]></string>'
        '</textSegment></text>'
        '</repeatingFrame>'
        '</body>'
        '<margin>%s</margin>'
        '</section></layout></report>'
        % (lbl, body_h, body_y, frame_h, close_y, who, foot)
    ).encode()


# ---------------------------------------------------------------------------
# 1. A DECLARED PAGE BAND IS SEATED IN THE DECLARED MARGIN
# ---------------------------------------------------------------------------

def test_declared_footer_band_does_not_eat_the_declared_body():
    """THE INVARIANT: the sheet must still leave the body the height its
    source declares for it once the declared bands are seated."""
    rdl = convert(_letter_xml())["rdl_xml"]
    assert _band_height(rdl, "PageFooter") > 0.0, (
        "fixture must emit the declared footer chrome, or it tests nothing")
    assert _printable_body(rdl) >= 9.6875 - 0.005, (
        "declared 9.6875in body got only %.4fin of sheet -- the footer band "
        "was charged to the body instead of to the margin it is declared in"
        % _printable_body(rdl))


def test_the_footerless_twin_keeps_the_whole_declared_margin():
    """DIFFERENTIAL: the rule keys on a band actually being emitted. With no
    declared footer chrome the bottom margin is the declared one, untouched --
    so the fix cannot be a blanket margin shrink."""
    with_foot = convert(_letter_xml())["rdl_xml"]
    without = convert(_letter_xml(footer=False))["rdl_xml"]
    assert _band_height(without, "PageFooter") == 0.0
    assert _tag_in(without, "BottomMargin") > _tag_in(with_foot,
                                                      "BottomMargin"), (
        "the band-bearing edge must give the band its room out of the MARGIN "
        "(%.4f) while the bandless twin keeps the declared margin (%.4f)"
        % (_tag_in(with_foot, "BottomMargin"),
           _tag_in(without, "BottomMargin")))


def test_a_record_that_fits_its_declared_body_prints_one_sheet():
    """END TO END: the record row must fit the printable body the sheet
    really has. This is the number that decides sheets-per-record."""
    rdl = convert(_letter_xml())["rdl_xml"]
    row, fits = _record_row_height(rdl), _printable_body(rdl)
    assert 0 < row <= fits, (
        "record row %.4fin against %.4fin of printable body: every record "
        "spills a furniture-only sheet" % (row, fits))


# ---------------------------------------------------------------------------
# 2. AN EMPTY CONTAINER TAIL IS NOT CONTENT
# ---------------------------------------------------------------------------

def test_declared_frame_taller_than_its_own_body_is_empty_paper():
    """A frame declared TALLER than the section body it sits in contributes
    empty paper below its last mark, never printable content."""
    tall = convert(_letter_xml(frame_h="10.06653"))["rdl_xml"]
    assert _record_row_height(tall) <= _printable_body(tall), (
        "the frame's empty tail was read as content and spilled the record")


def test_ink_past_the_declared_body_still_flows():
    """DIFFERENTIAL -- the rule may never clip a MARK. A record whose ink
    genuinely runs past the declared body keeps the grow/flow behaviour, so
    the clamp is not a blanket 'shrink every record'."""
    rdl = convert(_letter_xml(frame_h="10.06653",
                              close_y="12.40000"))["rdl_xml"]
    assert _record_row_height(rdl) > _printable_body(rdl), (
        "a record whose own marks outrun the declared body must still flow "
        "across sheets; clamping it would clip declared ink")


def test_painted_extent_ignores_grouping_boxes_but_not_painted_ones():
    """The measure the rules above key on: a Rectangle that paints nothing is
    pure geometry; one that declares a border or a fill IS ink to its box."""
    def _rect(style):
        return ET.fromstring(
            '<ReportItems xmlns="%s"><Rectangle Name="R"><Top>0in</Top>'
            '<Height>9in</Height>%s<ReportItems><Textbox Name="T">'
            '<Top>1.8in</Top><Height>0.2in</Height></Textbox></ReportItems>'
            '</Rectangle></ReportItems>' % (NS[1:-1], style))
    plain = _rect("<Style><Border><Style>None</Style></Border></Style>")
    ruled = _rect("<Style><Border><Style>Solid</Style></Border></Style>")
    filled = _rect("<Style><BackgroundColor>#eeeeee</BackgroundColor></Style>")
    assert _painted_extent_in(plain) == pytest.approx(2.0, abs=0.001), (
        "a grouping box must contribute only its children's marks")
    assert _painted_extent_in(ruled) == pytest.approx(9.0, abs=0.001)
    assert _painted_extent_in(filled) == pytest.approx(9.0, abs=0.001)
    assert not _rect_paints(plain[0])
    assert _rect_paints(ruled[0])


# ---------------------------------------------------------------------------
# 3. AN OVER-DECLARED BLOCK RESERVES ONLY WHAT IT PAINTS
# ---------------------------------------------------------------------------

def _packet_root(block_h, ink_bottom, block_top=12.33, body_h=23.71,
                 style="<Style><Border><Style>None</Style></Border></Style>",
                 lead_break=True):
    """A packet body in the shape the defect was measured in: two leading
    blocks that DECLARE a page break at their end, then the block under test
    -- declared ``block_h`` tall with its deepest mark at ``ink_bottom``
    inside it -- starting at the top of the third sheet, which has 9.977in of
    room."""
    brk = "<PageBreak><BreakLocation>End</BreakLocation></PageBreak>"
    lead = (
        '<Rectangle Name="Lead0"><Top>0in</Top><Left>0in</Left>'
        '<Width>7in</Width><Height>9.1868in</Height>%s<ReportItems>'
        '<Textbox Name="L0"><Top>1in</Top><Height>0.2in</Height></Textbox>'
        '</ReportItems></Rectangle>'
        '<Rectangle Name="Lead1"><Top>9.63in</Top><Left>0in</Left>'
        '<Width>7in</Width><Height>2.5in</Height>%s<ReportItems>'
        '<Textbox Name="L1"><Top>0.1in</Top><Height>0.2in</Height></Textbox>'
        '</ReportItems></Rectangle>' % (brk if lead_break else "",
                                        brk if lead_break else ""))
    return ET.fromstring(
        '<Report xmlns="%s"><Body><ReportItems>%s'
        '<Rectangle Name="Blk"><Top>%.4fin</Top><Left>0in</Left>'
        '<Width>7in</Width><Height>%.4fin</Height>%s<ReportItems>'
        '<Textbox Name="T"><Top>%.4fin</Top><Left>0in</Left>'
        '<Width>3in</Width><Height>0.2in</Height></Textbox>'
        '</ReportItems></Rectangle>'
        '</ReportItems><Height>%.2fin</Height></Body><Width>7in</Width>'
        '<Page><PageHeight>11.00in</PageHeight><PageWidth>8.5in</PageWidth>'
        '<TopMargin>0.5in</TopMargin><BottomMargin>0.523in</BottomMargin>'
        '<LeftMargin>0.5in</LeftMargin><RightMargin>0.5in</RightMargin>'
        '</Page></Report>'
        % (NS[1:-1], lead, block_top, block_h, style, ink_bottom - 0.2,
           body_h))


def _block_height(root):
    for r in root.iter("%sRectangle" % NS):
        if r.get("Name") == "Blk":
            return _in(r.findtext("%sHeight" % NS))
    raise AssertionError("fixture lost its block")


def test_over_declared_block_gives_up_only_its_empty_tail():
    """The measured packet: a 10.1875in block whose deepest mark lands at
    7.15in overran its sheet and printed a 4th, blank sheet."""
    root = _packet_root(block_h=10.1875, ink_bottom=7.1541)
    _trim_unpainted_block_slack(root)
    assert _block_height(root) == pytest.approx(7.1541, abs=0.001), (
        "the empty tail below the last mark must be given up, and nothing "
        "else")


def test_a_block_whose_marks_outrun_the_sheet_is_untouched():
    """DIFFERENTIAL: only a tail that MANUFACTURES a sheet is trimmed. A
    block whose own ink crosses the sheet is genuinely multi-sheet."""
    root = _packet_root(block_h=10.1875, ink_bottom=10.10)
    _trim_unpainted_block_slack(root)
    assert _block_height(root) == pytest.approx(10.1875, abs=0.001)


def test_a_block_that_fits_its_sheet_is_untouched():
    """DIFFERENTIAL: a block reserving empty area WITHIN its own sheet costs
    no sheet -- siblings are absolutely positioned -- so nothing moves."""
    root = _packet_root(block_h=5.0, ink_bottom=1.0, block_top=0.0)
    _trim_unpainted_block_slack(root)
    assert _block_height(root) == pytest.approx(5.0, abs=0.001)


def test_a_block_that_paints_keeps_its_declared_box():
    """DIFFERENTIAL: a ruled panel or write-in box IS ink to its full box."""
    root = _packet_root(block_h=10.1875, ink_bottom=7.1541,
                        style="<Style><Border><Style>Solid</Style></Border>"
                              "</Style>")
    _trim_unpainted_block_slack(root)
    assert _block_height(root) == pytest.approx(10.1875, abs=0.001)


def test_the_sheet_a_block_starts_on_comes_from_declared_breaks():
    """DIFFERENTIAL on the ORIGIN: with no DECLARED page break above it the
    block's sheet is not known from the artifact, so the conservative reading
    (measure from the top of the body) refuses the trim. The rule may only
    act on a boundary the declaration puts there."""
    root = _packet_root(block_h=10.1875, ink_bottom=7.1541, lead_break=False)
    _trim_unpainted_block_slack(root)
    assert _block_height(root) == pytest.approx(10.1875, abs=0.001)


# ---------------------------------------------------------------------------
# 4. A CONDITIONALLY-HIDDEN BLOCK COLLAPSES, IT DOES NOT RESERVE
# ---------------------------------------------------------------------------

def _conditional_packet(hidden='=(First(Fields!AMT.Value, "Q_G") &lt; 0)',
                        block_h=10.9827, toggle=False, scope="Q_G",
                        lead_break=False):
    """A packet whose second block is gated on a declared amount test. The
    report declares exactly one dataset, ``Q_G``; ``scope`` is what the BLOCK
    names, so a caller can make the block name a scope that does not exist.
    ``lead_break`` puts a DECLARED page break above the block, which starts it
    at the top of a fresh sheet."""
    vis = ("<Visibility><Hidden>%s</Hidden>%s</Visibility>"
           % (hidden, "<ToggleItem>Tb</ToggleItem>" if toggle else ""))
    lead = (
        '<Rectangle Name="Lead"><Top>0in</Top><Left>0in</Left>'
        '<Width>7in</Width><Height>9.1868in</Height>'
        '<PageBreak><BreakLocation>End</BreakLocation></PageBreak>'
        '<ReportItems><Textbox Name="L"><Top>1in</Top><Height>0.2in</Height>'
        '</Textbox></ReportItems></Rectangle>') if lead_break else ""
    return ET.fromstring(
        '<Report xmlns="%s"><DataSets><DataSet Name="Q_G"><Fields/></DataSet>'
        '</DataSets><Body><ReportItems>%s'
        '<Rectangle Name="Blk"><Top>9.39in</Top><Left>0in</Left>'
        '<Width>7in</Width><Height>%.4fin</Height>%s'
        '<Style><Border><Style>None</Style></Border></Style><ReportItems>'
        '<Textbox Name="T"><Top>0.1in</Top><Left>0in</Left><Width>3in</Width>'
        '<Height>0.2in</Height><Value>=First(Fields!AMT.Value, "%s")</Value>'
        '</Textbox></ReportItems></Rectangle>'
        '</ReportItems><Height>22.35in</Height></Body><Width>7in</Width>'
        '<Page><PageHeight>11.00in</PageHeight><PageWidth>8.5in</PageWidth>'
        '<TopMargin>0.5in</TopMargin><BottomMargin>0.302in</BottomMargin>'
        '<LeftMargin>0.5in</LeftMargin><RightMargin>0.5in</RightMargin>'
        '<PageFooter><Height>0.3543in</Height><ReportItems><Textbox '
        'Name="F"><Height>0.2in</Height></Textbox></ReportItems>'
        '</PageFooter></Page></Report>'
        % (NS[1:-1], lead, block_h, vis, scope))


def test_conditional_block_becomes_a_collapsing_row():
    """The rewrite: the block's Hidden moves to a STATIC Tablix row member,
    the block keeps its geometry, and the Tablix binds the ONE dataset scope
    the block names (a body-level Tablix without a DataSetName is rejected by
    the engine -- measured against the real ReportViewer)."""
    root = _conditional_packet()
    assert _collapse_conditional_body_blocks(root) == 1
    tab = root.find(".//%sTablix" % NS)
    assert tab is not None and tab.get("Name").startswith("Band_")
    assert tab.findtext("%sDataSetName" % NS) == "Q_G", (
        "the wrapping region must declare the scope the block itself names")
    member = tab.find(".//%sTablixRowHierarchy//%sTablixMember" % (NS, NS))
    assert member.find("%sVisibility" % NS) is not None, (
        "the Hidden must move to the ROW member -- a hidden ROW collapses "
        "while a hidden RECTANGLE reserves its box")
    row_h = _in(tab.find(".//%sTablixRow/%sHeight" % (NS, NS)).text)
    assert row_h == pytest.approx(10.9827, abs=0.001), (
        "visible, the row must still be the block's declared height")
    blk = tab.find(".//%sCellContents/%sRectangle" % (NS, NS))
    assert blk is not None and blk.find("%sVisibility" % NS) is None


@pytest.mark.parametrize("kwargs,why", [
    (dict(hidden="false"), "a constant Hidden is not a format trigger"),
    (dict(toggle=True), "a drill-down toggle must stay interactive"),
    (dict(block_h=0.4), "a block inside its own sheet manufactures no sheet"),
    (dict(hidden="=(1 &lt; 0)", scope="Q_NOT_DECLARED"),
     "a block naming no declared scope cannot be bound"),
])
def test_collapse_is_refused_where_it_is_not_the_defect(kwargs, why):
    """DIFFERENTIAL: every gate on the rewrite, one at a time."""
    root = _conditional_packet(**kwargs)
    assert _collapse_conditional_body_blocks(root) == 0, why
    assert root.find(".//%sTablix" % NS) is None


def test_collapse_reads_the_sheet_from_the_declared_break():
    """DIFFERENTIAL on the ORIGIN: the block's box is measured against the
    sheet the DECLARATION starts it on. With a declared break above it, a
    2in block at body-y 9.39 sits wholly inside its own sheet and costs
    nothing, so the rewrite is refused; measured from the top of the body
    instead, the same block looks like an overrun and gets rewritten for
    no gain."""
    root = _conditional_packet(block_h=2.0, lead_break=True)
    assert _collapse_conditional_body_blocks(root) == 0, (
        "a block that fits the sheet its declared break puts it on "
        "manufactures no sheet")
    assert root.find(".//%sTablix" % NS) is None


# ---------------------------------------------------------------------------
# 5. THE DECLARED WIDOW RULE
# ---------------------------------------------------------------------------

def _trailer_xml(min_widow="1", frame_h="1.67700"):
    """A report whose listing is followed by a TRAILER repeating frame bound
    to its own query -- the signature-block shape. The trailer declares a tall
    instance pitch and paints only a line at its top, which is what makes a
    SPLIT instance print a sheet carrying nothing."""
    widow = (' minWidowRecords="%s"' % min_widow) if min_widow else ""
    return (
        '<?xml version="1.0"?><report name="TRAILER_DOC" '
        'DTDVersion="9.0.2.0.10"><data>'
        '<dataSource name="Q_Main"><select><![CDATA[select main_a from '
        't_main]]></select><group name="G_Main">'
        '<dataItem name="MAIN_A" datatype="vchar2"/></group></dataSource>'
        '<dataSource name="Q_Sign"><select><![CDATA[select signer from '
        't_sign]]></select><group name="G_Sign">'
        '<dataItem name="SIGNER" datatype="vchar2"/></group></dataSource>'
        '</data><layout><section name="main">'
        '<body width="8.00000" height="9.75000">'
        '<frame name="M_Body"><geometryInfo x="0" y="0" width="8" '
        'height="3"/>'
        '<repeatingFrame name="R_Main" source="G_Main" printDirection="down">'
        '<geometryInfo x="0.2" y="0.2" width="7.5" height="0.2"/>'
        '<field name="F_A" source="MAIN_A"><geometryInfo x="0.2" y="0.2" '
        'width="3" height="0.2"/></field></repeatingFrame>'
        '<frame name="M_Sign"><geometryInfo x="0.2" y="1.2" width="7.5" '
        'height="2.0"/>'
        '<repeatingFrame name="R_Sign" source="G_Sign" '
        'printDirection="down"%s>'
        '<geometryInfo x="0.3" y="1.3" width="4" height="%s"/>'
        '<text name="B_SIGN"><geometryInfo x="0.3" y="1.3" width="2.4" '
        'height="0.18738"/><textSegment><font face="Arial" size="8"/>'
        '<string><![CDATA[Sincerely,]]></string></textSegment></text>'
        '<field name="F_S" source="SIGNER"><geometryInfo x="2.9" y="1.3" '
        'width="0.9" height="0.18738"/></field>'
        '</repeatingFrame></frame></frame>'
        '</body></section></layout></report>' % (widow, frame_h)).encode()


def _trailer_member_keeps_together(rdl):
    """Does the trailer region's ROW MEMBER declare KeepTogether? Read off the
    parsed tree, not the text: a KeepTogether anywhere else in the region (a
    cell rect, a textbox) is a different declaration entirely."""
    root = ET.fromstring(rdl)
    for t in root.iter("%sTablix" % NS):
        if not (t.get("Name") or "").startswith("Tablix_Breakdown"):
            continue
        rh = t.find("%sTablixRowHierarchy" % NS)
        assert rh is not None
        return any((m.findtext("%sKeepTogether" % NS) or "") == "true"
                   for m in rh.iter("%sTablixMember" % NS))
    raise AssertionError("fixture must emit a trailer breakdown region")


def test_declared_widow_rule_keeps_a_record_instance_whole():
    """``minWidowRecords`` is the fewest instances Oracle leaves at the bottom
    of a page: with one declared, a record that does not fit the room left
    moves WHOLE to the next page. SSRS splits the row instead, and a row whose
    marks all sit in its top slice then spills a sheet carrying nothing."""
    assert _trailer_member_keeps_together(convert(_trailer_xml())["rdl_xml"])


def test_a_frame_declaring_no_widow_rule_still_splits():
    """DIFFERENTIAL: the rule is the DECLARATION, not a habit. A frame that
    declares no widow minimum keeps SSRS's own splitting behaviour."""
    rdl = convert(_trailer_xml(min_widow=""))["rdl_xml"]
    assert not _trailer_member_keeps_together(rdl)


def test_an_instance_taller_than_the_sheet_is_not_kept_together():
    """DIFFERENTIAL: an instance that fits no page cannot be protected from
    splitting -- and demanding it makes the engine push it to a fresh sheet
    before splitting anyway, emptying the sheet it left. Oracle's own rule
    degrades the same way."""
    rdl = convert(_trailer_xml(frame_h="14.00000"))["rdl_xml"]
    assert not _trailer_member_keeps_together(rdl)


# ---------------------------------------------------------------------------
# 6. THE SAME ARITHMETIC IN EVERY SCRIPT
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("script", sorted(SCRIPTS))
def test_sheet_arithmetic_is_identical_in_every_script(script):
    """A rule that behaves differently for English than for Greek is the
    defect this campaign exists to kill. These rules are geometry, so the
    identical declaration must produce the identical sheet arithmetic with
    its labels written in any script."""
    rdl = convert(_letter_xml(labels=SCRIPTS[script]))["rdl_xml"]
    base = convert(_letter_xml(labels=SCRIPTS["ascii"]))["rdl_xml"]
    for name, fn in (("printable body", _printable_body),
                     ("record row", _record_row_height),
                     ("footer band", lambda r: _band_height(r, "PageFooter"))):
        assert fn(rdl) == pytest.approx(fn(base), abs=0.001), (
            "%s differs for %s (%.4f) vs ascii (%.4f)"
            % (name, script, fn(rdl), fn(base)))
    assert 0 < _record_row_height(rdl) <= _printable_body(rdl)

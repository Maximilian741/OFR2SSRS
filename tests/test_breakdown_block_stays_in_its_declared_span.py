"""A report-end breakdown block owns exactly the box its frame declares.

The report-end breakdown fallback (``_emit_secondary_breakdown_tables``)
appends a one-column tablix per dropped secondary frame. Two boundaries of
that box are load-bearing, and this file holds both. Every number below is
DECLARED by the synthetic fixture; nothing is name-matched.

  HORIZONTAL -- a member may not paint past its column span.
    The span is derived from the frame's own member extent, so members fit by
    construction. Two emitter-side adjustments can push one past it anyway:
    the minimum width applied so a hairline member still prints (a member
    declared narrower than that floor, flush at the frame's right edge, ends
    outside), and the page-edge clamp that shrinks the COLUMN to the printable
    width while leaving member boxes at their declared geometry. Past the span
    a member paints into whatever region is declared to its right, or off the
    paper -- the horizontal-overflow signature that paginates a near-blank
    companion sheet after every content page. Measured on the fixture below
    without the clip: a 0.15in member floored to 0.30in ends 0.10in outside a
    1.15in span, and a 1.50in member in a 1.55in span at the paper's right
    edge ends 0.05in outside, touching the 8.50in sheet edge exactly.

  VERTICAL -- consecutive blocks may not interleave.
    A data region's reflow box is the SUM OF ITS ROW HEIGHTS (see
    ``_region_reflow_height_in`` and test_nested_block_never_drifts_into_
    parent.py). A block anchored inside a preceding region's row-sum box is
    never reflowed by it: at three rows nothing shows, and at twenty-five the
    two advance on separate pitches and grow straight through each other.
    That is the shape the hostile-data-shape rail caught -- clean at rows=3
    and eleven painted-over pairs at rows=25 on a wild source, engine-
    measured. This file states the property structurally, at every shape at
    once, for EVERY pair of regions rather than one.

Both gates are mutation-proved below, each in two directions: the checker is
shown red on a doctored RDL, and the production clip is shown load-bearing by
neutering it and re-converting.
"""
import xml.etree.ElementTree as ET

import pytest

from converter import convert
from converter.generators import rdl as rdl_gen

NS = {"r": "http://schemas.microsoft.com/sqlserver/reporting/"
            "2008/01/reportdefinition"}


def _q(tag):
    return f"{{{NS['r']}}}{tag}"


def _in(el, tag, default=0.0):
    txt = el.findtext(_q(tag)) if el is not None else None
    try:
        return float((txt or "").replace("in", "").strip())
    except (AttributeError, ValueError):
        return default


def _fld(n, s, x, y, w, h):
    return (f'<field name="{n}" source="{s}"><geometryInfo x="{x}" y="{y}" '
            f'width="{w}" height="{h}"/></field>')


def _txt(n, t, x, y, w, h):
    return (f'<text name="{n}"><geometryInfo x="{x}" y="{y}" width="{w}" '
            f'height="{h}"/><textSegment><string><![CDATA[{t}]]></string>'
            f'</textSegment></text>')


# ---------------------------------------------------------------------------
# FIXTURE -- a main table plus TWO secondary frames with no resolvable
# correlation, i.e. two report-end breakdown blocks. Their declared members
# are the exact two shapes that leave the span: a 0.15in member (under the
# emitter's minimum member width) flush at the right edge of a 1.10in frame,
# and a 1.50in member inside a frame that reaches the 8.50in paper edge.
# ---------------------------------------------------------------------------
BDX_SRC = (
    '<?xml version="1.0"?><report name="BDX_T" DTDVersion="9.0.2.0.10"><data>'
    '<dataSource name="Q_Main"><select><![CDATA[select code, nm, amt from t]]>'
    '</select><group name="G_A"><dataItem name="CODE" datatype="number"/>'
    '<dataItem name="NM" datatype="vchar2"/>'
    '<dataItem name="AMT" datatype="number"/></group></dataSource>'
    '<dataSource name="Q_S1"><select><![CDATA[select lbl, amt1 from u]]>'
    '</select><group name="G_B"><dataItem name="LBL" datatype="vchar2"/>'
    '<dataItem name="AMT1" datatype="number"/></group></dataSource>'
    '<dataSource name="Q_S2"><select><![CDATA[select amt2 from v]]></select>'
    '<group name="G_C"><dataItem name="AMT2" datatype="number"/></group>'
    '</dataSource></data>'
    '<layout><section name="main"><body height="4.0">'
    '<frame name="M_A"><geometryInfo x="0.05" y="0.5" width="7.4" '
    'height="1.3"/>'
    '<repeatingFrame name="R_A" source="G_A" printDirection="down">'
    '<geometryInfo x="0.05" y="1.0" width="7.4" height="0.44"/>'
    + _fld("F_CODE", "CODE", 0.06, 1.02, 0.7, 0.40)
    + _fld("F_NM", "NM", 0.9, 1.02, 2.0, 0.40)
    + _fld("F_AMT", "AMT", 5.9, 1.02, 1.4, 0.40)
    + '<frame name="M_B"><geometryInfo x="6.9" y="1.02" width="1.1" '
      'height="0.21"/>'
      '<repeatingFrame name="R_B" source="G_B" printDirection="down">'
      '<geometryInfo x="6.9" y="1.02" width="1.1" height="0.21"/>'
    + _fld("F_LBL", "LBL", 6.9, 1.02, 0.9, 0.21)
    + _fld("F_AMT1", "AMT1", 7.85, 1.02, 0.15, 0.21)
    + '</repeatingFrame></frame>'
      '<frame name="M_C"><geometryInfo x="6.9" y="1.30" width="1.6" '
      'height="0.21"/>'
      '<repeatingFrame name="R_C" source="G_C" printDirection="down">'
      '<geometryInfo x="6.9" y="1.30" width="1.6" height="0.21"/>'
    + _fld("F_AMT2", "AMT2", 7.0, 1.30, 1.5, 0.21)
    + '</repeatingFrame></frame>'
      '</repeatingFrame></frame>'
    # a caption band, so the main region's declared <Height> falls SHORT of
    # its row sum — without that gap the vertical gate below is vacuous, and
    # the non-vacuity test says so.
    '<frame name="M_HDR"><geometryInfo x="0.05" y="0.5" width="7.4" '
    'height="0.5"/>'
    + _txt("B_CODE", "Code", 0.06, 0.55, 0.7, 0.4)
    + _txt("B_NM", "Name", 0.9, 0.55, 2.0, 0.4)
    + _txt("B_AMT", "Amount", 5.9, 0.55, 1.4, 0.4)
    + '</frame></body></section></layout></report>')


def _convert_fixture():
    return ET.fromstring(convert(BDX_SRC.encode(), "bdx.xml")["rdl_xml"])


@pytest.fixture(scope="module")
def bdx_root():
    return _convert_fixture()


# ---------------------------------------------------------------------------
# The two checkers. Both are pure functions of an RDL tree so the mutation
# proofs below can run them against a DOCTORED one and watch them go red.
# ---------------------------------------------------------------------------

def _blocks(root):
    body = root.find(_q("Body"))
    items = body.find(_q("ReportItems")) if body is not None else None
    if items is None:
        return []
    return [t for t in items if t.tag == _q("Tablix")
            and (t.get("Name") or "").startswith("Tablix_Breakdown")]


def _column_span(tablix):
    body = tablix.find(_q("TablixBody"))
    cols = body.find(_q("TablixColumns")) if body is not None else None
    if cols is None:
        return 0.0
    return sum(_in(c, "Width") for c in cols.findall(_q("TablixColumn")))


def _row_sum(tablix):
    body = tablix.find(_q("TablixBody"))
    rows = body.find(_q("TablixRows")) if body is not None else None
    if rows is None:
        return 0.0
    return sum(_in(r, "Height") for r in rows.findall(_q("TablixRow")))


def _members_past_span(root):
    """[(block, member, overhang_in)] for every member box leaving its column."""
    out = []
    for blk in _blocks(root):
        span = _column_span(blk)
        for tbx in blk.iter(_q("Textbox")):
            over = _in(tbx, "Left") + _in(tbx, "Width") - span
            if over > 0.005:
                out.append((blk.get("Name"), tbx.get("Name"), round(over, 4)))
    return out


def _regions_that_interleave(root):
    """[(later_block, earlier_region)] for every block anchored inside the
    REFLOW box (Top + row sum) of a region declared before it."""
    body = root.find(_q("Body"))
    items = list(body.find(_q("ReportItems")))
    out = []
    for i, blk in enumerate(items):
        if blk.tag != _q("Tablix") \
                or not (blk.get("Name") or "").startswith("Tablix_Breakdown"):
            continue
        for reg in items[:i]:
            if reg.tag != _q("Tablix"):
                continue
            if _in(blk, "Top") < _in(reg, "Top") + _row_sum(reg) - 0.005:
                out.append((blk.get("Name"), reg.get("Name")))
    return out


# ---------------------------------------------------------------- non-vacuity

def test_fixture_really_emits_two_stacked_breakdown_blocks(bdx_root):
    """If a builder change ever stops emitting these, every gate below goes
    vacuous — so it fails here instead of passing silently."""
    blocks = _blocks(bdx_root)
    assert len(blocks) == 2, \
        f"fixture must emit two breakdown blocks, got {len(blocks)}"
    assert all(len(list(b.iter(_q("Textbox")))) for b in blocks), \
        "a breakdown block with no member box gates nothing"


def test_fixture_declares_members_that_would_leave_their_span(bdx_root):
    """Non-vacuity for the horizontal gate: without the clip these exact
    members END OUTSIDE their column. Asserted through the production helper,
    so the fixture and the rule can never drift apart."""
    spans = {b.get("Name"): _column_span(b) for b in _blocks(bdx_root)}
    # the 0.15in member, floored to the emitter's 0.30in minimum, inside the
    # 1.15in span at Left 0.95in
    assert rdl_gen._clip_to_span(0.95, 0.30, spans["Tablix_Breakdown_0"]) \
        == pytest.approx(0.20)
    # the 1.50in member at Left 0.10in inside the page-edge-clamped 1.55in span
    assert rdl_gen._clip_to_span(0.10, 1.50, spans["Tablix_Breakdown_1"]) \
        == pytest.approx(1.45)


def test_fixture_really_declares_a_short_main_region_height(bdx_root):
    """Non-vacuity for the vertical gate: it only bites while the region
    above the blocks has rows summing PAST its declared <Height>. If a
    builder change ever makes the two agree, this fails and the gate must be
    re-aimed, not deleted."""
    body = bdx_root.find(_q("Body"))
    regions = [t for t in body.find(_q("ReportItems"))
               if t.tag == _q("Tablix")
               and not (t.get("Name") or "").startswith("Tablix_Breakdown")]
    assert regions, "fixture must emit a main data region"
    assert any(_row_sum(r) > _in(r, "Height") + 0.15 for r in regions), (
        "fixture no longer exercises the declared-vs-true height gap: "
        + ", ".join(f"{r.get('Name')} rows={_row_sum(r)} "
                    f"declared={_in(r, 'Height')}" for r in regions))


# ------------------------------------------------------------- the two gates

def test_no_breakdown_member_leaves_its_declared_column_span(bdx_root):
    assert _members_past_span(bdx_root) == [], (
        "a breakdown member paints past its column and into whatever is "
        "declared to its right")


def test_every_breakdown_block_clears_the_preceding_regions_reflow_box(
        bdx_root):
    assert _regions_that_interleave(bdx_root) == [], (
        "a breakdown block anchored inside a preceding region's row-sum box "
        "is never reflowed by it — the two grow through each other as rows "
        "arrive")


# --------------------------------------------------------- mutation proofs 1
# The CHECKERS must go red on the exact defect they claim to catch.

def test_span_checker_goes_red_on_a_member_widened_past_its_column(bdx_root):
    doctored = ET.fromstring(ET.tostring(bdx_root))
    blk = _blocks(doctored)[0]
    tbx = next(iter(blk.iter(_q("Textbox"))))
    tbx.find(_q("Width")).text = f"{_column_span(blk) + 0.5:.2f}in"
    flagged = _members_past_span(doctored)
    assert flagged, "the span checker cannot see a member widened past its column"
    assert flagged[0][1] == tbx.get("Name")


def test_interleave_checker_goes_red_on_a_block_pulled_into_the_box_above(
        bdx_root):
    doctored = ET.fromstring(ET.tostring(bdx_root))
    first, second = _blocks(doctored)[:2]
    second.find(_q("Top")).text = \
        f"{_in(first, 'Top') + _row_sum(first) - 0.05:.4f}in"
    flagged = _regions_that_interleave(doctored)
    assert (second.get("Name"), first.get("Name")) in flagged, (
        "the interleave checker cannot see a block pulled inside the reflow "
        f"box of the one above it: {flagged}")


# --------------------------------------------------------- mutation proofs 2
# The PRODUCTION rules must be load-bearing: undone, the report goes red.

def test_the_row_sum_anchor_is_what_keeps_the_blocks_clear(monkeypatch):
    """Anchor the appended block on the region's DECLARED <Height> instead of
    its row sum — the pre-fix rule — and the block lands inside the region
    above it. This is the wild-source defect the hostile-shape rail measured
    (rows=3 clean, rows=25 painted over) reproduced structurally."""
    monkeypatch.setattr(
        rdl_gen, "_region_reflow_height_in",
        lambda el: _in(el, "Height"))
    flagged = _regions_that_interleave(_convert_fixture())
    assert flagged, (
        "the declared-Height anchor must put a block inside the region above "
        "it — if it no longer does, this gate is asleep")


def test_the_clip_is_what_keeps_the_members_inside(monkeypatch):
    monkeypatch.setattr(rdl_gen, "_clip_to_span",
                        lambda left, width, span: width)
    flagged = _members_past_span(_convert_fixture())
    assert len(flagged) == 2, (
        "neutering the clip must put both declared members outside their "
        f"column — got {flagged}")
    assert sorted(o for _, _, o in flagged) == [
        pytest.approx(0.05), pytest.approx(0.10)]


def test_the_clip_never_moves_a_member_that_already_fits(bdx_root):
    """It clips, it does not re-place: a fitting member keeps its declared
    width and every member keeps its declared x."""
    assert rdl_gen._clip_to_span(0.10, 0.40, 1.55) == pytest.approx(0.40)
    lefts = sorted(_in(t, "Left")
                   for b in _blocks(bdx_root) for t in b.iter(_q("Textbox")))
    assert lefts == [pytest.approx(0.0), pytest.approx(0.10),
                     pytest.approx(0.95)]

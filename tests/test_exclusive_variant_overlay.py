"""MUTUALLY EXCLUSIVE VARIANT FRAMES OVERLAY; THEY DO NOT STACK.

Oracle prints ONE of a set of variant frames and collapses the rest to zero
height, so everything under them rides up. SSRS reserves the declared box of
every hidden item inside a rectangle, so emitting the variants at their
design-time y makes the page start below the SUM of all of them (measured on
a truth-paired per-record letter: first ink at 6.085in where the truth starts
at 0.484in).

Guarded here:

* two sibling frames whose declared format triggers test the SAME column for
  DIFFERENT values are emitted at ONE band top, and the band reserves the
  taller variant's height only -- everything below rides up by the rest;
* the exclusivity is PROVEN from the translated conditions. Two frames whose
  conditions are merely different (different columns, or a condition the
  prover cannot read) keep today's stacking -- the safe default;
* an UNCONDITIONAL frame between two variants pins the band: nothing is
  moved across a frame that always prints.

Synthetic fixtures only -- no client data.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.generators.rdl import _conds_exclusive  # noqa: E402

NS = ("{http://schemas.microsoft.com/sqlserver/reporting/"
      "2008/01/reportdefinition}")

PARA_A = ("This notice describes the determination made for the account "
          "shown above.\nRetain it with your records for the current cycle.")
PARA_B = ("Amounts still unpaid after the stated due date accrue interest "
          "at the published rate.\nContact the office with any question.")

# Marker lines used to find each emitted frame rect by its own content.
VAR_A = "Electronic delivery header"
VAR_B = "Printed delivery header"
MARK = "Reference mark"
BODY = "This notice describes the determination"
MARKERS = (VAR_A, VAR_B, MARK, BODY)

_TRIGGER = """    <function name="{name}">
      <textSource>
      <![CDATA[FUNCTION {upper}
RETURN BOOLEAN IS
BEGIN
\tIF {cond} THEN
\t\tRETURN(TRUE) ;
\tELSE
\t\tRETURN(FALSE) ;
\tEND IF ;
END ; ]]>
      </textSource>
    </function>
"""


def _letter_xml(cond_b=":Doc_Type = 'PRINT'", logo_between=False):
    """A per-record letter with two variant header frames.

    ``cond_b`` is the second variant's declared visibility condition;
    ``logo_between`` parks an UNCONDITIONAL frame between the two.
    """
    between = ""
    if logo_between:
        between = """        <frame name="M_MARK">
          <geometryInfo x="0.00000" y="2.10000" width="7.50000" height="0.50000"/>
          <text name="B_MARK">
            <geometryInfo x="0.20000" y="2.20000" width="6.00000" height="0.22000"/>
            <textSegment><font face="Arial" size="10"/>
            <string><![CDATA[Reference mark]]></string></textSegment></text>
        </frame>
"""
    var_b_y = 2.70 if logo_between else 2.10
    triggers = (_TRIGGER.format(name="f_var_a_ft", upper="F_VAR_A_FT",
                                cond=":Doc_Type = 'EMAIL'")
                + _TRIGGER.format(name="f_var_b_ft", upper="F_VAR_B_FT",
                                  cond=cond_b))
    return ("""<?xml version="1.0" encoding="UTF-8"?>
<report name="NOTICE_VARIANTS" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_MAIN">
      <select canParse="no"><![CDATA[SELECT ACCT_NO, DOC_TYPE, OTHER_CD FROM ACCOUNTS]]></select>
      <group name="G_MAIN">
        <dataItem name="ACCT_NO" datatype="vchar2" columnOrder="1" defaultLabel="Account"/>
        <dataItem name="DOC_TYPE" datatype="vchar2" columnOrder="2" defaultLabel="Doc Type"/>
        <dataItem name="OTHER_CD" datatype="vchar2" columnOrder="3" defaultLabel="Other"/>
      </group>
    </dataSource>
  </data>
  <programUnits>
"""
            + triggers
            + """  </programUnits>
  <layout>
  <section name="main" repeatOn="G_MAIN">
    <body width="7.50000" height="9.00000">
      <location x="0.50000" y="0.50000"/>
      <frame name="M_DOC">
        <geometryInfo x="0.00000" y="0.00000" width="7.50000" height="9.00000"/>
        <generalLayout verticalElasticity="variable"/>
        <frame name="M_VAR_A">
          <geometryInfo x="0.00000" y="0.50000" width="7.50000" height="1.50000"/>
          <advancedLayout formatTrigger="f_var_a_ft"/>
          <text name="B_VAR_A">
            <geometryInfo x="0.20000" y="0.60000" width="6.00000" height="0.22000"/>
            <textSegment><font face="Arial" size="10"/>
            <string><![CDATA[Electronic delivery header]]></string></textSegment></text>
        </frame>
"""
            + between
            + f"""        <frame name="M_VAR_B">
          <geometryInfo x="0.00000" y="{var_b_y:.5f}" width="7.50000" height="0.50000"/>
          <advancedLayout formatTrigger="f_var_b_ft"/>
          <text name="B_VAR_B">
            <geometryInfo x="0.20000" y="{var_b_y + 0.10:.5f}" width="6.00000" height="0.22000"/>
            <textSegment><font face="Arial" size="10"/>
            <string><![CDATA[Printed delivery header]]></string></textSegment></text>
        </frame>
        <frame name="M_BODY">
          <geometryInfo x="0.00000" y="{var_b_y + 0.70:.5f}" width="7.50000" height="4.00000"/>
          <text name="B_PARA_ONE">
            <geometryInfo x="0.20000" y="{var_b_y + 0.80:.5f}" width="6.00000" height="0.40000"/>
            <textSegment><font face="Arial" size="10"/>
            <string><![CDATA[{PARA_A}]]></string></textSegment></text>
          <text name="B_PARA_TWO">
            <geometryInfo x="0.20000" y="{var_b_y + 1.60:.5f}" width="6.00000" height="0.40000"/>
            <textSegment><font face="Arial" size="10"/>
            <string><![CDATA[{PARA_B}]]></string></textSegment></text>
          <field name="F_ACCT_NO" source="ACCT_NO">
            <font face="Arial" size="10"/>
            <geometryInfo x="0.20000" y="{var_b_y + 2.40:.5f}" width="3.00000" height="0.20000"/></field>
        </frame>
      </frame>
    </body>
  </section>
  </layout>
</report>""").encode("utf-8")


def _inches(txt):
    try:
        return float((txt or "0in").replace("in", "").strip())
    except ValueError:
        return 0.0


def _rect_tops_by_content(rdl):
    """{needle: (Top, Height)} of the frame rect that DIRECTLY holds each
    marker line. Rect names carry running counters, so anchor on content.
    """
    root = ET.fromstring(rdl)
    cell = None
    for tab in root.iter(f"{NS}Tablix"):
        if tab.get("Name") == "Tablix_Record":
            cell = next(iter(tab.iter(f"{NS}CellContents")), None)
            break
    assert cell is not None, "expected a per-record Tablix"
    out = {}
    for rect in cell.iter(f"{NS}Rectangle"):
        top = rect.find(f"{NS}Top")
        if top is None:
            continue
        geom = (_inches(top.text), _inches(rect.findtext(f"{NS}Height")))
        items = rect.find(f"{NS}ReportItems")
        if items is None:
            continue
        own = " ".join(
            (v.text or "") for tb in items.findall(f"{NS}Textbox")
            for v in tb.iter(f"{NS}Value"))
        for needle in MARKERS:
            if needle in own:
                out.setdefault(needle, geom)
    return out


def _hidden_exprs(rdl):
    return [h.text or "" for h in
            ET.fromstring(rdl).iter(f"{NS}Hidden")]


# --- the overlay ---------------------------------------------------------

def test_exclusive_variants_share_one_band_top():
    """Same column, different declared values -> one band top."""
    tops = _rect_tops_by_content(convert(_letter_xml())["rdl_xml"])
    a = tops.get(VAR_A)
    b = tops.get(VAR_B)
    assert a and b, f"both variants must be emitted, got {sorted(tops)}"
    assert abs(a[0] - b[0]) < 0.01, (
        f"variants emitted at {a[0]}in and {b[0]}in — mutually exclusive "
        "frames must OVERLAY, not stack")
    assert abs(a[0] - 0.50) < 0.03, (
        f"the band moved off the topmost variant's declared 0.50in to {a[0]}in")
    # ...and both keep their own visibility, so exactly one ever prints.
    assert len([h for h in _hidden_exprs(convert(_letter_xml())["rdl_xml"])
                if "DOC_TYPE" in h]) >= 2


def test_band_reserves_one_variant_height_and_body_rides_up():
    """Declared: A 0.50..2.00, B 2.10..2.60, body at 2.80. The band reserves
    A's 1.50in only, so the body rides up by the 0.60in reclaimed."""
    tops = _rect_tops_by_content(convert(_letter_xml())["rdl_xml"])
    body = tops.get(BODY)
    assert body is not None, f"body paragraph must be emitted: {sorted(tops)}"
    assert abs(body[0] - 2.20) < 0.05, (
        f"body emitted at {body[0]}in; declared 2.80in with 0.60in of "
        "reclaimed variant band, so it must print at 2.20in")


# --- the safe defaults ---------------------------------------------------

def test_unprovable_exclusivity_keeps_declared_stacking():
    """Different columns are not exclusive: keep every declared y."""
    rdl = convert(_letter_xml(cond_b=":Other_Cd = 'X'"))["rdl_xml"]
    tops = _rect_tops_by_content(rdl)
    a = tops.get(VAR_A)
    b = tops.get(VAR_B)
    body = tops.get(BODY)
    assert a and b and body
    assert abs(a[0] - 0.50) < 0.03 and abs(b[0] - 2.10) < 0.03, (
        f"unprovable variants moved: {a[0]}in / {b[0]}in — they must keep "
        "their declared stacking")
    assert abs(body[0] - 2.80) < 0.05, (
        f"body moved to {body[0]}in with nothing proven exclusive")


def test_unconditional_frame_between_variants_pins_the_band():
    """A frame with no condition always prints, so nothing may move across
    it — even when the two variants around it ARE exclusive."""
    rdl = convert(_letter_xml(logo_between=True))["rdl_xml"]
    tops = _rect_tops_by_content(rdl)
    a = tops.get(VAR_A)
    b = tops.get(VAR_B)
    mark = tops.get(MARK)
    assert a and b and mark
    assert abs(a[0] - 0.50) < 0.03, f"variant A moved to {a[0]}in"
    assert abs(mark[0] - 2.10) < 0.03, f"the always-printed frame moved to " \
                                       f"{mark[0]}in"
    assert abs(b[0] - 2.70) < 0.03, (
        f"variant B collapsed to {b[0]}in across a frame that always prints")


# --- the prover ----------------------------------------------------------

def test_prover_same_column_different_values():
    assert _conds_exclusive('=Not((Fields!T.Value = "A"))',
                            '=Not((Fields!T.Value = "B"))')


def test_prover_complement():
    assert _conds_exclusive('=Not((Fields!T.Value = "A"))',
                            '=(Fields!T.Value = "A")')


def test_prover_survives_extra_branches():
    """A multi-branch trigger still proves out when every visible path pins
    the same column to its own value."""
    email = ('=Not(IIf(((Parameters!MODE.Value = "YES") And '
             '(UCase(Parameters!SINK.Value) = "X.XML")), False, '
             'IIf((Fields!T.Value = "A"), True, False)))')
    assert _conds_exclusive(email, '=Not((Fields!T.Value = "B"))')


def test_prover_refuses_different_columns():
    assert not _conds_exclusive('=Not((Fields!T.Value = "A"))',
                                '=Not((Fields!U.Value = "B"))')


def test_prover_refuses_identical_conditions():
    assert not _conds_exclusive('=Not((Fields!T.Value = "A"))',
                                '=Not((Fields!T.Value = "A"))')


def test_prover_refuses_unreadable_conditions():
    """An expression the grammar cannot reduce is an UNKNOWN boolean, and an
    unknown can never carry a proof."""
    assert not _conds_exclusive('=Not((Count(Fields!Z.Value, "Q") < 2))',
                                '=Not((Fields!T.Value = "A"))')


def test_prover_refuses_a_never_visible_frame():
    """A frame that can never print is dead content, not a variant."""
    assert not _conds_exclusive("=True", '=Not((Fields!T.Value = "A"))')

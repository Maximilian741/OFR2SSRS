"""Oracle boilerplate &NAME references must RESOLVE, never print as ink.

An Oracle boilerplate `<text>` addresses a declared object with `&NAME`
(a formula column, a user parameter, a summary column). Every emission
site that embeds such a declared caption must therefore hand it to the
established token resolver. Two sites embedded the caption VERBATIM --
the section-direct criteria banner's label and the cross-tab corner
caption -- so the declaration's own token text reached the rendered page
as visible ink ("TOTALS FOR &C_PERIOD OF &P_YEAR" printed literally).

The same repair also covers the aggregate nesting those sites produced:
a hand-built scope-LESS ``First(Fields!X.Value)`` meets the dangling-ref
net, which can only rewrite the REFERENCE, so the dataset argument lands
on a NEW inner aggregate -- ``First(First(Fields!X.Value, "DS"))``. The
outer aggregate has no scope of its own, which is exactly the publish
rejection those nets exist to prevent.

Both fixtures are synthetic and both are asserted to actually REACH the
emission site they guard, so the gate cannot pass by never firing.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.parsers.oracle_xml import parse_oracle_xml  # noqa: E402


# A section-DIRECT criteria banner: a full-width rule, a date field, a
# boilerplate label carrying &tokens, and a value field beside it.
BANNER_SRC = (
    '<?xml version="1.0"?><report name="RB"><data>'
    '<userParameter name="P_YEAR" datatype="character" initialValue="2020"/>'
    '<dataSource name="Q_1">'
    '<select><![CDATA[SELECT area, amt, run_date FROM t]]></select>'
    '<group name="G_1"><dataItem name="AREA" datatype="vchar2"/>'
    '<dataItem name="AMT" datatype="number"/>'
    '<dataItem name="RUN_DATE" datatype="date"/></group>'
    '</dataSource>'
    '<formula name="C_PERIOD" source="c_periodformula" datatype="character"/>'
    '</data><layout><section name="main" width="8.5" height="11">'
    '<body width="8" height="9">'
    '<text name="T_TITLE"><geometryInfo x="1.0" y="0.1" width="6.0" '
    'height="0.3"/><font face="Arial" size="16" bold="yes"/>'
    '<textSegment><string><![CDATA[QUARTERLY SUMMARY]]></string>'
    '</textSegment></text>'
    '<field name="F_D" source="RUN_DATE">'
    '<geometryInfo x="0.2" y="0.6" width="1.5" height="0.2"/></field>'
    '<text name="B_L"><geometryInfo x="2.0" y="0.6" width="3.0" height="0.2"/>'
    '<textSegment><string><![CDATA[TOTALS FOR &C_PERIOD OF &P_YEAR]]>'
    '</string></textSegment></text>'
    '<field name="F_V" source="AREA">'
    '<geometryInfo x="5.5" y="0.6" width="1.5" height="0.2"/></field>'
    '<line name="L_1">'
    '<geometryInfo x="0.2" y="0.9" width="7.0" height="0.01"/></line>'
    '<repeatingFrame name="R_1" source="G_1">'
    '<geometryInfo x="0.2" y="1.5" width="7" height="0.3"/>'
    '<field name="F_A" source="AREA">'
    '<geometryInfo x="0.2" y="1.5" width="3" height="0.2"/></field>'
    '<field name="F_M" source="AMT">'
    '<geometryInfo x="3.4" y="1.5" width="2" height="0.2"/></field>'
    '</repeatingFrame>'
    '</body></section></layout>'
    '<programUnits><function name="c_periodformula"><textSource>'
    "<![CDATA[function C_PERIODFormula return Char is begin "
    "RETURN(TO_CHAR(SYSDATE,'MM')); end;]]>"
    '</textSource></function></programUnits>'
    '</report>'
).encode()


# A frame-ref cross-tab whose row-dimension column is headed by a declared
# boilerplate caption carrying &tokens.
MATRIX_SRC = (
    '<?xml version="1.0"?><report name="MX" DTDVersion="9.0.2.0.10"><data>'
    '<userParameter name="P_UNIT" datatype="character" initialValue="TONS"/>'
    '<dataSource name="Q_1">'
    '<select><![CDATA[SELECT emp_no,remarks,avl FROM v]]></select>'
    '<group name="G_emp"><dataItem name="emp_no" datatype="number"/></group>'
    '<group name="G_rem"><dataItem name="remarks" datatype="vchar2"/></group>'
    '<group name="G_m"><dataItem name="avl" datatype="number"/></group>'
    '<crossProduct name="G_X"><dimension><group name="G_emp"/></dimension>'
    '<dimension><group name="G_rem"/></dimension></crossProduct>'
    '</dataSource>'
    '<formula name="C_UNITTEXT" source="c_unittextformula" '
    'datatype="character"/>'
    '</data><layout><section name="main"><body width="8" height="9">'
    '<text name="T_CAP"><geometryInfo x="0" y="1.75" width="2" height="0.2"/>'
    '<textSegment><string><![CDATA[&P_UNIT &C_UNITTEXT]]></string>'
    '</textSegment></text>'
    '<repeatingFrame name="R_emp" source="G_emp">'
    '<geometryInfo x="2" y="1" width="2" height="0.3"/>'
    '<field name="f_e" source="emp_no">'
    '<geometryInfo x="2" y="1" width="1" height="0.2"/></field>'
    '</repeatingFrame>'
    '<repeatingFrame name="R_rem" source="G_rem">'
    '<geometryInfo x="0" y="2" width="2" height="0.3"/>'
    '<field name="f_r" source="remarks">'
    '<geometryInfo x="0" y="2" width="2" height="0.2"/></field>'
    '</repeatingFrame>'
    '<repeatingFrame name="R_m" source="G_m">'
    '<geometryInfo x="2" y="2" width="1" height="0.3"/>'
    '<field name="f_a" source="avl">'
    '<geometryInfo x="2" y="2" width="1" height="0.2"/></field>'
    '</repeatingFrame>'
    '<matrix name="X_G_X" horizontalFrame="R_emp" verticalFrame="R_rem" '
    'xProductGroup="G_X">'
    '<geometryInfo x="2" y="1" width="3" height="2"/></matrix>'
    '</body></section></layout>'
    '<programUnits><function name="c_unittextformula"><textSource>'
    "<![CDATA[function C_UNITTEXTFormula return Char is begin "
    "RETURN('NET'); end;]]>"
    '</textSource></function></programUnits>'
    '</report>'
).encode()


# A grouped tabular report with a group-header FIELD whose source is a
# declared FORMULA column. That builder hand-writes a scope-LESS
# ``First(Fields!X.Value)``; the formula is not a column of the tablix's
# dataset, so the dangling-ref net rewrites the REFERENCE and the dataset
# argument lands on a new INNER aggregate -- the nesting this guards.
GROUPED_SRC = '''<?xml version="1.0" encoding="UTF-8"?>
<report name="PITCHGT" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_OUTER">
      <select><![CDATA[SELECT BREAK_KEY, G_CODE FROM T_BREAK]]></select>
      <group name="G_OUTER">
        <dataItem name="BREAK_KEY" datatype="vchar2" width="30"
         defaultLabel="Break Key">
          <dataDescriptor expression="BREAK_KEY" order="1" width="30"/>
        </dataItem>
        <dataItem name="G_CODE" datatype="vchar2" width="20"
         defaultLabel="G Code">
          <dataDescriptor expression="G_CODE" order="2" width="20"/>
        </dataItem>
      </group>
    </dataSource>
    <dataSource name="Q_DETAIL">
      <select><![CDATA[SELECT D_ONE, D_TWO, D_THREE, D_FOUR, CS_TALLY
       FROM T_DETAIL]]></select>
      <group name="G_DETAIL">
        <dataItem name="D_ONE" datatype="vchar2" width="30"
         defaultLabel="D One">
          <dataDescriptor expression="D_ONE" order="1" width="30"/></dataItem>
        <dataItem name="D_TWO" oracleDatatype="number" width="10"
         defaultLabel="D Two">
          <dataDescriptor expression="D_TWO" order="2" width="10"/></dataItem>
        <dataItem name="D_THREE" datatype="vchar2" width="10"
         defaultLabel="D Three">
          <dataDescriptor expression="D_THREE" order="3" width="10"/></dataItem>
        <dataItem name="D_FOUR" oracleDatatype="number" width="10"
         defaultLabel="D Four">
          <dataDescriptor expression="D_FOUR" order="4" width="10"/></dataItem>
        <dataItem name="CS_TALLY" oracleDatatype="number" width="10"
         defaultLabel="Cs Tally">
          <dataDescriptor expression="CS_TALLY" order="5" width="10"/></dataItem>
      </group>
    </dataSource>
    <formula name="CF_BAND_NOTE" source="cf_band_noteformula" datatype="character"/>
    <summary name="CS_ALL_TALLY" source="CS_TALLY" function="sum" width="20"
     reset="report" compute="report" columnFlags="8"/>
  </data>
  <layout>
  <section name="main">
    <body height="9.6">
      <frame name="M_BODY">
        <geometryInfo x="0.00" y="0.00" width="7.50" height="2.00"/>
        <generalLayout verticalElasticity="variable"/>
      <repeatingFrame name="R_OUTER" source="G_OUTER" printDirection="down">
        <geometryInfo x="0.20" y="0.30000" width="7.20"
         height="1.60"/>
        <generalLayout verticalElasticity="variable"/>
        <field name="F_KEY" source="BREAK_KEY" alignment="start">
          <font face="Arial" size="10"/>
          <geometryInfo x="0.20" y="0.30000" width="3.00"
           height="0.19"/>
        </field>
        <field name="F_NOTE" source="CF_BAND_NOTE" alignment="start">
          <font face="Arial" size="10"/>
          <geometryInfo x="3.40" y="0.30000" width="2.00" height="0.19"/>
        </field>
        <field name="F_GCODE" source="G_CODE" alignment="end">
          <font face="Arial" size="10"/>
          <geometryInfo x="6.50" y="0.55000" width="0.70"
           height="0.19"/>
        </field>

        <text name="B_CAP_ONE" minWidowLines="1">
          <textSettings spacing="single"/>
          <geometryInfo x="0.20" y="0.80" width="1.40" height="0.19"/>
          <textSegment><font face="Arial" size="10"/>
          <string><![CDATA[Cap One]]></string></textSegment>
        </text>
        <text name="B_CAP_TWO" minWidowLines="1">
          <textSettings spacing="single"/>
          <geometryInfo x="1.80" y="0.80" width="1.20" height="0.19"/>
          <textSegment><font face="Arial" size="10"/>
          <string><![CDATA[Cap Two]]></string></textSegment>
        </text>
        <text name="B_CAP_THREE" minWidowLines="1">
          <textSettings spacing="single"/>
          <geometryInfo x="3.20" y="0.80" width="1.20" height="0.19"/>
          <textSegment><font face="Arial" size="10"/>
          <string><![CDATA[Cap Three]]></string></textSegment>
        </text>
        <text name="B_CAP_FOUR" minWidowLines="1">
          <textSettings spacing="single"/>
          <geometryInfo x="4.60" y="0.80" width="0.90" height="0.19"/>
          <textSegment><font face="Arial" size="10"/>
          <string><![CDATA[Cap Four]]></string></textSegment>
        </text>

        <repeatingFrame name="R_DETAIL" source="G_DETAIL"
         printDirection="down">
          <geometryInfo x="0.20" y="1.00" width="7.20"
           height="0.17000"/>
          <generalLayout verticalElasticity="variable"/>
          <field name="F_D_ONE" source="D_ONE" alignment="start">
            <font face="Arial" size="10"/>
            <geometryInfo x="0.20" y="1.00" width="1.40" height="0.19"/>
          </field>
          <field name="F_D_TWO" source="D_TWO" alignment="end">
            <font face="Arial" size="10"/>
            <geometryInfo x="1.80" y="1.00" width="1.20" height="0.19"/>
          </field>
          <field name="F_D_THREE" source="D_THREE" alignment="center">
            <font face="Arial" size="10"/>
            <geometryInfo x="3.20" y="1.00" width="1.20" height="0.19"/>
          </field>
          <field name="F_D_FOUR" source="D_FOUR" alignment="end">
            <font face="Arial" size="10"/>
            <geometryInfo x="4.60" y="1.00" width="1.20" height="0.19"/>
          </field>
        </repeatingFrame>

        <frame name="M_FOOT">
          <geometryInfo x="0.20" y="1.40" width="7.20" height="0.30"/>
          <text name="B_FOOT" minWidowLines="1">
            <textSettings spacing="single"/>
            <geometryInfo x="4.60" y="1.45" width="1.00" height="0.19"/>
            <textSegment><font face="Arial" size="10"/>
            <string><![CDATA[Foot Line]]></string></textSegment>
          </text>
          <field name="F_TALLY" source="CS_TALLY" alignment="end">
            <font face="Arial" size="10"/>
            <geometryInfo x="5.90" y="1.45" width="1.30" height="0.19"/>
          </field>
        </frame>
      </repeatingFrame>
      </frame>
    </body>
  </section>
  </layout>
</report>'''.encode()


_VALUE_RE = re.compile(r"<Value>(.*?)</Value>", re.S)
# The VB concatenation operator is always emitted as "&amp; " with a
# trailing space, so it can never match: an "&amp;" glued straight onto an
# identifier is the Oracle token form and nothing else.
_AMP_TOKEN_RE = re.compile(r"&amp;([A-Za-z_][A-Za-z0-9_]*)")
_NESTED_AGG_RE = re.compile(
    r"\b(?:First|Last|Sum|Avg|Min|Max|Count|CountDistinct|CountRows|StDev|"
    r"StDevP|Var|VarP)\(\s*(?:First|Last|Sum|Avg|Min|Max|Count|"
    r"CountDistinct|CountRows|StDev|StDevP|Var|VarP)\("
)


def _declared_names(src: bytes) -> set:
    """Every identifier the SOURCE declares as an addressable object."""
    rep = parse_oracle_xml(src)
    names = {(i.name or "").upper()
             for q in (rep.queries or []) for i in (q.items or [])}
    names |= {(p.name or "").upper() for p in (rep.parameters or [])}
    names |= {(f.name or "").upper()
              for f in (getattr(rep, "formulas", None) or [])}
    for p in (rep.parameters or []):
        u = (p.name or "").upper()
        for pre in ("P_", "PARM_"):
            if u.startswith(pre) and u[len(pre):]:
                names.add(u[len(pre):])
    return names


def _raw_declared_tokens(src: bytes, rdl: str) -> list:
    """Literal &NAME references that survived into a <Value>, naming a
    DECLARED object. This is the whole-artifact probe, portable."""
    declared = _declared_names(src)
    hits = []
    for value in _VALUE_RE.findall(rdl):
        for m in _AMP_TOKEN_RE.finditer(value):
            if m.group(1).upper() in declared:
                hits.append((m.group(1), value[:160]))
    return hits


def test_banner_label_resolves_its_tokens_instead_of_printing_them():
    rdl = convert(BANNER_SRC)["rdl_xml"]
    # the guarded emission site must actually be exercised
    assert 'Name="Tb_BannerCriteria"' in rdl, \
        "fixture no longer reaches the criteria-banner emission site"
    banner = re.search(
        r'<Textbox Name="Tb_BannerCriteria"\s*>.*?<Value>(.*?)</Value>',
        rdl, re.S)
    assert banner is not None
    value = banner.group(1)
    # the declared parameter and the declared formula both resolve
    assert "Parameters!P_YEAR.Value" in value, value
    assert "&amp;C_PERIOD" not in value and "&amp;P_YEAR" not in value, value
    assert not _raw_declared_tokens(BANNER_SRC, rdl), \
        _raw_declared_tokens(BANNER_SRC, rdl)[:4]


def test_crosstab_corner_caption_resolves_its_tokens():
    rdl = convert(MATRIX_SRC)["rdl_xml"]
    assert 'Name="Mx_Corner"' in rdl, \
        "fixture no longer reaches the cross-tab corner emission site"
    corner = re.search(
        r'<Textbox Name="Mx_Corner"\s*>.*?<Value>(.*?)</Value>', rdl, re.S)
    assert corner is not None
    value = corner.group(1)
    assert "Parameters!P_UNIT.Value" in value, value
    assert "&amp;P_UNIT" not in value and "&amp;C_UNITTEXT" not in value, value
    assert not _raw_declared_tokens(MATRIX_SRC, rdl), \
        _raw_declared_tokens(MATRIX_SRC, rdl)[:4]


def test_no_scopeless_aggregate_is_nested_around_a_scoped_one():
    for src in (BANNER_SRC, MATRIX_SRC, GROUPED_SRC):
        rdl = convert(src)["rdl_xml"]
        found = _NESTED_AGG_RE.findall(rdl)
        assert not found, found[:4]
    # ...and the grouped fixture must still REACH the producer: a group-header
    # box bound to a formula column, i.e. a scope-less aggregate the nets
    # then have to scope. Without this the test could pass by never firing.
    grouped = convert(GROUPED_SRC)["rdl_xml"]
    assert re.search(
        r'<Textbox Name="Tb_GH_\d+"\s*>.*?<Value>=First\('
        r'Fields!CF_BAND_NOTE\.Value, "DS_REPORT_FORMULAS"\)</Value>',
        grouped, re.S), "fixture no longer reaches the scope-less aggregate"


def test_nested_aggregate_collapse_is_narrow():
    """The collapse must drop ONLY a redundant First/Last wrapper around an
    aggregate that already names its scope. Every other shape -- a scope-less
    inner call, a non-idempotent outer function, an outer call that takes its
    own scope, a plain field reference -- must survive untouched, or the pass
    would silently change what a report computes."""
    import xml.etree.ElementTree as ET
    from converter.generators import rdl as R

    def run(expr: str) -> str:
        root = ET.Element(R._q("Report"))
        v = R._sub(root, "Value", expr)
        R._collapse_nested_scopeless_aggregates(root)
        return v.text

    # collapses (positive)
    assert run('=First(First(Fields!X.Value, "DS"))') \
        == '=First(Fields!X.Value, "DS")'
    assert run('="a" & Last(Sum(Fields!X.Value, "DS")) & "b"') \
        == '="a" & Sum(Fields!X.Value, "DS") & "b"'
    # survives (negative) -- each of these would be a behaviour change
    for keep in (
        '=First(First(Fields!X.Value))',        # inner has no scope
        '=Sum(First(Fields!X.Value, "DS"))',    # Sum over a scalar is not a no-op
        '=First(First(Fields!X.Value, "DS"), "DS2")',   # outer takes a scope
        '=First(Fields!X.Value, "DS")',         # nothing to collapse
        '=First(Fields!X.Value)',               # left for the scope nets
        '=First(First(Fields!X.Value, "DS") + 1)',      # more than one term
        '=First(Lookup(Fields!A.Value, Fields!B.Value, Fields!C.Value, "DS"))',
    ):
        assert run(keep) == keep, keep


# ---------------------------------------------------------------------------
# THE OTHER HALF OF THE HONEST PATH.
#
# "Never printed raw" is only half of it. A declared token that nothing can
# satisfy is emitted as =Nothing, and in the rendered PDF a caption that was
# BLANKED and a caption that was never declared look exactly the same -- so
# without a report of it the operator is never told a declaration was
# dropped. Each such blank must surface as a named finding: which token,
# which object, and what to supply.
#
# The finding is AMBER on the established scale (BLOCKER = Report Builder
# refuses to open or throws before data; RED = a query/section FAILS at run
# time; AMBER = opens and runs, rough edge): the RDL opens, publishes and
# renders, nothing fails -- only declared text is missing from the page.
#
# The audit is confirmed against the FINISHED document, because resolving is
# not emitting: builders discard results they judge unusable and later nets
# rewrite references, so a resolution-time candidate can name a blank that
# never reaches the page. BANNER_SRC is exactly that case and is used below
# to prove the confirmation step is load-bearing rather than decorative.
# ---------------------------------------------------------------------------

_UNRESOLVABLE_RULE = "rdl.unresolved_token_blank"


def _blank_findings(src: bytes) -> list:
    return [i for i in convert(src)["preflight"].get("issues", [])
            if i.get("rule") == _UNRESOLVABLE_RULE]


def _rdl_blank_count(rdl: str) -> int:
    """How many emitted values actually render (partly) blank."""
    import html
    from converter.generators.rdl import _expr_has_blank_atom
    return sum(1 for v in _VALUE_RE.findall(rdl)
               if _expr_has_blank_atom(html.unescape(v)))


def _two_dataset_src(token: str) -> bytes:
    """A boilerplate caption addressing ``token``.

    &OTHER_COL names a column of a SIBLING, unlinked dataset: nothing in the
    bound scope can satisfy it, so it blanks. &C1 is a column of the bound
    dataset and resolves normally. Same fixture either way, so the only
    variable between the positive and the control is the token itself.
    """
    return (
        '<?xml version="1.0"?><report name="UNRES_T" DTDVersion="9.0.2.0.10">'
        '<data>'
        '<dataSource name="Q_Main">'
        '<select><![CDATA[select c1 from t]]></select>'
        '<group name="G_Main"><dataItem name="C1" datatype="vchar2"/></group>'
        '</dataSource>'
        '<dataSource name="Q_Other">'
        '<select><![CDATA[select other_col from u]]></select>'
        '<group name="G_Other">'
        '<dataItem name="OTHER_COL" datatype="vchar2"/></group>'
        '</dataSource>'
        '</data><layout><section name="main">'
        '<frame name="F1">'
        '<geometryInfo x="0" y="0" width="6.0" height="2.0"/>'
        '<text name="B_Cap">'
        '<geometryInfo x="0" y="0" width="4.0" height="0.2"/>'
        '<textSegment><string><![CDATA[TOTAL FOR ' + token + ']]></string>'
        '</textSegment></text>'
        '<field name="F_C1" source="C1">'
        '<geometryInfo x="0" y="0.4" width="2.0" height="0.2"/></field>'
        '</frame></section></layout></report>'
    ).encode()


def test_unresolvable_token_produces_a_named_finding():
    src = _two_dataset_src("&OTHER_COL")
    out = convert(src)
    # the fixture must really blank something, or the gate is vacuous
    assert _rdl_blank_count(out["rdl_xml"]) == 1, \
        "fixture no longer produces a blanked caption"
    found = _blank_findings(src)
    assert len(found) == 1, found
    f = found[0]
    assert f["severity"] == "AMBER", f
    msg = f["message"]
    assert "OTHER_COL" in msg, msg          # WHICH token
    assert "B_Cap" in msg, msg              # WHICH declared object
    assert "Q_Other" in msg, msg            # WHAT the operator must supply
    # and it is reported, not printed: the token never reaches the page
    assert not _raw_declared_tokens(src, out["rdl_xml"])


def test_resolvable_token_produces_no_finding():
    """The control: identical fixture, a token the bound dataset declares."""
    src = _two_dataset_src("&C1")
    out = convert(src)
    assert _rdl_blank_count(out["rdl_xml"]) == 0, out["rdl_xml"][:400]
    assert _blank_findings(src) == []


def test_unconfirmed_candidates_are_dropped_not_reported():
    """Resolving is not emitting.

    BANNER_SRC's builders DO hand the resolver sources that blank, yet the
    finished RDL carries no blank at all -- the results were discarded or
    repaired downstream. Reporting them would be the cry-wolf gate. Proven
    by switching the confirmation step off: the raw candidates appear, the
    document still has zero blanks, and with confirmation on nothing is
    reported.
    """
    import converter.generators.rdl as R
    from converter.parsers.oracle_xml import parse_oracle_xml

    rep = parse_oracle_xml(BANNER_SRC)
    keep = R._confirm_blank_token_findings
    try:
        R._confirm_blank_token_findings = lambda report, root: None
        rdl = R.generate_rdl(rep)
        unconfirmed = R.blank_token_findings(rep)
    finally:
        R._confirm_blank_token_findings = keep

    assert unconfirmed, \
        "fixture no longer produces unconfirmed blank candidates"
    assert _rdl_blank_count(rdl) == 0, "fixture now really does blank a value"
    assert _blank_findings(BANNER_SRC) == [], \
        "unconfirmed candidates must never reach the operator"


def test_audit_never_names_more_blanks_than_the_document_carries():
    """The honesty invariant, over every fixture in this module."""
    for src in (BANNER_SRC, MATRIX_SRC, GROUPED_SRC,
                _two_dataset_src("&OTHER_COL"), _two_dataset_src("&C1")):
        out = convert(src)
        n = len([i for i in out["preflight"].get("issues", [])
                 if i.get("rule") == _UNRESOLVABLE_RULE])
        carried = _rdl_blank_count(out["rdl_xml"])
        assert n <= carried, (n, carried)


def test_blank_atom_detector_ignores_the_word_inside_a_literal():
    """A caption whose INK contains the word must never read as a blank."""
    from converter.generators.rdl import _expr_has_blank_atom

    assert _expr_has_blank_atom('="TOTAL FOR " & Nothing')
    assert _expr_has_blank_atom("=Nothing")
    assert not _expr_has_blank_atom('="Nothing to report"')
    assert not _expr_has_blank_atom('=Fields!Nothing_Left.Value')
    assert not _expr_has_blank_atom('="a" & Fields!X.Value')


# ---------------------------------------------------------------------------
# THE VERDICT CONTRACT: a blanked caption is NOT informational
# ---------------------------------------------------------------------------
#
# When this finding landed it was described as informational and
# verdict-neutral. It is neither, and it was measured: over the 346 sources
# the campaign converts, 11 move READY -> AMBER because of it, and 8 of those
# are the agency's own production reports. Every one of the 8 was then opened
# against the customer's OWN rendered truth (their Oracle PDFs and front-end
# screenshots) and 7 of them print visible text exactly where the conversion
# now prints nothing -- a criteria subtitle, a "run by" identity, a licence
# holder's name, a letter's closing paragraph and its signature block. The
# 8th has no truth render at all, and its tokens are the same two families.
#
# So the contract statement was wrong, not the behaviour: declared text that
# the reader will not find on the page is a fidelity defect the operator must
# be told about, and AMBER is the level the scale already defines for it (the
# RDL opens, publishes and renders; no query or section fails). These tests
# pin that, so nobody can quietly demote the finding back to a note.


def test_a_blanked_caption_moves_the_verdict_off_ready():
    """The contract, as an A/B on one fixture.

    Same source, one variable -- whether the caption's token can be satisfied.
    Resolvable: READY. Unresolvable: AMBER, and the finding is the ONLY issue
    in the list, so nothing else can be what moved it.
    """
    unresolvable = convert(_two_dataset_src("&OTHER_COL"))["preflight"]
    control = convert(_two_dataset_src("&C1"))["preflight"]

    assert control["verdict"] == "READY", control.get("issues")
    assert unresolvable["verdict"] == "AMBER", unresolvable.get("issues")
    others = [i for i in unresolvable.get("issues", [])
              if i.get("rule") != _UNRESOLVABLE_RULE]
    assert not others, \
        f"the fixture grew another finding, so this A/B proves nothing: {others}"


def test_mutation_silencing_the_finding_puts_the_verdict_back_to_ready():
    """MUTATION PROOF that the finding is what moves the verdict.

    Switch the audit off at its source and re-convert the SAME bytes: the
    document still blanks its caption, the reader still loses the declared
    text, and the banner goes back to saying READY. That is precisely the
    silent outcome the finding exists to end.
    """
    import converter.generators.rdl as R

    src = _two_dataset_src("&OTHER_COL")
    assert convert(src)["preflight"]["verdict"] == "AMBER"

    keep = R.blank_token_findings
    try:
        R.blank_token_findings = lambda report: []
        muted = convert(src)
    finally:
        R.blank_token_findings = keep

    assert muted["preflight"]["verdict"] == "READY", \
        "the mutation did not reopen the hole, so this proves nothing"
    assert _rdl_blank_count(muted["rdl_xml"]) == 1, \
        "the document must still be blanking the caption under the mutation"

"""A record's trailing band stays with its record — no sheet of sign-offs.

An agency letter printed 5 of 6 sheets at 25 rows, and 1 of 2 at one row and
at three, carrying nothing but the word "Sincerely," and the page footer. The
blank measure rightly does NOT flag those sheets: a sheet with one real word
is not blank. "Technically not blank" and "a letter your agency would mail"
are different standards, and this file holds the second one.

TWO INDEPENDENT CAUSES, one per net, each general and each proved here.

  (1) THE ALREADY-RENDERED TEST WAS ELEMENT-TYPE-SENSITIVE.
      ``_emit_secondary_breakdown_tables`` rescues a repeating frame whose
      dataset no data region renders, and reads "the body already pulls this
      dataset" off the scope name quoted in a body expression
      (``First(Fields!X.Value, "Q_DS")``). It scanned ``<Textbox>`` only. The
      Oracle blob idiom emits exactly that expression as an ``<Image><Value>``
      — so ONE declaration shape behaved two opposite ways: a site list the
      body renders through a Textbox was seen and left alone, while a
      signature the body renders through an Image was not, and its
      already-printed sign-off was re-emitted as a trailing per-row block.
      Every extra record then bought a sheet carrying that one word. The
      element type is not part of the declaration, so it cannot be part of
      the test (``_body_scope_datasets``).

  (2) A RESCUE ROW THAT RESOLVES NO PER-ROW DATA IS NOT A BREAKDOWN.
      When every field member declines — a blob, or a source that is not a
      column of this dataset — only the frame's static boilerplate survives
      and the block repeats one identical line per record
      (``_rescue_row_carries_row_data``).

  (3) …and the paper the trailing band left behind: a conditional block's box
      was baked into ``<Body><Height>``. ``_collapse_conditional_body_blocks``
      makes such a block collapse, but it runs AFTER
      ``_trim_body_slack_to_page`` and nothing handed the reclaimed paper back
      — the row collapsed and the body still declared the sheets
      (``_block_may_collapse``). Trimming to the always-printing bottom is
      lossless because ``<Body><Height>`` is a MINIMUM canvas, never a clip.

ROW-COUNT DIMENSION. The defect scales with the data — at 0 rows it is
invisible — which is exactly why every rule here is decided from the
DECLARATION and the emitted row, at conversion time, where no row count
exists. So each is the same answer at 0, 1, 3 and 25 rows by construction,
and the engine proofs render all four to show it.

THREE FIXTURES, because the properties need different shapes and a fixture
that cannot go red proves nothing:
  ``SRC``           signer frame beside a genuinely dropped secondary frame —
                    nets (1) and (2), and the non-vacuity that the rescue
                    pass still does its real job.
  ``SRC_COND``      letter plus a sheet-crossing conditional block — net (3).
  ``SRC_SIGN_ONLY`` signer as the ONLY secondary, so the rescue block is the
                    only thing below the letter. This is what turns the
                    duplication into word-only SHEETS, and it reproduces the
                    agency measurement exactly (5 of 6 at 25 rows, 1 of 2 at
                    3 and at 1, none at 0), so the engine gate is proved able
                    to fail before it is trusted to pass.

Structural assertions always run; the render proofs run wherever the
ReportViewer DLLs are present (tools/renderlab).
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools" / "renderlab"))

from converter import convert  # noqa: E402
from converter.generators import rdl as rdl_gen  # noqa: E402

try:
    from rdl_preview import render_to_pdf  # noqa: E402
    from render import lib_ready  # noqa: E402
    _LIB_OK = bool(lib_ready()) and sys.platform == "win32"
except Exception:  # noqa: BLE001
    _LIB_OK = False

_needs_engine = pytest.mark.skipif(not _LIB_OK,
                                   reason="ReportViewer DLLs not present")

NS = "http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition"


def _q(tag):
    return f"{{{NS}}}{tag}"


# ---------------------------------------------------------------------------
# FIXTURE — the production shape, declared from scratch.
#
# A prose letter whose closing block is a repeating frame over a SIGNER query
# (Q_SIGN). Its members are the sign-off boilerplate, the signature BLOB, and
# a name column belonging to a DIFFERENT query — so the body renders Q_SIGN
# only through an <Image>, and a rescue row over Q_SIGN could resolve nothing
# but the word. A second prose frame makes this a positional packet; Q_WHO is
# a genuine dropped secondary frame, so the rescue pass still has real work
# and none of the gates below can pass vacuously.
# ---------------------------------------------------------------------------
_SIGNOFF = "Sincerely,"
_PARA = ("Thank you for submitting the annual package with the itemized "
         "accounting form; the budget and the breakdown have been reviewed "
         "and approved as submitted this period.")
_ENC = ("Enclosed with this notice is the schedule of fees that applies to "
        "the reporting period, together with the remittance advice and the "
        "filing instructions.")


def _fld(n, s, x, y, w, h):
    return (f'<field name="{n}" source="{s}"><geometryInfo x="{x}" y="{y}" '
            f'width="{w}" height="{h}"/></field>')


def _txt(n, t, x, y, w, h):
    return (f'<text name="{n}"><geometryInfo x="{x}" y="{y}" width="{w}" '
            f'height="{h}"/><textSegment><string><![CDATA[{t}]]></string>'
            f'</textSegment></text>')


SRC = (
    '<?xml version="1.0"?><report name="SIGX" DTDVersion="9.0.2.0.10"><data>'
    '<dataSource name="Q_MAIN"><select><![CDATA[select code, nm from t]]>'
    '</select><group name="G_MAIN"><dataItem name="CODE" datatype="vchar2"/>'
    '<dataItem name="NM" datatype="vchar2"/></group></dataSource>'
    '<dataSource name="Q_SIGN"><select><![CDATA[select sig_img from s]]>'
    '</select><group name="G_SIGN">'
    '<dataItem name="SIG_IMG" datatype="blob" oracleDatatype="binLob" '
    'fileFormat="image"/></group></dataSource>'
    '<dataSource name="Q_WHO"><select><![CDATA[select who from w]]></select>'
    '<group name="G_WHO"><dataItem name="WHO" datatype="vchar2"/>'
    '</group></dataSource></data>'
    '<layout><section name="main"><body height="9.0">'
    '<frame name="M_LET"><geometryInfo x="0.05" y="0.2" width="7.4" '
    'height="8.0"/>'
    + _txt("B_BODY", _PARA, 0.1, 0.3, 6.0, 0.2)
    + '<repeatingFrame name="R_WHO" source="G_WHO" minWidowRecords="1" '
      'columnMode="no"><geometryInfo x="0.1" y="1.0" width="6.0" '
      'height="2.0"/>'
    + _fld("F_WHO", "WHO", 0.1, 1.0, 3.0, 0.2)
    + '<repeatingFrame name="R_SIGN" source="G_SIGN" minWidowRecords="1" '
      'columnMode="no"><geometryInfo x="0.1" y="1.4" width="6.0" '
      'height="1.6"/>'
    + _txt("B_SIGNOFF", _SIGNOFF, 0.1, 1.4, 0.7, 0.2)
    + _fld("F_SIG", "SIG_IMG", 0.1, 1.7, 3.0, 0.9)
    + _fld("F_WHO2", "WHO", 0.1, 2.7, 4.2, 0.3)
    + '</repeatingFrame></repeatingFrame></frame>'
      '<frame name="M_ENC"><geometryInfo x="0.05" y="8.3" width="7.4" '
      'height="0.6"/>'
    + _txt("B_ENC", _ENC, 0.1, 8.4, 4.0, 0.2)
    + '</frame></body></section></layout></report>')


# ---------------------------------------------------------------------------
# SECOND FIXTURE — net (3) alone: a letter plus a CONDITIONAL enclosure block
# a format trigger gates on a column, so its outcome is undecidable at
# conversion time. Declared tall enough that its box crosses the sheet the
# letter ends on, which is the only shape that manufactures a sheet. No
# secondary dataset here, so nothing is anchored below it and the
# always-printing bottom really is the letter's.
# ---------------------------------------------------------------------------
_ENC_LINES = "".join(
    _txt(f"B_E{i}", f"Enclosure schedule line {i} of the attached remittance "
                    f"advice and filing instructions for the period.",
         0.1, round(8.6 + i * 0.5, 2), 6.0, 0.2)
    for i in range(13))

SRC_COND = (
    '<?xml version="1.0"?><report name="CONDX" DTDVersion="9.0.2.0.10"><data>'
    '<dataSource name="Q_MAIN"><select><![CDATA[select code, nm from t]]>'
    '</select><group name="G_MAIN"><dataItem name="CODE" datatype="vchar2"/>'
    '<dataItem name="NM" datatype="vchar2"/></group></dataSource></data>'
    '<layout><section name="main"><body height="9.0">'
    '<frame name="M_LET"><geometryInfo x="0.05" y="0.2" width="7.4" '
    'height="8.0"/>'
    + _txt("B_BODY", _PARA, 0.1, 0.3, 6.0, 0.2)
    + '</frame>'
      '<frame name="M_ENC"><geometryInfo x="0.05" y="8.5" width="7.4" '
      'height="7.0"/><advancedLayout formatTrigger="f_enc_ft"/>'
    + _txt("B_ENC", _ENC, 0.1, 8.5, 6.0, 0.2) + _ENC_LINES
    + '</frame></body></section></layout>'
      '<programUnits><function name="f_enc_ft"><textSource><![CDATA['
      'FUNCTION F_Enc_FT RETURN BOOLEAN IS BEGIN '
      'IF :NM IS NULL THEN RETURN(FALSE); ELSE RETURN(TRUE); END IF; '
      'END;]]></textSource></function></programUnits></report>')


def _convert_cond():
    return convert(SRC_COND.encode(), "condx.xml")["rdl_xml"]


# ---------------------------------------------------------------------------
# THIRD FIXTURE — the production shape with the signer as the ONLY secondary
# dataset, so a rescue block over it is the only thing below the letter. That
# is what turns the duplication into WORD-ONLY SHEETS, and it reproduces the
# agency measurement exactly: with both nets undone this prints 5 word-only
# sheets of 6 at 25 rows, 1 of 2 at three rows and at one, and none at zero.
# The engine gates below run on this, so they can actually go red.
# ---------------------------------------------------------------------------
SRC_SIGN_ONLY = (
    '<?xml version="1.0"?><report name="SIGONLY" DTDVersion="9.0.2.0.10">'
    '<data><dataSource name="Q_MAIN">'
    '<select><![CDATA[select code, nm from t]]></select>'
    '<group name="G_MAIN"><dataItem name="CODE" datatype="vchar2"/>'
    '<dataItem name="NM" datatype="vchar2"/></group></dataSource>'
    '<dataSource name="Q_SIGN"><select><![CDATA[select sig_img from s]]>'
    '</select><group name="G_SIGN"><dataItem name="SIG_IMG" datatype="blob" '
    'oracleDatatype="binLob" fileFormat="image"/></group></dataSource></data>'
    '<layout><section name="main"><body height="9.0">'
    '<frame name="M_LET"><geometryInfo x="0.05" y="0.2" width="7.4" '
    'height="8.0"/>'
    + _txt("B_BODY", _PARA, 0.1, 0.3, 6.0, 0.2)
    + '<repeatingFrame name="R_SIGN" source="G_SIGN" minWidowRecords="1" '
      'columnMode="no"><geometryInfo x="0.1" y="6.4" width="6.0" '
      'height="1.6"/>'
    + _txt("B_SIGNOFF", _SIGNOFF, 0.1, 6.4, 0.7, 0.2)
    + _fld("F_SIG", "SIG_IMG", 0.1, 6.7, 3.0, 0.9)
    + _fld("F_NM2", "NM", 0.1, 7.7, 4.2, 0.3)
    + '</repeatingFrame></frame>'
      '<frame name="M_ENC"><geometryInfo x="0.05" y="8.3" width="7.4" '
      'height="0.6"/>'
    + _txt("B_ENC", _ENC, 0.1, 8.4, 4.0, 0.2)
    + '</frame></body></section></layout></report>')


def _convert_sign_only():
    return convert(SRC_SIGN_ONLY.encode(), "sigonly.xml")["rdl_xml"]


def _convert():
    return convert(SRC.encode(), "sigx.xml")["rdl_xml"]


@pytest.fixture(scope="module")
def rdl_xml():
    return _convert()


@pytest.fixture(scope="module")
def root(rdl_xml):
    return ET.fromstring(rdl_xml)


def _breakdowns(root):
    body = root.find(_q("Body"))
    items = body.find(_q("ReportItems")) if body is not None else None
    return [] if items is None else [
        t for t in items if t.tag == _q("Tablix")
        and (t.get("Name") or "").startswith("Tablix_Breakdown")]


def _breakdown_datasets(root):
    return {b.findtext(_q("DataSetName")) for b in _breakdowns(root)}


# ------------------------------------------------------------- non-vacuity

def test_fixture_renders_the_signer_query_only_through_an_image(root):
    """The whole point of net (1): Q_SIGN's scope is carried by an <Image>
    and by nothing else. If a builder change ever moves that expression into
    a Textbox, the Textbox-only rule would pass here for the wrong reason —
    so this fails instead of going quietly vacuous."""
    body = root.find(_q("Body"))
    carriers = {rdl_gen._local(el.tag) if hasattr(rdl_gen, "_local")
                else el.tag.rsplit("}", 1)[-1]
                for el in body.iter()
                if '"Q_SIGN"' in (el.text or "")}
    assert carriers, "fixture no longer renders Q_SIGN in the body at all"
    assert "Textbox" not in carriers, (
        f"Q_SIGN's scope reached a Textbox ({carriers}) — the fixture no "
        "longer exercises the element-type blindness")


def test_fixture_still_produces_a_real_breakdown_block(root):
    """The rescue pass must still do its job, or every gate here is vacuous:
    Q_WHO is genuinely dropped and its row carries real per-row data."""
    assert _breakdown_datasets(root) == {"Q_WHO"}, (
        f"expected exactly the Q_WHO rescue, got {_breakdown_datasets(root)}")
    who = _breakdowns(root)[0]
    assert any("Fields!" in (el.text or "") for el in who.iter()), \
        "the surviving rescue block carries no per-row data"


# -------------------------------------------------------------- the gates

def test_no_rescue_block_duplicates_a_dataset_the_body_already_renders(root):
    """Net (1). Q_SIGN is rendered in the body, so it gets no rescue."""
    assert "Q_SIGN" not in _breakdown_datasets(root), (
        "a dataset the body already renders was rescued a second time — "
        "the sign-off prints twice and every extra record buys a sheet")


def test_the_sign_off_is_declared_once_and_emitted_once(root):
    """The reader-facing property: the letter's closing word appears exactly
    as often as the source declares it — once."""
    n = sum((el.text or "").count(_SIGNOFF) for el in root.iter())
    assert n == 1, f"the sign-off is emitted {n} times, declared once"


def test_no_emitted_rescue_row_is_pure_static_boilerplate(root):
    """Net (2), stated over every block the pass emits, not just this one."""
    dead = [b.get("Name") for b in _breakdowns(root)
            if not any("Fields!" in (el.text or "") for el in b.iter())]
    assert dead == [], (
        f"{dead} repeat identical static ink once per record — paper bought "
        "in proportion to the row count, carrying no information")


def _height_facts(rdl_text):
    r = ET.fromstring(rdl_text)
    body = r.find(_q("Body"))
    items = body.find(_q("ReportItems"))
    declared = float((body.findtext(_q("Height")) or "0").replace("in", ""))
    fits = rdl_gen._printable_page_height(r)
    every = max((rdl_gen._rdl_inches(el, "Top", 0.0)
                 + rdl_gen._region_reflow_height_in(el) for el in items),
                default=0.0)
    uncond = max((rdl_gen._rdl_inches(el, "Top", 0.0)
                  + rdl_gen._region_reflow_height_in(el)
                  for el in items if not rdl_gen._block_may_collapse(el)),
                 default=0.0)
    return declared, fits, every, uncond


def test_cond_fixture_really_declares_a_sheet_crossing_conditional_block():
    """Non-vacuity for net (3): the conditional block's box must genuinely
    cross the sheet, or the gate below is about nothing."""
    _, fits, every, uncond = _height_facts(_convert_cond())
    assert uncond <= fits < every, (
        f"fixture no longer straddles the sheet: always-prints {uncond}in, "
        f"everything {every}in, sheet {fits}in")


def test_body_height_never_reserves_paper_for_a_block_that_may_not_print():
    """Net (3). <Body><Height> may not exceed the always-printing content
    when that content fits a sheet — the box of a collapsible block is not
    paper the sheet must always reserve."""
    declared, fits, _, uncond = _height_facts(_convert_cond())
    assert declared <= fits, (
        f"body declares {declared}in of paper while everything that always "
        f"prints ends at {uncond}in inside a {fits}in sheet")


# ------------------------------------------------------------ the helpers
# Each rule is a pure function, so it can be shown red on the exact defect.

def test_scope_scan_reads_every_element_that_carries_an_expression():
    """``_body_scope_datasets`` is element-agnostic; the narrow rule it
    replaces sees only one of these three."""
    body = ET.fromstring(
        f'<Body xmlns="{NS}"><ReportItems>'
        '<Textbox Name="a"><Value>=First(Fields!A.Value, "Q_TB")</Value>'
        '</Textbox>'
        '<Image Name="b"><Value>=First(Fields!B.Value, "Q_IMG")</Value>'
        '</Image>'
        '<Rectangle Name="c"><Visibility>'
        '<Hidden>=(First(Fields!C.Value, "Q_VIS") &lt; 0)</Hidden>'
        '</Visibility></Rectangle>'
        '</ReportItems></Body>')
    declared = {"Q_TB", "Q_IMG", "Q_VIS", "Q_UNUSED"}
    assert rdl_gen._body_scope_datasets(body, declared) == \
        {"Q_TB", "Q_IMG", "Q_VIS"}
    # ...and the rule this replaced, computed here, is the defect itself.
    narrow = "".join(v.text or "" for tb in body.iter(_q("Textbox"))
                     for v in tb.iter(_q("Value")))
    assert {d for d in declared if f'"{d}"' in narrow} == {"Q_TB"}, \
        "the narrow rule no longer misses the non-Textbox carriers"


def test_row_data_check_separates_a_breakdown_from_repeated_boilerplate():
    live = ET.fromstring(f'<Rectangle xmlns="{NS}"><ReportItems><Textbox>'
                         '<Value>=Fields!N.Value</Value></Textbox>'
                         '</ReportItems></Rectangle>')
    dead = ET.fromstring(f'<Rectangle xmlns="{NS}"><ReportItems><Textbox>'
                         f'<Value>{_SIGNOFF}</Value></Textbox>'
                         '</ReportItems></Rectangle>')
    assert rdl_gen._rescue_row_carries_row_data(live)
    assert not rdl_gen._rescue_row_carries_row_data(dead)


@pytest.mark.parametrize("hidden,expected", [
    ("false", False),          # a constant is not a condition
    ("true", True),
    ('=(First(Fields!X.Value, "Q") &lt; 0)', True),
])
def test_collapsible_block_is_read_from_the_declaration(hidden, expected):
    for shape in (
            # the format-trigger shape...
            f'<Rectangle xmlns="{NS}"><Visibility><Hidden>{hidden}</Hidden>'
            '</Visibility></Rectangle>',
            # ...and the shape once the collapse pass has wrapped it
            f'<Tablix xmlns="{NS}"><TablixRowHierarchy><TablixMembers>'
            f'<TablixMember><Visibility><Hidden>{hidden}</Hidden>'
            '</Visibility></TablixMember></TablixMembers>'
            '</TablixRowHierarchy></Tablix>'):
        assert rdl_gen._block_may_collapse(ET.fromstring(shape)) is expected


def test_a_toggle_driven_block_is_interactive_not_collapsible():
    el = ET.fromstring(
        f'<Rectangle xmlns="{NS}"><Visibility><Hidden>true</Hidden>'
        '<ToggleItem>Tb</ToggleItem></Visibility></Rectangle>')
    assert rdl_gen._block_may_collapse(el) is False


# --------------------------------------------------------- mutation proofs
# Each production rule must be LOAD-BEARING: undone, the defect returns.

def _narrow_scope_scan(body, declared_ds):
    """The pre-fix rule: Textbox-borne expressions only."""
    exprs = "".join(v.text or "" for tb in body.iter(_q("Textbox"))
                    for v in tb.iter(_q("Value")))
    return {ds for ds in declared_ds if ds and f'"{ds}"' in exprs}


def test_narrowing_the_scan_alone_is_caught_by_the_row_data_net(monkeypatch):
    """Nets (1) and (2) are independent: with the scan narrowed back, the
    Q_SIGN frame reaches the emitter again — and is stopped there, because
    its row resolves nothing but the sign-off."""
    monkeypatch.setattr(rdl_gen, "_body_scope_datasets", _narrow_scope_scan)
    r = ET.fromstring(_convert())
    assert "Q_SIGN" not in _breakdown_datasets(r)
    assert sum((el.text or "").count(_SIGNOFF) for el in r.iter()) == 1


def test_undoing_both_nets_brings_the_sign_off_sheets_back(monkeypatch):
    """Both undone, the production defect reappears exactly as measured: a
    second Q_SIGN block whose only ink is the already-printed sign-off."""
    monkeypatch.setattr(rdl_gen, "_body_scope_datasets", _narrow_scope_scan)
    monkeypatch.setattr(rdl_gen, "_rescue_row_carries_row_data",
                        lambda rect: True)
    r = ET.fromstring(_convert())
    assert "Q_SIGN" in _breakdown_datasets(r), (
        "neither net is load-bearing — the fixture stopped exercising the "
        "defect and the gates above prove nothing")
    dup = [b for b in _breakdowns(r) if b.findtext(_q("DataSetName")) == "Q_SIGN"]
    assert any(_SIGNOFF in (el.text or "") for el in dup[0].iter())
    assert not any("Fields!" in (el.text or "") for el in dup[0].iter()), \
        "the resurrected block carries data, so it is not the word-only shape"


def test_the_collapsible_read_is_what_hands_the_paper_back(monkeypatch):
    """Net (3) load-bearing: treat every block as always-printing — the
    pre-fix measurement — and the body goes back to declaring the paper.

    This proves the DECLARED HEIGHT, which is what the rule controls. The
    height-to-sheet link is engine-measured on the agency letter instead,
    where it is unambiguous: the same artifact renders 2 sheets with the
    body at 21.57in and 1 sheet at the trimmed 9.187in, at every row count,
    with the conditional block suppressed either way. This fixture does not
    reproduce that link — a trailing sheet with no ink at all is dropped by
    the engine here — so it is not asserted from here."""
    baseline, fits, _, _ = _height_facts(_convert_cond())
    monkeypatch.setattr(rdl_gen, "_block_may_collapse", lambda el: False)
    declared = _height_facts(_convert_cond())[0]
    assert baseline <= fits < declared, (
        f"neutering the collapsible read left the body at {declared}in vs "
        f"{baseline}in shipped ({fits}in sheet) — the rule is not what "
        "reclaims the paper")


# ------------------------------------------------------------ engine proof

def _sheet_words(pdf):
    import fitz
    with fitz.open(str(pdf)) as doc:
        return [[w[4] for w in pg.get_text("words")] for pg in doc]


def _word_only_sheets(pdf):
    """1-based sheets whose entire ink is the sign-off — what the reader
    would be asked to mail."""
    out = []
    for i, words in enumerate(_sheet_words(pdf), 1):
        ink = [w for w in words if w.strip()]
        if ink and set(ink) <= {_SIGNOFF}:
            out.append(i)
    return out


def _render(rdl_text, pdf, rows):
    res = render_to_pdf(rdl_text, pdf, rows=rows)
    assert res["ok"], res["log"][-400:]
    return pdf


@_needs_engine
@pytest.mark.parametrize("rows", [0, 1, 3, 25])
def test_no_sheet_carries_only_the_sign_off_at_any_row_count(rows, tmp_path):
    """The reader-facing standard, at every shape the rule can see.

    The defect scaled with the data, so a gate run at one row count would
    have called it clean — the mutation proof below measures this same
    fixture with the nets undone and gets 5 word-only sheets of 6 at 25
    rows, 1 of 2 at 3 and at 1, none at 0."""
    pdf = _render(_convert_sign_only(), tmp_path / f"s{rows}.pdf", rows)
    assert _word_only_sheets(pdf) == [], (
        f"sheets {_word_only_sheets(pdf)} of the rows={rows} render carry "
        f"nothing but {_SIGNOFF!r}")


@_needs_engine
@pytest.mark.parametrize("rows", [0, 1, 3, 25])
def test_the_sign_off_prints_once_per_render(rows, tmp_path):
    """Not merely 'no word-only sheet' — the word itself must not multiply,
    and the sheet count must not track the row count for a one-record
    letter."""
    pdf = _render(_convert_sign_only(), tmp_path / f"n{rows}.pdf", rows)
    sheets = _sheet_words(pdf)
    n = sum(w.count(_SIGNOFF) for sheet in sheets for w in sheet)
    assert n == 1, f"{_SIGNOFF!r} printed {n} times at rows={rows}"
    assert len(sheets) == 1, \
        f"the letter printed {len(sheets)} sheets at rows={rows}"


@_needs_engine
@pytest.mark.parametrize("rows,expect", [(0, 0), (1, 1), (3, 1), (25, 5)])
def test_the_engine_gate_goes_red_on_the_pre_fix_artifact(rows, expect,
                                                          tmp_path,
                                                          monkeypatch):
    """PROVE THE GATE CAN FAIL — and fail for the right reason at every row
    count. With both nets undone this fixture prints the agency defect
    exactly: word-only sheets appear, and their number tracks the data."""
    monkeypatch.setattr(rdl_gen, "_body_scope_datasets", _narrow_scope_scan)
    monkeypatch.setattr(rdl_gen, "_rescue_row_carries_row_data",
                        lambda rect: True)
    pdf = _render(_convert_sign_only(), tmp_path / f"p{rows}.pdf", rows)
    assert len(_word_only_sheets(pdf)) == expect, (
        f"rows={rows}: expected {expect} word-only sheets from the pre-fix "
        f"build, got {_word_only_sheets(pdf)} — the gate is blind to the "
        "defect it claims to catch")

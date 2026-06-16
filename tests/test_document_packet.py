"""Lock the positional-document-packet rendering and the generic field
detectors added during the artifact-fidelity loop.

These guard the real fixes verified against client artifacts (STP_PAYBACK's
memo+table+letter packet, CMVGY/MVWF letter logos & signatures, conditional
*_ERROR fields, CF_NULL blank rules, the report-title resolver) -- but using a
fully SYNTHETIC fixture and duck-typed helper inputs, so no client data lands
in the public repo.
"""
import os
import re

from backend.converter import convert
from backend.converter.parsers.oracle_xml import parse_oracle_xml
from backend.converter.preview import html_mockup as H

FIXTURE = os.path.join(os.path.dirname(__file__),
                       "fixtures", "document_packet", "source.xml")


def _packet_report():
    with open(FIXTURE, "rb") as fh:
        return parse_oracle_xml(fh.read())


# --- the packet (memo + table + letter on three sheets) --------------------

def test_packet_is_detected():
    rep = _packet_report()
    main = H._find_section(rep.layout, "section_main")
    assert [c.name for c in (main.children or [])] == ["M_MEMO", "M_TABLE", "M_LETTER"]
    assert H._is_positional_document_packet(rep) is True


def test_packet_frames_carry_page_break_after():
    # The parser must capture Oracle pageBreakAfter on the memo + table frames
    # (the packet page-split signal); the closing letter has none. This is the
    # data the RDL generator needs to paginate the packet onto three sheets.
    rep = _packet_report()
    main = H._find_section(rep.layout, "section_main")
    frames = {c.name: c for c in (main.children or [])}
    assert frames["M_MEMO"].page_break_after is True
    assert frames["M_TABLE"].page_break_after is True
    assert frames["M_LETTER"].page_break_after is False


def test_packet_renders_three_pages_with_tiled_table():
    rep = _packet_report()
    html = H.render_mockup(rep, mode="frontend")
    # one sheet per top-level frame: memo / table / letter
    assert len(re.findall(r">Page \d", html)) == 3
    # the embedded repeating frame tiles into MANY rows (not one scattered row)
    assert html.count("North District") + html.count("South District") >= 6
    # the $NNN,NN0.00 amount column renders as currency cells
    assert html.count("$") >= 6
    # the 18pt underlined "Memo" heading keeps its underline + large size
    assert "text-decoration:underline" in html


def test_packet_does_not_fabricate_a_run_info_cover():
    # The tabular template invents a "Run By / Total of ALL Records" cover; a
    # packet must render its real memo/letter prose instead.
    res = convert(open(FIXTURE, "rb").read())
    assert res["conversion_error"] is None
    assert res["fidelity_report"]["score"] == 1.0
    assert "Total of ALL Records" not in res["mockup_html"]
    assert "regional rebate listing" in res["mockup_html"]  # the real memo body


def test_packet_rdl_keeps_all_sections_and_paginates():
    """The packet RDL must contain ALL THREE sections' content -- the memo, the
    table, AND the closing letter (the old cover+tablix path LOST the prose
    frames) -- and carry a PageBreak for each pageBreakAfter frame. STRUCTURAL
    ONLY: the SSRS render engine is blocked in this environment, so the exact
    pagination is confirmed by uploading the .rdl, not asserted here."""
    res = convert(open(FIXTURE, "rb").read())
    rdl = res["rdl_xml"]
    assert res["conversion_error"] is None
    errs = [i for i in (res.get("rdl_issues") or []) if i.get("severity") == "error"]
    assert not errs, f"RDL has structural errors: {errs}"
    assert "regional rebate listing" in rdl    # memo body prose
    assert "No. of Items" in rdl                 # table column header
    assert "rebate due to your region" in rdl    # closing-letter body prose
    # pageBreakAfter on the memo + table frames -> at least two page breaks
    assert rdl.count("<PageBreak") >= 2


# --- generic field detectors (duck-typed inputs) ---------------------------

class _It:
    def __init__(self, name, datatype="vchar2"):
        self.name, self.datatype = name, datatype


class _Q:
    def __init__(self, items):
        self.items = items


class _Rep:
    def __init__(self, queries=None, formulas=None):
        self.queries, self.formulas = queries, formulas


def test_image_source_detection_and_false_positive_guard():
    rep = _Rep(queries=[_Q([
        _It("DEQ_LOGO", "blob"), _It("SIGNATURE", "binLob"),
        _It("BADGE_NAME", "vchar2"), _It("REGION_NAME", "vchar2"),
    ])])
    cols = H._image_source_names(rep)
    assert {"DEQ_LOGO", "SIGNATURE"} <= cols
    assert "BADGE_NAME" not in cols and "REGION_NAME" not in cols


def test_conditional_error_source():
    assert H._is_conditional_error_source("CP_PERMITEE_ERROR")
    assert H._is_conditional_error_source("ERR_MSG")
    assert H._is_conditional_error_source("ERROR_CODE")
    for safe in ("OPERATOR", "VENDOR", "TERMS", "PREFERRED", "NUMBER", "REGION_NAME"):
        assert not H._is_conditional_error_source(safe)


class _Formula:
    def __init__(self, name, plsql_body):
        self.name, self.plsql_body = name, plsql_body


def test_blank_formula_detection():
    blanks = H._blank_formula_literals(_Rep(formulas=[
        _Formula("CF_NULL",
                 "function CF_NULLFormula return Char is\nbegin\n  RETURN '      ';\nend;"),
        _Formula("CF_SYSDATE",
                 "function CF_SYSDATEFormula return Char is\nbegin\n"
                 "  RETURN TO_CHAR(SYSDATE,'fmMonth DD, YYYY');\nend;"),
    ]))
    assert "CF_NULL" in blanks            # constant whitespace return -> blank rule
    assert "CF_SYSDATE" not in blanks     # computes a value -> not blanked


def test_doc_cell_value_formats():
    assert H._doc_cell_value("REBATE_AMT", 0, "$NNN,NN0.00").startswith("$")
    assert H._doc_cell_value("REBATE_COUNT", 0, "").isdigit()

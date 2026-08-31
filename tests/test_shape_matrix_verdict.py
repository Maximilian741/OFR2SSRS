"""THE CLI SHAPE MATRIX MAY NOT CHANGE ITS MIND WITH THE ROW COUNT.

``tools/renderlab/shape_matrix.py`` is the command-line half of the hostile
data-shape rail, and its blank column carried the same hole the shared measure
did — in a second, independent copy:

    if blanks and shape == 0 and _says_no_data(pdf):   # NOT a failure
    elif blanks:                                       # failure

One blank sheet was a FAILURE at one, three and twenty-five rows and a PASS at
zero. Zero rows is the shape the tool exists for — a filter that matches
nothing is the commonest production shape — so the exemption switched the
column off exactly where it mattered.

And the exemption was decided by ``"no data was returned" in text.lower()``:
an English literal, matched against extracted text. A report whose own
``NoRowsMessage`` says anything else never qualified, and the identical
artifact in Greek, Cyrillic, Arabic or CJK could not qualify even when it
printed its notice perfectly — ReportViewer embeds those subsets with no
ToUnicode CMap, so not one glyph decodes and the phrase can never be found.

Both halves are pinned here: the verdict takes no row count at all, and the
notice is read from the ARTIFACT in whatever language it is written.
"""
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools" / "renderlab"))

SHAPES = (0, 1, 3, 25)
MODULE = ROOT / "tools" / "renderlab" / "shape_matrix.py"


def _sm():
    """Import the module under test.

    It pulls in the converter and the render harness at import time, so a
    machine without them skips rather than errors — the same contract the
    other renderlab-facing tests use."""
    try:
        import shape_matrix  # type: ignore
    except Exception as exc:  # noqa: BLE001  (missing engine/deps)
        pytest.skip(f"shape_matrix not importable here: {exc}")
    return shape_matrix


# ---------------------------------------------------------------------------
# THE ROW COUNT IS NOT AN ARGUMENT
# ---------------------------------------------------------------------------

def test_the_verdict_function_cannot_see_the_row_count():
    """Structural, so the hole cannot come back by a different route.

    A verdict that never receives the row count cannot depend on it. The
    historical rule read ``shape == 0`` inline in the render loop, which is
    why nothing could pin it — this test pins the shape of the decision, not
    just one of its answers."""
    import inspect

    sm = _sm()
    params = list(inspect.signature(sm.shape_verdict).parameters)
    assert params == ["blanks", "prints_notice"], (
        "shape_verdict must decide on the SHEET (does it carry blanks) and "
        f"the ARTIFACT (does the report print its declared notice): {params}")


def test_the_same_sheet_gets_the_same_verdict_at_every_row_count():
    """The property the old rule broke, asserted over the whole input space.

    Four row counts, every combination of the two things the verdict is
    allowed to see. The answer must be one answer."""
    sm = _sm()
    for blanks in ([], [1], [2, 3]):
        for notice in (False, True):
            verdicts = {shape: sm.shape_verdict(blanks, notice)
                        for shape in SHAPES}
            assert len(set(verdicts.values())) == 1, (
                f"blanks={blanks} notice={notice} produced different verdicts "
                f"by row count: {verdicts}")


def test_a_blank_sheet_is_a_failure_even_when_the_report_printed_its_notice():
    """The demotion is gone, and the information it carried is not.

    A report that told the reader nothing came back and then printed an empty
    sheet is still a report with an empty sheet. The notice is reported — the
    label says so — but it no longer cancels the finding."""
    sm = _sm()
    plain_label, plain_failed = sm.shape_verdict([1], False)
    notice_label, notice_failed = sm.shape_verdict([1], True)
    assert plain_failed is True and notice_failed is True, (
        "a blank sheet is a failure with or without the notice: "
        f"{plain_label}/{plain_failed} {notice_label}/{notice_failed}")
    assert notice_label != plain_label, (
        "...and the notice must still be VISIBLE in the cell, or the tool "
        "loses the distinction it was trying to make")
    assert sm.shape_verdict([], True) == ("ok", False)
    assert sm.shape_verdict([], False) == ("ok", False)


def test_reinstating_the_zero_row_exemption_breaks_the_invariance_property():
    """MUTATION PROOF — the rule as it stood, run against the same property.

    If this ever passes, the property test above is decorative."""
    def historical(shape, blanks, says_no_data):
        if blanks and shape == 0 and says_no_data:
            return "no-data+tail", False
        if blanks:
            return "BLANK", True
        return "ok", False

    verdicts = {shape: historical(shape, [1], True) for shape in SHAPES}
    assert len(set(verdicts.values())) > 1, (
        "the mutation must reproduce the reported hole — one sheet, two "
        f"verdicts, chosen by the row count: {verdicts}")
    assert verdicts[0][1] is False and verdicts[3][1] is True, (
        f"...and the zero-row arm is the one that goes silent: {verdicts}")


# ---------------------------------------------------------------------------
# THE NOTICE IS READ FROM THE ARTIFACT, IN WHATEVER LANGUAGE IT IS WRITTEN
# ---------------------------------------------------------------------------

NS = "http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition"

# Invented wording, one per script family — none of it from any real report.
# Each is the sentence a region would declare as its own no-rows notice.
NOTICES = {
    "ascii": "Nothing matched the filter",
    "accented": "Ningun registro coincide con el filtro",
    "greek": "Κανένα στοιχείο",
    "cyrillic": "Нет данных",
    "arabic": "لا توجد بيانات",
    "hebrew": "אין נתונים",
    "cjk": "該当データなし",
    "hangul": "데이터가 없습니다",
    "thai": "ไม่มีข้อมูล",
}


def _rdl_declaring(notice: str) -> str:
    return (f'<Report xmlns="{NS}"><DataSets/><Body><ReportItems>'
            f'<Tablix Name="T"><NoRowsMessage>{notice}</NoRowsMessage>'
            f'</Tablix></ReportItems></Body></Report>')


@pytest.mark.parametrize("script", sorted(NOTICES))
def test_the_declared_notice_is_recognised_in_every_script(script):
    """Same artifact shape, nine scripts, one behaviour.

    The English literal this replaces could only ever answer YES for one of
    these nine — and, on a real render, not even for that one when the PDF
    subset carries no ToUnicode map."""
    sm = _sm()
    notice = NOTICES[script]
    rdl = _rdl_declaring(notice)
    assert sm._prints_its_declared_notice([notice], rdl) is True, (
        f"{script}: the report declared this sentence and printed it")
    assert sm._prints_its_declared_notice(["Account 0001 4,120.55"], rdl) is False, (
        f"{script}: a data sheet is not the notice")
    assert sm._prints_its_declared_notice([notice], _rdl_declaring("")) is False, (
        f"{script}: an artifact that declares no notice recognises none")


def test_one_report_never_matches_another_report_s_wording():
    """The detector reads THIS artifact, not a vocabulary of notices."""
    sm = _sm()
    a, b = NOTICES["greek"], NOTICES["thai"]
    assert sm._prints_its_declared_notice([a], _rdl_declaring(a)) is True
    assert sm._prints_its_declared_notice([a], _rdl_declaring(b)) is False


def test_no_hardcoded_no_rows_wording_survives_in_the_module():
    """The literal is gone from the source, not just unreachable.

    A wordlist that still exists is a wordlist someone re-wires."""
    src = MODULE.read_text(encoding="utf-8")
    body = "\n".join(
        ln for ln in src.splitlines()
        if not ln.lstrip().startswith("#"))
    # The historical phrase, and the general shape of an English no-rows
    # match, in any string literal outside a comment or docstring.
    for pattern in (r'"[^"\n]*no data[^"\n]*"\.?\s*(?:in|==)',
                    r"\.lower\(\)\s*\n?\s*$"):
        hits = [m.group(0) for m in re.finditer(pattern, body)]
        assert not hits, f"a hardcoded wording test came back: {hits}"
    assert "_says_no_data" not in body, (
        "the English-literal detector must be gone, not renamed around")

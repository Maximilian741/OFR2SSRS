"""Static layout auditor: data-independent clip/overflow detection.

This is the mechanical check that the render-and-eyeball loop lacked. The offline
render substitutes short placeholder data, so a box that clips a real value looks
fine; this auditor inspects the generated RDL geometry directly and flags
``CanGrow=false`` textboxes whose declared content can't fit — the class that
dropped the wallet-card expiration date and clips multi-line letter bodies.
"""
from __future__ import annotations

from converter.validators.layout_audit import audit_layout

NS = "http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition"


def _rdl(textbox_inner: str) -> str:
    return f'<Report xmlns="{NS}"><Body><ReportItems>{textbox_inner}</ReportItems></Body></Report>'


def _tb(name: str, height_in: str, lines, pt: int = 8, cangrow: bool = False) -> str:
    paras = "".join(
        f"<Paragraph><TextRuns><TextRun><Value>{v}</Value>"
        f"<Style><FontSize>{pt}pt</FontSize></Style></TextRun></TextRuns></Paragraph>"
        for v in lines
    )
    cg = f'<CanGrow>{"true" if cangrow else "false"}</CanGrow>'
    return (f'<Textbox Name="{name}">{cg}<Height>{height_in}in</Height>'
            f"<Paragraphs>{paras}</Paragraphs></Textbox>")


def _rules(rdl):
    return {i["rule"] for i in audit_layout(rdl)}


def test_flags_stacked_paragraph_clip():
    """The bug that bit production: "expires" + the date as TWO stacked
    CanGrow=false paragraphs in a one-line (0.18in) box -> the date clips."""
    rdl = _rdl(_tb("Card", "0.18", ['="expires"', "=Fields!Exp_Date.Value"], pt=8))
    issues = audit_layout(rdl)
    assert any(i["rule"] == "layout.height_overflow" and i["item"] == "Card" for i in issues), issues


def test_inline_fit_is_clean():
    """The shipped fix: ONE inline paragraph ("expires " & date) fits the box."""
    rdl = _rdl(_tb("Card", "0.18", ['="expires " &amp; Fields!Exp_Date.Value'], pt=8))
    assert "layout.height_overflow" not in _rules(rdl)


def test_single_tall_line_not_flagged():
    """Calibration lock: a single line of large text fits a box sized to its own
    font (a 24pt title in a 0.34in box does NOT clip). The naive leading*1.2
    formula false-flagged these; the last-line=glyph-height refinement must not."""
    rdl = _rdl(_tb("Title", "0.34", ['="STATE TITLE"'], pt=24))
    assert "layout.height_overflow" not in _rules(rdl), audit_layout(rdl)


def test_growable_box_is_skipped():
    """A CanGrow=true box expands to fit, so overflowing content is NOT a clip."""
    rdl = _rdl(_tb("Body", "0.19", ["=a", "=b", "=c", "=d"], pt=10, cangrow=True))
    assert "layout.height_overflow" not in _rules(rdl)


def test_letter_body_overflow_caught():
    """The latent class: 4 paragraphs of prose in a 0.19in CanGrow=false box
    clips the bulk of the letter with real data, while the placeholder render
    looks fine. The auditor must catch it without any data."""
    rdl = _rdl(_tb("Body", "0.19", ["=p1", "=p2", "=p3", "=p4"], pt=10))
    assert "layout.height_overflow" in _rules(rdl)


def test_vbcrlf_lines_are_counted():
    """vbCrLf joins inside one paragraph add lines: 3 lines @10pt (~0.47in)
    in a 0.30in CanGrow=false box overflow."""
    rdl = _rdl(_tb("Stack", "0.30", ['=a &amp; vbCrLf &amp; b &amp; vbCrLf &amp; c'], pt=10))
    assert "layout.height_overflow" in _rules(rdl)


def test_clean_rdl_has_no_flags():
    """A textbox sized for its content produces no noise."""
    rdl = _rdl(_tb("Ok", "0.50", ["=a", "=b"], pt=10))
    assert audit_layout(rdl) == []


def test_convert_surfaces_layout_flags_as_amber(monkeypatch):
    """The convert() pipeline must merge layout_audit flags into the pre-download
    preflight verdict as NON-BLOCKING AMBER notes -- a clip risk surfaces to the
    user but never blocks a download. Locks the app wiring (a real overflow box
    is data-shape-specific, so we inject a flag and assert it rides out)."""
    import converter
    monkeypatch.setattr(
        converter, "audit_layout",
        lambda _rdl: [{"rule": "layout.height_overflow",
                       "severity": "warning", "message": "Box_X clips"}],
    )
    src = open("tests/fixtures/source_of_truth/letter/source.xml", "rb").read()
    pf = converter.convert(src)["preflight"]
    amber = [i for i in pf["issues"] if i.get("rule") == "layout.height_overflow"]
    assert amber, "layout flag did not surface in preflight.issues"
    assert amber[0]["severity"] == "AMBER", "must be AMBER (non-blocking), never BLOCKER"
    assert pf["verdict"] in ("AMBER", "RED", "BLOCKER"), pf["verdict"]

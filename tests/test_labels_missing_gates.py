"""Guards for the labelsMissing closure work (truth-paired campaign).

Two general defect classes lost declared static text from the verification
render and the emitted RDL:

1. HARNESS GATING (class b — wrong gating): ms_layout's parameter-default
   Hidden evaluator bailed on the deterministic VB string functions the
   Oracle UPPER(TRIM(:param)) trigger dialect compiles to (UCase/Trim), and
   on ANY Fields! token even when the visibility OUTCOME was decidable from
   parameter defaults alone (Fields! parked in an untaken IIf branch).  A
   declared summary block whose parameter default shows it rendered blank —
   37 declared caption fragments on one truth-paired report.

2. CONVERTER MARGIN CHROME (class a — real content loss): a report whose
   declared <margin> band is FOOTER-ONLY (run-date + page number + report
   name along the bottom of the sheet, no header-worthy furniture) lost the
   whole band: _margin_page_chrome only trusted bands with header furniture.
   Per-SECTION margin bands restating the same furniture also doubled every
   footer item, and a single-line literal a hair wider than its declared box
   wrapped its last glyph out of the band.
"""
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools" / "renderlab"))

_NS = "http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition"


def _q(tag):
    return f"{{{_NS}}}{tag}"


# ---------------------------------------------------------------------------
# 1) harness: parameter-decidable Hidden evaluates like a default-param run
# ---------------------------------------------------------------------------

def _mini_rdl_with_hidden(body_hidden: str, header_hidden: str,
                          p_summary_default: str = "YES") -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<Report xmlns="{_NS}">'
        '<Page><PageHeader><Height>0.5in</Height><ReportItems>'
        '<Textbox Name="Tb_Band"><Paragraphs><Paragraph><TextRuns><TextRun>'
        '<Value>band caption</Value></TextRun></TextRuns></Paragraph>'
        '</Paragraphs><Visibility>'
        f'<Hidden>{header_hidden}</Hidden></Visibility>'
        '<Top>0in</Top><Left>0in</Left><Width>2in</Width>'
        '<Height>0.2in</Height></Textbox>'
        '</ReportItems></PageHeader></Page>'
        '<Body><ReportItems>'
        '<Textbox Name="Tb_Body"><Paragraphs><Paragraph><TextRuns><TextRun>'
        '<Value>body caption</Value></TextRun></TextRuns></Paragraph>'
        '</Paragraphs><Visibility>'
        f'<Hidden>{body_hidden}</Hidden></Visibility>'
        '<Top>0in</Top><Left>0in</Left><Width>2in</Width>'
        '<Height>0.2in</Height></Textbox>'
        '</ReportItems><Height>1in</Height></Body><Width>8.5in</Width>'
        '<ReportParameters>'
        '<ReportParameter Name="P_SUMMARY"><DataType>String</DataType>'
        '<DefaultValue><Values>'
        f'<Value>{p_summary_default}</Value>'
        '</Values></DefaultValue></ReportParameter>'
        '<ReportParameter Name="P_STATUS"><DataType>String</DataType>'
        '<DefaultValue><Values><Value>=Nothing</Value></Values>'
        '</DefaultValue></ReportParameter>'
        '</ReportParameters></Report>'
    )


def _hidden_of(rdl_static: str, name: str) -> str:
    root = ET.fromstring(rdl_static.encode("utf-8"))
    tb = next(t for t in root.iter(_q("Textbox")) if t.get("Name") == name)
    return next(h.text or "" for h in tb.iter(_q("Hidden")))


_UCASE_TRIM = ('=Not((UCase(Trim(Parameters!P_SUMMARY.Value)) '
               '= &quot;YES&quot;))').replace("&quot;", '"')
_FIELDS_UNTAKEN = (
    '=Not(IIf(IsNothing(Parameters!P_STATUS.Value), True, '
    'IIf(((Parameters!P_STATUS.Value = "COMPLETE") And '
    '(First(Fields!F_Complete.Value, "DS_X") = 1)), True, False)))')


def test_param_only_ucase_trim_hidden_matches_default_run():
    """The Oracle UPPER(TRIM(:param)) idiom compiles to UCase(Trim(...)) in
    the Hidden expression; the evaluator must resolve it against the
    DECLARED default so the declared block renders exactly when a
    default-parameter server run shows it (measured: 30+ declared summary
    captions on one truth-paired report hid behind this one bail)."""
    from ms_layout import staticize                    # noqa: PLC0415
    shown = staticize(_mini_rdl_with_hidden(
        _UCASE_TRIM, "=False", p_summary_default="YES"))
    assert _hidden_of(shown, "Tb_Body") == "false", (
        "a parameter-only Hidden whose default SHOWS the block must "
        "staticize visible")
    # PROVE THE GATE CAN FAIL: a default that HIDES the block stays hidden.
    hidden = staticize(_mini_rdl_with_hidden(
        _UCASE_TRIM, "=False", p_summary_default="NO"))
    assert _hidden_of(hidden, "Tb_Body") == "true", (
        "a parameter-only Hidden whose default hides the block must stay "
        "hidden")


def test_fields_in_untaken_branch_decides_in_band_but_not_in_body():
    """A Fields! term parked in an UNTAKEN IIf branch does not make the
    visibility outcome data-dependent: with the =Nothing parameter default
    the IsNothing() branch decides.  That verdict applies inside page
    bands / cover rects.  In the BODY the always-hide rule stands even for
    a decidable expression: un-hiding a body grid off this exact shape
    exposed a latent internal overlap the paint gate rejects (and the
    wake-16 letters incident was the same class)."""
    from ms_layout import staticize                    # noqa: PLC0415
    out = staticize(_mini_rdl_with_hidden(_FIELDS_UNTAKEN, _FIELDS_UNTAKEN))
    assert _hidden_of(out, "Tb_Band") == "false", (
        "in a page band, a parameter-decidable Hidden (Fields! only in the "
        "untaken branch, =Nothing default) must staticize visible")
    assert _hidden_of(out, "Tb_Body") == "true", (
        "in the body, a Hidden that mentions Fields! must keep the "
        "always-hide fallback even when parameter-decidable")


def test_truly_data_dependent_hidden_still_falls_back_to_hidden():
    """When the TAKEN path needs data, no verdict exists — fallback holds
    everywhere (the variant-overlap rule)."""
    from ms_layout import staticize                    # noqa: PLC0415
    expr = '=Not((First(Fields!F_Kind.Value, "DS_X") = "M"))'
    out = staticize(_mini_rdl_with_hidden(expr, expr))
    assert _hidden_of(out, "Tb_Band") == "true"
    assert _hidden_of(out, "Tb_Body") == "true"


# ---------------------------------------------------------------------------
# 2) converter: footer-only declared margin bands
# ---------------------------------------------------------------------------

def _report_xml(margins: str) -> str:
    """Synthetic two-section report in the per-section <margin> dialect."""
    return (
        '<?xml version="1.0"?><report name="FOOTER_T" '
        'DTDVersion="9.0.2.0.10">'
        '<data><dataSource name="Q_Main">'
        '<select><![CDATA[select item_nm, amt from t]]></select>'
        '<group name="G_Main"><dataItem name="ITEM_NM" datatype="vchar2"/>'
        '<dataItem name="AMT" datatype="number"/></group></dataSource>'
        '</data>'
        '<layout><section name="main" width="8.50000" height="11.00000">'
        '<body><frame name="M_ALL">'
        '<geometryInfo x="0" y="0" width="7.5" height="3"/>'
        '<repeatingFrame name="R_Main" source="G_Main" '
        'printDirection="down">'
        '<geometryInfo x="0" y="0.5" width="7.5" height="0.4"/>'
        '<field name="F_ITEM" source="ITEM_NM">'
        '<geometryInfo x="0.1" y="0.55" width="2.0" height="0.2"/></field>'
        '</repeatingFrame></frame></body>'
        f'{margins}'
        '</section></layout></report>'
    )


_FOOTER_ONLY_MARGIN = (
    '<margin><frame name="M_Footer_G">'
    '<geometryInfo x="0.5" y="10.5" width="7.5" height="0.2"/>'
    '<text name="B_Ftr_Page"><textSettings justify="center"/>'
    '<geometryInfo x="3.5" y="10.5" width="2.0" height="0.19"/>'
    '<textSegment><font face="Arial" size="8"/>'
    '<string><![CDATA[&<PageNumber> of &<TotalPages>]]></string>'
    '</textSegment></text>'
    '<text name="B_Ftr_Report"><textSettings justify="center"/>'
    '<geometryInfo x="6.4" y="10.5" width="1.6" height="0.19"/>'
    '<textSegment><font face="Arial" size="8"/>'
    '<string><![CDATA[STUB_FOOTER_STAMP.rdf]]></string>'
    '</textSegment></text>'
    '</frame></margin>'
)


def test_footer_only_margin_band_reaches_the_page_footer():
    """A declared margin band with FOOTER furniture only (no header-worthy
    text/image) must still emit its declared bottom chrome 1:1 — the
    header-only trust gate dropped the run-date / page-number / report-name
    stamps entirely (measured: a truth-paired report lost its declared
    footer stamp while a synthesized header played stand-in)."""
    from converter import convert                      # noqa: PLC0415
    rdl = convert(_report_xml(_FOOTER_ONLY_MARGIN).encode())["rdl_xml"]
    pf = re.search(r"<PageFooter>(.*?)</PageFooter>", rdl, re.S)
    assert pf, "a footer band must exist"
    assert "STUB_FOOTER_STAMP.rdf" in pf.group(1), (
        "the declared footer-only report stamp must reach the PageFooter")
    assert "Globals!PageNumber" in pf.group(1), (
        "the declared footer page number must reach the PageFooter")
    # PROVE THE GATE CAN FAIL: no margin band -> no declared chrome items.
    bare = convert(_report_xml("").encode())["rdl_xml"]
    assert "MChrome_" not in bare


def test_per_section_restated_margin_furniture_collapses_to_one_box():
    """Each Oracle section declares its own copy of the same footer
    furniture (same text, same paper y, same box size, slightly different
    x for its own body width); a merged single-page-model band must print
    ONE copy, preferring the MAIN section's x (measured: every footer item
    printed twice on every page of a truth-paired report)."""
    from converter import convert                      # noqa: PLC0415
    two_sections = _report_xml(_FOOTER_ONLY_MARGIN).replace(
        "</section></layout>",
        '</section><section name="header" width="8.50000" '
        'height="11.00000"><body><frame name="M_H">'
        '<geometryInfo x="0" y="0" width="7.5" height="1"/>'
        '<text name="B_HdrNote">'
        '<geometryInfo x="0.2" y="0.2" width="2.0" height="0.2"/>'
        '<textSegment><font face="Arial" size="10"/>'
        '<string><![CDATA[Cover note]]></string></textSegment></text>'
        '</frame></body>'
        + _FOOTER_ONLY_MARGIN
        .replace("M_Footer_G", "M_HdrFooter_G")
        .replace("B_Ftr_Page", "B_HFtr_Page")
        .replace("B_Ftr_Report", "B_HFtr_Report")
        .replace('x="6.4" y="10.5" width="1.6"',
                 'x="6.1" y="10.5" width="1.6"')
        + "</section></layout>")
    rdl = convert(two_sections.encode())["rdl_xml"]
    assert rdl.count("STUB_FOOTER_STAMP.rdf") == 1, (
        "restated per-section footer furniture must collapse to one box")


def test_single_line_footer_literal_keeps_its_last_glyph():
    """A single-line literal whose true Arial extent overhangs its declared
    box by a metric sliver must not WRAP its final glyph out of the
    one-line-high band box: the box grows to the measured extent anchored
    at the declared justification (measured: a footer report-name stamp
    rendered missing its final glyph; deficit 0.009in)."""
    from converter import convert                      # noqa: PLC0415
    from converter.generators.rdl import _afm_text_width  # noqa: PLC0415
    stamp = "STUB_FOOTER_STAMP.rdf"
    ext = _afm_text_width(stamp, 8.0, False, True)
    tight = f"{ext - 0.005:.3f}"
    xml = _report_xml(_FOOTER_ONLY_MARGIN.replace(
        'x="6.4" y="10.5" width="1.6"', f'x="6.4" y="10.5" width="{tight}"'))
    rdl = convert(xml.encode())["rdl_xml"]
    m = re.search(r'<Textbox Name="MChrome_F_B_Ftr_Report">'
                  r'((?:(?!</Textbox>).)*)</Textbox>', rdl, re.S)
    assert m, "the footer stamp textbox must emit"
    w = float(re.search(r"<Width>([\d.]+)in</Width>", m.group(1)).group(1))
    assert w >= ext + 0.019, (
        f"box must grow to the measured extent (+GDI cushion): {w} < {ext}")
    # PROVE THE GATE CAN FAIL: a REAL overflow (way past the drift budget)
    # keeps the declared width — growth is bounded.
    xml2 = _report_xml(_FOOTER_ONLY_MARGIN.replace(
        'x="6.4" y="10.5" width="1.6"', 'x="6.4" y="10.5" width="0.6"'))
    rdl2 = convert(xml2.encode())["rdl_xml"]
    m2 = re.search(r'<Textbox Name="MChrome_F_B_Ftr_Report">'
                   r'((?:(?!</Textbox>).)*)</Textbox>', rdl2, re.S)
    w2 = float(re.search(r"<Width>([\d.]+)in</Width>", m2.group(1)).group(1))
    assert abs(w2 - 0.6) < 0.02, (
        "a real overflow must keep the declared width (bounded growth)")

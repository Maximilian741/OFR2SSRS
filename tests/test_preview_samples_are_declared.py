"""The preview's sample values come from DECLARATIONS, never a name table.

Until this landed, the HTML preview filled `&TOKEN` substitutions and bound
fields from a 37-entry dict keyed by token NAME. That construct is the
"written beside one corpus" failure in its purest form: it produces a
convincing value because it RECOGNISES the token, so it looks perfect on the
reports it was written next to and does something arbitrary on the next
site's naming convention -- silently, with no gate going red. (It also hid
from the domain-vocabulary guard, whose scanner read only tuple/list/set
literals and never looked at a dict's keys. That scanner is widened in
tests/test_domain_vocabulary_guard.py; this file guards the behaviour.)

Every value the preview invents must now be traceable to something the
report itself DECLARED, in rank order:

    1. a parameter's own ``initialValue``   -- the author's literal default
    2. the declared ``datatype``            -- date / number / character
    3. the declared ``formatMask``          -- the printed SHAPE within a type
    4. nothing declared -> the structural keyword fallback

Rank matters and is guarded below: a CHARACTER column carrying a layout date
mask must NOT print a date. Reading the mask ahead of the datatype did
exactly that on a real report -- a person-name column rendered "01/05/2026".

Every fixture here is synthetic. No real report, column or parameter name
appears in this file.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter.parsers.oracle_xml import parse_oracle_xml  # noqa: E402
from converter.preview import html_mockup as M             # noqa: E402


def _src(params="", items="", fields=""):
    """A minimal but REAL Oracle report the parser accepts."""
    return (
        '<?xml version="1.0"?><report name="RPT"><data>'
        + params +
        '<dataSource name="Q_1"><select><![CDATA[SELECT a FROM t]]></select>'
        '<group name="G_1">' + items + '</group></dataSource>'
        '</data><layout><section name="main"><body>'
        '<frame name="FR_1" x="0" y="0" width="7" height="1">'
        + fields +
        '</frame></body></section></layout></report>'
    ).encode("utf-8")


def _parse(**kw):
    return parse_oracle_xml(_src(**kw))


def _sample(report, token):
    return M._declared_sample(token, report)


# ---------------------------------------------------------------------------
# rank 1 -- the author's own declared default
# ---------------------------------------------------------------------------

def test_a_parameters_declared_initial_value_is_what_the_preview_prints():
    rep = _parse(params='<userParameter name="P_ALPHA" datatype="character"'
                        ' initialValue="Zeta, Omicron"/>')
    assert _sample(rep, "P_ALPHA") == "Zeta, Omicron"


def test_the_initial_value_wins_over_the_declared_type():
    """A DATE parameter with a literal default prints the default, not a
    synthesized date -- the author already said what it shows."""
    rep = _parse(params='<userParameter name="P_ALPHA" datatype="date"'
                        ' initialValue="31-DEC-2099"/>')
    assert _sample(rep, "P_ALPHA") == "31-DEC-2099"


def test_a_field_object_wrapper_reaches_the_parameter_it_wraps():
    """Boilerplate references the field OBJECT (&F_P_X); the declaration
    lives on the parameter."""
    rep = _parse(params='<userParameter name="P_ALPHA" datatype="character"'
                        ' initialValue="Kappa"/>')
    assert _sample(rep, "F_P_ALPHA") == "Kappa"


# ---------------------------------------------------------------------------
# rank 2 -- the declared datatype
# ---------------------------------------------------------------------------

def test_a_declared_date_column_samples_as_a_date():
    rep = _parse(items='<dataItem name="ZETA" datatype="date"/>')
    assert re.match(r"^\d\d/\d\d/\d{4}$", _sample(rep, "ZETA") or "")


def test_a_declared_number_column_samples_as_a_number():
    rep = _parse(items='<dataItem name="ZETA" datatype="number"/>')
    got = _sample(rep, "ZETA")
    assert got and got.replace(".", "").replace(",", "").isdigit(), got


def test_a_declared_character_column_defers_to_the_structural_fallback():
    """Character says nothing about CONTENT, so the sampler must decline
    (None) rather than invent a shape."""
    rep = _parse(items='<dataItem name="ZETA" datatype="vchar2"/>')
    assert _sample(rep, "ZETA") is None


def test_a_name_the_report_never_declares_gets_no_declared_sample():
    """The anti-name-table property: an unknown token has no answer here.
    If a lookup table ever comes back, this is the test that goes red."""
    rep = _parse(items='<dataItem name="ZETA" datatype="number"/>')
    for unknown in ("OMICRON", "P_OMICRON", "CF_OMICRON", "SOME_COLUMN"):
        assert _sample(rep, unknown) is None, unknown


# ---------------------------------------------------------------------------
# rank 3 -- the declared format mask shapes the value
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mask,pattern", [
    ("MM/DD/YYYY", r"^\d\d/\d\d/\d{4}$"),
    ("DD-MON-RRRR", r"^\d\d-[A-Z]{3}-\d{4}$"),
    ("fmMonth DD, YYYY", r"^[A-Z]+ \d\d, \d{4}$"),
])
def test_a_declared_date_mask_shapes_the_sample(mask, pattern):
    rep = _parse(items='<dataItem name="ZETA" datatype="date"/>',
                 fields='<field name="F_1" source="ZETA" formatMask="%s"'
                        ' x="0" y="0" width="2" height="0.2"/>' % mask)
    got = _sample(rep, "ZETA")
    assert re.match(pattern, got or ""), (mask, got)


@pytest.mark.parametrize("mask,pattern", [
    ("NNN,NN0", r"^[\d,]+$"),
    ("$NNN,NN0.00", r"^\$[\d,]+\.\d\d$"),
    ("NN0.00%", r"^\d+\.\d\d%$"),
])
def test_a_declared_number_mask_shapes_the_sample(mask, pattern):
    rep = _parse(items='<dataItem name="ZETA" datatype="number"/>',
                 fields='<field name="F_1" source="ZETA" formatMask="%s"'
                        ' x="0" y="0" width="2" height="0.2"/>' % mask)
    got = _sample(rep, "ZETA")
    assert re.match(pattern, got or ""), (mask, got)


# ---------------------------------------------------------------------------
# RANK ORDER -- the defect this ordering was written to stop
# ---------------------------------------------------------------------------

def test_a_character_column_with_a_layout_date_mask_never_prints_a_date():
    """THE REGRESSION GUARD.

    A real report declares a character column and puts a date mask on the
    layout object drawn beside it. Reading the mask ahead of the declared
    datatype turned that column into "01/05/2026" in the preview. The
    datatype is the higher-ranked declaration; the mask is a grammar within
    a type, not evidence of the type.
    """
    rep = _parse(items='<dataItem name="ZETA" datatype="vchar2"/>',
                 fields='<field name="F_1" source="ZETA" formatMask="mm/dd/yyyy"'
                        ' x="0" y="0" width="2" height="0.2"/>')
    got = _sample(rep, "ZETA")
    assert got is None or not re.search(r"\d\d/\d\d/\d{4}", got), got


# ---------------------------------------------------------------------------
# rename invariance -- the property a name table cannot have
# ---------------------------------------------------------------------------

def test_samples_depend_on_declarations_only_not_on_spelling():
    """Rename every identifier, keep every declaration: the preview's values
    must be unchanged. A name table fails this by construction."""
    def build(a, b, c):
        return parse_oracle_xml(_src(
            params='<userParameter name="%s" datatype="character"'
                   ' initialValue="Kappa"/>' % a,
            items='<dataItem name="%s" datatype="date"/>'
                  '<dataItem name="%s" datatype="number"/>' % (b, c)))

    first = build("P_ALPHA", "ZETA", "OMICRON")
    second = build("P_QUEBEC", "XRAY", "YANKEE")
    assert ([_sample(first, n) for n in ("P_ALPHA", "ZETA", "OMICRON")]
            == [_sample(second, n) for n in ("P_QUEBEC", "XRAY", "YANKEE")])


# ---------------------------------------------------------------------------
# end to end -- the preview still fills every token
# ---------------------------------------------------------------------------

_RAW_TOKEN = re.compile(r"&(?!amp;|lt;|gt;|quot;|nbsp;|apos;|#)"
                        r"[A-Za-z_][A-Za-z0-9_]{2,}")


def test_the_rendered_preview_leaves_no_raw_token_on_the_page():
    """Removing the name table must not reintroduce '&FOO' as visible ink --
    the failure the table was silently covering for."""
    rep = _parse(
        params='<userParameter name="P_ALPHA" datatype="character"'
               ' initialValue="Kappa"/>',
        items='<dataItem name="ZETA" datatype="date"/>',
        fields='<text name="T_1" x="0" y="0" width="6" height="0.3">'
               '<textSegment>Run for &amp;P_ALPHA on &amp;ZETA '
               'ref &amp;CF_OMICRON</textSegment></text>')
    html = M.render_mockup(rep, mode="frontend")
    body = re.sub(r"<[^>]+>", " ", html)
    assert not _RAW_TOKEN.findall(body), _RAW_TOKEN.findall(body)


def test_the_render_context_is_restored_after_a_render():
    """The active-declarations slot is module state; a render must put it
    back so one report's declarations never leak into the next preview."""
    before = M._ACTIVE_DECLS
    M.render_mockup(_parse(items='<dataItem name="ZETA" datatype="number"/>'),
                    mode="frontend")
    assert M._ACTIVE_DECLS is before


def test_no_name_keyed_sample_table_survives_in_the_preview_module():
    """Structural backstop: the module must not carry a dict that maps
    identifier-shaped NAMES to printable sample strings."""
    import ast
    src = (ROOT / "backend" / "converter" / "preview"
           / "html_mockup.py").read_text(encoding="utf-8")
    offenders = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Dict):
            continue
        keys = [k.value for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)]
        vals = [v.value for v in node.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)]
        if len(keys) < 8 or len(vals) < len(keys):
            continue
        ident = [k for k in keys if re.match(r"^[A-Z][A-Z0-9_]{2,}$", k)]
        # A sample TABLE maps names to prose/values; the surviving dialect
        # tables map mask tokens to slot NAMES, which are themselves
        # identifier-shaped. Prose on the right is the tell.
        prose = [v for v in vals if " " in v or any(c.isdigit() for c in v)
                 and not re.match(r"^[A-Z0-9]+$", v)]
        if len(ident) >= 8 and len(prose) >= 4:
            offenders.append(node.lineno)
    assert not offenders, (
        "a name-keyed sample table is back in html_mockup.py at line(s) "
        + ", ".join(str(n) for n in offenders)
        + " -- fill values from the report's declarations instead")

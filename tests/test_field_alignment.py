"""Regression test for the "Tablix cell references a field that doesn't
exist in the dataset" class of bug.

Triggered in Report Builder when:
  1. The user uploads the RDL (parses, uploads fine).
  2. The user repoints the data source and clicks "Refresh Fields".
  3. Report Builder rebuilds the Fields collection from Oracle's actual
     column-name response.
  4. Any unaliased SELECT expression (UPPER(...), CONCAT(...), etc.)
     comes back with Oracle's verbose default column name that DOES
     NOT match the <Field Name="..."> we declared.
  5. Every Tablix cell that references the now-missing field becomes
     orphan. Save fails with:
       "The Value expression for the text box 'Cell_X' refers to the
        field 'X'. ... Letters in the names of fields must use the
        correct case."

The generator must add an explicit ``AS <name>`` to every unaliased
SELECT expression so Oracle returns the column name we already
declared. This test asserts that contract for every dataset in every
discovered case fixture.

Name-agnostic: walks tests/fixtures/source_of_truth/ and runs against
whatever case_NNN subdirectories exist. NO report names anywhere.
"""
from __future__ import annotations
import html
import re
from pathlib import Path

import pytest


FIXTURES = Path(__file__).parent / "fixtures" / "source_of_truth"


def _cases():
    if not FIXTURES.exists():
        return []
    out = []
    for d in sorted(FIXTURES.iterdir()):
        src = d / "source.xml"
        if src.exists():
            out.append(pytest.param(d.name, src, id=d.name))
    return out


def _command_texts(rdl: str):
    return [html.unescape(c)
            for c in re.findall(r"<CommandText>(.*?)</CommandText>", rdl, re.DOTALL)]


def _field_decls(rdl: str):
    return set(re.findall(r'<Field Name="([^"]+)"', rdl))


def _field_refs(rdl: str):
    return set(re.findall(r"Fields!([A-Za-z0-9_]+)\.Value", rdl))


def _split_select_items(sql: str):
    """Yield each top-level SELECT item (between SELECT and FROM)."""
    upper = sql.upper()
    sel = upper.find("SELECT")
    if sel < 0:
        return
    # Find top-level FROM.
    depth = 0
    i = sel + len("SELECT")
    fm = -1
    while i < len(sql):
        ch = sql[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and upper[i:i + 4] == "FROM" and (
            i + 4 == len(sql) or not sql[i + 4].isalnum()
        ):
            fm = i
            break
        i += 1
    if fm < 0:
        return
    body = sql[sel + len("SELECT"):fm]
    cur = []
    depth = 0
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            yield "".join(cur).strip()
            cur = []
        else:
            cur.append(ch)
    if cur:
        yield "".join(cur).strip()


@pytest.mark.parametrize("case_name,src_path", _cases())
def test_no_orphan_field_refs(case_name, src_path):
    """Every Fields!X.Value reference in the RDL body must have a
    matching <Field Name="X"> declaration in some dataset."""
    from converter import convert
    rdl = convert(src_path.read_bytes())["rdl_xml"]
    declared = _field_decls(rdl)
    refs = _field_refs(rdl)
    orphans = refs - declared
    assert not orphans, (
        f"[{case_name}] Tablix cells reference fields not declared in any "
        f"dataset: {sorted(orphans)} -- Report Builder will reject Save."
    )


@pytest.mark.parametrize("case_name,src_path", _cases())
def test_every_unaliased_expression_has_an_alias_added(case_name, src_path):
    """For every SELECT item that is a non-bare expression, the emitted
    CommandText must include either an explicit AS alias or an implicit
    trailing-identifier alias. Catches the "expression returns verbose
    Oracle column name" bug at the SQL level."""
    from converter import convert
    rdl = convert(src_path.read_bytes())["rdl_xml"]

    bare_col = re.compile(
        r"^\s*[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?\s*$"
    )
    explicit_alias = re.compile(
        r"\bAS\b\s+[A-Za-z_][A-Za-z0-9_]*\s*$", re.IGNORECASE
    )
    implicit_alias = re.compile(
        # Trailing identifier following ")", a word char, or a closing
        # single-quote (string-literal concatenation alias). Mirrors the
        # generator's detection in _alias_select_items.
        r"(?:\)|[A-Za-z0-9_]|')\s+[A-Za-z_][A-Za-z0-9_]*\s*$"
    )

    offenders = []
    for ct in _command_texts(rdl):
        for item in _split_select_items(ct):
            if not item or bare_col.match(item):
                continue
            if explicit_alias.search(item) or implicit_alias.search(item):
                continue
            offenders.append(item[:80])
    assert not offenders, (
        f"[{case_name}] SELECT expression(s) with no alias -- Refresh "
        f"Fields will produce verbose Oracle column names that don't "
        f"match our <Field> declarations and Save will fail:\n"
        + "\n".join(f"  {o}" for o in offenders[:5])
    )


@pytest.mark.parametrize("case_name,src_path", _cases())
def test_no_double_alias_inserted(case_name, src_path):
    """SQL must never end up with two AS clauses in a row (would happen
    if the aliaser failed to detect an existing implicit alias)."""
    from converter import convert
    rdl = convert(src_path.read_bytes())["rdl_xml"]
    for ct in _command_texts(rdl):
        dbl = re.findall(r"AS\s+[A-Za-z_][A-Za-z0-9_]*\s+AS\s+",
                         ct, re.IGNORECASE)
        assert not dbl, (
            f"[{case_name}] double-AS sequence in CommandText -- the "
            f"aliaser failed to detect an existing alias: {dbl[:3]}"
        )


def test_alias_after_string_concat_not_double_aliased():
    """Oracle SQL allows implicit aliasing after a string-literal concat:

        'DEPT' || CHR(10) || UPPER(X) || ' LICENSE' Perm_Type

    The bare 'Perm_Type' is the alias of the whole concat. Our
    aliaser must NOT add a second 'AS ...' to it -- doing so produces
    invalid SQL and ORA-00923 'FROM keyword not found' at runtime.
    Regression test for the SAMPLE_DRILLTHROUGH upload break."""
    from converter.generators.rdl import _alias_select_items
    sql = (
        "SELECT 'DEPT' || CHR(10) || UPPER(X) || ' LICENSE' Perm_Type, "
        "       Y.NAME "
        "FROM T Y"
    )
    out = _alias_select_items(sql, ["Perm_Type", "NAME"])
    # The implicit alias 'Perm_Type' should still be the only alias
    # on that item -- NO 'AS ...' appended.
    assert "Perm_Type AS" not in out, (
        f"double-aliased: {out!r}"
    )
    # And the SQL should not have a doubled-up 'AS X AS Y' anywhere.
    import re as _re
    assert not _re.search(r"AS\s+\w+\s+AS\s+", out, _re.IGNORECASE), (
        f"double-AS detected: {out!r}"
    )


def test_double_quoted_alias_not_double_aliased():
    """Oracle SQL aliases a select item with a DOUBLE-QUOTED identifier:

        NVL(d.localdepartement,'-----') "localdepartement"
        TO_CHAR(d.created)          AS "dateCreation"

    The quoted token IS the column alias (Oracle string literals use single
    quotes, so a trailing "..." is never a literal). The aliaser must NOT
    append a second alias -- ``expr "a" AS b`` is invalid SQL (ORA-00923
    'FROM keyword not found') and also renames the result column away from the
    declared <Field>, blanking the binding. Regression for the quoted-alias
    double-aliasing found across the French wild-corpus reports."""
    from converter.generators.rdl import _alias_select_items
    import re as _re
    sql = (
        "SELECT NVL(d.localdepartement,'-----') \"localdepartement\", "
        "       TO_CHAR(d.created) AS \"dateCreation\", "
        "       d.id "
        "FROM departements d"
    )
    out = _alias_select_items(sql, ["localdepartement", "dateCreation", "id"])
    # No second alias appended after either quoted alias.
    assert '"localdepartement" AS' not in out, f"double-aliased: {out!r}"
    assert '"dateCreation" AS' not in out, f"double-aliased: {out!r}"
    # The quoted aliases must survive verbatim (Oracle returns those columns).
    assert '"localdepartement"' in out and '"dateCreation"' in out, (
        f"quoted alias was dropped: {out!r}"
    )
    # No 'expr "x" AS y' invalid-SQL pattern.
    assert not _re.search(r'"[^"]*"\s+AS\s+\w', out, _re.IGNORECASE), (
        f"quoted alias followed by AS (invalid SQL): {out!r}"
    )


def test_string_literal_comma_not_split_or_aliased():
    """A comma INSIDE a string literal is not a select-item separator. The
    classic Oracle idiom builds a delimited string:

        LAST_NAME || ', ' || FIRST_NAME   AS full_name

    A paren-depth-only tokenizer split on the comma inside ', ', injected an
    alias INTO the literal ( ' AS FULL_NAME, ' ), and corrupted the output --
    this actually mangled real reports (a single-record form, a list report). The
    tokenizer must treat '...' literals as opaque."""
    from converter.generators.rdl import _alias_select_items
    sql = ("SELECT LAST_NAME || ', ' || FIRST_NAME full_name, "
           "       DEPT "
           "FROM EMP")
    out = _alias_select_items(sql, ["full_name", "DEPT"])
    # The ', ' literal must survive verbatim -- nothing injected inside it.
    assert "', '" in out, f"string literal ', ' was corrupted: {out!r}"
    assert "AS FULL_NAME," not in out.upper().replace(" ", " "), (
        f"alias injected into the string literal: {out!r}"
    )
    # full_name keeps its single implicit alias; no double-alias.
    import re as _re
    assert not _re.search(r"AS\s+\w+\s+AS\s+\w", out, _re.IGNORECASE), out


def test_commented_out_trailing_columns_not_mangled():
    """Oracle authors comment out trailing select columns:

        o.a || ' ' || o.b "Initial"/*, t.c, t.d, t.e*/

    A comment-blind tokenizer split on the commas inside the /* */, then injected
    aliases around the comment delimiters -> 'expr "Initial" /*...*/ AS X' double
    alias = invalid SQL (this hit the ASBESTOS client report). The comment span
    must be opaque and the existing quoted alias respected."""
    from converter.generators.rdl import _alias_select_items
    import re as _re
    sql = ('SELECT count(*) n, '
           'o.a || \' \' || o.b "Initial"/*, t.c, t.d, t.e*/ '
           'FROM o')
    out = _alias_select_items(sql, ["n", "Initial"])
    # The author's comment survives; no alias injected around it.
    assert "/*, t.c, t.d, t.e*/" in out, f"comment was altered: {out!r}"
    assert not _re.search(r'"Initial"\s*/\*.*?\*/\s*AS\s+\w', out, _re.IGNORECASE | _re.DOTALL), (
        f"alias injected around the comment (invalid SQL): {out!r}"
    )
    assert not _re.search(r"AS\s+\w+\s+AS\s+\w", out, _re.IGNORECASE), out


def test_repair_misscoped_field_ref_rewrites_to_owning_dataset():
    """An aggregate scoped to a dataset that does NOT declare the field
    ( First(Fields!CF_X.Value, "Q_Main") where CF_X lives in the formula
    dataset ) renders #Error at run time. The repair rewrites the scope to the
    dataset that actually has the field, while leaving a correctly-scoped ref
    untouched. Regression for an accounting/summary report (RED -> READY: a
    section-title formula was scoped to the wrong query)."""
    import xml.etree.ElementTree as ET
    from converter.generators.rdl import (
        _repair_misscoped_field_refs, _q, _FORMULA_DATASET_NAME)
    root = ET.Element(_q("Report"))
    dss = ET.SubElement(root, _q("DataSets"))
    f_ds = ET.SubElement(dss, _q("DataSet")); f_ds.set("Name", _FORMULA_DATASET_NAME)
    ff = ET.SubElement(f_ds, _q("Fields"))
    ET.SubElement(ff, _q("Field")).set("Name", "CF_X")
    q_ds = ET.SubElement(dss, _q("DataSet")); q_ds.set("Name", "Q_Main")
    qf = ET.SubElement(q_ds, _q("Fields"))
    ET.SubElement(qf, _q("Field")).set("Name", "Y")
    v1 = ET.SubElement(root, _q("Value")); v1.text = '=First(Fields!CF_X.Value, "Q_Main")'
    v2 = ET.SubElement(root, _q("Value")); v2.text = '=First(Fields!Y.Value, "Q_Main")'
    _repair_misscoped_field_refs(root)
    assert v1.text == f'=First(Fields!CF_X.Value, "{_FORMULA_DATASET_NAME}")', v1.text
    assert v2.text == '=First(Fields!Y.Value, "Q_Main")', v2.text  # correct ref untouched


def test_leading_set_quantifier_not_absorbed_into_first_alias():
    """SELECT DISTINCT / ALL / UNIQUE -- the set-quantifier is part of the SELECT,
    not the first column. It must NOT be absorbed into the first item's derived
    alias ( "SELECT ALL T.COL" -> ALL_T_COL ), which makes that <Field> bind to
    nothing -> BLANK first column. Regression for a positional form report
    whose DISTINCT dataset's first column was
    mangled to DISTINCT_SITE_SITE_ID."""
    from converter.generators.rdl import _alias_select_items
    import re as _re
    for kw in ("DISTINCT", "ALL", "UNIQUE"):
        out = _alias_select_items(f"SELECT {kw} T.COL, X FROM t", ["COL", "X"])
        assert _re.match(rf"\s*SELECT\s+{kw}\s+T\.COL\s*,", out, _re.I), out
        assert not _re.search(rf"AS\s+{kw}_", out, _re.I), f"{kw} absorbed: {out!r}"
    # An aliased first item after the quantifier is preserved verbatim.
    out2 = _alias_select_items("SELECT DISTINCT count(*) Pemits, X FROM t",
                               ["Pemits", "X"])
    assert "SELECT DISTINCT count(*) Pemits," in out2, out2
    # A column literally named all_total is NOT mistaken for the ALL quantifier.
    out3 = _alias_select_items("SELECT all_total, y FROM t", ["ALL_TOTAL", "Y"])
    assert _re.match(r"\s*SELECT\s+all_total\s*,", out3, _re.I), out3


def test_multiword_quoted_alias_rewritten_to_dataitem_name():
    """A multi-word quoted alias ( "Denumire joc" ) makes Oracle return a column
    of that exact spaced name, which the sanitized <Field DataField>
    ( Denumire_joc_ ) can never match -> BLANK column in SSRS. When the SQL
    columns align 1:1 with the dataItems, the alias is rewritten to the
    positionally-bound dataItem name (quoted, exact case) so SSRS's by-name
    binding matches Oracle's by-position binding. A plain-identifier quoted
    alias ( "localdepartement" ) already matches its field and is left as-is."""
    from converter.generators.rdl import _alias_select_items
    sql = 'SELECT g."name" as "Denumire joc", c."name" as "Nume client" FROM g, c'
    out = _alias_select_items(sql, ["Denumire_joc_", "Nume_client_"])
    assert '"Denumire_joc_"' in out and '"Nume_client_"' in out, out
    assert '"Denumire joc"' not in out, f"spaced alias not rewritten: {out!r}"

    # Plain-identifier quoted alias is NOT rewritten (already binds; zero drift).
    sql2 = 'SELECT NVL(d.x,0) "localdepartement", d.id FROM d'
    out2 = _alias_select_items(sql2, ["localdepartement", "ID"])
    assert '"localdepartement"' in out2, out2

    # When counts do NOT align 1:1, the rewrite is suppressed (conservative).
    sql3 = 'SELECT g."name" as "Denumire joc" FROM g'
    out3 = _alias_select_items(sql3, ["a", "b", "c"])
    assert '"Denumire joc"' in out3, f"rewrote despite count mismatch: {out3!r}"


def test_strip_trailing_comment_handles_dashdash_inside_block():
    """A '--' INSIDE a /* ... -- ... */ block is not a line comment. A naive
    strip that removes a trailing '--' first breaks the block's closing '*/' and
    corrupts the item (the aliaser then injects an alias into the broken
    comment). The span-aware strip must drop the whole block as one unit."""
    from converter.generators.rdl import _strip_trailing_sql_comment
    s = "NULL /* lexical ref &COL_1 -- reimplement at deploy time */"
    assert _strip_trailing_sql_comment(s) == "NULL", repr(_strip_trailing_sql_comment(s))


def test_strip_trailing_comment_keeps_string_literal():
    """A trailing '...' string literal is part of the expression, not a comment;
    it must survive the strip."""
    from converter.generators.rdl import _strip_trailing_sql_comment
    s = "LAST || ', '"
    assert _strip_trailing_sql_comment(s) == "LAST || ', '"


def test_no_query_stub_has_from_dual_for_oracle():
    """A report with no extractable query gets a placeholder dataset. The Oracle
    CommandText MUST select FROM DUAL -- 'SELECT 1, 0, ''' with no FROM throws
    ORA-00923 on Oracle (SQL Server allows it, and has no DUAL)."""
    from converter.generators import rdl as _rdl
    import inspect
    src = inspect.getsource(_rdl)
    assert "SELECT 1 AS Column1, 0 AS Column2, '' AS Column3 FROM DUAL" in src, (
        "the Oracle no-query stub must end with FROM DUAL"
    )


# -- _widen_clipped_constant_labels: Oracle->SSRS font-metric label clips --

_RDL_NS = "http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition"


def _label_rdl(text, left, width, align="Left", neighbor_left=None,
               neighbor_right_left=None, font="Times New Roman"):
    """A minimal RDL: one positioned constant-string label (12pt Bold, font
    selectable) plus optional left/right neighbour boxes that bound the grow.
    """
    def _box(name, l, w, val='=Fields!X.Value', extra=""):
        return (f'<Textbox Name="{name}"><CanGrow>false</CanGrow><Paragraphs>'
                f'<Paragraph><TextRuns><TextRun><Value>{val}</Value>'
                f'<Style><FontFamily>{font}</FontFamily>'
                f'<FontSize>12pt</FontSize><FontWeight>Bold</FontWeight></Style>'
                f'</TextRun></TextRuns>{extra}</Paragraph></Paragraphs>'
                f'<Top>0in</Top><Left>{l}in</Left><Width>{w}in</Width>'
                f'<Height>0.2in</Height></Textbox>')
    label = _box("L", left, width,
                 val=f'="{text}"',
                 extra=f'<Style><TextAlign>{align}</TextAlign></Style>')
    sibs = ""
    if neighbor_left is not None:
        sibs += _box("NL", neighbor_left, 0.5)
    if neighbor_right_left is not None:
        sibs += _box("NR", neighbor_right_left, 0.5)
    return (f'<?xml version="1.0"?><Report xmlns="{_RDL_NS}"><Body>'
            f'<ReportItems>{label}{sibs}</ReportItems><Height>1in</Height>'
            f'</Body><Width>8in</Width></Report>')


def _label_width(root, name="L"):
    import xml.etree.ElementTree as ET
    for tb in root.iter(f"{{{_RDL_NS}}}Textbox"):
        if tb.get("Name") == name:
            return float((tb.find(f"{{{_RDL_NS}}}Width").text or "0").replace("in", ""))
    raise AssertionError("label not found")


def test_widen_clips_left_label_into_free_space():
    """A left-aligned constant label whose box is too narrow for its text (and
    which has clear space to its right) is widened to show the full label on one
    line."""
    import xml.etree.ElementTree as ET
    from converter.generators.rdl import _widen_clipped_constant_labels
    root = ET.fromstring(_label_rdl("Geographic Detail Section", 0.06, 0.5, "Left"))
    _widen_clipped_constant_labels(root)
    assert _label_width(root) > 0.5, "clipping left label should have been widened"


def test_no_widen_when_right_neighbor_blocks():
    """The same clip, but a sibling box sits immediately to the right -- widening
    would overlap it, so the label is left exactly as-is."""
    import xml.etree.ElementTree as ET
    from converter.generators.rdl import _widen_clipped_constant_labels
    root = ET.fromstring(
        _label_rdl("Geographic Detail Section", 0.06, 0.5, "Left",
                   neighbor_right_left=0.58))
    _widen_clipped_constant_labels(root)
    assert _label_width(root) == 0.5, "must not widen into a right-neighbour"


def test_no_widen_center_label_pinned_to_left_edge():
    """A centre-aligned clip pinned against the page's left edge cannot grow left
    while keeping its centre fixed -> left unchanged (no overlap, no shift)."""
    import xml.etree.ElementTree as ET
    from converter.generators.rdl import _widen_clipped_constant_labels
    root = ET.fromstring(_label_rdl("Geographic Detail Section", 0.0, 0.5, "Center"))
    _widen_clipped_constant_labels(root)
    assert _label_width(root) == 0.5, "centre label at the edge must stay put"


def test_widen_leaves_fitting_label_untouched():
    """A label whose box already fits its text is never touched."""
    import xml.etree.ElementTree as ET
    from converter.generators.rdl import _widen_clipped_constant_labels
    root = ET.fromstring(_label_rdl("Hi", 0.06, 3.0, "Left"))
    _widen_clipped_constant_labels(root)
    assert _label_width(root) == 3.0, "a label that already fits must be untouched"


def test_widen_clips_arial_label_via_helvetica_metrics():
    """Regression (fire-92): an ARIAL constant label that clips must be widened
    too -- Arial renders wider than Times, so the Times-only metric under-counted
    it and missed the clip. Helvetica (Arial-compatible) AFM metrics catch it."""
    import xml.etree.ElementTree as ET
    from converter.generators.rdl import _widen_clipped_constant_labels
    root = ET.fromstring(
        _label_rdl("Certificate Number", 0.06, 0.5, "Left", font="Arial"))
    _widen_clipped_constant_labels(root)
    assert _label_width(root) > 0.5, "a clipping Arial label should be widened"


def test_widen_skips_unknown_font():
    """A label in a font we have no portable metrics for is left untouched
    (no guessing -> no over/under-widen)."""
    import xml.etree.ElementTree as ET
    from converter.generators.rdl import _widen_clipped_constant_labels
    root = ET.fromstring(
        _label_rdl("Certificate Number", 0.06, 0.5, "Left", font="Comic Sans MS"))
    _widen_clipped_constant_labels(root)
    assert _label_width(root) == 0.5, "an unknown-font label must be untouched"


def test_letter_cover_has_no_injected_body_title():
    """Regression (fire-90): the layout-driven letter/certificate criteria cover
    must NOT inject the report body letterhead title (e.g. a centred state title)
    at the top -- Oracle's Parameter Form output is just the label:value criteria
    list (verified against the certificate / letter ground-truth
    covers, which start directly at "Report:"). Guard against re-adding the
    Cov_Title band."""
    import inspect
    from converter.generators.rdl import _build_letter_cover_page as _f
    src = inspect.getsource(_f)
    assert "Cov_Title" not in src, (
        "the letter/certificate criteria cover must not emit a Cov_Title band "
        "(the body title belongs on the letter/certificate pages, not the cover)"
    )


def test_summary_section_value_columns_right_aligned():
    """Regression (fire-95): in the accounting/summary section table
    (_build_section_tablix), the numeric count/fee VALUE columns (every column
    after the label) must be RIGHT-aligned -- matching the band's right-aligned
    "Number"/"Fees" caption and Oracle's numeric right-justify (verified against
    an accounting/summary report truth, where the counts align right). The label
    column (col 0) stays left.

    This positional right-align is the DEFAULT, not an override: a column whose
    layout field declares its own justification is drawn as declared
    (``_col_ta``). Both spellings below keep ``"Right"`` as the fallback that
    applies when the layout declares nothing."""
    import inspect
    from converter.generators.rdl import _build_section_tablix as _f
    src = inspect.getsource(_f)
    assert '"Right" if ci > 0 else "Left"' in src, (
        "summary-section DETAIL value cells must right-align cols[1:]"
    )
    assert "=Sum(Val(" in src and '_falign = _col_ta[i] or "Right"' in src, (
        "summary-section TOTAL value cells must Sum(Val(...)) and right-align "
        "(not Center)"
    )


def test_geoband_master_card_pagebreak_is_between_not_start():
    """Regression (fire-96): a per-master CARD nested-MD report (geo_band: a
    colored master band like a green record-id header block) breaks ONE
    master per page. The group PageBreak must be BreakLocation="Between" (break
    BETWEEN instances) -- "Start" breaks before EVERY instance INCLUDING the
    first, producing a blank leading page (one report rendered an empty page 1 +
    the data on page 2; the truth is "Page 1 of 1" with data at the top)."""
    import inspect
    from converter.generators.rdl import _build_nested_group_tablix as _f
    src = inspect.getsource(_f)
    assert '_sub(opb, "BreakLocation", "Between")' in src, (
        "geo_band master-card group break must be 'Between' (one-per-page "
        "without a blank leading page)"
    )
    assert '_sub(opb, "BreakLocation", "Start")' not in src, (
        "geo_band master-card group break must NOT be 'Start' (it inserts a "
        "blank leading page before the first master)"
    )


def test_clamp_body_height_trims_trailing_blank_overflow():
    """Regression (fire-97): _clamp_body_height_to_page shrinks a body whose
    height + page chrome (margins + header + footer) overflows the page height,
    which otherwise spills a TRAILING blank page (engine-verified: two corpus
    reports each 2->1). Shrink-only + never clips a positioned body item."""
    import xml.etree.ElementTree as ET
    from converter.generators.rdl import _clamp_body_height_to_page
    NS = "http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition"
    # body 9.0 + margins 1.0 + header 1.16 + footer 0.6 = 11.76 > 11.0 page.
    # A Tablix at Top 0.1 / Height 1.9 (grows at render) does NOT block the clamp.
    rdl = (f'<Report xmlns="{NS}"><Body><ReportItems>'
           f'<Tablix Name="T"><Top>0.1in</Top><Height>1.9in</Height></Tablix>'
           f'</ReportItems><Height>9.0in</Height></Body>'
           f'<Page><PageHeight>11in</PageHeight><TopMargin>0.5in</TopMargin>'
           f'<BottomMargin>0.5in</BottomMargin>'
           f'<PageHeader><Height>1.16in</Height></PageHeader>'
           f'<PageFooter><Height>0.6in</Height></PageFooter></Page>'
           f'<Width>7.5in</Width></Report>')
    root = ET.fromstring(rdl)
    _clamp_body_height_to_page(root)
    bh = float(root.find(f"{{{NS}}}Body/{{{NS}}}Height").text.replace("in", ""))
    assert bh < 9.0, "overflowing body must be shrunk"
    assert bh + 1.0 + 1.16 + 0.6 <= 11.0 + 1e-6, "body + chrome must fit the page"


def test_clamp_body_height_never_clips_positioned_item():
    """The clamp must NOT shrink a body when a positioned item (a positional
    form's bottom-of-page field) would fall past the reduced height."""
    import xml.etree.ElementTree as ET
    from converter.generators.rdl import _clamp_body_height_to_page
    NS = "http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition"
    # A textbox at Top 8.5 / Height 0.3 = bottom 8.8, past the ~8.2 avail.
    rdl = (f'<Report xmlns="{NS}"><Body><ReportItems>'
           f'<Textbox Name="B"><Top>8.5in</Top><Height>0.3in</Height></Textbox>'
           f'</ReportItems><Height>9.0in</Height></Body>'
           f'<Page><PageHeight>11in</PageHeight><TopMargin>0.5in</TopMargin>'
           f'<BottomMargin>0.5in</BottomMargin>'
           f'<PageHeader><Height>1.16in</Height></PageHeader>'
           f'<PageFooter><Height>0.6in</Height></PageFooter></Page>'
           f'<Width>7.5in</Width></Report>')
    root = ET.fromstring(rdl)
    _clamp_body_height_to_page(root)
    bh = float(root.find(f"{{{NS}}}Body/{{{NS}}}Height").text.replace("in", ""))
    assert bh == 9.0, "must not shrink a body that would clip a positioned item"


def test_title_centering_requires_frame_spanning_box():
    """Regression (fire-100): the bold-near-top title-centering heuristic in
    _emit_field_textbox must only centre a box that SPANS most of its frame (a
    banner title), NOT a narrow left-anchored field. Stacked bold ROW LABELS
    (a header-summary report's stat-row labels in a
    3.4in box within a 7.3in frame; a positional form's "location"/"address"
    section labels) were wrongly centred -- the truth left-aligns them."""
    import inspect
    from converter.generators.rdl import _emit_field_textbox as _f
    src = inspect.getsource(_f)
    assert "_fw >= 0.6 * rect_w" in src, (
        "title-centering must require the box to span >=60% of the frame, "
        "else stacked bold row labels get mis-centred"
    )


def test_parser_reads_vertical_elasticity():
    """The parser must carry Oracle <generalLayout verticalElasticity="..."> onto
    the LayoutField so the generator can tell a sized-to-content box (collapse
    stacked segments) from a growable full-width prose box (leave it to flow).
    Regression for the permit-certificate defects 3 (bureau-chief) + 4 (card date)."""
    import xml.etree.ElementTree as ET
    from converter.parsers.oracle_xml import _layout_field_from_element
    card = ET.fromstring(
        '<text name="B_CARD"><geometryInfo x="0" y="0" width="3.05" height="0.156"/>'
        '<generalLayout verticalElasticity="variable"/>'
        '<textSegment><font face="Arial" size="8"/><string>expires</string></textSegment>'
        '<textSegment><font face="Arial" size="8" bold="yes"/><string> &amp;EXP_DATE</string></textSegment>'
        '</text>')
    lf = _layout_field_from_element(card)
    assert lf.vertical_elasticity == "variable"
    assert len(lf.segments) == 2  # mixed weight -> segments kept

    chief = ET.fromstring(
        '<text name="B"><geometryInfo width="4.25" height="0.9"/>'
        '<generalLayout verticalElasticity="contract"/>'
        '<textSegment><font size="12"/><string>NAME</string></textSegment>'
        '<textSegment><font size="10"/><string>ADDR</string></textSegment></text>')
    assert _layout_field_from_element(chief).vertical_elasticity == "contract"


def test_inline_html_markup_tags_never_paint():
    """Oracle web-mode boilerplate embeds literal <b>/<i>/<u>/<br> tags between
    styled segments (webSettings containHTMLTags); the paper renderer never
    paints them (truth PDFs carry ZERO literal tags) — the segment <font>
    attributes already hold the styling. The parser must strip exactly the
    recognizable inline-markup tag forms (b/i/u/em/strong, and <br> -> newline)
    while leaving legitimate angle-bracket prose untouched."""
    import xml.etree.ElementTree as ET
    from converter.parsers.oracle_xml import _layout_field_from_element

    el = ET.fromstring(
        '<text name="B_ACCT"><geometryInfo x="0" y="0" width="2" height="0.2"/>'
        '<textSegment><font face="Arial" size="8"/><string>Account &lt;b&gt;</string></textSegment>'
        '<textSegment><font face="Arial" size="8" bold="yes"/><string>&amp;CP_X</string></textSegment>'
        '<textSegment><font face="Arial" size="8"/><string>&lt;/b&gt; (Remediation)&lt;br&gt;next</string></textSegment>'
        '</text>')
    lf = _layout_field_from_element(el)
    joined = lf.text + "".join(s["text"] for s in (lf.segments or []))
    assert "<b>" not in joined and "</b>" not in joined, (
        "literal inline-markup tags must never survive into paintable text")
    assert "\nnext" in lf.text, "<br> must translate to a newline, not vanish"

    # Guard: legitimate less-than prose is NEVER eaten — an <ERROR - ...>
    # sentinel and a math comparison must survive byte-for-byte.
    prose = ET.fromstring(
        '<text name="B_ERR"><geometryInfo x="0" y="0" width="2" height="0.2"/>'
        '<textSegment><font face="Arial" size="8"/>'
        '<string>&lt;ERROR - MISSING ADDRESS&gt; x &lt; 5 &lt;brains&gt;</string></textSegment>'
        '</text>')
    lf2 = _layout_field_from_element(prose)
    assert "<ERROR - MISSING ADDRESS>" in lf2.text
    assert "x < 5" in lf2.text
    assert "<brains>" in lf2.text, "only exact b/i/u/em/strong/br tags strip"


def test_exact_fit_label_partial_widens_into_gap_and_strips_padding():
    """Oracle sizes some labels EXACTLY to their text (box width == metric text
    width); SSRS's 2pt+2pt padding alone then clips the final glyph, and the
    free gap before the right neighbour is smaller than the fully-padded widen
    target. The net must still rescue the label: widen INTO the available gap
    (never past the neighbour's guard gap) and strip the paddings."""
    import xml.etree.ElementTree as ET
    from converter.generators.rdl import (
        _widen_clipped_constant_labels, _AFM_HELVETICA_BOLD)

    text = "CUSTOMER NO"
    bare = sum(_AFM_HELVETICA_BOLD[ord(c) - 32] for c in text) / 1000.0 * 12 / 72.0
    L = 0.06
    W = round(bare - 0.005, 4)          # exact-fit minus a hair -> clips w/ padding
    nb_left = round(L + bare + 0.05, 4)  # gap fits bare text but NOT padded target
    rdl = (
        f'<?xml version="1.0"?><Report xmlns="{_RDL_NS}"><Body><ReportItems>'
        f'<Textbox Name="L"><CanGrow>false</CanGrow><Paragraphs><Paragraph>'
        f'<TextRuns><TextRun><Value>="{text}"</Value>'
        f'<Style><FontFamily>Arial</FontFamily><FontSize>12pt</FontSize>'
        f'<FontWeight>Bold</FontWeight></Style></TextRun></TextRuns>'
        f'<Style><TextAlign>Left</TextAlign></Style></Paragraph></Paragraphs>'
        f'<Style><PaddingLeft>2pt</PaddingLeft><PaddingRight>2pt</PaddingRight></Style>'
        f'<Top>0in</Top><Left>{L}in</Left><Width>{W}in</Width><Height>0.2in</Height>'
        f'</Textbox>'
        f'<Textbox Name="NR"><CanGrow>false</CanGrow><Paragraphs><Paragraph>'
        f'<TextRuns><TextRun><Value>=Fields!X.Value</Value>'
        f'<Style><FontFamily>Arial</FontFamily><FontSize>12pt</FontSize></Style>'
        f'</TextRun></TextRuns></Paragraph></Paragraphs>'
        f'<Top>0in</Top><Left>{nb_left}in</Left><Width>0.5in</Width>'
        f'<Height>0.2in</Height></Textbox>'
        f'</ReportItems><Height>1in</Height></Body><Width>8in</Width></Report>')
    root = ET.fromstring(rdl)
    _widen_clipped_constant_labels(root)
    got_w = _label_width(root)
    assert got_w > W + 1e-6, "exact-fit label must partial-widen into the gap"
    assert got_w + 1e-6 >= bare, "widened box must fit the bare text"
    assert L + got_w <= nb_left - 0.04 + 1e-6, (
        "partial widen must respect the neighbour guard gap")
    pads = [p.text for p in root.iter(f"{{{_RDL_NS}}}PaddingLeft")
            if p is not None] + [p.text for p in root.iter(f"{{{_RDL_NS}}}PaddingRight")]
    lab = [tb for tb in root.iter(f"{{{_RDL_NS}}}Textbox") if tb.get("Name") == "L"][0]
    lab_pads = [p.text for p in lab.iter(f"{{{_RDL_NS}}}PaddingLeft")] + \
               [p.text for p in lab.iter(f"{{{_RDL_NS}}}PaddingRight")]
    assert lab_pads and all(p == "0pt" for p in lab_pads), (
        "paddings must be stripped so the bare text truly fits")


def test_segment_collapse_protects_growable_wide_prose():
    """A multi-segment Oracle <text> must never be split into stacked
    CanGrow=false <Paragraph>s that overflow + clip the box: an inline
    label+value like "expires <date>" keeps its segments on ONE line, while a
    GROWABLE, ~full-printable-width prose body keeps FLOWING and is never
    folded onto one fixed line.

    This used to be a source-token grep over _emit_field_textbox
    (_growable / _wide / _keep_stacked).  That is blind to what actually
    ships -- it passes whether or not the emitter is correct -- and the
    mechanism it named has since been replaced: segments now flow as INLINE
    RUNS inside a paragraph, and the *declaration's* own line breaks decide
    the paragraph count (truth-measured; see test_inline_text_segments).
    The replacement asserts the emitted RDL, which is strictly stronger:
    every property the grep stood for is now checked on the output.
    """
    import importlib.util
    import xml.etree.ElementTree as ET
    from converter import convert

    # Load the sibling module by path: the tests directory is not a package,
    # so a plain import is not portable across pytest import modes.
    _p = Path(__file__).with_name("test_inline_text_segments.py")
    _spec = importlib.util.spec_from_file_location("_inline_seg_fixture", _p)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    NS, _source_xml = _mod.NS, _mod._source_xml

    root = ET.fromstring(
        convert(_source_xml().encode(), "INLINESEG.xml")["rdl_xml"].encode())
    boxes = []
    for tb in root.iter(f"{NS}Textbox"):
        boxes.append([[(r.find(f"{NS}Value").text or "")
                       for r in p.findall(f".//{NS}TextRun")]
                      for p in tb.findall(f".//{NS}Paragraph")])

    def _box(fragment):
        hits = [b for b in boxes
                if any(fragment in v for p in b for v in p)]
        assert len(hits) == 1, f"{fragment!r} -> {len(hits)} boxes"
        return hits[0]

    # A one-line-tall box: its two declared segments stay on ONE line, so
    # nothing overflows the box and clips the value.
    short = _box("expires")
    assert len(short) == 1, (
        f"a one-line box must emit ONE paragraph, got {len(short)} stacked "
        f"paragraphs -- they overflow a CanGrow=false box and clip it")
    assert len(short[0]) > 1, "the declared segments must survive as runs"

    # A growable, full-width prose body keeps flowing: it is NOT folded onto
    # one line, and each DECLARED line is one paragraph.
    prose = _box("any handling levied on the account.")
    assert len(prose) == 1 and "vbCrLf" in prose[0][0], (
        "a growable prose body must keep its declared line breaks")
    flowing = _box("You may mail your payment")
    assert len(flowing) == 1 and len(flowing[0]) == 3, (
        "a sentence with no declared break must flow as inline runs of ONE "
        f"paragraph; got {len(flowing)} paragraphs")

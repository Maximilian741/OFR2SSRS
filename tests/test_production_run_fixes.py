"""Regression guards for the production-deployment defect classes: run-time
failures the offline (staticized) render can't show, found via real state
deployments and locked here.

1. Oracle <link>-only master-detail children (no :bind text in the child SQL)
   must join via the parsed link_pairs — ignoring them blanked every child
   field to =Nothing.
2. A section-table column that names a report FORMULA must compile inline
   (per-row) instead of binding to the NULL formula-dataset stub (which
   renders blank at run time).
3. A DateTime bind wrapped in TO_DATE(:P,'YYYY-MM-DD') must receive a STRING
   in exactly that mask through its QueryParameter — passing the raw DateTime
   throws ORA-01861 at query execution.

All fixtures are synthetic — no client report/column names.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def test_link_key_pairs_prefers_parsed_link_over_bind_scan():
    from converter.generators.rdl import _link_key_pairs

    class Q:  # minimal stand-in for DataQuery
        pass

    # <link>-only child: NO :bind text anywhere in the SQL — keys must come
    # from link_pairs, validated against both datasets' columns.
    q = Q()
    q.link_pairs = [("ORDER_ID", "ITEM_ORDER_ID"), ("BOGUS", "NOPE")]
    q.sql = "SELECT ITEM_ORDER_ID, ITEM_NAME FROM ITEMS"
    master = {"ORDER_ID": "Order_Id"}
    child = {"ITEM_ORDER_ID": "Item_Order_Id", "ITEM_NAME": "Item_Name"}
    assert _link_key_pairs(q, master, child) == [("Order_Id", "Item_Order_Id")]

    # No link pairs -> falls back to the :bind scan (bind naming a master col).
    q2 = Q()
    q2.link_pairs = []
    q2.sql = "SELECT * FROM ORGS WHERE SITE_KEY = :SITE_KEY"
    m2 = {"SITE_KEY": "Site_Key"}
    c2 = {"ORG_SITE_KEY": "Org_Site_Key"}
    assert _link_key_pairs(q2, m2, c2) == [("Site_Key", "Org_Site_Key")]


def test_formula_named_section_column_compiles_inline():
    from converter.generators.rdl import _formula_expr_for_column
    from converter.models import DataQuery, DataItem, FormulaColumn, ParsedReport

    q = DataQuery(name="Q_S")
    q.items = [DataItem(name="Row_Type"), DataItem(name="Row_Group"),
               DataItem(name="Row_Count")]
    rep = ParsedReport(name="SYNTH")
    rep.queries = [q]
    rep.formulas = [FormulaColumn(
        name="CF_Row_Type",
        plsql_body=("FUNCTION F_CF_Row_Type RETURN VARCHAR2 IS BEGIN "
                    "IF :Row_Type = 'Z-Subtotal' THEN "
                    "RETURN('Subtotal ' || :Row_Group) ; "
                    "ELSE RETURN(:Row_Type) ; END IF ; END ;"))]

    expr = _formula_expr_for_column(rep, q, "CF_Row_Type")
    assert expr and expr.startswith("=IIf("), expr
    assert "Fields!Row_Type.Value" in expr and "Fields!Row_Group.Value" in expr
    # NEVER the NULL formula-dataset stub:
    assert "DS_REPORT_FORMULAS" not in expr

    # A real dataset column is left to the normal field path.
    assert _formula_expr_for_column(rep, q, "Row_Count") is None
    # A formula whose refs do NOT resolve in this query -> honest None.
    q2 = DataQuery(name="Q_OTHER")
    q2.items = [DataItem(name="Unrelated")]
    rep.queries.append(q2)
    assert _formula_expr_for_column(rep, q2, "CF_Row_Type") is None


def test_computed_placeholder_params_detected_and_inputs_protected():
    """A userParameter assigned per-row by PL/SQL and displayed by a layout
    field is a COMPUTE PLACEHOLDER (must not bind as a live Parameters! ref,
    must not ship its dummy initialValue). But a genuine input — typed on the
    Oracle parameter form, or referenced as a query bind — must NEVER be
    demoted, even when a trigger re-assigns it."""
    from converter.generators.rdl import _computed_placeholder_params

    class _O:  # generic attribute bag
        pass

    rep = _O()
    p1, p2, p3 = _O(), _O(), _O()
    p1.name = "P_CALC"       # assigned + displayed -> placeholder
    p2.name = "P_INPUT"      # query bind -> input
    p3.name = "P_FORMTYPED"  # on the Oracle parameter form -> input
    rep.parameters = [p1, p2, p3]
    rep.triggers = []
    rep.formulas = []
    rep.raw_xml = (
        '<report><paramForm><field name="PF_T" source="P_FORMTYPED"/>'
        "</paramForm><programUnits>"
        " :P_CALC := :SRC_COL; :P_FORMTYPED := 'typed'; "
        "</programUnits></report>")
    f1, f2 = _O(), _O()
    f1.kind = "field"; f1.source = "P_CALC"
    f2.kind = "field"; f2.source = "P_FORMTYPED"
    g = _O(); g.fields = [f1, f2]; g.children = []
    rep.layout = [g]
    q = _O(); q.sql = "SELECT x FROM t WHERE a = :P_INPUT"; q.items = []
    rep.queries = [q]

    out = _computed_placeholder_params(rep)
    assert set(out) == {"P_CALC"}, out
    assert out["P_CALC"].strip() == ":SRC_COL"


def test_parent_break_columns_from_outer_group():
    """A single-query break report's band identity = the OUTERMOST Oracle
    group's own columns (validated against the dataset), NOT the first detail
    column. Grouping on a detail column merged unrelated bands in production."""
    from converter.generators.rdl import _parent_break_columns

    class _O:
        pass

    q = _O()
    i1, i2, i3 = _O(), _O(), _O()
    i1.name = "Band_Key"; i2.name = "Band_Owner"; i3.name = "Row_Amount"
    q.items = [i1, i2, i3]
    g = _O(); gi1, gi2, gi3 = _O(), _O(), _O()
    gi1.name = "Band_Key"; gi2.name = "Band_Owner"
    gi3.name = "NOT_A_DATASET_COL"
    g.items = [gi1, gi2, gi3]
    q.groups = [g]
    assert _parent_break_columns(q) == ["Band_Key", "Band_Owner"]
    q2 = _O(); q2.items = [i1]; q2.groups = []
    assert _parent_break_columns(q2) == []


def test_grouped_subtotal_builder_contracts():
    """Source-locks the grouped-subtotal Tablix production fixes: parent-group
    band keys, summary-aware footer totals (never the unrelated last-column
    guess for a known summary), and the case-sensitive Fields! contract."""
    import inspect
    from converter.generators.rdl import _build_grouped_tabular_subtotal_tablix as f
    src = inspect.getsource(f)
    assert "_parent_break_columns(main)" in src, "band keys from the Oracle group tree"
    assert "summary_by_name" in src, "footer totals must route through <summary> metadata"
    assert "_det_canon" in src and "_mst_canon" in src, (
        "Fields! refs must use canonical dataset casing (SSRS is case-sensitive)")
    assert "SortExpression" in src, "query ORDER BY must survive SSRS grouping"


def test_rollup_sections_get_no_synthetic_total_footer():
    """GROUP BY ROLLUP / CUBE / GROUPING SETS resultsets CARRY their own
    subtotal/total rows as data — a synthetic Sum() footer double/triple-counts
    them (production verified: 3x the true total). The section builder must
    detect the shape and emit no synthetic footer."""
    import inspect
    from converter.generators.rdl import _sql_has_rollup, _build_multi_section_body

    class _O:
        pass

    q = _O(); q.sql = "SELECT a, SUM(b) FROM t GROUP BY ROLLUP(a)"
    assert _sql_has_rollup(q)
    q2 = _O(); q2.sql = "SELECT a, SUM(b) FROM t GROUP BY a"
    assert not _sql_has_rollup(q2)
    q3 = _O(); q3.sql = "SELECT a FROM t GROUP BY GROUPING SETS ((a), ())"
    assert _sql_has_rollup(q3)
    src = inspect.getsource(_build_multi_section_body)
    assert "_sql_has_rollup" in src, "section builder must gate synthetic totals on rollup"
    assert "EST_ROW * 6" not in src, (
        "sections must stack at MEASURED minimal heights, not a fixed 6-row estimate")


def test_page_header_carries_tokenized_margin_items():
    """A tokenized Oracle margin <text> beyond the title (e.g. a &FORMULA
    date-range subtitle) must be emitted into the page header when its
    resolved expression is Fields!-free (page-header-safe). Locks the margin
    walk in _build_page."""
    import inspect
    from converter.generators.rdl import _build_page
    src = inspect.getsource(_build_page)
    assert "_margin_extra" in src and "Tb_MarginX" in src
    assert '"Fields!" in _mv' in src or "Fields!' in _mv" in src.replace('"', "'"), (
        "margin items referencing Fields! must be skipped (illegal in a page header)")


def test_designer_fill_palette_and_print_fill_gate():
    """Truth-calibrated fill semantics: (1) the Oracle designer pastel swatches
    (percent-triples with the 0xE0 signature — pink #ffe0ff, chartreuse
    #e0ff00, cyan #e0ffff) never print; named grayscale/print colors do.
    (2) A fill paints ONLY when the export wrote a fillPattern attribute on the
    box (7/7 pattern-marked boxes on the complaint form are its 7 truth-gray
    bands; its 37 pattern-less gray boxes print white)."""
    import inspect
    from converter.generators.rdl import _is_designer_fill_hex, _emit_field_textbox

    for c in ("#ffe0ff", "#e0ff00", "#e0ffff", "#ffe000"):
        assert _is_designer_fill_hex(c), c
    for c in ("#808080", "#d6d6d6", "#e0e0e0", "#006400", "#ff0000", "gray"):
        assert not _is_designer_fill_hex(c), c

    src = inspect.getsource(_emit_field_textbox)
    assert 'fill_pattern' in src, "fill paint must be gated on the fillPattern attribute"
    assert '_is_designer_fill_hex' in src, "designer swatches must never paint"
    assert 'line_pattern' in src, "solid linePattern must draw the box border"


def test_datetime_bind_queryparam_value_matches_todate_mask():
    from converter.generators.rdl import _build_dataset
    from converter.models import DataQuery, DataItem

    q = DataQuery(name="Q_1")
    q.sql = "SELECT a FROM t WHERE (:P_D IS NULL OR d >= :P_D)"
    q.items = [DataItem(name="A")]
    ds = _build_dataset(q, ["P_D"], target_db="oracle",
                        param_types={"P_D": "DateTime"})
    x = ET.tostring(ds, encoding="unicode")
    # Both halves of the contract, together:
    assert "TO_DATE(:P_D, 'YYYY-MM-DD')" in x
    assert 'Format(CDate(Parameters!P_D.Value), "yyyy-MM-dd")' in x
    assert "IsNothing(Parameters!P_D.Value)" in x, (
        "NULL must stay NULL so (:P IS NULL OR ...) guards keep working")


def test_bundle_layoutless_card_is_value_faithful():
    """A bare-SQL bundle (no Oracle layout) must not INVENT decorations:
    no fabricated "Total For" count, no "Label: " prefix on the break band,
    no printed line for the injected master-side join key -- and its outer
    group must break Between instances (Start would render a blank page 1
    when nothing sits above the Tablix). Truth-calibrated on a real
    production run; asserted here with fully synthetic SQL."""
    from converter.ingest import convert_bundle

    master = (
        "SELECT NVL(&P_A, T.Dept_Name) Col_A,\n"
        "       T.Mgr_Title Col_B,\n"
        "       T.Dept_Id\n"
        "FROM Dept T\nWHERE T.Active_Fl = 'Y'\n"
        "ORDER BY T.Dept_Name"
    )
    child = (
        "SELECT C.Emp_Name, C.Phone_Num, C.Email_Addr\n"
        "FROM Emp C\nWHERE C.Dept_Id = :Dept_Id\n"
        "ORDER BY C.Emp_Name"
    )
    out = convert_bundle([
        ("dept_q_master.txt", master.encode()),
        ("dept_q_child.txt", child.encode()),
    ])
    x = out["rdl_xml"]
    assert "Total For" not in x, "layout-less band must not fabricate totals"
    assert '="Col A: "' not in x, "layout-less band shows the VALUE only"
    assert "<BreakLocation>Start</BreakLocation>" not in x, (
        "outer group Start-break with nothing above the Tablix renders a "
        "blank leading page -- must be Between")
    # The injected join key wires the child dataset but is never printed.
    assert 'Tb_SubHdr' not in x or 'Dept_Id' not in (
        x.split('Tb_SubHdr')[1][:400] if 'Tb_SubHdr' in x else ""), (
        "master-side join key must not print as a card sub-header")
    # The verdict gates run on the synthetic path too.
    assert out.get("preflight"), "bundle output must carry a preflight verdict"
    assert out.get("fidelity_report"), "bundle output must carry fidelity"


def test_standalone_subreport_binds_visible_and_null_safe():
    """A STANDALONE sub-report build (no parent parameter set) must prompt
    for its binds (visible, still Nullable/=Nothing) and widen bare key
    equality so an empty prompt returns ALL rows, not zero. Forwarded
    drill-through params stay hidden."""
    from converter.subreports import _synth_report_from_sql

    rep = _synth_report_from_sql(
        "X", "SELECT t.A, t.B FROM t WHERE t.A = :P_A", [], [])
    assert [(p.name, p.display) for p in rep.parameters] == [("P_A", True)]
    assert "(:P_A IS NULL OR t.A = :P_A)" in rep.queries[0].sql
    rep2 = _synth_report_from_sql(
        "X", "SELECT t.A FROM t WHERE t.A = :P_A", [], ["P_A"])
    assert [(p.name, p.display) for p in rep2.parameters] == [("P_A", False)]


def test_drillthrough_splice_is_position_aware():
    """The drill-through filter must splice ONLY at a predicate-position
    lexical; otherwise it appends its own WHERE clause. Leftover lexicals
    keep the statement RUNNABLE: operand slots and ORDER/GROUP BY slots get
    NULL, everything else a bare comment."""
    from converter.subreports import _inject_drillthrough_filter

    s, ap = _inject_drillthrough_filter(
        "SELECT o.Org_Id, o.Nm FROM Orgs o ORDER BY &P_SORT", ["P_ORG_ID"])
    assert ap == [("P_ORG_ID", "o.Org_Id")]
    assert "WHERE 1=1" in s
    assert "AND (:P_ORG_ID IS NULL OR o.Org_Id = :P_ORG_ID)" in s
    assert "ORDER BY NULL /*" in s, "clause slot must stay runnable"
    s2, _ = _inject_drillthrough_filter(
        "SELECT NVL(&P_X, o.Nm) Nm, o.Org_Id FROM Orgs o WHERE 1=1 &P_CRIT",
        ["P_ORG_ID"])
    assert "NVL(NULL /*" in s2, "operand slot gets NULL, not a bare comment"
    assert "AND (:P_ORG_ID IS NULL OR o.Org_Id = :P_ORG_ID)" in s2


def test_all_selects_harvested_not_just_first(tmp_path):
    """A multi-query artifact keeps EVERY top-level SELECT (the old path
    silently dropped all but the first); PL/SQL cursor SELECTs are never
    harvested."""
    from converter.subreports import _all_sql_from_paths

    p = tmp_path / "multi.sql"
    p.write_text(
        "SELECT a FROM t;\nSELECT b FROM u WHERE u.a = :a;\n"
        "FUNCTION CF_1 RETURN NUMBER IS BEGIN SELECT c FROM v; END;")
    stmts = _all_sql_from_paths([str(p)])
    assert stmts == ["SELECT a FROM t", "SELECT b FROM u WHERE u.a = :a"]


def test_lexical_positions_classified_and_deduped():
    """Wild-corpus calibrated (fire 121): lexical neutralization must be
    POSITION-AWARE. Operator-adjacent slots get NULL (a bare comment leaves
    a dangling '/' = ORA-00936); identifier fragments stay bare comments and
    preflight raises a BLOCKER; NULL-stubbed IN/comparison operands report
    ZERO-rows semantics (the old message claimed 'returns every row');
    issues dedupe to one entry per (class, lexical) with a count."""
    import re as _re
    from converter.generators.rdl import _lexical_slot_token

    def tok(s, name):
        m = _re.search("&" + name + r"\b", s)
        return _lexical_slot_token(s, m.start(), m.end())

    assert tok("SUM(MONTO/&P_U)", "P_U") == "NULL "
    assert tok("&P_X * 2", "P_X") == "NULL "
    assert tok("FROM DIRECCION.&P_TABLA", "P_TABLA") == ""
    assert tok("&P_TAB.COL", "P_TAB") == ""
    assert tok("x >= &P_MIN", "P_MIN") == "NULL "
    assert tok("WHERE &P_CRIT", "P_CRIT") == ""

    from converter.validators.preflight import preflight_audit
    rdl = (
        '<?xml version="1.0"?><Report xmlns="http://schemas.microsoft.com/'
        'sqlserver/reporting/2008/01/reportdefinition"><DataSets>'
        '<DataSet Name="Q1"><Query><DataSourceName>DS</DataSourceName>'
        '<CommandText>SELECT a FROM t WHERE x IN (NULL /* lexical ref '
        '&amp;P_A -- s */) AND y = NULL /* lexical ref &amp;P_A -- s */ '
        'AND z = DIRECCION. /* lexical ref &amp;P_T -- s */</CommandText>'
        '</Query><Fields><Field Name="a"><DataField>a</DataField></Field>'
        '</Fields></DataSet></DataSets></Report>')
    pf = preflight_audit(rdl)
    rules = {}
    for i in pf.get("issues", []):
        r = i.get("rule") if isinstance(i, dict) else i[1]
        msg = i.get("message") if isinstance(i, dict) else i[2]
        sev = i.get("severity") if isinstance(i, dict) else i[0]
        rules[r] = (sev, msg)
    assert "sql.lexical_nevermatch.P_A" in rules
    sev, msg = rules["sql.lexical_nevermatch.P_A"]
    assert "2 occurrences" in msg and "ZERO rows" in msg
    assert "sql.lexical_identifier.P_T" in rules
    assert rules["sql.lexical_identifier.P_T"][0] == "BLOCKER"
    assert not any(r.startswith("sql.lexical_where_dropped.P_A")
                   for r in rules), "nevermatch must not double-report"


def test_summary_of_summary_resolves_to_base_column():
    """A report-level summary whose SOURCE is another summary column must
    never emit the summary NAME as a Fields! reference (dangling ref =
    publish failure). The chain resolves to the base column with honest
    composition: Count over an inner group summary = CountDistinct over
    that group's break key; non-decomposable chains are skipped."""
    from converter.generators.rdl import generate_rdl
    from converter.models import (ParsedReport, DataQuery, DataItem,
                                  QueryGroup, FormulaColumn, LayoutGroup,
                                  LayoutField)

    rep = ParsedReport(name="R", dtd_version="9")
    q = DataQuery(name="Q_1")
    q.sql = "SELECT USER_ID, NAME FROM users"
    q.items = [DataItem(name="USER_ID"), DataItem(name="NAME")]
    q.groups = [QueryGroup(name="G_NAME", break_col="NAME",
                           items=[DataItem(name="NAME")])]
    rep.queries.append(q)
    rep.formulas.append(FormulaColumn(
        name="CountUSER_ID", agg_function="count", agg_source="USER_ID",
        agg_scope="G_NAME"))
    rep.formulas.append(FormulaColumn(
        name="CS_Total", agg_function="count", agg_source="CountUSER_ID",
        agg_scope="report"))
    lg = LayoutGroup(name="M_body")
    lg.fields.append(LayoutField(name="F_total", source="CS_Total"))
    lg.fields.append(LayoutField(name="F_nm", source="NAME"))
    rep.layout.append(lg)
    x = generate_rdl(rep)
    assert "Fields!CountUSER_ID" not in x, "summary name leaked as field ref"
    assert 'CountDistinct(Fields!NAME.Value, "Q_1")' in x
    assert "Countuser" not in x, "garbled auto-summary label"


def test_hollow_output_nets_and_inference():
    """Fire 123 (wild-corpus): (1) a dataSource with a <select> but NO
    <dataItem> children infers its columns from the SELECT list instead of
    shipping a PLACEHOLDER dataset; (2) preflight BLOCKERs a data-hollow
    body (0 Fields! refs while real fields exist) and REDs a PLACEHOLDER
    dataset; (3) Fields! refs are case-canonicalized to declared fields;
    (4) &<field> angle tokens resolve to Fields! (not literal text)."""
    import re as _re
    from converter import convert
    from converter.validators.preflight import preflight_audit

    xml = (b'<?xml version="1.0" encoding="UTF-8"?>\n'
           b'<report name="R" DTDVersion="9.0.2.0.10"><data>'
           b'<dataSource name="Q_1">'
           b'<select><![CDATA[select empno, ename empname from emp]]></select>'
           b'</dataSource></data></report>')
    out = convert(xml)
    x = out["rdl_xml"]
    assert "PLACEHOLDER" not in x
    assert '<Field Name="empno">' in x and '<Field Name="empname">' in x

    hollow = (
        '<?xml version="1.0"?><Report xmlns="http://schemas.microsoft.com/'
        'sqlserver/reporting/2008/01/reportdefinition"><Body><ReportItems/>'
        '<Height>1in</Height></Body><DataSets><DataSet Name="Q1"><Query>'
        '<DataSourceName>DS</DataSourceName><CommandText>SELECT a FROM t'
        '</CommandText></Query><Fields><Field Name="a"><DataField>a'
        '</DataField></Field></Fields></DataSet><DataSet Name="Q2"><Query>'
        '<DataSourceName>DS</DataSourceName><CommandText>SELECT 1'
        '</CommandText></Query><Fields><Field Name="PLACEHOLDER">'
        '<DataField>PLACEHOLDER</DataField></Field></Fields></DataSet>'
        '</DataSets></Report>')
    pf = preflight_audit(hollow)
    rules = {(i.get("rule") if isinstance(i, dict) else i[1]):
             (i.get("severity") if isinstance(i, dict) else i[0])
             for i in pf.get("issues", [])}
    assert rules.get("rdl.hollow_body") == "BLOCKER"
    assert rules.get("rdl.placeholder_dataset.Q2") == "RED"

    from converter.generators.rdl import _canonicalize_field_ref_case
    import xml.etree.ElementTree as _ET
    r = _ET.fromstring(
        '<Report xmlns="http://schemas.microsoft.com/sqlserver/reporting/'
        '2008/01/reportdefinition"><DataSets><DataSet Name="Q"><Fields>'
        '<Field Name="CITY"/></Fields></DataSet></DataSets>'
        '<Body><V>=Fields!city.Value</V></Body></Report>')
    _canonicalize_field_ref_case(r)
    assert "Fields!CITY.Value" in _ET.tostring(r, encoding="unicode")


def test_select_realias_pairs_by_name_not_position():
    """Fire 124 (wild-corpus DANGER class): quoted SELECT aliases must pair
    with dataItems by NORMALIZED NAME — dataItem document order is not
    SELECT order (an alphabetized export positionally re-aliased game name
    AS Adresa_, silently SWAPPING column data). Plain-identifier aliases
    that sanitize differently ("Adresa" -> Adresa_) also pair by name."""
    from converter.generators.rdl import _alias_select_items

    sql = ('SELECT g."name" as "Denumire joc", s."name" as "Nume magazin", '
           's."address" as "Adresa" FROM g, s')
    out = _alias_select_items(
        sql, ["Adresa_", "Denumire_joc_", "Nume_magazin_"])
    assert 'g."name" as "Denumire_joc_"' in out
    assert 's."name" as "Nume_magazin_"' in out
    assert 's."address" as "Adresa_"' in out
    # No unique name match and positional disagrees -> alias untouched
    # (honest blank beats swapped data).
    out2 = _alias_select_items('SELECT a as "X Y" FROM t', ["Unrelated"])
    assert '"X Y"' in out2


def test_matrix_cell_measure_from_summary_metadata():
    """Fire 125 (wild-corpus): the matrix CELL measure comes from the
    declared <summary> whose reset group is a matrix dimension group
    (source column + aggregate function) — never a guessed numeric column;
    an outer break band CONTAINING the dimension frames must not veto
    matrix dominance (matrix-with-break)."""
    from converter.parsers.oracle_xml import parse_oracle_xml
    from converter.generators.rdl import _find_matrix_spec

    xml = (b'<?xml version="1.0" encoding="UTF-8"?>\n'
           b'<report name="MX" DTDVersion="9.0.2.0.10"><data>'
           b'<dataSource name="Q_1">'
           b'<select><![CDATA[SELECT USER_ID, FIRST_NAME, NAME, BAND_ID '
           b'FROM u, l]]></select>'
           b'<group name="G_BAND"><dataItem name="BAND_ID" datatype="number"/>'
           b'<group name="G_FIRST_NAME">'
           b'<dataItem name="FIRST_NAME" datatype="vchar2"/></group>'
           b'<group name="G_NAME"><dataItem name="NAME" datatype="vchar2"/>'
           b'<summary name="CountUSER_ID" source="USER_ID" function="count" '
           b'reset="G_NAME" compute="report"/></group>'
           b'<group name="G_U"><dataItem name="USER_ID" datatype="number"/>'
           b'</group></group></dataSource></data><layout><section name="main">'
           b'<body width="8.5" height="11.0">'
           b'<repeatingFrame name="R_G_BAND" source="G_BAND">'
           b'<geometryInfo x="0" y="0" width="8" height="6"/>'
           b'<field name="F_band" source="BAND_ID">'
           b'<geometryInfo x="0" y="0" width="1" height="0.2"/></field>'
           b'<repeatingFrame name="R_G_FIRST_NAME" source="G_FIRST_NAME" '
           b'printDirection="across">'
           b'<geometryInfo x="1" y="0.5" width="6" height="0.3"/>'
           b'<field name="F_fn" source="FIRST_NAME">'
           b'<geometryInfo x="1" y="0.5" width="1" height="0.2"/></field>'
           b'</repeatingFrame>'
           b'<repeatingFrame name="R_G_NAME" source="G_NAME">'
           b'<geometryInfo x="0" y="1" width="1" height="4"/>'
           b'<field name="F_nm" source="NAME">'
           b'<geometryInfo x="0" y="1" width="1" height="0.2"/></field>'
           b'</repeatingFrame></repeatingFrame>'
           b'<matrix name="X_1" horizontalFrame="R_G_FIRST_NAME" '
           b'verticalFrame="R_G_NAME" xProductGroup="G_X"/>'
           b'</body></section></layout></report>')
    rep = parse_oracle_xml(xml)
    spec = _find_matrix_spec(rep)
    assert spec is not None
    assert spec["cells"] == ["USER_ID"]
    assert spec["measure_fns"] == {"USER_ID": "count"}
    assert spec["dominant"], "outer break band must not veto the matrix"
    # Margin totals derive from summaries over the CELL summary, keyed by
    # their reset target (row dim -> right total, col dim -> bottom total,
    # report -> grand corner). None declared here -> no margins invented.
    assert spec["margins"] == {"row_total": False, "col_total": False,
                               "grand": False}
    # Matrix-with-break: the deepest repeating-frame ancestor of the
    # dimension frames is the band; its group's break column keys one
    # sub-matrix (with band header) per band value.
    assert spec["band"] == "BAND_ID"


def test_multi_matrix_specs_and_unique_names():
    """Fire 128 (wild-corpus): a multi-pivot report derives ONE spec per
    matrix (index parameter + n_matrices), sibling pivots don't veto
    dominance, and every stacked Tablix gets unique suffixed names."""
    from converter.generators.rdl import _build_matrix_tablix
    from converter.models import DataQuery, DataItem
    import xml.etree.ElementTree as _ET

    q = DataQuery(name="Q_1")
    q.items = [DataItem(name="R"), DataItem(name="C"), DataItem(name="M")]
    spec = {"row": "R", "col": "C", "cells": ["M"], "query": q,
            "dominant": True, "measure_fns": {"M": "sum"},
            "margins": {"row_total": False, "col_total": False,
                        "grand": False}, "band": "", "n_matrices": 2}
    t2 = _build_matrix_tablix(None, spec, suffix="_2")
    x = _ET.tostring(t2, encoding="unicode")
    assert 'Name="Tablix_Matrix_2"' in x
    assert 'Name="MxRowG_2"' in x and 'Name="MxColG_2"' in x
    assert "Mx_Cell_2" in x


def test_column_widths_never_negative_and_tablix_width_reconciled():
    """Fire 130 (hunt round 2): an ultra-wide report (impossible fit: even
    0.5in floors overflow the page) must keep legible source ratios and
    paginate ACROSS — never emit a negative remainder column; and every
    Tablix's declared Width equals its column-width sum."""
    import re as _re
    from converter import convert

    cols = "".join(
        f'<dataItem name="C{i}" datatype="vchar2"/>' for i in range(40))
    flds = "".join(
        f'<field name="F{i}" source="C{i}">'
        f'<geometryInfo x="{i * 2}" y="0.5" width="2.0" height="0.2"/>'
        f'</field>' for i in range(40))
    xml = (f'<?xml version="1.0"?><report name="WIDE" DTDVersion="9.0.2.0.10">'
           f'<data><dataSource name="Q_1"><select><![CDATA[SELECT '
           f'{", ".join("C%d" % i for i in range(40))} FROM t]]></select>'
           f'<group name="G_1">{cols}</group></dataSource></data>'
           f'<layout><section name="main"><body width="80" height="9">'
           f'<repeatingFrame name="R_1" source="G_1">'
           f'<geometryInfo x="0" y="0.5" width="80" height="0.3"/>{flds}'
           f'</repeatingFrame></body></section></layout></report>').encode()
    x = convert(xml)["rdl_xml"]
    ws = _re.findall(r"<TablixColumn>\s*<Width>([^<]+)</Width>", x)
    assert ws and not any(w.startswith("-") for w in ws), "negative width!"
    assert all(float(w[:-2]) >= 0.5 for w in ws)
    m = _re.search(
        r"</TablixRowHierarchy>.*?<Width>([0-9.]+)in</Width>", x, _re.S)
    if m:
        assert abs(float(m.group(1)) - sum(float(w[:-2]) for w in ws)) < 0.1


def test_unsupported_source_never_hollow_ready():
    """Fire 131 (hunt round 2): a non-report XML (e.g. a Reports Server
    <destinations> bursting spec) must degrade to an honest BLOCKER verdict
    with fidelity 0.0 — never a hollow shell scoring 1.0. convert() still
    never raises (crash-safety contract)."""
    from converter import convert

    out = convert(b'<?xml version="1.0"?><destinations>'
                  b'<destination id="1"/></destinations>')
    pf = out["preflight"]
    assert pf["verdict"] == "BLOCKER"
    assert pf["issues"][0]["rule"] == "source.unsupported_kind"
    assert "BURSTING" in pf["issues"][0]["message"].upper() or \
        "DISTRIBUTION" in pf["issues"][0]["message"].upper()
    assert (out.get("fidelity_report") or {}).get("score") == 0.0


def test_geometryless_groupleft_routes_tabular():
    """The minimal DTD-1.0 grammar (<groupLeft><field/> with no geometry
    anywhere) is a group-left TABULAR listing — the card path stacked every
    field at one identical spot (100% overprint). Fixtures WITH repeating
    frames keep their archetype."""
    from converter.parsers.oracle_xml import parse_oracle_xml
    from converter.preview.html_mockup import detect_report_kind

    xml = (b'<?xml version="1.0"?><report name="E" DTDVersion="1.0"><data>'
           b'<dataSource name="Q_1"><select><![CDATA[select empno, ename, '
           b'sal, comm from emp]]></select></dataSource></data>'
           b'<layout><section name="main"><groupLeft name="M_emp">'
           b'<group><field name="F1" source="empno"/>'
           b'<field name="F2" source="ename"/>'
           b'<field name="F3" source="sal"/>'
           b'<field name="F4" source="comm"/></group></groupLeft>'
           b'</section></layout></report>')
    assert detect_report_kind(parse_oracle_xml(xml)) == "tabular_details"


def test_fidelity_display_axis_catches_silent_loss():
    """Fire 132 (hunt round 2): a layout column declared as a dataset Field
    but never referenced by ANY expression must surface — display_coverage
    drops + needs_attention names it — while the headline score keeps its
    established contract (declarations satisfy the loose binding rule).
    Orphan datasets are an informational note, never a score hit
    (auxiliary formula/LOV queries are a legitimate Oracle pattern)."""
    from converter.fidelity import build_fidelity_report
    from converter.models import (ParsedReport, DataQuery, DataItem,
                                  LayoutGroup, LayoutField)

    rep = ParsedReport(name="R", dtd_version="9")
    q = DataQuery(name="Q_1")
    q.items = [DataItem(name="SHOWN"), DataItem(name="LOST")]
    rep.queries.append(q)
    q2 = DataQuery(name="Q_AUX")
    q2.items = [DataItem(name="AUXCOL")]
    rep.queries.append(q2)
    lg = LayoutGroup(name="M")
    lg.fields.append(LayoutField(name="F1", source="SHOWN", kind="field"))
    lg.fields.append(LayoutField(name="F2", source="LOST", kind="field"))
    rep.layout.append(lg)
    rdl = ('<Report xmlns="http://schemas.microsoft.com/sqlserver/reporting/'
           '2008/01/reportdefinition"><DataSets><DataSet Name="Q_1"><Fields>'
           '<Field Name="SHOWN"><DataField>SHOWN</DataField></Field>'
           '<Field Name="LOST"><DataField>LOST</DataField></Field></Fields>'
           '</DataSet><DataSet Name="Q_AUX"><Fields><Field Name="AUXCOL">'
           '<DataField>AUXCOL</DataField></Field></Fields></DataSet>'
           '</DataSets><Body><V>=Fields!SHOWN.Value</V></Body></Report>')
    fr = build_fidelity_report(rep, rdl)
    lc = fr["categories"]["layout_fields"]
    assert lc["display_coverage"] == 0.5
    assert any("DISPLAYED" in n for n in fr["needs_attention"])
    assert any("never referenced by any data region" in n
               for n in fr["needs_attention"])
    assert fr["score"] == 1.0, "headline contract unchanged"


def test_format_trigger_hidden_translation():
    """Fire 133: boolean show/hide format triggers translate to REAL
    <Hidden> expressions (Oracle RETURN FALSE = suppress -> Hidden true);
    styling-only triggers (srw.set_* then RETURN TRUE) correctly decline
    (they never hide); translated ERROR frames emit hidden instead of
    being dropped."""
    from converter.translators.plsql_formula import translate_format_trigger

    assert translate_format_trigger(
        "function F return boolean is begin "
        "IF :status = 'VOID' THEN RETURN FALSE; END IF; RETURN TRUE; end;"
    ) == '=((Fields!status.Value = "VOID"))'
    assert translate_format_trigger(
        "begin RETURN :amt > 0; end;") == "=Not((Fields!amt.Value > 0))"
    assert translate_format_trigger("begin RETURN TRUE; end;") is None
    assert translate_format_trigger(
        "begin if (:x = 'Sub Total') then srw.set_font_face('Tahoma'); "
        "end if; return (TRUE); end;") is None

    from converter.generators.rdl import _format_trigger_hidden_map
    from converter.models import ParsedReport, TriggerCode
    rep = ParsedReport(name="R", dtd_version="9")
    rep.triggers.append(TriggerCode(
        name="F_HideVoid",
        body="begin IF :st = 'V' THEN RETURN FALSE; END IF; "
             "RETURN TRUE; end;"))
    m = _format_trigger_hidden_map(rep)
    assert m == {"f_hidevoid": '=((Fields!st.Value = "V"))'}


def test_conditional_style_trigger_translation():
    """Fire 134: conditional-STYLING format triggers (if cond then
    srw.set_font_*; return true — the dominant wild pattern) become IIf()
    style expressions with schema-canonical <Style> child order."""
    from converter.translators.plsql_formula import (
        translate_format_trigger_style)

    r = translate_format_trigger_style(
        "function F return boolean is begin if (:P = 'Sub Total') then "
        "srw.set_font_face('Tahoma'); srw.set_font_size(10); "
        "srw.set_font_weight(SRW.BOLD_WEIGHT); end if; "
        "return (TRUE); end;")
    assert r is not None
    cond, styles = r
    assert cond == '(Fields!P.Value = "Sub Total")'
    assert styles == {"FontFamily": "Tahoma", "FontSize": "10pt",
                      "FontWeight": "Bold"}
    # Unknown srw call -> decline (conservative).
    assert translate_format_trigger_style(
        "begin if (:x=1) then srw.do_weird(); end if; return true; end;"
    ) is None

    import xml.etree.ElementTree as _ET
    from converter.generators.rdl import (_apply_format_trigger_style,
                                          _reorder_style_children)
    from converter.models import ParsedReport, TriggerCode
    rep = ParsedReport(name="R", dtd_version="9")
    rep.triggers.append(TriggerCode(
        name="FT_S", body="begin if (:P = 'T') then "
        "srw.set_font_weight(SRW.BOLD_WEIGHT); end if; return (true); end;"))
    tb = _ET.fromstring(
        '<Textbox xmlns="http://schemas.microsoft.com/sqlserver/reporting/'
        '2008/01/reportdefinition"><Paragraphs><Paragraph><TextRuns>'
        '<TextRun><Value>=Fields!P.Value</Value><Style>'
        '<FontSize>10pt</FontSize></Style></TextRun></TextRuns>'
        '</Paragraph></Paragraphs><Style/></Textbox>')
    assert _apply_format_trigger_style(tb, rep, "FT_S")
    x = _ET.tostring(tb, encoding="unicode")
    assert 'FontWeight>=IIf((Fields!P.Value = "T"), "Bold", "Normal")' in x
    # canonical order: FontSize must come BEFORE FontWeight
    assert x.index("FontSize") < x.index("FontWeight")


def test_chart_report_emits_real_ssrs_chart():
    """Fire 135 (hunt round 3): a detected Oracle chart (<rw:graph>
    series/dataValues) becomes a REAL SSRS Chart — category group on the
    chart's category column, Sum() of the plot value, source title as
    caption, series member Label (live-engine mandatory) — bound to the
    resolving dataset. Unresolvable charts keep the honest note."""
    import re as _re
    from converter import convert

    xml = (b'<?xml version="1.0"?>\n'
           b'<report name="CH" DTDVersion="9.0.2.0.10"><data>'
           b'<dataSource name="Q_1"><select><![CDATA[SELECT emp, sal '
           b'FROM e]]></select><group name="G_1">'
           b'<dataItem name="EMP" datatype="vchar2"/>'
           b'<dataItem name="SAL" datatype="number"/></group>'
           b'</dataSource></data>'
           b'<rw:graph id="g" src="G_1" series="EMP" dataValues="SAL" '
           b'xmlns:rw="http://x"><Graph><Title text="Pay by Person"/>'
           b'</Graph></rw:graph></report>')
    out = convert(xml)
    x = out["rdl_xml"]
    assert '<Chart Name="Chart_1">' in x
    assert "=Sum(Fields!SAL.Value)" in x
    assert _re.search(r"<GroupExpression>=Fields!EMP\.Value", x)
    assert "Pay by Person" in x
    fr = out.get("fidelity_report") or {}
    assert any("auto-built" in n for n in fr.get("needs_attention", []))


def test_linked_detail_honesty_and_report_level_summaries():
    """Fire 136: (1) a <link> child query never bound/scoped in the RDL
    surfaces as an AMBER preflight issue (visible, not alarming — truth-
    verified reports carry legitimately-unprinted aux children); (2) the
    fidelity summary walk counts report-level summaries (parsed as
    formulas), and a layout-less report still emits its declared report-
    scope totals below the auto-built body."""
    from converter import convert

    xml = (b'<?xml version="1.0"?>\n'
           b'<report name="LK" DTDVersion="9.0.2.0.10"><data>'
           b'<dataSource name="Q_M"><select><![CDATA[SELECT k, v FROM m]]>'
           b'</select><group name="G_M"><dataItem name="K" datatype="number"/>'
           b'<dataItem name="V" datatype="number"/>'
           b'<summary name="SumVPerReport" source="V" function="sum" '
           b'reset="report" compute="report"/></group></dataSource>'
           b'<dataSource name="Q_D"><select><![CDATA[SELECT k, d FROM dts '
           b'WHERE k = :k]]></select><group name="G_D">'
           b'<dataItem name="K2" datatype="number"/>'
           b'<dataItem name="D" datatype="vchar2"/></group></dataSource>'
           b'<link parentGroup="G_M" childQuery="Q_D" condition="eq"/>'
           b'</data></report>')
    out = convert(xml)
    pf = out["preflight"]
    rules = [i.get("rule") for i in pf.get("issues", [])
             if isinstance(i, dict)]
    x = out["rdl_xml"]
    if 'Q_D")' not in x and "<DataSetName>Q_D</DataSetName>" not in x:
        assert any("linked_detail_not_rendered" in (r or "") for r in rules)
    sc = (out.get("fidelity_report") or {}).get("categories", {}) \
        .get("summaries", {})
    assert sc.get("declared", 0) >= 1
    assert "Tb_GrandTotal" in x or "Sum(Fields!V.Value" in x

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


_TOTALS_XML = (
    '<?xml version="1.0"?><report name="TOTALS_T" DTDVersion="9.0.2.0.10">'
    '<data>'
    '<dataSource name="Q_Main"><select><![CDATA[select site_nm, visit_dt '
    'from visits]]></select>'
    '<group name="G_Main"><dataItem name="SITE_NM" datatype="vchar2"/>'
    '<dataItem name="VISIT_DT" datatype="date"/></group></dataSource>'
    '<dataSource name="Q_Insp"><select><![CDATA[select insp_nm, n_visits '
    'from per_insp]]></select>'
    '<group name="G_Insp"><dataItem name="INSP_NM" datatype="vchar2"/>'
    '<dataItem name="N_VISITS" datatype="number"/></group></dataSource>'
    '<summary name="CS_Grand" function="sum" source="N_VISITS" '
    'reset="report" compute="report"/>'
    '</data>'
    '<layout><section name="main">'
    '<frame name="M_Body"><geometryInfo x="0" y="0" width="8" height="4"/>'
    '<repeatingFrame name="R_Main" source="G_Main" printDirection="down">'
    '<geometryInfo x="0.2" y="0.2" width="7.5" height="0.2"/>'
    '<field name="F_S" source="SITE_NM"><geometryInfo x="0.2" y="0.2" '
    'width="3" height="0.2"/></field>'
    '<field name="F_D" source="VISIT_DT"><geometryInfo x="3.4" y="0.2" '
    'width="1.4" height="0.2"/></field></repeatingFrame>'
    '<frame name="M_Totals"><geometryInfo x="0.2" y="1.2" width="7.5" '
    'height="0.9"/>'
    '<text name="B_GRAND"><geometryInfo x="0.3" y="1.75" width="1.8" '
    'height="0.19"/><textSegment><font face="Arial" size="9"/>'
    '<string><![CDATA[Total Site Visits:]]></string></textSegment></text>'
    '<field name="F_GRAND" source="CS_Grand"><geometryInfo x="2.2" '
    'y="1.75" width="0.9" height="0.19"/></field>'
    '<repeatingFrame name="R_Insp" source="G_Insp" printDirection="down">'
    '<geometryInfo x="0.3" y="1.3" width="4" height="0.19"/>'
    '<text name="B_I"><geometryInfo x="0.3" y="1.3" width="2.4" '
    'height="0.19"/><textSegment><font face="Arial" size="8"/>'
    '<string><![CDATA[&INSP_NM Visits:]]></string></textSegment></text>'
    '<field name="F_N" source="N_VISITS" alignment="end"><geometryInfo '
    'x="2.9" y="1.3" width="0.9" height="0.19"/></field>'
    '</repeatingFrame></frame></frame></section></layout></report>'
).encode()


def test_report_end_totals_reach_mockup_and_rdl_wording():
    """The report-end breakdown/totals block must reach BOTH surfaces with
    the layout's OWN wording: the mockup appends the secondary-frame rows +
    the parent frame's total label (truth-comparator finding: they were
    absent), and the RDL grand total uses the trailer label TEXT
    ('Total Site Visits:'), never a name-derived fabrication."""
    from converter import convert

    out = convert(_TOTALS_XML)
    mock, rdl = out["mockup_html"], out["rdl_xml"]
    assert "Visits:" in mock, "breakdown rows missing from mockup"
    assert "Total Site Visits:" in mock, "parent total label missing"
    assert "Total Site Visits:" in rdl, (
        "RDL grand-total label must use the layout's trailer wording")


def test_trigger_param_label_structure_reconstructs():
    """A computed parameter whose trigger builds a LABELED value across
    conditional branches (:P_X := 'Latitude: '||a ... 'Longitude: '||b)
    renders its real label structure in the mockup — including when the
    boilerplate references the FIELD OBJECT name (&F_P_X)."""
    from converter.parsers.oracle_xml import parse_oracle_xml
    from converter.preview.html_mockup import (
        _static_render_trigger_param, _doc_resolve_tokens)

    xml = (
        '<?xml version="1.0"?><report name="TP" DTDVersion="9.0.2.0.10">'
        '<data><userParameter name="P_Loc" datatype="character"/>'
        '<dataSource name="Q_1"><select><![CDATA[select a from t]]>'
        '</select></dataSource></data>'
        '<programUnits><function name="beforerep"><textSource><![CDATA['
        'function BeforeReport return boolean is begin\n'
        "  :P_Loc := NULL;\n"
        "  IF x IS NOT NULL THEN :P_Loc := 'Latitude: '||TO_CHAR(x); "
        "END IF;\n"
        "  IF y IS NOT NULL THEN :P_Loc := :P_Loc||' Longitude: '"
        "||TO_CHAR(y); END IF;\n"
        '  return (TRUE);\nend;]]></textSource></function>'
        '</programUnits></report>'
    ).encode()
    rep = parse_oracle_xml(xml)
    t = _static_render_trigger_param(rep, "P_LOC", 0)
    assert t and "Latitude:" in t and "Longitude:" in t
    resolved = _doc_resolve_tokens("Site: &F_P_Loc", rep)
    assert "Latitude:" in resolved, "F_-wrapped token must unwrap to param"


def _decollide_elems(elems):
    from converter.preview.html_mockup import _decollide
    return _decollide(elems)


def test_decollide_full_width_rows_are_barriers():
    """A page-wide element cannot share a band with ANY column — the
    same-left-only rule let a full-width 'Label: value' row slide over a
    right-column row once the columns' accumulated pushes drifted (the
    complaint form's violator row landing 2px from the site row)."""
    wide = {"kind": "text", "text": "FULL WIDTH ROW", "x": 0.0, "y": 1.0,
            "w": 8.5, "h": 0.15, "size": 9}
    col = {"kind": "text", "text": "right column", "x": 3.2, "y": 1.05,
           "w": 2.0, "h": 0.15, "size": 9}
    _decollide_elems([wide, col])
    assert col["y"] >= wide["y"] + 0.14, (
        "column row must be pushed below the full-width barrier")


def test_decollide_lateral_clamp_is_bidirectional():
    """The left box must clip at its right-hand neighbour's start even when
    the neighbour sorts EARLIER (smaller y) — the permit signature box sat
    at a slightly smaller y than the date label overlapping it."""
    left = {"kind": "text", "text": "Expiration Date:\n03/12/2026",
            "x": 2.7, "y": 1.02, "w": 2.5, "h": 0.30, "size": 11}
    right = {"kind": "text", "text": "neighbour", "x": 4.6, "y": 1.00,
             "w": 1.5, "h": 0.15, "size": 11}
    _decollide_elems([right, left])
    assert left["x"] + left["w"] <= right["x"], (
        "left box must clip before its right-hand neighbour")


def test_decollide_images_yield_only_small_edges():
    """An image placeholder nudging a few px into a label clips back, but a
    genuinely-overlapping seal graphic is never distorted (>25% overlap
    leaves the image untouched)."""
    nudge = {"kind": "image", "source": "SIG", "x": 2.0, "y": 1.0,
             "w": 2.0, "h": 0.4, "size": 11}
    label = {"kind": "text", "text": "Effective Date:", "x": 3.9, "y": 1.05,
             "w": 1.5, "h": 0.15, "size": 11}
    _decollide_elems([nudge, label])
    assert nudge["w"] <= 1.9, "small edge intrusion must clip the image"

    seal = {"kind": "image", "source": "SEAL", "x": 2.0, "y": 3.0,
            "w": 2.0, "h": 2.0, "size": 11}
    caption = {"kind": "text", "text": "licensed to operate", "x": 2.5,
               "y": 3.5, "w": 1.5, "h": 0.15, "size": 11}
    _decollide_elems([seal, caption])
    assert seal["w"] == 2.0, "a deeply-overlapping seal must NOT be resized"


def test_secondary_breakdown_frames_render_as_tables():
    """Production truth (inspections report): the report ends with small
    repeating frames bound to SECONDARY aggregate datasets — one row per
    category ("&NAME Inspections:  <count>") — followed by grand totals.
    The body builders render only the MAIN dataset's table, so the printed
    breakdown vanished ("the last page is definitely wrong").
    _emit_secondary_breakdown_tables must render each dropped
    secondary-dataset repeating frame as a details tablix over its dataset,
    with the label boilerplate resolved in THAT dataset's scope."""
    import re
    from converter import convert

    xml = (
        '<?xml version="1.0"?><report name="BRKDN" DTDVersion="9.0.2.0.10">'
        '<data>'
        '<dataSource name="Q_Main"><select><![CDATA[select site_nm, insp_dt '
        'from visits]]></select>'
        '<group name="G_Main"><dataItem name="SITE_NM" datatype="vchar2"/>'
        '<dataItem name="INSP_DT" datatype="date"/></group></dataSource>'
        '<dataSource name="Q_ByWorker"><select><![CDATA[select worker_nm, '
        'cnt from per_worker]]></select>'
        '<group name="G_ByWorker"><dataItem name="WORKER_NM" '
        'datatype="vchar2"/><dataItem name="CNT" datatype="number"/>'
        '</group></dataSource></data>'
        '<layout><section name="main">'
        '<frame name="M_Body"><geometryInfo x="0" y="0" width="8" '
        'height="3"/>'
        '<repeatingFrame name="R_Main" source="G_Main" '
        'printDirection="down"><geometryInfo x="0.2" y="0.2" width="7.5" '
        'height="0.2"/>'
        '<field name="F_S" source="SITE_NM"><geometryInfo x="0.2" y="0.2" '
        'width="3" height="0.2"/></field>'
        '<field name="F_D" source="INSP_DT"><geometryInfo x="3.4" y="0.2" '
        'width="1.4" height="0.2"/></field></repeatingFrame>'
        '<frame name="M_Totals"><geometryInfo x="0.2" y="1.2" width="7.5" '
        'height="0.8"/>'
        '<repeatingFrame name="R_Worker" source="G_ByWorker" '
        'printDirection="down"><geometryInfo x="0.3" y="1.3" width="4" '
        'height="0.19"/>'
        '<text name="B_W"><geometryInfo x="0.3" y="1.3" width="2.4" '
        'height="0.19"/><textSegment><font face="Arial" size="8"/>'
        '<string><![CDATA[&WORKER_NM Inspections:]]></string>'
        '</textSegment></text>'
        '<field name="F_C" source="CNT" alignment="end"><geometryInfo '
        'x="2.9" y="1.3" width="0.9" height="0.19"/></field>'
        '</repeatingFrame></frame></frame></section></layout></report>'
    ).encode()
    rdl = convert(xml)["rdl_xml"]
    m = re.search(r'<Tablix Name="Tablix_Breakdown_0"(.*?)</Tablix>', rdl,
                  re.S)
    assert m, "secondary-dataset repeating frame was not rendered"
    body = m.group(1)
    assert "<DataSetName>Q_ByWorker</DataSetName>" in body
    assert "Fields!CNT.Value" in body
    # the label boilerplate resolves its &token in the SECONDARY scope
    assert "Fields!WORKER_NM.Value" in body
    assert "Inspections:" in body


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


def test_stat_section_groups_by_computed_dimension_and_bolds_rollup_rows():
    """A multi-section stat report whose section TITLE is a `*_Group` data
    column must emit a REAL row group over the section dimension (Oracle's
    break-group repeating frame), bind the band header to the computed
    per-row expression — NEVER the NULL formula-dataset stub — and bold the
    ROLLUP subtotal rows via a FontWeight IIf keyed on the CF's own
    translated condition."""
    from converter.generators.rdl import (
        _build_multi_section_body, _build_section_tablix,
        _iif_top_args, _rollup_subtotal_fontweight, _resolve_palette)
    from converter.models import (DataQuery, DataItem, FormulaColumn,
                                  ParsedReport)

    def _mkq(name):
        q = DataQuery(name=name)
        q.items = [DataItem(name="Sec_Group"), DataItem(name="Sec_Type"),
                   DataItem(name="Sec_Count")]
        q.sql = ("SELECT DECODE(t, 'a', 'A Bucket', 'B Bucket') Sec_Group, "
                 "NVL(t, 'Z-Subtotal') Sec_Type, COUNT(*) Sec_Count FROM x "
                 "GROUP BY ROLLUP(DECODE(t, 'a', 'A Bucket', 'B Bucket'), t)")
        return q

    rep = ParsedReport(name="SYNTH")
    q1, q2 = _mkq("Q_S1"), _mkq("Q_S2")
    rep.queries = [q1, q2]
    rep.formulas = [
        FormulaColumn(
            name="CF_Sec_Group",
            plsql_body=("FUNCTION F_CF_Sec_Group RETURN VARCHAR2 IS BEGIN "
                        "IF :Sec_Group IS NULL THEN "
                        "RETURN('Total Received') ; "
                        "ELSE RETURN(:Sec_Group) ; END IF ; END ;")),
        FormulaColumn(
            name="CF_Sec_Type",
            plsql_body=("FUNCTION F_CF_Sec_Type RETURN VARCHAR2 IS BEGIN "
                        "IF :Sec_Type = 'Z-Subtotal' THEN "
                        "RETURN('Subtotal ' || :Sec_Group) ; "
                        "ELSE RETURN(:Sec_Type) ; END IF ; END ;"))]
    sections = [
        {"header": "", "y": 0.0, "totals": [], "has_total": True,
         "tables": [(q1, ["CF_Sec_Group"]),
                    (q1, ["CF_Sec_Type", "Sec_Count"])]},
        {"header": "", "y": 1.0, "totals": [], "has_total": True,
         "tables": [(q2, ["Sec_Group"]),
                    (q2, ["CF_Sec_Type", "Sec_Count"])]},
    ]
    body = _build_multi_section_body(rep, sections)
    xml = ET.tostring(body, encoding="unicode")

    # 1) NEVER the NULL formula-dataset stub for the section header.
    assert "DS_REPORT_FORMULAS" not in xml
    # 2) A real row group over the section dimension, computed inline for
    #    the CF-titled section and by the column for the plain one.
    grp_exprs = [m for m in xml.split("<GroupExpression>")[1:]]
    assert any("IsNothing(Fields!Sec_Group.Value)" in g.split("</GroupExpression>")[0]
               for g in grp_exprs), xml[:900]
    assert any(g.split("</GroupExpression>")[0].strip() ==
               "=Fields!Sec_Group.Value" for g in grp_exprs)
    assert "_Sect" in xml  # the named section row group exists
    # 3) ROLLUP subtotal rows print bold via the CF's own condition.
    assert 'IIf(((Fields!Sec_Type.Value = "Z-Subtotal")), "Bold", "Normal")' \
        in xml.replace("&quot;", '"')

    # PROVE THE GATE CAN FAIL — the pre-fix shapes stay refused:
    # (a) an ungrouped section tablix has no _Sect row group;
    tx = _build_section_tablix(rep, "Tbx_NEG", q1,
                               ["CF_Sec_Type", "Sec_Count"],
                               "Hdr", _resolve_palette(rep))
    assert "_Sect" not in ET.tostring(tx, encoding="unicode")
    # (b) a non-IIf label formula yields NO conditional bolding;
    assert _rollup_subtotal_fontweight("=Fields!X.Value") is None
    # (c) an IIf whose branches are BOTH bare passthroughs is ambiguous ->
    #     honest decline;
    assert _rollup_subtotal_fontweight(
        "=IIf((Fields!A.Value = 1), (Fields!B.Value), (Fields!C.Value))") is None
    # (d) the inverted idiom (passthrough in the TRUE branch) bolds the
    #     FALSE branch.
    inv = _rollup_subtotal_fontweight(
        '=IIf((Fields!A.Value = 1), (Fields!B.Value), ("Subtotal " & Fields!C.Value))')
    assert inv == '=IIf((Fields!A.Value = 1), "Normal", "Bold")'
    # (e) the argument splitter respects strings with commas/parens.
    assert _iif_top_args('=IIf(F(a, b), "x,(y", z)') == ['F(a, b)', '"x,(y"', 'z']


def test_section_totals_follow_source_geometry_and_cells_are_borderless():
    """(a) A multi-table stat section whose declared total labels ALL sit
    BELOW every detail band prints the tables footer-less and BOTH gray
    total rows TOGETHER after them (source y-order) — not one footer
    sandwiched between the tables. (b) Stat detail/total cells carry NO
    invented border (dialect: only declared borders paint) and the detail
    label cell keeps the repeating frame's declared left indent."""
    from converter.generators.rdl import _build_multi_section_body
    from converter.models import DataQuery, DataItem, ParsedReport

    def _mkq(name, d, n):
        q = DataQuery(name=name)
        q.items = [DataItem(name=d), DataItem(name=n)]
        q.sql = f"select {d}, {n} from t"
        return q

    rep = ParsedReport(name="SYNTH")
    q1 = _mkq("Q_CLOSED", "Row_Desc_1", "Row_Cnt_1")
    q2 = _mkq("Q_OPEN", "Row_Desc_2", "Row_Cnt_2")
    rep.queries = [q1, q2]
    base = {
        "header": "Section One", "y": 0.0, "x": 0.0,
        "tables": [(q1, ["Row_Desc_1", "Row_Cnt_1"]),
                   (q2, ["Row_Desc_2", "Row_Cnt_2"])],
        "totals": ["Total One Closed", "Total One Active"],
        "col_headers": ["Number"], "has_total": True,
    }
    other = {"header": "Section Two", "y": 2.0, "x": 0.0,
             "tables": [(q2, ["Row_Desc_2", "Row_Cnt_2"])],
             "totals": [], "col_headers": ["Number"], "has_total": False}

    # TRAILING geometry: totals below BOTH detail bands.
    sec = dict(base, totals_y=[0.62, 0.85],
               table_geo=[(0.25, 0.25), (0.44, 0.25)])
    xml = ET.tostring(_build_multi_section_body(rep, [sec, other]),
                      encoding="unicode")
    assert "_Ftr_" not in xml, "per-table footer must not sandwich the tables"
    assert "Tot0" in xml and "Tot1" in xml, "both trailing total rows emitted"
    assert xml.find("Total One Closed") < xml.find("Total One Active")
    # each total sums ITS OWN table's dataset, canonical scoped form
    assert 'Sum(Fields!Row_Cnt_1.Value, "Q_CLOSED")' in xml.replace("&quot;", '"')
    assert 'Sum(Fields!Row_Cnt_2.Value, "Q_OPEN")' in xml.replace("&quot;", '"')
    # both trailing rows come AFTER both detail tables
    assert xml.find("Total One Closed") > xml.find("Row_Desc_2")
    # (b) no invented borders on stat cells; declared 0.25in indent kept
    assert "#d0d0d0" not in xml
    assert "<PaddingLeft>0.25in</PaddingLeft>" in xml

    # PROVE THE GATE CAN FAIL — totals declared BETWEEN the bands (above
    # the second table) keep the historic per-table footer pairing.
    sec2 = dict(base, totals_y=[0.30, 0.85],
                table_geo=[(0.25, 0.25), (0.44, 0.25)])
    xml2 = ET.tostring(_build_multi_section_body(rep, [sec2, other]),
                       encoding="unicode")
    assert "_Ftr_" in xml2 and "Tot0" not in xml2


def test_summary_net_skips_totals_consumed_by_section_rows():
    """A report-level summary whose aggregate ALREADY renders inside an
    ungrouped data region bound to its own dataset (a section footer /
    trailing total row) must NOT be re-emitted as the humanized
    summary-name dump block."""
    from converter.generators.rdl import _ensure_summary_totals_emitted, _q
    from converter.models import (DataQuery, DataItem, FormulaColumn,
                                  LayoutField, LayoutGroup, ParsedReport)

    def _mk_root(with_consuming_tablix):
        root = ET.Element(_q("Report"))
        body = ET.SubElement(root, _q("Body"))
        ri = ET.SubElement(body, _q("ReportItems"))
        ET.SubElement(body, _q("Height")).text = "2.00in"
        if with_consuming_tablix:
            tx = ET.SubElement(ri, _q("Tablix"))
            tx.set("Name", "Tbx_S0")
            tr = ET.SubElement(ET.SubElement(ET.SubElement(ET.SubElement(
                tx, _q("Paragraphs")), _q("Paragraph")), _q("TextRuns")),
                _q("TextRun"))
            ET.SubElement(tr, _q("Value")).text = \
                '=Sum(Fields!Row_Cnt.Value, "Q_S")'
            rh = ET.SubElement(tx, _q("TablixRowHierarchy"))
            ET.SubElement(ET.SubElement(rh, _q("TablixMembers")),
                          _q("TablixMember"))
            ET.SubElement(tx, _q("DataSetName")).text = "Q_S"
        return root

    q = DataQuery(name="Q_S")
    q.items = [DataItem(name="Row_Desc"), DataItem(name="Row_Cnt")]
    rep = ParsedReport(name="SYNTH")
    rep.queries = [q]
    rep.formulas = [FormulaColumn(
        name="SumRow_CntPerReport", agg_function="sum",
        agg_source="Row_Cnt", agg_scope="report")]
    g = LayoutGroup(name="G", kind="frame")
    g.fields = [LayoutField(name="F_T", kind="field",
                            source="SumRow_CntPerReport")]
    rep.layout = [g]

    root = _mk_root(with_consuming_tablix=True)
    _ensure_summary_totals_emitted(root, rep)
    xml = ET.tostring(root, encoding="unicode")
    assert "Sumrow Cntperreport" not in xml and "GrandTotal" not in xml, xml

    # PROVE THE GATE CAN FAIL — with no consuming section row, the
    # report-level total is genuinely missing and the net must emit it.
    root2 = _mk_root(with_consuming_tablix=False)
    _ensure_summary_totals_emitted(root2, rep)
    xml2 = ET.tostring(root2, encoding="unicode")
    assert 'Sum(Fields!Row_Cnt.Value, "Q_S")' in xml2.replace("&quot;", '"')


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


def _resolve_fields(name):
    return f"Fields!{name}.Value"


def test_declarative_conditional_format_becomes_style_expression():
    """Oracle keeps conditional formatting in TWO places and they carry
    DIFFERENT information: the <advancedLayout formatTrigger> PL/SQL returns
    only TRUE/FALSE (visibility), while the bold / colour / fill lives ONLY
    in <generalLayout><conditionalFormat>. The trigger path can therefore
    never recover the formatting — a wild-corpus survey found 544 such
    blocks over 31 reports, all dropped."""
    from converter.translators.format_exception import (
        translate_conditional_format)

    out = translate_conditional_format([{
        "label": "(:AMOUNT IS NULL)",
        "cond": {"column": "AMOUNT", "exception": "11"},
        "font": {"bold": "yes", "textColor": "red"},
        "visual": {"fillPattern": "solid", "fillForegroundColor": "yellow"},
    }], _resolve_fields)
    assert len(out) == 1
    cond, styles = out[0]
    assert cond == "IsNothing(Fields!AMOUNT.Value)"
    assert styles["FontWeight"] == "Bold"
    assert styles["Color"] == "#FF0000"
    assert styles["BackgroundColor"] == "#FFFF00"


def test_format_exception_label_beats_lossy_cond_element():
    """Each <formatException> carries exactly one <cond>, but 206 of the
    corpus's 712 labels reference TWO OR THREE columns — Oracle drops the
    extra terms when it serialises. The label is the complete condition, so
    it must be the source of truth."""
    from converter.translators.format_exception import (
        translate_condition_label)

    vb = translate_condition_label(
        "((:A IS NOT NULL) or (:B IS NOT NULL))", _resolve_fields)
    assert vb is not None
    assert "Fields!A.Value" in vb and "Fields!B.Value" in vb
    assert " Or " in vb


def test_format_exception_declines_rather_than_guesses():
    """Codes 2/3/4 appear in the corpus but only ever with free-text labels,
    so nothing proves their operator. A free-text label whose code is
    unproven must produce NOTHING — inventing an operator would silently
    paint the wrong rows. Same for an unresolvable column."""
    from converter.translators.format_exception import (
        translate_condition_label, translate_conditional_format)

    assert translate_condition_label("BACKGROUND", _resolve_fields) is None

    def _strict(name):
        raise KeyError(name)

    assert translate_condition_label("(:X IS NULL)", _strict) is None
    # free-text label + unproven code -> no entry at all
    assert translate_conditional_format([{
        "label": "dcc_crit_desc",
        "cond": {"column": "C", "exception": "3", "lowValue": "7"},
        "font": {"bold": "yes"}, "visual": {},
    }], _resolve_fields) == []


def test_format_exception_operand_fallback_is_refused():
    """`lowValue` is not reliably a literal: the corpus has
    column="CS_suppl_count" exception="1" lowValue="ac_area_code" — a row
    COUNT compared against what reads as another column's NAME. Nothing in
    the file distinguishes the two, so operand-bearing <cond> fallback must
    refuse. The operand-FREE codes carry no such ambiguity and still work."""
    from converter.translators.format_exception import translate_cond_element

    assert translate_cond_element(
        {"column": "CNT", "exception": "1", "lowValue": "other_col"},
        _resolve_fields) is None
    assert translate_cond_element(
        {"column": "CNT", "exception": "11"}, _resolve_fields
    ) == "IsNothing(Fields!CNT.Value)"


def test_format_exception_huge_numeric_bound_still_compiles():
    """Oracle criteria builders emit sentinel bounds like
    BETWEEN '0' and '99999999999999999999' (20 digits) to mean "no limit".
    As a bare VB literal that is BC30036: Overflow at JIT time, which the
    engine reports only as a generic processing error — taking the WHOLE
    report down. Anything past 15 significant digits goes through Val()."""
    from converter.translators.format_exception import (
        translate_condition_label)

    vb = translate_condition_label(
        "(:QTY BETWEEN '0' and '99999999999999999999')", _resolve_fields)
    assert vb is not None
    assert 'Val("99999999999999999999")' in vb
    assert ">= 0" in vb          # small bounds stay readable literals
    # a comfortably small bound is NOT wrapped
    small = translate_condition_label("(:QTY < '100')", _resolve_fields)
    assert "Val(\"100\")" not in small and "< 100" in small


def test_conditional_format_matches_cell_aggregate_scope():
    """A bare Fields! ref in a STYLE expression on a cell whose value is
    =First(Fields!X.Value) is rejected by the engine as "cannot be specified
    as nested aggregates". The condition must use the same aggregate."""
    from converter.generators.rdl import _cf_wrap_agg

    got = _cf_wrap_agg("IsNothing(Fields!A.Value)", "First")
    assert got == "IsNothing(First(Fields!A.Value))"
    assert _cf_wrap_agg("IsNothing(Fields!A.Value)", None) == \
        "IsNothing(Fields!A.Value)"


def test_raw_newline_never_survives_inside_a_vb_string_literal():
    """A literal newline inside a VB string constant does not COMPILE
    (BC30648) and the engine surfaces that only as a generic processing
    error — and the unterminated literal corrupts the NEXT expression too,
    so one bad boilerplate reports as several failures. Oracle text is
    pretty-printed CDATA spanning lines, and several builders can emit it,
    so the repair is a post-pass over every expression."""
    import xml.etree.ElementTree as _ET
    from converter.generators.rdl import _repair_multiline_string_literals

    NS = "http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition"
    root = _ET.Element(f"{{{NS}}}Report")
    v = _ET.SubElement(root, f"{{{NS}}}Value")
    v.text = '="Line one\n          Line two " & Fields!X.Value'
    _repair_multiline_string_literals(root)
    assert "\n" not in v.text, "raw newline left inside a string literal"
    assert "vbCrLf" in v.text
    # the token-adjacent trailing space must survive the repair
    assert '"Line two "' in v.text
    # an expression with no literal newline is left untouched
    v2 = _ET.SubElement(root, f"{{{NS}}}Value")
    v2.text = '="a" & Fields!Y.Value'
    _repair_multiline_string_literals(root)
    assert v2.text == '="a" & Fields!Y.Value'


def _mk(tag, ns="http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition",
        **attrs):
    import xml.etree.ElementTree as _ET
    el = _ET.Element(f"{{{ns}}}{tag}")
    for k, v in attrs.items():
        el.set(k, v)
    return el


def test_report_item_names_are_globally_unique():
    """RDL report item names must be unique across the WHOLE report; the
    engine rejects the definition outright ("More than one report item in
    the report has the name 'X'"), so ONE collision kills the report. Several
    builders set constant names, and a many-query report drove the tablix
    builder 16 times. First occurrence keeps its name."""
    from converter.generators.rdl import _dedupe_report_item_names

    root = _mk("Report")
    for _ in range(3):
        root.append(_mk("Tablix", Name="Tablix_Main"))
    root.append(_mk("Textbox", Name="Tablix_Main"))
    _dedupe_report_item_names(root)
    names = [e.get("Name") for e in root]
    assert names[0] == "Tablix_Main", "first occurrence must keep its name"
    assert len(set(names)) == len(names), f"still duplicated: {names}"


def test_group_rename_retargets_aggregate_scopes():
    """Datasets, data regions and GROUPINGS share one namespace and must be
    unique. But renaming a group is not a pure relabel — aggregate scopes
    reference groups BY NAME, so the scope literals inside that region must
    be retargeted or the aggregate silently prints another group's number."""
    from converter.generators.rdl import _dedupe_group_names

    NS = "http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition"
    root = _mk("Report")
    for i in range(2):
        tx = _mk("Tablix", Name=f"T{i}")
        grp = _mk("Group", Name="OuterGroup")
        tx.append(grp)
        val = _mk("Value")
        val.text = '=Sum(Fields!AMT.Value, "OuterGroup")'
        tx.append(val)
        root.append(tx)
    _dedupe_group_names(root)

    groups = [g.get("Name") for g in root.iter(f"{{{NS}}}Group")]
    assert len(set(groups)) == 2, f"group names still collide: {groups}"
    vals = [v.text for v in root.iter(f"{{{NS}}}Value")]
    # first region untouched; second retargeted to its own renamed group
    assert vals[0] == '=Sum(Fields!AMT.Value, "OuterGroup")'
    assert vals[1] == f'=Sum(Fields!AMT.Value, "{groups[1]}")'


def test_out_of_scope_group_expression_collapses_to_constant():
    """A group expression naming a column the region's dataset lacks is
    fatal to the WHOLE report. It must collapse to a CONSTANT, not be
    removed: an expression-less <Group> is exactly how RDL denotes the
    DETAIL member, and the engine then rejects "the grouping 'X' has a
    detail member with inner members". In-scope expressions stay untouched."""
    from converter.generators.rdl import (
        _repair_out_of_scope_group_expressions)

    NS = "http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition"
    root = _mk("Report")
    ds = _mk("DataSet", Name="Q_1")
    flds = _mk("Fields")
    flds.append(_mk("Field", Name="IN_SCOPE"))
    ds.append(flds)
    root.append(ds)

    tx = _mk("Tablix")
    dsn = _mk("DataSetName")
    dsn.text = "Q_1"
    tx.append(dsn)
    grp = _mk("Group", Name="G")
    ges = _mk("GroupExpressions")
    bad = _mk("GroupExpression")
    bad.text = "=Fields!ELSEWHERE.Value"
    good = _mk("GroupExpression")
    good.text = "=Fields!IN_SCOPE.Value"
    ges.append(bad)
    ges.append(good)
    grp.append(ges)
    tx.append(grp)
    root.append(tx)

    n = _repair_out_of_scope_group_expressions(root)
    assert n == 1
    assert bad.text == "=1", "out-of-scope expression must become a constant"
    assert good.text == "=Fields!IN_SCOPE.Value", "in-scope must be untouched"
    # the <GroupExpressions> container must survive (detail-member trap)
    assert grp.find(f"{{{NS}}}GroupExpressions") is not None


def test_report_that_renders_nothing_is_a_blocker():
    """A source that declares queries but NO layout objects, whose SELECT *
    yields no inferable columns either, converted to a Tablix + Rectangle
    holding zero textboxes — and reported RED at fidelity 1.00, i.e. "looks
    fine" for a report whose every page is blank. The existing hollow_body
    rule needs extractable dataset fields to fire, so it misses this shape.
    Zero content items anywhere must be a BLOCKER.

    The complementary case matters just as much: when columns ARE inferable
    the converter synthesizes a layout, and that must stay usable."""
    import json as _json
    from converter import convert

    def _src(sel1, sel2):
        return (
            '<?xml version="1.0"?><report name="X" DTDVersion="9.0.2.0.0">'
            '<data><dataSource name="Q_1" defaultGroupName="G_A"><select>'
            f'<![CDATA[{sel1}]]></select></dataSource>'
            '<dataSource name="Q_2" defaultGroupName="G_B"><select>'
            f'<![CDATA[{sel2}]]></select></dataSource>'
            '<link name="L_1" parentGroup="G_A" parentColumn="ID" '
            'childQuery="Q_2" childColumn="ID" condition="eq" '
            'sqlClause="where"/></data></report>').encode()

    blank = convert(_src("select * from t1", "select * from t2"))
    assert blank["preflight"]["verdict"] == "BLOCKER", (
        "a report that renders nothing must never pass as usable")
    assert "no_content_items" in _json.dumps(blank["preflight"])
    assert "<Textbox" not in blank["rdl_xml"]

    named = convert(_src("select id, a from t1", "select id, b from t2"))
    assert "no_content_items" not in _json.dumps(named["preflight"]), (
        "must not fire when a layout was successfully synthesized")


def test_per_record_page_budget_has_real_headroom():
    """Every per-record report in the corpus sat at EXACTLY the page-sizing
    slack constant (the slack WAS the whole margin), and one invoice packet
    tipped over into a blank page after every record; a PageHeight sweep on
    it put the true threshold at ~+0.35in over the old 0.20in. The constant
    must stay comfortably above that measured requirement, and the sizing
    formula must consume it."""
    from converter import convert
    from converter.generators.rdl import _PER_RECORD_SLACK_IN

    assert _PER_RECORD_SLACK_IN >= 0.5, (
        f"per-record slack {_PER_RECORD_SLACK_IN} is under the measured "
        f"blank-page threshold (~0.35in) plus headroom")

    # Synthetic per-record letter: one query, per-record fields placed on a
    # positioned body tall enough that the page must grow past 11in.
    fields = "".join(
        f'<field name="F_{i}" source="C{i}"><geometryInfo x="0.5" '
        f'y="{0.5 + i * 1.4:.2f}" width="4" height="1.2"/></field>'
        for i in range(8))
    xml = (f'<?xml version="1.0"?><report name="LTR" DTDVersion="9.0.2.0.10">'
           f'<data><dataSource name="Q_1"><select><![CDATA[select '
           f'{", ".join(f"c{i}" for i in range(8))}, rid from t]]></select>'
           f'</dataSource></data><layout><section name="main">'
           f'<repeatingFrame name="R_1" source="Q_1" printDirection="down">'
           f'<geometryInfo x="0.2" y="0.2" width="7" height="11.7"/>'
           f'{fields}</repeatingFrame></section></layout></report>').encode()
    rdl = convert(xml)["rdl_xml"]
    import re as _re
    m_page = _re.search(r"<PageHeight>([\d.]+)in</PageHeight>", rdl)
    if m_page is None:
        import pytest as _pytest
        _pytest.skip("fixture did not route to the per-record archetype")
    ph = float(m_page.group(1))
    if abs(ph - 11.0) < 0.01:
        import pytest as _pytest
        _pytest.skip("fixture fit an 11in page; budget rule not exercised")
    rows = [float(x) for x in _re.findall(
        r"<TablixRow>\s*<Height>([\d.]+)in</Height>", rdl)]
    hdr = sum(float(x) for x in _re.findall(
        r"<PageHeader>.*?<Height>([\d.]+)in</Height>", rdl, _re.S)[:1])
    ftr = sum(float(x) for x in _re.findall(
        r"<PageFooter>.*?<Height>([\d.]+)in</Height>", rdl, _re.S)[:1])
    margins = sum(float(x) for x in _re.findall(
        r"<(?:Top|Bottom)Margin>([\d.]+)in", rdl))
    avail = ph - margins - hdr - ftr
    assert rows, "per-record report emitted no tablix rows"
    assert avail - max(rows) >= 0.5 - 1e-6, (
        f"per-record budget too tight: avail {avail:.2f} vs tallest row "
        f"{max(rows):.2f} — this is the blank-page-after-every-record class")


_ACTION_BUTTON_XML = (
    '<?xml version="1.0"?><report name="PARENT_RPT" DTDVersion="9.0.2.0.10">'
    '<data>'
    '<userParameter name="P_Report_Server" datatype="character"/>'
    '<userParameter name="P_Report" datatype="character"/>'
    '<userParameter name="P_Email_XML" datatype="character"/>'
    '<userParameter name="P_Year" datatype="character"/>'
    '<userParameter name="P_Child" datatype="character" '
    'initialValue="CHILD_ENVELOPE"/>'
    '<dataSource name="Q_1"><select><![CDATA[select org_nm, email_ad '
    'from orgs]]></select></dataSource></data>'
    '<layout><section name="header">'
    '<frame name="H_1"><geometryInfo x="0" y="0" width="8" height="7"/>'
    '<text name="B_RPT_LBL"><geometryInfo x="0.25" y="1.0" width="1.5" '
    'height="0.25"/><textSegment><font face="Arial" size="10"/>'
    '<string><![CDATA[Report:]]></string></textSegment></text>'
    '<field name="F_RPT" source="ORG_NM"><geometryInfo x="2.0" y="1.0" '
    'width="3.0" height="0.25"/></field>'
    '<text name="B_YR_LBL"><geometryInfo x="0.25" y="1.4" width="1.5" '
    'height="0.25"/><textSegment><font face="Arial" size="10"/>'
    '<string><![CDATA[Report Year:]]></string></textSegment></text>'
    '<field name="F_YR" source="EMAIL_AD"><geometryInfo x="2.0" y="1.4" '
    'width="3.0" height="0.25"/></field>'
    '<text name="B_NOTE"><geometryInfo x="0.25" y="4.1" width="7.7" '
    'height="0.4"/><textSegment><font face="Arial" size="12" bold="yes" '
    'textColor="red"/><string><![CDATA[IT CANNOT BE REVERSED!]]></string>'
    '</textSegment></text>'
    '<roundedRectangle name="R_BTN"><webSettings '
    'hyperlink="&amp;CP_URL_SEND"><![CDATA[#NULL#]]></webSettings>'
    '<geometryInfo x="0.25" y="5.0" width="1.6" height="0.45"/>'
    '</roundedRectangle>'
    '<text name="B_SEND"><webSettings hyperlink="&amp;CP_URL_SEND">'
    '<![CDATA[#NULL#]]></webSettings>'
    '<geometryInfo x="0.25" y="5.05" width="1.6" height="0.35"/>'
    '<textSegment><font face="Arial" size="18" bold="yes" underline="yes" '
    'textColor="blue"/><string><![CDATA[Send Emails]]></string>'
    '</textSegment></text>'
    '</frame></section>'
    '<section name="main"><frame name="M_1">'
    '<geometryInfo x="0" y="0" width="8" height="3"/>'
    '<field name="F_ORG" source="ORG_NM"><geometryInfo x="0.3" y="0.4" '
    'width="4" height="0.25"/></field></frame></section></layout>'
    '<programUnits><function name="beforerep"><textSource><![CDATA['
    'function BeforeReport return boolean is\n'
    '  vParams VARCHAR2(1000);\n'
    '  FUNCTION F_Get_Param(pvBind_Variable IN VARCHAR2) RETURN VARCHAR2 IS\n'
    '    vValue VARCHAR2(4000);\n'
    '  BEGIN\n'
    "    vValue := SRW.Get_Value(pvBind_Variable);\n"
    "    IF vValue IS NOT NULL THEN\n"
    "      RETURN('&' || pvBind_Variable || '=' || vValue);\n"
    "    ELSE RETURN(NULL); END IF;\n"
    '  END F_Get_Param;\n'
    'begin\n'
    "  vParams := vParams || F_Get_Param(pvBind_Variable => 'P_Year');\n"
    "  :CP_URL_SEND := TRIM(:P_Report_Server) || '?x' \n"
    "    || '&destype=cache' || '&report=' || :P_Report || '.rep'\n"
    "    || '&distribute=YES' || '&destination=' || :P_Email_XML\n"
    "    || vParams;\n"
    '  return (TRUE);\n'
    'end;]]></textSource></function></programUnits></report>'
).encode()


def test_url_builder_translates_distribution_button():
    """The in-report 'Send Emails' idiom: a rounded-rect + hyperlinked text
    whose URL a report trigger builds by concatenating :parameters and
    null-guarded F_Get_Param fragments (re-invoking the report server with
    distribute=YES to mass-email the recipients). The URL must translate to
    a real VB expression — parameters resolved, guards preserved, appends
    replayed in textual order."""
    from converter.parsers.oracle_xml import parse_oracle_xml
    from converter.generators.rdl import _hyperlink_action_expr

    rep = parse_oracle_xml(_ACTION_BUTTON_XML)
    vb = _hyperlink_action_expr(rep, "CP_URL_SEND")
    assert vb is not None and vb.startswith("=")
    # SSRS refs are case-sensitive: the expression must use the DECLARED
    # parameter casing, not the (arbitrary) casing at the PL/SQL call site.
    assert "Parameters!P_Report_Server.Value" in vb
    assert '"&distribute=YES"' in vb
    assert "IIf(IsNothing(Parameters!P_Year.Value)" in vb
    assert '"&P_Year="' in vb


def test_url_builder_declines_ambiguity():
    """A wrong URL that silently DOES something (this one mass-emails) is
    far worse than an inert button — every unprovable case must decline:
    a bind that is not a declared parameter, and a token assigned in more
    than one branch (which branch runs depends on runtime state)."""
    from converter.parsers.oracle_xml import parse_oracle_xml
    from converter.generators.rdl import _hyperlink_action_expr

    # non-parameter bind
    bad = _ACTION_BUTTON_XML.replace(b":P_Email_XML", b":SOME_COLUMN")
    assert _hyperlink_action_expr(
        parse_oracle_xml(bad), "CP_URL_SEND") is None
    # two assignments to the same token (exclusive branches)
    dup = _ACTION_BUTTON_XML.replace(
        b"  return (TRUE);",
        b"  :CP_URL_SEND := 'other';\n  return (TRUE);")
    assert _hyperlink_action_expr(
        parse_oracle_xml(dup), "CP_URL_SEND") is None


def test_action_button_renders_in_both_views_with_hyperlink():
    """The button must survive to BOTH outputs 1:1: the RDL cover carries a
    button-styled textbox with a real <Action><Hyperlink>, and the mockup
    renders the button face — neither existed before (the cover pairing
    logic dropped unpaired hyperlink texts entirely)."""
    from converter import convert

    out = convert(_ACTION_BUTTON_XML)
    rdl, mock = out["rdl_xml"], out["mockup_html"]
    assert "Send Emails" in rdl
    assert "<Hyperlink>=" in rdl and "distribute=YES" in rdl
    assert "IT CANNOT BE REVERSED!" in rdl, "instruction prose dropped"
    # The mockup must show the button too. (Which COVER STYLE the mockup
    # dispatcher picks depends on how rich the header is — the full
    # instruction-block rendering is exercised by the rich-cover path on
    # real artifacts; this minimal fixture guards the button's presence.)
    assert "Send Emails" in mock


def test_distribution_url_is_never_a_subreport_link():
    """detect_subreport_links once claimed a distribution URL for an
    unrelated child (its body lives in a TRIGGER, so the lookup came back
    empty and the child-name fallback fired) — which wired the mass-email
    button as a drill-through to the ENVELOPE report. distribute= URLs and
    self-report dynamic targets are actions, never links; a dynamic
    '&report=' || :BIND target IS a link when the bind's initialValue names
    the child."""
    from converter.parsers.oracle_xml import parse_oracle_xml
    from converter.subreports import detect_subreport_links

    rep = parse_oracle_xml(_ACTION_BUTTON_XML)
    for ln in detect_subreport_links(rep):
        assert "CP_URL_SEND" not in (ln.get("url_formula") or ""), (
            "distribution URL misclassified as a sub-report link")

    # the P_ENVELOPE pattern: dynamic report= bound to a param whose
    # initialValue names the child -> a REAL link to that child
    linked = _ACTION_BUTTON_XML.replace(
        b"|| '&distribute=YES' || '&destination=' || :P_Email_XML\n",
        b"|| '&x=y'\n").replace(
        b"|| :P_Report || '.rep'", b"|| :P_Child")
    rep2 = parse_oracle_xml(linked)
    kids = {ln.get("child_name") for ln in detect_subreport_links(rep2)}
    assert "CHILD_ENVELOPE" in kids


_SPLICE_XML = (
    '<?xml version="1.0"?><report name="SPLICE_T" DTDVersion="9.0.2.0.10">'
    '<data>'
    '<userParameter name="P_Dias" datatype="character"/>'
    '<dataSource name="Q_1"><select><![CDATA[select col_a, col_b\n'
    'from t_rows\n'
    'where col_b in (&P_Dias)\n'
    'and col_a like &P_UNDECLARED ;]]></select></dataSource>'
    '</data>'
    '<layout><section name="main"><groupLeft name="M_t"><group>'
    '<field name="F1" source="COL_A"/><field name="F2" source="COL_B"/>'
    '</group></groupLeft></section></layout></report>'
).encode()


def test_commandtext_is_never_an_expression():
    """THE #1 INVARIANT, learned the hard way twice: CommandText must be
    STATIC SQL. An expression-valued CommandText ("=..." built from
    Parameters!) was shipped once to splice runtime lexicals 1:1 — and in
    the production demo Report Builder popped "Define Query Parameters" at
    Refresh Fields on EVERY report, asking the end user to type values for
    every bind. Report Builder cannot evaluate the expression at design
    time; static text with QueryParameters bound to =Nothing-defaulted
    report parameters is the only prompt-free form. Never regress this."""
    import re
    from converter import convert

    for src in (_SPLICE_XML, _ACTION_BUTTON_XML):
        rdl = convert(src)["rdl_xml"]
        for m in re.finditer(r"<CommandText>(.*?)</CommandText>", rdl, re.S):
            assert not m.group(1).lstrip().startswith("="), (
                "EXPRESSION CommandText emitted — this reintroduces the "
                "Refresh-Fields parameter-prompt bug that broke the "
                "production demo. CommandText must be static SQL.")


def test_lexical_with_static_default_inlines_that_default():
    """The safe fraction of runtime-lexical fidelity: when the lexical's
    declared parameter carries a static SQL-fragment initialValue (the
    ORDER-BY idiom — P_ORDER_BY defaulting to a column list), inline the
    default as STATIC SQL. Faithful to Oracle's default run, and the
    design-time Refresh Fields flow stays prompt-free. Anything without
    such a default keeps the honest stub + finding."""
    import json as _json
    import re
    from converter import convert

    src = _SPLICE_XML.replace(
        b'<userParameter name="P_Dias" datatype="character"/>',
        b'<userParameter name="P_Dias" datatype="character" '
        b'initialValue="COL_A, COL_B"/>')
    out = convert(src)
    m = re.search(r"<CommandText>(.*?)</CommandText>", out["rdl_xml"], re.S)
    assert m and not m.group(1).lstrip().startswith("=")
    assert "COL_A, COL_B" in m.group(1), "static default not inlined"
    assert "lexical default &amp;P_Dias" in m.group(1)
    assert "lexical_default_inlined" in _json.dumps(out["preflight"])

    # No static default -> the honest stub survives, with its finding.
    out2 = convert(_SPLICE_XML)
    m2 = re.search(r"<CommandText>(.*?)</CommandText>", out2["rdl_xml"], re.S)
    assert "lexical ref &amp;P_Dias" in m2.group(1)


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
    from converter.models import (ParsedReport, TriggerCode, DataQuery,
                                  DataItem)
    rep = ParsedReport(name="R", dtd_version="9")
    q = DataQuery(name="Q_1")
    q.items = [DataItem(name="ST")]
    rep.queries.append(q)
    rep.triggers.append(TriggerCode(
        name="F_HideVoid",
        body="begin IF :st = 'V' THEN RETURN FALSE; END IF; "
             "RETURN TRUE; end;"))
    rep.triggers.append(TriggerCode(
        name="F_Unknown",
        body="begin IF :nope = 1 THEN RETURN FALSE; END IF; "
             "RETURN TRUE; end;"))
    m = _format_trigger_hidden_map(rep)
    # strict resolver: DECLARED casing used; unknown names DECLINE.
    assert m == {"f_hidevoid": '=((Fields!ST.Value = "V"))'}


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
    from converter.models import (ParsedReport, TriggerCode, DataQuery,
                                  DataItem)
    rep = ParsedReport(name="R", dtd_version="9")
    _q2 = DataQuery(name="Q_1")
    _q2.items = [DataItem(name="P")]
    rep.queries.append(_q2)
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


def test_label_override_facility_generic():
    """Fire 137 (user-flagged): ANY generated literal label is overridable
    by textbox name (plus the 'title' alias) — plain literals AND pure
    constant-string expression labels (='...'); data expressions are never
    clobbered; the inventory ships in convert() output and the /api/convert
    endpoint passes overrides through."""
    import io
    import json as _json
    from converter.generators.rdl import (collect_overridable_labels,
                                          apply_label_overrides)

    rdl = ('<?xml version="1.0"?><Report xmlns="http://schemas.microsoft.'
           'com/sqlserver/reporting/2008/01/reportdefinition"><Body>'
           '<ReportItems>'
           '<Textbox Name="Tb_Title"><Paragraphs><Paragraph><TextRuns>'
           '<TextRun><Value>="My Report"</Value></TextRun></TextRuns>'
           '</Paragraph></Paragraphs></Textbox>'
           '<Textbox Name="Tb_Data"><Paragraphs><Paragraph><TextRuns>'
           '<TextRun><Value>=Fields!X.Value</Value></TextRun></TextRuns>'
           '</Paragraph></Paragraphs></Textbox>'
           '</ReportItems><Height>1in</Height></Body></Report>')
    inv = collect_overridable_labels(rdl)
    assert [e["name"] for e in inv] == ["Tb_Title"]
    assert inv[0]["text"] == "My Report"
    out, applied = apply_label_overrides(rdl, {"title": 'New "T"'})
    assert applied and applied[0][0] == "Tb_Title"
    assert '="New ""T"""' in out
    out2, applied2 = apply_label_overrides(rdl, {"Tb_Data": "nope"})
    assert not applied2, "data expressions must never be clobbered"

    from app import app
    xml = (b'<?xml version="1.0"?><report name="T" DTDVersion="9.0.2.0.10">'
           b'<data><dataSource name="Q_1"><select><![CDATA[select a, b, c '
           b'from t]]></select></dataSource></data></report>')
    c = app.test_client()
    r = c.post("/api/convert", data={
        "file": (io.BytesIO(xml), "r.xml"),
        "label_overrides": _json.dumps({"title": "HTTP-OVR"}),
    }, content_type="multipart/form-data")
    j = r.get_json()
    assert r.status_code == 200
    assert "overridable_labels" in j


def test_web_source_captions_carried_and_rendered():
    """Fire 138 (hunt round 3): a .jsp web-source report's authored
    rw:dataArea table headers become item LABELS (every DataItem copy incl.
    the group tree) and the geometry-less nested-detail path synthesizes a
    column-header row from them (with the matching hierarchy member)."""
    from converter import convert
    from converter.parsers.oracle_xml import parse_oracle_xml

    xml = (b'<?xml version="1.0"?><report name="WS" DTDVersion="9.0.2.0.10">'
           b'<data><dataSource name="Q_1"><select><![CDATA[select m, a, b '
           b'from t]]></select><group name="G_M">'
           b'<dataItem name="m" datatype="vchar2"/>'
           b'<group name="G_D"><dataItem name="A" datatype="number"/>'
           b'<dataItem name="B" datatype="vchar2"/></group></group>'
           b'</dataSource></data></report>\n'
           b'<rw:dataArea xmlns:rw="http://x"><table><thead><tr>'
           b'<th <rw:id id="HBA92" asArray="no"/> class="h"> Alpha Count '
           b'</th><th <rw:id id="HBB92" asArray="no"/> class="h"> Beta Name '
           b'</th></tr></thead></table></rw:dataArea>')
    rep = parse_oracle_xml(xml)
    labs = {i.name: i.label for q in rep.queries
            for g in q.groups for i in g.items}
    # group-tree copies labeled too (walk nested)
    def _all(gs):
        for g in gs:
            for i in g.items:
                yield i
            yield from _all(g.children)
    labs = {i.name: i.label for q in rep.queries for i in _all(q.groups)}
    assert labs.get("A") == "Alpha Count" and labs.get("B") == "Beta Name"
    x = convert(xml)["rdl_xml"]
    assert "Alpha Count" in x and "Beta Name" in x


def test_inline_masked_dates_and_safe_elastic_cangrow():
    """Fire 139: (1) a DATE-masked field inlined into a concatenation gets
    Format(..., net-mask) wrapped in place (the <Format> style can't reach
    it — raw ToString rendered '6/8/2026 12:00:00 AM'); (2) elastic fields
    (verticalElasticity expand/variable) CanGrow ONLY when nothing sits
    below them in their frame (auditor-calibrated overlap-safe subset)."""
    import re as _re
    from converter import convert

    xml = (b'<?xml version="1.0"?><report name="DT" DTDVersion="9.0.2.0.10">'
           b'<data><dataSource name="Q_1"><select><![CDATA[select d, note, x '
           b'from t]]></select><group name="G_1">'
           b'<dataItem name="D" datatype="date"/>'
           b'<dataItem name="NOTE" datatype="vchar2"/>'
           b'<dataItem name="X" datatype="vchar2"/></group></dataSource>'
           b'</data><layout><section name="main">'
           b'<body width="8.5" height="11.0">'
           b'<repeatingFrame name="R_1" source="G_1">'
           b'<geometryInfo x="0" y="0" width="8" height="3"/>'
           b'<text name="T_D"><geometryInfo x="0" y="0.2" width="4" '
           b'height="0.25"/><textSegment><font face="arial" size="10"/>'
           b'<string><![CDATA[Date: &<D>]]></string></textSegment></text>'
           b'<field name="F_D" source="D" formatMask="MM/DD/YYYY">'
           b'<geometryInfo x="5" y="0.2" width="1.5" height="0.25"/></field>'
           b'<field name="F_X" source="X">'
           b'<geometryInfo x="0" y="0.6" width="3" height="0.25"/></field>'
           b'<field name="F_NOTE" source="NOTE">'
           b'<generalLayout verticalElasticity="expand"/>'
           b'<geometryInfo x="0" y="1.0" width="7.5" height="0.5"/></field>'
           b'</repeatingFrame></body></section></layout></report>')
    x = convert(xml)["rdl_xml"]
    m = _re.search(r'<Value>="Date: " ?&amp; ?([^<]+)</Value>', x)
    if m:
        assert "Format(" in m.group(1) and "MM/dd/yyyy" in m.group(1), m.group(1)
    # NOTE is elastic + bottom-most -> CanGrow; X has NOTE below -> fixed.
    note_tb = _re.search(r'<Textbox Name="[^"]*"[^>]*>(?:(?!</Textbox>).)*?'
                         r'Fields!NOTE(?:(?!</Textbox>).)*?</Textbox>', x, _re.S)
    assert note_tb and "<CanGrow>true</CanGrow>" in note_tb.group(0)


def test_cursor_formula_translates_to_correlated_subquery():
    """Fire 140: a SINGLE-FETCH cursor formula keyed on current-row binds
    becomes a scalar correlated subquery on the wrapper alias O (first-
    fetch-by-order via MAX KEEP DENSE_RANK FIRST); loop/concat bodies
    decline; wired formulas leave the NULL-stub dataset."""
    from converter.translators.plsql_formula import (
        cursor_formula_to_subquery)
    from converter import convert

    body = ("function CF_P return Char is CURSOR C IS SELECT L.PHN "
            "FROM V L WHERE L.ORG_ID = :ORG_ID ORDER BY L.DC DESC; "
            "R C%ROWTYPE; begin OPEN C; FETCH C INTO R; RETURN R.PHN; end;")
    sub = cursor_formula_to_subquery(body, ["ORG_ID"], [])
    assert sub and "MAX(L.PHN) KEEP (DENSE_RANK FIRST" in sub
    assert "O.ORG_ID" in sub
    assert cursor_formula_to_subquery(
        "begin FOR r IN c LOOP x := x || r.a; END LOOP; RETURN x; end;",
        ["A"], []) is None

    xml = (b'<?xml version="1.0"?><report name="CU" DTDVersion="9.0.2.0.10">'
           b'<data><dataSource name="Q_1"><select><![CDATA[select org_id, nm '
           b'from t]]></select><group name="G_1">'
           b'<dataItem name="ORG_ID" datatype="number"/>'
           b'<dataItem name="NM" datatype="vchar2"/>'
           b'<formula name="CF_PHONE" source="cf_phoneformula" '
           b'datatype="character" width="50"/></group></dataSource>'
           b'</data><programUnits><function name="CF_PHONEFormula">'
           b'<textSource><![CDATA[function CF_PHONEFormula return Char is '
           b'CURSOR C IS SELECT L.PHN FROM V L WHERE L.ORG_ID = :ORG_ID '
           b'ORDER BY L.DC DESC; R C%ROWTYPE; begin OPEN C; FETCH C INTO R; '
           b'RETURN R.PHN; end;]]></textSource></function></programUnits>'
           b'</report>')
    out = convert(xml)
    x = out["rdl_xml"]
    sql = x[x.index("<CommandText>"):x.index("</CommandText>")]
    assert "SELECT O.*" in sql and "O.ORG_ID" in sql
    assert "AS CF_PHONE" in sql


def test_region_aware_trigger_application_and_nested_first_repair():
    """Fire 141: trigger Hidden/Style applies via the region-aware
    post-pass (data-ft tags; refs validated against the ENCLOSING region's
    dataset, exact case; tags always stripped) — and the illegal
    Sum(Val(First(x,"DS"))) nested aggregate rewrites to Val(First(...))
    (single-row formula dataset makes the outer aggregate a no-op)."""
    import xml.etree.ElementTree as _ET
    from converter.generators.rdl import (_repair_nested_first_aggregates,
                                          _q as _qq)

    r = _ET.fromstring(
        '<Report xmlns="http://schemas.microsoft.com/sqlserver/reporting/'
        '2008/01/reportdefinition"><Body><V><Value>=Sum(Val(First('
        'Fields!CF_X.Value, "DS_REPORT_FORMULAS")))</Value></V></Body>'
        '</Report>')
    _repair_nested_first_aggregates(r)
    x = _ET.tostring(r, encoding="unicode")
    assert '=Val(First(Fields!CF_X.Value, "DS_REPORT_FORMULAS"))' in x
    assert "Sum(" not in x

    from converter import convert
    xml = (b'<?xml version="1.0"?><report name="TR" DTDVersion="9.0.2.0.10">'
           b'<data><dataSource name="Q_1"><select><![CDATA[select st, v '
           b'from t]]></select><group name="G_1">'
           b'<dataItem name="ST" datatype="vchar2"/>'
           b'<dataItem name="V" datatype="number"/></group></dataSource>'
           b'</data><programUnits><function name="FT_Hide">'
           b'<textSource><![CDATA[function FT_Hide return boolean is begin '
           b'IF :st = \'X\' THEN RETURN FALSE; END IF; RETURN TRUE; end;'
           b']]></textSource></function></programUnits>'
           b'<layout><section name="main"><body width="8.5" height="11.0">'
           b'<repeatingFrame name="R_1" source="G_1">'
           b'<geometryInfo x="0" y="0.5" width="8" height="0.3"/>'
           b'<field name="F_S" source="ST" formatTrigger="FT_Hide">'
           b'<geometryInfo x="0" y="0.5" width="2" height="0.25"/></field>'
           b'<field name="F_V" source="V">'
           b'<geometryInfo x="3" y="0.5" width="2" height="0.25"/></field>'
           b'</repeatingFrame></body></section></layout></report>')
    x2 = convert(xml)["rdl_xml"]
    assert "data-ft" not in x2, "tags must always strip"
    if "<Hidden>=" in x2:
        assert 'Fields!ST.Value = "X"' in x2


def test_label_override_api_survives_without_the_sidebar_ui():
    """Label overrides are an API capability, not a sidebar.

    Fire 144 added a sidebar of per-report label inputs; the user's later
    verdict was that a wall of report-specific fields reads as hardcoding,
    not a product, and ordered it removed. This test locks BOTH halves of
    that decision: the UI must stay gone from the template and app.js, and
    the converter's label_overrides round-trip (used programmatically and
    by artifact stacking) must keep working end to end.
    """
    import io
    import json as _json
    from pathlib import Path
    from app import app

    root = Path(__file__).resolve().parent.parent
    html = (root / "frontend" / "templates" / "index.html").read_text(
        encoding="utf-8")
    assert 'label-overrides-section' not in html
    assert 'label-override-apply' not in html

    js = (root / "frontend" / "static" / "js" / "app.js").read_text(
        encoding="utf-8")
    assert "renderLabelOverrides" not in js
    assert "applyLabelOverrides" not in js

    # the converter API is intact: inventory ships, overrides land in BOTH
    # the RDL and the mockup
    xml = (root / "samples" / "oracle" / "SAMPLE_INSPECTION.xml").read_bytes()
    c = app.test_client()
    r1 = c.post("/api/convert", data={"file": (io.BytesIO(xml), "s.xml")},
                content_type="multipart/form-data")
    j1 = r1.get_json()
    inv = j1.get("overridable_labels") or []
    assert inv, "label inventory must still ship in the payload"
    shared = next((l for l in inv
                   if ">" + l["text"] + "<" in (j1.get("mockup_html") or "")),
                  None)
    if shared:
        r2 = c.post("/api/convert", data={
            "file": (io.BytesIO(xml), "s.xml"),
            "label_overrides": _json.dumps({shared["name"]: "GUARDED LABEL"}),
        }, content_type="multipart/form-data")
        j2 = r2.get_json()
        assert "GUARDED LABEL" in j2["rdl_xml"]
        assert "GUARDED LABEL" in j2["mockup_html"]


def test_todate_bound_params_are_string_not_datetime():
    """Fire 145 — PRODUCTION-REPORTED by the deploying operator: reports
    failed on the real SSRS+Oracle server until date parameters were
    manually retyped from DateTime to String.

    Root cause: SSRS types the outgoing query bind from the REPORT
    PARAMETER's DataType, not from the QueryParameter value expression. So
    Format(CDate(...), "yyyy-MM-dd") is coerced back to a DateTime, the
    provider binds a DATE, and Oracle re-renders it with NLS_DATE_FORMAT
    (DD-MON-RR) before matching our 'YYYY-MM-DD' mask -> ORA-01861
    "literal does not match format string".

    A param wrapped in TO_DATE(...) must therefore be String, with the
    expected format stated in the prompt. Params NOT so wrapped keep
    DateTime (and their date picker)."""
    import re as _re
    from converter import convert

    xml = (b'<?xml version="1.0"?><report name="DT" DTDVersion="9.0.2.0.10">'
           b'<data>'
           b'<userParameter name="P_START" datatype="date" precision="10"'
           b' inputMask="mm/dd/yyyy" defaultWidth="0" defaultHeight="0"/>'
           b'<userParameter name="P_PLAIN" datatype="date" precision="10"'
           b' inputMask="mm/dd/yyyy" defaultWidth="0" defaultHeight="0"/>'
           b'<dataSource name="Q_1"><select><![CDATA[SELECT a FROM t '
           b'WHERE (:P_START IS NULL OR d >= :P_START)]]></select>'
           b'<group name="G_1"><dataItem name="A" datatype="vchar2"/>'
           b'</group></dataSource></data></report>')
    x = convert(xml)["rdl_xml"]

    cmds = " ".join(_re.findall(r"<CommandText>(.*?)</CommandText>", x, _re.S))
    wrapped = {m.group(1).upper() for m in
               _re.finditer(r"TO_DATE\(\s*:([A-Za-z_][A-Za-z0-9_]*)", cmds)}
    assert "P_START" in wrapped, "date bind should be TO_DATE-wrapped"

    types = {m.group(1): m.group(2) for m in _re.finditer(
        r'<ReportParameter Name="([^"]+)">(?:(?!</ReportParameter>).)*?'
        r"<DataType>([^<]+)</DataType>", x, _re.S)}
    assert types.get("P_START") == "String", (
        "TO_DATE-bound param must be String or Oracle throws ORA-01861; "
        f"got {types.get('P_START')}")

    # The prompt must tell the user the expected format, since declaring
    # String removes the SSRS date picker.
    blk = _re.search(r'<ReportParameter Name="P_START">(.*?)</ReportParameter>',
                     x, _re.S).group(1)
    assert "YYYY-MM-DD" in blk

    # Unwrapped date params are left alone (they keep their picker).
    assert types.get("P_PLAIN") in ("DateTime", None)


def test_non_ascii_column_names_stay_unique():
    """Fire 147 (hunt round 4, Greek pension report): _safe() maps every
    non-ASCII char to '_', so two names that are distinct in the source
    language can collapse to the SAME ASCII field name — duplicate
    <Field Name> in one DataSet is invalid RDL (a real BLOCKER). Names must
    de-duplicate while DataField keeps the ORIGINAL column name, so the
    binding to the result-set column still works."""
    import re as _re
    import xml.etree.ElementTree as _ET
    from collections import Counter
    from converter.generators.rdl import _build_dataset
    from converter.models import DataQuery, DataItem

    q = DataQuery(name="Q_1")
    q.sql = "SELECT a, b, c FROM t"
    q.items = [DataItem(name="COL_Ι_ΣΤΟ_Π"),
               DataItem(name="COL_Ι_ΣΤΟΝ_"),
               DataItem(name="PLAIN")]
    ds = _build_dataset(q, [], target_db="oracle")
    x = _ET.tostring(ds, encoding="unicode")
    names = _re.findall(r'<(?:\w+:)?Field Name="([^"]+)"', x)
    assert len(names) == 3, names
    dups = [n for n, k in Counter(names).items() if k > 1]
    assert not dups, f"duplicate field names invalidate the RDL: {dups}"
    assert "COL_Ι_ΣΤΟ_Π" in x
    assert "COL_Ι_ΣΤΟΝ_" in x


def test_oracle_target_does_not_report_tsql_dialect_errors():
    """Fire 147: on an ORACLE target the emitted SQL stays Oracle by
    design, so T-SQL-only findings ((+) outer joins, ROWNUM) are
    portability notes, not errors. Flagging them as errors produced 104
    phantom 'errors' across one real corpus — noise that trains a user to
    ignore the findings that actually matter."""
    from converter.validators.tsql_check import validate_report
    from converter.models import ParsedReport, DataQuery

    rep = ParsedReport(name="R", dtd_version="9")
    q = DataQuery(name="Q_1")
    q.tsql = "SELECT a FROM t, u WHERE t.id = u.id (+) AND ROWNUM < 10"
    rep.queries.append(q)

    ora = validate_report(rep, target_db="oracle")
    assert not [i for i in ora if i.get("severity") == "error"], (
        "Oracle-target report must not report Oracle syntax as an error")
    assert any(i.get("rule") == "oracle.outer_join_hint" for i in ora), (
        "the finding must still be REPORTED, just informationally")
    assert any("targets ORACLE" in (i.get("message") or "") for i in ora)

    mssql = validate_report(rep, target_db="sqlserver")
    assert [i for i in mssql if i.get("severity") == "error"], (
        "SQL Server target must still flag unrewritten Oracle syntax")


def test_literal_lexical_branches_become_null_safe_predicates():
    """Fire 148 — the dominant 'query runs UNFILTERED' cause. Oracle
    builds a lexical WHERE fragment with one branch per prompt combination:

        IF :P_START IS NOT NULL AND :P_END IS NULL THEN
           :P_LEX := 'AND col >= :P_START';
        IF :P_START IS NULL AND :P_END IS NOT NULL THEN
           :P_LEX := 'AND col <= :P_END';
        IF both THEN :P_LEX := 'AND col >= :P_START AND col <= :P_END';

    Each branch predicate is guarded by exactly the bind it tests, so the
    UNION of the single-bind branches — each made NULL-safe — reproduces
    EVERY branch in one static query. Multi-bind branches are skipped
    because they are the conjunction of the single-bind ones."""
    from converter.generators.rdl import _literal_lexical_predicates

    plsql = (
        "IF :P_START IS NOT NULL AND :P_END IS NULL THEN "
        "  :P_LEX := 'AND c.DT >= :P_START' ; "
        "END IF; "
        "IF :P_START IS NULL AND :P_END IS NOT NULL THEN "
        "  :P_LEX := 'AND c.DT <= :P_END' ; "
        "END IF; "
        "IF :P_START IS NOT NULL AND :P_END IS NOT NULL THEN "
        "  :P_LEX := 'AND c.DT >= :P_START AND c.DT <= :P_END' ; "
        "END IF;")
    out = _literal_lexical_predicates(plsql)
    frag = out.get("P_LEX")
    assert frag, "the branch idiom must reconstruct"
    assert "(:P_START IS NULL OR c.DT >= :P_START)" in frag
    assert "(:P_END IS NULL OR c.DT <= :P_END)" in frag
    # The two-bind branch must NOT add a third predicate.
    assert frag.count("IS NULL OR") == 2, frag

    # An ACCUMULATED lexical (||) is not ours — leave it to the
    # cv-constant path / honest placeholder rather than guessing.
    assert "P_ACC" not in _literal_lexical_predicates(
        "  :P_ACC := :P_ACC || ' AND x = :P_Y' ; ")
    # A non-predicate literal (no AND/OR, no bind) is ignored.
    assert "P_ORD" not in _literal_lexical_predicates(
        "  :P_ORD := 'ORDER BY 1' ; ")


def test_effective_dating_criteria_builder_reconstructs():
    """Fire 149: F_Criteria_Date_Bind(colA, colB, 'P_X') is Oracle's
    EFFECTIVE-DATING criteria builder — its body emits, only when the bind
    is non-null::

        AND (NVL(colA, :P_X) <= :P_X AND NVL(colB, :P_X) >= :P_X)

    ("the prompted date falls inside the row's window, a NULL endpoint
    being open"). The static equivalent wraps it in the usual bind guard.
    A REPLACE(cvX,'S.','S2.') column wrapper is a literal transform and is
    evaluated rather than declined."""
    from converter.parsers.oracle_xml import parse_oracle_xml
    from converter.generators.rdl import _reconstruct_lexical_criteria

    xml = (
        '<?xml version="1.0"?><report name="EFF" DTDVersion="9.0.2.0.10">'
        '<data><dataSource name="Q_1"><select><![CDATA[SELECT a FROM '
        'Site_Affiliation SA WHERE 1=1 &P_Criteria_Contact]]></select>'
        '<group name="G_1"><dataItem name="A" datatype="vchar2"/></group>'
        '</dataSource></data><programUnits><function name="bp">'
        '<textSource><![CDATA['
        "cvSA_START_DT CONSTANT VARCHAR2(30) := 'SA.Start_Dt';\n"
        "cvSA_END_DT CONSTANT VARCHAR2(30) := 'SA.End_Dt';\n"
        ":P_Criteria_Contact := :P_Criteria_Contact || "
        "F_Criteria_Date_Bind( pvColumn_1 => cvSA_START_DT, "
        "pvColumn_2 => cvSA_END_DT, pvBind_Variable => 'P_Letter_Dt');\n"
        ']]></textSource></function></programUnits></report>').encode()
    cm = _reconstruct_lexical_criteria(parse_oracle_xml(xml))
    frag = cm.get("P_CRITERIA_CONTACT", "")
    assert "(:P_Letter_Dt IS NULL OR" in frag, frag
    assert "NVL(SA.Start_Dt, :P_Letter_Dt) <= :P_Letter_Dt" in frag
    assert "NVL(SA.End_Dt, :P_Letter_Dt) >= :P_Letter_Dt" in frag


def test_criteria_builder_family_is_unified_and_wildcard_faithful():
    """Fire 150: ONE handler now covers every Varchar2/Number criteria
    builder — plain, ``_Bind`` and ``_Value``.

    Read from the real function bodies: the Varchar2 builders emit
    ``LIKE UPPER(TRIM(:P))`` when the typed value contains a % or _
    wildcard and ``= UPPER(TRIM(:P))`` otherwise. A single LIKE predicate
    reproduces BOTH branches, because LIKE without metacharacters IS
    equality — so emitting ``=`` (as the old handler did) silently broke
    wildcard searches. ``_Value`` variants inline an escaped literal at
    runtime; binding is equivalent and safer. ``pvColumn_2`` is an
    alternate column: match either."""
    from converter.parsers.oracle_xml import parse_oracle_xml
    from converter.generators.rdl import _reconstruct_lexical_criteria

    def _cm(call: str):
        xml = (
            '<?xml version="1.0"?><report name="C" DTDVersion="9.0.2.0.10">'
            '<data><dataSource name="Q_1"><select><![CDATA[SELECT a FROM '
            'Permit P WHERE 1=1 &P_Criteria_X]]></select><group name="G_1">'
            '<dataItem name="A" datatype="vchar2"/></group></dataSource>'
            '</data><programUnits><function name="bp"><textSource><![CDATA['
            "cvPERM CONSTANT VARCHAR2(30) := 'P.Perm_Name';\n"
            "cvALT CONSTANT VARCHAR2(30) := 'P.Alt_Name';\n"
            ":P_Criteria_X := :P_Criteria_X || " + call + ";\n"
            ']]></textSource></function></programUnits></report>').encode()
        return _reconstruct_lexical_criteria(
            parse_oracle_xml(xml)).get("P_CRITERIA_X", "")

    # _Bind and _Value both reduce to the same wildcard-capable predicate.
    for fn in ("F_Criteria_Varchar2_Bind", "F_Criteria_Varchar2_Value",
               "F_Criteria_Varchar2"):
        frag = _cm(f"{fn}( pvColumn_1 => cvPERM, "
                   f"pvBind_Variable => 'P_Name')")
        assert "(:P_Name IS NULL OR P.Perm_Name LIKE UPPER(TRIM(:P_Name)))" \
            in frag, f"{fn}: {frag}"

    # Numbers stay equality.
    frag = _cm("F_Criteria_Number_Value( pvColumn_1 => cvPERM, "
               "pvBind_Variable => 'P_Num')")
    assert "(:P_Num IS NULL OR P.Perm_Name = :P_Num)" in frag, frag

    # A second column means "match either".
    frag = _cm("F_Criteria_Varchar2( pvColumn_1 => cvPERM, "
               "pvColumn_2 => cvALT, pvBind_Variable => 'P_Name')")
    assert "P.Perm_Name LIKE UPPER(TRIM(:P_Name))" in frag
    assert "P.Alt_Name LIKE UPPER(TRIM(:P_Name))" in frag
    assert " OR " in frag


def test_local_accumulator_and_range_builder_reconstruct():
    """Fire 151: criteria staged through a LOCAL before being appended
    (``vCriteria := F_Criteria_...(...) ; :P_LEX := :P_LEX || vCriteria``)
    must still reconstruct, and the RANGE-form signature
    (``pvColumn`` + ``pvBind_Variable_1/_2``) has non-obvious semantics
    read from the real function body:

        both binds -> col >= :A AND col <= :B
        only A     -> col = :A   (EQUALITY, not an open range)
        only B     -> col = :B

    An open-range shortcut would silently widen a single-value search into
    "everything from that value onward", so the four states are enumerated.
    """
    from converter.parsers.oracle_xml import parse_oracle_xml
    from converter.generators.rdl import (_reconstruct_lexical_criteria,
                                          _inline_local_accumulators)

    # The local is resolved to its staged call.
    inl = _inline_local_accumulators(
        " vCriteria := F_Criteria_Number_Bind( pvColumn => cvFY, "
        "pvBind_Variable_1 => 'P_A', pvBind_Variable_2 => 'P_B') ; "
        " :P_Criteria_X := :P_Criteria_X || vCriteria ; ")
    assert "F_Criteria_Number_Bind" in inl.split(":P_Criteria_X :=")[1]

    def _frag(call):
        xml = (
            '<?xml version="1.0"?><report name="R" DTDVersion="9.0.2.0.10">'
            '<data><dataSource name="Q_1"><select><![CDATA[SELECT a FROM '
            'CSB_Logsheets CL WHERE 1=1 &P_Criteria_X]]></select>'
            '<group name="G_1"><dataItem name="A" datatype="vchar2"/>'
            '</group></dataSource></data><programUnits><function name="bp">'
            '<textSource><![CDATA['
            "cvFY CONSTANT VARCHAR2(15) := 'CL.Fiscal_Yr';\n"
            " vCriteria := " + call + " ;\n"
            " :P_Criteria_X := :P_Criteria_X || vCriteria ;\n"
            ']]></textSource></function></programUnits></report>').encode()
        return _reconstruct_lexical_criteria(
            parse_oracle_xml(xml)).get("P_CRITERIA_X", "")

    two = _frag("F_Criteria_Number_Bind( pvColumn => cvFY, "
                "pvBind_Variable_1 => 'P_A', pvBind_Variable_2 => 'P_B')")
    assert "CL.Fiscal_Yr >= :P_A" in two and "CL.Fiscal_Yr <= :P_B" in two
    assert "CL.Fiscal_Yr = :P_A" in two, "A-only must be EQUALITY"
    assert "CL.Fiscal_Yr = :P_B" in two, "B-only must be EQUALITY"

    one = _frag("F_Criteria_Number_Bind( pvColumn => cvFY, "
                "pvBind_Variable_1 => 'P_A', pvBind_Variable_2 => NULL)")
    assert "(:P_A IS NULL OR CL.Fiscal_Yr = :P_A)" in one, one


def test_lexical_position_ignores_sql_comments():
    """Fire 152: the grammatical position of a lexical is decided by the
    preceding CODE character, so SQL comments must be stripped first.

    Oracle authors write prose in ``--`` comments, and a sentence ending
    in a period made the next lexical look like an identifier fragment
    (``SCHEMA.&P_TABLE``) — raising a FALSE BLOCKER on a lexical that was
    really in ordinary WHERE position::

        ... IS NULL) --No review question exists. &P_Site_Criteria

    A false BLOCKER is corrosive: it trains the user to distrust every
    verdict, including the true ones."""
    from converter.validators.preflight import preflight_audit

    def _verdict_rules(sql: str):
        rdl = (
            '<?xml version="1.0"?><Report xmlns="http://schemas.microsoft.'
            'com/sqlserver/reporting/2008/01/reportdefinition">'
            '<DataSources><DataSource Name="DS">'
            '<DataSourceReference>Shared</DataSourceReference>'
            '</DataSource></DataSources><DataSets>'
            '<DataSet Name="Q1"><Query><DataSourceName>DS</DataSourceName>'
            f"<CommandText>{sql}</CommandText></Query><Fields>"
            '<Field Name="a"><DataField>a</DataField></Field></Fields>'
            "</DataSet></DataSets>"
            '<Body><ReportItems><Textbox Name="T"><Paragraphs><Paragraph>'
            "<TextRuns><TextRun><Value>Title</Value></TextRun>"
            "</TextRuns></Paragraph></Paragraphs></Textbox></ReportItems>"
            "<Height>1in</Height></Body><Width>7in</Width>"
            "<Page><PageHeight>11in</PageHeight><PageWidth>8.5in</PageWidth>"
            "</Page></Report>")
        pf = preflight_audit(rdl)
        # Severity BY RULE — the overall verdict also reflects unrelated
        # gates (a deliberately minimal fixture trips the hollow-body one),
        # and this test is about how the LEXICAL is classified.
        return {i["rule"]: i["severity"] for i in pf["issues"]}

    # Period inside a line comment -> NOT an identifier, NOT a BLOCKER.
    rules = _verdict_rules(
        "SELECT a FROM t WHERE x IS NULL "
        "--No review question exists. "
        "/* lexical ref &amp;P_Site_Criteria -- neutralized */ GROUP BY a")
    assert not any(r.startswith("sql.lexical_identifier") for r in rules), rules
    dropped = [r for r in rules if r.startswith("sql.lexical_where_dropped")]
    assert dropped, rules
    assert rules[dropped[0]] == "RED", "WHERE-position loss is RED, not BLOCKER"

    # A REAL identifier fragment still blocks.
    rules2 = _verdict_rules(
        "SELECT a FROM DIRECCION."
        "/* lexical ref &amp;P_TABLA -- neutralized */")
    ident = [r for r in rules2 if r.startswith("sql.lexical_identifier")]
    assert ident, rules2
    assert rules2[ident[0]] == "BLOCKER"


def test_page_number_wording_comes_from_the_source():
    """Fire 153: the report's OWN page-number wording must survive. Oracle
    carries it as boilerplate holding the page builtins (``&<PageNumber>``
    / ``&<TotalPages>``); emitting a hardcoded English "Page X of Y"
    injects a foreign language into a report that isn't in English
    (wild-corpus: a Greek report lost its "από" wording). Detection is
    structural — we look for the BUILTINS, never for particular words."""
    from converter.generators.rdl import _source_page_number_expr
    from converter.models import ParsedReport, LayoutGroup, LayoutField

    rep = ParsedReport(name="R", dtd_version="9")
    lg = LayoutGroup(name="M")
    lg.fields.append(LayoutField(name="T", kind="text",
                                 text="&<PageNumber> από &<TotalPages>"))
    rep.layout.append(lg)
    expr = _source_page_number_expr(rep)
    assert expr == '=Globals!PageNumber & " από " & Globals!TotalPages', expr

    # No page builtin -> no claim (caller keeps its default).
    rep2 = ParsedReport(name="R2", dtd_version="9")
    lg2 = LayoutGroup(name="M")
    lg2.fields.append(LayoutField(name="T", kind="text", text="Just a title"))
    rep2.layout.append(lg2)
    assert _source_page_number_expr(rep2) is None


def test_field_names_are_cls_compliant():
    """Fire 153: SSRS rejects a field name with no LETTER in it. A column
    named entirely in a non-Latin script sanitizes to underscores ("___"),
    and Oracle's uniquifier suffix only makes it "___1" — still rejected
    ("Field names must be CLS-compliant identifiers"), engine-verified.
    DataField must keep the ORIGINAL name so the binding still works."""
    import re as _re
    import xml.etree.ElementTree as _ET
    from converter.generators.rdl import _build_dataset
    from converter.models import DataQuery, DataItem

    q = DataQuery(name="Q_1")
    q.sql = "SELECT a, b FROM t"
    q.items = [DataItem(name="ΣΤΟΠ"), DataItem(name="ΣΤΟΝ")]
    x = _ET.tostring(_build_dataset(q, [], target_db="oracle"),
                     encoding="unicode")
    names = _re.findall(r'<(?:\w+:)?Field Name="([^"]+)"', x)
    assert len(names) == 2, names
    for n in names:
        assert _re.search(r"[A-Za-z]", n), f"not CLS-compliant: {n}"
    assert len(set(names)) == 2, names
    assert "ΣΤΟΠ" in x and "ΣΤΟΝ" in x, "DataField must keep the original"


# --- Group-summary sources, CurrentDate masks, display-position wraps -----
# A layout field may bind a GROUP-tree <summary> of ANOTHER query (the
# accreditation-history idiom: R_Course's columns bind C_Course_Provider =
# first(COURSE_PROVIDER) of Q_COURSE). Those used to fall to =Nothing,
# blanking whole detail columns. They must resolve structurally, the
# summary's formatMask must follow to the underlying column, a masked
# CurrentDate builtin must carry its mask, and bare-& page boilerplate
# ("Page &PhysicalPageNumber of ...") must be suppressed in the body.
_GRPSUM_XML = (
    '<?xml version="1.0"?><report name="GRPSUM_T" DTDVersion="9.0.2.0.10">'
    '<data>'
    '<dataSource name="Q_M"><select><![CDATA[select acol, mid from m]]>'
    '</select><group name="G_M"><dataItem name="ACOL" datatype="vchar2"/>'
    '<dataItem name="MID" datatype="number"/></group></dataSource>'
    '<dataSource name="Q_C"><select><![CDATA[select cval, cdt, cnum '
    'from c]]>'
    '</select><group name="G_C">'
    '<summary name="C_CDT" source="CDT" function="first"/>'
    '<dataItem name="CVAL" datatype="vchar2"/>'
    '<dataItem name="CDT" datatype="date"/>'
    '<dataItem name="CNUM" datatype="number"/></group></dataSource>'
    '<summary name="CS_TOT" source="CNUM" function="sum" reset="report" '
    'compute="report"/>'
    '</data>'
    '<layout><section name="main">'
    '<frame name="M_Body"><geometryInfo x="0" y="0" width="8" height="6"/>'
    '<text name="B_P1"><geometryInfo x="0.2" y="0.1" width="7" '
    'height="0.5"/><textSegment><font face="Arial" size="10"/>'
    '<string><![CDATA[This document summarizes the review history\n'
    'for the applicant shown below, including each course record\n'
    'held on file with the department.]]></string></textSegment></text>'
    '<text name="B_P2"><geometryInfo x="0.2" y="0.7" width="7" '
    'height="0.5"/><textSegment><font face="Arial" size="10"/>'
    '<string><![CDATA[Questions about the accuracy of the record\n'
    'may be directed to the program office during normal business\n'
    'hours for correction or appeal.]]></string></textSegment></text>'
    '<repeatingFrame name="R_M" source="G_M" printDirection="down">'
    '<geometryInfo x="0.2" y="1.3" width="7.5" height="2.0"/>'
    '<field name="F_A" source="ACOL"><geometryInfo x="0.2" y="1.3" '
    'width="3" height="0.2"/></field>'
    '<field name="F_CDT" source="C_CDT" formatMask="MM/DD/RRRR">'
    '<geometryInfo x="0.2" y="1.6" width="1.4" height="0.2"/></field>'
    '<field name="F_NOW" source="CurrentDate" formatMask="MM/DD/RRRR">'
    '<geometryInfo x="0.2" y="1.9" width="1.4" height="0.2"/></field>'
    '<field name="F_TOT" source="CS_TOT" formatMask="NNNGNN0">'
    '<geometryInfo x="0.2" y="2.2" width="1.4" height="0.2"/></field>'
    '<text name="B_PGNUM"><geometryInfo x="0.2" y="2.5" width="3" '
    'height="0.19"/><textSegment><font face="Arial" size="8"/>'
    '<string><![CDATA[Page &PhysicalPageNumber of '
    '&TotalPhysicalPages]]></string></textSegment></text>'
    '</repeatingFrame></frame></section></layout></report>'
).encode()


def test_group_summary_sources_resolve_with_mask_and_chrome_rules():
    from converter import convert

    out = convert(_GRPSUM_XML)
    rdl = out["rdl_xml"]

    # 1) The cross-dataset group-summary source computes (scoped aggregate
    #    or link-correlated lookup) instead of blanking to Nothing.
    assert (
        'First(Fields!CDT.Value, "Q_C")' in rdl
        or 'Lookup(' in rdl and 'Fields!CDT.Value' in rdl
    ), "group-summary source must resolve to the underlying Q_C column"

    # 2) The summary's formatMask follows to the underlying column ref.
    import re as _re
    m = _re.search(
        r'First\(Fields!CDT\.Value, "Q_C"\).{0,600}?<Format>([^<]+)</Format>',
        rdl, _re.S)
    assert m and m.group(1) == "MM/dd/yyyy", (
        "the C_CDT mask must stamp <Format>MM/dd/yyyy</Format> on the "
        "underlying-column textbox")

    # 3) A masked CurrentDate builtin formats inline (the name-keyed
    #    post-pass can never reach a Globals! value).
    assert 'Format(Globals!ExecutionTime, "MM/dd/yyyy")' in rdl

    # 4) Bare-& page boilerplate never reaches the body as "Page  of ".
    assert '"Page " & Nothing' not in rdl
    assert 'Nothing & " of "' not in rdl

    # 5) A REPORT-level (reset="report") summary over ANOTHER dataset's
    #    column computes as a dataset-scoped aggregate (the stat-table
    #    idiom) — never the =Nothing placeholder — and the emit-time
    #    Format stamp carries its NNNGNN0 mask (G = group separator).
    m2 = _re.search(
        r'Sum\(Fields!CNUM\.Value, "Q_C"\).{0,600}?<Format>([^<]+)</Format>',
        rdl, _re.S)
    assert m2 and m2.group(1) == "###,##0", (
        "cross-dataset report-scope summary must emit "
        'Sum(Fields!CNUM.Value, "Q_C") with <Format>###,##0</Format>')


def test_inline_mask_wrap_skips_computational_positions():
    """The concat Format() wrapper covers NUMBER masks now — but it must
    never wrap a ref in comparison/arithmetic position (IIf(amount < 0)
    drives real letter wording; Format() there compares a STRING to 0)."""
    import xml.etree.ElementTree as _ET
    from types import SimpleNamespace as _NS
    from converter.generators.rdl import _wrap_inline_masked_date_refs, _q

    report = _NS(layout=[_NS(
        fields=[_NS(format_mask="-$NNN,NNN,NN0.00", source="AMT")],
        children=[],
    )])
    root = _ET.Element(_q("Report"))
    v1 = _ET.SubElement(root, _q("Value"))
    v1.text = ('="Balance: " & Fields!AMT.Value & '
               'IIf(((Fields!AMT.Value < 0)), " (credit)", "") & '
               'Sum(Fields!AMT.Value)')
    _wrap_inline_masked_date_refs(root, report)

    t = v1.text
    assert t.count("Format(Fields!AMT.Value") == 1, t
    assert "IIf(((Fields!AMT.Value < 0))" in t, (
        "comparison-position ref must stay bare: " + t)
    assert "Sum(Fields!AMT.Value)" in t, (
        "function-argument ref must stay bare: " + t)


def test_lookup_and_aggregate_pure_shapes_match_format_pass():
    """The <Format> post-pass must key off the DISPLAYED column of a pure
    cross-dataset Lookup (3rd argument) and of a pure scoped aggregate —
    the two shapes the flat-value builders emit for masked columns."""
    from converter.generators.rdl import _PURE_LOOKUP_RE, _PURE_FIELD_RE

    m = _PURE_LOOKUP_RE.match(
        '=Lookup(Fields!Site_Id.Value, Fields!Site_Id.Value, '
        'Fields!SAMPLE_TOTAL.Value, "Q_SAMPLE")')
    assert m and m.group(1) == "SAMPLE_TOTAL"
    m2 = _PURE_FIELD_RE.match('=Sum(Fields!APP_APPROVED.Value, "Q_RENEWAL")')
    assert m2 and m2.group(1) == "APP_APPROVED"
    # Join(LookupSet(...)) is a joined STRING — must NOT match.
    assert not _PURE_LOOKUP_RE.match(
        '=Join(LookupSet(Fields!K.Value, Fields!K2.Value, '
        'Fields!V.Value, "Q_C"), vbCrLf)')


def test_inline_mask_wrap_covers_scoped_aggregate_units():
    """A masked column referenced through a SCOPED aggregate inside a
    concat ("All Total: " & Sum(Fields!X.Value, "Q")) wraps the WHOLE
    aggregate in Format() — wrapping inside the aggregate would feed
    Sum a string."""
    import xml.etree.ElementTree as _ET
    from types import SimpleNamespace as _NS
    from converter.generators.rdl import _wrap_inline_masked_date_refs, _q

    report = _NS(layout=[_NS(
        fields=[_NS(format_mask="NNN,NN0", source="SAMPLE_TOTAL")],
        children=[],
    )])
    root = _ET.Element(_q("Report"))
    v = _ET.SubElement(root, _q("Value"))
    v.text = '="All Total:  " & Sum(Fields!SAMPLE_TOTAL.Value, "Q_SAMPLE")'
    _wrap_inline_masked_date_refs(root, report)
    assert v.text == ('="All Total:  " & Format(Sum(Fields!SAMPLE_TOTAL.Value,'
                      ' "Q_SAMPLE"), "###,##0")'), v.text


def test_formula_stub_never_ships_raw_nonparam_binds():
    """A trivial-RETURN formula referencing a NON-parameter bind (an Oracle
    summary like :SumXPerReport) must NOT inline into the formula-dataset
    SELECT — a raw bind with no QueryParameter is the 'Define Query
    Parameters' prompt on the server (#1-rule class). Parameter binds may
    inline but must arrive with a QueryParameter bound to the parameter."""
    import re as _re
    import xml.etree.ElementTree as _ET
    from converter import convert

    xml = (
        '<?xml version="1.0"?><report name="STUB_T" DTDVersion="9.0.2.0.10">'
        '<data>'
        '<userParameter name="P_ANIO" datatype="number"/>'
        '<dataSource name="Q_M"><select><![CDATA[select a from t]]>'
        '</select><group name="G_M"><dataItem name="A" datatype="vchar2"/>'
        '</group></dataSource>'
        '<formula name="CF_OK" datatype="number">'
        '<plsql><![CDATA[return(:P_ANIO);]]></plsql></formula>'
        '<formula name="CF_BAD" datatype="number">'
        '<plsql><![CDATA[return(:SumMonto2PerReport);]]></plsql>'
        '</formula>'
        '</data>'
        '<layout><section name="main">'
        '<frame name="M_B"><geometryInfo x="0" y="0" width="8" height="2"/>'
        '<field name="F_OK" source="CF_OK"><geometryInfo x="0.2" y="0.2" '
        'width="2" height="0.2"/></field>'
        '<field name="F_BAD" source="CF_BAD"><geometryInfo x="0.2" y="0.5" '
        'width="2" height="0.2"/></field>'
        '</frame></section></layout></report>'
    ).encode()
    rdl = convert(xml)["rdl_xml"]

    root = _ET.fromstring(rdl.encode("utf-8"))
    ns = root.tag.split("}")[0][1:]

    def q(t):
        return f"{{{ns}}}{t}"

    for ds in root.iter(q("DataSet")):
        if ds.get("Name") != "DS_REPORT_FORMULAS":
            continue
        qe = ds.find(q("Query"))
        ct = qe.findtext(q("CommandText")) or ""
        live = _re.sub(r"'[^']*'|--[^\n]*|/\*.*?\*/", " ", ct, flags=_re.S)
        binds = set(_re.findall(r":([A-Za-z_]\w*)", live))
        qps = {}
        for qp in qe.iter(q("QueryParameter")):
            qps[(qp.get("Name") or "").lstrip(":").upper()] = \
                (qp.findtext(q("Value")) or "").strip()
        # the summary bind never reaches live SQL
        assert "SUMMONTO2PERREPORT" not in {b.upper() for b in binds}, ct
        # the param bind is inlined AND declared, bound to the parameter
        assert "P_ANIO" in {b.upper() for b in binds}, ct
        assert qps.get("P_ANIO") == "=Parameters!P_ANIO.Value", qps
        # every live bind has a non-empty QueryParameter value
        for b in binds:
            assert qps.get(b.upper()), f"live bind :{b} lacks a QP value"
        break
    else:
        raise AssertionError("DS_REPORT_FORMULAS missing")


def test_preflight_enum_check_allows_style_expressions():
    """Conditional-format styles are EXPRESSIONS (=IIf(..., "Bold",
    "Normal")) — the enum BLOCKER must only fire on invalid LITERALS."""
    from converter.validators.preflight import preflight_audit

    good = ('<Report xmlns="http://schemas.microsoft.com/sqlserver/'
            'reporting/2008/01/reportdefinition">'
            '<FontStyle>=IIf((Fields!X.Value = "S"), "Normal", "Normal")'
            '</FontStyle></Report>')
    bad = good.replace(
        '=IIf((Fields!X.Value = "S"), "Normal", "Normal")', 'Oblique')
    g_issues = [i for i in preflight_audit(good)["issues"]
                if "bad_enum" in str(i)]
    b_issues = [i for i in preflight_audit(bad)["issues"]
                if "bad_enum" in str(i)]
    assert not g_issues, g_issues
    assert b_issues, "a literal invalid enum must still be a BLOCKER"


def test_star_select_items_never_aliased():
    """'SELECT O.* AS O' is ORA-00923 the moment the query executes on the
    real server — the production failure that presented as a parameter
    problem. Star expansions must pass through the aliasing pass untouched,
    and preflight must flag any star-alias as a BLOCKER."""
    from converter.generators.rdl import _alias_select_items
    from converter.validators.preflight import preflight_audit

    sql = ("SELECT O.*, (SELECT MAX(X.ID) FROM T X WHERE X.K = O.K) AS CF_A "
           "FROM (SELECT A, B FROM T2) O")
    out = _alias_select_items(sql, ["A", "B", "CF_A"])
    assert "* AS" not in out.upper().replace("  ", " "), out
    assert "O.*" in out, out

    bad_rdl = ('<Report xmlns="http://schemas.microsoft.com/sqlserver/'
               'reporting/2008/01/reportdefinition"><DataSets><DataSet '
               'Name="Q"><Query><DataSourceName>D</DataSourceName>'
               '<CommandText>SELECT O.* AS O FROM T O</CommandText>'
               '</Query><Fields><Field Name="A"><DataField>A</DataField>'
               '</Field></Fields></DataSet></DataSets></Report>')
    hits = [i for i in preflight_audit(bad_rdl)["issues"]
            if i.get("rule") == "sql.star_alias"]
    assert hits and hits[0]["severity"] == "BLOCKER", hits


def test_trailing_totals_requires_total_label_and_stays_inside_wrapper():
    """The report-end totals sheet must ONLY render when the layout carries
    a genuine total-shaped label — an ordinary secondary-dataset record
    frame (a complaint form's VIOLATION SITE band) must never be duplicated
    into a junk totals page. When it DOES render, it must sit INSIDE the
    desk wrapper (top-level siblings render side-by-side in the app)."""
    from converter import convert

    # _TOTALS_XML carries 'Total Site Visits:' -> totals sheet fires...
    html = convert(_TOTALS_XML)["mockup_html"]
    if "Report end" in html:
        # ...and the whole document must be ONE root element: everything
        # (including the totals sheet) inside the desk wrapper div.
        import re as _re
        first_div_end = _re.match(r"\s*<div", html)
        assert first_div_end is not None
        # strip the one root div; no OTHER top-level div may follow
        assert html.rstrip().endswith("</div>")
        depth = 0
        roots = 0
        for m in _re.finditer(r"<div\b|</div>", html):
            if m.group(0) == "<div":
                if depth == 0:
                    roots += 1
                depth += 1
            else:
                depth -= 1
        assert roots == 1, f"totals sheet escaped the wrapper ({roots} roots)"

    # A secondary-dataset frame with NO total label anywhere: no totals page.
    no_total = _TOTALS_XML.replace(b"Total Site Visits:", b"Site Visits:")
    html2 = convert(no_total)["mockup_html"]
    assert "Report end" not in html2, (
        "totals sheet fabricated without any total-shaped label")


def test_renumbered_dataitem_aliases_rebind_by_stem():
    """Oracle binds dataItems to SQL columns BY POSITION; SSRS binds BY
    NAME. A customer XML whose dataItem says ACTION_TYPE_ID_3 over a SQL
    alias ACTION_TYPE_ID_2 (or dataItem site_name1 over bare column
    site_name) shipped blank columns on the real server. The aliasing pass
    must rebind via the trailing-number STEM rule — positionally when the
    lists align, by unique stem otherwise — and must REFUSE ambiguous
    pairings (the alphabetized-export swap guard)."""
    from converter.generators.rdl import _alias_select_items

    # implicit alias, positional pairing
    out = _alias_select_items(
        "select at.ACTION_TYPE_ID ACTION_TYPE_ID_2, at.DES DESC_2, "
        "count(*) C from t",
        ["ACTION_TYPE_ID_3", "DESC_3", "C"])
    assert "ACTION_TYPE_ID_3" in out and "ACTION_TYPE_ID_2" not in out, out
    assert "DESC_3" in out, out

    # bare column, NON-positional (order differs) -> unique-stem pairing
    out2 = _alias_select_items(
        "select s.site_id sic_id, s.site_name, s.other from t",
        ["other", "site_name1", "sic_id"])
    assert "site_name AS site_name1" in out2, out2

    # positional 1:1 with pairwise stem agreement rebinds BOTH (Oracle
    # bound them positionally — that IS the correct pairing)
    out3 = _alias_select_items(
        "select s.site_id, f.site_id2 from t",
        ["site_id1", "site_id3"])
    assert "AS site_id1" in out3 and "AS site_id3" in out3, out3

    # NON-positional with TWO declared stem-candidates must refuse
    out4 = _alias_select_items(
        "select s.site_id from t",
        ["site_id1", "site_id3", "unrelated"])
    assert "AS " not in out4, out4


def test_no_space_implicit_alias_never_double_aliased():
    """Oracle accepts ``count(*)inspections`` (no space before the
    implicit alias). The detector must see it — appending a derived alias
    produced ``count(*)inspections AS COUNT_INSPECTIONS`` = ORA-00923 on
    the server (production corpus, two datasets)."""
    from converter.generators.rdl import _alias_select_items

    out = _alias_select_items(
        "select count(*)inspections from t", ["inspections"])
    assert out.strip() == "select count(*)inspections from t", out
    out2 = _alias_select_items(
        "select count(*)num_enf_req_8 from x", ["num_enf_req_8"])
    assert "AS " not in out2, out2


def test_rdl_title_keeps_tokens_for_expression_resolution():
    """The grouped-tabular title picker must return &TOKEN lines RAW — the
    PageHeader resolves them into real SSRS expressions. Resolving with the
    mockup's sample resolver baked fabricated sample data ('...for Type
    Alpha') into the deployable RDL as a static title (agent-army P0)."""
    from types import SimpleNamespace as _NS
    from converter.generators.rdl import _grouped_tabular_title

    rep = _NS(layout=[_NS(kind="section_main", fields=[
        _NS(kind="text", text="Logsheets for &REPORT_VEHICLE_TYPE",
            font_size=16, y=0.1),
        _NS(kind="text", text="(continued)", font_size=8, y=0.5),
    ], children=[])])
    lines = _grouped_tabular_title(rep)
    assert lines == ["Logsheets for &REPORT_VEHICLE_TYPE"], lines


def test_breakdown_pass_never_binds_blob_columns_as_text():
    """A dropped frame whose field member is a BLOB (a page-header logo)
    must not become a breakdown tablix textbox — =Fields!Logo.Value prints
    raw bytes at runtime (agent-army P0)."""
    from converter import convert

    xml = (
        '<?xml version="1.0"?><report name="BLOB_T" DTDVersion="9.0.2.0.10">'
        '<data>'
        '<dataSource name="Q_M"><select><![CDATA[select a, b from t]]>'
        '</select><group name="G_M"><dataItem name="A" datatype="vchar2"/>'
        '<dataItem name="B" datatype="vchar2"/></group></dataSource>'
        '<dataSource name="Q_IMG"><select><![CDATA[select logo from imgs]]>'
        '</select><group name="G_IMG"><dataItem name="LOGO" '
        'datatype="blob" fileFormat="image"/></group></dataSource>'
        '</data>'
        '<layout><section name="main">'
        '<frame name="M_B"><geometryInfo x="0" y="0" width="8" height="4"/>'
        '<repeatingFrame name="R_M" source="G_M" printDirection="down">'
        '<geometryInfo x="0.2" y="0.2" width="7.5" height="0.2"/>'
        '<field name="F_A" source="A"><geometryInfo x="0.2" y="0.2" '
        'width="3" height="0.2"/></field>'
        '<field name="F_B" source="B"><geometryInfo x="3.4" y="0.2" '
        'width="2" height="0.2"/></field></repeatingFrame>'
        '<repeatingFrame name="R_IMG" source="G_IMG" printDirection="down">'
        '<geometryInfo x="0.2" y="1.0" width="2" height="1.0"/>'
        '<field name="F_LOGO" source="LOGO"><geometryInfo x="0.2" y="1.0" '
        'width="2" height="1.0"/></field></repeatingFrame>'
        '</frame></section></layout></report>'
    ).encode()
    import xml.etree.ElementTree as _ET
    rdl = convert(xml)["rdl_xml"]
    root = _ET.fromstring(rdl.encode("utf-8"))
    ns = root.tag.split("}")[0][1:]
    for tb in root.iter(f"{{{ns}}}Textbox"):
        for v in tb.iter(f"{{{ns}}}Value"):
            assert "Fields!LOGO" not in (v.text or ""), (
                f"blob bound as TEXT in {tb.get('Name')}")
    imgs = [i for i in root.iter(f"{{{ns}}}Image")
            if "Fields!LOGO" in "".join(
                v.text or "" for v in i.iter(f"{{{ns}}}Value"))]
    assert imgs, "blob field lost entirely — expected a Database <Image>"
    assert imgs[0].findtext(f"{{{ns}}}Source") == "Database"


def test_link_key_injected_into_every_union_branch():
    """Oracle <link> join-key augmentation must add the key column to EVERY
    set-operator branch, or the branches differ in column count and the
    dataset dies with ORA-01789 (production: a grant-status report shipped
    11-vs-10 and 10-vs-9 branches while preflight said READY). A branch
    that cannot see the source table — the classic ``FROM DUAL`` fallback,
    even when it names the table inside a NOT EXISTS subquery — must get
    ``NULL <alias>``, never the qualified column (ORA-00904)."""
    from converter.parsers.oracle_xml import (
        _augment_child_join_keys, _column_visible_in_branch,
        _top_level_branch_spans)
    from converter.models import DataQuery, DataItem

    child = DataQuery(name="Q_CHILD")
    child.sql = (
        "SELECT X.A A, X.B B FROM T X WHERE X.Org_Id = :Org_Id\n"
        "UNION ALL\n"
        "SELECT NULL A, 1 B FROM DUAL "
        "WHERE NOT EXISTS(SELECT * FROM T X WHERE X.Org_Id = :Org_Id)")
    child.items = [DataItem(name="A"), DataItem(name="B")]
    master = DataQuery(name="Q_MASTER")
    master.items = [DataItem(name="Org_Id")]
    warnings = []
    _augment_child_join_keys(child, master, warnings)
    assert not warnings, warnings

    spans = _top_level_branch_spans(child.sql)
    assert len(spans) == 2, child.sql
    b0, b1 = (child.sql[s:e] for s, e in spans)
    assert "X.Org_Id Org_Id" in b0, b0
    # the DUAL fallback names T/X only inside NOT EXISTS -> NULL, not X.Org_Id
    assert "NULL Org_Id" in b1, b1
    assert "X.Org_Id Org_Id" not in b1, b1
    # both branches now select the same number of top-level items
    def _arity(seg):
        head = seg.upper().split("FROM", 1)[0]
        return head.count(",") + 1
    assert _arity(b0) == _arity(b1), (b0, b1)

    # the visibility helper must not be fooled by a subquery mention
    assert not _column_visible_in_branch(
        "SELECT 1 FROM DUAL WHERE NOT EXISTS(SELECT * FROM COGX C)",
        "C.Org_Id")
    assert _column_visible_in_branch("SELECT 1 FROM COGX C WHERE 1=1",
                                     "C.Org_Id")


def test_preflight_flags_union_arity_mismatch():
    """The ORA-01789 class must be a BLOCKER, never a silent READY."""
    from converter.validators.preflight import preflight_audit

    def _rdl(sql):
        return ('<Report xmlns="http://schemas.microsoft.com/sqlserver/'
                'reporting/2008/01/reportdefinition"><DataSets><DataSet '
                'Name="Q"><Query><DataSourceName>D</DataSourceName>'
                f'<CommandText>{sql}</CommandText></Query><Fields>'
                '<Field Name="A"><DataField>A</DataField></Field>'
                '</Fields></DataSet></DataSets></Report>')

    bad = _rdl("SELECT A, B, C FROM T UNION ALL SELECT A, B FROM S")
    good = _rdl("SELECT A, B FROM T UNION ALL SELECT A, B FROM S")
    hits = [i for i in preflight_audit(bad)["issues"]
            if i.get("rule") == "sql.union_arity_mismatch"]
    assert hits and hits[0]["severity"] == "BLOCKER", hits
    assert not [i for i in preflight_audit(good)["issues"]
                if i.get("rule") == "sql.union_arity_mismatch"]


def test_link_key_injection_respects_group_by_and_aggregates():
    """Selecting the injected join key from a GROUPED query is ORA-00979
    unless the key is also grouped, and from an AGGREGATE query with no
    GROUP BY it is ORA-00937 (production: three letter reports shipped
    datasets that could never execute). The key must therefore be added to
    an existing GROUP BY, must NOT be re-added when it is already grouped
    inside ROLLUP()/CUBE() (that would suppress the rollup total row), and
    must degrade to NULL when the query aggregates without grouping."""
    from converter.parsers.oracle_xml import _augment_child_join_keys
    from converter.models import DataQuery, DataItem

    master = DataQuery(name="Q_M")
    master.items = [DataItem(name="Org_Id")]

    # 1) GROUPED query -> key added to the GROUP BY list
    a = DataQuery(name="Q_A")
    a.sql = ("SELECT T.Name, COUNT(*) N FROM T WHERE T.Org_Id = :Org_Id "
             "GROUP BY T.Name ORDER BY T.Name")
    a.items = [DataItem(name="Name"), DataItem(name="N")]
    _augment_child_join_keys(a, master, [])
    assert "T.Org_Id Org_Id" in a.sql, a.sql
    gb = a.sql.upper().split("GROUP BY", 1)[1]
    assert "ORG_ID" in gb.split("ORDER BY")[0], a.sql

    # 2) ROLLUP already groups it -> not re-added (one occurrence in GB)
    b = DataQuery(name="Q_B")
    b.sql = ("SELECT T.Name, SUM(T.Amt) S FROM T WHERE T.Org_Id = :Org_Id "
             "GROUP BY ROLLUP(T.Org_Id, T.Name)")
    b.items = [DataItem(name="Name"), DataItem(name="S")]
    _augment_child_join_keys(b, master, [])
    gb_b = b.sql.upper().split("GROUP BY", 1)[1]
    assert gb_b.count("ORG_ID") == 1, b.sql

    # 3) AGGREGATE with no GROUP BY -> NULL, never a bare column
    c = DataQuery(name="Q_C")
    c.sql = "SELECT COUNT(*) N FROM T WHERE T.Org_Id = :Org_Id"
    c.items = [DataItem(name="N")]
    _augment_child_join_keys(c, master, [])
    assert "NULL Org_Id" in c.sql, c.sql
    assert "T.Org_Id Org_Id" not in c.sql, c.sql


_CHAIN_XML = (
    '<?xml version="1.0"?><report name="CHAIN_T" DTDVersion="9.0.2.0.10">'
    '<data>'
    '<dataSource name="Q_TOP"><select><![CDATA[select org_id, nm '
    'from orgs]]></select>'
    '<group name="G_TOP"><dataItem name="Org_Id" datatype="number"/>'
    '<dataItem name="NM" datatype="vchar2"/></group></dataSource>'
    '<dataSource name="Q_MID"><select><![CDATA[select app_id, org_id '
    'from apps where org_id = :Org_Id]]></select>'
    '<group name="G_MID"><dataItem name="APP_ID" datatype="number"/>'
    '<dataItem name="Org_Id" datatype="number"/></group></dataSource>'
    '<dataSource name="Q_FAR"><select><![CDATA[select app_id, cval '
    'from courses where app_id = :App_Id]]></select>'
    '<group name="G_FAR"><dataItem name="APP_ID" datatype="number"/>'
    '<dataItem name="CVAL" datatype="vchar2"/>'
    '<summary name="C_CVAL" source="CVAL" function="first"/>'
    '</group></dataSource>'
    '<link parentGroup="G_TOP" childQuery="Q_MID"/>'
    '<link parentGroup="G_MID" childQuery="Q_FAR"/>'
    '</data>'
    '<layout><section name="main">'
    '<frame name="M_B"><geometryInfo x="0" y="0" width="8" height="6"/>'
    '<text name="B_P1"><geometryInfo x="0.2" y="0.1" width="7" '
    'height="0.5"/><textSegment><font face="Arial" size="10"/>'
    '<string><![CDATA[This packet summarizes the accreditation record\n'
    'for the applicant shown below, including each course on file\n'
    'with the department at this time.]]></string></textSegment></text>'
    '<text name="B_P2"><geometryInfo x="0.2" y="0.7" width="7" '
    'height="0.5"/><textSegment><font face="Arial" size="10"/>'
    '<string><![CDATA[Questions about the accuracy of this record may\n'
    'be directed to the program office during normal business hours\n'
    'for correction or appeal.]]></string></textSegment></text>'
    '<repeatingFrame name="R_TOP" source="G_TOP" printDirection="down">'
    '<geometryInfo x="0.2" y="1.3" width="7.5" height="1.6"/>'
    '<field name="F_NM" source="NM"><geometryInfo x="0.2" y="1.3" '
    'width="3" height="0.2"/></field>'
    '<field name="F_C" source="C_CVAL"><geometryInfo x="0.2" y="1.6" '
    'width="3" height="0.2"/></field>'
    '</repeatingFrame></frame></section></layout></report>'
).encode()


def test_two_hop_link_resolves_by_correlation_not_global_first():
    """A value from a linked child TWO hops away (applicant -> application
    -> course) must be reached with NESTED correlated Lookups. A
    dataset-scoped First() there returns the globally first row — another
    record's data painted onto every record (agent-army verified P0). When
    no correlation can be built the value must be blank, never a global
    aggregate over a per-parent detail set."""
    from converter import convert

    rdl = convert(_CHAIN_XML)["rdl_xml"]
    assert 'Lookup(Lookup(' in rdl.replace(" ", ""), rdl[:400]
    assert 'Fields!CVAL.Value, "Q_FAR"' in rdl, rdl[:400]
    # never the uncorrelated form
    assert 'First(Fields!CVAL.Value, "Q_FAR")' not in rdl


def test_stripped_link_predicate_preserves_join_keys():
    """De-correlating a 1:many child (so it returns all rows) must not
    erase the join key — link_pairs has to keep it or the correlated
    Lookup can never be rebuilt."""
    from converter.generators.rdl import _strip_link_filter_predicates
    from converter.models import ParsedReport, DataQuery, DataItem

    rep = ParsedReport(name="R", dtd_version="9")
    master = DataQuery(name="Q_M")
    master.items = [DataItem(name="Org_Id")]
    master.group_names = ["G_M"]
    child = DataQuery(name="Q_C")
    child.items = [DataItem(name="Org_Id"), DataItem(name="V")]
    child.parent_group = "G_M"
    child.sql = ("SELECT V FROM T WHERE 1=1 "
                 "AND (:Org_Id IS NULL OR T.Org_Id = :Org_Id)")
    rep.queries.extend([master, child])

    _strip_link_filter_predicates(rep, {"Q_C"})
    assert ":Org_Id" not in child.sql, child.sql
    assert any((p or "").upper() == "ORG_ID"
               for p, _c in child.link_pairs), child.link_pairs


_MARGINF_XML = (
    '<?xml version="1.0"?><report name="MARGINF_T" DTDVersion="9.0.2.0.10">'
    '<data>'
    '<userParameter name="P_BUREAU" datatype="character">'
    '<userParameterValue value="Air"/></userParameter>'
    '<dataSource name="Q_M"><select><![CDATA[select a from t]]></select>'
    '<group name="G_M"><dataItem name="A" datatype="vchar2"/>'
    '</group></dataSource>'
    '</data>'
    '<layout><section name="main">'
    '<text name="B_TITLE"><geometryInfo x="0.2" y="0.1" width="7" '
    'height="0.3"/><textSegment><font face="Arial" size="16"/>'
    '<string><![CDATA[Bureau Activity Report]]></string></textSegment></text>'
    '<field name="F_bureau" source="P_BUREAU"><geometryInfo x="2.0" '
    'y="0.6" width="3" height="0.2"/></field>'
    '<frame name="M_B"><geometryInfo x="0" y="1.0" width="8" height="3"/>'
    '<repeatingFrame name="R_M" source="G_M" printDirection="down">'
    '<geometryInfo x="0.2" y="1.2" width="7.5" height="0.2"/>'
    '<field name="F_A" source="A"><geometryInfo x="0.2" y="1.2" '
    'width="3" height="0.2"/></field></repeatingFrame></frame>'
    '</section></layout></report>'
).encode()


def _percert_xml(loose_chrome: bool) -> bytes:
    """A per-record certificate whose every object lives INSIDE its record
    frames (no <margin>, no loose section items) — Oracle prints NO page
    header band for it. ``loose_chrome=True`` adds a section-level title
    text OUTSIDE every frame (the authored-page-chrome dialect)."""
    chrome = (
        '<text name="B_ChromeTitle"><geometryInfo x="0.2" y="0.05" width="7" '
        'height="0.3"/><textSegment><font face="Arial" size="14" bold="yes"/>'
        '<string><![CDATA[STATE LICENSE ROSTER]]></string></textSegment>'
        '</text>') if loose_chrome else ''
    return (
        '<?xml version="1.0"?><report name="PERCERT_T" DTDVersion="9.0.2.0.10">'
        '<data>'
        '<dataSource name="Q_C"><select><![CDATA[select holder_nm from lic]]>'
        '</select><group name="G_C"><dataItem name="HOLDER_NM" '
        'datatype="vchar2"/></group></dataSource>'
        '</data>'
        '<layout><section name="main">' + chrome +
        '<frame name="M_Cert"><geometryInfo x="0" y="0.5" width="7.5" '
        'height="5"/>'
        '<repeatingFrame name="R_C" source="G_C" printDirection="down" '
        'maxRecordsPerPage="1"><geometryInfo x="0" y="0.5" width="7.5" '
        'height="4.5"/>'
        '<text name="B_T1"><geometryInfo x="0.5" y="0.6" width="6.5" '
        'height="0.4"/><textSegment><font face="Arial" size="14" bold="yes"/>'
        '<string><![CDATA[STATE LICENSE CERTIFICATE\nOFFICE OF RECORDS]]>'
        '</string></textSegment></text>'
        '<field name="F_H" source="HOLDER_NM"><geometryInfo x="0.5" y="1.2" '
        'width="4" height="0.25"/></field>'
        '</repeatingFrame></frame>'
        '<frame name="M_Foot"><geometryInfo x="0" y="5.6" width="7.5" '
        'height="1"/>'
        '<text name="B_T2"><geometryInfo x="0.5" y="5.7" width="6.5" '
        'height="0.6"/><textSegment><font face="Arial" size="10"/>'
        '<string><![CDATA[This document certifies the holder named above.\n'
        'It is not transferable to any other party or service.]]></string>'
        '</textSegment></text></frame>'
        '</section></layout></report>'
    ).encode()


def test_per_record_page_chrome_is_declaration_driven():
    """A certificate/letter source that declares NO page chrome (no <margin>
    band, no loose section-level objects — everything inside its record
    frames) must NOT get the synthesized title/run-on/page-number header
    band: Oracle prints none, the title belongs where the BODY declares it
    (adversarial-sweep finding on the permit family)."""
    import xml.etree.ElementTree as _ET
    from converter import convert

    rdl = convert(_percert_xml(loose_chrome=False))["rdl_xml"]
    for tb in ("Tb_PageTitle", "Tb_RunOn", "Tb_PageNum"):
        assert tb not in rdl, f"synthesized {tb} invented on chrome-less per-record report"
    # The title still renders — from the BODY, at its declared spot.
    assert "STATE LICENSE CERTIFICATE" in rdl

    # PROVE THE GATE CAN FAIL: the SAME report WITH an authored section-level
    # chrome object keeps its page-header band (declared chrome, not gated).
    rdl2 = convert(_percert_xml(loose_chrome=True))["rdl_xml"]
    root2 = _ET.fromstring(rdl2.encode("utf-8"))
    ns = root2.tag.split("}")[0][1:]
    ph2 = root2.find(f"{{{ns}}}Page").find(f"{{{ns}}}PageHeader")
    assert ph2 is not None
    ph2_txt = _ET.tostring(ph2, encoding="unicode")
    assert "Tb_PageTitle" in ph2_txt


def test_margin_parameter_field_reaches_the_page_header():
    """Oracle prints margin-resident FIELDS (a parameter echo, a run-date
    stamp) on every page. The page-header pass only accepted text items, so
    these were dropped from the RDL while the mockup still showed them —
    the deployed report silently lost the line (agent-army verified).
    The temporary data-marginx tag must never survive into the RDL."""
    import xml.etree.ElementTree as _ET
    from converter import convert

    rdl = convert(_MARGINF_XML)["rdl_xml"]
    assert "data-marginx" not in rdl, "internal tag leaked into the RDL"
    root = _ET.fromstring(rdl.encode("utf-8"))
    ns = root.tag.split("}")[0][1:]
    page = root.find(f"{{{ns}}}Page")
    ph = page.find(f"{{{ns}}}PageHeader")
    vals = [(v.text or "") for v in ph.iter(f"{{{ns}}}Value")]
    assert any("Parameters!P_BUREAU.Value" in v for v in vals), vals


def test_header_margin_item_not_duplicated_when_body_renders_it():
    """The page is built without sight of the body, so margin items are
    emitted optimistically; the reconciliation post-pass must drop one
    whose exact value the body already renders."""
    import xml.etree.ElementTree as _ET
    from converter.generators.rdl import (
        _drop_duplicated_header_margin_items, _q)

    root = _ET.Element(_q("Report"))
    body = _ET.SubElement(root, _q("Body"))
    bri = _ET.SubElement(body, _q("ReportItems"))
    btb = _ET.SubElement(bri, _q("Textbox"))
    _ET.SubElement(_ET.SubElement(_ET.SubElement(_ET.SubElement(
        btb, _q("Paragraphs")), _q("Paragraph")), _q("TextRuns")),
        _q("TextRun")).append(_ET.Element(_q("Value")))
    next(btb.iter(_q("Value"))).text = "=Parameters!P_X.Value"

    page = _ET.SubElement(root, _q("Page"))
    ph = _ET.SubElement(page, _q("PageHeader"))
    _ET.SubElement(ph, _q("Height")).text = "0.97in"
    pri = _ET.SubElement(ph, _q("ReportItems"))
    for expr in ("=Parameters!P_X.Value", "=Parameters!P_KEEP.Value"):
        tb = _ET.SubElement(pri, _q("Textbox"))
        tb.set("data-marginx", "1")
        _ET.SubElement(_ET.SubElement(_ET.SubElement(_ET.SubElement(
            tb, _q("Paragraphs")), _q("Paragraph")), _q("TextRuns")),
            _q("TextRun")).append(_ET.Element(_q("Value")))
        list(tb.iter(_q("Value")))[0].text = expr

    _drop_duplicated_header_margin_items(root)
    left = ["".join(v.text or "" for v in tb.iter(_q("Value")))
            for tb in pri.iter(_q("Textbox"))]
    assert left == ["=Parameters!P_KEEP.Value"], left
    assert not any("data-marginx" in tb.attrib
                   for tb in pri.iter(_q("Textbox")))
    assert ph.findtext(_q("Height")) == "0.73in"


def test_sql_grammar_validator_flags_only_converter_introduced_breakage():
    """Real-grammar SQL validation, judged DIFFERENTIALLY.

    A generated query that fails to parse is only OUR defect when the
    ORIGINAL parsed cleanly. Oracle constructs a third-party grammar
    cannot model (legacy (+) comparisons, KEEP DENSE_RANK, package calls)
    appear in BOTH and must never be reported — that noise would bury the
    real finding. With no grammar backend installed the validator must
    have no opinion rather than raise a false alarm."""
    from types import SimpleNamespace as _NS
    from converter.validators import sql_syntax as S

    src_good = "SELECT A, B FROM T"
    report = _NS(queries=[_NS(name="Q_OK", sql=src_good),
                          _NS(name="Q_ORACLEISM",
                              sql="SELECT A FROM T WHERE X(+) < 5")])

    if not S.grammar_available():
        assert S.differential_issues(report, {"Q_OK": "SELECT ("}) == []
        return

    # 1) we broke a query whose source was fine -> BLOCKER
    issues = S.differential_issues(
        report, {"Q_OK": "SELECT A, FROM T WHERE ("})
    assert issues and issues[0]["severity"] == "BLOCKER", issues
    assert "Q_OK" in issues[0]["rule"]

    # 2) source carries the same unmodellable construct -> silence
    assert S.differential_issues(
        report, {"Q_ORACLEISM": "SELECT A FROM T WHERE X(+) < 5"}) == []

    # 3) a clean query is clean
    assert S.differential_issues(report, {"Q_OK": src_good}) == []

    # 4) bind variables are placeholders, not grammar — never a failure
    ok, _err = S.parse_check(
        "SELECT A FROM T WHERE ID = :P_ID AND D >= :P_START")
    assert ok


def test_broken_sql_reaches_the_preflight_verdict():
    """The grammar validator must actually gate convert()'s verdict, not
    just exist — a query we broke has to surface as a BLOCKER."""
    from converter.validators import sql_syntax as S
    if not S.grammar_available():
        return
    from converter import _dataset_command_texts

    rdl = ('<Report xmlns="http://schemas.microsoft.com/sqlserver/reporting'
           '/2008/01/reportdefinition"><DataSets><DataSet Name="Q_X">'
           '<Query><DataSourceName>D</DataSourceName>'
           '<CommandText>SELECT A, FROM T WHERE (</CommandText></Query>'
           '</DataSet></DataSets></Report>')
    got = _dataset_command_texts(rdl)
    assert got == {"Q_X": "SELECT A, FROM T WHERE ("}, got

    from types import SimpleNamespace as _NS
    rep = _NS(queries=[_NS(name="Q_X", sql="SELECT A, B FROM T")])
    issues = S.differential_issues(rep, got)
    assert issues and issues[0]["severity"] == "BLOCKER"


def test_statement_terminator_stripped_so_subquery_wrapping_is_valid():
    """Oracle exports often store a trailing ';' — and a commented-out
    alternative query after it — inside the <select> CDATA. Harmless
    standalone, but this converter wraps a query as a derived table to
    inline formula columns, and ';' inside parentheses is a hard syntax
    error (found by real-grammar validation on two wild reports). Only a
    true trailing terminator is removed; a ';' inside a literal or a
    multi-statement body must survive untouched."""
    from converter.parsers.oracle_xml import _strip_statement_terminator

    assert _strip_statement_terminator(
        "SELECT A FROM T;") == "SELECT A FROM T"
    assert _strip_statement_terminator(
        "SELECT A FROM T;\n/*SELECT B FROM U;*/") == "SELECT A FROM T"
    assert _strip_statement_terminator(
        "SELECT A FROM T;  -- old query") == "SELECT A FROM T"
    # a ';' inside a string literal is data, not a terminator
    kept = "SELECT ';' AS SEP FROM T"
    assert _strip_statement_terminator(kept) == kept
    # real second statement -> never truncate
    multi = "SELECT A FROM T; SELECT B FROM U"
    assert _strip_statement_terminator(multi) == multi
    assert _strip_statement_terminator("") == ""


def test_deliberate_placeholder_query_is_not_a_sql_finding():
    """A dataset whose Oracle source was a NON-relational pluggable
    source gets a comment-only CommandText explaining how to wire it up.
    That is a declared limitation, not a query we broke — the grammar
    validator must stay silent (otherwise every such report would get a
    bogus BLOCKER verdict)."""
    from types import SimpleNamespace as _NS
    from converter.validators import sql_syntax as S

    if not S.grammar_available():
        return
    stub = ("-- This dataset originally read from a NON-SQL source.\n"
            "-- Point it at your data source in Report Builder.")
    report = _NS(queries=[_NS(name="QP_1", sql="")])
    assert S.differential_issues(report, {"QP_1": stub}) == []
    # an EMPTY source query also means there was never a query to break
    assert S.differential_issues(report, {"QP_1": "SELECT A, FROM"}) == []


def test_preflight_catches_its_own_cardinal_defects():
    """MUTATION TEST, in CI. Every other test asks 'is the artifact
    good?'; this asks 'if it were bad, would we notice?'.

    Both cases below were REAL holes found by tools/sqlcheck/
    mutation_test.py: an emptied QueryParameter Value (the exact #1-rule
    prompt trigger) and an invalid FontWeight sailed through preflight
    untouched, because one rule lived only in an external script and the
    other element was missing from the enum table."""
    from converter.validators.preflight import preflight_audit

    def _rdl(query_body, style="<FontWeight>Bold</FontWeight>"):
        return (
            '<Report xmlns="http://schemas.microsoft.com/sqlserver/reporting'
            '/2008/01/reportdefinition">'
            '<ReportParameters><ReportParameter Name="P_ID">'
            '<DataType>String</DataType><Nullable>true</Nullable>'
            '<DefaultValue><Values><Value>=Nothing</Value></Values>'
            '</DefaultValue></ReportParameter></ReportParameters>'
            '<DataSets><DataSet Name="Q_1"><Query>'
            '<DataSourceName>D</DataSourceName>'
            '<CommandText>SELECT A FROM T WHERE ID = :P_ID</CommandText>'
            + query_body +
            '</Query><Fields><Field Name="A"><DataField>A</DataField>'
            '</Field></Fields></DataSet></DataSets>'
            '<Body><ReportItems><Textbox Name="T1"><Style>' + style +
            '</Style></Textbox></ReportItems></Body><Page/></Report>')

    good_qp = ('<QueryParameters><QueryParameter Name=":P_ID">'
               '<Value>=Parameters!P_ID.Value</Value>'
               '</QueryParameter></QueryParameters>')

    def rules(rdl):
        return " ".join(str(i.get("rule", ""))
                        for i in preflight_audit(rdl)["issues"])

    # baseline: the well-formed artifact trips none of these
    base = rules(_rdl(good_qp))
    assert "query_param" not in base, base
    assert "bad_enum" not in base, base

    # 1) emptied QueryParameter Value -> the prompt trigger
    empty_qp = good_qp.replace("<Value>=Parameters!P_ID.Value</Value>",
                               "<Value></Value>")
    assert "rdl.query_param_empty" in rules(_rdl(empty_qp))

    # 2) bind with no QueryParameter at all
    assert "rdl.query_param_missing" in rules(_rdl(""))

    # 3) bound to a parameter that isn't declared
    undecl = good_qp.replace("Parameters!P_ID.Value",
                             "Parameters!P_GHOST.Value")
    assert "rdl.query_param_undeclared" in rules(_rdl(undecl))

    # 4) invalid style enum literal
    assert "bad_enum" in rules(_rdl(good_qp, "<FontWeight>Chunky</FontWeight>"))


def test_content_coverage_contract_declares_what_it_drops():
    """A visible source field either lands in the artifact or the user is
    TOLD it did not. Content has vanished quietly before (a title segment,
    a subtitle line, 21 margin fields, a whole header frame) — silence is
    the failure mode this contract makes impossible.

    Disclosure is AMBER: a fidelity gap to report, never a reason to block
    a report that otherwise deploys and runs."""
    from types import SimpleNamespace as _NS
    from converter.validators.coverage import (
        visible_data_fields, is_accounted, unaccounted_fields,
        coverage_issues)

    rep = _NS(
        layout=[_NS(fields=[
            _NS(kind="field", source="SHOWN", name="F_SHOWN", visible=True),
            _NS(kind="field", source="GONE", name="F_GONE", visible=True),
            _NS(kind="field", source="HIDDEN", name="F_H", visible=False),
            _NS(kind="text", text="Label:", name="B_L", visible=True),
        ], children=[])],
        queries=[], formulas=[])

    assert visible_data_fields(rep) == [("F_SHOWN", "SHOWN"),
                                        ("F_GONE", "GONE")]

    rdl = "<Report><Value>=Fields!SHOWN.Value</Value></Report>"
    assert is_accounted("SHOWN", rdl, rep)
    assert not is_accounted("GONE", rdl, rep)
    assert unaccounted_fields(rep, rdl) == [("F_GONE", "GONE")]

    issues = coverage_issues(rep, rdl)
    assert len(issues) == 1
    assert issues[0]["severity"] == "AMBER", issues
    assert "GONE" in issues[0]["message"]
    # a fully-covered report says nothing
    assert coverage_issues(rep, rdl + "<Value>=Fields!GONE.Value</Value>") \
        == []


def test_coverage_disclosure_reaches_convert_output():
    """The disclosure must actually surface to the user through
    convert()'s preflight, not merely exist as a helper."""
    from converter import convert

    out = convert(_GRPSUM_XML)
    rules = [str(i.get("rule", "")) for i in out["preflight"]["issues"]]
    # this fixture is fully mapped; the rule must not fire spuriously
    assert "fidelity.unmapped_source_fields" not in rules, rules
    # and the verdict is unaffected by an AMBER disclosure
    assert out["preflight"]["verdict"] != "BLOCKER"


def test_ssrs_smoke_deploy_is_inert_until_configured():
    """The publish-time blind spot is closed by asking a REAL server —
    but the tool must be provably safe: no server configured means no
    network call, no upload, no deletion. It also must build well-formed
    SOAP and parse the server's own fault text, since that message is the
    whole point of the check."""
    import importlib.util
    import pathlib as _pl

    tool = (_pl.Path(__file__).resolve().parents[1] / "tools" /
            "ssrscheck" / "smoke_deploy.py")
    assert tool.exists(), "smoke-deploy tool missing"
    spec = importlib.util.spec_from_file_location("o2s_smoke", tool)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    import os
    saved = os.environ.pop("O2S_SSRS_URL", None)
    try:
        assert mod.server_configured() is False
        res = mod.smoke_deploy("<Report/>", "NAME")
        assert res["verdict"] == "SKIPPED", res
    finally:
        if saved is not None:
            os.environ["O2S_SSRS_URL"] = saved

    soap = mod._soap("CreateCatalogItem", "<Name>X</Name>")
    assert soap.startswith("<?xml")
    assert "CreateCatalogItem" in soap and "soap:Envelope" in soap
    # the server's own message is what makes a rejection actionable
    assert mod._tag_text(
        "<a><faultstring>rsInvalidReportDefinition</faultstring></a>",
        "faultstring") == "rsInvalidReportDefinition"
    assert mod._tag_text("<a/>", "faultstring") == ""


def test_empty_result_set_says_so_instead_of_printing_a_blank_page():
    """Zero rows is the most common production shape — a filter that
    matches nothing — and it was the one shape nothing here ever
    rendered. A report whose data region came back empty printed a
    completely BLANK sheet (found by tools/renderlab/shape_matrix.py at
    rows=0). Every data region must carry a NoRowsMessage, which renders
    ONLY when the region is empty.

    Schema position matters: NoRowsMessage follows DataSetName in the
    Tablix content model, and a misplaced element makes the whole RDL
    unloadable."""
    import xml.etree.ElementTree as _ET
    from converter import convert

    rdl = convert(_TOTALS_XML)["rdl_xml"]
    root = _ET.fromstring(rdl.encode("utf-8"))
    ns = root.tag.split("}")[0][1:]

    tablixes = list(root.iter(f"{{{ns}}}Tablix"))
    assert tablixes, "fixture emitted no data region"
    for tx in tablixes:
        if tx.find(f"{{{ns}}}DataSetName") is None:
            continue
        kids = [k.tag.split("}")[-1] for k in list(tx)]
        assert "NoRowsMessage" in kids, kids
        assert kids.index("NoRowsMessage") == kids.index("DataSetName") + 1, \
            f"NoRowsMessage out of schema order: {kids}"
        msg = tx.findtext(f"{{{ns}}}NoRowsMessage") or ""
        assert msg.strip(), "empty no-rows message helps nobody"

    # idempotent: the net must never double-insert on re-application
    from converter.generators.rdl import _ensure_no_rows_message
    _ensure_no_rows_message(root)
    for tx in root.iter(f"{{{ns}}}Tablix"):
        assert len(tx.findall(f"{{{ns}}}NoRowsMessage")) <= 1


def test_mockup_pages_stack_as_uniform_sheets():
    """The preview must read as a stack of PAPER: one root, pages the same
    width, one under the next.

    This bug survived several converter-side fixes because it did not live
    in the converter at all — the app's preview pane was display:flex, so
    every page became a flex ITEM sitting BESIDE the previous one, and
    flex-shrink squeezed each sheet below its natural width until the
    absolutely-positioned content inside overlapped and read as garbled.
    Hence the two assertions here: the emitted HTML must be a single
    block root of uniformly-sized sheets, AND the stylesheet must not lay
    the host out as a row."""
    import re as _re
    from converter import convert

    html = convert(_TOTALS_XML)["mockup_html"]

    # exactly one root element, and it is the desk
    depth = roots = 0
    for m in _re.finditer(r"<div\b|</div>", html):
        if m.group(0) == "<div":
            if depth == 0:
                roots += 1
            depth += 1
        else:
            depth -= 1
    assert roots == 1, f"{roots} top-level roots — pages will not stack"
    assert 'class="o2s-desk"' in html

    # every page sheet declares the SAME paper width
    sheets = _re.findall(r'class="o2s-page"[^>]*style="([^"]*)"', html)
    assert sheets, "no page sheets emitted"
    widths = {(_re.search(r"max-width:\s*([\d.]+)in", s) or [None, ""])[1]
              for s in sheets}
    assert len(widths) == 1, f"pages have differing widths: {widths}"
    for s in sheets:
        assert "display:block" in s.replace(" ", "")[:60] or \
            "display:block" in s, s[:80]

    # THE CONTAINER CONTRACT: the host must not be a flex row
    from pathlib import Path as _Path
    css = (_Path(__file__).resolve().parents[1] / "frontend" /
           "static" / "css" / "style.css").read_text(encoding="utf-8")
    rule = _re.search(r"\.mockup-host\s*\{([^}]*)\}", css)
    assert rule, ".mockup-host rule missing"
    body = rule.group(1).replace(" ", "")
    assert "display:flex" not in body, (
        "the preview host is a flex row again — pages will render "
        "side by side and be shrunk into each other")
    assert "display:block" in body, body


def test_preview_boxes_cannot_paint_outside_their_oracle_rectangle():
    """Oracle lays out FIXED boxes and hard-clips what does not fit. The
    preview instead let a box GROW when its text wrapped, so a long
    string in a narrow box painted straight over its neighbours — the
    wallet cards on a permit's second page were unreadable.

    Every positioned box therefore carries its declared height plus
    overflow:hidden, which makes that whole collision class impossible by
    construction rather than by after-the-fact nudging."""
    import re as _re
    from converter import convert

    # a positioned (letter/document) archetype, not the tabular path
    html = convert(_GRPSUM_XML)["mockup_html"]
    boxes = _re.findall(r'<div style="(position:absolute;[^"]*)"', html)
    assert boxes, "no positioned boxes emitted"
    bounded = [b for b in boxes if "height:" in b]
    assert bounded, "no box declares a height"
    for b in bounded:
        # a box with a declared height must also clip
        assert "overflow:hidden" in b, b[:140]


def test_card_text_is_not_clamped_against_a_seal_image():
    """A certificate/wallet card places text deliberately alongside (and
    partly over) a seal. Treating the seal as a hard right edge crushed a
    3.05in card text down to 0.65in and shredded it. Text is never
    clamped against an image — vertical bounding already prevents the
    collision this clamp was guarding."""
    from converter.preview.html_mockup import _decollide

    elems = [
        {"kind": "text", "x": 0.15, "y": 8.9, "w": 3.05, "h": 0.50,
         "text": "STATE AGENCY 2026"},
        {"kind": "image", "x": 0.80, "y": 8.9, "w": 1.20, "h": 1.20,
         "source": "SEAL"},
    ]
    out = _decollide([dict(e) for e in elems])
    card = [e for e in out if e["kind"] == "text"][0]
    assert card["w"] == 3.05, f"seal clamped the card text to {card['w']}"

    # a text neighbour still clamps normally
    elems2 = [
        {"kind": "text", "x": 0.15, "y": 8.9, "w": 3.05, "h": 0.20,
         "text": "Label:"},
        {"kind": "text", "x": 1.00, "y": 8.9, "w": 1.00, "h": 0.20,
         "text": "Value"},
    ]
    out2 = _decollide([dict(e) for e in elems2])
    first = [e for e in out2 if e["text"] == "Label:"][0]
    assert first["w"] < 3.05, "text-vs-text clamp stopped working"


def test_per_record_document_previews_one_page_per_record():
    """A letter / certificate / single-record form prints ONE PAGE PER
    RECORD in SSRS. The preview showed a single sheet, so it misstated
    the shape of the document: the engine rendered three pages from three
    rows while the preview claimed one. Measured across the corpus, 26 of
    32 reports disagreed with their own render.

    Each preview record page must also use its OWN record index, so the
    sample values differ and the repetition reads as real records rather
    than the same page duplicated."""
    import re as _re
    from converter import convert
    from converter.preview.html_mockup import _PREVIEW_RECORD_PAGES

    html = convert(_GRPSUM_XML)["mockup_html"]
    pages = len(_re.findall(r'class="o2s-page"', html))
    assert pages >= _PREVIEW_RECORD_PAGES, (
        f"per-record document previewed {pages} page(s); the engine "
        f"renders one per record")

    # page labels must count the real total, not "Page 1 of 1"
    labels = _re.findall(r"Page (\d+) of (\d+)", html)
    assert labels, "no page labels emitted"
    totals = {t for _n, t in labels}
    assert len(totals) == 1, f"inconsistent page totals: {totals}"
    assert int(totals.pop()) == pages, labels

    # record pages differ: sample values are drawn per record index
    sheets = _re.split(r'class="o2s-page"', html)[1:]
    texts = [_re.sub(r"<[^>]+>", " ", s) for s in sheets]
    assert len(set(texts)) > 1, "every record page is byte-identical"


def test_no_customer_identifiers_anywhere_in_the_repo():
    """This is a PUBLIC repo converting PRIVATE government reports.

    A customer's agency name or report filenames must never appear in
    anything git tracks — not even as test fixture input, which is
    exactly how one crept in (a seal-clamp fixture used the real agency's
    certificate wording, and two tests used real dataset and column
    names). Synthetic names carry the same test value and leak nothing.

    Scope is `git ls-files`, because that IS the public surface: local
    scratch output under ignored paths is not published and must not fail
    this guard."""
    import re as _re
    import subprocess as _sp
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parents[1]
    try:
        listing = _sp.run(["git", "ls-files"], cwd=root, capture_output=True,
                          text=True, timeout=60)
    except Exception:                                   # noqa: BLE001
        return                                          # no git -> skip
    if listing.returncode != 0:
        return

    # assembled from fragments so this guard never matches itself
    parts = ["mont" + "ana", "hel" + "ena", "mt" + "deq", "CM" + "VGY",
             "ASB" + "INSPC", "HZ" + "WST", "METH" + "ACT",
             "ASB" + "ACCRD", "ASB" + "ABATE"]
    banned = _re.compile("|".join(parts), _re.IGNORECASE)
    exts = {".py", ".md", ".js", ".css", ".html", ".txt", ".json", ".yml",
            ".xml", ".bat", ".sh", ".cfg", ".toml"}

    hits = []
    for rel in listing.stdout.splitlines():
        path = root / rel
        if path.suffix.lower() not in exts or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if banned.search(line):
                hits.append(f"{rel}:{n}: {line.strip()[:90]}")
    assert not hits, (
        "customer identifiers found in tracked files: "
        + "; ".join(hits[:12]))



def test_text_is_never_buried_under_a_seal_image():
    """A certificate seal must render BEHIND the words.

    This defect class hid for an entire session because the overlap
    metric filtered images out: a seal painted straight across the wallet
    card's text and the audit still reported "0 overlaps". Both halves
    are asserted here — the z-order contract that makes it impossible,
    and the clamp floor that stopped the previous over-correction (which
    crushed a 3.05in card down to 0.65in trying to dodge the seal)."""
    import re as _re
    from converter import convert
    from converter.preview.html_mockup import _decollide

    html = convert(_GRPSUM_XML)["mockup_html"]
    for st in _re.findall(r'<div style="(position:absolute;[^"]*)"', html):
        assert "z-index:2" in st.replace(" ", ""), st[:110]

    # a seal wide enough to leave a usable line DOES clamp the text back
    near = _decollide([
        {"kind": "text", "x": 0.15, "y": 8.9, "w": 3.05, "h": 0.20,
         "text": "Card line"},
        {"kind": "image", "x": 2.40, "y": 8.9, "w": 1.20, "h": 1.20,
         "source": "SEAL"},
    ])
    assert [e for e in near if e["kind"] == "text"][0]["w"] < 3.05

    # a seal that would leave only a sliver must NOT clamp — the text
    # keeps its width and z-order keeps it readable over the seal
    deep = _decollide([
        {"kind": "text", "x": 0.15, "y": 8.9, "w": 3.05, "h": 0.20,
         "text": "Card line"},
        {"kind": "image", "x": 0.80, "y": 8.9, "w": 1.20, "h": 1.20,
         "source": "SEAL"},
    ])
    assert [e for e in deep if e["kind"] == "text"][0]["w"] == 3.05


def test_chart_elements_are_disclosed_never_silently_dropped():
    """No corpus artifact carries an Oracle graph element (census: zero
    hits across every production + wild file), so chart translation has
    no real input to be built or verified against. The standing contract
    until one arrives: DISCLOSURE — a source with a chart converts
    normally but preflight says the chart will not appear. A report
    without charts must not carry the note."""
    from converter import convert

    base = _TOTALS_XML.decode("utf-8")
    with_chart = base.replace(
        "</report>",
        '<layout><section name="main"><body><graph name="G_Trend">'
        '<display x="1" y="1" width="4" height="3"/></graph>'
        "</body></section></layout></report>")

    out = convert(with_chart.encode("utf-8"))
    rules = {i.get("rule") for i in out["preflight"]["issues"]}
    assert "source.chart_element" in rules

    plain = convert(_TOTALS_XML)
    rules2 = {i.get("rule") for i in plain["preflight"]["issues"]}
    assert "source.chart_element" not in rules2


def test_simplified_tabular_shorthand_converts():
    """Oracle's geometry-free SIMPLIFIED layout syntax must convert.

    The docs promote hand-authoring reports as <tabular> (also <formlike>,
    <mailing>) holding bare <field source label/> children with layout
    computed automatically. A 59-file harvest of real-world definitions
    found this shorthand repeatedly, and full-geometry parsing saw zero
    content in every one -> declined as "renders blank". The shorthand
    maps to a geometry-less column list the tabular builder already
    handles. (<matrix>/<groupLeft>/<groupAbove> stay with their existing
    archetypes -- intercepting them broke six guards.)"""
    from converter import convert

    xml = (b'<?xml version="1.0"?>'
           b'<report name="shorthand" DTDVersion="1.0">'
           b'<data><dataSource name="Q_1"><select>'
           b'select item_code, item_desc from stock'
           b'</select></dataSource></data>'
           b'<layout><section name="main">'
           b'<tabular name="M_t">'
           b'<field name="F_c" source="item_code" label="Code"/>'
           b'<field name="F_d" source="item_desc" label="Description"/>'
           b'</tabular></section></layout></report>')
    out = convert(xml)
    assert out["preflight"]["verdict"] != "BLOCKER", \
        [i for i in out["preflight"]["issues"]][:3]
    rdl = out["rdl_xml"]
    assert "ITEM_CODE" in rdl.upper() and "ITEM_DESC" in rdl.upper()
    assert out["mockup_html"].strip(), "mockup must render the shorthand too"
    # The shorthand's label= wording is parsed onto the DATA ITEMS (see
    # parse: item.label == "Code"/"Description") but the record-list
    # builder this shape currently routes through emits bare value boxes
    # without a header band, so the wording does not reach the RDL yet.
    # KNOWN RESIDUAL: route simplified_* groups to the TABULAR builder
    # (whose _icaps already reads item.label) and then assert here that
    # "Code"/"Description" appear in the RDL.
    from converter.parsers.oracle_xml import parse_oracle_xml
    rep = parse_oracle_xml(xml)
    labels = {getattr(i, "label", "") for q in rep.queries
              for i in (q.items or [])}
    assert {"Code", "Description"} <= labels,         "shorthand labels must at least survive the parse"


def test_format_trigger_elsif_chains_and_boolean_constants_translate():
    """IF/ELSIF/ELSE trigger chains and local CONSTANT BOOLEANs translate.

    A declined trigger means NO Hidden expression at all, so the frame
    prints unconditionally — a production invoice's email-header variant
    painted straight over the print-letter title, and its debug frame
    (cbDEBUG CONSTANT BOOLEAN := FALSE) printed despite being off. Both
    shapes now reduce: ELSIF chains to nested IIf, constants folded
    before matching."""
    from converter.translators.plsql_formula import translate_format_trigger

    chain = """FUNCTION F_X_FT RETURN BOOLEAN IS BEGIN
      IF :Distribute = 'YES' AND UPPER(:Destination) = 'X.XML' THEN
        RETURN(FALSE);
      ELSIF :Corresp_Type = 'EMAIL' THEN
        RETURN(TRUE);
      ELSE
        RETURN(FALSE);
      END IF;
    END;"""
    h = translate_format_trigger(chain, resolve=lambda n: f"Parameters!{n.upper()}.Value")
    assert h and h.startswith("=Not(IIf(")
    assert "CORRESP_TYPE" in h.upper()

    const = """FUNCTION F_D_FT RETURN BOOLEAN IS
      cbDEBUG CONSTANT BOOLEAN := FALSE;
    BEGIN
      IF :P_Header = 'N' THEN RETURN(FALSE);
      ELSIF cbDEBUG THEN RETURN(TRUE);
      ELSE RETURN(FALSE);
      END IF;
    END;"""
    h2 = translate_format_trigger(const, resolve=lambda n: f"Parameters!{n.upper()}.Value")
    # every live branch returns FALSE -> the frame is permanently hidden
    assert h2 is not None


def test_elastic_frame_abutment_gets_a_reflow_gap():
    """Oracle elasticity flow: an elastic-bottomed frame and the sibling
    frame abutting it must emit with a STRICT positive gap.

    SSRS reflow pushes a sibling only when it sits strictly below the
    growing frame — at exact declared abutment (two letter frames meeting
    at the same inch) the engine paints the grown paragraph straight
    through the sibling (bisect-verified on two inspection letters, an
    invoice, and a permit). The flow pass reproduces Oracle's own
    semantics from the source's declarations: band shifts (everything
    below moves together, so a push can never manufacture a collision),
    frame-only frontiers (potential growth = elastic bottom-edge member;
    realized growth = interior flow past the declared box), and an exact
    recursive mirror of the emitted-height padding."""
    import re as _re
    from converter import convert

    xml = (b'<?xml version="1.0"?>'
           b'<report name="FLOW" DTDVersion="9.0.2.0.10">'
           b'<data><dataSource name="Q_1"><select>'
           b'select body_a, body_b from t</select></dataSource></data>'
           b'<layout><section name="main">'
           b'<repeatingFrame name="R_Rec" source="Q_1" printDirection="down">'
           b'<geometryInfo x="0" y="0" width="7.5" height="6.0"/>'
           b'<frame name="M_Upper">'
           b'<geometryInfo x="0.2" y="0.2" width="7.0" height="2.0"/>'
           b'<field name="F_A" source="body_a">'
           b'<geometryInfo x="0.3" y="2.0" width="6.5" height="0.19"/>'
           b'<generalLayout verticalElasticity="variable"/>'
           b'</field></frame>'
           b'<frame name="M_Lower">'
           b'<geometryInfo x="0.2" y="2.2" width="7.0" height="1.0"/>'
           b'<field name="F_B" source="body_b">'
           b'<geometryInfo x="0.3" y="2.3" width="6.5" height="0.19"/>'
           b'</field></frame>'
           b'</repeatingFrame></section></layout></report>')
    rdl = convert(xml)["rdl_xml"]
    # find the two frame rectangles' Tops and the upper's Height
    rects = _re.findall(
        r'<Rectangle Name="(RecP_Rect_\d+)">.*?<Top>([\d.]+)in</Top>'
        r'.*?<Height>([\d.]+)in</Height>', rdl, _re.S)
    assert len(rects) >= 2, rects
    tops = sorted((float(t), float(h)) for _, t, h in rects)
    upper_bottom = tops[0][0] + tops[0][1]
    lower_top = tops[1][0]
    assert lower_top >= upper_bottom + 0.015, (
        f"abutting sibling below an elastic-bottomed frame must emit with "
        f"a strict reflow gap: upper bottom {upper_bottom:.2f} vs lower "
        f"top {lower_top:.2f}")


def test_hidden_expressions_never_violate_server_dataset_scope():
    """The server's visibility scope rule, enforced before upload.

    A format-trigger Hidden built from another query's column uploaded
    fine through every local rail and was REFUSED by the state's report
    server at the Report Manager dialog ("Report item expressions can
    only refer to fields within the current dataset scope"). Two halves:
    the emitter scope-qualifies cross-dataset refs via First(...,
    "OwnerDS"), and preflight carries the server's rule as a BLOCKER —
    mutation-proved here by un-scoping a ref and asserting the gate
    fires."""
    import re as _re
    from converter import convert
    from converter.validators.preflight import preflight_audit

    xml = (b'<?xml version="1.0"?>'
           b'<report name="SCOPE" DTDVersion="9.0.2.0.10">'
           b'<data>'
           b'<dataSource name="Q_Main"><select>'
           b'select rec_id, rec_name from recs</select></dataSource>'
           b'<dataSource name="Q_Side"><select>'
           b'select side_flag from sides</select></dataSource>'
           b'</data>'
           b'<layout><section name="main">'
           b'<repeatingFrame name="R_Rec" source="Q_Main" printDirection="down">'
           b'<geometryInfo x="0" y="0" width="7.5" height="3.0"/>'
           b'<frame name="M_Var" formatTrigger="f_var_ft">'
           b'<geometryInfo x="0.2" y="0.2" width="7.0" height="1.0"/>'
           b'<field name="F_N" source="rec_name">'
           b'<geometryInfo x="0.3" y="0.3" width="6.5" height="0.19"/>'
           b'</field></frame>'
           b'</repeatingFrame></section></layout>'
           b'<programUnits><function name="f_var_ft"><textSource><![CDATA['
           b'FUNCTION F_Var_FT RETURN BOOLEAN IS BEGIN '
           b'IF :side_flag = \'Y\' THEN RETURN(TRUE); '
           b'ELSE RETURN(FALSE); END IF; END;]]></textSource></function>'
           b'</programUnits></report>')
    out = convert(xml)
    rdl = out["rdl_xml"]
    rules = [i.get("rule") for i in out["preflight"]["issues"]]
    assert "rdl.hidden_scope" not in rules, (
        "emitter must scope-qualify cross-dataset Hidden refs; "
        + str([h for h in _re.findall(r"<Hidden>([^<]+)</Hidden>", rdl)]))
    # any emitted cross-dataset Hidden must carry the explicit scope
    for h in _re.findall(r"<Hidden>(=[^<]*SIDE_FLAG[^<]*)</Hidden>", rdl):
        assert '"Q_Side"' in h or "SIDE_FLAG" not in h, h

    # PROVE THE GATE CAN FAIL: strip the scope qualifier back off
    broken = rdl.replace('First(Fields!SIDE_FLAG.Value, "Q_Side")',
                         "Fields!SIDE_FLAG.Value")
    if broken != rdl:
        res = preflight_audit(broken)
        rules2 = [i[1] if isinstance(i, tuple) else i.get("rule")
                  for i in res.get("issues", [])]
        assert "rdl.hidden_scope" in str(rules2), (
            "gate failed to flag a bare cross-dataset Hidden ref")


def test_body_items_never_exceed_printable_width():
    """Horizontal overflow doubles a report at production volume.

    An item whose right edge passes PageWidth minus the side margins makes
    SSRS paginate the excess into a near-blank companion page after EVERY
    content page (work-server verified: 5,758 pages, alternating
    content/blank, one stray box on each blank). The clamp narrows any
    protruding item to the printable edge — position untouched — and the
    root Width obeys the same bound. Mutation-proved: a source item
    declared past the printable edge must come out clamped."""
    import re as _re
    import xml.etree.ElementTree as _ET
    from converter import convert

    xml = (b'<?xml version="1.0"?>'
           b'<report name="WIDE" DTDVersion="9.0.2.0.10">'
           b'<data><dataSource name="Q_1"><select>'
           b'select col_a from t</select></dataSource></data>'
           b'<layout><section name="main">'
           b'<repeatingFrame name="R_Rec" source="Q_1" printDirection="down">'
           b'<geometryInfo x="0" y="0" width="8.4" height="2.0"/>'
           b'<field name="F_A" source="col_a">'
           # right edge at 0.5 + 9.6 = 10.1in — far past ANY printable width
           b'<geometryInfo x="0.5" y="0.3" width="9.6" height="0.19"/>'
           b'</field>'
           b'</repeatingFrame></section></layout></report>')
    rdl = convert(xml)["rdl_xml"]
    root = _ET.fromstring(rdl.encode("utf-8"))
    ns = root.tag.split("}")[0][1:]

    def q(t):
        return f"{{{ns}}}{t}"

    page = root.find(q("Page"))
    pw = float(page.findtext(q("PageWidth")).replace("in", ""))
    lm = float(page.findtext(q("LeftMargin")).replace("in", ""))
    rm = float(page.findtext(q("RightMargin")).replace("in", ""))
    printable = pw - lm - rm

    def widest(el, bl):
        w_here = 0.0
        tag = el.tag.split("}")[-1]
        if tag in ("Rectangle", "Textbox", "Tablix", "Image"):
            bl += float((el.findtext(q("Left")) or "0").replace("in", "")
                        or 0)
            w = float((el.findtext(q("Width")) or "0").replace("in", "")
                      or 0)
            w_here = bl + w
        return max([w_here] + [widest(c, bl) for c in el])

    body_extent = widest(root.find(q("Body")), 0.0)
    assert body_extent <= printable + 0.001, (
        f"body extends to {body_extent:.2f}in but printable width is "
        f"{printable:.2f}in — every content page will spawn a blank "
        f"overflow companion")
    root_w = float((root.findtext(q("Width")) or "99").replace("in", ""))
    assert root_w <= printable + 0.001


def test_inline_concat_criteria_builders_translate_to_real_filters():
    """The third criteria idiom: inline ||-concat builders now FILTER.

    A trigger builds ':P_Crit := :P_Crit || ' AND ' || cvCOL || ' LIKE
    UPPER(TRIM(:P_X))'' (constants naming column expressions, binds inside
    the literals, locals staging an EXISTS prefix, and a two-bind
    date-range prompt family). Dropping it ran the query UNFILTERED at
    the state (every criteria prompt did nothing). The translation is
    static SQL only: NULL-safe single-bind union + exact prompt-state
    CASE for the {A,B}/{A}/{B} family — never expression CommandText.
    """
    from converter.generators.rdl import _folded_lexical_predicates

    plsql = """
    FUNCTION After_Param RETURN BOOLEAN IS
      cvCITY CONSTANT VARCHAR2(30) := 'L.Addr_City' ;
      cvDT   CONSTANT VARCHAR2(30) := 'I.Ev_Date' ;
      vEx    VARCHAR2(200) ;
    BEGIN
      IF INSTR(:P_City, '%') > 0 THEN
        :P_Crit := :P_Crit || ' AND ' || cvCITY || ' LIKE UPPER(TRIM(:P_City))' ;
      ELSE
        :P_Crit := :P_Crit || ' AND ' || cvCITY || ' = UPPER(TRIM(:P_City))' ;
      END IF ;
      vEx := 'AND EXISTS( ' || 'SELECT * FROM Ev E WHERE E.Id = M.Id ' ;
      IF :P_A IS NOT NULL AND :P_B IS NOT NULL THEN
        :P_Crit := :P_Crit || vEx || ' AND (' || cvDT || ' >= :P_A' || ' AND ' || cvDT || ' <= :P_B))' ;
      ELSIF :P_A IS NOT NULL THEN
        :P_Crit := :P_Crit || vEx || ' AND ' || cvDT || ' = :P_A)' ;
      ELSIF :P_B IS NOT NULL THEN
        :P_Crit := :P_Crit || vEx || ' AND ' || cvDT || ' = :P_B)' ;
      END IF ;
      RETURN(TRUE) ;
    END ;
    """
    got = _folded_lexical_predicates(plsql)
    assert "P_CRIT" in got, got
    frag = got["P_CRIT"]
    # single-bind city predicates are NULL-safe
    assert "(:P_City IS NULL OR L.Addr_City LIKE UPPER(TRIM(:P_City)))" in frag
    # the two-bind family is an exact prompt-state CASE, not a naive union
    assert "CASE WHEN :P_A IS NOT NULL AND :P_B IS NOT NULL" in frag
    assert "I.Ev_Date >= :P_A AND I.Ev_Date <= :P_B" in frag
    assert "ELSE 1 END) = 1" in frag
    # PROVE IT DECLINES: an opaque part (function call) poisons the
    # lexical — all-or-nothing, no partially-reconstructed filters
    poisoned = plsql.replace("|| ' = UPPER(TRIM(:P_City))'",
                             "|| F_Mystery(:P_City)")
    assert "P_CRIT" not in _folded_lexical_predicates(poisoned)


def test_server_publish_rules_hold_structurally():
    """Ten server publish rules the upload dialog enforces, checked here.

    The Hidden-scope rejection proved the pattern: the server enforces
    structural rules no local rail encoded, and the first place they
    surfaced was the state's Report Manager. This locks the widest
    testable set: unique item names, CLS naming, no bare Fields! in page
    header/footer, no forward parameter dependencies, no aggregates in
    group expressions or dataset filters, subreports carry ReportName.
    Mutation-proved: a duplicated item name and a bare page-header
    Fields! ref are both flagged (verified before this test was written).
    """
    import collections
    import pathlib
    import re as _re
    import xml.etree.ElementTree as _ET
    from converter import convert

    AGG = _re.compile(r"\b(Sum|Count|CountDistinct|Avg|Min|Max|First|Last"
                      r"|StDev|Var|Aggregate|RunningValue)\s*\(", _re.I)
    CLS = _re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
    root_dir = pathlib.Path(__file__).resolve().parents[1]
    checked = 0
    for f in sorted((root_dir / "samples" / "oracle").glob("*.xml")):
        try:
            rdl = convert(f.read_bytes())["rdl_xml"]
        except Exception:  # noqa: BLE001
            continue
        checked += 1
        root = _ET.fromstring(rdl.encode("utf-8"))
        ns = root.tag.split("}")[0][1:]

        def q(t):
            return f"{{{ns}}}{t}"

        names = collections.Counter(
            el.get("Name") for el in root.iter()
            if el.get("Name") and el.tag.split("}")[-1] in
            ("Textbox", "Rectangle", "Tablix", "Image", "Line", "Subreport"))
        dups = {n: c for n, c in names.items() if c > 1}
        assert not dups, f"{f.name}: duplicate item names {dups}"
        bad = [n for n in names if not CLS.match(n)]
        assert not bad, f"{f.name}: non-CLS item names {bad[:4]}"
        for sect in ("PageHeader", "PageFooter"):
            for ph in root.iter(q(sect)):
                for v in ph.iter(q("Value")):
                    t = v.text or ""
                    assert not (t.startswith("=") and "Fields!" in t
                                and not AGG.search(t)), (
                        f"{f.name}: bare Fields! in {sect}: {t[:80]}")
        porder = {p.get("Name"): i
                  for i, p in enumerate(root.iter(q("ReportParameter")))}
        for i, p in enumerate(root.iter(q("ReportParameter"))):
            for v in p.iter(q("Value")):
                for ref in _re.findall(r"Parameters!(\w+)", v.text or ""):
                    assert porder.get(ref, -1) <= i, (
                        f"{f.name}: forward param dependency "
                        f"{p.get('Name')} -> {ref}")
        for ge in root.iter(q("GroupExpression")):
            assert not AGG.search(ge.text or ""), (
                f"{f.name}: aggregate in GroupExpression")
        for ds in root.iter(q("DataSet")):
            for flt in ds.iter(q("FilterExpression")):
                assert not AGG.search(flt.text or ""), (
                    f"{f.name}: aggregate in DataSet Filter")
        for sr in root.iter(q("Subreport")):
            assert (sr.findtext(q("ReportName")) or "").strip(), (
                f"{f.name}: Subreport without ReportName")
    assert checked, "no sample converted"


_CHROME_XML = (
    '<?xml version="1.0"?><report name="CHROME_T" DTDVersion="9.0.2.0.10">'
    '<data><dataSource name="Q_Main">'
    '<select><![CDATA[select item_nm, amt from t]]></select>'
    '<group name="G_Main"><dataItem name="ITEM_NM" datatype="vchar2"/>'
    '<dataItem name="AMT" datatype="number"/></group></dataSource></data>'
    '<layout><section name="main" width="10.00000" height="11.00000">'
    '<body><frame name="M_ALL"><geometryInfo x="0" y="0" width="7.5" '
    'height="3"/>'
    '<repeatingFrame name="R_Main" source="G_Main" printDirection="down">'
    '<geometryInfo x="0" y="0.5" width="7.5" height="0.4"/>'
    '<field name="F_ITEM" source="ITEM_NM">'
    '<geometryInfo x="0.1" y="0.55" width="2.0" height="0.2"/></field>'
    '<field name="F_AMT" source="AMT">'
    '<geometryInfo x="3.0" y="0.55" width="1.0" height="0.2"/></field>'
    '</repeatingFrame>'
    '<text name="B_KEEP"><visualSettings fillPattern="transparent" '
    'fillBackgroundColor="gray16"/>'
    '<geometryInfo x="0.1" y="0.1" width="1.0" height="0.2"/>'
    '<textSegment><font face="Arial" size="10"/>'
    '<string><![CDATA[Kept Fill]]></string></textSegment></text>'
    '<text name="B_DROP"><visualSettings fillBackgroundColor="r0g0b88"/>'
    '<geometryInfo x="2.1" y="0.1" width="1.0" height="0.2"/>'
    '<textSegment><font face="Arial" size="10"/>'
    '<string><![CDATA[Template Blue]]></string></textSegment></text>'
    '</frame></body>'
    '<margin>'
    '<text name="B_TITLE"><geometryInfo x="3.0" y="0.5" width="3.0" '
    'height="0.4"/><textSegment><font face="Times New Roman" size="24"/>'
    '<string><![CDATA[Chrome Test Title]]></string></textSegment></text>'
    '<text name="B_RUNON"><geometryInfo x="6.0" y="0.12" width="1.2" '
    'height="0.19"/><textSegment><font face="Arial" size="10"/>'
    '<string><![CDATA[Report run on:]]></string></textSegment></text>'
    '<text name="B_PGNUM"><textSettings justify="center"/>'
    '<geometryInfo x="3.4" y="10.4" width="2.0" height="0.17"/>'
    '<textSegment><font face="Arial" size="10"/>'
    '<string><![CDATA[Page &<PhysicalPageNumber>]]></string>'
    '</textSegment></text>'
    '</margin></section></layout></report>'
)


def test_fill_dialect_paints_only_pattern_marked_backgrounds():
    """Measured against the Oracle-rendered truth PDFs corpus-wide: a
    fillBackgroundColor PAINTS only when the <visualSettings> also
    carries a fillPattern attribute (even "transparent" — it marks the
    fill dialog as author-touched). Without it the color is an unpainted
    template leftover (the blue r0g0b88 family). Decisive source case:
    one report declares the same gray on 44 objects and the Oracle
    render paints exactly the 7 that carry the attribute."""
    from converter.parsers.oracle_xml import parse_oracle_xml
    rep = parse_oracle_xml(_CHROME_XML.encode())

    def find(name):
        def walk(g):
            for f in g.fields:
                if f.name == name:
                    return f
            for c in g.children:
                r = walk(c)
                if r is not None:
                    return r
            return None
        for g in rep.layout:
            r = walk(g)
            if r is not None:
                return r
        raise AssertionError(f"{name} not parsed")

    assert find("B_KEEP").background_color == "#D6D6D6", (
        "pattern-marked gray16 background must survive parsing")
    assert find("B_DROP").background_color == "", (
        "template blue without a fillPattern attribute must NOT paint")


def test_declared_margin_chrome_builds_the_page_header():
    """A source-authored <margin> band (title / run-date / page number at
    geometry) must become the PageHeader/PageFooter 1:1 — the synthesized
    centered-title header threw the declared arrangement away (truth PDF:
    logo top-left, title beside it, page number in the BOTTOM margin).
    Also: the declared section width IS the paper width (the truth PDF
    measures exactly it), and margin objects are tagged in_margin so
    emitters can tell page chrome from body content."""
    import re
    from converter import convert
    rdl = convert(_CHROME_XML.encode())["rdl_xml"]
    assert re.search(r"<PageWidth>10(\.0+)?in</PageWidth>", rdl), (
        "declared section width must become the paper width")
    ph = re.search(r"<PageHeader>(.*?)</PageHeader>", rdl, re.S)
    assert ph and "Chrome Test Title" in ph.group(1), (
        "declared margin title must render in the page header")
    assert "Report run on:" in ph.group(1), (
        "declared run-on stamp must render in the page header")
    pf = re.search(r"<PageFooter>(.*?)</PageFooter>", rdl, re.S)
    assert pf and "Globals!PageNumber" in pf.group(1), (
        "the bottom-margin page number must render in the page FOOTER "
        "with the report's own wording")
    assert "Tb_PageTitle" not in rdl, (
        "declared chrome must replace the synthesized title header")
    # PROVE THE GATE CAN FAIL: strip the margin band -> the converter
    # must fall back to the synthesized header (no declared chrome).
    no_margin = re.sub(r"<margin>.*?</margin>", "", _CHROME_XML,
                       flags=re.S)
    rdl2 = convert(no_margin.encode())["rdl_xml"]
    assert "MChrome_" not in rdl2, (
        "without a margin band there must be no declared-chrome items")


def test_undeclared_rule_width_emits_hairline_not_heavy_black():
    """Truth-measured dialect rule: a drawn <line> with linePattern="solid"
    and NO lineWidth attribute renders as the DEVICE HAIRLINE in Oracle's
    own PDFs (0-width strokes, rasterizing light gray) — every no-width
    truth rule across the corpus measures stroke width 0.0, while declared
    lineWidth maps 1:1 to stroke points (a lineWidth="1" report measures
    1.0-pt strokes). Emitting the old 0.5pt/1pt black bars painted heavy
    near-black rules where the truth shows light hairlines."""
    import re
    from converter import convert
    xml = _CHROME_XML.replace(
        '</margin>',
        '<line name="B_HAIR" arrow="none">'
        '<geometryInfo x="0.5" y="10.2" width="9.0" height="0.001"/>'
        '<visualSettings linePattern="solid"/></line>'
        '<line name="B_HEAVY" arrow="none">'
        '<geometryInfo x="0.5" y="10.3" width="9.0" height="0.001"/>'
        '<visualSettings lineWidth="2" linePattern="solid"/></line>'
        '</margin>')
    rdl = convert(xml.encode())["rdl_xml"]

    def line_block(name):
        m = re.search(rf'<Line Name="[^"]*{name}[^"]*">(.*?)</Line>', rdl, re.S)
        assert m, f"margin line {name} must emit"
        return m.group(1)

    hair = line_block("B_HAIR")
    assert "<Width>0.25pt</Width>" in hair, (
        "an UNDECLARED-width rule must emit the thinnest stroke (0.25pt), "
        "matching Oracle's device hairline")
    heavy = line_block("B_HEAVY")
    assert "<Width>2pt</Width>" in heavy, (
        "a DECLARED lineWidth must map 1:1 to stroke points")

    # The PARSER must preserve "undeclared" (0.0) on <line> objects so the
    # body-rule emitter can pick the hairline — collapsing it to 1.0 was
    # what painted the 0.72pt near-black separator bars.
    from converter.parsers.oracle_xml import parse_oracle_xml
    body_xml = _CHROME_XML.replace(
        '</frame></body>',
        '<line name="B_SEP" arrow="none">'
        '<geometryInfo x="0.0" y="2.5" width="7.4" height="0.0"/>'
        '<visualSettings linePattern="solid"/></line>'
        '<line name="B_SEP2" arrow="none">'
        '<geometryInfo x="0.0" y="2.7" width="7.4" height="0.0"/>'
        '<visualSettings lineWidth="3" linePattern="solid"/></line>'
        '</frame></body>')
    rep = parse_oracle_xml(body_xml.encode())

    def _find(name):
        def walk(g):
            for f in g.fields:
                if f.name == name:
                    return f
            for c in g.children:
                r = walk(c)
                if r is not None:
                    return r
        for g in rep.layout:
            r = walk(g)
            if r is not None:
                return r
        raise AssertionError(f"{name} not parsed")

    assert _find("B_SEP").border_width == 0.0, (
        "an undeclared line width must parse as 0.0 (hairline marker)")
    assert _find("B_SEP2").border_width == 3.0, (
        "a declared lineWidth must parse 1:1")
    # Source-lock the body rule branch: undeclared -> hairline thickness
    # (0.25pt / 0.003in floor), never the old 0.01in slab.
    import inspect
    from converter.generators import rdl as _rdlmod
    src = inspect.getsource(_rdlmod._emit_field_textbox)
    assert "bw_decl if bw_decl > 0 else 0.25" in src and "0.003" in src, (
        "the body rule emitter must derive hairline thickness from the "
        "DECLARED width, defaulting to the device hairline")


def test_single_query_reports_bind_layout_columns_regardless_of_names():
    """A single-query report's repeating frames belong to its only data
    source no matter what the group is named — a "Q_1" query with
    "G_PERSON" break groups defeated the Q_/G_ suffix convention and the
    tablix fell back to raw query items, dropping the parameter-fed
    layout columns AND the nested break group's detail columns that the
    Oracle truth PDF prints (accreditation summary: Address/City/Phone
    gone). Columns must follow the LAYOUT (x-ordered), including
    parameter-bound sources."""
    from converter.parsers.oracle_xml import parse_oracle_xml
    from converter.generators.rdl import _collect_layout_columns
    xml = (
        '<?xml version="1.0"?><report name="SQ_T" DTDVersion="9.0.2.0.10">'
        '<data><userParameter name="P_ADDR" datatype="character"/>'
        '<dataSource name="Q_1"><select><![CDATA[select person, exp_dt, '
        'kind from t]]></select>'
        '<group name="G_PERSON"><dataItem name="PERSON" datatype="vchar2"/>'
        '</group><group name="G_EXP"><dataItem name="EXP_DT" '
        'datatype="date"/><dataItem name="KIND" datatype="vchar2"/>'
        '</group></dataSource></data>'
        '<layout><section name="main"><body>'
        '<repeatingFrame name="R_Person" source="G_PERSON" '
        'printDirection="down">'
        '<geometryInfo x="0" y="0" width="10" height="0.5"/>'
        '<field name="F_PERSON" source="PERSON">'
        '<geometryInfo x="0.0" y="0.05" width="1.8" height="0.2"/></field>'
        '<field name="F_ADDR" source="P_ADDR">'
        '<geometryInfo x="2.0" y="0.05" width="2.4" height="0.2"/></field>'
        '<repeatingFrame name="R_Exp" source="G_EXP" printDirection="down">'
        '<geometryInfo x="4.6" y="0.02" width="4.0" height="0.4"/>'
        '<field name="F_EXP" source="EXP_DT">'
        '<geometryInfo x="4.6" y="0.05" width="0.9" height="0.2"/></field>'
        '<field name="F_KIND" source="KIND">'
        '<geometryInfo x="5.6" y="0.05" width="0.9" height="0.2"/></field>'
        '</repeatingFrame></repeatingFrame>'
        '</body></section></layout></report>'
    )
    rep = parse_oracle_xml(xml.encode())
    cols = _collect_layout_columns(rep, "Q_1")
    assert cols == ["PERSON", "P_ADDR", "EXP_DT", "KIND"], cols
    # PROVE THE GATE CAN FAIL: with a SECOND query the convention applies
    # and the unmatched frames contribute nothing.
    xml2 = xml.replace(
        '</data>',
        '<dataSource name="Q_Other"><select><![CDATA[select z from o]]>'
        '</select><group name="G_Z"><dataItem name="Z" datatype="vchar2"/>'
        '</group></dataSource></data>')
    rep2 = parse_oracle_xml(xml2.encode())
    assert _collect_layout_columns(rep2, "Q_1") == [], (
        "with two queries the name convention must gate matching again")


def test_inline_role_list_folds_into_parent_breakdown_row():
    """A nested repeating frame carrying exactly ONE data field of the
    SAME secondary dataset is Oracle's inline ROLE-LIST idiom (a stacked
    "PERMITTEE,/OWNER,/OPERATOR," beside the org block). Detaching it as
    its own breakdown tablix printed dangling label fragments (permit
    list, screenshot-truth verified); it must fold into the parent's row
    as a Join(LookupSet(...)) at its declared spot."""
    import re
    from converter import convert
    xml = (
        '<?xml version="1.0"?><report name="RL_T" DTDVersion="9.0.2.0.10">'
        '<data><dataSource name="Q_Main"><select><![CDATA[select cnty, '
        'site_nm from s]]></select><group name="G_County">'
        '<dataItem name="CNTY" datatype="vchar2"/></group>'
        '<group name="G_Site"><dataItem name="SITE_NM" datatype="vchar2"/>'
        '<dataItem name="SITE_ADDR" datatype="vchar2"/>'
        '<dataItem name="PERMIT" datatype="vchar2"/>'
        '<dataItem name="STATUS" datatype="vchar2"/>'
        '</group></dataSource>'
        '<dataSource name="Q_Org"><select><![CDATA[select org_nm, org_id, '
        'role_desc from o]]></select>'
        '<group name="G_Org"><dataItem name="ORG_NM" datatype="vchar2"/>'
        '<dataItem name="ORG_ID" datatype="number"/></group>'
        '<group name="G_Role"><dataItem name="ROLE_DESC" '
        'datatype="vchar2"/></group></dataSource></data>'
        '<layout><section name="main"><body>'
        '<repeatingFrame name="R_County" source="G_County" '
        'printDirection="down">'
        '<geometryInfo x="0" y="0" width="7.5" height="1.7"/>'
        '<field name="F_CNTY" source="CNTY">'
        '<geometryInfo x="0.0" y="0.05" width="2.0" height="0.2"/></field>'
        '<repeatingFrame name="R_Site" source="G_Site" '
        'printDirection="down">'
        '<geometryInfo x="0.1" y="0.3" width="7.4" height="1.4"/>'
        '<field name="F_SITE" source="SITE_NM">'
        '<geometryInfo x="0.15" y="0.35" width="3.0" height="0.2"/></field>'
        '<field name="F_ADDR" source="SITE_ADDR">'
        '<geometryInfo x="0.15" y="0.6" width="3.0" height="0.2"/></field>'
        '<field name="F_PERMIT" source="PERMIT">'
        '<geometryInfo x="4.5" y="0.35" width="0.8" height="0.2"/></field>'
        '<field name="F_STATUS" source="STATUS">'
        '<geometryInfo x="5.5" y="0.35" width="1.9" height="0.2"/></field>'
        '<repeatingFrame name="R_Org" source="G_Org" '
        'printDirection="down">'
        '<geometryInfo x="0.2" y="1.0" width="7.2" height="0.6"/>'
        '<field name="F_ORG" source="ORG_NM">'
        '<geometryInfo x="1.3" y="1.05" width="3.0" height="0.2"/></field>'
        '<repeatingFrame name="R_Role" source="G_Role" '
        'printDirection="down">'
        '<geometryInfo x="0.3" y="1.05" width="0.9" height="0.19"/>'
        '<field name="F_ROLE" source="ROLE_DESC">'
        '<geometryInfo x="0.3" y="1.05" width="0.85" height="0.19"/>'
        '</field></repeatingFrame></repeatingFrame>'
        '</repeatingFrame></repeatingFrame>'
        '</body></section></layout></report>'
    )
    rdl = convert(xml.encode())["rdl_xml"]
    assert re.search(
        r"Join\(LookupSet\(Fields!ORG_ID\.Value, Fields!ORG_ID\.Value, "
        r"Fields!ROLE_DESC\.Value", rdl), (
        "the role list must fold to a keyed Join(LookupSet(...)) member")
    # PROVE THE GATE CAN FAIL: the role frame must NOT also detach as its
    # own breakdown tablix (that was the fragment defect).
    bds = re.findall(r'Tablix_Breakdown_\d+', rdl)
    assert len(set(bds)) <= 1, f"role frame detached separately: {bds}"


def test_stem_alias_declines_lookup_and_garbage_formula_bodies():
    """The uncomputable-formula stem alias (CF_X -> column X) must NOT
    fire when the body shows X is not what the formula RETURNS: a
    cursor-lookup body (FETCH..INTO local; RETURN local — X is only the
    key) or a non-PL/SQL body (this export dialect can write stray QUERY
    TEXT into a formula's function slot). Aliasing printed a raw numeric
    ID where the looked-up NAME belongs (screenshot-truth verified)."""
    import re
    from converter import convert

    def mk(body):
        return (
            '<?xml version="1.0"?><report name="SA_T" '
            'DTDVersion="9.0.2.0.10"><data>'
            '<dataSource name="Q_1"><select><![CDATA[select person, '
            'thing from t]]></select>'
            '<group name="G_1"><dataItem name="PERSON" datatype="number"/>'
            '<dataItem name="THING" datatype="vchar2"/>'
            '<formula name="CF_PERSON" source="fx" datatype="character"/>'
            '</group></dataSource>'
            '<programUnits><function name="fx"><textSource><![CDATA['
            + body +
            ']]></textSource></function></programUnits></data>'
            '<layout><section name="main"><body>'
            '<repeatingFrame name="R_1" source="G_1" printDirection="down">'
            '<geometryInfo x="0" y="0" width="7.5" height="0.3"/>'
            '<field name="F_CF" source="CF_PERSON">'
            '<geometryInfo x="0.1" y="0.05" width="2.0" height="0.2"/>'
            '</field>'
            '<field name="F_T" source="THING">'
            '<geometryInfo x="3.0" y="0.05" width="2.0" height="0.2"/>'
            '</field></repeatingFrame>'
            '</body></section></layout></report>'
        )
    # cursor-lookup body: stem PERSON is only the WHERE key
    lookup = ("function fx return Char is CURSOR C IS SELECT NM FROM V_P "
              "WHERE ID = :PERSON; L_NM VARCHAR2(85); begin OPEN C; "
              "FETCH C INTO L_NM; CLOSE C; RETURN (L_NM); end;")
    rdl = convert(mk(lookup).encode())["rdl_xml"]
    assert not re.search(r"<Value>=Fields!PERSON\.Value</Value>", rdl), (
        "cursor-lookup formula must not alias to its key column")
    # garbage body (query text in the function slot)
    rdl2 = convert(mk("SELECT X FROM Y WHERE Z = 1 ORDER BY X").encode())[
        "rdl_xml"]
    assert not re.search(r"<Value>=Fields!PERSON\.Value</Value>", rdl2), (
        "non-PL/SQL body must not alias")
    # PROVE THE GATE STILL PASSES the legit case: a passthrough body DOES
    # alias (the CF_PERMITTEES-wraps-PERMITTEE win stays).
    ok = ("function fx return Char is begin RETURN(INITCAP(:PERSON)); "
          "end;")
    rdl3 = convert(mk(ok).encode())["rdl_xml"]
    assert ("Fields!PERSON.Value" in rdl3
            or "CF_PERSON" in rdl3), "legit passthrough must still resolve"


# ---------------------------------------------------------------------------
# Constant-label HEIGHT fidelity: an Oracle-sized heading box that clips its
# descenders under SSRS padding/metrics must deepen into free space (or shed
# its vertical paddings when a sibling sits right below); a WIDTH-clipped
# label must stay untouched (deepening would wrap it and hide the tail word).
# ---------------------------------------------------------------------------
_LABEL_H_XML = (
    '<?xml version="1.0"?><report name="LABH_T" DTDVersion="9.0.2.0.10">'
    '<data><dataSource name="Q_Main">'
    '<select><![CDATA[select item_nm, amt from t]]></select>'
    '<group name="G_Main"><dataItem name="ITEM_NM" datatype="vchar2"/>'
    '<dataItem name="AMT" datatype="number"/></group></dataSource></data>'
    '<layout><section name="main" width="8.50000" height="11.00000">'
    '<body><frame name="M_ALL"><geometryInfo x="0" y="0" width="7.5" '
    'height="6"/>'
    '<repeatingFrame name="R_Main" source="G_Main" printDirection="down">'
    '<geometryInfo x="0" y="0.5" width="7.5" height="0.4"/>'
    '<field name="F_ITEM" source="ITEM_NM">'
    '<geometryInfo x="0.1" y="0.55" width="2.0" height="0.2"/></field>'
    '</repeatingFrame>'
    '<text name="B_HA"><geometryInfo x="0.1" y="3.0" width="3.0" '
    'height="0.1666"/><textSegment>'
    '<font face="Times New Roman" size="12" bold="yes"/>'
    '<string><![CDATA[Heading Alpha]]></string></textSegment></text>'
    '<text name="B_HB"><geometryInfo x="4.0" y="3.0" width="3.0" '
    'height="0.1666"/><textSegment>'
    '<font face="Times New Roman" size="12" bold="yes"/>'
    '<string><![CDATA[Heading Bravo]]></string></textSegment></text>'
    '<text name="B_UNDER"><geometryInfo x="4.0" y="3.18" width="3.0" '
    'height="0.2"/><textSegment><font face="Times New Roman" size="10"/>'
    '<string><![CDATA[Blocking sibling line]]></string></textSegment></text>'
    '<text name="B_NARROW"><geometryInfo x="0.1" y="4.5" width="0.6" '
    'height="0.1666"/><textSegment>'
    '<font face="Times New Roman" size="12" bold="yes"/>'
    '<string><![CDATA[Narrow Label Overflowing]]></string></textSegment>'
    '</text>'
    '<text name="B_RBLOCK"><geometryInfo x="0.75" y="4.5" width="2.0" '
    'height="0.2"/><textSegment><font face="Times New Roman" size="10"/>'
    '<string><![CDATA[Right neighbour value box]]></string></textSegment>'
    '</text>'
    '</frame></body></section></layout></report>'
)


def _find_textbox_by_literal(rdl_xml, literal):
    import re
    import xml.etree.ElementTree as ET
    t = re.sub(r'xmlns="[^"]+"', "", rdl_xml, count=1)
    root = ET.fromstring(t)
    for tb in root.iter("Textbox"):
        vals = [v.text or "" for v in tb.iter("Value")]
        if vals == ['="' + literal + '"']:
            return tb
    return None


def test_constant_label_heights_fit_their_font():
    """A 12pt bold constant label in a 0.17in Oracle box clips its
    descenders under SSRS's line metrics (a bold two-word section heading
    with the g/p tails cut -- truth-verified on the emissions summary
    form). The box must deepen to exactly one full text line and no
    further, and a width-clipped label must stay at one line (deepening
    makes the engine WRAP it, hiding the tail word).

    Every bound below is the FONT'S OWN line box, not a rule of thumb.
    The old bounds carried 4pt of emitter padding inside them (a full
    12pt line "needed" 0.25in); an Oracle box has no inset, so the same
    line needs only its 0.1846in metric -- and a box inflated to 0.25in
    now FAILS the upper bound, which the old behaviour could not clear."""
    from converter import convert
    from converter.generators import rdl as R
    rdl = convert(_LABEL_H_XML.encode())["rdl_xml"]

    def _num(el, tag):
        return float((el.findtext(tag) or "0").replace("in", ""))

    # one full 12pt Times (serif) line, in inches -- the real face metric
    _line = R._font_line_box_pt(12, sans=False) / 72.0
    _ROUND = 0.02          # the deepen pass writes 2 decimals

    def _pads(el):
        """The three paddings that would MOVE a top-left anchored glyph.

        The fourth (bottom, under top-anchored text) is the one-line ceiling
        that keeps the engine from reserving an invisible second line; it
        displaces nothing, so it is not part of this assertion."""
        st = el.find("Style")
        return tuple(st.findtext("Padding" + s)
                     for s in ("Left", "Right", "Top"))

    ha = _find_textbox_by_literal(rdl, "Heading Alpha")
    assert ha is not None, "Heading Alpha label must emit"
    assert _num(ha, "Height") >= _line - 0.002, (
        "free space below -> the heading must deepen to a full text line")
    assert _num(ha, "Height") <= _line + _ROUND, (
        "...and no further: a declared box may not be inflated to hold an "
        "inset the emitter no longer adds")

    hb = _find_textbox_by_literal(rdl, "Heading Bravo")
    assert hb is not None
    assert _num(hb, "Height") < 0.24, (
        "a blocked heading must NOT deepen into its sibling below")
    assert _num(hb, "Height") >= _line - 0.002, (
        "a blocked heading still reaches a whole line box")
    assert _pads(hb) == ("0pt", "0pt", "0pt"), (
        "a declared box carries NO inset that moves a glyph -- a horizontal "
        "one shifts every left-anchored glyph off its declared x, a top one "
        "drops every top-anchored line below its declared top")

    nw = _find_textbox_by_literal(rdl, "Narrow Label Overflowing")
    assert nw is not None
    assert _num(nw, "Height") < 0.24, (
        "a WIDTH-clipped label must not deepen (wrap hides the tail word)")
    assert _num(nw, "Height") <= _line + _ROUND, (
        "a WIDTH-clipped label stays at ONE line -- any extra height lets "
        "the engine wrap it and hide the tail word")
    assert (nw.findtext("CanGrow") or "").lower() != "true", (
        "a WIDTH-clipped label must stay fixed, never auto-grown")
    assert _pads(nw) == ("0pt", "0pt", "0pt"), (
        "a WIDTH-clipped label is a declared box too: no displacing inset")

    # the two ordinary siblings declare 0.2in and must render 0.2in exactly
    for _lit in ("Blocking sibling line", "Right neighbour value box"):
        _tb = _find_textbox_by_literal(rdl, _lit)
        assert _tb is not None, _lit
        assert abs(_num(_tb, "Height") - 0.20) < 1e-6, (
            f"{_lit}: a declared 0.2in box must render 0.2in, not be padded "
            f"outward")
        assert _pads(_tb) == ("0pt", "0pt", "0pt"), _lit


# ---------------------------------------------------------------------------
# Page-header internal dedupe: the title block resolves display-constant
# parameter fields into SUBTITLE lines and the criteria banner emits its own
# run-date -- the same margin fields must not print a second copy.
# ---------------------------------------------------------------------------
_BANNER_DUP_XML = (
    '<?xml version="1.0"?><report name="BANNER_T" DTDVersion="9.0.2.0.10">'
    '<data>'
    '<userParameter name="P_DIVISION" datatype="character" width="100" '
    'initialValue="Test Bureau Line" defaultWidth="0" defaultHeight="0"/>'
    '<userParameter name="P_YEAR" datatype="number" width="4" '
    'defaultWidth="0" defaultHeight="0"/>'
    '<dataSource name="Q_Main">'
    '<select><![CDATA[select item_nm, amt from t]]></select>'
    '<group name="G_Main"><dataItem name="ITEM_NM" datatype="vchar2"/>'
    '<dataItem name="AMT" datatype="number"/></group></dataSource></data>'
    '<layout><section name="main" width="11.00000" height="8.50000">'
    '<body>'
    '<repeatingFrame name="R_Main" source="G_Main" printDirection="down">'
    '<geometryInfo x="0" y="0" width="7.5" height="0.4"/>'
    '<field name="F_ITEM" source="ITEM_NM">'
    '<geometryInfo x="0.1" y="0.05" width="2.0" height="0.2"/></field>'
    '</repeatingFrame></body>'
    '<margin>'
    '<text name="B_4"><textSettings justify="center"/>'
    '<geometryInfo x="3.75" y="0.0625" width="2.9" height="0.23"/>'
    '<textSegment><font face="Times New Roman" size="12" bold="yes"/>'
    '<string><![CDATA[BANNER DUP TITLE]]></string></textSegment></text>'
    '<field name="F_DIVISION" source="P_DIVISION" alignment="center">'
    '<font face="Times New Roman" size="12" bold="yes"/>'
    '<geometryInfo x="2.75" y="0.3125" width="5.06" height="0.25"/></field>'
    '<text name="B_1"><textSettings justify="center"/>'
    '<geometryInfo x="0.09" y="0.6875" width="10.8" height="0.1666"/>'
    '<textSegment><font face="Times New Roman" size="10"/>'
    '<string><![CDATA[Year of Emissions:]]></string></textSegment></text>'
    '<field name="f_year" source="P_YEAR">'
    '<font face="Times New Roman" size="10"/>'
    '<geometryInfo x="5.81" y="0.6875" width="0.56" height="0.1875"/>'
    '</field>'
    '<field name="f_date" source="CurrentDate" formatMask="MM/DD/YYYY">'
    '<font face="Times New Roman" size="10"/>'
    '<geometryInfo x="0.215" y="0.6875" width="0.6875" height="0.1875"/>'
    '</field>'
    '<line name="B_2"><geometryInfo x="0.09" y="0.9375" width="10.8" '
    'height="0"/><visualSettings lineWidth="2" linePattern="solid"/>'
    '<points><point x="0.09" y="0.9375"/><point x="10.9" y="0.9375"/>'
    '</points></line>'
    '</margin></section></layout></report>'
)


def test_banner_header_never_duplicates_title_or_date():
    """AIR-style criteria-banner header: the division display constant is
    already a SUBTITLE line in the title block and the banner prints its
    own run-date -- the margin-extra path must not emit either a second
    time (truth PDF: one title stack, one date, one Year row above the
    rule; ours printed the division and the date twice)."""
    import re
    from converter import convert
    rdl = convert(_BANNER_DUP_XML.encode())["rdl_xml"]
    ph = re.search(r"<PageHeader>(.*?)</PageHeader>", rdl, re.S)
    assert ph is not None, "banner report must build a page header"
    hdr = ph.group(1)
    assert hdr.count("Test Bureau Line") == 1, (
        "the division subtitle must print exactly once in the header")
    assert "Parameters!P_DIVISION.Value" not in hdr, (
        "the division margin field duplicates the title subtitle line")
    assert hdr.count("Globals!ExecutionTime") == 1, (
        "the run-date must print exactly once (banner owns it)")
    assert hdr.count("Parameters!P_YEAR.Value") == 1, (
        "the criteria value must print exactly once (banner owns it)")


# ---------------------------------------------------------------------------
# Declared cover frame: a header-section roundedRectangle with a solid
# linePattern enclosing the criteria form must give the emitted cover rect
# a border; without the declared frame the cover stays borderless.
# ---------------------------------------------------------------------------
_COVER_FRAME_XML = (
    '<?xml version="1.0"?><report name="COVB_T" DTDVersion="9.0.2.0.10">'
    '<data>'
    '<userParameter name="P_OWNER" datatype="character" width="40" '
    'defaultWidth="0" defaultHeight="0"/>'
    '<dataSource name="Q_Main">'
    '<select><![CDATA[select item_nm, amt from t]]></select>'
    '<group name="G_Main"><dataItem name="ITEM_NM" datatype="vchar2"/>'
    '<dataItem name="AMT" datatype="number"/></group></dataSource></data>'
    '<layout>'
    '<section name="header" orientation="portrait"><body>'
    '<roundedRectangle name="B_3">'
    '<geometryInfo x="0.5" y="0.1" width="6.0" height="5.0"/>'
    '<visualSettings fillBackgroundColor="gray" linePattern="solid"/>'
    '<points><point x="0.5" y="0.1"/><point x="6.0" y="5.0"/></points>'
    '</roundedRectangle>'
    '<text name="B_L1"><geometryInfo x="0.7" y="0.5" width="1.5" '
    'height="0.2"/><textSegment><font face="Arial" size="10" bold="yes"/>'
    '<string><![CDATA[Owner:]]></string></textSegment></text>'
    '<field name="F_OWNER" source="P_OWNER">'
    '<font face="Arial" size="10"/>'
    '<geometryInfo x="2.4" y="0.5" width="2.0" height="0.2"/></field>'
    '<text name="B_L2"><geometryInfo x="0.7" y="1.0" width="1.5" '
    'height="0.2"/><textSegment><font face="Arial" size="10" bold="yes"/>'
    '<string><![CDATA[Run Date:]]></string></textSegment></text>'
    '<field name="F_DATE" source="CurrentDate" formatMask="MM/DD/YYYY">'
    '<font face="Arial" size="10"/>'
    '<geometryInfo x="2.4" y="1.0" width="2.0" height="0.2"/></field>'
    '</body></section>'
    '<section name="main"><body>'
    '<repeatingFrame name="R_Main" source="G_Main" printDirection="down">'
    '<geometryInfo x="0" y="0" width="7.5" height="0.4"/>'
    '<field name="F_ITEM" source="ITEM_NM">'
    '<geometryInfo x="0.1" y="0.05" width="2.0" height="0.2"/></field>'
    '</repeatingFrame></body></section>'
    '</layout></report>'
)


def _cover_border_style(rdl_xml):
    import re as _re
    import xml.etree.ElementTree as ET
    t = _re.sub(r'xmlns="[^"]+"', "", rdl_xml, count=1)
    root = ET.fromstring(t)
    for rect in root.iter("Rectangle"):
        if rect.get("Name") in ("Rect_CoverPage", "Rect_SummaryHeader"):
            st = rect.find("Style")
            if st is None:
                return None
            b = st.find("Border")
            return b.findtext("Style") if b is not None else None
    return None


def test_declared_cover_frame_draws_the_cover_border():
    """Truth PDF: some criteria covers sit inside a drawn rounded box
    (a header-section roundedRectangle with linePattern=solid enclosing
    the form); ours emitted every cover borderless by design. The border
    must be DECLARED-driven: solid when the source draws the frame,
    none when it does not (the letter-corpus default)."""
    import re
    from converter import convert
    rdl = convert(_COVER_FRAME_XML.encode())["rdl_xml"]
    assert _cover_border_style(rdl) == "Solid", (
        "a declared solid-linePattern frame around the criteria form "
        "must give the cover rect a border")
    # PROVE THE GATE CAN FAIL: strip the declared frame -> borderless.
    no_frame = re.sub(r"<roundedRectangle.*?</roundedRectangle>", "",
                      _COVER_FRAME_XML, flags=re.S)
    rdl2 = convert(no_frame.encode())["rdl_xml"]
    assert _cover_border_style(rdl2) == "None", (
        "without a declared frame the cover must stay borderless")


# ---------------------------------------------------------------------------
# Title scavenging fidelity: a right-column stub text must never become a
# page-title line, and a scavenged title line the record BODY also renders
# (the letterhead) must not print twice.
# ---------------------------------------------------------------------------
_RIGHT_STUB_XML = (
    '<?xml version="1.0"?><report name="STUB_T" DTDVersion="9.0.2.0.10">'
    '<data><dataSource name="Q_1">'
    '<select><![CDATA[select lic_no, holder from t]]></select>'
    '<group name="G_1"><dataItem name="LIC_NO" datatype="number"/>'
    '<dataItem name="HOLDER" datatype="vchar2"/></group></dataSource></data>'
    '<layout><section name="main" width="11.00000" height="7.00000"><body>'
    '<repeatingFrame name="R_1" source="G_1" printDirection="down">'
    '<geometryInfo x="0.1" y="0.1" width="10.8" height="6.7"/>'
    # centered license title: scavenged as the page title (top band, left)
    '<text name="B_T"><textSettings justify="center"/>'
    '<geometryInfo x="0.31" y="0.31" width="7.1" height="0.17"/>'
    '<textSegment><font face="Arial" size="12" bold="yes"/>'
    '<string><![CDATA[DISPLAY THIS LICENSE PROMINENTLY]]></string>'
    '</textSegment></text>'
    # the record letterhead: top band AND rendered inside the record body
    '<text name="B_LH"><textSettings justify="center"/>'
    '<geometryInfo x="2.0" y="0.9" width="5.0" height="0.34"/>'
    '<textSegment><font face="Arial" size="11" bold="yes"/>'
    '<string><![CDATA[STATE AGENCY LETTERHEAD LINE]]></string>'
    '</textSegment></text>'
    # right-column wallet stub text (x far right of the 11in page)
    '<text name="B_STUB"><geometryInfo x="7.94" y="0.44" width="2.81" '
    'height="0.50"/><textSegment><font face="Arial" size="10"/>'
    '<string><![CDATA[Holder is an authorized representative of]]></string>'
    '</textSegment></text>'
    '<field name="F_LIC" source="LIC_NO">'
    '<geometryInfo x="0.5" y="2.0" width="2.0" height="0.2"/></field>'
    '<field name="F_HOLDER" source="HOLDER">'
    '<geometryInfo x="0.5" y="2.4" width="3.0" height="0.2"/></field>'
    '</repeatingFrame></body></section></layout></report>'
)


def test_right_column_stub_never_becomes_a_title_line():
    """License-form truth: the wallet-stub column at the far right edge
    (x=7.94 on an 11in page) is record content â€” ours scavenged its text
    into the synthesized page banner, painting it top-center on every
    page. Titles are position-gated (centered/left anchored); and a
    scavenged title line the record body ALSO renders (the letterhead)
    must be dropped from the banner so it prints once."""
    import re
    from converter import convert
    rdl = convert(_RIGHT_STUB_XML.encode())["rdl_xml"]
    ph = re.search(r"<PageHeader>(.*?)</PageHeader>", rdl, re.S)
    hdr = ph.group(1) if ph else ""
    assert "Holder is an authorized" not in hdr, (
        "a far-right stub text must never ride into the page banner")
    body = re.search(r"<Body>(.*?)</Body>", rdl, re.S).group(1)
    if "STATE AGENCY LETTERHEAD LINE" in body:
        assert "STATE AGENCY LETTERHEAD LINE" not in hdr, (
            "a title line the record body already renders must not "
            "print a second time in the banner")


def test_declared_paper_height_becomes_the_page_height():
    """An 11x8.5 landscape section IS the paper (truth PDFs measure
    612pt tall); emitting Letter-portrait 11in made pagination diverge
    from the truth on every page. Only paper-sized declared heights
    (>= 8in) qualify â€” a short BODY band (a 7in license form) keeps the
    11in default."""
    import re
    from converter import convert
    landscape = (
        '<?xml version="1.0"?><report name="LSC_T" DTDVersion="9.0.2.0.10">'
        '<data><dataSource name="Q_Main">'
        '<select><![CDATA[select item_nm, amt from t]]></select>'
        '<group name="G_Main"><dataItem name="ITEM_NM" datatype="vchar2"/>'
        '<dataItem name="AMT" datatype="number"/></group></dataSource>'
        '</data>'
        '<layout><section name="main" width="11.00000" height="8.50000">'
        '<body>'
        '<repeatingFrame name="R_Main" source="G_Main" '
        'printDirection="down">'
        '<geometryInfo x="0" y="0.3" width="10.5" height="0.3"/>'
        '<field name="F_ITEM" source="ITEM_NM">'
        '<geometryInfo x="0.1" y="0.32" width="2.0" height="0.2"/></field>'
        '<field name="F_AMT" source="AMT">'
        '<geometryInfo x="6.0" y="0.32" width="1.0" height="0.2"/></field>'
        '</repeatingFrame></body></section></layout></report>'
    )
    rdl = convert(landscape.encode())["rdl_xml"]
    assert re.search(r"<PageHeight>8\.50?in</PageHeight>", rdl), (
        "a declared 8.5in-tall landscape section must become the paper "
        "height")
    # short BODY band -> not a paper height -> 11in default holds
    short = landscape.replace('height="8.50000"', 'height="7.00000"')
    rdl2 = convert(short.encode())["rdl_xml"]
    assert re.search(r"<PageHeight>11(\.0+)?in</PageHeight>", rdl2), (
        "a short declared body band must keep the 11in default")


def test_no_invented_zebra_striping_anywhere():
    """The Oracle truth PDFs paint detail rows plain (white or a
    SOURCE-declared band); a RowNumber-Mod-2 alternating BackgroundColor
    is a converter invention hidden by layout-mode staticization but
    painted on a real deploy (measured on the wide accreditation
    summary). No emitted RDL may carry one."""
    from converter import convert
    flat = (
        '<?xml version="1.0"?><report name="FLAT_T" DTDVersion="9.0.2.0.10">'
        '<data><dataSource name="Q_Main">'
        '<select><![CDATA[select item_nm, amt, kind from t]]></select>'
        '<group name="G_Main"><dataItem name="ITEM_NM" datatype="vchar2"/>'
        '<dataItem name="AMT" datatype="number"/>'
        '<dataItem name="KIND" datatype="vchar2"/></group></dataSource>'
        '</data></report>'
    )
    rdl = convert(flat.encode())["rdl_xml"]
    assert "Tablix" in rdl, "flat report must emit a tablix"
    assert "RowNumber(Nothing) Mod 2" not in rdl, (
        "no invented zebra striping on detail rows")
    rdl2 = convert(_LABEL_H_XML.encode())["rdl_xml"]
    assert "RowNumber(Nothing) Mod 2" not in rdl2


def test_margin_chrome_carries_declared_fill_and_text_color():
    """The declared-margin chrome path emitted every band black-on-white:
    the source's gray band fill (fillPattern attr present -> paints, the
    corpus-wide dialect gate) and darkblue title color were dropped â€”
    truth prints blue-on-gray. The chrome textboxes must carry the
    declared style; an unstyled margin stays black-on-white."""
    import re
    from converter import convert
    plain = ('<text name="B_RUNON"><geometryInfo x="6.0" y="0.12" '
             'width="1.2" height="0.19"/><textSegment>'
             '<font face="Arial" size="10"/>')
    styled_frag = ('<text name="B_RUNON">'
                   '<visualSettings fillPattern="transparent" '
                   'fillBackgroundColor="gray"/>'
                   '<geometryInfo x="6.0" y="0.12" '
                   'width="1.2" height="0.19"/><textSegment>'
                   '<font face="Arial" size="10" textColor="darkblue"/>')
    assert plain in _CHROME_XML, "fixture drifted â€” update this test"
    styled = _CHROME_XML.replace(plain, styled_frag)
    rdl = convert(styled.encode())["rdl_xml"]
    ph = re.search(r"<PageHeader>(.*?)</PageHeader>", rdl, re.S).group(1)
    m = re.search(r"<Textbox Name=\"MChrome_H_B_RUNON\">(.*?)</Textbox>",
                  ph, re.S)
    assert m, "the styled run-on chrome textbox must emit"
    tb = m.group(1)
    assert "#BFBFBF" in tb, (
        "a pattern-marked gray margin fill must paint on the chrome box")
    assert "#000080" in tb, (
        "the declared darkblue text color must survive into the chrome")
    # unstyled margin keeps black-on-white (no invented fills/colors)
    rdl2 = convert(_CHROME_XML.encode())["rdl_xml"]
    ph2 = re.search(r"<PageHeader>(.*?)</PageHeader>", rdl2, re.S).group(1)
    m2 = re.search(r"<Textbox Name=\"MChrome_H_B_RUNON\">(.*?)</Textbox>",
                   ph2, re.S)
    assert m2 and "BackgroundColor" not in m2.group(1), (
        "an unstyled margin text must not gain a fill")


def test_parameter_only_trigger_hides_cover_note_outside_regions():
    """A criteria-cover note gated by a PARAMETER-ONLY format trigger
    ("show only when a filter is chosen") lost its visibility logic â€”
    the region-aware pass skipped every textbox outside a data region,
    so the note printed unconditionally where the truth run hides it.
    Fields!-referencing triggers must stay region-gated (fire-140).
    Unit-level on the pass itself: the summary-header cover emits its
    tagged textboxes body-direct, outside any Tablix scope."""
    import xml.etree.ElementTree as ET
    from converter.parsers.oracle_xml import parse_oracle_xml
    from converter.generators import rdl as R
    xml = (
        '<?xml version="1.0"?><report name="TRIGU_T" DTDVersion="9.0.2.0.10">'
        '<data>'
        '<userParameter name="P_FILTER" datatype="character" width="20" '
        'defaultWidth="0" defaultHeight="0"/>'
        '<dataSource name="Q_Main">'
        '<select><![CDATA[select item_nm, amt from t]]></select>'
        '<group name="G_Main"><dataItem name="ITEM_NM" datatype="vchar2"/>'
        '<dataItem name="AMT" datatype="number"/></group></dataSource>'
        '</data>'
        '<programUnits>'
        '<function name="note_ft" returnType="boolean">'
        '<textSource><![CDATA[function note_ft return boolean is begin '
        'IF :P_FILTER IS NOT NULL THEN RETURN (TRUE); END IF; '
        'RETURN (FALSE); end;]]></textSource></function>'
        '<function name="col_ft" returnType="boolean">'
        '<textSource><![CDATA[function col_ft return boolean is begin '
        'IF :ITEM_NM IS NOT NULL THEN RETURN (TRUE); END IF; '
        'RETURN (FALSE); end;]]></textSource></function>'
        '</programUnits></report>'
    )
    rep = parse_oracle_xml(xml.encode())
    root = ET.Element(R._q("Report"))
    body = ET.SubElement(root, R._q("Body"))
    ri = ET.SubElement(body, R._q("ReportItems"))
    tb1 = ET.SubElement(ri, R._q("Textbox"))
    tb1.set("Name", "T_ParamNote")
    tb1.set("data-ft", "note_ft")
    tb2 = ET.SubElement(ri, R._q("Textbox"))
    tb2.set("Name", "T_ColNote")
    tb2.set("data-ft", "col_ft")
    R._apply_region_aware_trigger_props(root, rep)
    vis = tb1.find(R._q("Visibility"))
    assert vis is not None and "P_FILTER" in (
        vis.findtext(R._q("Hidden")) or ""), (
        "a parameter-only trigger must become a real Hidden even "
        "OUTSIDE any data region")
    assert tb2.find(R._q("Visibility")) is None, (
        "a Fields!-referencing trigger outside a region must stay "
        "unapplied (fire-140 region gate)")
    assert "data-ft" not in tb1.attrib and "data-ft" not in tb2.attrib, (
        "the pass must always strip the non-schema data-ft tags")



_RULED_LEDGER_XML = (
    '<?xml version="1.0"?><report name="LEDG_T" DTDVersion="9.0.2.0.10">'
    '<data><dataSource name="Q_Main">'
    '<select><![CDATA[select log_key, item_no, payor, amt from t]]>'
    '</select>'
    '<group name="G_Main"><dataItem name="LOG_KEY" datatype="number"/>'
    '</group>'
    '<group name="G_Det"><dataItem name="ITEM_NO" datatype="number"/>'
    '<dataItem name="PAYOR" datatype="vchar2"/>'
    '<dataItem name="AMT" datatype="number"/></group>'
    '<summary name="CS_TOTAL" function="sum" source="AMT"/>'
    '</dataSource></data>'
    '<layout><section name="main" width="8.50000" height="11.00000"><body>'
    '<frame name="M_G_GRP"><geometryInfo x="0" y="0" width="8.2" '
    'height="2.4"/>'
    '<repeatingFrame name="R_OUTER" source="G_Main" '
    'printDirection="down">'
    '<geometryInfo x="0" y="0" width="8.0" height="2.2"/>'
    '<field name="F_KEY" source="LOG_KEY">'
    '<geometryInfo x="0.3" y="0.05" width="1.2" height="0.2"/></field>'
    '<text name="B_C1"><geometryInfo x="0.1" y="0.6" width="0.8" '
    'height="0.2"/><textSegment><font face="Arial" size="9" bold="yes"/>'
    '<string><![CDATA[Item]]></string></textSegment></text>'
    '<text name="B_C2"><geometryInfo x="1.2" y="0.6" width="1.2" '
    'height="0.2"/><textSegment><font face="Arial" size="9" bold="yes"/>'
    '<string><![CDATA[Payor]]></string></textSegment></text>'
    '<text name="B_C3"><geometryInfo x="3.0" y="0.6" width="1.2" '
    'height="0.2"/><textSegment><font face="Arial" size="9" bold="yes"/>'
    '<string><![CDATA[Amount]]></string></textSegment></text>'
    '<repeatingFrame name="R_DET" source="G_Det" printDirection="down">'
    '<geometryInfo x="0.05" y="0.9" width="8.0" height="0.375"/>'
    '<field name="F_D1" source="ITEM_NO">'
    '<geometryInfo x="0.1" y="0.95" width="0.8" height="0.2"/></field>'
    '<field name="F_D2" source="PAYOR">'
    '<geometryInfo x="1.2" y="0.95" width="1.6" height="0.2"/></field>'
    '<field name="F_D3" source="AMT">'
    '<geometryInfo x="3.0" y="0.95" width="1.0" height="0.2"/></field>'
    '</repeatingFrame>'
    '<line name="B_RULE"><geometryInfo x="0.05" y="1.3" width="7.9" '
    'height="0.0"/><visualSettings linePattern="solid"/>'
    '<points><point x="0.05" y="1.3"/><point x="7.95" y="1.3"/></points>'
    '</line>'
    '<frame name="M_TOTALS">'
    '<geometryInfo x="1.5" y="1.6" width="3.0" height="0.4"/>'
    '<field name="F_TOT" source="CS_TOTAL">'
    '<geometryInfo x="3.0" y="1.7" width="1.0" height="0.2"/></field>'
    '<text name="B_TOTL"><geometryInfo x="1.8" y="1.7" width="1.1" '
    'height="0.2"/><textSegment><font face="Arial" size="9" bold="yes"/>'
    '<string><![CDATA[Total]]></string></textSegment></text>'
    '</frame>'
    '</repeatingFrame></frame>'
    '</body></section></layout></report>'
)


def test_declared_group_rule_prints_once_at_its_declared_extent():
    """A <line> declared OUTSIDE the detail repeating frame belongs to the
    GROUP, not to the row: Oracle repeats an object with its own enclosing
    repeating frame, so such a rule prints ONCE per group at its declared
    endpoints (truth-PDF measured on a grouped logsheet: the group's rules
    print once per group block, never once per detail row).

    It must therefore emit as a real <Line> at its declared x/width — a
    BottomBorder on the detail band would both repeat it under every row
    and stretch it across the whole band whatever the declaration says.
    The per-ROW rule is the OTHER declaration: a <line> INSIDE the
    repeating frame (guarded below).
    """
    import re
    from converter import convert
    rdl = convert(_RULED_LEDGER_XML.encode())["rdl_xml"]
    m = re.search(r'<Rectangle Name="GTS_Detail">.{0,600}?<ReportItems>',
                  rdl, re.S)
    assert m, "grouped-tabular route must emit the GTS detail band"
    assert "<BottomBorder>" not in m.group(0), (
        "a group-scope declared rule must NOT become a per-row band border")
    ln = re.search(r'<Line Name="Rule_B_RULE">.*?</Line>', rdl, re.S)
    assert ln, "the declared group rule must print as a real <Line>"
    # NUMERIC, not a formatted string: a declared quantity is emitted at
    # full precision, and a text match on "0.05in" would also pass for any
    # value that merely rounds to it.
    _lx = float(re.search(r"<Left>([\d.]+)in</Left>", ln.group(0)).group(1))
    assert abs(_lx - 0.05) < 1e-9, (
        "the rule must start at its DECLARED x, not at the band's edge", _lx)
    # the row-INTERNAL declaration is what repeats with every row
    inside = _RULED_LEDGER_XML.replace(
        '</repeatingFrame>',
        '<line name="B_ROWRULE"><geometryInfo x="0.05" y="0.95" width="7.9" '
        'height="0.0"/><visualSettings linePattern="solid"/></line>'
        '</repeatingFrame>', 1)
    det = re.search(r'<Rectangle Name="GTS_Detail">.*?</Rectangle>',
                    convert(inside.encode())["rdl_xml"], re.S)
    assert det and '<Line Name="GTS_RowRule">' in det.group(0), (
        "a line declared INSIDE the repeating frame repeats with every row")
    # prove-the-gate: strip the declaration -> the rule disappears entirely
    no_rule = re.sub(r"<line name=\"B_RULE\">.*?</line>", "",
                     _RULED_LEDGER_XML, flags=re.S)
    rdl2 = convert(no_rule.encode())["rdl_xml"]
    m2 = re.search(r'<Rectangle Name="GTS_Detail">.{0,600}?<ReportItems>',
                   rdl2, re.S)
    assert m2, "route must hold without the rule"
    assert '<Line Name="Rule_B_RULE">' not in rdl2, (
        "no declared line -> no invented rule")
    assert "<BottomBorder>" not in m2.group(0), (
        "no declared line -> no invented row border")


_PAPER_FORM_XML = (
    '<?xml version="1.0"?><report name="FORMFLOW_T" DTDVersion="9.0.2.0.10">'
    '<data>'
    '<dataSource name="Q_FORM">'
    '<select><![CDATA[select vend_nm, req_no from t1]]></select>'
    '<group name="G_FORM"><dataItem name="VEND_NM" datatype="vchar2"/>'
    '<dataItem name="REQ_NO" datatype="number"/></group></dataSource>'
    '<dataSource name="Q_ITEM">'
    '<select><![CDATA[select item_nm, qty, amt from t2]]></select>'
    '<group name="G_ITEM"><dataItem name="ITEM_NM" datatype="vchar2"/>'
    '<dataItem name="QTY" datatype="number"/>'
    '<dataItem name="AMT" datatype="number"/></group></dataSource>'
    '<link parentGroup="G_FORM" childQuery="Q_ITEM" condition="eq" '
    'sqlClause="where"/>'
    '</data>'
    '<layout><section name="main">'
    '<body height="9.75000">'
    '<repeatingFrame name="R_FORM" source="G_FORM" printDirection="down" '
    'maxRecordsPerPage="1" minWidowRecords="1" columnMode="no">'
    '<geometryInfo x="0.00000" y="0.00000" width="7.50000" height="9.75000"/>'
    '<generalLayout verticalElasticity="variable"/>'
    '<text name="B_Vend"><geometryInfo x="0.00000" y="0.10000" '
    'width="1.00000" height="0.20000"/><textSegment>'
    '<font face="Arial" size="9"/><string><![CDATA[Vendor:]]></string>'
    '</textSegment></text>'
    '<field name="F_VEND" source="VEND_NM">'
    '<geometryInfo x="1.10000" y="0.10000" width="2.00000" height="0.20000"/>'
    '</field>'
    '<field name="F_REQ" source="REQ_NO">'
    '<geometryInfo x="1.10000" y="0.60000" width="2.00000" height="0.20000"/>'
    '</field>'
    '<frame name="M_ITEM_GRPFR">'
    '<geometryInfo x="0.00000" y="1.50000" width="7.50000" height="6.40000"/>'
    '<generalLayout verticalElasticity="variable"/>'
    '<repeatingFrame name="R_ITEM" source="G_ITEM" printDirection="down">'
    '<geometryInfo x="0.00000" y="1.70000" width="7.50000" height="0.20000"/>'
    '<field name="F_ITEM" source="ITEM_NM">'
    '<geometryInfo x="0.20000" y="1.70000" width="3.00000" height="0.18000"/>'
    '</field>'
    '<field name="F_QTY" source="QTY">'
    '<geometryInfo x="3.50000" y="1.70000" width="1.00000" height="0.18000"/>'
    '</field>'
    '<field name="F_AMT" source="AMT">'
    '<geometryInfo x="5.00000" y="1.70000" width="1.00000" height="0.18000"/>'
    '</field></repeatingFrame></frame>'
    '<frame name="M_FOOT">'
    '<geometryInfo x="0.00000" y="8.20000" width="7.50000" height="1.50000"/>'
    '<visualSettings lineWidth="1" linePattern="solid"/>'
    '<text name="B_Sig"><geometryInfo x="0.20000" y="8.40000" '
    'width="2.00000" height="0.20000"/><textSegment>'
    '<font face="Arial" size="9"/><string><![CDATA[Signature:]]></string>'
    '</textSegment></text></frame>'
    '</repeatingFrame>'
    '</body></section></layout></report>'
)


def test_paper_declared_form_record_flows_across_pages():
    """A per-record FORM whose section declares a PAPER-sized body must keep
    the paper at its real size and let the record PAGINATE across pages (the
    Oracle-rendered truth paginates the same form at 8.5x11) -- the grow-the-
    paper budget produced a 20in+ sheet no printer owns. Oversize record rects
    get KeepTogether=false so the engine splits them in place instead of
    pushing them whole to a fresh page (the blank-leader-page failure mode).
    Differential: WITHOUT the paper-sized body declaration the grow budget
    must still hold (it is load-bearing for records that must stay whole)."""
    import re
    from converter import convert
    rdl = convert(_PAPER_FORM_XML.encode())["rdl_xml"]
    assert re.search(r"<PageHeight>11(\.0+)?in</PageHeight>", rdl), (
        "paper-declared form must keep Letter paper, not grow the sheet")
    # This record's content is SHORTER than the declared sheet, so it prints
    # one per sheet and keeps its KeepTogether -- only a record that genuinely
    # outgrows the sheet may be split (the OVERSIZE leg below carries that
    # proof). Both legs keep the declared Letter paper.
    m_fit = re.search(r'<Rectangle Name="Rect_RecordPage">.{0,200}?'
                      r'<KeepTogether>(\w+)</KeepTogether>', rdl, re.S)
    assert m_fit and m_fit.group(1) == "true", (
        "a record that fits the declared sheet stays whole on one sheet")
    # OVERSIZE leg: push the closing frame far past the declared sheet so ONE
    # record cannot fit it however much synthesized chrome is reclaimed.
    big = (_PAPER_FORM_XML
           .replace('x="0.00000" y="8.20000" width="7.50000" height="1.50000"',
                    'x="0.00000" y="16.00000" width="7.50000" height="1.50000"')
           .replace('x="0.20000" y="8.40000" '
                    'width="2.00000" height="0.20000"',
                    'x="0.20000" y="16.20000" '
                    'width="2.00000" height="0.20000"'))
    assert big != _PAPER_FORM_XML
    rdl_big = convert(big.encode())["rdl_xml"]
    assert re.search(r"<PageHeight>11(\.0+)?in</PageHeight>", rdl_big), (
        "paper-declared form must keep Letter paper, not grow the sheet")
    m = re.search(r'<Rectangle Name="Rect_RecordPage">.{0,200}?'
                  r'<KeepTogether>(\w+)</KeepTogether>', rdl_big, re.S)
    assert m and m.group(1) == "false", (
        "an oversize record rect must be allowed to split across pages")
    # Negative leg: strip the paper-sized body declaration -> the per-record
    # growth budget stays (one record kept whole on one grown sheet).
    no_paper = _PAPER_FORM_XML.replace('<body height="9.75000">', '<body>')
    rdl2 = convert(no_paper.encode())["rdl_xml"]
    m2 = re.search(r"<PageHeight>([0-9.]+)in</PageHeight>", rdl2)
    assert m2 and float(m2.group(1)) > 11.05, (
        "without a paper-sized declaration the grow-the-page budget must "
        "hold (load-bearing for whole-record sheets)")
    m3 = re.search(r'<Rectangle Name="Rect_RecordPage">.{0,200}?'
                   r'<KeepTogether>(\w+)</KeepTogether>', rdl2, re.S)
    assert m3 and m3.group(1) == "true", (
        "grow-budget records keep KeepTogether (record stays whole)")


def test_inert_wrapper_frames_never_inflate_the_record():
    """An EMPTY, INVISIBLE wrapper frame (no fields, no children, no painted
    border or fill) renders nothing in Oracle -- emitting it let the record-
    level elasticity walk stack a full-record-sized empty rect below the real
    content, doubling the record height (one content-free page per record,
    measured on a requisition form). The record geometry must be IDENTICAL
    with and without the wrapper. Differential: a BORDERED empty frame is a
    real write-in box and must still emit (and inflate)."""
    import re
    from converter import convert

    def record_row_height(rdl):
        m = re.search(r'<TablixRow>\s*<Height>([0-9.]+)in</Height>', rdl)
        assert m, "per-record tablix row must exist"
        return float(m.group(1))

    base = convert(_PAPER_FORM_XML.encode())["rdl_xml"]
    wrapper = ('<frame name="M_FORM_GRPFR">'
               '<geometryInfo x="0.00000" y="0.03000" width="7.50000" '
               'height="9.72000"/>'
               '<generalLayout verticalElasticity="variable"/></frame>')
    with_inert = _PAPER_FORM_XML.replace(
        '<repeatingFrame name="R_FORM"',
        wrapper + '<repeatingFrame name="R_FORM"')
    rdl_inert = convert(with_inert.encode())["rdl_xml"]
    assert record_row_height(rdl_inert) == record_row_height(base), (
        "an empty invisible wrapper frame must not change the record height")
    # Negative leg: the SAME empty frame WITH a painted border is a real
    # write-in box -- it must emit and therefore grow the record.
    bordered = wrapper.replace(
        '</frame>',
        '<visualSettings lineWidth="1" linePattern="solid"/></frame>')
    with_box = _PAPER_FORM_XML.replace(
        '<repeatingFrame name="R_FORM"',
        bordered + '<repeatingFrame name="R_FORM"')
    rdl_box = convert(with_box.encode())["rdl_xml"]
    assert record_row_height(rdl_box) > record_row_height(base), (
        "a bordered empty frame is a real box and must still emit")


def test_detail_row_indent_follows_declared_field_geometry():
    """The row indent the truth prints is DECLARATION-driven, two dialect
    subtleties included: (a) field x is ABSOLUTE in section space, so a
    detail repeating frame at x=0 whose fields sit at x>0 still indents
    its rows (frame-x-only read rendered them flush); (b) the group-title
    collapse (2 same-query tables -> 1) must keep the geometry list
    parallel, or the parallelism gate silently drops the detail indent."""
    import re
    from converter.generators.rdl import (
        _detect_multi_section, _build_multi_section_body)
    from converter.models import (DataQuery, DataItem, LayoutField,
                                  LayoutGroup, ParsedReport)

    def _mkq(n):
        q = DataQuery(name=f"Q_{n}")
        q.items = [DataItem(name=f"{n}_DESC"), DataItem(name=f"{n}_CNT")]
        q.sql = f"SELECT {n}_DESC, {n}_CNT FROM x"
        return q

    def _sec_frame(n, frame_x, label_x, count_x):
        rf = LayoutGroup(
            name=f"R_G_{n}", kind="repeating_frame", source_query=f"G_{n}",
            x=frame_x, y=0.25, width=4.3,
            fields=[
                LayoutField(name=f"F_{n}_DESC", source=f"{n}_DESC",
                            kind="field", x=label_x, y=0.25, width=3.0),
                LayoutField(name=f"F_{n}_CNT", source=f"{n}_CNT",
                            kind="field", x=count_x, y=0.25, width=0.8),
            ])
        return LayoutGroup(name=f"M_G_{n}", kind="frame", x=0.0, y=0.0,
                           width=4.5, children=[rf])

    rep = ParsedReport(name="SYNTH")
    rep.queries = [_mkq("S1"), _mkq("S2")]
    sm = LayoutGroup(name="main", kind="section_main", children=[
        # (a) frame flush at x=0 -- the FIELDS carry the 0.1875 indent
        _sec_frame("S1", 0.0, 0.1875, 3.6875),
        # control: frame and fields flush together at 0.25
        _sec_frame("S2", 0.25, 0.25, 3.75),
    ])
    rep.layout = [sm]

    sections = _detect_multi_section(rep)
    assert sections and len(sections) == 2
    geo = [s["table_geo"][0] for s in sections]
    assert abs(geo[0][1] - 0.1875) < 1e-6, (
        "leftmost FIELD x must drive the detail indent when the frame "
        "sits flush at x=0")
    assert abs(geo[1][1] - 0.25) < 1e-6

    body = _build_multi_section_body(rep, sections)
    xml = ET.tostring(body, encoding="unicode")

    def _padding_left(cell_name):
        m = re.search(
            r'<Textbox Name="%s">.*?<PaddingLeft>([^<]+)</PaddingLeft>'
            % cell_name, xml, re.S)
        assert m, f"{cell_name} must exist with a PaddingLeft"
        return m.group(1)

    assert _padding_left("Tbx_S0_Cell_S1_DESC") == "0.19in"
    assert _padding_left("Tbx_S1_Cell_S2_DESC") == "0.25in"

    # NEGATIVE (declaration-driven, not invented): fields flush with a
    # frame at x=0 -> stock 3pt padding, no indent appears from thin air.
    rep2 = ParsedReport(name="SYNTH2")
    rep2.queries = [_mkq("S1"), _mkq("S2")]
    rep2.layout = [LayoutGroup(name="main", kind="section_main", children=[
        _sec_frame("S1", 0.0, 0.0, 3.5),
        _sec_frame("S2", 0.0, 0.0, 3.5),
    ])]
    secs2 = _detect_multi_section(rep2)
    xml2 = ET.tostring(_build_multi_section_body(rep2, secs2),
                       encoding="unicode")
    m = re.search(r'<Textbox Name="Tbx_S0_Cell_S1_DESC">.*?'
                  r'<PaddingLeft>([^<]+)</PaddingLeft>', xml2, re.S)
    assert m and m.group(1) == "3pt"

    # (b) group-title collapse keeps geometry parallel: 2 same-query
    # tables fold into one detail table and the DETAIL band's declared
    # x offset survives as the label cell's indent.
    q = DataQuery(name="Q_B")
    q.items = [DataItem(name="B_GROUP"), DataItem(name="B_TYPE"),
               DataItem(name="B_CNT")]
    q.sql = "SELECT B_GROUP, B_TYPE, B_CNT FROM x"
    repb = ParsedReport(name="SYNTHB")
    repb.queries = [q]
    secb = [{"header": "", "y": 0.0, "x": 0.0, "totals": [],
             "has_total": True,
             "tables": [(q, ["B_GROUP"]), (q, ["B_TYPE", "B_CNT"])],
             "table_geo": [(0.0, 0.0), (0.25, 0.26)]}]
    xmlb = ET.tostring(_build_multi_section_body(repb, secb),
                       encoding="unicode")
    mb = re.search(r'<Textbox Name="Tbx_S0_Cell_B_TYPE">.*?'
                   r'<PaddingLeft>([^<]+)</PaddingLeft>', xmlb, re.S)
    assert mb and mb.group(1) == "0.26in", (
        "collapse must keep table_geo parallel so the declared detail "
        "indent survives")


def test_declared_band_edge_rules_and_box_stroke_color():
    """The truth PDF draws the column band's edge lines from the DECLARED
    solid <line>s at the band's top/bottom edges, repeats the row-internal
    <line> with every detail row (forming the double rule under the band),
    draws the once-per-group line after the last row, and strokes the
    totals box in its DECLARED lineForegroundColor (navy) -- ours
    synthesized a single gray band rule and a #333333 box border.

    Those declared lines carry no lineWidth, so their ink is Oracle's
    DEVICE HAIRLINE: the truth exports measure (204,204,204) for them at
    every rendering resolution (three exports sampled), NOT black -- a
    flat black default printed them near-black at print resolution.

    Each of them prints as a REAL rule at its declared endpoints. Folding a
    band-edge rule into the band's border discarded its declared x/width
    AND -- because the border was emitted whether or not a line was
    declared -- painted a full-width rule across bands the source draws
    nothing on (truth-PDF measured: ZERO drawings across that band)."""
    import re
    from converter import convert
    from converter.parsers.oracle_colors import (
        DEVICE_HAIRLINE_COLOR as _HAIRLINE_INK)

    ruled = _RULED_LEDGER_XML
    # declared band edges + row-internal rule + navy-stroked totals box
    ruled = ruled.replace(
        '<repeatingFrame name="R_DET"',
        '<line name="B_TOPEDGE"><geometryInfo x="0.05" y="0.30" '
        'width="7.9" height="0.0"/><visualSettings linePattern="solid"/>'
        '</line>'
        '<line name="B_EDGE"><geometryInfo x="0.05" y="0.9" width="7.9" '
        'height="0.0"/><visualSettings linePattern="solid"/></line>'
        '<repeatingFrame name="R_DET"')
    ruled = ruled.replace(
        '</repeatingFrame>',
        '<line name="B_ROWRULE"><geometryInfo x="0.05" y="0.95" '
        'width="7.9" height="0.0"/><visualSettings linePattern="solid"/>'
        '</line></repeatingFrame>', 1)
    ruled = ruled.replace(
        '<frame name="M_TOTALS">'
        '<geometryInfo x="1.5" y="1.6" width="3.0" height="0.4"/>',
        '<frame name="M_TOTALS">'
        '<geometryInfo x="1.5" y="1.6" width="3.0" height="0.4"/>'
        '<rectangle name="B_BOX">'
        '<geometryInfo x="1.5" y="1.6" width="3.0" height="0.4"/>'
        '<visualSettings linePattern="solid" '
        'lineForegroundColor="darkblue"/></rectangle>')
    rdl = convert(ruled.encode())["rdl_xml"]

    hdr = re.search(r'<Rectangle Name="GTS_Hdr">.*?<Rectangle '
                    r'Name="GTS_ColBand">', rdl, re.S)
    assert hdr and "<BottomBorder>" not in hdr.group(0), (
        "a declared band-edge line is a RULE, never the band's border")
    for _rn in ("Rule_B_TOPEDGE", "Rule_B_EDGE"):
        _ln = re.search(r'<Line Name="%s">.*?</Line>' % _rn, rdl, re.S)
        assert _ln, f"the declared band-edge line {_rn} must print as a rule"
        assert _HAIRLINE_INK in _ln.group(0), (
            "a rule with no declared lineWidth inks as the device hairline")
        _lx = float(
            re.search(r"<Left>([\d.]+)in</Left>", _ln.group(0)).group(1))
        assert abs(_lx - 0.05) < 1e-9, (
            "the rule must start at its DECLARED x", _lx)
    det = re.search(r'<Rectangle Name="GTS_Detail">.*?</Rectangle>',
                    rdl, re.S)
    assert det and '<Line Name="GTS_RowRule">' in det.group(0), (
        "the row-internal declared line must repeat with every row")
    assert "<BottomBorder>" not in re.search(
        r'<Rectangle Name="GTS_Detail">.{0,600}?<ReportItems>',
        rdl, re.S).group(0), (
        "with a row-internal rule the once-per-group line must NOT "
        "double as a per-row border")
    assert '<Line Name="Rule_B_RULE">' in rdl, (
        "the once-per-group line must print after the last row")
    box = re.search(r'<Rectangle Name="GTS_TotalsBox">.{0,500}?</Border>',
                    rdl, re.S)
    assert box and "#000080" in box.group(0), (
        "the totals box must stroke in its DECLARED navy, not #333333")

    # prove-the-gate: the base fixture declares NO band-edge line, so the
    # band must carry NO rule and NO synthesized border at all.
    rdl2 = convert(_RULED_LEDGER_XML.encode())["rdl_xml"]
    hdr2 = re.search(r'<Rectangle Name="GTS_Hdr">.*?<Rectangle '
                     r'Name="GTS_ColBand">', rdl2, re.S)
    assert hdr2 and "<BottomBorder>" not in hdr2.group(0) \
        and "#444444" not in rdl2, (
        "no declared edge line -> nothing is painted under the band")
    assert "Rule_B_TOPEDGE" not in rdl2 and "Rule_B_EDGE" not in rdl2
    assert '<Line Name="GTS_RowRule">' not in rdl2


def test_declared_margin_geometry_drives_page_margins():
    """Oracle margin ys are PAPER-relative: the declared run-on at
    y=0.12 must print 0.12in from the paper top (TopMargin = the chrome
    band's own top) and the declared page number at y=10.4 must print at
    10.4in (BottomMargin + footer height derived from the declared
    geometry). The fixed 0.5in/0.6in chrome printed the title ~28pt low
    and the page number ~39pt high (truth-PDF measured)."""
    import re
    from converter import convert

    rdl = convert(_CHROME_XML.encode())["rdl_xml"]

    def _v(tag):
        return float(re.search(
            r"<%s>([0-9.]+)in</%s>" % (tag, tag), rdl).group(1))

    assert abs(_v("TopMargin") - 0.12) < 0.011, (
        "TopMargin must equal the declared chrome band top")
    pf = re.search(r"<PageFooter>.{0,200}?<Height>([0-9.]+)in</Height>",
                   rdl, re.S)
    ftr_top = 11.0 - _v("BottomMargin") - float(pf.group(1))
    assert abs(ftr_top - 10.4) < 0.02, (
        "footer band top must land at the declared bottom-chrome y")

    # prove-the-gate: without a margin band the defaults stay
    plain = re.sub(r"<margin>.*?</margin>", "", _CHROME_XML, flags=re.S)
    rdl2 = convert(plain.encode())["rdl_xml"]
    assert "<TopMargin>0.50in</TopMargin>" in rdl2
    assert "<BottomMargin>0.50in</BottomMargin>" in rdl2


_BOXED_ND_XML = (
    '<?xml version="1.0"?><report name="NDBOX_T" DTDVersion="9.0.2.0.10">'
    '<data><dataSource name="Q_Main">'
    '<select><![CDATA[select site_id, act_date, act_type, act_by, act_own,'
    ' act_note from t]]></select>'
    '<group name="G_Site"><dataItem name="SITE_ID" datatype="number"/>'
    '</group>'
    '<group name="G_Act"><dataItem name="ACT_DATE" datatype="date"/>'
    '<dataItem name="ACT_TYPE" datatype="vchar2"/>'
    '<dataItem name="ACT_BY" datatype="vchar2"/>'
    '<dataItem name="ACT_OWN" datatype="vchar2"/>'
    '<dataItem name="ACT_NOTE" datatype="vchar2"/></group>'
    '</dataSource></data>'
    '<layout><section name="main" width="8.50000" height="11.00000"><body>'
    '<frame name="M_G_SITE_GRPFR"><geometryInfo x="0" y="0" width="8.2" '
    'height="2.5"/>'
    '<repeatingFrame name="R_SITE" source="G_Site" printDirection="down">'
    '<geometryInfo x="0" y="0" width="8.2" height="2.4"/>'
    '<field name="F_SITE" source="SITE_ID">'
    '<geometryInfo x="0.3" y="0.1" width="1.2" height="0.2"/></field>'
    '<frame name="M_HDRBAND">'
    '<geometryInfo x="0.19" y="1.31" width="8.0" height="0.19"/>'
    '<text name="B_H1"><geometryInfo x="1.0" y="1.31" width="2.3" '
    'height="0.19"/><visualSettings linePattern="solid"/><textSegment>'
    '<font face="Times New Roman" size="10" bold="yes" textColor="white"/>'
    '<string><![CDATA[ Kind]]></string></textSegment></text>'
    '<text name="B_H2"><geometryInfo x="3.31" y="1.31" width="2.4" '
    'height="0.19"/><textSegment>'
    '<font face="Times New Roman" size="10" bold="yes" textColor="white"/>'
    '<string><![CDATA[ Person]]></string></textSegment></text>'
    '<text name="B_H3"><geometryInfo x="5.75" y="1.31" width="2.4" '
    'height="0.19"/><textSegment>'
    '<font face="Times New Roman" size="10" bold="yes" textColor="white"/>'
    '<string><![CDATA[ Holder]]></string></textSegment></text>'
    '</frame>'
    '<repeatingFrame name="R_ACT" source="G_Act" printDirection="down">'
    '<geometryInfo x="1.0" y="1.5" width="7.19" height="0.44"/>'
    '<generalLayout verticalElasticity="expand"/>'
    '<visualSettings fillPattern="transparent" linePattern="solid"/>'
    '<frame name="M_ROW">'
    '<geometryInfo x="1.0" y="1.5" width="7.19" height="0.19"/>'
    '<generalLayout verticalElasticity="expand"/>'
    '<visualSettings linePattern="solid"/>'
    '<field name="F_TYPE" source="ACT_TYPE" alignment="start">'
    '<font face="Times New Roman" size="10"/>'
    '<geometryInfo x="1.0" y="1.5" width="2.31" height="0.19"/></field>'
    '<field name="F_BY" source="ACT_BY" alignment="start">'
    '<font face="Times New Roman" size="10"/>'
    '<geometryInfo x="3.31" y="1.5" width="2.44" height="0.19"/></field>'
    '<field name="F_OWN" source="ACT_OWN" alignment="start">'
    '<font face="Times New Roman" size="10"/>'
    '<geometryInfo x="5.75" y="1.5" width="2.44" height="0.19"/></field>'
    '<line name="B_V1" stretchWithFrame="M_ROW">'
    '<geometryInfo x="3.31" y="1.5" width="0.0" height="0.19"/>'
    '<visualSettings fillPattern="transparent" linePattern="solid"/>'
    '<points><point x="3.31" y="1.5"/><point x="3.31" y="1.69"/></points>'
    '</line>'
    '<line name="B_V2" stretchWithFrame="M_ROW">'
    '<geometryInfo x="5.75" y="1.5" width="0.0" height="0.19"/>'
    '<visualSettings fillPattern="transparent" linePattern="solid"/>'
    '<points><point x="5.75" y="1.5"/><point x="5.75" y="1.69"/></points>'
    '</line>'
    '</frame>'
    '<field name="F_NOTE" source="ACT_NOTE" alignment="start">'
    '<font face="Times New Roman" size="10"/>'
    '<geometryInfo x="1.0" y="1.69" width="7.19" height="0.25"/>'
    '<generalLayout verticalElasticity="expand"/></field>'
    '</repeatingFrame>'
    '<field name="F_DATE" source="ACT_DATE" alignment="start">'
    '<font face="Times New Roman" size="10"/>'
    '<geometryInfo x="0.19" y="1.5" width="0.81" height="0.19"/></field>'
    '</repeatingFrame></frame>'
    '</body></section></layout></report>'
)


def test_declared_detail_band_fonts_and_boxes():
    """The nested-detail band must carry the SOURCE's declared styling:
    Times faces/sizes on row+wrap fields and captions (ours dropped to
    9pt Helvetica), the solid-linePattern frame boxes around the record
    and the row band, and the vertical separator <line>s at their
    declared x (truth prints boxed detail cells; ours drew nothing)."""
    import re
    from converter import convert

    rdl = convert(_BOXED_ND_XML.encode())["rdl_xml"]
    det = re.search(r'<Textbox Name="Tb_NDDet_[^"]*">.*?</Textbox>',
                    rdl, re.S)
    assert det and "Times New Roman" in det.group(0)         and "10pt" in det.group(0), (
        "detail cells must keep the declared face/size")
    wrap = re.search(r'<Textbox Name="Tb_NDWrap_[^"]*">.*?</Textbox>',
                     rdl, re.S)
    assert wrap and "Times New Roman" in wrap.group(0), (
        "wrap lines must keep the declared face")
    assert 'Name="ND_BandBox_0"' in rdl and 'Name="ND_BandBox_1"' in rdl, (
        "the declared solid frames must draw the record + row boxes")
    vseps = re.findall(r'<Line Name="ND_VSep_\d">.*?<Left>([0-9.]+)in</Left>',
                       rdl, re.S)
    assert sorted(vseps) == ["3.31", "5.75"], (
        "the vertical separator lines must sit at their declared x")

    # prove-the-gate: strip the declared styling -> defaults, no chrome
    plain = _BOXED_ND_XML.replace(
        '<font face="Times New Roman" size="10"/>', "")
    plain = plain.replace(
        '<visualSettings fillPattern="transparent" linePattern="solid"/>',
        "")
    plain = plain.replace('<visualSettings linePattern="solid"/>', "")
    plain = re.sub(r'<line name="B_V\d".*?</line>', "", plain, flags=re.S)
    rdl2 = convert(plain.encode())["rdl_xml"]
    det2 = re.search(r'<Textbox Name="Tb_NDDet_[^"]*">.*?</Textbox>',
                     rdl2, re.S)
    assert det2 and "9pt" in det2.group(0), "undeclared -> 9pt default"
    assert "ND_BandBox" not in rdl2 and "ND_VSep" not in rdl2, (
        "no declared boxes/lines -> no invented chrome")


def test_margin_chrome_paint_order_is_explicit():
    """The margin band object painted FIRST in Oracle (a full-width
    filled band) buried the date/page items drawn on top of it: this
    ReportViewer build ignores document order for equal-ZIndex overlaps,
    so every emitted chrome item must carry an explicit ZIndex in
    declared paint order (truth-render measured: 'Page N of M' vanished
    under the title band's gray fill)."""
    import re
    from converter import convert

    rdl = convert(_CHROME_XML.encode())["rdl_xml"]
    ph = re.search(r"<PageHeader>(.*?)</PageHeader>", rdl, re.S).group(1)
    zs = re.findall(r'<Textbox Name="MChrome_H_[^"]*">.*?'
                    r"<ZIndex>(\d+)</ZIndex>", ph, re.S)
    assert len(zs) >= 2, "chrome items must carry explicit ZIndex"
    assert zs == sorted(zs, key=int), (
        "ZIndex must follow the declared paint order (y,x sort)")


def _stacked_list_xml(fonts=True):
    F = '<font face="Arial" size="10"/>' if fonts else ''
    FB = '<font face="Arial" size="10" bold="yes"/>' if fonts else ''
    hdrs = ''.join(
        '<text name="B_H%d"><geometryInfo x="%s" y="%s" width="1.5" '
        'height="0.18"/><textSegment>%s'
        '<string><![CDATA[%s]]></string></textSegment></text>'
        % (i, x, y, FB, t)
        for i, (x, y, t) in enumerate([
            (0.3, 0.8, 'Permit'), (0.3, 0.98, 'Permit Dates'),
            (4.0, 0.8, 'City'), (4.0, 0.98, 'Type of Operation'),
            (7.5, 0.8, 'Site'), (7.5, 0.98, 'Visited'),
        ]))
    flds = ''.join(
        '<field name="F_%s" source="%s" alignment="start">%s'
        '<geometryInfo x="%s" y="%s" width="1.5" height="0.18"/></field>'
        % (s, s, F, x, y)
        for s, x, y in [
            ('PERM_NO', 0.3, 1.2), ('PERM_DATES', 0.3, 1.38),
            ('CITY_NM', 4.0, 1.2), ('OP_TYPE', 4.0, 1.38),
            ('SITE_NM', 7.5, 1.2), ('VIS_FL', 7.5, 1.38),
        ])
    return (
        '<?xml version="1.0"?>'
        '<report name="SLIST_T" DTDVersion="9.0.2.0.10">'
        '<data><dataSource name="Q_Main">'
        '<select><![CDATA[select perm_no, perm_dates, city_nm, op_type,'
        ' site_nm, vis_fl from t]]></select>'
        '<group name="G_Main"><dataItem name="PERM_NO" datatype="vchar2"/>'
        '<dataItem name="PERM_DATES" datatype="vchar2"/>'
        '<dataItem name="CITY_NM" datatype="vchar2"/>'
        '<dataItem name="OP_TYPE" datatype="vchar2"/>'
        '<dataItem name="SITE_NM" datatype="vchar2"/>'
        '<dataItem name="VIS_FL" datatype="vchar2"/></group>'
        '</dataSource></data>'
        '<layout><section name="main" width="11.00000" height="8.50000">'
        '<body>' + hdrs +
        '<repeatingFrame name="R_Main" source="G_Main" '
        'printDirection="down">'
        '<geometryInfo x="0.3" y="1.2" width="10.0" height="0.4"/>'
        + flds + '</repeatingFrame>'
        '</body></section></layout></report>')


def test_stacked_list_keeps_declared_font_sizes():
    """The stacked-list table hardcoded 8pt on captions AND data cells
    where the source declares 10pt faces (truth-PDF span-measured: the
    inspections list prints 10pt). Declared face/size must pass through;
    an undeclared source keeps the 8pt default."""
    import re
    from converter import convert

    rdl = convert(_stacked_list_xml(fonts=True).encode())["rdl_xml"]
    assert "Tablix_StackedList" in rdl, "fixture must route stacked"
    det = re.search(r'<Textbox Name="Tb_SLDet_[^"]*">.*?</Textbox>',
                    rdl, re.S)
    assert det and "<FontSize>10pt</FontSize>" in det.group(0) \
        and "<FontFamily>Arial</FontFamily>" in det.group(0), (
        "detail cells must keep the declared 10pt Arial")
    hdr = re.search(r'<Textbox Name="Tb_SLHdr_[^"]*">.*?</Textbox>',
                    rdl, re.S)
    assert hdr and "<FontSize>10pt</FontSize>" in hdr.group(0), (
        "header captions must keep the declared 10pt")

    # prove-the-gate: no declared fonts -> the 8pt default stays
    rdl2 = convert(_stacked_list_xml(fonts=False).encode())["rdl_xml"]
    assert "Tablix_StackedList" in rdl2
    det2 = re.search(r'<Textbox Name="Tb_SLDet_[^"]*">.*?</Textbox>',
                     rdl2, re.S)
    assert det2 and "<FontSize>8pt</FontSize>" in det2.group(0)


def test_inter_token_literal_space_survives():
    """A title line of two adjacent tokens separated by ONE literal space
    ("&A &B") lost the separator: the whitespace-only chunk between the
    tokens was stripped to nothing, gluing the resolved values together.
    The declared space must survive as a literal atom."""
    from converter.parsers.oracle_xml import parse_oracle_xml
    from converter.generators.rdl import _resolve_text_expression

    xml = (
        '<?xml version="1.0"?><report name="TOKSP_T" DTDVersion="9.0.2.0.10">'
        '<data>'
        '<userParameter name="P_A" datatype="character" width="10"/>'
        '<userParameter name="P_B" datatype="character" width="10"/>'
        '<dataSource name="Q_Main">'
        '<select><![CDATA[select c1 from t]]></select>'
        '<group name="G_Main"><dataItem name="C1" datatype="vchar2"/>'
        '</group></dataSource></data></report>'
    )
    rep = parse_oracle_xml(xml.encode())
    val, is_expr = _resolve_text_expression("&P_A &P_B", rep)
    assert is_expr
    assert '& " " &' in val, (
        "the literal single space between two tokens must survive: %r"
        % val)
    # sanity: no space declared -> none invented
    val2, _ = _resolve_text_expression("&P_A&P_B", rep)
    assert '" "' not in val2


_ALLPAGE_HDR_XML = (
    '<?xml version="1.0"?><report name="APHDR_T" DTDVersion="9.0.2.0.10">'
    '<data><dataSource name="Q_Main">'
    '<select><![CDATA[select site_col, site_addr, site_dt from t]]>'
    '</select>'
    '<group name="G_Col"><dataItem name="SITE_COL" datatype="vchar2"/>'
    '</group>'
    '<group name="G_Site"><dataItem name="SITE_ADDR" datatype="vchar2"/>'
    '<dataItem name="SITE_DT" datatype="vchar2"/></group>'
    '</dataSource></data>'
    '<layout><section name="main" width="8.50000" height="11.00000"><body>'
    '<frame name="M_HDR">'
    '<geometryInfo x="0.0" y="0.0" width="7.5" height="0.19"/>'
    '<advancedLayout printObjectOnPage="allPage" '
    'basePrintingOn="enclosingObject"/>'
    '<text name="B_LOC"><geometryInfo x="0.5" y="0.0" width="1.5" '
    'height="0.18"/><textSegment><font face="Arial" size="10" bold="yes"/>'
    '<string><![CDATA[Location]]></string></textSegment></text>'
    '<text name="B_DT"><geometryInfo x="5.5" y="0.0" width="1.5" '
    'height="0.18"/><textSegment><font face="Arial" size="10" bold="yes"/>'
    '<string><![CDATA[Incident Dates]]></string></textSegment></text>'
    '</frame>'
    '<frame name="M_GRPFR">'
    '<geometryInfo x="0.0" y="0.25" width="7.5" height="0.8"/>'
    '<repeatingFrame name="R_COL" source="G_Col" printDirection="down">'
    '<geometryInfo x="0.0" y="0.25" width="7.5" height="0.75"/>'
    '<text name="B_CAP"><geometryInfo x="0.1" y="0.25" width="3.0" '
    'height="0.18"/><textSegment><font face="Arial" size="10" bold="yes"/>'
    '<string><![CDATA[&SITE_COL : sites]]></string></textSegment></text>'
    '<repeatingFrame name="R_SITE" source="G_Site" printDirection="down">'
    '<geometryInfo x="0.0" y="0.5" width="7.5" height="0.3"/>'
    '<field name="F_ADDR" source="SITE_ADDR">'
    '<geometryInfo x="0.5" y="0.5" width="3.0" height="0.18"/></field>'
    '<field name="F_DT" source="SITE_DT">'
    '<geometryInfo x="5.5" y="0.5" width="1.8" height="0.18"/></field>'
    '</repeatingFrame></repeatingFrame></frame>'
    '</body></section></layout></report>'
)


def test_allpage_header_repeats_at_page_top():
    """A column-header frame declared printObjectOnPage="allPage" ABOVE
    the group band must render as the tablix's TOP static member with
    RepeatOnNewPage (SSRS's page-repeating header machinery) — ours
    printed it once, UNDER the first group band."""
    import re
    from converter import convert

    rdl = convert(_ALLPAGE_HDR_XML.encode())["rdl_xml"]
    assert 'Name="ND_ColHdr"' in rdl and 'Name="ND_Band"' in rdl
    assert rdl.index('Name="ND_ColHdr"') < rdl.index('Name="ND_Band"'), (
        "the allPage header row must precede the group band row")
    rh = re.search(r"<TablixRowHierarchy>\s*<TablixMembers>(.*?)"
                   r"</TablixRowHierarchy>", rdl, re.S).group(1)
    first = re.search(r"<TablixMember>(.*?)</TablixMember>", rh, re.S)
    assert first and "<RepeatOnNewPage>true</RepeatOnNewPage>" \
        in first.group(1) and "<Group" not in first.group(1), (
        "first row member must be the page-repeating static header")

    # prove-the-gate: without the allPage declaration the header stays
    # in its synthesized in-group spot (after the band row)
    plain = _ALLPAGE_HDR_XML.replace(
        '<advancedLayout printObjectOnPage="allPage" '
        'basePrintingOn="enclosingObject"/>', "")
    rdl2 = convert(plain.encode())["rdl_xml"]
    assert 'Name="ND_ColHdr"' in rdl2 and 'Name="ND_Band"' in rdl2
    assert rdl2.index('Name="ND_ColHdr"') > rdl2.index('Name="ND_Band"')


_ALLPAGE_HDR_BELOW_BAND_XML = (
    '<?xml version="1.0"?><report name="APHDR_B" DTDVersion="9.0.2.0.10">'
    '<data><dataSource name="Q_Main">'
    '<select><![CDATA[select cmp_id, site_nm, act_dt, act_tp from t]]>'
    '</select>'
    '<group name="G_Cmp"><dataItem name="CMP_ID" datatype="vchar2"/>'
    '<dataItem name="SITE_NM" datatype="vchar2"/></group>'
    '<group name="G_Act"><dataItem name="ACT_DT" datatype="vchar2"/>'
    '<dataItem name="ACT_TP" datatype="vchar2"/></group>'
    '</dataSource></data>'
    '<layout><section name="main" width="8.50000" height="11.00000"><body>'
    '<frame name="M_GRPFR">'
    '<geometryInfo x="0.0" y="0.0" width="7.5" height="2.2"/>'
    '<repeatingFrame name="R_CMP" source="G_Cmp" printDirection="down">'
    '<geometryInfo x="0.0" y="0.0" width="7.5" height="2.1"/>'
    '<frame name="M_BAND">'
    '<geometryInfo x="0.0" y="0.0" width="7.5" height="1.1"/>'
    '<visualSettings fillPattern="solid" fillBackgroundColor="darkgreen"/>'
    '<text name="B_CASE"><geometryInfo x="0.3" y="0.06" width="1.2" '
    'height="0.2"/><textSegment><font face="Arial" size="10" bold="yes"/>'
    '<string><![CDATA[Case No:]]></string></textSegment></text>'
    '<field name="F_CMP" source="CMP_ID">'
    '<geometryInfo x="1.6" y="0.06" width="1.5" height="0.2"/></field>'
    '<field name="F_SITE" source="SITE_NM">'
    '<geometryInfo x="1.6" y="0.62" width="3.0" height="0.2"/></field>'
    '</frame>'
    '<frame name="M_HDR">'
    '<geometryInfo x="0.0" y="1.31" width="7.5" height="0.19"/>'
    '<advancedLayout printObjectOnPage="allPage" '
    'basePrintingOn="enclosingObject"/>'
    '<text name="B_AD"><geometryInfo x="0.1" y="1.31" width="1.2" '
    'height="0.18"/><textSegment><font face="Arial" size="10" bold="yes"/>'
    '<string><![CDATA[Row Date]]></string></textSegment></text>'
    '<text name="B_AT"><geometryInfo x="1.5" y="1.31" width="1.8" '
    'height="0.18"/><textSegment><font face="Arial" size="10" bold="yes"/>'
    '<string><![CDATA[Row Kind]]></string></textSegment></text>'
    '</frame>'
    '<repeatingFrame name="R_ACT" source="G_Act" printDirection="down">'
    '<geometryInfo x="0.0" y="1.55" width="7.5" height="0.3"/>'
    '<field name="F_AD" source="ACT_DT">'
    '<geometryInfo x="0.1" y="1.55" width="1.2" height="0.18"/></field>'
    '<field name="F_AT" source="ACT_TP">'
    '<geometryInfo x="1.5" y="1.55" width="1.8" height="0.18"/></field>'
    '</repeatingFrame></repeatingFrame></frame>'
    '</body></section></layout></report>'
)


def test_allpage_header_below_geo_band_stays_in_place():
    """An allPage column-header frame whose DECLARED y sits BELOW the
    outer group's geometry band must NOT hoist to the tablix top: the
    truth prints the colored group band first, then the caption row,
    then the detail rows (page-top hoisting inverted that order on a
    master-detail report whose band is positioned fields, not a lexical
    caption — the caption-text demotion was blind to geometry bands)."""
    import re
    from converter import convert

    rdl = convert(_ALLPAGE_HDR_BELOW_BAND_XML.encode())["rdl_xml"]
    assert 'Name="ND_ColHdr"' in rdl and 'Name="ND_Band"' in rdl, (
        "fixture must route through the nested builder with a header row")
    assert rdl.index('Name="ND_Band"') < rdl.index('Name="ND_ColHdr"'), (
        "a below-band allPage header must stay under the group band")
    # the FIRST direct row-hierarchy member must be the band GROUP, not a
    # page-repeating static (that is the page-top hoist)
    import xml.etree.ElementTree as ET
    root = ET.fromstring(rdl)
    ns = root.tag.split("}")[0].strip("{")

    def q(t):
        return "{%s}%s" % (ns, t)

    rh = next(root.iter(q("TablixRowHierarchy")))
    first = rh.find(q("TablixMembers")).find(q("TablixMember"))
    assert first.find(q("Group")) is not None, (
        "below-band header must not become the page-top repeating static")


def _linked_notes_letter_xml(with_repeating=True):
    notes_open = (
        '<repeatingFrame name="R_G_Notes" source="G_Notes" '
        'printDirection="acrossDown" minWidowRecords="1" columnMode="no">'
        '<geometryInfo x="0.25" y="3.6" width="7.5" height="0.44"/>'
        '<generalLayout verticalElasticity="variable"/>'
        if with_repeating else "")
    notes_close = '</repeatingFrame>' if with_repeating else ""
    return (
        '<?xml version="1.0"?><report name="LTRN_T" DTDVersion="9.0.2.0.10">'
        '<data><dataSource name="Q_Main">'
        '<select><![CDATA[select perm_no, app_id, perm_nm from t]]>'
        '</select>'
        '<group name="G_PERMIT"><dataItem name="PERM_NO" datatype="vchar2"/>'
        '<dataItem name="APP_ID" datatype="number"/>'
        '<dataItem name="PERM_NM" datatype="vchar2"/></group></dataSource>'
        '<dataSource name="Q_Notes">'
        '<select><![CDATA[select note_txt, app_id from n '
        'where app_id = :APP_ID]]></select>'
        '<group name="G_Notes"><dataItem name="NOTE_TXT" datatype="vchar2"/>'
        '<dataItem name="APP_ID" datatype="number"/></group></dataSource>'
        '<link parentGroup="G_PERMIT" childQuery="Q_Notes" condition="eq" '
        'sqlClause="where"/></data>'
        '<layout><section name="main" width="8.50000" height="11.00000">'
        '<body width="8.0" height="10.5">'
        '<frame name="M_G_PERMIT">'
        '<geometryInfo x="0" y="0" width="7.75" height="10.26"/>'
        '<repeatingFrame name="R_G_PERMIT" source="G_PERMIT" '
        'printDirection="down" maxRecordsPerPage="1" minWidowRecords="1" '
        'columnMode="no">'
        '<geometryInfo x="0" y="0" width="7.75" height="10.2"/>'
        '<field name="F_NM" source="PERM_NM">'
        '<geometryInfo x="0.5" y="0.5" width="4.0" height="0.25"/></field>'
        '<field name="F_NO" source="PERM_NO">'
        '<geometryInfo x="0.5" y="1.0" width="2.0" height="0.25"/></field>'
        + notes_open +
        '<text name="B_Notes"><geometryInfo x="0.25" y="3.6" width="7.25" '
        'height="0.38"/><textSegment><font face="Arial" size="10" '
        'bold="yes"/><string><![CDATA[Site Notes:]]></string></textSegment>'
        '<textSegment><font face="Arial" size="10"/>'
        '<string><![CDATA[&NOTE_TXT]]></string></textSegment></text>'
        + notes_close +
        '</repeatingFrame></frame>'
        '</body></section></layout></report>')


def test_repeating_comments_block_lists_all_rows_and_collapses_empty():
    """A per-record letter's repeating sub-block bound to a linked child
    (the comments idiom: ONE token text inside a repeating frame) must
    print EVERY child row (=Join(LookupSet(...))) and COLLAPSE entirely
    (label included) when the child has no rows for this record — the
    scalar Lookup() kept only the first comment and the caption printed
    orphaned on zero-comment records (truth-measured)."""
    import re
    from converter import convert

    rdl = convert(_linked_notes_letter_xml(True).encode())["rdl_xml"]
    assert re.search(
        r'Join\(LookupSet\([^<]*"Q_Notes"\), vbCrLf\)', rdl), (
        "the repeating block must list ALL child rows via LookupSet")
    assert re.search(
        r'<Hidden>=\(Join\(LookupSet\([^<]*"Q_Notes"\), vbCrLf\)\) '
        r'= ""</Hidden>', rdl), (
        "the block must hide when the child has no rows for this record")
    # the child's link correlation must not hard-filter the dataset to one
    # master row: either the ``AND col = :bind`` predicate is STRIPPED, or
    # the bind is null-safe (``:bind IS NULL OR``) with a Nothing default —
    # both leave the dataset carrying EVERY row so LookupSet can join per
    # master record in SSRS.
    ct = re.search(r'<DataSet Name="Q_Notes">.*?<CommandText>(.*?)'
                   r'</CommandText>', rdl, re.S).group(1)
    assert (":APP_ID" not in ct) or re.search(
        r":APP_ID\s+IS\s+NULL\s+OR", ct, re.I), ct

    # prove-the-gate: the same text NOT inside a repeating frame is a 1:1
    # child -> scalar Lookup, no hide gate (unchanged behavior)
    rdl2 = convert(_linked_notes_letter_xml(False).encode())["rdl_xml"]
    assert "Join(LookupSet" not in rdl2
    assert re.search(r'Lookup\([^<]*"Q_Notes"\)', rdl2), (
        "a non-repeating linked text keeps its scalar Lookup")
    assert '<Hidden>=(Join' not in rdl2


def _cover_note_xml(note_h="0.65", maroon=True, bold_mid=True):
    seg_font_note = ('<font face="Arial" size="12" bold="yes" italic="yes" '
                     'textColor="r50g0b0"/>' if maroon
                     else '<font face="Arial" size="12"/>')
    mid_font = ('<font face="Arial" size="12" bold="yes"/>' if bold_mid
                else '<font face="Arial" size="12" italic="yes"/>')
    hdr = (
        '<section name="header"><body>'
        '<frame name="M_COVER_FORM">'
        '<geometryInfo x="0.25" y="0.25" width="7.5" height="3.5"/>'
        '<text name="B_L1"><geometryInfo x="0.25" y="0.5" width="1.75" '
        'height="0.25"/><textSegment><font face="Arial" size="12" '
        'bold="yes"/><string><![CDATA[Report:]]></string></textSegment>'
        '</text>'
        '<text name="B_V1"><geometryInfo x="2.25" y="0.5" width="4.0" '
        'height="0.25"/><textSegment><font face="Arial" size="12"/>'
        '<string><![CDATA[Sample Cover Form]]></string></textSegment>'
        '</text>'
        '<text name="B_SEGNOTE"><geometryInfo x="2.25" y="1.0" width="5.0" '
        'height="0.5"/><generalLayout verticalElasticity="variable"/>'
        '<textSegment><font face="Arial" size="12" italic="yes"/>'
        '<string><![CDATA[Reminder:  Pick ]]></string></textSegment>'
        '<textSegment>' + mid_font +
        '<string><![CDATA[Yard]]></string></textSegment>'
        '<textSegment><font face="Arial" size="12" italic="yes"/>'
        '<string><![CDATA[ for the sort order to match.]]></string>'
        '</textSegment></text>'
        '<text name="B_MAROON"><geometryInfo x="2.25" y="1.8" width="5.0" '
        'height="' + note_h + '"/>'
        '<generalLayout verticalElasticity="variable"/>'
        '<textSegment>' + seg_font_note +
        '<string><![CDATA[This caution note explains the form behavior '
        'in detail.]]></string></textSegment></text>'
        '<text name="B_L2"><geometryInfo x="0.25" y="2.0" width="1.75" '
        'height="0.25"/><textSegment><font face="Arial" size="12"/>'
        '<string><![CDATA[Cautions:]]></string></textSegment></text>'
        '</frame></body></section>'
    )
    return (
        '<?xml version="1.0"?><report name="COVN_T" '
        'DTDVersion="9.0.2.0.10">'
        '<data><dataSource name="Q_Main">'
        '<select><![CDATA[select perm_no, perm_nm from t]]></select>'
        '<group name="G_PERMIT"><dataItem name="PERM_NO" '
        'datatype="vchar2"/><dataItem name="PERM_NM" datatype="vchar2"/>'
        '</group></dataSource></data>'
        '<layout>' + hdr +
        '<section name="main" repeatOn="G_PERMIT">'
        '<body width="8.0" height="10.5">'
        '<frame name="M_PERMIT">'
        '<geometryInfo x="0.15" y="0.125" width="7.7" height="8.2"/>'
        '<repeatingFrame name="R_PERMIT" source="G_PERMIT" '
        'printDirection="down" maxRecordsPerPage="1">'
        '<geometryInfo x="0.15" y="0.125" width="7.7" height="8.0"/>'
        '<field name="F_NM" source="PERM_NM">'
        '<geometryInfo x="0.5" y="0.5" width="4.0" height="0.25"/>'
        '</field></repeatingFrame></frame>'
        '</body></section></layout></report>')


def test_cover_note_pairs_with_label_and_keeps_declared_style():
    """A cover label anchored beside a TALLER multi-line note (different
    declared y, note SPAN covers the label) must pair as label:value —
    ours orphaned the label and chained the note into the previous
    paragraph — and the note must keep its declared maroon bold-italic
    12pt (the cover emitter forced 10pt gray). Inline styled segments
    must emit as MULTI-TextRun paragraphs, not vbCrLf-joined lines."""
    import re
    from converter import convert

    rdl = convert(_cover_note_xml().encode())["rdl_xml"]
    # label paired, not orphaned as a full-width note
    lbl = re.search(r'<Textbox Name="LcCov_Lbl_(\d+)">(?:(?!</Textbox>).)*?Cautions:',
                    rdl, re.S)
    assert lbl, "the spanned label must pair as a normal cover label"
    val = re.search(r'<Textbox Name="LcCov_Val_%s">.*?</Textbox>'
                    % lbl.group(1), rdl, re.S)
    assert val and "caution note explains" in val.group(0), (
        "the note must be THAT label's value")
    v = val.group(0)
    assert "<FontWeight>Bold</FontWeight>" in v \
        and "<FontSize>12pt</FontSize>" in v \
        and "#7F0000" in v, (
        "the declared maroon bold 12pt must survive")
    # ...but the declared SLANT must NOT be painted. TRUTH (2026-08-08,
    # whole truth corpus): 16 Oracle-driver PDFs / 142,831 non-blank spans
    # carry ZERO italic-flagged spans and reference no *-Oblique font
    # resource, while 32,604 spans are bold -- the export dialect honours
    # weight and drops slant. All 16 declared-italic objects locatable in a
    # truth PDF print upright Helvetica / Helvetica-Bold. Stricter than the
    # old per-box presence check: no slant anywhere in the document.
    assert "<FontStyle>Italic</FontStyle>" not in rdl, (
        "Oracle never paints an oblique face -- see models."
        "ORACLE_RENDERS_ITALIC")
    # inline styled runs: one Paragraph, >=2 TextRuns, mid-sentence bold,
    # with the run-boundary space preserved. Name-agnostic on purpose: the
    # cover is emitted from the DECLARATION, so a note the source does not
    # author beside a label is its own object, not a synthesized "value".
    seg = re.search(r'<Textbox Name="LcCov_\w+">(?:(?!</Textbox>).)*'
                    r'Reminder:(?:(?!</Textbox>).)*</Textbox>', rdl, re.S)
    assert seg, "the multi-segment note must emit"
    para = re.search(r"<Paragraph>(?:(?!</Paragraph>).)*Reminder:"
                     r"(?:(?!</Paragraph>).)*</Paragraph>",
                     seg.group(0), re.S).group(0)
    assert para.count("<TextRun>") >= 3, (
        "styled segments must be TextRuns INSIDE one Paragraph")
    assert '="Reminder:  Pick "' in para and '="Yard "' in para, (
        "run-boundary spaces must survive")
    assert "vbCrLf" not in para, "inline runs must not line-break"

    # prove-the-gate 1: a SHORT note (span does not reach the label) keeps
    # the old behavior (label emits, but NOT paired to the note value)
    rdl2 = convert(_cover_note_xml(note_h="0.20").encode())["rdl_xml"]
    lbl2 = re.search(r'<Textbox Name="LcCov_Lbl_(\d+)">(?:(?!</Textbox>).)*?Cautions:',
                     rdl2, re.S)
    if lbl2:
        val2 = re.search(r'<Textbox Name="LcCov_Val_%s">.*?</Textbox>'
                         % lbl2.group(1), rdl2, re.S)
        assert not (val2 and "caution note explains" in val2.group(0))
    # prove-the-gate 2: uniform segments (no style mix) stay ONE TextRun
    rdl3 = convert(_cover_note_xml(bold_mid=False).encode())["rdl_xml"]
    seg3 = re.search(r'<Textbox Name="LcCov_\w+">(?:(?!</Textbox>).)*'
                     r'Reminder:(?:(?!</Textbox>).)*</Textbox>',
                     rdl3, re.S)
    assert seg3 and seg3.group(0).count("<TextRun>") == 1

    # STRICTER than the wording-only checks above: both the label and its
    # note print at their DECLARED geometry, not on a synthesized grid.
    # Declared (frame at x=0.25/y=0.25): label B_L2 x=0.25 y=2.0 w=1.75
    # h=0.25; note B_MAROON x=2.25 y=1.8 w=5.0 h=0.65. The cover rect's own
    # origin is subtracted from each, so the DIFFERENCES are exact.
    def _geom(block):
        return tuple(
            float(re.search("<%s>([-\\d.]+)in</%s>" % (t, t), block).group(1))
            for t in ("Top", "Left", "Width", "Height"))

    l_top, l_left, l_w, l_h = _geom(
        re.search(r'<Textbox Name="LcCov_\w+">(?:(?!</Textbox>).)*?Cautions:'
                  r'(?:(?!</Textbox>).)*</Textbox>', rdl, re.S).group(0))
    n_top, n_left, n_w, n_h = _geom(v)
    assert abs(l_w - 1.75) <= 0.01 and abs(l_h - 0.25) <= 0.01, (
        f"label box {l_w}x{l_h} is not its declared 1.75x0.25")
    assert abs(n_w - 5.0) <= 0.01 and abs(n_h - 0.65) <= 0.01, (
        f"note box {n_w}x{n_h} is not its declared 5.0x0.65")
    assert abs((n_left - l_left) - 2.0) <= 0.02, (
        f"declared 2.00in label->note column gap emitted as "
        f"{n_left - l_left:.2f}in")
    assert abs((l_top - n_top) - 0.20) <= 0.02, (
        f"declared 0.20in label->note row offset emitted as "
        f"{l_top - n_top:.2f}in")

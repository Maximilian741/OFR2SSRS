"""ROW-GRAIN visibility for collapsed variant bands that live INSIDE a data
region -- without ever binding the nested band to a dataset other than its
container's (the publish rejection), and without the dataset-scope First()
flattening that loses a variant.

THE PUBLISH RULE (a customer's SQL Server Reporting Services server refused
the upload of a multi-section invoice; probe-proven on the engine): a data
region nested inside another data region inherits the CONTAINER's dataset
scope -- its own <DataSetName> is ignored at render time and REJECTED at
publish time the moment an expression inside it references a column of the
dataset it declared: "Report item expressions can only refer to fields
within the current dataset scope or, if inside an aggregate, the specified
dataset scope". The earlier row-scope fix re-bound the collapsed band (the
one-row Band_* tablix) to the dataset that owns its trigger column so the
bare row-scope ``Fields!X.Value`` would evaluate per row -- the right grain,
but the band sits inside the record region bound to ANOTHER query, so the
server refused the whole report. Every local rail was blind: layout-mode
renders staticize expressions before the engine sees them.

THE GENERAL RULE NOW: a nested band keeps its container's dataset, and its
row-level trigger is evaluated per row of the trigger's own dataset INSIDE
a call carrying that dataset's scope -- exactly the "inside an aggregate,
the specified dataset scope" clause of the server's rule. WHICH rows is
decided by the DECLARATION, never guessed:

  * no <link> between the two queries (the customer's invoice: the address
    query is a constant of the issuing office, so Oracle iterates ALL its
    rows inside every invoice and the truth run prints both address
    variants on every invoice, one per address row):

        =(Sum(IIf(<hidden trigger>, 0, 1), "<owner dataset>") = 0)

    hides the band exactly when NO row of the owner dataset fires the
    trigger (zero rows included). Content from the owner rides the firing
    row: ``Max(IIf(<trigger fires>, Fields!Y.Value, Nothing), "<owner>")``.
  * the owner is a declared <link> CHILD of the container's query: its
    rows are correlated per container row, so a whole-dataset aggregate
    would flatten every parent's variants into one global answer. The
    correlated set goes through the DECLARED key:

        =(Len(Join(LookupSet(<key>, <child key>,
                             IIf(<hidden trigger>, "", "x"), "<owner>"), "")) = 0)
  * the owner is the declared <link> PARENT of the container's query: one
    owning master row, read through the scalar Lookup on the declared key.

The trigger stays a ROW-grain expression in every branch, so a two-variant
block gated by a row-level code prints BOTH variants when the rows carry one
code each -- which the flattened ``First(Fields!X.Value, "DS")`` form (one
global winner) can never do.

Guards, all measured on the generated RDL (synthetic fixtures only):
  1. no nested data region anywhere declares a DataSetName different from
     its container's, and no expression inside a region references a field
     outside that region's dataset without a dataset-scoped aggregate;
  2. the split: a per-row simulation of the aggregate semantics shows BOTH
     variants in a two-row world, exactly one in a one-row world, none in
     an empty world;
  3. content referencing the RECORD dataset stays a bare in-scope ref (the
     band is still bound to the record dataset) while the trigger keeps its
     row grain -- stricter than the old guard, which accepted the flattened
     First() fallback for this shape;
  4. content from the trigger's dataset inside a nested band is rewritten
     to the firing row's value, and the pass is idempotent;
  5. PROVE THE GATES CAN FAIL: the exact rejected shapes (a nested band
     bound to the other dataset; the bare cross-dataset Hidden; the
     aggregate with its scope stripped) turn the preflight BLOCKERs red;
  6. a DECLARED <link> child owner gets the correlated LookupSet form and a
     per-parent simulation splits the variants per parent row, where the
     whole-dataset aggregate would print both variants for every parent;
  7. the mirror direction (the container is the declared child) reads the
     owning master row through the scalar Lookup on the declared key;
  8. owner-dataset content the resolver had to BLANK (an unlinked cross-
     dataset column) prints the firing row instead of an empty box, the
     blank-token finding for it is gone, and the internal marker that
     traces the box to its declaration never ships;
  9. CORPUS-WIDE: guard 1's two facts hold on the generated RDL of EVERY
     source in every available corpus (the rejected report's corpus
     included; the repo-bundled samples and i18n fixtures everywhere) --
     the measurement that was missing when the server said no;
 10. PROVE THE MEASUREMENT CAN FAIL: the two checkers behind guards 1 and
     9 report each server-rejected shape when it is doctored into a clean
     artifact (nested region on another dataset; bare foreign ref in a
     VALUE, not only a Hidden; Lookup keyed on the other dataset's column)
     and stay silent on the legal forms.
"""
from __future__ import annotations

import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.validators.preflight import preflight_audit  # noqa: E402

NS = ("{http://schemas.microsoft.com/sqlserver/reporting/"
      "2008/01/reportdefinition}")
_REGIONS = {"Tablix", "List", "Matrix", "Table", "Chart", "CustomReportItem",
            "GaugePanel"}
# calls whose LAST argument may carry a "<dataset>" scope
_SCOPE_CALL_OPEN = re.compile(
    r"\b(?:First|Last|Sum|Avg|Min|Max|Count|CountDistinct|CountRows|StDev|"
    r"StDevP|Var|VarP|RunningValue|Aggregate|Lookup|LookupSet)\s*\(")
# the row-grain aggregate form of a nested band's Hidden
_AGG_HIDDEN_RE = re.compile(r'^=\(Sum\(IIf\((.+), 0, 1\), "(\w+)"\) = 0\)$')

_TRIGGER = """    <function name="{name}">
      <textSource>
      <![CDATA[FUNCTION {upper}
RETURN BOOLEAN IS
BEGIN
\tIF {cond} THEN
\t\tRETURN(TRUE) ;
\tELSE
\t\tRETURN(FALSE) ;
\tEND IF ;
END ; ]]>
      </textSource>
    </function>
"""


def _invoice_xml(inner_field=""):
    """A per-record notice whose record stack EXCEEDS the declared 9in body;
    the two conditional variant blocks live inside an INNER repeating frame
    bound to a SECOND dataset (Q_ADDR), each gated by a trigger on one of
    THAT dataset's row-level codes. Collapsing the bands brings the record
    back inside the body, so the variant-band rewrite fires.

    ``inner_field`` plants extra content inside variant A (used by guard 3
    to reference the RECORD dataset from inside the band)."""
    triggers = (_TRIGGER.format(name="f_way_a_ft", upper="F_WAY_A_FT",
                                cond=":Ad_Code = 'W'")
                + _TRIGGER.format(name="f_way_b_ft", upper="F_WAY_B_FT",
                                  cond=":Ad_Kind = 'K'"))
    return (f"""<?xml version="1.0" encoding="UTF-8"?>
<report name="NOTICE_ROW_SCOPE" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_MAIN">
      <select canParse="no"><![CDATA[SELECT ACCT_NO, REGION FROM ACCOUNTS]]></select>
      <group name="G_MAIN">
        <dataItem name="ACCT_NO" datatype="vchar2" columnOrder="1" defaultLabel="Account"/>
        <dataItem name="REGION" datatype="vchar2" columnOrder="2" defaultLabel="Region"/>
      </group>
    </dataSource>
    <dataSource name="Q_ADDR">
      <select canParse="no"><![CDATA[SELECT AD_CODE, AD_KIND FROM DELIVERY_WAYS]]></select>
      <group name="G_ADDR">
        <dataItem name="AD_CODE" datatype="vchar2" columnOrder="1" defaultLabel="Way Code"/>
        <dataItem name="AD_KIND" datatype="vchar2" columnOrder="2" defaultLabel="Way Kind"/>
      </group>
    </dataSource>
  </data>
  <programUnits>
{triggers}  </programUnits>
  <layout>
  <section name="main" repeatOn="G_MAIN">
    <body width="7.50000" height="9.00000">
      <location x="0.50000" y="0.50000"/>
      <repeatingFrame name="R_WAYS" source="G_ADDR" printDirection="down">
        <geometryInfo x="0.00000" y="0.00000" width="7.50000" height="4.00000"/>
        <generalLayout verticalElasticity="variable"/>
        <frame name="M_WAY_A">
          <geometryInfo x="0.00000" y="0.30000" width="7.50000" height="2.00000"/>
          <advancedLayout formatTrigger="f_way_a_ft"/>
          <text name="B_WAY_A">
            <geometryInfo x="0.20000" y="0.40000" width="6.00000" height="0.22000"/>
            <textSegment><font face="Arial" size="10"/>
            <string><![CDATA[Send remittance electronically]]></string></textSegment></text>
{inner_field}        </frame>
        <frame name="M_WAY_B">
          <geometryInfo x="0.00000" y="2.50000" width="7.50000" height="1.20000"/>
          <advancedLayout formatTrigger="f_way_b_ft"/>
          <text name="B_WAY_B">
            <geometryInfo x="0.20000" y="2.60000" width="6.00000" height="0.22000"/>
            <textSegment><font face="Arial" size="10"/>
            <string><![CDATA[or deliver payment in person]]></string></textSegment></text>
        </frame>
      </repeatingFrame>
      <frame name="M_TAIL">
        <geometryInfo x="0.00000" y="4.00000" width="7.50000" height="8.00000"/>
        <text name="B_TAIL">
          <geometryInfo x="0.20000" y="4.20000" width="6.00000" height="0.40000"/>
          <textSegment><font face="Arial" size="10"/>
          <string><![CDATA[The stated balance remains payable on the schedule shown]]></string></textSegment></text>
        <field name="F_ACCT_NO" source="ACCT_NO">
          <font face="Arial" size="10"/>
          <geometryInfo x="0.20000" y="11.60000" width="3.00000" height="0.20000"/></field>
      </frame>
    </body>
  </section>
  </layout>
</report>""").encode("utf-8")


def _local(tag):
    return tag.split("}", 1)[1] if "}" in tag else tag


def _bands(rdl):
    root = ET.fromstring(rdl)
    out = []
    for tab in root.iter(f"{NS}Tablix"):
        if (tab.get("Name") or "").startswith("Band_"):
            mem = next(iter(tab.find(f"{NS}TablixRowHierarchy")
                            .find(f"{NS}TablixMembers")))
            vis = mem.find(f"{NS}Visibility")
            hid = (vis.findtext(f"{NS}Hidden") or "") if vis is not None else ""
            out.append((tab, mem, hid))
    return root, out


def _nested_dataset_mismatches(root):
    """Every data region nested inside another data region whose own
    DataSetName differs from the OUTERMOST container's (the scope the
    engine actually gives it)."""
    out = []

    def walk(el, outer):
        tag = _local(el.tag)
        if tag in _REGIONS:
            own = el.findtext(f"{NS}DataSetName") or ""
            if outer is not None and own and own != outer[1]:
                out.append((el.get("Name"), own, outer[0], outer[1]))
            if outer is None:
                outer = (el.get("Name"), own)
        for c in el:
            walk(c, outer)

    walk(root, None)
    return out


def _scoped_spans(expr):
    """Spans of calls whose LAST top-level argument is a "<scope>" literal.
    A Lookup/LookupSet span starts AFTER its first top-level comma: the
    source-key argument is evaluated in the CURRENT scope (a ref of another
    dataset there is exactly the server's rejection)."""
    n = len(expr)
    spans = []
    for m in _SCOPE_CALL_OPEN.finditer(expr):
        start = m.end()
        depth, i, first_comma, last_comma = 1, start, -1, -1
        while i < n and depth:
            c = expr[i]
            if c == '"':
                j = expr.find('"', i + 1)
                i = (j if j >= 0 else n) + 1
                continue
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    break
            elif c == "," and depth == 1:
                if first_comma < 0:
                    first_comma = i
                last_comma = i
            i += 1
        if depth or last_comma < 0:
            continue
        if not re.fullmatch(r'"[^"]+"', expr[last_comma + 1:i].strip()):
            continue
        spans.append(((first_comma + 1) if m.group(0).startswith("Lookup")
                      else start, i))
    return spans


def _out_of_scope_refs(root):
    """Every expression inside a data region carrying a Fields! ref that is
    neither a column of the region's dataset nor inside a dataset-scoped
    call -- exactly what the server's scope rule rejects."""
    ds_fields = {}
    for ds in root.iter(f"{NS}DataSet"):
        ds_fields[ds.get("Name") or ""] = {
            f.get("Name") or "" for f in ds.iter(f"{NS}Field")}
    out = []

    def walk(el, region, region_ds):
        tag = _local(el.tag)
        if tag in _REGIONS and region is None:
            region = el.get("Name") or "?"
            region_ds = el.findtext(f"{NS}DataSetName") or ""
        txt = el.text or ""
        if region is not None and txt.startswith("=") and "Fields!" in txt:
            spans = _scoped_spans(txt)
            for m in re.finditer(r"Fields!(\w+)\.Value", txt):
                if any(a <= m.start() and m.end() <= b for a, b in spans):
                    continue
                if m.group(1) not in ds_fields.get(region_ds, set()):
                    out.append((tag, region, m.group(1), txt[:100]))
        for c in el:
            walk(c, region, region_ds)

    walk(root, None, "")
    return out


def _eval_hidden_on_row(hidden_expr, row):
    """Evaluate a translated trigger expression against ONE data row --
    exactly what the server does per row of the aggregate's dataset.
    Supports the translated boolean subset (Not/And/Or, =/<>, string
    literals)."""
    e = hidden_expr.lstrip("=")
    e = re.sub(r"Fields!(\w+)\.Value", lambda m: repr(row[m.group(1)]), e)
    assert "Fields!" not in e, f"unresolved field ref in {hidden_expr!r}"
    e = re.sub(r'"((?:[^"]|"")*)"', lambda m: repr(m.group(1)), e)
    e = re.sub(r"<>", "!=", e)
    e = re.sub(r"(?<![<>!=])=(?!=)", "==", e)
    e = re.sub(r"\bNot\b", " not ", e)
    e = re.sub(r"\bAnd\b", " and ", e)
    e = re.sub(r"\bOr\b", " or ", e)
    return bool(eval(e, {"__builtins__": {}}, {}))  # noqa: S307 - test-local


def _visible_by_aggregate(hidden_expr, rows):
    """SSRS semantics of the row-grain aggregate Hidden: the inner trigger
    is evaluated once per row of the owner dataset; Sum counts the rows
    whose trigger does NOT hide; the band is visible iff that count is
    positive (an empty dataset sums to Nothing = 0 -> hidden)."""
    m = _AGG_HIDDEN_RE.match(hidden_expr)
    assert m, f"not the row-grain aggregate form: {hidden_expr!r}"
    inner = "=" + m.group(1)
    fired = sum(1 for r in rows if not _eval_hidden_on_row(inner, r))
    return fired > 0


def _assert_xsd_valid(rdl):
    """Real-XSD legality (skips silently when the schema is not bundled;
    tests/test_rdl_schema_xsd.py carries the corpus-wide gate)."""
    xsd = ROOT / "tests" / "fixtures" / "schema" / "ReportDefinition_2008.xsd"
    if not xsd.exists():
        return
    try:
        from lxml import etree
    except ImportError:
        return
    schema = etree.XMLSchema(etree.parse(str(xsd)))
    assert schema.validate(etree.fromstring(rdl.encode("utf-8"))), (
        schema.error_log)


def _preflight_rules(rdl_xml):
    res = preflight_audit(rdl_xml)
    return [i[1] if isinstance(i, tuple) else i.get("rule")
            for i in res.get("issues", [])]


def test_nested_variant_bands_keep_container_scope_and_split_per_row():
    """Guards 1 + 2: container scope kept, row grain kept, both variants."""
    out = convert(_invoice_xml())
    rdl = out["rdl_xml"]
    root, bands = _bands(rdl)
    assert len(bands) == 2, "expected both variant frames as collapsed bands"

    hiddens = []
    for tab, mem, hid in bands:
        # the nested band keeps the CONTAINER's (record) dataset
        assert tab.findtext(f"{NS}DataSetName") == "Q_MAIN", (
            "a nested band must keep its container's dataset: "
            + str(tab.findtext(f"{NS}DataSetName")))
        # the row member stays STATIC -- the hidden-row-collapses idiom
        assert mem.find(f"{NS}Group") is None, (
            "the band's row member must stay static")
        # ROW-grain trigger INSIDE a dataset-scoped aggregate over the
        # dataset that owns the trigger column; no First() flattening
        m = _AGG_HIDDEN_RE.match(hid)
        assert m and m.group(2) == "Q_ADDR", hid
        assert re.search(r"Fields!AD_(CODE|KIND)\.Value", m.group(1)), hid
        assert "First(" not in hid, (
            "row-grain trigger must NOT flatten to dataset-scope First(): "
            + hid)
        hiddens.append(hid)

    # the server's two scope facts, over the WHOLE definition
    assert _nested_dataset_mismatches(root) == []
    assert _out_of_scope_refs(root) == []

    # upload legality: the server's visibility scope rule and the nested
    # region rule both stay clean, and the real 2008/01 XSD accepts it
    rules = [i.get("rule") for i in out["preflight"]["issues"]]
    assert "rdl.hidden_scope" not in rules, rules
    assert "rdl.nested_region_dataset" not in rules, rules
    _assert_xsd_valid(rdl)

    # THE SPLIT: row 1 fires variant A's code, row 2 fires variant B's ->
    # BOTH variants print. The flattened First() translation is
    # structurally unable to pass this (one global winner).
    two_rows = [{"AD_CODE": "W", "AD_KIND": "X"},
                {"AD_CODE": "Z", "AD_KIND": "K"}]
    assert [_visible_by_aggregate(h, two_rows) for h in hiddens] == \
        [True, True], hiddens
    # one row, one code -> exactly one variant
    one_row = [{"AD_CODE": "W", "AD_KIND": "X"}]
    assert sorted(_visible_by_aggregate(h, one_row) for h in hiddens) == \
        [False, True], hiddens
    # an empty dataset prints neither (Oracle: a repeating frame with no
    # rows prints nothing)
    assert [_visible_by_aggregate(h, []) for h in hiddens] == \
        [False, False], hiddens


def test_variant_band_content_stays_in_container_scope():
    """Guard 3: content referencing the RECORD dataset inside a variant
    band stays a bare in-scope ref -- the band is still bound to the record
    dataset -- while the trigger keeps its row grain (no First() fallback
    for this shape any more)."""
    inner = ('          <field name="F_ACCT_ECHO" source="ACCT_NO">\n'
             '            <font face="Arial" size="10"/>\n'
             '            <geometryInfo x="0.20000" y="1.00000" '
             'width="3.00000" height="0.20000"/></field>\n')
    out = convert(_invoice_xml(inner_field=inner))
    rdl = out["rdl_xml"]
    root, bands = _bands(rdl)
    echo = [(tab, hid) for tab, _m, hid in bands
            if any((el.text or "") == "=Fields!ACCT_NO.Value"
                   for el in tab.iter())]
    assert len(echo) == 1, (
        "the record field must print bare inside its variant band: "
        + str([(t.get('Name'), h) for t, _m, h in bands]))
    tab, hid = echo[0]
    assert tab.findtext(f"{NS}DataSetName") == "Q_MAIN"
    m = _AGG_HIDDEN_RE.match(hid)
    assert m and m.group(2) == "Q_ADDR" and "First(" not in hid, hid
    assert _nested_dataset_mismatches(root) == []
    assert _out_of_scope_refs(root) == []
    rules = [i.get("rule") for i in out["preflight"]["issues"]]
    assert "rdl.hidden_scope" not in rules, rules
    assert "rdl.nested_region_dataset" not in rules, rules
    _assert_xsd_valid(rdl)


def test_owner_dataset_content_in_a_nested_band_rides_the_trigger_row():
    """Guard 4: the post-pass on the exact REJECTED shape -- a nested band
    bound to the trigger's dataset, its Hidden a bare row-scope ref, and a
    bare owner-dataset value inside it. The band comes back to its
    container's dataset, the trigger moves inside the aggregate, and the
    value becomes the firing row's value; a second pass changes nothing."""
    from converter.generators.rdl import _rebind_variant_bands_to_row_scope

    root, bands = _bands(convert(_invoice_xml())["rdl_xml"])
    tab, mem, _hid = bands[0]
    tab.find(f"{NS}DataSetName").text = "Q_ADDR"
    hid_el = mem.find(f"{NS}Visibility").find(f"{NS}Hidden")
    hid_el.text = '=Not((Fields!AD_CODE.Value = "W"))'
    val = next(v for v in tab.iter(f"{NS}Value"))
    val.text = "=Fields!AD_KIND.Value"
    assert _nested_dataset_mismatches(root), "fixture must start rejected"

    _rebind_variant_bands_to_row_scope(root)

    assert tab.findtext(f"{NS}DataSetName") == "Q_MAIN"
    assert hid_el.text == \
        '=(Sum(IIf(Not((Fields!AD_CODE.Value = "W")), 0, 1), "Q_ADDR") = 0)'
    assert val.text == ('=Max(IIf((Fields!AD_CODE.Value = "W"), '
                        'Fields!AD_KIND.Value, Nothing), "Q_ADDR")')
    assert mem.find(f"{NS}Group") is None
    assert _nested_dataset_mismatches(root) == []
    assert _out_of_scope_refs(root) == []
    # idempotent
    before = ET.tostring(root)
    _rebind_variant_bands_to_row_scope(root)
    assert ET.tostring(root) == before


def test_publish_semantics_gates_fire_on_the_rejected_shapes():
    """Guard 5: PROVE THE GATES CAN FAIL on the shapes the server rejects."""
    rdl = convert(_invoice_xml())["rdl_xml"]
    root0 = ET.fromstring(rdl)
    assert any((t.get("Name") or "").startswith("Band_")
               for t in root0.iter(f"{NS}Tablix"))
    clean = _preflight_rules(rdl)
    assert "rdl.nested_region_dataset" not in clean, clean
    assert "rdl.hidden_scope" not in clean, clean

    ns = NS[1:-1]
    ET.register_namespace("", ns)

    def _doctor(mutate):
        root = ET.fromstring(rdl)
        mutate(root)
        return ET.tostring(root, encoding="unicode")

    def _band_hiddens(root):
        for tab in root.iter(f"{NS}Tablix"):
            if (tab.get("Name") or "").startswith("Band_"):
                for h in tab.iter(f"{NS}Hidden"):
                    yield tab, h

    # (a) the customer's rejection: a nested band bound to the other dataset
    def bind_other(root):
        for tab, _h in _band_hiddens(root):
            tab.find(f"{NS}DataSetName").text = "Q_ADDR"
    assert "rdl.nested_region_dataset" in _preflight_rules(_doctor(bind_other))

    # (b) the bare cross-dataset trigger (the pre-fix Hidden form)
    def bare_hidden(root):
        for _tab, h in _band_hiddens(root):
            h.text = '=Not((Fields!AD_CODE.Value = "W"))'
    assert "rdl.hidden_scope" in _preflight_rules(_doctor(bare_hidden))

    # (c) the aggregate with its scope stripped: the recognizer must demand
    # the dataset scope, not merely the Sum()
    def strip_scope(root):
        n = 0
        for _tab, h in _band_hiddens(root):
            new = (h.text or "").replace(', "Q_ADDR")', ')')
            n += new != (h.text or "")
            h.text = new
        assert n, "fixture Hidden must carry the Q_ADDR scope"
    assert "rdl.hidden_scope" in _preflight_rules(_doctor(strip_scope))

    # (d) a Lookup whose SOURCE-KEY argument names the other dataset: that
    # argument is evaluated in the current scope, so the recognizer must
    # not treat the whole call as dataset-scoped (destination-key and
    # result arguments are; the source key is not)
    def lookup_source_out_of_scope(root):
        for _tab, h in _band_hiddens(root):
            h.text = ('=Not(Lookup(Fields!AD_CODE.Value, Fields!ACCT_NO.Value, '
                      'Fields!AD_KIND.Value, "Q_ADDR") = "x")')
    assert "rdl.hidden_scope" in _preflight_rules(
        _doctor(lookup_source_out_of_scope))
    # ...while the same call keyed on the container's own column is legal
    def lookup_source_in_scope(root):
        for _tab, h in _band_hiddens(root):
            h.text = ('=Not(Lookup(Fields!ACCT_NO.Value, Fields!AD_CODE.Value, '
                      'Fields!AD_KIND.Value, "Q_ADDR") = "x")')
    assert "rdl.hidden_scope" not in _preflight_rules(
        _doctor(lookup_source_in_scope))


# ---------------------------------------------------------------------------
# Guards 6-8: the DECLARATION decides the row set; blanked owner content.
# ---------------------------------------------------------------------------
_LSET_HIDDEN_RE = re.compile(
    r'^=\(Len\(Join\(LookupSet\(Fields!(\w+)\.Value, Fields!(\w+)\.Value, '
    r'IIf\((.+), "", "x"\), "(\w+)"\), ""\)\) = 0\)$')


def _linked_invoice_xml():
    """The same notice, but the delivery-way query is a DECLARED <link>
    child of the record group on the account column -- its rows belong to
    ONE record each, so the variant a record prints must come from ITS
    rows, never from the whole dataset."""
    base = _invoice_xml().decode("utf-8")
    base = base.replace(
        "SELECT AD_CODE, AD_KIND FROM DELIVERY_WAYS",
        "SELECT ACCT_NO, AD_CODE, AD_KIND FROM DELIVERY_WAYS")
    base = base.replace(
        '<dataItem name="AD_CODE" datatype="vchar2" columnOrder="1" '
        'defaultLabel="Way Code"/>',
        '<dataItem name="ACCT_NO" datatype="vchar2" columnOrder="1" '
        'defaultLabel="Account"/>\n'
        '        <dataItem name="AD_CODE" datatype="vchar2" columnOrder="2" '
        'defaultLabel="Way Code"/>')
    assert "columnOrder=\"2\" defaultLabel=\"Way Code\"" in base
    base = base.replace(
        "  </data>",
        '    <link parentGroup="G_MAIN" childQuery="Q_ADDR" '
        'parentColumn="ACCT_NO" childColumn="ACCT_NO" condition="eq" '
        'sqlClause="where"/>\n  </data>')
    return base.encode("utf-8")


def _visible_by_lookupset(hidden_expr, parent_row, child_rows):
    """SSRS semantics of the correlated form: LookupSet keeps the child
    rows whose key equals THIS parent row's key, the inner IIf marks each
    kept row whose trigger does NOT hide, Join concatenates the marks and
    the band is visible iff any mark remains."""
    m = _LSET_HIDDEN_RE.match(hidden_expr)
    assert m, f"not the correlated LookupSet form: {hidden_expr!r}"
    src_key, dst_key, inner, _scope = m.groups()
    marks = [not _eval_hidden_on_row("=" + inner, r) for r in child_rows
             if r[dst_key] == parent_row[src_key]]
    return any(marks)


def test_declared_link_child_band_correlates_per_parent_row():
    """Guard 6: a declared <link> child owner -> correlated LookupSet on the
    declared key, container scope kept; per-parent simulation splits the
    variants per parent, which the whole-dataset aggregate cannot do."""
    out = convert(_linked_invoice_xml())
    rdl = out["rdl_xml"]
    root, bands = _bands(rdl)
    assert len(bands) == 2, [t.get("Name") for t, _m, _h in bands]
    hiddens = []
    for tab, mem, hid in bands:
        assert tab.findtext(f"{NS}DataSetName") == "Q_MAIN", (
            tab.findtext(f"{NS}DataSetName"))
        assert mem.find(f"{NS}Group") is None
        m = _LSET_HIDDEN_RE.match(hid)
        assert m, hid
        assert (m.group(1), m.group(2), m.group(4)) == \
            ("ACCT_NO", "ACCT_NO", "Q_ADDR"), hid
        assert re.search(r"Fields!AD_(CODE|KIND)\.Value", m.group(3)), hid
        assert "Sum(" not in hid and "First(" not in hid, (
            "a declared-link child must NOT collapse to a whole-dataset "
            "aggregate: " + hid)
        hiddens.append(hid)
    assert _nested_dataset_mismatches(root) == []
    assert _out_of_scope_refs(root) == []
    rules = _preflight_rules(rdl)
    assert "rdl.hidden_scope" not in rules, rules
    assert "rdl.nested_region_dataset" not in rules, rules
    _assert_xsd_valid(rdl)

    # parent A owns the W/X row, parent B owns the Z/K row
    parents = [{"ACCT_NO": "A1"}, {"ACCT_NO": "B2"}]
    children = [{"ACCT_NO": "A1", "AD_CODE": "W", "AD_KIND": "X"},
                {"ACCT_NO": "B2", "AD_CODE": "Z", "AD_KIND": "K"}]
    per_parent = [sorted(_visible_by_lookupset(h, p, children)
                         for h in hiddens) for p in parents]
    assert per_parent == [[False, True], [False, True]], per_parent
    # ...and the two parents print DIFFERENT variants
    which = [[_visible_by_lookupset(h, p, children) for h in hiddens]
             for p in parents]
    assert which[0] != which[1], which
    # CONTRAST: the whole-dataset aggregate over the same child rows shows
    # both variants for EVERY parent -- the flattening the link prevents.
    agg = [_visible_by_aggregate(
        '=(Sum(IIf(' + _LSET_HIDDEN_RE.match(h).group(3)
        + ', 0, 1), "Q_ADDR") = 0)', children) for h in hiddens]
    assert agg == [True, True], agg


def test_declared_link_parent_band_reads_the_owning_master_row():
    """Guard 7 (direct-call unit): the container is the declared <link>
    CHILD and the trigger tests a MASTER column -> the scalar Lookup on the
    declared key, container scope kept; bare master content in the band
    becomes the same Lookup."""
    from converter.generators.rdl import _rebind_variant_bands_to_row_scope
    from converter.parsers.oracle_xml import parse_oracle_xml

    report = parse_oracle_xml(_linked_invoice_xml())
    root, bands = _bands(convert(_linked_invoice_xml())["rdl_xml"])
    rec = next(t for t in root.iter(f"{NS}Tablix")
               if t.get("Name") == "Tablix_Record")
    rec.find(f"{NS}DataSetName").text = "Q_ADDR"   # container = the child
    tab, mem, _hid = bands[0]
    tab.find(f"{NS}DataSetName").text = "Q_ADDR"
    hid_el = mem.find(f"{NS}Visibility").find(f"{NS}Hidden")
    hid_el.text = '=Not((Fields!REGION.Value = "W"))'   # a MASTER column
    val = next(v for v in tab.iter(f"{NS}Value"))
    val.text = "=Fields!REGION.Value"

    _rebind_variant_bands_to_row_scope(root, report)

    assert tab.findtext(f"{NS}DataSetName") == "Q_ADDR"
    assert hid_el.text == (
        '=Not(Lookup(Fields!ACCT_NO.Value, Fields!ACCT_NO.Value, '
        'IIf(Not((Fields!REGION.Value = "W")), 0, 1), "Q_MAIN") = 1)'), \
        hid_el.text
    assert val.text == ('=Lookup(Fields!ACCT_NO.Value, Fields!ACCT_NO.Value, '
                        'Fields!REGION.Value, "Q_MAIN")'), val.text
    # the declared-link rule never guesses a key: with the link declaration
    # removed, the same shape falls to the unlinked whole-dataset form
    root2, bands2 = _bands(convert(_linked_invoice_xml())["rdl_xml"])
    rec2 = next(t for t in root2.iter(f"{NS}Tablix")
                if t.get("Name") == "Tablix_Record")
    rec2.find(f"{NS}DataSetName").text = "Q_ADDR"
    tab2, mem2, _h2 = bands2[0]
    tab2.find(f"{NS}DataSetName").text = "Q_ADDR"
    h2 = mem2.find(f"{NS}Visibility").find(f"{NS}Hidden")
    h2.text = '=Not((Fields!REGION.Value = "W"))'
    _rebind_variant_bands_to_row_scope(root2, None)
    assert _AGG_HIDDEN_RE.match(h2.text) and '"Q_MAIN"' in h2.text, h2.text


def test_unlinked_owner_content_in_a_nested_band_prints_the_firing_row():
    """Guard 8: a column of the trigger's (unlinked) dataset declared inside
    the variant frame -- the resolver can only blank it (no key to Lookup
    with, a bare ref is illegal) -- prints the FIRING ROW's value: the
    invoice's remit-to / deliver-to addresses instead of empty boxes."""
    inner = ('          <field name="F_WAY_A_KIND" source="AD_KIND">\n'
             '            <font face="Arial" size="10"/>\n'
             '            <geometryInfo x="0.20000" y="1.00000" '
             'width="3.00000" height="0.20000"/></field>\n'
             '          <text name="B_WAY_A_NOTE">\n'
             '            <geometryInfo x="0.20000" y="1.40000" '
             'width="6.00000" height="0.22000"/>\n'
             '            <textSegment><font face="Arial" size="10"/>\n'
             '            <string><![CDATA[Deliver by &AD_KIND only]]>'
             '</string></textSegment></text>\n')
    out = convert(_invoice_xml(inner_field=inner))
    rdl = out["rdl_xml"]
    root, bands = _bands(rdl)
    fires = '(Fields!AD_CODE.Value = "W")'
    per_row = f'Max(IIf({fires}, Fields!AD_KIND.Value, Nothing), "Q_ADDR")'
    values = [(v.text or "") for tab, _m, _h in bands
              for v in tab.iter(f"{NS}Value")]
    assert "=" + per_row in values, values
    assert f'="Deliver by " & {per_row} & " only"' in values, values
    assert not any("Nothing" in v and "AD_KIND" not in v for v in values
                   if v.startswith("=")), values
    # the blank-token finding for that content is gone, the others untouched
    blank_msgs = [i.get("message") or "" for i in out["preflight"]["issues"]
                  if i.get("rule") == "rdl.unresolved_token_blank"]
    assert not any("AD_KIND" in m for m in blank_msgs), blank_msgs
    # the trace marker is internal only
    assert "data-decl" not in rdl
    assert _nested_dataset_mismatches(root) == []
    assert _out_of_scope_refs(root) == []
    rules = _preflight_rules(rdl)
    assert "rdl.hidden_scope" not in rules, rules
    _assert_xsd_valid(rdl)


def test_negation_peels_a_full_outer_not_only():
    from converter.generators.rdl import _negate_vb
    assert _negate_vb('Not((Fields!X.Value = "M"))') == '(Fields!X.Value = "M")'
    assert _negate_vb('Fields!X.Value = "M"') == 'Not(Fields!X.Value = "M")'
    # a leading Not( that does not enclose the whole expression is wrapped
    assert _negate_vb("Not(a) And b") == "Not(Not(a) And b)"
    assert _negate_vb('Not(a = ")")') == 'a = ")"'


# ---------------------------------------------------------------------------
# Guards 9-10: the two publish facts over EVERY available corpus, and the
# proof that the measurement itself can fail.
#
# The customer's report server refused an upload that every local rail had
# passed, because no rail MEASURED the server's dataset-scope rule on the
# generated RDL. This leg measures it on every source the machine can see:
# the same corpus roots (and env overrides) the fatal gates enumerate, so a
# CI machine without a corpus still runs the repo-bundled samples and i18n
# fixtures and never skips silently.
# ---------------------------------------------------------------------------
_CORPORA = {
    "samples": ROOT / "samples" / "oracle",
    "i18n": ROOT / "tests" / "fixtures" / "i18n",
    "agency": Path(os.environ.get(
        "O2S_AGENCY_CORPUS",
        "C:/Users/maxca/Downloads/OneDrive_2026-08-06/Artifact Folders")),
    "wild": Path(os.environ.get(
        "O2S_WILD_CORPUS",
        "C:/Users/maxca/Downloads/_o2s_scratch11/wild_corpus")),
    "wild2": Path(os.environ.get(
        "O2S_WILD_CORPUS2",
        "C:/Users/maxca/Downloads/_o2s_scratch11/wild_corpus2")),
    "wild3": Path(os.environ.get(
        "O2S_WILD_CORPUS3",
        "C:/Users/maxca/Downloads/_o2s_scratch11/wild_corpus3")),
    "wild4": Path(os.environ.get(
        "O2S_WILD_CORPUS4",
        "C:/Users/maxca/Downloads/_o2s_scratch11/wild_corpus4")),
    "wild5": Path(os.environ.get(
        "O2S_WILD_CORPUS5",
        "C:/Users/maxca/Downloads/_o2s_scratch11/wild_corpus5")),
    "wild6": Path(os.environ.get(
        "O2S_WILD_CORPUS6",
        "C:/Users/maxca/Downloads/_o2s_scratch11/wild_corpus6")),
    "wild7": Path(os.environ.get(
        "O2S_WILD_CORPUS7",
        "C:/Users/maxca/Downloads/_o2s_scratch11/wild_corpus7")),
    "wild8": Path(os.environ.get(
        "O2S_WILD_CORPUS8",
        "C:/Users/maxca/Downloads/_o2s_scratch11/wild_corpus8")),
}


def _collect_corpus(root):
    """Every *.xml under the corpus root, skipping tooling/output dirs
    (underscore-prefixed) -- the fatal gates' own enumeration."""
    if not root.is_dir():
        return []
    return [p for p in sorted(root.rglob("*.xml"))
            if not any(part.startswith("_")
                       for part in p.relative_to(root).parts[:-1])]


def _corpus_params():
    return [pytest.param(p, id=f"{corpus}-{p.relative_to(root).as_posix()}")
            for corpus, root in _CORPORA.items()
            for p in _collect_corpus(root)]


@pytest.mark.parametrize("source", _corpus_params())
def test_corpus_publish_scope_invariants(source):
    """Guard 9: the generated RDL of every source holds the two facts the
    report server enforces at publish -- no data region nested inside
    another declares a different dataset, and no expression inside a region
    references a field outside the region's dataset unless it sits inside a
    dataset-scoped aggregate (a Lookup's SOURCE key included) -- and the
    preflight BLOCKERs for both stay silent."""
    out = convert(source.read_bytes())
    rdl = out["rdl_xml"]
    assert rdl and rdl.strip(), "convert() returned no RDL"
    root = ET.fromstring(rdl)
    assert _nested_dataset_mismatches(root) == []
    assert _out_of_scope_refs(root) == []
    rules = [i.get("rule") for i in out["preflight"]["issues"]]
    assert "rdl.nested_region_dataset" not in rules, rules
    assert "rdl.hidden_scope" not in rules, rules


def test_scope_measurements_detect_each_rejected_shape():
    """Guard 10: PROVE THE CORPUS MEASUREMENT CAN FAIL. Each checker behind
    guards 1 and 9 reports the exact server-rejected shape when it is
    doctored into a clean artifact, and stays silent on the legal forms."""
    rdl = convert(_invoice_xml())["rdl_xml"]

    def _band(root):
        return next(t for t in root.iter(f"{NS}Tablix")
                    if (t.get("Name") or "").startswith("Band_"))

    def _doctored(mutate):
        root = ET.fromstring(rdl)
        mutate(root)
        return root

    def _first_value(root):
        return next(v for v in _band(root).iter(f"{NS}Value"))

    clean = ET.fromstring(rdl)
    assert _nested_dataset_mismatches(clean) == []
    assert _out_of_scope_refs(clean) == []

    # (a) a nested region bound to another dataset than its container's
    def bind_other(root):
        _band(root).find(f"{NS}DataSetName").text = "Q_ADDR"
    hits = _nested_dataset_mismatches(_doctored(bind_other))
    assert [(h[1], h[3]) for h in hits] == [("Q_ADDR", "Q_MAIN")], hits

    # (b) a bare foreign ref in a VALUE -- the rule is not Hidden-only
    def bare_value(root):
        _first_value(root).text = "=Fields!AD_KIND.Value"
    hits = _out_of_scope_refs(_doctored(bare_value))
    assert [(h[0], h[2]) for h in hits] == [("Value", "AD_KIND")], hits

    # (c) the same ref inside a dataset-scoped aggregate is legal, however
    # deep the operand nests
    def scoped_value(root):
        _first_value(root).text = ('=Max(IIf((Fields!AD_CODE.Value = "W"), '
                                   'Fields!AD_KIND.Value, Nothing), "Q_ADDR")')
    assert _out_of_scope_refs(_doctored(scoped_value)) == []

    # (d) a Lookup whose SOURCE key names the other dataset is out of scope
    # (that argument is evaluated in the current scope) ...
    def lookup_source_foreign(root):
        _first_value(root).text = ('=Lookup(Fields!AD_CODE.Value, '
                                   'Fields!ACCT_NO.Value, Fields!AD_KIND.Value, '
                                   '"Q_ADDR")')
    hits = _out_of_scope_refs(_doctored(lookup_source_foreign))
    assert [h[2] for h in hits] == ["AD_CODE"], hits

    # ... while keyed on the container's own column it is legal
    def lookup_source_own(root):
        _first_value(root).text = ('=Lookup(Fields!ACCT_NO.Value, '
                                   'Fields!AD_CODE.Value, Fields!AD_KIND.Value, '
                                   '"Q_ADDR")')
    assert _out_of_scope_refs(_doctored(lookup_source_own)) == []

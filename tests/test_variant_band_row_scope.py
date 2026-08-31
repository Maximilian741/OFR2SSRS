"""ROW-GRAIN visibility for collapsed variant bands (no dataset-scope
First() flattening of per-row triggers).

The judged defect class: per-row format triggers on a record's inner
repeating frame (two variants of a block gated by a row-level code) were
translated with dataset-scope ``First(Fields!X.Value, "DS")`` — so ONE
variant wins globally, where the Oracle truth prints the right variant per
row. The general rule: a trigger referencing columns of the region's OWN
dataset at ROW grain must translate to the ROW-scope ``Fields!X.Value``,
legal exactly when the band region is bound to that dataset and iterates
it — so the band is re-bound to the owner dataset and its row member gets
a DETAIL group. ``First(...)`` stays reserved for genuinely region-external
references.

Engine constraints baked in (measured on the real ReportViewer renderer):
  * a non-static member may not sit under a DETAIL member of an enclosing
    tablix ("Detail members can only contain static inner members"), so
    every ancestor detail member of a re-bound band becomes a DYNAMIC
    per-row group over its own dataset's declared columns;
  * simple ``=Fields!X.Value`` group expressions load without an
    expression host (the layout render keeps its per-row pagination).

Three guards:
  1. the rebind happens: band bound to the owner dataset, detail-grain
     member, BARE row-scope Hidden, ancestor detail members made dynamic
     per-row — and a two-row simulation shows BOTH variants appear, one
     per row (the flattened First() form can never do that);
  2. upload legality holds: preflight's server-scope rule stays clean and
     the RDL validates against the real 2008/01 XSD when available;
  3. the rebind DECLINES when the band's content references the record
     dataset (a rebind would push the content out of scope) — the
     cross-dataset Hidden then keeps the explicit First(..., "owner")
     scope, exactly today's upload-legal behavior.

Synthetic fixtures only -- no client data.
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402

NS = ("{http://schemas.microsoft.com/sqlserver/reporting/"
      "2008/01/reportdefinition}")

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


def _eval_hidden_on_row(hidden_expr, row):
    """Evaluate a translated trigger <Hidden> against ONE data row --
    exactly what the server does per detail-row instance. Supports the
    translated boolean subset (Not/And/Or, =/<>, string literals)."""
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


def test_variant_band_triggers_stay_row_scoped_and_split_per_row():
    """Guard 1 + 2: the rebind, the grain, the legality, the two-row split."""
    out = convert(_invoice_xml())
    rdl = out["rdl_xml"]
    root, bands = _bands(rdl)
    assert len(bands) == 2, "expected both variant frames as collapsed bands"

    hiddens = []
    for tab, mem, hid in bands:
        # bound to the dataset that OWNS the trigger's row-level code
        assert tab.findtext(f"{NS}DataSetName") == "Q_ADDR", (
            "band must re-bind to the dataset owning its trigger columns")
        # DETAIL grain: a Group with no GroupExpressions on the row member
        grp = mem.find(f"{NS}Group")
        assert grp is not None and grp.find(f"{NS}GroupExpressions") is None, (
            "band row member must be a detail-grain group")
        # ROW-scope Hidden: bare Fields! ref, no dataset-scope First() wrap
        assert re.search(r"Fields!AD_(CODE|KIND)\.Value", hid), hid
        assert "First(" not in hid, (
            "row-grain trigger must NOT flatten to dataset-scope First(): "
            + hid)
        hiddens.append(hid)

    # every ancestor DETAIL member of the record tablix became a DYNAMIC
    # per-row group over the record dataset's own declared columns
    # (engine rule: no non-static member under a detail member)
    rec = next(t for t in root.iter(f"{NS}Tablix")
               if t.get("Name") == "Tablix_Record")
    detail_left = []
    for g in rec.find(f"{NS}TablixRowHierarchy").iter(f"{NS}Group"):
        if (g.get("Name") or "").startswith("Band_"):
            continue  # the bands' own detail members are the new grain
        ges = g.find(f"{NS}GroupExpressions")
        if ges is None:
            detail_left.append(g.get("Name"))
    assert not detail_left, (
        f"ancestor detail members must go dynamic per-row: {detail_left}")
    ge_texts = [ge.text for g in rec.find(f"{NS}TablixRowHierarchy")
                .iter(f"{NS}Group")
                if (g.get("Name") or "").startswith("Details")
                for ge in g.iter(f"{NS}GroupExpression")]
    assert "=Fields!ACCT_NO.Value" in ge_texts \
        and "=Fields!REGION.Value" in ge_texts, (
        "per-row grouping must list the record dataset's declared columns: "
        + str(ge_texts))

    # upload legality: the server's visibility scope rule stays clean
    rules = [i.get("rule") for i in out["preflight"]["issues"]]
    assert "rdl.hidden_scope" not in rules, rules

    # real-XSD legality (skips silently when the schema is not bundled;
    # tests/test_rdl_schema_xsd.py carries the corpus-wide gate)
    xsd = ROOT / "tests" / "fixtures" / "schema" / "ReportDefinition_2008.xsd"
    if xsd.exists():
        try:
            from lxml import etree
        except ImportError:
            etree = None
        if etree is not None:
            schema = etree.XMLSchema(etree.parse(str(xsd)))
            assert schema.validate(etree.fromstring(rdl.encode("utf-8"))), (
                schema.error_log)

    # THE TWO-ROW SPLIT: evaluate each band's Hidden per row, as the
    # server does per detail instance. Row 1 fires variant A's code, row 2
    # fires variant B's -> BOTH variants appear, each on ITS OWN row. The
    # flattened First() translation is structurally unable to pass this
    # (one global winner).
    rows = [{"AD_CODE": "W", "AD_KIND": "X"},
            {"AD_CODE": "Z", "AD_KIND": "K"}]
    visible = [[not _eval_hidden_on_row(h, r) for r in rows]
               for h in hiddens]
    assert sorted(visible) == [[False, True], [True, False]], (
        f"each variant must print on exactly its own row: {visible} "
        f"for {hiddens}")


def test_variant_band_rebind_declines_when_content_leaves_scope():
    """Guard 3: a band whose CONTENT references the record dataset must NOT
    re-bind (the rebind would push that content out of dataset scope);
    the cross-dataset trigger then keeps the explicit First(..., owner)
    wrap -- today's upload-legal, honestly-flattened fallback."""
    inner = ('          <field name="F_ACCT_ECHO" source="ACCT_NO">\n'
             '            <font face="Arial" size="10"/>\n'
             '            <geometryInfo x="0.20000" y="1.00000" '
             'width="3.00000" height="0.20000"/></field>\n')
    out = convert(_invoice_xml(inner_field=inner))
    rdl = out["rdl_xml"]
    root, bands = _bands(rdl)
    assert bands, "fixture must still produce collapsed bands"
    rebound = [tab for tab, _m, _h in bands
               if tab.findtext(f"{NS}DataSetName") == "Q_ADDR"
               and any("Fields!ACCT_NO.Value" in (el.text or "")
                       and (el.text or "").startswith("=")
                       and 'Fields!ACCT_NO.Value, "' not in (el.text or "")
                       for el in tab.iter())]
    assert not rebound, (
        "a band whose content references the record dataset bare must not "
        "re-bind away from it")
    # and the server-scope rule still holds everywhere
    rules = [i.get("rule") for i in out["preflight"]["issues"]]
    assert "rdl.hidden_scope" not in rules, rules

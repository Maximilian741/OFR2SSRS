"""
Field/alias desync classes — the no-prompt gate's L5 backlog, closed.

Oracle Reports binds SQL columns to dataItems BY POSITION; SSRS binds
<Field DataField> BY NAME. Three wild-corpus classes made the two sides
diverge while the Oracle report kept working (so nothing looked wrong at
the source):

  (a) SUFFIX-DEDUP: near-identical queries each contain the same unaliased
      expression; the designer auto-derived per-query item names with dedup
      suffixes, while the converter's alias deriver names every copy
      identically from the expression text;
  (b) TRUNCATION: the item name is a source-side truncated form of the SQL
      alias (identifier-length limits at export time);
  (c) ALIAS MAINTENANCE DRIFT: the SQL was edited and its aliases renamed;
      the data model kept the old item names.

The fix is ONE rule (single source of truth): every dataItem records the
SQL text that produced it (dataDescriptor expression); when that record
provably identifies exactly one select item, the emitted SQL alias BECOMES
the dataItem name, so the alias and the <Field DataField> come from the
same derivation. Renamed aliases are followed into the statement's
top-level ORDER BY (the one clause that resolves against select aliases).

Object-column (ADT) attribute items — which record a parentColumn — are
projected as real SQL (O.<parent>.<ATTR>) instead of a valueless stub.

Every test here fails against the pre-fix emitter (mutation-proved by
neutering the pairing / the ADT projection and watching them go red).
"""
from __future__ import annotations

import re

from converter import convert
from converter.generators.rdl import _alias_select_items
from converter.validators.no_prompt_gate import audit_no_prompt


# ---------------------------------------------------------------------------
# Unit level: the recorded-expression pairing inside _alias_select_items
# ---------------------------------------------------------------------------

def test_suffix_dedup_alias_binds_item_name_not_derived_name():
    """(a): an unaliased aggregate must take the ITEM name (dedup suffix and
    all), not the expression-derived name every sibling query also derives."""
    sql = ("select distinct t.zone, sum (nvl(t.load_qty,0)), "
           "sum (nvl(t.grand_amt,0)) shipped "
           "from loads t group by t.zone")
    names = ["zone", "sum_nvl_t_load_qty_0_1", "shipped"]
    exprs = ["t.zone", "sum ( nvl ( t.load_qty , 0 ) )",
             "sum ( nvl ( t.grand_amt , 0 ) )"]
    out = _alias_select_items(sql, names, exprs)
    assert "AS sum_nvl_t_load_qty_0_1" in out, out
    # Without the recorded expressions the deriver names the copy from the
    # expression text — the exact desync this pairing closes.
    legacy = _alias_select_items(sql, names)
    assert "AS SUM_NVL_T_LOAD_QTY_0" in legacy, legacy
    assert "sum_nvl_t_load_qty_0_1" not in legacy


def test_truncated_item_name_wins_over_longer_sql_alias():
    """(b): the DataField is the (truncated) item name, so the SQL alias must
    become that name — the recorded expression proves they are the same."""
    sql = ("select c.iso_code, c.rate_precision_value "
           "monetary_rate_precision_full from currencies c")
    names = ["iso_code", "monetary_rate_preci"]
    exprs = ["c.iso_code", "c.rate_precision_value"]
    out = _alias_select_items(sql, names, exprs)
    assert "monetary_rate_preci" in out, out
    assert "monetary_rate_precision_full" not in out


def test_alias_rename_follows_top_level_order_by():
    """(c): renaming an implicit alias must rewrite a top-level ORDER BY ref
    to it — otherwise the emitted statement dies with ORA-00904 at upload."""
    sql = ("select t.open_dt first_open_dt, t.code from ops t "
           "order by first_open_dt")
    names = ["op_start_dt", "code"]
    exprs = ["t.open_dt", "t.code"]
    out = _alias_select_items(sql, names, exprs)
    assert "t.open_dt op_start_dt" in out, out
    assert re.search(r"order by\s+op_start_dt", out, re.IGNORECASE), out
    assert "first_open_dt" not in out


def test_subquery_order_by_is_never_rewritten():
    """Only the depth-0 ORDER BY resolves against the outer select list; a
    subquery's ORDER BY belongs to the subquery and must stay untouched."""
    sql = ("select t.open_dt first_open_dt, t.code from "
           "(select * from ops order by first_open_dt) t")
    names = ["op_start_dt", "code"]
    exprs = ["t.open_dt", "t.code"]
    out = _alias_select_items(sql, names, exprs)
    assert "t.open_dt op_start_dt" in out, out
    assert "order by first_open_dt) t" in out, out


def test_recorded_expression_pairing_requires_two_way_uniqueness():
    """Two-way uniqueness still governs DIFFERENT expressions: when the
    record cannot say which of two different select items an item came
    from, nothing is renamed (an honest miss beats silently swapped
    columns). See test_identical_expression_copies_pair_to_their_fields for
    the one case where a tie is not a real ambiguity."""
    sql = "select sum(t.amt), sum(t.qty) from bills t"
    names = ["amt_sum_a", "amt_sum_b"]
    exprs = ["t.amt", "t.amt"]           # both records point at ONE column
    out = _alias_select_items(sql, names, exprs)
    assert "amt_sum_a" not in out, out
    assert "amt_sum_b" not in out, out


def test_identical_expression_copies_pair_to_their_fields():
    """REVISED (was: identical copies must never pair). Select items with
    IDENTICAL expression text return IDENTICAL values on every row, so which
    copy feeds which field is unobservable -- there is nothing to swap. The
    old refusal left both fields on the derived-name path, i.e. both printed
    blank through the NULL-stub wrap. Each copy now carries its field."""
    sql = "select sum(t.amt), sum(t.amt) from bills t"
    names = ["amt_sum_a", "amt_sum_b"]
    exprs = ["sum ( t.amt )", "sum ( t.amt )"]
    out = _alias_select_items(sql, names, exprs)
    assert "amt_sum_a" in out and "amt_sum_b" in out, out


def test_pairing_never_steals_a_select_item_that_yields_a_declared_name():
    """A select item whose output name already IS a declared item name is
    off-limits: renaming it would break that field's binding."""
    sql = "select t.zone, t.zone zone_b from loads t"
    names = ["zone_b", "zone_c"]
    exprs = ["t.zone", "t.zone"]
    out = _alias_select_items(sql, names, exprs)
    assert "zone_c" not in out, out


# ---------------------------------------------------------------------------
# End-to-end: convert() emits matching Field/alias pairs and a clean gate
# ---------------------------------------------------------------------------

_DEDUP_XML = b"""<?xml version="1.0" encoding="UTF-8" ?>
<report name="TEST_DEDUP" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_1">
      <select>
      <![CDATA[select distinct t.zone, sum (nvl(t.load_qty,0)), sum (nvl(t.grand_amt,0)) shipped from loads t where t.route = 'A' group by t.zone order by t.zone]]>
      </select>
      <displayInfo x="0" y="0" width="1" height="0.5"/>
      <group name="G_1">
        <displayInfo x="0" y="0" width="1" height="1"/>
        <dataItem name="zone" datatype="vchar2" columnOrder="1" width="10"
         defaultWidth="100000" defaultHeight="10000" columnFlags="0"
         defaultLabel="Zone">
          <dataDescriptor expression="t.zone" order="1" width="10"/>
          <dataItemPrivate adtName="" schemaName=""/>
        </dataItem>
        <dataItem name="sum_nvl_t_load_qty_0_" oracleDatatype="number"
         columnOrder="2" width="22" defaultWidth="90000"
         defaultHeight="10000" columnFlags="33" defaultLabel="Loads">
          <dataDescriptor expression="sum ( nvl ( t.load_qty , 0 ) )"
           order="2" oracleDatatype="number" width="22" precision="38"/>
          <dataItemPrivate adtName="" schemaName=""/>
        </dataItem>
        <dataItem name="shipped" oracleDatatype="number" columnOrder="3"
         width="22" defaultWidth="90000" defaultHeight="10000"
         columnFlags="33" defaultLabel="Shipped">
          <dataDescriptor expression="sum ( nvl ( t.grand_amt , 0 ) )"
           order="3" oracleDatatype="number" width="22" precision="38"/>
          <dataItemPrivate adtName="" schemaName=""/>
        </dataItem>
      </group>
    </dataSource>
    <dataSource name="Q_2">
      <select>
      <![CDATA[select distinct t.zone, sum (nvl(t.load_qty,0)), sum (nvl(t.grand_amt,0)) shipped1 from loads t where t.route = 'B' group by t.zone order by t.zone]]>
      </select>
      <displayInfo x="0" y="1" width="1" height="0.5"/>
      <group name="G_2">
        <displayInfo x="0" y="1" width="1" height="1"/>
        <dataItem name="zone1" datatype="vchar2" columnOrder="4" width="10"
         defaultWidth="100000" defaultHeight="10000" columnFlags="0"
         defaultLabel="Zone1">
          <dataDescriptor expression="t.zone" order="1" width="10"/>
          <dataItemPrivate adtName="" schemaName=""/>
        </dataItem>
        <dataItem name="sum_nvl_t_load_qty_0_1" oracleDatatype="number"
         columnOrder="5" width="22" defaultWidth="90000"
         defaultHeight="10000" columnFlags="33"
         defaultLabel="Sum Nvl T Load Qty 0 1">
          <dataDescriptor expression="sum ( nvl ( t.load_qty , 0 ) )"
           order="2" oracleDatatype="number" width="22" precision="38"/>
          <dataItemPrivate adtName="" schemaName=""/>
        </dataItem>
        <dataItem name="shipped1" oracleDatatype="number" columnOrder="6"
         width="22" defaultWidth="90000" defaultHeight="10000"
         columnFlags="33" defaultLabel="Shipped1">
          <dataDescriptor expression="sum ( nvl ( t.grand_amt , 0 ) )"
           order="3" oracleDatatype="number" width="22" precision="38"/>
          <dataItemPrivate adtName="" schemaName=""/>
        </dataItem>
      </group>
    </dataSource>
  </data>
</report>"""


def _command_texts(rdl_xml: str):
    """DataSet name -> CommandText, namespace-agnostic."""
    import xml.etree.ElementTree as ET

    def local(tag):
        return tag.split("}", 1)[1] if "}" in tag else tag

    out = {}
    root = ET.fromstring(rdl_xml)
    for ds in root.iter():
        if local(ds.tag) != "DataSet":
            continue
        for el in ds.iter():
            if local(el.tag) == "CommandText":
                out[ds.get("Name")] = el.text or ""
    return out


def test_dedup_desync_converts_with_matching_alias_and_clean_gate():
    rdl = convert(_DEDUP_XML, target_db="oracle")["rdl_xml"]
    cts = _command_texts(rdl)
    assert re.search(r"AS sum_nvl_t_load_qty_0_\b", cts.get("Q_1", "")), \
        cts.get("Q_1")
    assert "AS sum_nvl_t_load_qty_0_1" in cts.get("Q_2", ""), cts.get("Q_2")
    # The projection is REAL — no marked-NULL stub, no star wrap needed.
    assert "NULL AS sum_nvl_t_load_qty" not in rdl
    violations = [v for v in audit_no_prompt(rdl) if v.startswith("L5")]
    assert violations == [], violations


_ADT_XML = b"""<?xml version="1.0" encoding="UTF-8" ?>
<report name="TEST_ADT" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_A">
      <select>
      <![CDATA[select site_id, depot_addr from depots where site_id = :p_site]]>
      </select>
      <displayInfo x="0" y="0" width="1" height="0.5"/>
      <group name="G_A">
        <displayInfo x="0" y="0" width="1" height="1"/>
        <dataItem name="site_id" oracleDatatype="number" columnOrder="1"
         width="22" defaultWidth="80000" defaultHeight="10000"
         columnFlags="1" defaultLabel="Site Id">
          <dataDescriptor expression="site_id" order="1" width="22"
           precision="6"/>
        </dataItem>
        <dataItem name="depot_addr" datatype="object"
         oracleDatatype="nameObjType" columnOrder="2" width="32"
         defaultWidth="100000" defaultHeight="10000" columnFlags="0"
         defaultLabel="Depot Addr">
          <dataDescriptor expression="depot_addr" order="2" width="32"/>
          <dataItemPrivate adtName="DEPOT_ADDR_TYP"/>
        </dataItem>
        <dataItem name="c_STREET_NAME" datatype="vchar2"
         oracleDatatype="varCharStr" columnOrder="3" width="40"
         defaultWidth="100000" defaultHeight="10000" columnFlags="1"
         defaultLabel="Street Name">
          <dataDescriptor expression="STREET_NAME" order="1" width="40"/>
          <dataItemPrivate displayWithParentColumn="yes"
           parentColumn="depot_addr"/>
        </dataItem>
        <dataItem name="c_LOCALITY" datatype="vchar2"
         oracleDatatype="varCharStr" columnOrder="4" width="30"
         defaultWidth="100000" defaultHeight="10000" columnFlags="1"
         defaultLabel="Locality">
          <dataDescriptor expression="LOCALITY" order="2" width="30"/>
          <dataItemPrivate displayWithParentColumn="yes"
           parentColumn="depot_addr"/>
        </dataItem>
      </group>
    </dataSource>
  </data>
</report>"""


def test_adt_attribute_items_project_real_sql_not_null_stubs():
    """(c)-adjacent: an object-column attribute records its parent column —
    project it through the wrap's table alias instead of stubbing NULL."""
    rdl = convert(_ADT_XML, target_db="oracle")["rdl_xml"]
    ct = _command_texts(rdl).get("Q_A", "")
    assert "O.depot_addr.STREET_NAME AS c_STREET_NAME" in ct, ct
    assert "O.depot_addr.LOCALITY AS c_LOCALITY" in ct, ct
    assert "NULL AS c_STREET_NAME" not in ct
    assert "NULL AS c_LOCALITY" not in ct
    violations = [v for v in audit_no_prompt(rdl) if v.startswith("L5")]
    assert violations == [], violations


_STUB_XML = b"""<?xml version="1.0" encoding="UTF-8" ?>
<report name="TEST_STUB" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_S">
      <select>
      <![CDATA[select site_id from depots]]>
      </select>
      <displayInfo x="0" y="0" width="1" height="0.5"/>
      <group name="G_S">
        <displayInfo x="0" y="0" width="1" height="1"/>
        <dataItem name="site_id" oracleDatatype="number" columnOrder="1"
         width="22" defaultWidth="80000" defaultHeight="10000"
         columnFlags="1" defaultLabel="Site Id">
          <dataDescriptor expression="site_id" order="1" width="22"
           precision="6"/>
        </dataItem>
        <dataItem name="derived_metric_x" oracleDatatype="number"
         columnOrder="2" width="22" defaultWidth="80000"
         defaultHeight="10000" columnFlags="1" defaultLabel="Derived X">
          <dataDescriptor expression="an_absent_source_col" order="2"
           width="22"/>
        </dataItem>
      </group>
    </dataSource>
  </data>
</report>"""


def test_truly_report_computed_item_keeps_honest_null_stub():
    """An item whose recorded expression matches nothing and that records no
    parent column stays on the marked-NULL manifest — the projection is
    real (upload-safe) and honestly labeled for the operator."""
    rdl = convert(_STUB_XML, target_db="oracle")["rdl_xml"]
    ct = _command_texts(rdl).get("Q_S", "")
    assert "NULL AS derived_metric_x" in ct, ct
    violations = [v for v in audit_no_prompt(rdl) if v.startswith("L5")]
    assert violations == [], violations


# ---------------------------------------------------------------------------
# (d) DUPLICATE-COLUMN dedup rename: the same column name picked from two
# joined tables. The report tool auto-renamed the later occurrence with a
# numeric suffix (id -> id1) and recorded WHICH source expression each item
# bound; the FULL-expression record must win over the "yields another
# declared name" hands-off (which is what left these on the star-wrap NULL
# stub — and SELECT O.* over an inline view with duplicate column names is
# ORA-00918 at Refresh Fields; wild-corpus verified on 8 reports).
# ---------------------------------------------------------------------------

def test_duplicate_column_across_tables_realiases_renamed_item():
    sql = ("select a.zone_id, a.zone, b.zone_id, b.title "
           "from zones a, sites b where b.zone_id = a.zone_id")
    names = ["zone_id", "zone", "zone_id1", "title"]
    exprs = ["a.zone_id", "a.zone", "b.zone_id", "b.title"]
    out = _alias_select_items(sql, names, exprs)
    assert "b.zone_id AS zone_id1" in out, out
    # The occurrence that legitimately supplies the unsuffixed field is
    # never touched.
    assert re.search(r"a\.zone_id\s*,", out), out


def test_duplicate_same_expression_pairs_and_keeps_the_original_name():
    """REVISED (was: never pairs). Leaving ``amt1`` unpaired was not an
    honest stub: the emitter then wrapped the query as
    ``SELECT O.*, NULL AS amt1 FROM (select t.amt, t.amt ...) O`` and
    ``O.*`` over two columns named AMT is ORA-00918 at Refresh Fields --
    fatal error #2 -- while amt1 printed blank. The copies are identical, so
    the pairing cannot swap anything: one copy becomes amt1, and one copy
    keeps AMT because the declared field amt binds to that name."""
    sql = "select t.amt, t.amt from bills t"
    names = ["amt", "amt1"]
    exprs = ["t.amt", "t.amt"]
    out = _alias_select_items(sql, names, exprs)
    assert "AS amt1" in out, out
    assert out.count("t.amt AS amt1") == 1, out
    assert re.search(r"t\.amt\s*(,|\s+from)", out, re.I),         "one copy must keep yielding AMT for the declared field amt: " + out


_DUPCOL_XML = b"""<?xml version="1.0" encoding="UTF-8" ?>
<report name="TEST_DUPCOL" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_1">
      <select>
      <![CDATA[SELECT ALL ZONES.ZONE_ID, ZONES.ZONE, SITES.ZONE_ID, SITES.TITLE FROM ZONES, SITES WHERE (SITES.ZONE_ID = ZONES.ZONE_ID)]]>
      </select>
      <displayInfo x="0" y="0" width="1" height="0.5"/>
      <group name="G_1">
        <displayInfo x="0" y="0" width="1" height="1"/>
        <dataItem name="ZONE_ID" oracleDatatype="number" columnOrder="1"
         width="22" defaultWidth="80000" defaultHeight="10000"
         columnFlags="33" defaultLabel="Zone Id">
          <dataDescriptor expression="ZONES.ZONE_ID" order="1" width="22"
           precision="6"/>
          <dataItemPrivate adtName="" schemaName=""/>
        </dataItem>
        <dataItem name="ZONE" datatype="vchar2" columnOrder="2" width="30"
         defaultWidth="100000" defaultHeight="10000" columnFlags="33"
         defaultLabel="Zone">
          <dataDescriptor expression="ZONES.ZONE" order="2" width="30"/>
          <dataItemPrivate adtName="" schemaName=""/>
        </dataItem>
        <dataItem name="ZONE_ID1" oracleDatatype="number" columnOrder="3"
         width="22" defaultWidth="80000" defaultHeight="10000"
         columnFlags="1" defaultLabel="Zone Id1">
          <dataDescriptor expression="SITES.ZONE_ID" order="3" width="22"
           precision="6"/>
          <dataItemPrivate adtName="" schemaName=""/>
        </dataItem>
        <dataItem name="TITLE" datatype="vchar2" columnOrder="4" width="40"
         defaultWidth="100000" defaultHeight="10000" columnFlags="1"
         defaultLabel="Title">
          <dataDescriptor expression="SITES.TITLE" order="4" width="40"/>
          <dataItemPrivate adtName="" schemaName=""/>
        </dataItem>
      </group>
    </dataSource>
  </data>
</report>"""


def test_duplicate_join_key_converts_aliased_no_stub_wrap_clean_gate():
    """End-to-end: the renamed duplicate takes its item name as a real SQL
    alias; no star wrap, no NULL stub, and the no-prompt gate (whose
    star-side leg would flag the ORA-00918 wrap) is clean."""
    rdl = convert(_DUPCOL_XML, target_db="oracle")["rdl_xml"]
    ct = _command_texts(rdl).get("Q_1", "")
    assert "SITES.ZONE_ID AS ZONE_ID1" in ct, ct
    assert "O.*" not in ct, ct
    assert "NULL AS ZONE_ID1" not in ct, ct
    violations = [v for v in audit_no_prompt(rdl) if v.startswith("L5")]
    assert violations == [], violations

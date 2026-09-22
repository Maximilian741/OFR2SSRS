"""A query that selects the SAME expression twice must refresh cleanly.

Real Oracle SQL does this ("SELECT T.X, ..., T.X" -- one copy feeds a group,
one a detail). The report tool names the extra copy X1 and records the
IDENTICAL source expression for both items. The converter's recorded-
expression pairing could not choose between two equal candidates, fell
back to wrapping the query as ``SELECT O.*, NULL AS X1 FROM (<query>) O``,
and that is two failures at once:

  * ``O.*`` over an inline view with two columns called X is ORA-00918 at
    Refresh Fields -- fatal error #2, the Report Builder failure;
  * the X1 field reads NULL where Oracle printed the value.

Language-independent (first measured on a non-English harvest, reproduced
here with an English permit report). Synthetic fixture; no real report,
column or parameter name appears in this file.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.generators import rdl as R  # noqa: E402
from converter.validators.no_prompt_gate import audit_no_prompt  # noqa: E402
from converter.validators.publish_semantics import publish_violations  # noqa: E402


def _report(select_sql: str, items) -> bytes:
    """items = [(name, recorded_expression, order)]"""
    dis = "".join(
        '<dataItem name="%s" datatype="vchar2" width="20">'
        '<dataDescriptor expression="%s" descriptiveExpression="%s" order="%d" width="20"/>'
        '</dataItem>' % (n, e, e.rsplit(".", 1)[-1].upper(), o) for n, e, o in items)
    fields = "".join(
        '<field name="F_%d" source="%s"><geometryInfo x="0" y="%.2f" width="3" height="0.2"/></field>'
        % (i, n, 0.3 * i) for i, (n, _e, _o) in enumerate(items))
    return ('<?xml version="1.0"?><report name="DUP" DTDVersion="9.0.2.0.10"><data>'
            '<dataSource name="Q_1"><select><![CDATA[' + select_sql + ']]></select>'
            '<group name="G_1">' + dis + '</group></dataSource></data>'
            '<layout><section name="main"><body width="8" height="9">'
            '<repeatingFrame name="R" source="G_1"><geometryInfo x="0" y="0" width="7" height="2"/>'
            + fields + '</repeatingFrame></body></section></layout></report>').encode("utf-8")


# The customer shape in English: the permit number is selected twice.
TWICE = _report(
    "SELECT permits.permit_no, permits.site_name, permits.permit_no FROM permits",
    [("permit_no", "permits.permit_no", 1),
     ("site_name", "permits.site_name", 2),
     ("permit_no1", "permits.permit_no", 3)])

# Three copies: two renamed items must share out the copies, one copy keeps
# the original name.
THRICE = _report(
    "SELECT p.permit_no, p.permit_no, p.site_name, p.permit_no FROM permits p",
    [("permit_no", "p.permit_no", 1), ("permit_no1", "p.permit_no", 2),
     ("site_name", "p.site_name", 3), ("permit_no2", "p.permit_no", 4)])


def _command_text(rdl: str) -> str:
    m = re.search(r'<DataSet Name="Q_1">.*?<CommandText>(.*?)</CommandText>', rdl, re.S)
    assert m, "no Q_1 CommandText"
    return m.group(1)


def test_a_twice_selected_expression_refreshes_without_ora_00918():
    rdl = convert(TWICE, target_db="oracle")["rdl_xml"]
    sql = _command_text(rdl)
    assert "O.*" not in sql, "fell back to the star wrap:\n" + sql
    assert audit_no_prompt(rdl) == [], audit_no_prompt(rdl)
    assert publish_violations(rdl) == []


def test_the_extra_copy_is_named_for_its_field_and_carries_real_data():
    rdl = convert(TWICE, target_db="oracle")["rdl_xml"]
    sql = _command_text(rdl)
    assert re.search(r"\bpermits\.permit_no\s+AS\s+permit_no1\b", sql, re.I), sql
    assert "NULL AS permit_no1" not in sql, "the field would read NULL"


def test_three_copies_share_out_and_one_keeps_the_original_name():
    rdl = convert(THRICE, target_db="oracle")["rdl_xml"]
    sql = _command_text(rdl)
    assert "O.*" not in sql, sql
    assert re.search(r"\bAS\s+permit_no1\b", sql, re.I), sql
    assert re.search(r"\bAS\s+permit_no2\b", sql, re.I), sql
    # exactly one copy is left un-renamed (it still yields permit_no)
    bare = [m for m in re.finditer(r"\bp\.permit_no\b(?!\s+AS\b)", sql, re.I)]
    assert len(bare) == 1, sql
    assert audit_no_prompt(rdl) == []


# Same shape, but the report recorded the column UNQUALIFIED while the SQL
# qualifies it -- the variant that survived the first version of the fix.
UNQUALIFIED_RECORD = _report(
    "SELECT permits.permit_no, permits.site_name, permits.permit_no FROM permits",
    [("permit_no", "permit_no", 1),
     ("site_name", "site_name", 2),
     ("permit_no2", "permit_no", 3)])

# Two DIFFERENT tables' columns under one name, recorded unqualified: the
# record cannot say which is which, and the values differ -- no rename.
TWO_TABLES = _report(
    "SELECT a.permit_no, b.permit_no FROM a, b WHERE a.k = b.k",
    [("permit_no", "permit_no", 1), ("permit_no1", "permit_no", 2)])


def test_an_unqualified_record_still_resolves_identical_copies():
    rdl = convert(UNQUALIFIED_RECORD, target_db="oracle")["rdl_xml"]
    sql = _command_text(rdl)
    assert "O.*" not in sql, sql
    assert re.search(r"\bpermits\.permit_no\s+AS\s+permit_no2\b", sql, re.I), sql
    assert audit_no_prompt(rdl) == []


def test_two_different_tables_are_never_renamed_on_a_guess():
    """The looser output-name match must not let the tie-break pick between
    two DIFFERENT columns: a.permit_no and b.permit_no hold different values
    and the unqualified record cannot say which one the renamed item was."""
    sql = _command_text(convert(TWO_TABLES, target_db="oracle")["rdl_xml"])
    assert not re.search(r"\b[ab]\.permit_no\s+AS\s+permit_no1\b", sql, re.I), sql


def test_different_expressions_under_one_name_are_not_touched_by_the_tie_break():
    """Two DIFFERENT expressions that merely share an output name are a real
    ambiguity (e.g. A.ID and B.ID when the record says only 'ID'): the
    tie-break must leave them to the existing rules."""
    sql = "SELECT a.id, b.id FROM a, b"
    out = R._alias_select_items(sql, ["id", "id1"], ["id", "id"])
    assert "id1" not in out.lower().replace("a.id", "").replace("b.id", ""), out


def test_without_expression_pairing_the_star_wrap_returns(monkeypatch):
    """The gate CAN see this class: when the recorded expressions cannot be
    paired to the select list at all, the converter falls back to the
    ``SELECT O.*`` wrap and the no-prompt gate goes red on it.

    (What this does NOT prove is the tie-break itself -- that was proven at
    source level: disabling only the identical-expression tie-break turned
    the three tests above red, and restoring it turned them green.)"""
    real = R._alias_select_items

    def no_tie_break(sql, item_names, item_exprs=None):
        # give each recorded expression a unique suffix so equal candidates
        # can never be proven identical
        exprs = [(e or "") + (" /*%d*/" % i if e else "")
                 for i, e in enumerate(item_exprs or [])]
        return real(sql, item_names, [e.split(" /*")[0] + "_%d" % i if e else e
                                      for i, e in enumerate(exprs)])

    monkeypatch.setattr(R, "_alias_select_items", no_tie_break)
    rdl = convert(TWICE, target_db="oracle")["rdl_xml"]
    assert audit_no_prompt(rdl), "the gate did not go red without the tie-break"

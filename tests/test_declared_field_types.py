"""A field's rd:TypeName must come from the SOURCE's declarations.

Two ways the generator used to throw a declaration away, both found by the
expression-evaluation rail once it stopped treating an aggregate over a
non-numeric column as an informational note:

1. every field of the synthetic formula dataset was declared System.String,
   even though Oracle declares the type on the object itself
   (``<placeholder name="CP_X" datatype="number">``);
2. a query column that declares NO datatype fell back to the character
   default, which is indistinguishable from a declared character column --
   so a column the source reduces with ``<summary function="sum">`` shipped
   as System.String.

Either way the RDL contradicted itself: it carried ``Sum(Fields!X.Value)``
over a column it declared non-numeric -- the shape SSRS answers with
rsAggregateOfNonNumericData, and the silent-zero shape the campaign hunts.

A DECLARATION IS NEVER OVERRIDDEN BY AN INFERENCE: a column that declares its
own datatype keeps it, whatever any summary says.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.models import DataItem  # noqa: E402
from converter.parsers.oracle_xml import parse_oracle_xml  # noqa: E402
from converter.generators.rdl import (  # noqa: E402
    _field_clr_type,
    _numeric_summary_sources,
)

_RD_TYPE = "<rd:TypeName>%s</rd:TypeName>"


def _src(sal_decl: str, summary: str) -> bytes:
    return ("""<?xml version="1.0"?><report name="R" DTDVersion="9.0.2.0.10">
<data><dataSource name="Q_1"><select><![CDATA[SELECT nume, sal FROM t]]></select>
<group name="G"><dataItem name="nume" datatype="vchar2"/>
<dataItem name="sal" %s/></group>
%s
</dataSource></data><layout><section name="main"><body width="8" height="9">
<repeatingFrame name="R_G" source="G">
<geometryInfo x="0" y="0.25" width="6" height="0.3"/>
<field name="f_nume" source="nume">
<geometryInfo x="0" y="0.25" width="2" height="0.2"/></field>
<field name="f_sal" source="sal">
<geometryInfo x="2" y="0.25" width="2" height="0.2"/></field>
</repeatingFrame></body></section></layout></report>"""
            % (sal_decl, summary)).encode()


_SUM = ('<summary name="SumsalPerReport" source="sal" function="sum" '
        'reset="report"/>')
_SUM_NO_FUNC = '<summary name="SumsalPerReport" source="sal" reset="report"/>'
_COUNT = ('<summary name="CountsalPerReport" source="sal" function="count" '
          'reset="report"/>')


def _typename_of(rdl_xml: str, field: str) -> str:
    """The rd:TypeName the RDL declares for one field."""
    import re
    m = re.search(r'<Field Name="%s">.*?<rd:TypeName>([^<]+)</rd:TypeName>'
                  % field, rdl_xml, re.S)
    assert m, "no <Field Name=%r> in the RDL" % field
    return m.group(1)


# --------------------------------------------------------------------------
# The rule, at the unit
# --------------------------------------------------------------------------
def test_a_numeric_summary_declares_its_source_column_numeric():
    rep = parse_oracle_xml(_src("", _SUM))
    assert _numeric_summary_sources(rep) == {"SAL"}
    item = [i for q in rep.queries for i in q.items if i.name == "sal"][0]
    assert item.datatype_declared is False, "the source declares no datatype"
    assert _field_clr_type(item, _numeric_summary_sources(rep)) \
        == "System.Decimal"


def test_a_counting_summary_declares_nothing_about_the_type():
    """Count tallies rows of ANY type; it is not a statement about the column."""
    rep = parse_oracle_xml(_src("", _COUNT))
    assert _numeric_summary_sources(rep) == set()
    item = [i for q in rep.queries for i in q.items if i.name == "sal"][0]
    assert _field_clr_type(item, _numeric_summary_sources(rep)) \
        == "System.String"


def test_an_absent_function_is_oracles_default_sum():
    """Oracle omits ``function`` when it is the default. Measured over every
    source on this box: 67 of 1,027 summaries omit it and 63 of those carry
    Oracle's own generated name "Sum..."; the rest declare no name either."""
    rep = parse_oracle_xml(_src("", _SUM_NO_FUNC))
    assert _numeric_summary_sources(rep) == {"SAL"}


def test_a_declared_datatype_is_never_overridden_by_the_inference():
    """The column declares character AND a sum summary names it. The
    DECLARATION wins: an inference may only fill a gap, never overrule."""
    rep = parse_oracle_xml(_src('datatype="character"', _SUM))
    item = [i for q in rep.queries for i in q.items if i.name == "sal"][0]
    assert item.datatype_declared is True
    assert _field_clr_type(item, _numeric_summary_sources(rep)) \
        == "System.String"


def test_a_declared_number_needs_no_inference():
    item = DataItem(name="sal", datatype="number")
    assert _field_clr_type(item, set()) == "System.Decimal"
    assert _field_clr_type(item, {"SAL"}) == "System.Decimal"


# --------------------------------------------------------------------------
# ... and end to end, in the artifact that ships
# --------------------------------------------------------------------------
def test_the_rdl_declares_the_summed_column_numeric():
    rdl = convert(_src("", _SUM))["rdl_xml"]
    assert _typename_of(rdl, "sal") == "System.Decimal", rdl[:0]
    # the other column declares nothing and is summed by nothing
    assert _typename_of(rdl, "nume") == "System.String"


def test_the_rdl_keeps_a_declared_character_column_string():
    rdl = convert(_src('datatype="character"', _SUM))["rdl_xml"]
    assert _typename_of(rdl, "sal") == "System.String"


def test_the_summed_column_and_its_aggregate_agree():
    """The point of the whole rule: whatever the RDL aggregates, the RDL must
    declare numeric. No self-contradicting artifact."""
    import re
    rdl = convert(_src("", _SUM))["rdl_xml"]
    summed = set(re.findall(r"Sum\(Fields!([A-Za-z_][A-Za-z0-9_]*)\.Value",
                            rdl))
    assert summed, "the fixture must emit at least one Sum()"
    for name in summed:
        assert _typename_of(rdl, name) in (
            "System.Decimal", "System.Double", "System.Int32",
            "System.Int64", "System.Single"), (name, _typename_of(rdl, name))

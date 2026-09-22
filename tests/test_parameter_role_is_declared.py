"""
PARAMETER ROLE IS DECLARED, NOT SPELLED.

Which parameters reach the end-user prompt used to be decided by a roster of
uppercase name fragments. A roster only knows the naming conventions of the
reports it was written beside: the next site spells its lexical WHERE-clause
holder, its computed subtitle or its dead wiring differently and gets a prompt
asking an end user to type a SQL fragment.

The rule is now read off what the SOURCE DECLARES about each name:

  LEXICAL     spliced into a query as SQL TEXT (&NAME) -> the value IS SQL
  DEAD        referenced nowhere at all -> nothing the user types matters
  WRITE-ONLY  a program unit assigns it and no layout prints it -> the typed
              value is overwritten and the result is invisible
  (and never, in any of the three, a query bind or a field on Oracle's own
   <paramForm> -- either of those makes it a genuine input)

Every parameter below is spelled with invented ZZQX* words that match no
vocabulary list anywhere in the converter, so the only thing that can classify
them is their declared role. The mutation proof at the bottom takes the
report away and shows all three fall back to VISIBLE -- i.e. the structural
rule, not a name, is doing the work.

No real report / column / parameter name appears in this file.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.generators import rdl as R  # noqa: E402
from converter.parsers.oracle_xml import parse_oracle_xml  # noqa: E402


# ZZQX_* everywhere: no token here appears in any roster in the converter.
ROLES_XML = b"""<?xml version="1.0" encoding="UTF-8" ?>
<report name="ZZQX_ROLES" DTDVersion="9.0.2.0.10">
  <data>
    <userParameter name="P_ZZQX_BOUND" datatype="character" width="30"
     precision="10" label="Bound" defaultWidth="0" defaultHeight="0"/>
    <userParameter name="P_ZZQX_SPLICE" datatype="character" width="200"
     precision="10" initialValue="AND 1 = 1" label="Splice"
     defaultWidth="0" defaultHeight="0"/>
    <userParameter name="P_ZZQX_UNUSED" datatype="character" width="30"
     precision="10" label="Unused" defaultWidth="0" defaultHeight="0"/>
    <userParameter name="P_ZZQX_WRITEONLY" datatype="character" width="30"
     precision="10" label="WriteOnly" defaultWidth="0" defaultHeight="0"/>
    <userParameter name="P_ZZQX_SHOWN" datatype="character" width="30"
     precision="10" initialValue="ZZQX CONSTANT" label="Shown"
     defaultWidth="0" defaultHeight="0"/>
    <dataSource name="Q_ZZQX">
      <select>
      <![CDATA[SELECT
    zzqx_alpha,
    zzqx_beta
FROM zzqx_table
WHERE (:P_ZZQX_BOUND IS NULL OR zzqx_alpha = :P_ZZQX_BOUND)
&P_ZZQX_SPLICE
ORDER BY 1]]>
      </select>
      <displayInfo x="0" y="0" width="1.5" height="0.5"/>
      <group name="G_ZZQX">
        <displayInfo x="0" y="0" width="1.5" height="3"/>
        <dataItem name="zzqx_alpha" datatype="vchar2" columnOrder="1" width="30"
         defaultWidth="100000" defaultHeight="10000" columnFlags="0"
         defaultLabel="Alpha">
          <dataDescriptor expression="zzqx_alpha" descriptiveExpression="ZZQX_ALPHA"
           order="1" width="30"/>
          <dataItemPrivate adtName="" schemaName=""/>
        </dataItem>
        <dataItem name="zzqx_beta" datatype="vchar2" columnOrder="2" width="30"
         defaultWidth="100000" defaultHeight="10000" columnFlags="0"
         defaultLabel="Beta">
          <dataDescriptor expression="zzqx_beta" descriptiveExpression="ZZQX_BETA"
           order="2" width="30"/>
          <dataItemPrivate adtName="" schemaName=""/>
        </dataItem>
      </group>
    </dataSource>
  </data>
  <programUnits>
    <function name="f_after_param_form" returnType="character">
      <textSource>
      <![CDATA[FUNCTION F_After_Param_Form RETURN BOOLEAN IS
BEGIN
  :P_ZZQX_WRITEONLY := 'ZZQX' ;
  RETURN(TRUE) ;
END ;]]>
      </textSource>
    </function>
  </programUnits>
  <layout>
    <section name="main">
      <body>
        <field name="F_ZZQX_SHOWN" source="P_ZZQX_SHOWN" pageNumber="1"
         minWidowLines="1" spacing="0">
          <geometryInfo x="0.1" y="0.1" width="2.0" height="0.2"/>
        </field>
      </body>
    </section>
  </layout>
</report>"""


@pytest.fixture(scope="module")
def roles_report():
    return parse_oracle_xml(ROLES_XML)


@pytest.fixture(scope="module")
def roles_rdl():
    res = convert(ROLES_XML, target_db="oracle")
    rdl = (res or {}).get("rdl_xml") or ""
    assert rdl.strip(), "convert() returned no RDL"
    return rdl


def _report_parameters(rdl_xml):
    out = {}
    for rp in ET.fromstring(rdl_xml).iter():
        if not rp.tag.endswith("}ReportParameter") and rp.tag != "ReportParameter":
            continue
        name = rp.get("Name") or ""
        hidden = None
        values = []
        for child in rp.iter():
            tag = child.tag.split("}")[-1]
            if tag == "Hidden":
                hidden = (child.text or "").strip().lower()
            elif tag == "Value":
                values.append((child.text or "").strip())
        out[name.upper()] = {"hidden": hidden == "true", "values": values}
    return out


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------

def test_declared_roles_decide_visibility_without_any_name_match(roles_report):
    """Three not-a-filter classes, all classified from declarations alone."""
    non_filter = R._non_filter_parameters(roles_report)
    assert "P_ZZQX_SPLICE" in non_filter        # LEXICAL: &NAME in the SQL
    assert "P_ZZQX_UNUSED" in non_filter        # DEAD: referenced nowhere
    assert "P_ZZQX_WRITEONLY" in non_filter     # WRITE-ONLY: assigned, unprinted
    # A genuine query bind and a printed display constant are inputs/content.
    assert "P_ZZQX_BOUND" not in non_filter
    assert "P_ZZQX_SHOWN" not in non_filter


def test_roles_are_read_from_the_source_not_guessed(roles_report):
    roles = R._source_parameter_roles(roles_report)
    assert "P_ZZQX_BOUND" in roles["binds"]
    assert "P_ZZQX_SPLICE" in roles["lexical"]
    assert "P_ZZQX_WRITEONLY" in roles["assigned"]
    assert "P_ZZQX_SHOWN" in roles["layout"]
    assert "P_ZZQX_UNUSED" not in roles["binds"]
    assert "P_ZZQX_UNUSED" not in roles["lexical"]
    assert "P_ZZQX_UNUSED" not in roles["layout"]
    assert "P_ZZQX_UNUSED" not in roles["assigned"]


def test_rdl_hides_exactly_the_non_filter_parameters(roles_rdl):
    params = _report_parameters(roles_rdl)
    for name in ("P_ZZQX_SPLICE", "P_ZZQX_UNUSED", "P_ZZQX_WRITEONLY"):
        assert params[name]["hidden"], name + " reached the end-user prompt"
    assert not params["P_ZZQX_BOUND"]["hidden"], "a real filter was hidden"


def test_a_printed_display_constant_keeps_its_declared_default(roles_rdl):
    """The classes above can never swallow a display constant: it is printed
    (so not DEAD), not assigned (so not WRITE-ONLY) and not spliced (so not
    LEXICAL). Its Oracle default must still ship, or the value prints blank."""
    params = _report_parameters(roles_rdl)
    assert "ZZQX CONSTANT" in params["P_ZZQX_SHOWN"]["values"]


def test_every_parameter_keeps_a_usable_default(roles_rdl):
    """#1 rule: never an empty <Value/>, hidden or not."""
    for name, info in _report_parameters(roles_rdl).items():
        assert info["values"], name + " has no DefaultValue at all"
        for v in info["values"]:
            assert v != "", name + " shipped an EMPTY default (prompt trigger)"


# ---------------------------------------------------------------------------
# Mutation proof - the STRUCTURE is what classifies, not the spelling
# ---------------------------------------------------------------------------

def test_without_the_source_none_of_the_three_can_be_classified(roles_report):
    """Take the declarations away and every ZZQX name falls back to VISIBLE.
    That is the proof the rule reads the source: no roster in the converter
    recognises these names, so nothing else could have hidden them."""
    for name in ("P_ZZQX_SPLICE", "P_ZZQX_UNUSED", "P_ZZQX_WRITEONLY"):
        assert not R._should_hide_parameter(name, True), (
            name + " was hidden by SPELLING, not by a declaration")
        assert R._should_hide_parameter(name, True, report=roles_report), (
            name + " is not classified from its declared role")


def test_a_bind_is_never_reclassified_as_wiring(roles_report):
    """Mutation: even when a program unit also assigns a bound parameter (the
    defensive-default idiom), a query bind stays an input."""
    trigger = type("T", (), {"name": "zzqx", "body":
                             ":P_ZZQX_BOUND := 'ZZQX' ;"})()
    doctored = parse_oracle_xml(ROLES_XML)
    doctored.triggers = list(doctored.triggers or []) + [trigger]
    for attr in ("_o2s_param_roles", "_o2s_non_filter_params"):
        if hasattr(doctored, attr):
            delattr(doctored, attr)
    assert "P_ZZQX_BOUND" not in R._non_filter_parameters(doctored)

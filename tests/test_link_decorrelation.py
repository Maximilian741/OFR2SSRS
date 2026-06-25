"""Oracle <link> child queries: decorrelation + composite Lookup keys.

In Oracle Reports a <link> re-executes the child query PER MASTER ROW with
the bind set from that row. SSRS runs the dataset ONCE with binds coming
from report parameters that default to NULL. Two things are therefore
load-bearing (both verified by rendering production-shaped data through
Microsoft's engine — single-key joins put the SAME permittee on every
record when the first key is a constant like a program id):

  1. Every correlation predicate ``col = :bind`` must be widened to
     ``(:bind IS NULL OR col = :bind)`` so a NULL bind returns the FULL
     set (the Lookup re-applies the correlation client-side).
  2. The Lookup must join on the COMPOSITE of ALL correlation keys,
     not just the first one.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402

# Master with two key columns (K_CONST mimics a program id that is the
# same on every row; K_SEL is the selective key) and a linked child
# correlated on BOTH. Structure-only — nothing client-specific.
_LINKED_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<report name="SAMPLE_LINKED" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_MASTER">
      <select>
      <![CDATA[SELECT M.K_Const, M.K_Sel, M.Title FROM Masters M]]>
      </select>
      <group name="G_MASTER">
        <dataItem name="K_Const" datatype="number"/>
        <dataItem name="K_Sel" datatype="number"/>
        <dataItem name="Title" datatype="vchar2"/>
      </group>
    </dataSource>
    <dataSource name="Q_CHILD">
      <select>
      <![CDATA[SELECT C.Payload
FROM Children C
WHERE C.K_Const = :K_Const
  AND C.K_Sel = :K_Sel]]>
      </select>
      <group name="G_CHILD">
        <dataItem name="Payload" datatype="vchar2"/>
      </group>
    </dataSource>
    <link parentGroup="G_MASTER" childQuery="Q_CHILD" condition="eq"
     sqlClause="where"/>
  </data>
  <layout>
  <section name="main">
    <body width="8.0" height="10.0">
      <field name="F_TITLE" x="0.5" y="0.5" width="7.0" height="0.4"
             source="Title"/>
      <field name="F_PAYLOAD" x="0.5" y="1.2" width="7.0" height="0.4"
             source="Payload"/>
    </body>
  </section>
  </layout>
</report>
"""


def test_link_predicates_are_null_safe_widened():
    rdl = convert(_LINKED_XML)["rdl_xml"]
    m = re.search(r'<DataSet Name="Q_CHILD">.*?<CommandText>(.*?)</CommandText>',
                  rdl, re.S)
    assert m, "child dataset missing"
    cmd = m.group(1)
    assert re.search(r":K_Const IS NULL OR", cmd), (
        "K_Const correlation not widened — NULL bind would return 0 rows "
        "on the server:\n" + cmd)
    assert re.search(r":K_Sel IS NULL OR", cmd), (
        "K_Sel correlation not widened:\n" + cmd)


def test_lookup_uses_composite_key_when_multiple_correlations():
    rdl = convert(_LINKED_XML)["rdl_xml"]
    lookups = [l for l in re.findall(r"Lookup\([^)]*\)", rdl)
               if "Payload" in l]
    assert lookups, "no Lookup generated for the linked child payload"
    lk = lookups[0]
    # Both keys must participate in the join (composite "|" key), so a
    # constant first key can't collapse every row onto the first match.
    assert "K_Const" in lk and "K_Sel" in lk, (
        f"Lookup joins on a single key — wrong rows with constant keys: {lk}")
    assert '"|"' in lk or "&quot;|&quot;" in lk, (
        f"expected composite key separator in: {lk}")


def test_reconstruct_drops_shared_cv_builders_keeps_unique():
    """A cv* column-builder CONSTANT reused to build MORE THAN ONE lexical is a
    SHARED helper Oracle splices into DIFFERENT queries (e.g. :P_Criteria_A
    AND :P_Criteria_B from the same cvCOL set). Reconstructing one query's
    WHERE from it injects a column that won't resolve in the sibling query
    (ORA-01858/ORA-00904) -- which broke a permit certificate's date + permit
    prompts in production. Such predicates MUST be dropped (caller keeps the honest
    placeholder); a cv-constant UNIQUE to one lexical is provably that query's own
    filter and is kept. Regression for the permit-certificate defect 1+2 general fix."""
    from converter.generators.rdl import _reconstruct_lexical_criteria
    plsql = (
        "cvSHARED CONSTANT VARCHAR2(50) := 'P.Shared_Col';\n"
        "cvONLYA  CONSTANT VARCHAR2(50) := 'A.Only_Col';\n"
        ":P_Criteria_A := :P_Criteria_A || F_Criteria_Varchar2_Bind(pvColumn_1 => cvSHARED, pvBind_Variable => 'P_S');\n"
        ":P_Criteria_A := :P_Criteria_A || F_Criteria_Varchar2_Bind(pvColumn_1 => cvONLYA, pvBind_Variable => 'P_A');\n"
        ":P_Criteria_B := :P_Criteria_B || F_Criteria_Varchar2_Bind(pvColumn_1 => cvSHARED, pvBind_Variable => 'P_S');\n"
    )

    class _R:
        raw_xml = plsql
        triggers = []
        formulas = []

    cm = _reconstruct_lexical_criteria(_R())
    a = cm.get("P_CRITERIA_A", "")
    assert "A.Only_Col" in a, "unique-cv predicate must be kept (provably this query's filter)"
    assert "P.Shared_Col" not in a, "shared-cv predicate must be dropped (cross-query ORA-00904 risk)"
    assert "P_CRITERIA_B" not in cm, (
        "a wholly-shared builder must reconstruct to nothing -> caller keeps the honest comment"
    )

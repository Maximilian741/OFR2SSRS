"""A NON-SQL (text/CSV/XML pluggable) data source has columns but no query.
Its dataset must ship a HELPFUL commented CommandText scaffold (expected
columns + a starter SELECT + wiring instructions), not a bare
"-- empty query", so the user can wire it in Report Builder. A normal SQL
report keeps its real query untouched.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402

_PDS = b"""<?xml version="1.0"?><report name="PDS" DTDVersion="9.0.2.0.10"><data>
<dataSource name="QP_1"><select></select>
<group name="G"><dataItem name="CITY" datatype="vchar2"/>
<dataItem name="CAPITAL" datatype="vchar2"/></group></dataSource></data>
<layout><section name="main"><body width="8" height="9">
<repeatingFrame name="R_G" source="G"><geometryInfo x="0" y="0" width="6" height="0.3"/>
<field name="f_c" source="CITY"><geometryInfo x="0" y="0" width="3" height="0.2"/></field>
<field name="f_k" source="CAPITAL"><geometryInfo x="3" y="0" width="3" height="0.2"/></field>
</repeatingFrame></body></section></layout></report>"""

_SQL = _PDS.replace(b"<select></select>",
                    b"<select><![CDATA[SELECT city, capital FROM t]]></select>")


def _command_texts(rdl):
    return re.findall(r"<CommandText>(.*?)</CommandText>", rdl, re.S)


def test_non_sql_dataset_gets_helpful_scaffold():
    rdl = convert(_PDS)["rdl_xml"]
    cmds = _command_texts(rdl)
    assert cmds
    cmd = next((c for c in cmds if "NON-SQL" in c), "")
    assert cmd, cmds
    # names the expected columns + offers a starter SELECT, all commented
    assert "CITY" in cmd and "CAPITAL" in cmd
    assert "SELECT" in cmd
    assert all(ln.strip().startswith("--") for ln in cmd.splitlines() if ln.strip())
    # still a valid, READY report (empty dataset, but honest about it)
    assert convert(_PDS)["preflight"]["verdict"] in ("READY", "AMBER", "RED")


def test_real_sql_query_is_untouched():
    rdl = convert(_SQL)["rdl_xml"]
    cmds = _command_texts(rdl)
    assert any("select city, capital from t" in c.lower() for c in cmds)
    assert not any("NON-SQL" in c for c in cmds)

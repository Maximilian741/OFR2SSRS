"""
Shared test fixtures.

Tests should NEVER depend on files in samples/oracle/ (those may be cleared
or replaced). We build a synthetic Oracle Reports XML inline here so the
suite is fully self-contained.
"""
import sys
from pathlib import Path

import pytest

# Make `backend.converter` importable
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "backend"))


SYNTHETIC_XML = b"""<?xml version="1.0" encoding="WINDOWS-1252" ?>
<report name="TEST_REPORT" DTDVersion="9.0.2.0.10"
 afterParameterFormTrigger="after_param_form">
  <data>
    <userParameter name="P_REGION" datatype="character" width="50"
     precision="10" label="Region" defaultWidth="0" defaultHeight="0"/>
    <userParameter name="P_YEAR" datatype="number" width="4"
     precision="10" label="Year" defaultWidth="0" defaultHeight="0"/>
    <userParameter name="P_BEGIN_DATE" datatype="date" width="10"
     precision="10" inputMask="MM/DD/YYYY" label="Begin Date"
     defaultWidth="0" defaultHeight="0"/>
    <dataSource name="Q_MAIN">
      <select>
      <![CDATA[SELECT
    DECODE(:P_REGION, 'NORTH', 'N', 'SOUTH', 'S', 'OTHER') AS region_code,
    NVL(emp_name, 'unknown') AS name,
    TO_CHAR(hire_date, 'YYYY') AS hire_year,
    salary
FROM employees e, departments d
WHERE e.dept_id = d.dept_id(+)
  AND :P_YEAR = TO_CHAR(hire_date, 'YYYY')
ORDER BY 1]]>
      </select>
      <displayInfo x="0" y="0" width="1.5" height="0.5"/>
      <group name="G_MAIN">
        <displayInfo x="0" y="0" width="1.5" height="3"/>
        <dataItem name="region_code" datatype="vchar2" columnOrder="1"
         width="1" defaultWidth="100000" defaultHeight="10000"
         columnFlags="0" defaultLabel="Region Code">
          <dataDescriptor expression="DECODE(...)" descriptiveExpression="REGION_CODE"
           order="1" width="1"/>
          <dataItemPrivate adtName="" schemaName=""/>
        </dataItem>
        <dataItem name="name" datatype="vchar2" columnOrder="2" width="50"
         defaultWidth="100000" defaultHeight="10000" columnFlags="0"
         defaultLabel="Name">
          <dataDescriptor expression="NVL(emp_name)" descriptiveExpression="NAME"
           order="2" width="50"/>
          <dataItemPrivate adtName="" schemaName=""/>
        </dataItem>
        <dataItem name="hire_year" datatype="vchar2" columnOrder="3" width="4"
         defaultWidth="40000" defaultHeight="10000" columnFlags="0"
         defaultLabel="Hire Year">
          <dataDescriptor expression="TO_CHAR(hire_date)" descriptiveExpression="HIRE_YEAR"
           order="3" width="4"/>
          <dataItemPrivate adtName="" schemaName=""/>
        </dataItem>
      </group>
    </dataSource>
    <formula name="CF_Total_F" source="cf_total_f" datatype="number" width="22"
     precision="10" defaultWidth="120000" defaultHeight="10000" />
  </data>
  <programUnits>
    <programUnit name="cf_total_f" type="function">
      <textSource><![CDATA[FUNCTION CF_Total_F RETURN NUMBER IS
BEGIN
  RETURN(:salary * 1.0);
END;]]></textSource>
    </programUnit>
    <programUnit name="after_param_form" type="function">
      <textSource><![CDATA[FUNCTION After_Param_Form RETURN BOOLEAN IS
BEGIN
  RETURN(TRUE);
END;]]></textSource>
    </programUnit>
  </programUnits>
</report>"""


@pytest.fixture(scope="session")
def synthetic_xml_bytes():
    """Synthetic Oracle Reports XML — minimal but exercises the parser/translator/generator."""
    return SYNTHETIC_XML


@pytest.fixture(scope="session")
def parsed_report(synthetic_xml_bytes):
    """Parse the synthetic XML once and share the result across tests."""
    from converter.parsers.oracle_xml import parse_oracle_xml
    return parse_oracle_xml(synthetic_xml_bytes)


@pytest.fixture(scope="session")
def translated_report(parsed_report):
    """Run the translator on the parsed report (mutates in place)."""
    from converter.translators.plsql_to_tsql import translate_report
    translate_report(parsed_report)
    return parsed_report


@pytest.fixture(scope="session")
def samples_dir():
    return ROOT / "samples" / "oracle"

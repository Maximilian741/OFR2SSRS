"""F1: Oracle formatMask -> SSRS <Format>.

Oracle display masks (currency, dates, thousands) were dropped at parse time so
the RDL emitted no <Format> -- numbers/dates rendered in raw DB form. These
cover the mask translator and the central emission pass.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.generators.rdl import _oracle_mask_to_net, _spelled_case  # noqa: E402


@pytest.mark.parametrize("mask,want", [
    ("$NNN,NN0.00", "$###,##0.00"),
    # The UNITS digit is a required '0', not '#': Oracle '9'/'N' show "0" for a
    # zero value, while .NET '#' blanks it. So an all-9 integer mask keeps
    # leading '#' (suppress leading zeros) but a trailing '0' (show zero).
    ("999,999", "###,##0"),
    ("0.0000", "0.0000"),
    # Fractional 9s PAD trailing zeros (TO_CHAR(1230,'9,999.99')->"1,230.00");
    # only FM/FX suppresses them. So D99 -> ".00", not ".##".
    ("999G999D99", "###,##0.00"),
    ("FM999G999D99", "###,##0.##"),
    ("DD-MON-YYYY", "dd-MMM-yyyy"),
    ("MM/DD/YYYY", "MM/dd/yyyy"),
    ("MONTH DD, YYYY", "MMMM dd, yyyy"),
    ("HH24:MI:SS", "HH:mm:ss"),
    ("MM/DD/YYYY HH24:MI", "MM/dd/yyyy HH:mm"),
    # Oracle FM/FX fill-mode modifiers are stripped; A.M./P.M. -> tt
    ("FMMONTH dd, yyyy", "MMMM dd, yyyy"),
    ("FMMONTH dd, yyyy hh:FMMI P.M.", "MMMM dd, yyyy hh:mm tt"),
    ("FM999G990D00", "###,##0.00"),
])
def test_oracle_mask_to_net(mask, want):
    assert _oracle_mask_to_net(mask) == want


def test_unrecognized_mask_returns_empty():
    assert _oracle_mask_to_net("") == ""
    assert _oracle_mask_to_net("garbage") == ""


# Each row: (oracle_mask, net_format, [(value, oracle_documented_output), ...]).
# The net_format was rendered through the ACTUAL .NET formatter
# (Double.ToString(fmt, InvariantCulture)) and confirmed to equal Oracle's
# documented TO_CHAR output for every value below -- a true 1:1 check. Oracle's
# leading alignment SPACES (" 1,234") aren't reproducible in a .NET format
# string and SSRS handles alignment via the textbox, so we compare content.
_DOCUMENTED_1TO1 = [
    ("9999",        "###0",            [(-1234, "-1234"), (1234, "1234"), (0, "0")]),
    ("9,999.99",    "#,##0.00",        [(1210.73, "1,210.73"), (1230, "1,230.00")]),
    ("$9,999.00",   "$#,##0.00",       [(1210.73, "$1,210.73")]),
    ("000099",      "0000##",          [(21, "000021")]),
    ("9999.99",     "###0.00",         [(1.5, "1.50"), (0.5, "0.50")]),   # frac pads
    ("FM9999.99",   "###0.##",         [(1.5, "1.5")]),                    # FM suppresses
    ("999.99",      "##0.00",          [(0, "0.00")]),
    ("9999MI",      "###0;###0-",      [(-1234, "1234-"), (1234, "1234")]),
    ("9999PR",      "###0;<###0>",     [(-1234, "<1234>")]),
    ("S9999",       "+###0;-###0",     [(1234, "+1234"), (-1234, "-1234")]),
    ("9999S",       "###0+;###0-",     [(-1234, "1234-")]),
    ("$NNN,NN0.00", "$###,##0.00",     [(1234.5, "$1,234.50")]),
    ("999%",        r"##0\%",          [(50, "50%")]),                     # literal %
    # EEEE scientific notation: mantissa normalized to 1 digit, decimals set
    # mantissa precision, EEEE -> exponent.
    ("9.9999EEEE",  "0.0000E+00",      [(123456789, "1.2346E+08")]),
    ("9.99EEEE",    "0.00E+00",        [(1234.5, "1.23E+03"), (0.000123, "1.23E-04")]),
]


@pytest.mark.parametrize("mask,net,_samples", _DOCUMENTED_1TO1)
def test_documented_oracle_masks_translate_to_verified_net_format(mask, net, _samples):
    """1:1: the translator must emit the .NET format that was independently
    confirmed (via the real .NET formatter) to reproduce Oracle's documented
    TO_CHAR output -- covering fraction zero-padding, FM suppression, the
    units-zero rule, and MI/PR/S negative sections."""
    assert _oracle_mask_to_net(mask) == net


@pytest.mark.parametrize("mask,want", [
    ("999%", r"##0\%"),
    ("990.99%", r"##0.00\%"),
])
def test_percent_is_escaped_not_scaled(mask, want):
    """Oracle '%' is a LITERAL percent sign (Oracle's x100/scaling element is
    'V', and '%' isn't even in Oracle's number-format token list -- verified
    against the Oracle number-format docs). .NET's BARE '%' multiplies the
    value by 100, so an unescaped passthrough turns "50%" into "5000%". The
    translator must emit an escaped '\\%' so SSRS renders a literal percent
    with no scaling."""
    net = _oracle_mask_to_net(mask)
    assert net == want, net
    # no bare (unescaped) '%' may survive -- that is the x100 trigger
    bare = any(net[i] == "%" and (i == 0 or net[i - 1] != "\\")
               for i in range(len(net)))
    assert not bare, f"{mask} -> {net!r} has an unescaped % (scales x100)"


@pytest.mark.parametrize("mask", ["9999", "NNNNNN", "9G999G999", "-9999",
                                  "999D00", "999D99"])
def test_all_nine_mask_keeps_a_required_zero_so_zero_is_not_blank(mask):
    """Regression: an all-9/all-N integer mask (the default numeric format in
    Oracle Reports) used to translate to an all-'#' .NET format, which renders
    a ZERO value as an empty string -- Oracle shows "0". Every numeric mask
    must keep at least one required '0' in its integer part so zeros display."""
    net = _oracle_mask_to_net(mask)
    assert net, mask
    head = net.partition(".")[0]
    assert "0" in head, f"{mask} -> {net!r} blanks zero (no required digit)"


_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<report name="PRICED_LIST" DTDVersion="9.0.2.0.10">
  <data><dataSource name="Q">
    <select canParse="no"><![CDATA[SELECT SKU, PRICE, ORDER_DT FROM T]]></select>
    <group name="G">
      <dataItem name="SKU" oracleDatatype="number" defaultLabel="Sku" breakOrder="none"><dataDescriptor expression="SKU"/></dataItem>
      <dataItem name="PRICE" oracleDatatype="number" defaultLabel="Price" breakOrder="none"><dataDescriptor expression="PRICE" precision="10" scale="2"/></dataItem>
      <dataItem name="ORDER_DT" datatype="date" defaultLabel="Order Date" breakOrder="none"><dataDescriptor expression="ORDER_DT"/></dataItem>
    </group>
  </dataSource></data>
  <layout><section name="main" width="11" height="8.5">
    <body width="10" height="7"><location x="0.3" y="0.7"/>
      <repeatingFrame name="R_G" source="G" printDirection="down">
        <geometryInfo x="0" y="0.4" width="10" height="0.2"/>
        <field name="F_SKU" source="SKU"><font face="Arial" size="10"/><geometryInfo x="0" y="0.4" width="1.5" height="0.18"/></field>
        <field name="F_PRICE" source="PRICE" formatMask="$NNN,NN0.00" alignment="end"><font face="Arial" size="10"/><geometryInfo x="1.6" y="0.4" width="1.5" height="0.18"/></field>
        <field name="F_DT" source="ORDER_DT" formatMask="MM/DD/YYYY"><font face="Arial" size="10"/><geometryInfo x="3.2" y="0.4" width="1.5" height="0.18"/></field>
      </repeatingFrame>
      <frame name="HDR"><geometryInfo x="0" y="0" width="10" height="0.38"/>
        <visualSettings fillPattern="solid" fillForegroundColor="darkblue" lineForegroundColor="white"/>
        <text name="B_SKU"><geometryInfo x="0" y="0" width="1.5" height="0.17"/><textSegment><font face="Arial" size="10" bold="yes"/><string><![CDATA[SKU]]></string></textSegment></text>
        <text name="B_PRICE"><geometryInfo x="1.6" y="0" width="1.5" height="0.17"/><textSegment><font face="Arial" size="10" bold="yes"/><string><![CDATA[Price]]></string></textSegment></text>
        <text name="B_DT"><geometryInfo x="3.2" y="0" width="1.5" height="0.17"/><textSegment><font face="Arial" size="10" bold="yes"/><string><![CDATA[Order Date]]></string></textSegment></text>
      </frame>
    </body>
    <margin><text name="T"><textSettings justify="center"/><geometryInfo x="3" y="0.25" width="4" height="0.2"/><textSegment><font face="Arial" size="12" bold="yes"/><string><![CDATA[Priced List]]></string></textSegment></text></margin>
  </section></layout>
</report>"""


def test_format_emitted_on_masked_field_values():
    rdl = convert(_XML)["rdl_xml"]
    # currency + date masks land as <Format> next to their field value
    assert re.search(r"Fields!PRICE\.Value.*?<Format>\$###,##0\.00</Format>", rdl, re.DOTALL)
    assert re.search(r"Fields!ORDER_DT\.Value.*?<Format>MM/dd/yyyy</Format>", rdl, re.DOTALL)


def test_no_format_on_unmasked_field():
    rdl = convert(_XML)["rdl_xml"]
    # SKU has no formatMask -> exactly two <Format> elements total (PRICE, DT)
    assert len(re.findall(r"<Format>", rdl)) == 2


@pytest.mark.parametrize("mask,net,want", [
    ("DD-MON-YYYY",   "dd-MMM-yyyy",   "U"),   # uppercase MON
    ("DD-Mon-YYYY",   "dd-MMM-yyyy",   ""),    # proper -> .NET default matches
    ("dd-mon-yyyy",   "dd-MMM-yyyy",   "L"),   # lowercase
    ("MONTH DD, YYYY", "MMMM dd, yyyy", "U"),
    ("DAY",           "dddd",          "U"),
    ("MM/DD/YYYY",    "MM/dd/yyyy",    ""),    # digits only -> no spelled name
    ("MON DD HH24:MI", "MMM dd HH:mm", ""),    # spelled + TIME -> unsafe, skip
])
def test_spelled_date_case_detection(mask, net, want):
    """Oracle spells month/day in the format model's case (MON->JAN). .NET
    can only render proper case, so UPPER/lower need a value-expr wrap -- but
    only when it's safe (a spelled NAME and NO time token, so VB Format() is
    unambiguous)."""
    assert _spelled_case(mask, net) == want


_DATE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<report name="DATES" DTDVersion="9.0.2.0.10"><data>
  <dataSource name="Q"><select><![CDATA[SELECT d1,d2,d3 FROM t]]></select>
    <group name="G">
      <dataItem name="D1" datatype="date"/>
      <dataItem name="D2" datatype="date"/>
      <dataItem name="D3" datatype="date"/>
    </group></dataSource></data>
  <layout><section name="main"><body width="8" height="9">
    <repeatingFrame name="R" source="G"><geometryInfo x="0" y="0" width="7" height="0.3"/>
      <field name="F1" source="D1" formatMask="DD-MON-YYYY"><geometryInfo x="0" y="0" width="2" height="0.2"/></field>
      <field name="F2" source="D2" formatMask="DD-Mon-YYYY"><geometryInfo x="2" y="0" width="2" height="0.2"/></field>
      <field name="F3" source="D3" formatMask="MM/DD/YYYY"><geometryInfo x="4" y="0" width="2" height="0.2"/></field>
    </repeatingFrame></body></section></layout></report>"""


def test_uppercase_date_mask_wraps_value_in_ucase():
    """A .NET <Format> can't force case, so an UPPER spelled date mask
    (DD-MON-YYYY, the DEFAULT Oracle date format) must wrap the value in
    UCase(Format(..)). Proper-case + digit masks keep the plain field ref +
    <Format>. (Render-verified through the real MS engine: D1 -> '10-JAN-2026',
    D2 -> '10-Jan-2026'.)"""
    rdl = convert(_DATE_XML)["rdl_xml"]
    # D1 (uppercase) -> UCase wrap, no <Format>
    assert re.search(
        r'<Value>=UCase\(Format\(Fields!D1\.Value, "dd-MMM-yyyy"\)\)</Value>', rdl)
    # D2 (proper) -> plain field ref + <Format>
    assert re.search(r'<Value>=Fields!D2\.Value</Value>', rdl)
    # D3 (digits) -> plain field ref (no case wrap)
    assert re.search(r'<Value>=Fields!D3\.Value</Value>', rdl)
    # no UCase on the proper/digit fields
    assert "UCase(Format(Fields!D2" not in rdl
    assert "UCase(Format(Fields!D3" not in rdl

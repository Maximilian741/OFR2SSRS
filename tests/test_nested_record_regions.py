"""A repeating frame declared INSIDE a record frame is a NESTED REGION.

Oracle nests a child repeating frame inside the record frame (an
organisation/affiliation block declared inside a site record). It prints
inside every parent instance, ABOVE the rule that closes that instance.

The converter used to drop it from the record and let the report-end
breakdown fallback pick it up, so the child rows printed once, after the
whole body, BELOW the group's closing rule, unlinked from the parent they
belong to. These tests pin the declared behaviour:

  * the region's members render inside the record's own rectangle, at their
    DECLARED offsets from the detail band, correlated to the parent row;
  * the record row is tall enough to contain the region;
  * the report-end fallback does NOT also fire for it;
  * every declared rule still renders, none invented;
  * a MARGIN-declared field (page chrome) never joins a body record band;
  * a nested frame with NO resolvable correlation still reaches the
    fallback -- misplaced beats missing.
"""
import re
import xml.etree.ElementTree as ET

import pytest

from converter import convert

NS = {"r": "http://schemas.microsoft.com/sqlserver/reporting/"
            "2008/01/reportdefinition"}


def _q(tag):
    return f"{{{NS['r']}}}{tag}"


def _fixture(linked=True):
    """A County -> Site record list with an Org block declared INSIDE the
    site record, an inline role list inside that, both declared rules, and
    a margin-declared subtitle. ``linked=False`` removes the Oracle <link>
    and its correlation bind, so no key can be derived."""
    child_sql = ("select site_id, org_nm, org_addr from o"
                 + (" where (:SITE_ID is null or o.site_id = :SITE_ID)"
                    if linked else ""))
    link = ('<link parentGroup="G_Site" childQuery="Q_Org" condition="eq" '
            'sqlClause="where"/>') if linked else ""
    return (
        '<?xml version="1.0"?><report name="NR_T" DTDVersion="9.0.2.0.10">'
        '<data>'
        '<userParameter name="P_SUBTITLE" datatype="character" width="2000" '
        'label="P Subtitle" display="no"/>'
        '<dataSource name="Q_Main"><select><![CDATA[select cnty, site_nm, '
        'site_id from s]]></select>'
        '<group name="G_County"><dataItem name="CNTY" datatype="vchar2"/>'
        '</group>'
        '<group name="G_Site">'
        '<dataItem name="SITE_NM" datatype="vchar2"/>'
        '<dataItem name="SITE_ID" datatype="number"/>'
        '<dataItem name="SITE_ADDR" datatype="vchar2"/>'
        '<dataItem name="PERMIT" datatype="vchar2"/>'
        '<dataItem name="STATUS" datatype="vchar2"/>'
        '</group></dataSource>'
        f'<dataSource name="Q_Org"><select><![CDATA[{child_sql}]]></select>'
        '<group name="G_Org">'
        '<dataItem name="SITE_ID" datatype="number"/>'
        '<dataItem name="ORG_NM" datatype="vchar2"/>'
        '<dataItem name="ORG_ADDR" datatype="vchar2"/>'
        '<dataItem name="ROLE_DESC" datatype="vchar2"/>'
        '</group>'
        '<group name="G_Role"><dataItem name="ROLE_DESC" datatype="vchar2"/>'
        '</group></dataSource>'
        f'{link}'
        '</data>'
        '<layout><section name="main"><body height="9.6">'
        '<repeatingFrame name="R_County" source="G_County" '
        'printDirection="down">'
        '<geometryInfo x="0" y="0" width="7.5" height="1.7"/>'
        '<field name="F_CNTY" source="CNTY">'
        '<geometryInfo x="0.0" y="0.05" width="2.0" height="0.2"/></field>'
        '<repeatingFrame name="R_Site" source="G_Site" printDirection="down">'
        '<geometryInfo x="0.1" y="0.3" width="7.4" height="1.4"/>'
        '<field name="F_SITE" source="SITE_NM">'
        '<geometryInfo x="0.15" y="0.35" width="3.0" height="0.2"/></field>'
        '<field name="F_ADDR" source="SITE_ADDR">'
        '<geometryInfo x="0.15" y="0.6" width="3.0" height="0.2"/></field>'
        '<field name="F_PERMIT" source="PERMIT">'
        '<geometryInfo x="4.5" y="0.35" width="0.8" height="0.2"/></field>'
        '<field name="F_STATUS" source="STATUS">'
        '<geometryInfo x="5.5" y="0.35" width="1.9" height="0.2"/></field>'
        '<repeatingFrame name="R_Org" source="G_Org" printDirection="down">'
        '<geometryInfo x="0.2" y="1.05" width="7.2" height="0.6"/>'
        '<field name="F_ORG" source="ORG_NM">'
        '<geometryInfo x="1.3" y="1.05" width="3.0" height="0.2"/></field>'
        '<field name="F_OADDR" source="ORG_ADDR">'
        '<geometryInfo x="1.3" y="1.3" width="3.0" height="0.35"/></field>'
        '<repeatingFrame name="R_Role" source="G_Role" '
        'printDirection="down">'
        '<geometryInfo x="0.3" y="1.05" width="0.9" height="0.19"/>'
        '<field name="F_ROLE" source="ROLE_DESC">'
        '<geometryInfo x="0.3" y="1.05" width="0.85" height="0.19"/></field>'
        '<text name="B_COMMA"><geometryInfo x="1.19" y="1.06" width="0.01" '
        'height="0.17"/><textSegment><string><![CDATA[,]]></string>'
        '</textSegment></text>'
        '</repeatingFrame></repeatingFrame>'
        '<line name="B_SITE_RULE"><geometryInfo x="0.15" y="0.32" '
        'width="7.3" height="0"/>'
        '<visualSettings lineWidth="1" linePattern="solid"/>'
        '<points><point x="0.15" y="0.32"/><point x="7.45" y="0.32"/>'
        '</points></line>'
        '</repeatingFrame></repeatingFrame>'
        '<line name="B_REPORT_RULE"><geometryInfo x="0.01" y="1.74" '
        'width="7.46" height="0"/>'
        '<visualSettings lineWidth="2" linePattern="solid"/>'
        '<points><point x="0.01" y="1.74"/><point x="7.47" y="1.74"/>'
        '</points></line>'
        '</body>'
        '<margin><text name="B_TITLE">'
        '<geometryInfo x="3.0" y="0.23" width="2.5" height="0.17"/>'
        '<textSegment><string><![CDATA[Nested Region Probe]]></string>'
        '</textSegment></text>'
        '<field name="F_SUB" source="P_SUBTITLE">'
        '<geometryInfo x="0.49" y="0.5" width="7.5" height="0.42"/>'
        '<advancedLayout printObjectOnPage="allPage" '
        'basePrintingOn="enclosingObject"/></field>'
        '</margin>'
        '</section></layout></report>'
    )


def _boxes(el):
    """{textbox name: (value, top_in, left_in, width_in, height_in)}."""
    out = {}
    for tb in el.iter(_q("Textbox")):
        val = ""
        for v in tb.iter(_q("Value")):
            val = v.text or ""
            break

        def _n(tag):
            t = tb.findtext(_q(tag)) or ""
            try:
                return float(t.replace("in", ""))
            except ValueError:
                return None
        out[tb.get("Name")] = (val, _n("Top"), _n("Left"), _n("Width"),
                               _n("Height"))
    return out


@pytest.fixture(scope="module")
def nested_rdl():
    return convert(_fixture().encode())["rdl_xml"]


def _record_rect(root):
    for rect in root.iter(_q("Rectangle")):
        if rect.get("Name") == "ND_Detail":
            return rect
    raise AssertionError("the record rectangle was not emitted at all")


def _by_left(boxes, left):
    """The region box at a DECLARED x (never by which column its expression
    mentions -- the break key names the parent columns in EVERY box)."""
    hit = [v for v in boxes.values()
           if v[2] is not None and abs(v[2] - left) <= 0.005]
    assert len(hit) == 1, f"expected one box at x={left}in, got {hit}"
    return hit[0]


def _fold_args(val):
    """The Code.NDBreakBlock arguments, split at TOP-LEVEL commas."""
    m = re.match(r'=Code\.NDBreakBlock\((.*)\)$', val, re.S)
    assert m, f"the region must fold on its declared break group: {val!r}"
    depth = 0
    args, cur = [], ""
    for ch in m.group(1):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            args.append(cur.strip())
            cur = ""
            continue
        cur += ch
    args.append(cur.strip())
    assert len(args) == 8, args
    return args


def test_nested_region_renders_inside_the_record(nested_rdl):
    root = ET.fromstring(nested_rdl)
    inside = _boxes(_record_rect(root))
    # every declared member of the nested region prints inside the record,
    # correlated to THIS parent row (a nested data region may not carry its
    # own DataSetName in RDL, so the rows arrive by the linked-child
    # LookupSet the generator already uses).
    for col in ("ORG_NM", "ORG_ADDR", "ROLE_DESC"):
        hit = [n for n, (v, *_r) in inside.items()
               if f"Fields!{col}.Value" in v]
        assert hit, f"nested region member {col} never reached the record"
    # STRICTER than the Join(LookupSet(..)) shape this replaces: EVERY
    # Fields! reference in EVERY region box -- break key, break-level lines
    # and per-row lines alike -- must sit inside a LookupSet over the
    # declared link key, so nothing can print a row of another parent.
    boxes = {n: v for n, v in inside.items() if "Q_Org" in (v[0] or "")}
    for name, (val, *_r) in boxes.items():
        outside = re.sub(
            r'LookupSet\(Fields!SITE_ID\.Value, Fields!SITE_ID\.Value, '
            r'[^()]*(?:\([^()]*\)[^()]*)*, "Q_Org"\)', "", val)
        assert "Fields!" not in outside, (
            f"{name} references a field outside the correlated lookup: "
            f"{outside!r}")


def test_nested_region_repeats_once_per_declared_break_group(nested_rdl):
    """ONE BLOCK PER DECLARED BREAK GROUP, at the declared pitch.

    Replaces an assertion that the block repeats once per CHILD ROW. That
    dialect is disproven by the source's own declaration and by the truth
    render: the child dataSource declares G_Org ABOVE G_Role, so its rows are
    finer than the region -- an organisation with four affiliation rows
    printed FOUR blocks, each repeating the same organisation, where the
    truth prints ONE block with the four affiliations stacked inside it
    (front-end truth, 4-row / 2-row / 2-row organisations on one site).
    The replacement is stricter: it pins the fold arguments, so the
    break-level columns can only be emitted once per break.
    """
    root = ET.fromstring(nested_rdl)
    inside = _boxes(_record_rect(root))
    boxes = {n: v for n, v in inside.items() if "Q_Org" in (v[0] or "")}
    assert len(boxes) == 2, (
        f"one box per DECLARED x-column of the region, got {sorted(boxes)}")
    org = _by_left(boxes, 1.30)
    role = _by_left(boxes, 0.30)
    # every column anchors at the region's declared top (band anchor 0.35,
    # region declared at y=1.05): a lower band cannot hold a declared Top of
    # its own once the box above it grows.
    assert org[1] == pytest.approx(0.70, abs=0.005)
    assert role[1] == pytest.approx(0.70, abs=0.005)
    # declared x/width are still the x/width -- no re-flow to invented columns
    assert org[3] == pytest.approx(3.00, abs=0.005)
    assert role[3] == pytest.approx(0.85, abs=0.005)
    org_a = _fold_args(org[0])
    role_a = _fold_args(role[0])
    # the BREAK KEY is the declared G_Org level and nothing finer: ROLE_DESC
    # is declared in G_Role, so keying on it would break on every row and
    # bring the per-child-row defect straight back.
    for a in (org_a[0], role_a[0]):
        assert "Fields!ORG_NM.Value" in a and "Fields!ORG_ADDR.Value" in a, a
        assert "Fields!ROLE_DESC.Value" not in a, a
    # break-level columns ride the ONCE argument (printed once per block);
    # the deeper frame's column rides the EACH argument (one entry per row).
    assert "Fields!ORG_NM.Value" in org_a[1], org_a[1]
    assert "Fields!ORG_ADDR.Value" in org_a[1], org_a[1]
    assert org_a[2] == "Nothing", org_a[2]
    assert role_a[1] == "Nothing", role_a[1]
    assert "Fields!ROLE_DESC.Value" in role_a[2], role_a[2]
    # the DECLARED band offset inside a block survives as a LINE position:
    # the address is declared 0.25in under the name, which is 2 lines of the
    # 10pt text the engine actually sets (11.16pt/line), so exactly one blank
    # line stands between them in the same box.
    assert re.search(r'Fields!ORG_NM\.Value & vbCrLf & "" & vbCrLf & '
                     r'Fields!ORG_ADDR\.Value', org_a[1]), org_a[1]
    # the block's own geometry goes over in DECLARED INCHES: frame body
    # 0.60in, no declared gutter, the deeper frame at the region top with a
    # declared 0.19in pitch. The engine ignores <LineHeight>, so each column
    # also carries the line height of ITS OWN declared font.
    for a in (org_a, role_a):
        assert [float(x) for x in a[3:7]] == [0.60, 0.0, 0.0, 0.19], a
        assert float(a[7]) == pytest.approx(10 * 1.116 / 72.0, abs=5e-4), a
    # the declared punctuation of the inline list rides on the VALUE, never on
    # the separator -- as a separator it landed on the block's trailing blank
    # line and stretched every instance by one line (engine-measured).
    assert 'Fields!ROLE_DESC.Value & ","' in role_a[2], role_a[2]


def test_break_fold_reducer_is_declared_in_the_report_code(nested_rdl):
    """A =Code.X call the report never declares is an #Error at run time."""
    root = ET.fromstring(nested_rdl)
    code = root.find(_q("Code"))
    assert code is not None and "Function NDBreakBlock" in (code.text or ""), (
        "the fold reducer must be declared in the report's <Code> block")


def test_record_row_contains_the_region_and_precedes_the_closing_rule(
        nested_rdl):
    root = ET.fromstring(nested_rdl)
    tablix = next(t for t in root.iter(_q("Tablix"))
                  if t.get("Name") == "Tablix_Nested")
    rows = list(tablix.find(_q("TablixBody")).find(_q("TablixRows")))
    det_i = rule_i = None
    det_h = 0.0
    for i, row in enumerate(rows):
        names = {e.get("Name") for e in row.iter() if e.get("Name")}
        if "ND_Detail" in names:
            det_i = i
            det_h = float((row.findtext(_q("Height")) or "0")
                          .replace("in", ""))
        if "Rule_B_REPORT_RULE" in names:
            rule_i = i
    assert det_i is not None and rule_i is not None
    assert det_i < rule_i, (
        "the nested region's record row must print BEFORE the rule that "
        "closes the group -- content under that rule was the defect")
    # the row must be tall enough to hold the region (declared y 1.05 +
    # height 0.60, measured from the band anchor 0.35)
    assert det_h >= 1.30 - 0.005, (
        f"record row {det_h}in cannot contain the declared region")


def test_declared_nesting_does_not_also_detach_as_a_report_end_table(
        nested_rdl):
    assert "Tablix_Breakdown_" not in nested_rdl, (
        "a frame declared inside a rendered record must not ALSO print as "
        "a sibling table after the body")
    root = ET.fromstring(nested_rdl)
    body = root.find(_q("Body"))
    tablixes = [t.get("Name") for t in body.find(_q("ReportItems"))
                if t.tag == _q("Tablix")]
    assert tablixes == ["Tablix_Nested"], (
        f"the body must hold ONE data region, got {tablixes}")


def test_every_declared_rule_survives_and_none_is_invented(nested_rdl):
    root = ET.fromstring(nested_rdl)
    lines = sorted(ln.get("Name") for ln in root.iter(_q("Line")))
    assert lines == ["Rule_B_REPORT_RULE", "Rule_B_SITE_RULE"], (
        f"declared rules changed: {lines}")


def test_margin_declared_field_never_duplicates_into_a_record_band(
        nested_rdl):
    root = ET.fromstring(nested_rdl)
    # page chrome must appear NOWHERE inside the body's data region
    tablix = next(t for t in root.iter(_q("Tablix"))
                  if t.get("Name") == "Tablix_Nested")
    dupes = [n for n, (v, *_r) in _boxes(tablix).items()
             if "P_SUBTITLE" in (v or "")]
    assert not dupes, (
        f"margin page chrome duplicated into the record band: {dupes}")
    # ... and the page-chrome copy is the correct one, so it must remain
    chrome = [n for n in _boxes(root) if n.startswith("MChrome_")
              and n.endswith("F_SUB")]
    assert chrome, "the margin subtitle lost its page-chrome copy"


def test_uncorrelated_nested_frame_still_reaches_the_fallback():
    """PROVE THE GATE CANNOT EAT CONTENT: with no <link> and no correlation
    bind the record builder cannot place the frame, so the report-end
    fallback must still render it. Misplaced beats missing."""
    rdl = convert(_fixture(linked=False).encode())["rdl_xml"]
    assert "Tablix_Breakdown_" in rdl, (
        "an uncorrelated nested frame must not vanish")

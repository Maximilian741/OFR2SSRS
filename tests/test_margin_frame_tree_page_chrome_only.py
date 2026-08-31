"""MARGIN-RESIDENT FRAME TREE = PAGE CHROME ONLY, never body content.

Wild-corpus defect class (P0-4, 2026-08-28): a report may author its whole
running master header INSIDE the main section's ``<margin>`` band — frames,
fields, even a repeatingFrame bound to a secondary group. Margin objects are
page furniture printed once per page in PAPER coordinates; the RDL page bands
already carry them (``_margin_page_chrome`` / ``_emit_margin_chrome``). The
body/record walks re-emitted the same declared subtree into the Body — the
engine then painted a second, offset copy of every margin object on every
page (measured: 9 painted-over pairs across 3 pages on one wild report), and
the breakdown-rescue pass re-emitted a margin repeatingFrame's dataset as a
spurious body tablix on its sibling.

The rule is structural and declaration-driven: the parser tags every object
authored inside ``<margin>`` (fields AND groups), and every body walk must
exclude such subtrees via ``_is_margin_resident_group``. No names, no
report-specific tokens.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.parsers.oracle_xml import parse_oracle_xml  # noqa: E402

NS = ("{http://schemas.microsoft.com/sqlserver/reporting/"
      "2008/01/reportdefinition}")

LABEL = "CHROMEBANDLABEL"


def _margin_tree(y0: float) -> str:
    """A margin band holding a frame > repeatingFrame > text+field tree —
    the running master-header idiom, declared as page chrome."""
    return (
        '<frame name="M_CHROME_WRAP">'
        f'<geometryInfo x="0.00000" y="{y0:.5f}" width="8.00000" '
        'height="0.60000"/>'
        '<repeatingFrame name="R_CHROME" source="G_CHROME" '
        'printDirection="down">'
        f'<geometryInfo x="0.00000" y="{y0 + 0.05:.5f}" width="8.00000" '
        'height="0.50000"/>'
        '<text name="B_CHROME_LBL">'
        f'<geometryInfo x="3.50000" y="{y0 + 0.10:.5f}" width="2.00000" '
        'height="0.20000"/>'
        '<textSegment><font face="Arial" size="9"/>'
        f'<string><![CDATA[{LABEL}]]></string>'
        '</textSegment></text>'
        '<field name="F_RUN_BY" source="RUN_BY">'
        f'<geometryInfo x="0.30000" y="{y0 + 0.10:.5f}" width="2.00000" '
        'height="0.20000"/>'
        '</field>'
        '</repeatingFrame></frame>'
    )


_CHROME_DS = (
    '<dataSource name="Q_CHROME">'
    '<select><![CDATA[select run_by from dual]]></select>'
    '<group name="G_CHROME"><dataItem name="RUN_BY" datatype="vchar2"/>'
    '</group></dataSource>'
)


def _letter_xml() -> bytes:
    """Per-record letter (record-frame walk) + the margin master tree."""
    return (
        '<?xml version="1.0"?>'
        '<report name="MARGIN_TREE_DOC" DTDVersion="9.0.2.0.10">'
        '<data><dataSource name="Q_DOC">'
        '<select><![CDATA[select acct_no, addressee from notices]]></select>'
        '<group name="G_DOC">'
        '<dataItem name="ACCT_NO" datatype="vchar2"/>'
        '<dataItem name="ADDRESSEE" datatype="vchar2"/>'
        '</group></dataSource>'
        f'{_CHROME_DS}</data>'
        '<layout><section name="main" width="8.50000">'
        '<body width="7.50000" height="10.60000">'
        '<repeatingFrame name="R_DOC" source="G_DOC" printDirection="down" '
        'maxRecordsPerPage="1" minWidowRecords="1" columnMode="no">'
        '<geometryInfo x="0.00000" y="0.00000" width="7.50000" '
        'height="5.00000"/>'
        '<generalLayout verticalElasticity="variable"/>'
        '<field name="F_ADDR" source="ADDRESSEE">'
        '<geometryInfo x="0.30000" y="0.30000" width="4.00000" '
        'height="0.20000"/></field>'
        '<field name="F_ACCT" source="ACCT_NO">'
        '<geometryInfo x="0.30000" y="0.70000" width="4.00000" '
        'height="0.20000"/></field>'
        '</repeatingFrame></body>'
        f'<margin>{_margin_tree(10.00)}</margin>'
        '</section></layout></report>'
    ).encode()


def _tabular_xml() -> bytes:
    """Tabular list (breakdown-rescue territory) + the margin master tree.
    The margin repeatingFrame's dataset is rendered by NO body region, which
    is exactly the shape the breakdown pass rescues — it must not, because
    the subtree is declared page chrome."""
    return (
        '<?xml version="1.0"?>'
        '<report name="MARGIN_TREE_TAB" DTDVersion="9.0.2.0.10">'
        '<data><dataSource name="Q_MAIN">'
        '<select><![CDATA[select item_cd, item_desc, qty from items]]>'
        '</select>'
        '<group name="G_MAIN">'
        '<dataItem name="ITEM_CD" datatype="vchar2"/>'
        '<dataItem name="ITEM_DESC" datatype="vchar2"/>'
        '<dataItem name="QTY" datatype="number"/>'
        '</group></dataSource>'
        f'{_CHROME_DS}</data>'
        '<layout><section name="main" width="8.50000">'
        '<body width="7.50000" height="10.00000">'
        '<repeatingFrame name="R_MAIN" source="G_MAIN" '
        'printDirection="down">'
        '<geometryInfo x="0.00000" y="0.50000" width="7.50000" '
        'height="0.25000"/>'
        '<field name="F_CD" source="ITEM_CD">'
        '<geometryInfo x="0.10000" y="0.52000" width="1.50000" '
        'height="0.20000"/></field>'
        '<field name="F_DESC" source="ITEM_DESC">'
        '<geometryInfo x="1.80000" y="0.52000" width="3.50000" '
        'height="0.20000"/></field>'
        '<field name="F_QTY" source="QTY">'
        '<geometryInfo x="5.50000" y="0.52000" width="1.00000" '
        'height="0.20000"/></field>'
        '</repeatingFrame></body>'
        f'<margin>{_margin_tree(0.10)}</margin>'
        '</section></layout></report>'
    ).encode()


def _split(rdl: str):
    """(body_xml_string, page_bands_xml_string) for containment checks."""
    root = ET.fromstring(rdl)
    body = root.find(".//" + NS + "Body")
    bands = [el for tag in ("PageHeader", "PageFooter")
             for el in root.iter(NS + tag)]
    to_s = lambda el: ET.tostring(el, encoding="unicode")  # noqa: E731
    return to_s(body), "".join(to_s(b) for b in bands)


def test_parser_tags_margin_frame_tree_groups():
    """The discriminator is really declared: GROUPS authored inside <margin>
    are tagged in_margin (not only their leaf fields), and body-declared
    groups stay clear."""
    rep = parse_oracle_xml(_letter_xml())
    flags = {}

    def walk(node):
        flags[getattr(node, "name", "")] = bool(
            getattr(node, "in_margin", False))
        for c in (getattr(node, "children", None) or []):
            walk(c)
    for s in rep.layout:
        walk(s)
    assert flags.get("M_CHROME_WRAP") is True, flags
    assert flags.get("R_CHROME") is True, flags
    assert flags.get("R_DOC") is False, flags


def test_margin_resident_group_helper_reads_both_signals():
    """_is_margin_resident_group honours the group tag AND falls back to the
    all-fields-tagged shape (a dialect whose frame carries no name attribute
    never reaches the name-keyed parser pass)."""
    from converter.generators import rdl as R
    from converter.models import LayoutField, LayoutGroup

    tagged = LayoutGroup(name="M_X", kind="frame", in_margin=True)
    assert R._is_margin_resident_group(tagged) is True

    untagged = LayoutGroup(name="", kind="frame")
    untagged.fields.append(LayoutField(name="B_X", kind="text", text="t"))
    untagged.fields[0].in_margin = True
    assert R._is_margin_resident_group(untagged) is True

    body_grp = LayoutGroup(name="M_Y", kind="frame")
    body_grp.fields.append(LayoutField(name="B_Y", kind="text", text="t"))
    assert R._is_margin_resident_group(body_grp) is False


def test_margin_frame_tree_stays_out_of_the_record_body():
    """Per-record path: the margin master tree's declared text must appear in
    a page band (chrome preserved — dropping it everywhere is the module-stamp
    regression) and NEVER inside the Body (the second, offset copy)."""
    body, bands = _split(convert(_letter_xml())["rdl_xml"])
    assert LABEL not in body, (
        "margin-declared frame tree leaked into the record body")
    assert bands.count(LABEL) == 1, (
        f"margin chrome must print once via the page bands, "
        f"found {bands.count(LABEL)}")


def test_margin_repeating_frame_is_not_rescued_as_a_body_breakdown():
    """Tabular path: a repeatingFrame declared inside <margin> binds a dataset
    no body region renders — the breakdown-rescue pass must NOT re-emit it as
    a body tablix (it re-painted the margin furniture into the body)."""
    rdl = convert(_tabular_xml())["rdl_xml"]
    body, bands = _split(rdl)
    root = ET.fromstring(rdl)
    body_el = root.find(".//" + NS + "Body")
    body_ds = {t.findtext(NS + "DataSetName") or ""
               for t in body_el.iter(NS + "Tablix")}
    assert "Q_CHROME" not in body_ds, (
        f"margin repeatingFrame's dataset re-emitted as a body region: "
        f"{sorted(body_ds)}")
    assert LABEL not in body, (
        "margin-declared boilerplate leaked into the tabular body")
    assert bands.count(LABEL) == 1, (
        f"margin chrome must print once via the page bands, "
        f"found {bands.count(LABEL)}")

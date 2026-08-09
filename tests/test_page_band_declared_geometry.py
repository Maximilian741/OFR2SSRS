"""Page header/footer band height comes from DECLARED chrome geometry.

Oracle authors page furniture in a section ``<margin>`` band, in PAPER
coordinates. The RDL page's TopMargin IS that band's own top offset, so
``TopMargin + PageHeader Height`` is exactly where SSRS starts the body --
and it must land on the declared chrome BOTTOM (or on the declared body
origin, when the source states one, which is never above the chrome).

Any slack added there is not cosmetic: it is spent pushing every body row
down on every page. Truth-measured on the Oracle-rendered corpus -- a
+0.12in content-measure cushion put one report's first body rule at
1.1241in where the truth prints it at 1.0067in and the declared chrome
ends at exactly 1.0000in. The footer rule is the mirror image: the band
spans the declared footer chrome exactly and BottomMargin is the paper
left under it.
"""
import re

from converter import convert

# Geometry the fixture declares. The header band top is deliberately NOT a
# 2-decimal number: quantising it moves the whole sheet and forces the band
# to swallow the rounding to avoid clipping its own last box.
BAND_TOP = 0.24585
CHROME_BOTTOM = 1.00000          # title box bottom == the body's first line
FTR_TOP = 10.35291
FTR_BOTTOM = 10.68665
PAGE_H = 11.0


def _chrome_xml(body_origin_y=None, title_elasticity=None,
                thin_bottom=False):
    """A minimal report that declares a <margin> page band: a run-on stamp
    and a tall title in the top band, a page number in the bottom band."""
    loc = ""
    if body_origin_y is not None:
        loc = f'<location x="0.25000" y="{body_origin_y:.5f}"/>'
    elas = ""
    if title_elasticity:
        elas = (f'<generalLayout verticalElasticity="{title_elasticity}"/>')
    title_h = CHROME_BOTTOM - 0.30000
    thin = ""
    if thin_bottom:
        # A declared box THINNER than anything the engine can draw: the
        # emitter floors it, so the band has to hold the floored box.
        thin = (
            f'<text name="B_THIN"><geometryInfo x="0.50000" '
            f'y="{CHROME_BOTTOM - 0.05:.5f}" width="1.50000" '
            f'height="0.05000"/>'
            '<textSegment><font face="Arial" size="6"/>'
            '<string><![CDATA[thin strip]]></string></textSegment></text>')
    ftr_h = FTR_BOTTOM - FTR_TOP
    return (
        '<?xml version="1.0"?><report name="BAND_T" DTDVersion="9.0.2.0.10">'
        '<data><dataSource name="Q_Main">'
        '<select><![CDATA[select item_nm, amt from t]]></select>'
        '<group name="G_Main"><dataItem name="ITEM_NM" datatype="vchar2"/>'
        '<dataItem name="AMT" datatype="number"/></group></dataSource></data>'
        f'<layout><section name="main" width="8.50000" height="{PAGE_H:.5f}">'
        f'<body>{loc}<frame name="M_ALL">'
        '<geometryInfo x="0" y="0" width="7.5" height="3"/>'
        '<repeatingFrame name="R_Main" source="G_Main" printDirection="down">'
        '<geometryInfo x="0" y="0" width="7.5" height="0.4"/>'
        '<field name="F_ITEM" source="ITEM_NM">'
        '<geometryInfo x="0.1" y="0.05" width="2.0" height="0.2"/></field>'
        '<field name="F_AMT" source="AMT">'
        '<geometryInfo x="3.0" y="0.05" width="1.0" height="0.2"/></field>'
        '</repeatingFrame></frame></body>'
        '<margin>'
        f'<text name="B_RUNON"><geometryInfo x="6.00000" y="{BAND_TOP:.5f}" '
        'width="1.20000" height="0.19000"/>'
        '<textSegment><font face="Arial" size="10"/>'
        '<string><![CDATA[Stamp line]]></string></textSegment></text>'
        f'<text name="B_TITLE">{elas}'
        f'<geometryInfo x="2.00000" y="0.30000" width="4.00000" '
        f'height="{title_h:.5f}"/>'
        '<textSegment><font face="Arial" size="24"/>'
        '<string><![CDATA[Band Fixture Title]]></string></textSegment></text>'
        + thin +
        '<text name="B_PGNUM"><textSettings justify="center"/>'
        f'<geometryInfo x="3.40000" y="{FTR_TOP:.5f}" width="2.00000" '
        f'height="{ftr_h:.5f}"/>'
        '<textSegment><font face="Arial" size="10"/>'
        '<string><![CDATA[Page &<PhysicalPageNumber>]]></string>'
        '</textSegment></text>'
        '</margin></section></layout></report>'
    )


def _page_num(rdl, tag):
    m = re.search(r"<%s>([0-9.]+)in</%s>" % (tag, tag), rdl)
    assert m, f"<{tag}> missing from the emitted page"
    return float(m.group(1))


def _band_height(rdl, band):
    seg = rdl.split(f"<{band}>", 1)[1].split(f"</{band}>", 1)[0]
    m = re.search(r"<Height>([0-9.]+)in</Height>", seg)
    assert m, f"{band} carries no Height"
    return float(m.group(1))


def _chrome_box(rdl, band, name):
    """(Top, Height) of a declared chrome box inside a page band."""
    seg = rdl.split(f"<{band}>", 1)[1].split(f"</{band}>", 1)[0]
    box = re.search(r'<Textbox Name="[^"]*%s">.*?</Textbox>' % name, seg,
                    re.S)
    assert box, f"{name} not emitted into the {band}"
    t = float(re.search(r"<Top>([0-9.]+)in</Top>", box.group(0)).group(1))
    h = float(re.search(r"<Height>([0-9.]+)in</Height>",
                        box.group(0)).group(1))
    return t, h


# --------------------------------------------------------------------------
# header
# --------------------------------------------------------------------------

def test_header_band_ends_on_the_declared_chrome_bottom():
    """TopMargin is the band's declared top; TopMargin + PageHeader Height
    is the declared chrome bottom -- to the inch, with no cushion."""
    rdl = convert(_chrome_xml().encode())["rdl_xml"]
    tm = _page_num(rdl, "TopMargin")
    ph = _band_height(rdl, "PageHeader")
    assert abs(tm - BAND_TOP) < 0.0006, (
        "TopMargin must be the declared band top at its own precision, "
        f"got {tm}")
    assert abs((tm + ph) - CHROME_BOTTOM) < 0.0011, (
        "TopMargin + PageHeader Height must land on the declared chrome "
        f"bottom {CHROME_BOTTOM}; got {tm} + {ph} = {tm + ph}")


def test_header_band_never_clips_its_own_declared_chrome():
    """The no-slack rule may not be paid for by cutting the band short:
    every emitted chrome box still ends inside the band."""
    rdl = convert(_chrome_xml().encode())["rdl_xml"]
    ph = _band_height(rdl, "PageHeader")
    seg = rdl.split("<PageHeader>", 1)[1].split("</PageHeader>", 1)[0]
    items = []
    for m in re.finditer(r"<(Textbox|Image|Line) Name=.*?</\1>", seg, re.S):
        blk = m.group(0)
        t = re.search(r"<Top>([0-9.]+)in</Top>", blk)
        h = re.search(r"<Height>([0-9.]+)in</Height>", blk)
        if t and h:
            items.append((float(t.group(1)), float(h.group(1))))
    assert items, "no declared chrome emitted into the header band"
    for top, h in items:
        assert top + h <= ph + 0.0011, (
            f"chrome box at {top}+{h} spills out of a {ph}in band")


def test_band_holds_a_box_the_engine_floors_taller_than_declared():
    """The no-slack rule has exactly one floor: a declared box thinner than
    the engine's minimum drawable height is emitted at that minimum, and the
    band must still contain it. This is a never-clip guard, not breathing
    room -- it can only bind when a declaration is thinner than SSRS draws."""
    rdl = convert(_chrome_xml(thin_bottom=True).encode())["rdl_xml"]
    ph = _band_height(rdl, "PageHeader")
    top, h = _chrome_box(rdl, "PageHeader", "B_THIN")
    assert h > 0.05 + 0.0011, (
        "fixture no longer exercises the floor: the thin box was not "
        f"floored (h={h})")
    assert top + h <= ph + 0.0011, (
        f"the floored box at {top}+{h} spills out of a {ph}in band")


def test_declared_body_origin_wins_when_it_sits_below_the_chrome():
    """A source that also declares <body><location y=> states where the
    body starts itself; the band grows to meet it."""
    origin = 1.30000
    rdl = convert(_chrome_xml(body_origin_y=origin).encode())["rdl_xml"]
    tm = _page_num(rdl, "TopMargin")
    ph = _band_height(rdl, "PageHeader")
    assert abs((tm + ph) - origin) < 0.0011, (
        f"declared body origin {origin} must be where the body starts; "
        f"got {tm} + {ph} = {tm + ph}")


# --------------------------------------------------------------------------
# footer (the mirror rule)
# --------------------------------------------------------------------------

def test_footer_band_spans_exactly_the_declared_footer_chrome():
    """PageHeight - BottomMargin - PageFooter Height is the declared
    footer-chrome top, and PageHeight - BottomMargin is its bottom."""
    rdl = convert(_chrome_xml().encode())["rdl_xml"]
    bm = _page_num(rdl, "BottomMargin")
    pf = _band_height(rdl, "PageFooter")
    assert abs((PAGE_H - bm - pf) - FTR_TOP) < 0.0011, (
        "footer band top must be the declared footer chrome top; got "
        f"{PAGE_H - bm - pf}")
    assert abs((PAGE_H - bm) - FTR_BOTTOM) < 0.0011, (
        "BottomMargin must be the paper left under the declared footer "
        f"chrome; got {PAGE_H - bm}")


# --------------------------------------------------------------------------
# the band contract holds only if its boxes keep their declared height
# --------------------------------------------------------------------------

def test_chrome_box_grows_only_when_the_source_declares_that_it_does():
    """The band is measured from the declared boxes, so a box may not grow
    past its declaration -- SSRS centres text in the GROWN box and drops it
    through the band floor onto the body's first rows. Oracle's default for
    a boilerplate object is fixed; a declared variable/expand elasticity
    still grows."""
    fixed = convert(_chrome_xml().encode())["rdl_xml"]
    seg = fixed.split("<PageHeader>", 1)[1].split("</PageHeader>", 1)[0]
    box = re.search(r'<Textbox Name="[^"]*B_TITLE">.*?</Textbox>', seg, re.S)
    assert box, "declared title not emitted into the header band"
    assert "<CanGrow>false</CanGrow>" in box.group(0), (
        "an undeclared vertical elasticity is Oracle's FIXED default")

    grew = convert(_chrome_xml(title_elasticity="variable")
                   .encode())["rdl_xml"]
    seg2 = grew.split("<PageHeader>", 1)[1].split("</PageHeader>", 1)[0]
    box2 = re.search(r'<Textbox Name="[^"]*B_TITLE">.*?</Textbox>', seg2,
                     re.S)
    assert box2 and "<CanGrow>true</CanGrow>" in box2.group(0), (
        "a DECLARED variable elasticity must still grow")


def test_declared_chrome_box_keeps_its_declared_height():
    """Prove-the-gate companion: the box the band is measured from is the
    DECLARED box, emitted at its declared geometry."""
    rdl = convert(_chrome_xml().encode())["rdl_xml"]
    top, h = _chrome_box(rdl, "PageHeader", "B_TITLE")
    tm = _page_num(rdl, "TopMargin")
    assert abs((tm + top) - 0.30000) < 0.0011, (tm, top)
    assert abs(h - (CHROME_BOTTOM - 0.30000)) < 0.0011, h

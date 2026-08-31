"""THE DECLARED-VS-ENGINE WIDTH BUDGET.

A landscape report is wider than the paper it prints on, so the engine cuts
it into COLUMN SLICES. Three ratchet entries in the hostile-shape rail were
all one class of that cut going wrong: a sheet arrived at the reader
carrying a residual strip of the layout and nothing else.

WHAT THE ENGINE ACTUALLY DOES (probe-measured here, not assumed). The
dispositions of those entries blamed "the engine's per-cell padding pushing
a declared extent past the edge". Probed against the real ReportViewer on a
17.00in sheet with zero side margins, that is FALSE: a region whose extent
is exactly the printable width still prints on ONE sheet, at 1, 2, 4, 8, 16
and 32 columns alike -- the engine adds nothing to a declared extent. What
it does do is cut a wider region by WHOLE COLUMNS, filling the first sheet
against ``printable - Left`` and every later sheet against the full
printable width (measured: 10 columns of 3.5in packed 4/4/2; 54 of 0.634in
packed 26/26/2; 8 of 2.125in at Left 1.0in packed 7/1).
``_engine_column_slices`` is that measurement, and the first leg below is
its regression net -- if the engine's rule ever differs from the emitter's
model of it, the whole budget is guesswork again.

THE TWO KNOBS THIS FILE GUARDS, and why each is the budget and not a
rewrite of declared geometry:

1. A BAND'S EMITTED SPAN IS THE BUDGET FOR THE CHILDREN PLACED IN IT.
   A painted header/detail band is one spanned Tablix cell holding the
   declared caption objects at their declared offsets. The band can only be
   as wide as the tablix columns it spans, and the column reconciler may
   have compressed those below the frame's declared width. Then the last
   caption keeps a declared offset the band no longer has room for, lands
   past the printable edge of the sheet, and the engine opens a column
   slice that prints that one caption. The knob: an overflowing declared
   run is scaled into the band by the MINIMUM factor that contains it.
   Nothing moves when the run already fits -- proven both ways below, and
   proven identical across scripts, because the rule is geometry and a
   Greek caption may not be treated differently from a Latin one.

2. A REGION MAY NOT BUY A SHEET FOR A RESIDUAL SLICE.
   Paginating across is honest; a final sheet holding 3% of a strip is not.
   "Residual" is not invented here: it is the shared blank measure's own
   sparse-sheet shape (an order of magnitude starved beside the fullest
   sheet of the same document), applied to the packing the emitter can
   compute before it ships. The knob is the one ``_narrow_item`` already
   turns -- the region gives width back from its WIDEST columns, never
   below the legibility floor, and only the minimum that folds the residual
   sheet away. A region with no slack to pay from is left completely alone.

Every leg is mutation-proven: the fold pass is disabled and the residual
sheet comes back, and the band's children are re-placed at their declared
offsets and the extra sheet comes back -- at every row count the defect is
visible at (the engine legs render 0 / 1 / 3 / 25).

Synthetic fixtures only -- no client data.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools" / "renderlab"))

from converter import convert  # noqa: E402
import converter.generators.rdl as rdl_mod  # noqa: E402

NS = "{http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition}"
_ITEMS = ("Rectangle", "Textbox", "Tablix", "Image", "Line", "Subreport",
          "List", "Chart", "CustomReportItem")


def _q(tag):
    return NS + tag


def _in(v, default=0.0):
    try:
        return float((v or "").replace("in", ""))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Fixtures: a wide landscape grid, in two shapes
# ---------------------------------------------------------------------------

def _band_source(ncol=20, pitch=0.90, cap_w=0.60, field_w=0.30,
                 frame_w=17.80, caption=None, stray_text_at=None):
    """A landscape column grid whose captions live in a DECLARED painted
    header frame. ``pitch``/``frame_w`` decide whether the declared caption
    run fits the band the reconciled columns leave for it.
    ``stray_text_at`` adds a text object the frame declares but no column
    claims -- one the band therefore never places."""
    caption = caption or (lambda i: f"Col {i + 1:02d}")
    cols = [f"C{i + 1:02d}" for i in range(ncol)]
    items = "".join(
        f'<dataItem name="{c}" datatype="vchar2" columnOrder="{i + 1}" '
        f'defaultLabel="{caption(i)}"/>' for i, c in enumerate(cols))
    flds = "".join(
        f'<field name="F_{c}" source="{c}" minWidowLines="1" alignment="center">'
        f'<font face="Arial" size="5"/>'
        f'<geometryInfo x="{0.5 + i * pitch:.5f}" y="0.14758" '
        f'width="{field_w:.5f}" height="0.07385"/></field>'
        for i, c in enumerate(cols))
    caps = "".join(
        f'<text name="B_{c}" minWidowLines="1">'
        f'<textSettings justify="center" spacing="0"/>'
        f'<geometryInfo x="{0.5 + i * pitch:.5f}" y="0.00000" '
        f'width="{cap_w:.5f}" height="0.06250"/>'
        f'<visualSettings fillPattern="transparent" '
        f'fillBackgroundColor="r75g88b100" linePattern="solid"/>'
        f'<textSegment><font face="Arial" size="4" bold="yes"/>'
        f'<string><![CDATA[{caption(i)}]]></string></textSegment></text>'
        for i, c in enumerate(cols))
    if stray_text_at is not None:
        caps += (
            f'<text name="B_STRAY" minWidowLines="1">'
            f'<textSettings justify="center" spacing="0"/>'
            f'<geometryInfo x="{stray_text_at:.5f}" y="0.00000" '
            f'width="{cap_w:.5f}" height="0.06250"/>'
            f'<visualSettings fillPattern="transparent" '
            f'fillBackgroundColor="r75g88b100" linePattern="solid"/>'
            f'<textSegment><font face="Arial" size="4"/>'
            f'<string><![CDATA[Footnote marker]]></string></textSegment></text>')
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<report name="WIDE_DAY_GRID" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_MAIN">
      <select canParse="no"><![CDATA[SELECT {", ".join(cols)} FROM GRID]]></select>
      <group name="G_MAIN">{items}</group>
    </dataSource>
  </data>
  <layout>
  <section name="main" width="60.00000" height="8.26758" orientation="landscape">
    <body width="59.00000" height="6.35522"><location y="1.12500"/>
      <frame name="M_G_MAIN_GRPFR">
        <geometryInfo x="0.00000" y="0.00000" width="48.25000" height="0.31250"/>
        <generalLayout verticalElasticity="variable"/>
        <visualSettings fillPattern="transparent"/>
        <repeatingFrame name="R_G_MAIN" source="G_MAIN" printDirection="down"
         minWidowRecords="1" columnMode="no">
          <geometryInfo x="0.06250" y="0.14758" width="17.43750" height="0.16492"/>
          <generalLayout verticalElasticity="expand"/>
          {flds}
        </repeatingFrame>
        <frame name="M_G_MAIN_HDR">
          <geometryInfo x="0.50000" y="0.00000" width="{frame_w:.5f}" height="0.08508"/>
          <advancedLayout printObjectOnPage="allPage" basePrintingOn="anchoringObject"/>
          <visualSettings fillPattern="transparent"
           fillBackgroundColor="r75g88b100" linePattern="solid"/>
          {caps}
        </frame>
      </frame>
    </body>
    <margin/>
  </section>
  </layout>
</report>""".encode("utf-8")


def _wide_source(widths, gap=0.05, orientation="landscape",
                 section_w=60.0):
    """A landscape grid of ``widths`` columns and nothing else -- no painted
    band, so the slice budget is measured on its own."""
    cols = [f"K{i + 1:02d}" for i in range(len(widths))]
    items = "".join(
        f'<dataItem name="{c}" datatype="vchar2" columnOrder="{i + 1}" '
        f'defaultLabel="{c}"/>' for i, c in enumerate(cols))
    x, flds = 0.0, []
    for c, w in zip(cols, widths):
        flds.append(
            f'<field name="F_{c}" source="{c}" minWidowLines="1" alignment="center">'
            f'<font face="Arial" size="5"/>'
            f'<geometryInfo x="{x:.5f}" y="0.10000" width="{w:.5f}" '
            f'height="0.09000"/></field>')
        x += w + gap
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<report name="WIDE_LEDGER_GRID" DTDVersion="9.0.2.0.10">
  <data><dataSource name="Q_MAIN">
    <select canParse="no"><![CDATA[SELECT {", ".join(cols)} FROM LEDGER]]></select>
    <group name="G_MAIN">{items}</group></dataSource></data>
  <layout>
  <section name="main" width="{section_w:.5f}" height="8.26758"
   orientation="{orientation}">
    <body width="59.00000" height="6.35522"><location y="1.12500"/>
      <frame name="M_G_MAIN_GRPFR">
        <geometryInfo x="0.00000" y="0.00000" width="48.25000" height="0.31250"/>
        <generalLayout verticalElasticity="variable"/>
        <repeatingFrame name="R_G_MAIN" source="G_MAIN" printDirection="down"
         minWidowRecords="1" columnMode="no">
          <geometryInfo x="0.00000" y="0.10000" width="48.00000" height="0.20000"/>
          <generalLayout verticalElasticity="expand"/>
          {"".join(flds)}
        </repeatingFrame>
      </frame>
    </body>
    <margin/>
  </section>
  </layout>
</report>""".encode("utf-8")


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------

def _printable_width(root):
    page = root.find(_q("Page"))
    return (_in(page.findtext(_q("PageWidth")), 8.5)
            - _in(page.findtext(_q("LeftMargin")))
            - _in(page.findtext(_q("RightMargin"))))


def _body_items(root):
    """[(name, abs_left, width)] for every emitted body report item."""
    out = []

    def walk(el, left):
        if isinstance(el.tag, str) and el.tag.split("}")[-1] in _ITEMS:
            left = left + _in(el.findtext(_q("Left")))
            out.append((el.get("Name") or "", left,
                        _in(el.findtext(_q("Width")))))
        for c in el:
            walk(c, left)

    body = root.find(_q("Body"))
    if body is not None:
        walk(body, 0.0)
    return out


def _items_past_printable(rdl_xml):
    """Body items whose ABSOLUTE right edge passes the printable width, and
    which are not a data region paginating across on purpose."""
    root = ET.fromstring(rdl_xml)
    p = _printable_width(root)
    regions = {t.get("Name") for t in root.iter(_q("Tablix"))}
    return [(n, round(l, 4), round(w, 4)) for n, l, w in _body_items(root)
            if n not in regions and w > 0 and l + w > p + 0.005]


def _band(root, name="HdrBand_0"):
    for e in root.iter(_q("Rectangle")):
        if (e.get("Name") or "") == name:
            return e
    return None


def _band_children(root, name="HdrBand_0"):
    b = _band(root, name)
    if b is None:
        return None, []
    items = b.find(_q("ReportItems"))
    kids = [(k.get("Name") or "", _in(k.findtext(_q("Left"))),
             _in(k.findtext(_q("Width"))))
            for k in (list(items) if items is not None else [])]
    return _in(b.findtext(_q("Width"))), kids


def _region_slices(rdl_xml):
    """{tablix name: engine column packing} for every emitted data region."""
    root = ET.fromstring(rdl_xml)
    p = _printable_width(root)
    lefts = {n: l for n, l, _w in _body_items(root)}
    out = {}
    for t in root.iter(_q("Tablix")):
        tb = t.find(_q("TablixBody"))
        cols = tb.find(_q("TablixColumns")) if tb is not None else None
        if cols is None:
            continue
        widths = [_in(c.findtext(_q("Width")))
                  for c in cols.findall(_q("TablixColumn"))]
        out[t.get("Name") or ""] = rdl_mod._engine_column_slices(
            lefts.get(t.get("Name") or "", 0.0), widths, p)
    return out


def _residual_slices(rdl_xml):
    """Regions whose LAST slice is an order of magnitude starved beside
    their fullest -- the sheet nobody wants to receive."""
    bad = []
    for name, sl in _region_slices(rdl_xml).items():
        if len(sl) < 2:
            continue
        fullest = max(c for _n, c in sl)
        if fullest > 0 and sl[-1][1] <= fullest / 10.0:
            bad.append((name, [(n, round(c, 3)) for n, c in sl]))
    return bad


# ---------------------------------------------------------------------------
# LEG 1 -- the emitter's model of the engine IS the engine
# ---------------------------------------------------------------------------

# (left, widths, printable) -> the packing the real ReportViewer produced.
# Each row was rendered and its per-sheet columns counted; see the module
# docstring. The 17.000in row is the one that matters most: it is the
# measurement that killed the "per-cell padding" premise.
_MEASURED = [
    (0.0, [8.0] * 2, 17.0, [2]),
    (0.0, [8.5] * 2, 17.0, [2]),
    (0.0, [8.6] * 2, 17.0, [1, 1]),
    (0.0, [8.5] * 4, 17.0, [2, 2]),
    (0.0, [3.4] * 10, 17.0, [5, 5]),
    (0.0, [3.5] * 10, 17.0, [4, 4, 2]),
    (0.0, [0.634] * 54, 17.0, [26, 26, 2]),
    (0.0, [1.7] * 20, 17.0, [10, 10]),
    (0.0, [1.8] * 20, 17.0, [9, 9, 2]),
    (0.0, [11.5] * 3, 17.0, [1, 1, 1]),
    (1.0, [2.0] * 8, 17.0, [8]),
    (1.0, [2.125] * 8, 17.0, [7, 1]),
    (2.0, [3.0] * 5, 17.0, [5]),
    (0.25, [0.5] * 33, 17.0, [33]),
    (0.0, [0.5] * 34, 17.0, [34]),
]


@pytest.mark.parametrize("left,widths,printable,counts", _MEASURED)
def test_engine_slice_model_reproduces_the_measured_engine(
        left, widths, printable, counts):
    got = [n for n, _c in rdl_mod._engine_column_slices(
        left, widths, printable)]
    assert got == counts, (
        f"the emitter's model of the engine's column cut drifted: "
        f"left={left} {len(widths)}x{widths[0]}in on {printable}in "
        f"-> {got}, engine-measured {counts}")


def test_an_extent_equal_to_the_printable_width_is_one_slice():
    """The premise the three ratchet dispositions were written on -- that
    the engine adds per-cell padding to a declared extent -- measured
    FALSE. A declared extent of exactly the printable width fits, at every
    column count, so the budget the emitter reserves is the printable width
    itself and not the printable width minus an invented allowance."""
    for n in (1, 2, 4, 8, 16, 32, 64):
        widths = [17.0 / n] * n
        assert len(rdl_mod._engine_column_slices(0.0, widths, 17.0)) == 1, (
            f"{n} columns summing to exactly the printable width were "
            f"modelled as more than one slice")


# ---------------------------------------------------------------------------
# LEG 2 -- a band's emitted span is the budget for its declared children
# ---------------------------------------------------------------------------

def test_declared_band_run_is_contained_by_the_band_that_holds_it():
    rdl = convert(_band_source(), "WIDE_DAY_GRID.xml")["rdl_xml"]
    band_w, kids = _band_children(ET.fromstring(rdl))
    assert kids, "fixture did not take the declared-band path"
    far = max(l + w for _n, l, w in kids)
    assert far <= band_w + 0.005, (
        f"a declared caption run of {far:.4f}in was placed inside a "
        f"{band_w:.4f}in band: {kids[-1]}")
    assert not _items_past_printable(rdl), _items_past_printable(rdl)


def test_a_band_run_that_fits_is_placed_verbatim():
    """The containment scale is the MINIMUM that contains, so a band whose
    declared run already fits keeps the declaration to the last decimal --
    otherwise this pass would quietly re-scale every header in the corpus."""
    rdl = convert(_band_source(pitch=0.50, cap_w=0.30, frame_w=10.5),
                  "WIDE_DAY_GRID.xml")["rdl_xml"]
    band_w, kids = _band_children(ET.fromstring(rdl))
    assert kids, "fixture did not take the declared-band path"
    for i, (_n, left, width) in enumerate(kids):
        assert abs(width - 0.30) < 1e-4, f"caption {i} width {width}"
        assert abs(left - i * 0.50) < 1e-4, f"caption {i} left {left}"
    assert max(l + w for _n, l, w in kids) <= band_w + 0.005


def test_a_text_the_band_never_places_does_not_shrink_the_ones_it_does():
    """The budget is what the band actually PLACES. A frame may declare text
    objects no column claims (a footnote marker parked far to the right);
    those are never emitted into the band, so counting them against its
    width would shrink every real caption for nothing. Measured on the wild
    corpus: counting them changed two more reports than the defect needed.
    """
    plain = convert(_band_source(pitch=0.50, cap_w=0.30, frame_w=10.5),
                    "WIDE_DAY_GRID.xml")["rdl_xml"]
    stray = convert(_band_source(pitch=0.50, cap_w=0.30, frame_w=10.5,
                                 stray_text_at=30.0),
                    "WIDE_DAY_GRID.xml")["rdl_xml"]
    _bw0, k0 = _band_children(ET.fromstring(plain))
    _bw1, k1 = _band_children(ET.fromstring(stray))
    assert k0 and k1
    assert [(round(l, 4), round(w, 4)) for _n, l, w in k0] ==            [(round(l, 4), round(w, 4)) for _n, l, w in k1], (
        "a text object the band never places shrank the captions it does")


@pytest.mark.parametrize("script,letters", [
    ("latin", "ABCDEFGHIJKLMNOPQRST"),
    ("greek", "ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥ"),
    ("cyrillic", "АБВГДЕЖЗИЙКЛМНОПРСТУ"),
    ("arabic", "ابتثجحخدذرزسشصضطظعغفق"),
    ("cjk", "一二三四五六七八九十百千万億兆京垓阿僧祇"),
])
def test_band_containment_is_the_same_in_every_script(script, letters):
    """The budget is GEOMETRY. A rule that contained a Latin caption run and
    not a Greek one (or scaled them by different amounts) would be the
    script-sensitive defect this campaign exists to kill, so the emitted
    band geometry must be identical to the Latin baseline in every script.
    """
    def _cap(i):
        return f"{letters[i % len(letters)]}{i + 1:02d}"

    base = convert(_band_source(), "WIDE_DAY_GRID.xml")["rdl_xml"]
    got = convert(_band_source(caption=_cap), "WIDE_DAY_GRID.xml")["rdl_xml"]
    bw0, k0 = _band_children(ET.fromstring(base))
    bw1, k1 = _band_children(ET.fromstring(got))
    assert k1, f"{script}: fixture did not take the declared-band path"
    assert abs(bw0 - bw1) < 1e-6, f"{script}: band width {bw0} vs {bw1}"
    assert [(round(l, 4), round(w, 4)) for _n, l, w in k0] == \
           [(round(l, 4), round(w, 4)) for _n, l, w in k1], (
        f"{script}: band geometry differs from the Latin baseline")
    assert not _items_past_printable(got)


def test_the_containment_gate_fails_when_the_run_keeps_its_declared_offset():
    """RED-FIRST. Undo the containment on the emitted artifact -- put every
    caption back at the offset it declares -- and the invariant this file
    guards must report the overflow. A gate that cannot go red proves
    nothing."""
    rdl = convert(_band_source(), "WIDE_DAY_GRID.xml")["rdl_xml"]
    root = ET.fromstring(rdl)
    band = _band(root)
    for i, kid in enumerate(band.find(_q("ReportItems"))):
        kid.find(_q("Left")).text = f"{i * 0.90:.4f}in"
        kid.find(_q("Width")).text = "0.6000in"
    doctored = ET.tostring(root, encoding="unicode")
    assert _items_past_printable(doctored), (
        "the declared-offset geometry was accepted by the invariant -- the "
        "containment leg above is not measuring anything")


# ---------------------------------------------------------------------------
# LEG 3 -- no region buys a sheet for a residual slice
# ---------------------------------------------------------------------------

# 62 columns: one 3.5in column carrying the slack, 61 at the legibility
# floor. Extent 34.25in against a 17.00in printable width packs 27/34/1 --
# the last sheet holding a single 0.5in column.
_RESIDUAL_WIDTHS = [3.5] + [0.5] * 61


def test_no_region_buys_a_sheet_for_a_residual_column_slice():
    rdl = convert(_wide_source(_RESIDUAL_WIDTHS),
                  "WIDE_LEDGER_GRID.xml")["rdl_xml"]
    sl = _region_slices(rdl)["Tablix_Main"]
    assert len(sl) >= 2, "fixture stopped paginating across; it must not"
    assert not _residual_slices(rdl), _residual_slices(rdl)


def test_the_residual_slice_comes_back_when_the_fold_is_disabled(monkeypatch):
    """RED-FIRST for the same fixture: with the pass switched off the
    region packs 27/34/1 and the last sheet is 3% of the paper."""
    monkeypatch.setattr(rdl_mod, "_fold_residual_column_slices",
                        lambda root: None)
    rdl = convert(_wide_source(_RESIDUAL_WIDTHS),
                  "WIDE_LEDGER_GRID.xml")["rdl_xml"]
    bad = _residual_slices(rdl)
    assert bad, "the fixture does not reproduce the residual slice any more"
    assert bad[0][1][-1][0] == 1, bad


def test_the_fold_is_paid_out_of_slack_and_never_below_the_floor():
    """The knob is ``_narrow_item``'s: widest-first, out of SLACK only. The
    columns already at the legibility floor may not be touched at all."""
    off = convert(_wide_source(_RESIDUAL_WIDTHS),
                  "WIDE_LEDGER_GRID.xml")
    on = ET.fromstring(off["rdl_xml"])
    widths = [_in(c.findtext(_q("Width")))
              for c in on.iter(_q("TablixColumn"))]
    assert min(widths) >= rdl_mod._TABLIX_MIN_COL_W_IN - 1e-6, min(widths)
    assert sorted(widths)[:-1] == [rdl_mod._TABLIX_MIN_COL_W_IN] * 61, (
        "the fold took width from a column that had no slack to give")
    # ...and it paid the debt out of the ONE column that had slack.
    assert max(widths) < 3.5 - 1e-6, max(widths)


def test_a_region_with_no_slack_paginates_across_untouched():
    """Negative control: every column already at the floor, so the residual
    cannot be folded out of slack. The declaration is then left completely
    alone -- an ultra-wide table paginates across at readable widths rather
    than being crushed to threads."""
    widths = [0.5] * 68              # 34.0in + Left, no slack anywhere
    before = convert(_wide_source(widths), "WIDE_LEDGER_GRID.xml")["rdl_xml"]
    got = [_in(c.findtext(_q("Width")))
           for c in ET.fromstring(before).iter(_q("TablixColumn"))]
    assert got == [rdl_mod._TABLIX_MIN_COL_W_IN] * len(widths), (
        "a region with no slack was scaled anyway")


def test_a_region_that_fits_the_paper_is_never_touched(monkeypatch):
    """The pass must be a provable no-op for every report that fits its
    sheet -- which is the whole portrait corpus."""
    widths = [0.8] * 8
    on = convert(_wide_source(widths, orientation="portrait",
                             section_w=8.5), "WIDE_LEDGER_GRID.xml")["rdl_xml"]
    monkeypatch.setattr(rdl_mod, "_fold_residual_column_slices",
                        lambda root: None)
    off = convert(_wide_source(widths, orientation="portrait",
                              section_w=8.5), "WIDE_LEDGER_GRID.xml")["rdl_xml"]
    assert on == off


# ---------------------------------------------------------------------------
# LEG 4 -- the engine has the last word, at every row count
# ---------------------------------------------------------------------------

try:
    from render import lib_ready, render_rdl  # noqa: E402
    _LIB_OK = lib_ready()
except Exception:  # noqa: BLE001
    _LIB_OK = False

_SHAPES = (0, 1, 3, 25)
_engine = pytest.mark.skipif(
    not _LIB_OK or sys.platform != "win32",
    reason="ReportViewer DLLs not fetched (tools/renderlab) or non-Windows")


def _sheets(rdl_xml, tmp_path, tag):
    """Sheets the real engine prints, per row count."""
    p = Path(tmp_path) / f"{tag}.rdl"
    p.write_text(rdl_xml, encoding="utf-8")
    out = {}
    for rows in _SHAPES:
        pdf = Path(tmp_path) / f"{tag}_{rows}.pdf"
        res = render_rdl(p, pdf, rows=rows)
        assert res.get("ok"), f"{tag} rows={rows} failed to render"
        import pypdf
        out[rows] = len(pypdf.PdfReader(str(pdf)).pages)
    return out


@_engine
def test_contained_band_prints_no_extra_sheet_at_any_row_count(tmp_path):
    rdl = convert(_band_source(), "WIDE_DAY_GRID.xml")["rdl_xml"]
    got = _sheets(rdl, tmp_path, "band_on")
    assert set(got.values()) == {1}, got

    root = ET.fromstring(rdl)
    for i, kid in enumerate(_band(root).find(_q("ReportItems"))):
        kid.find(_q("Left")).text = f"{i * 0.90:.4f}in"
        kid.find(_q("Width")).text = "0.6000in"
    doctored = ET.tostring(root, encoding="unicode")
    mutated = _sheets(doctored, tmp_path, "band_mut")
    assert [s for s in _SHAPES if mutated[s] > got[s]] == [1, 3, 25], (
        f"the declared-offset geometry has stopped manufacturing the extra "
        f"sheet the containment exists for: contained={got} mutated={mutated}")


@_engine
def test_residual_slice_costs_a_real_sheet_and_the_fold_removes_it(
        tmp_path, monkeypatch):
    rdl = convert(_wide_source(_RESIDUAL_WIDTHS),
                  "WIDE_LEDGER_GRID.xml")["rdl_xml"]
    folded = _sheets(rdl, tmp_path, "wide_on")
    monkeypatch.setattr(rdl_mod, "_fold_residual_column_slices",
                        lambda root: None)
    raw = convert(_wide_source(_RESIDUAL_WIDTHS),
                  "WIDE_LEDGER_GRID.xml")["rdl_xml"]
    unfolded = _sheets(raw, tmp_path, "wide_off")
    assert set(folded.values()) == {2}, folded
    assert set(unfolded.values()) == {3}, unfolded

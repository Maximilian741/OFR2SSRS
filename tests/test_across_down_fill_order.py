"""printDirection="acrossDown" labels must fill ACROSS then DOWN — row-major.

SSRS's newspaper Page-Columns construct fills DOWN-then-across, and it SLICES
a body wider than one column at the column edge. Engine-measured on a wild
2-up label report (8 rows): every record stacked in the left column (column 2
never reached), a strict-blank trailing page, and every address line split
mid-word at the column-1 right edge, resuming at column-2's left edge — the
"mid-address splice gap" defect. One root cause for all three.

The faithful general construct is a ROW-MAJOR grouped Tablix:

    column group  =(RowNumber(Nothing) - 1) Mod N
    row group     =Ceiling(RowNumber(Nothing) / N)

Engine probes (tools/renderlab/render_rdl.ps1, signed ReportViewer DLLs):

* ACCEPTANCE — a probe RDL with those exact group expressions passes
  publish-time semantic validation (it fails only at this machine's known
  expression-host wall, which comes AFTER validation). The probe's control —
  a group expression on a nonexistent field — fails with
  DefinitionInvalidException/ReportPublishingException through the same
  pipeline, so the acceptance is a real measurement, not silence.
* FILL ORDER — the same tablix grouped on precomputed integer fields
  (RowNumber's documented semantics: sequential feed order) renders 8
  distinct records as 1,2 / 3,4 / 5,6 / 7,8 by band: ACROSS then DOWN, at
  the declared tile pitch.

The layout-mode harness cannot compile RowNumber (expression host blocked),
so ms_layout.staticize rewrites the two tile group expressions to synthetic
integer FIELDS (O2S_TILE_ROW_N / O2S_TILE_COL_N) whose synthesized data
carries the feed order — grouping by them reproduces the identical grid
natively (that is exactly the probe's field-sim form, render-verified).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools" / "renderlab"))

from converter import convert  # noqa: E402


def _label_xml(body_width: str | None = "8.5", vgap: str = "") -> bytes:
    vs = f' vertSpaceBetweenFrames="{vgap}"' if vgap else ""
    body_attrs = f' width="{body_width}"' if body_width else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<report name="MAIL_LABELS" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_1">
      <select><![CDATA[SELECT first_name, last_name, city FROM contacts]]></select>
      <group name="G_first">
        <dataItem name="first_name" datatype="vchar2"/>
        <dataItem name="last_name" datatype="vchar2"/>
        <dataItem name="city" datatype="vchar2"/>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="main">
    <body{body_attrs} height="11.0">
      <repeatingFrame name="R_G_first" source="G_first"
                      printDirection="acrossDown"{vs}>
        <geometryInfo x="0" y="0" width="3.0" height="1.0"/>
        <text name="B_tbp">
          <geometryInfo x="0" y="0" width="3.0" height="1.0"/>
          <textSegment><font face="helvetica" size="10"/>
            <string><![CDATA[To
Mr./Ms &<first_name> &<last_name>
&<city>]]></string></textSegment>
        </text>
      </repeatingFrame>
    </body>
  </section>
  </layout>
</report>
""".encode()


_ROWG = r"<GroupExpression>=Ceiling\(RowNumber\(Nothing\) / (\d+)\)</GroupExpression>"
_COLG = r"<GroupExpression>=\(RowNumber\(Nothing\) - 1\) Mod (\d+)</GroupExpression>"


def test_ncols_derives_from_declared_geometry():
    """N = declared tiling extent // declared tile width — never a habit
    constant. 8.5in body / 3.0in tile -> 2 across; a 3.5in body -> 1."""
    rdl2 = convert(_label_xml("8.5"))["rdl_xml"]
    assert int(re.search(_ROWG, rdl2).group(1)) == 2
    assert int(re.search(_COLG, rdl2).group(1)) == 2
    rdl1 = convert(_label_xml("3.5"))["rdl_xml"]
    # N=1 degenerates to one tile per band (Mod 1 == slot 0, Ceiling(k/1)
    # == k) — still the same construct, still publish-valid.
    assert int(re.search(_ROWG, rdl1).group(1)) == 1
    assert int(re.search(_COLG, rdl1).group(1)) == 1


def test_row_pitch_is_declared_height_plus_vert_space():
    """Settled pitch rule: row pitch = declared tile height +
    vertSpaceBetweenFrames (the declared gutter); tiles butt horizontally
    (no gutter attr is ever declared in the corpora)."""
    rdl = convert(_label_xml("8.5", vgap="0.25"))["rdl_xml"]
    m = re.search(r"<TablixRow>\s*<Height>([\d.]+)in</Height>", rdl)
    assert m and abs(float(m.group(1)) - 1.25) < 0.005
    # column width stays the declared tile width — the gutter is vertical
    mw = re.search(r"<TablixColumn>\s*<Width>([\d.]+)in</Width>", rdl)
    assert mw and abs(float(mw.group(1)) - 3.0) < 0.005


def test_address_block_flows_as_one_textbox():
    """The boilerplate + field splices must emit as ONE concatenated
    textbox expression (a single flowing address block), never fragmented
    boxes — fragmentation is what re-creates splice gaps at render."""
    rdl = convert(_label_xml())["rdl_xml"]
    cell = re.search(r'<Rectangle Name="Lbl_Cell">.*?</Rectangle>', rdl, re.S)
    assert cell
    boxes = re.findall(r"<Textbox Name=", cell.group(0))
    assert len(boxes) == 1, "address block must be a single textbox"
    val = re.search(r"<Value>(=[^<]+)</Value>", cell.group(0))
    assert val and val.group(1).count("Fields!") >= 3
    assert "&amp;" in val.group(1), "literal+field segments must concatenate"


def test_staticize_rewrites_tile_groups_to_native_field_binds():
    """Layout-mode render: staticize must leave NO RowNumber in any
    GroupExpression (the engine would demand the blocked expression host)
    and must not blank the tile groups either (that collapses the grid to
    one tile). It rewrites them to synthetic integer fields whose
    synthesized data carries RowNumber's sequential-feed semantics."""
    from ms_layout import staticize  # noqa: E402  (tools/renderlab)
    from render import synthesize_data  # noqa: E402

    rdl = convert(_label_xml())["rdl_xml"]
    static = staticize(rdl)
    ges = re.findall(r"<GroupExpression>([^<]*)</GroupExpression>", static)
    tile_ges = [g for g in ges if "O2S_TILE_" in g]
    assert not any("RowNumber" in g for g in ges)
    assert "=Fields!O2S_TILE_ROW_2.Value" in tile_ges
    assert "=Fields!O2S_TILE_COL_2.Value" in tile_ges
    assert all(g.strip() for g in tile_ges), "tile groups must never blank"
    # the synthetic fields joined the tablix's dataset
    assert '<Field Name="O2S_TILE_ROW_2">' in static
    assert '<Field Name="O2S_TILE_COL_2">' in static
    # and the data feed simulates RowNumber: ceil((i+1)/2) / i mod 2
    spec = synthesize_data(static, rows=8)
    ds = next(d for d in spec["datasets"]
              if any(c["name"] == "O2S_TILE_ROW_2" for c in d["columns"]))
    names = [c["name"] for c in ds["columns"]]
    r_i, c_i = names.index("O2S_TILE_ROW_2"), names.index("O2S_TILE_COL_2")
    assert [row[r_i] for row in ds["rows"]] == [1, 1, 2, 2, 3, 3, 4, 4]
    assert [row[c_i] for row in ds["rows"]] == [0, 1, 0, 1, 0, 1, 0, 1]


def test_fit_body_budgets_full_tiled_span():
    """_fit_body_to_page's declared-span target only sees ONE tile; the
    label branch must widen the budget to the FULL tiled span or the
    residual RightMargin slices the grid at the printable edge (the
    engine-measured splice-gap defect). Width >= N*tile, and left margin +
    width must fit the sheet. The fixture declares NO body width -- the
    wild report's exact shape -- so this measurement depends on the label
    branch's tiled-span budget, not on a declared body width that happens
    to cover the grid."""
    rdl = convert(_label_xml(body_width=None))["rdl_xml"]
    assert int(re.search(_COLG, rdl).group(1)) == 2  # printable 7.5 // 3.0
    w = float(re.search(r"<Width>([\d.]+)in</Width>\s*<Page>", rdl).group(1))
    assert w >= 6.0 - 0.05, "Width must budget the full tiled span"
    lm = float(re.search(r"<LeftMargin>([\d.]+)in</LeftMargin>", rdl).group(1))
    rm = float(re.search(r"<RightMargin>([\d.]+)in</RightMargin>", rdl).group(1))
    pw = float(re.search(r"<PageWidth>([\d.]+)in</PageWidth>", rdl).group(1))
    assert w + lm + rm <= pw + 0.01, "grid must fit the printable width"
    assert rm >= 0.0

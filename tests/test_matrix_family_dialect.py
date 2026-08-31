"""Cross-tab (matrix) FAMILY dialect rules — measured over every
matrix-declaring source in the corpora (92 declared cross-tabs across 40
sources, the largest single archetype), then locked here.

Each rule below is a DECLARATION the source makes about its cross-tab that
the generator used to ignore, or an ENGINE rule measured on Microsoft's real
ReportViewer:

  A. AXIS      — <repeatingFrame printDirection="down|across"> says which
                 dimension goes DOWN (rows) and which goes ACROSS (columns).
                 The <matrix> attribute NAMES do not: census over the corpus
                 found 89/89 frame-ref cross-tabs declaring
                 horizontalFrame=down + verticalFrame=across, so reading the
                 names as the axis transposed every one of them.
  B. CORNER    — the row-dimension's heading is declared as ordinary
                 boilerplate directly above the row-header column (69/89
                 declare one); the column name is only the fallback.
  C. MASKS     — a multi-measure cell concatenates its measures, so a
                 <Format> style cannot reach them: each masked measure must
                 be wrapped in Format(). 19 measure/total cells corpus-wide
                 printed the raw ToString before this.
  D. ENGINE    — a TablixColumnHierarchy member may carry NEITHER
                 RepeatOnNewPage=true NOR KeepWithGroup other than None.
                 Both are hard LoadReportDefinition rejections (upload-fatal,
                 measured on the engine over the whole family), which is why
                 the Oracle row-header "print on every page" declaration
                 cannot be expressed by an RDL 2008 Tablix.
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.parsers.oracle_xml import parse_oracle_xml  # noqa: E402
from converter.generators.rdl import _find_matrix_spec  # noqa: E402

NS = ("{http://schemas.microsoft.com/sqlserver/reporting/"
      "2008/01/reportdefinition}")


def _source(down_dir: str = "down", across_dir: str = "across",
            caption: bool = True, mask: str = "NNNGNN0D00") -> bytes:
    """A 9.0.2 frame-ref cross-tab in the shape the corpus declares:
    a wide/short DOWN band holding the row-header column at the left, a
    narrow/tall ACROSS strip holding the column headers along the top, a
    boilerplate caption above the row-header column, and two measures."""
    cap = ('<frame name="M_HDR"><geometryInfo x="0.0" y="0.18" '
           'width="1.35" height="0.18"/>'
           '<advancedLayout printObjectOnPage="allPage"/>'
           '<text name="B_CAP"><geometryInfo x="0.0" y="0.18" '
           'width="1.35" height="0.18"/><textSegment><string>'
           '<![CDATA[Branch]]></string></textSegment></text></frame>'
           ) if caption else ""
    return (
        '<?xml version="1.0"?><report name="XT" DTDVersion="9.0.2.0.10"><data>'
        '<dataSource name="Q_1"><select><![CDATA[SELECT branch, period, '
        'amount, units FROM t]]></select>'
        '<group name="G_BR"><dataItem name="branch" datatype="vchar2"/></group>'
        '<group name="G_PE"><dataItem name="period" datatype="vchar2"/></group>'
        '<group name="G_M"><dataItem name="amount" datatype="number"/>'
        '<dataItem name="units" datatype="number"/></group>'
        '</dataSource>'
        '<crossProduct name="G_X"><dimension><group name="G_BR"/></dimension>'
        '<dimension><group name="G_PE"/></dimension></crossProduct>'
        '</data><layout><section name="main"><body width="8" height="9">'
        f'{cap}'
        f'<repeatingFrame name="R_BR" source="G_BR" printDirection="{down_dir}">'
        '<geometryInfo x="0.0" y="0.37" width="4.10" height="0.18"/>'
        '<field name="F_BR" source="branch">'
        '<geometryInfo x="0.0" y="0.37" width="1.35" height="0.18"/>'
        '<advancedLayout printObjectOnPage="allPage"/></field>'
        '</repeatingFrame>'
        f'<repeatingFrame name="R_PE" source="G_PE" '
        f'printDirection="{across_dir}">'
        '<geometryInfo x="1.37" y="0.0" width="1.35" height="0.75"/>'
        '<field name="F_PE" source="period">'
        '<geometryInfo x="1.37" y="0.0" width="1.35" height="0.18"/></field>'
        '</repeatingFrame>'
        '<repeatingFrame name="R_M" source="G_M">'
        '<geometryInfo x="1.37" y="0.37" width="1.35" height="0.18"/>'
        f'<field name="F_AM" source="amount" formatMask="{mask}">'
        '<geometryInfo x="1.37" y="0.37" width="1.35" height="0.18"/></field>'
        f'<field name="F_UN" source="units" formatMask="{mask}">'
        '<geometryInfo x="2.75" y="0.37" width="1.35" height="0.18"/></field>'
        '</repeatingFrame>'
        '<matrix name="X_G_X" horizontalFrame="R_BR" verticalFrame="R_PE" '
        'xProductGroup="G_X">'
        '<geometryInfo x="0.0" y="0.0" width="4.1" height="0.75"/></matrix>'
        '</body></section></layout></report>').encode()


# --------------------------------------------------------------- A. AXIS

def test_axis_follows_the_declared_print_direction():
    """The frame that declares printDirection="down" is the ROW dimension and
    the one declaring "across" is the COLUMN dimension -- whatever the
    <matrix> attribute happens to be called."""
    spec = _find_matrix_spec(parse_oracle_xml(_source()))
    assert spec is not None
    assert spec["row"] == "branch", spec      # horizontalFrame, prints DOWN
    assert spec["col"] == "period", spec      # verticalFrame, prints ACROSS

    rdl = convert(_source())["rdl_xml"]
    i = rdl.index('<Tablix Name="Tablix_Matrix')
    blk = rdl[i:rdl.index("</Tablix>", i)]
    rh = blk[blk.index("<TablixRowHierarchy>"):]
    ch = blk[blk.index("<TablixColumnHierarchy>"):
             blk.index("<TablixRowHierarchy>")]
    assert re.search(r"=Fields!branch\.Value", rh, re.I), rh
    assert re.search(r"=Fields!period\.Value", ch, re.I), ch


def test_axis_mutation_swapping_the_declaration_swaps_the_axes():
    """MUTATION: declare the directions the other way round and the pivot
    must transpose. Proves the axis is read from the declaration and not
    from the attribute name (which is unchanged here)."""
    spec = _find_matrix_spec(
        parse_oracle_xml(_source(down_dir="across", across_dir="down")))
    assert spec is not None
    assert spec["row"] == "period", spec
    assert spec["col"] == "branch", spec


def test_axis_without_a_declaration_keeps_the_frame_name_mapping():
    """No printDirection anywhere = no declaration to honour; the historical
    attribute-name mapping stands rather than a guess."""
    spec = _find_matrix_spec(parse_oracle_xml(_source(down_dir="",
                                                      across_dir="")))
    assert spec is not None
    assert spec["row"] == "period" and spec["col"] == "branch", spec


# ------------------------------------------------------------- B. CORNER

def test_corner_prints_the_declared_caption_not_the_column_name():
    rdl = convert(_source())["rdl_xml"]
    m = re.search(r'<Textbox Name="Mx_Corner">.*?<Value>([^<]*)</Value>',
                  rdl, re.S)
    assert m, "no corner textbox emitted"
    assert m.group(1) == "Branch", m.group(1)


def test_corner_falls_back_to_the_column_name_when_none_is_declared():
    """MUTATION: remove the declared caption -- the corner must fall back,
    never print an empty box."""
    rdl = convert(_source(caption=False))["rdl_xml"]
    m = re.search(r'<Textbox Name="Mx_Corner">.*?<Value>([^<]*)</Value>',
                  rdl, re.S)
    assert m and m.group(1).strip() == "branch", m.group(1) if m else None


# -------------------------------------------------------------- C. MASKS

def test_multi_measure_cell_prints_every_measure_through_its_mask():
    """A cross-tab cell that stacks two measures concatenates them, so the
    <Format> style cannot reach either one: each masked aggregate must carry
    its own Format() wrap."""
    rdl = convert(_source())["rdl_xml"]
    m = re.search(r'<Textbox Name="Mx_Cell">.*?<Value>(.*?)</Value>',
                  rdl, re.S)
    assert m, "no measure cell emitted"
    val = m.group(1)
    assert "&amp;" in val, ("this fixture must exercise the CONCAT shape", val)
    for col in ("amount", "units"):
        assert re.search(r'Format\(Sum\(Fields!' + col + r'\.Value\), "',
                         val, re.I), (col, val)


def test_mask_wrap_never_reaches_a_computational_position():
    """The wrap may only touch a DISPLAY operand. The measure inside the
    aggregate itself stays a typed value -- a formatted string there would
    make Sum() add text."""
    rdl = convert(_source())["rdl_xml"]
    m = re.search(r'<Textbox Name="Mx_Cell">.*?<Value>(.*?)</Value>',
                  rdl, re.S)
    assert m
    assert "Sum(Format(" not in m.group(1), m.group(1)


def test_unmasked_measures_get_no_invented_format():
    """MUTATION: drop the declared formatMask -- nothing may be wrapped."""
    rdl = convert(_source(mask=""))["rdl_xml"]
    m = re.search(r'<Textbox Name="Mx_Cell">.*?<Value>(.*?)</Value>',
                  rdl, re.S)
    assert m and "Format(" not in m.group(1), m.group(1) if m else None


# ------------------------------------------------------------- D. ENGINE

def _column_hierarchy_members(rdl: str):
    root = ET.fromstring(rdl.encode())
    for t in root.iter(NS + "Tablix"):
        ch = t.find(NS + "TablixColumnHierarchy")
        if ch is None:
            continue
        for mem in ch.iter(NS + "TablixMember"):
            yield (t.get("Name"), mem)


def test_no_tablix_column_hierarchy_member_carries_repeat_or_keepwithgroup():
    """ENGINE RULE, measured: LoadReportDefinition rejects outright
    ("All TablixMember elements in a TablixColumnHierarchy must have the
    RepeatOnNewPage property set to false" / "... the KeepWithGroup property
    set to None"). Either one is an UPLOAD-FATAL definition, so no emit path
    may ever produce them -- including the tempting one that would honour
    Oracle's printObjectOnPage="allPage" on a row-header column."""
    for src in (_source(), _source(caption=False),
                _source(down_dir="across", across_dir="down")):
        rdl = convert(src)["rdl_xml"]
        for tname, mem in _column_hierarchy_members(rdl):
            rep = (mem.findtext(NS + "RepeatOnNewPage") or "").strip().lower()
            kwg = (mem.findtext(NS + "KeepWithGroup") or "").strip()
            assert rep in ("", "false"), (tname, rep)
            assert kwg in ("", "None"), (tname, kwg)


def test_declared_row_header_page_repeat_is_disclosed_not_silently_dropped():
    """Oracle declares printObjectOnPage="allPage" on the row-header field --
    the row-header column repeats on every continuation page. RDL 2008's
    Tablix has no such construct (see the engine rule above), so the limit
    must be DISCLOSED on the preflight rather than dropped in silence."""
    out = convert(_source())
    pf = out.get("preflight") or {}
    rules = {(i.get("rule") or "") for i in (pf.get("issues") or [])}
    assert "matrix.row_header_page_repeat" in rules, sorted(rules)
    # informational only: an honest limit must never change the verdict
    sev = {i.get("severity") for i in (pf.get("issues") or [])
           if i.get("rule") == "matrix.row_header_page_repeat"}
    assert sev == {"INFO"}, sev


def test_no_disclosure_when_the_source_does_not_declare_the_repeat():
    """MUTATION: strip the printObjectOnPage declaration -- there is then no
    Oracle behaviour being lost, so the note must NOT appear."""
    src = _source().replace(
        b'<advancedLayout printObjectOnPage="allPage"/></field>',
        b'</field>')
    pf = convert(src).get("preflight") or {}
    rules = {(i.get("rule") or "") for i in (pf.get("issues") or [])}
    assert "matrix.row_header_page_repeat" not in rules, sorted(rules)


# ------------------------------------------- B2. CORNER CAPTION INJECTIVITY

def _stacked_two_pivots() -> bytes:
    """Two cross-tabs stacked down one page under a column of banners --
    the shape a multi-pivot report declares. ONE banner is declared above
    each pivot; the lower pivot must take its OWN banner, never re-claim
    the upper pivot's."""
    def pivot(n, y):
        return (
            f'<frame name="M_HDR{n}"><geometryInfo x="0.0" y="{y - 0.20:.2f}" '
            f'width="1.35" height="0.18"/>'
            f'<text name="B_CAP{n}"><geometryInfo x="0.0" y="{y - 0.20:.2f}" '
            f'width="1.35" height="0.18"/><textSegment><string>'
            f'<![CDATA[Head{n}]]></string></textSegment></text></frame>'
            f'<repeatingFrame name="R_D{n}" source="G_D{n}" '
            f'printDirection="down">'
            f'<geometryInfo x="0.0" y="{y:.2f}" width="4.10" height="0.18"/>'
            f'<field name="F_D{n}" source="dim{n}">'
            f'<geometryInfo x="0.0" y="{y:.2f}" width="1.35" '
            f'height="0.18"/></field></repeatingFrame>'
            f'<repeatingFrame name="R_A{n}" source="G_A{n}" '
            f'printDirection="across">'
            f'<geometryInfo x="1.37" y="{y:.2f}" width="1.35" height="0.18"/>'
            f'<field name="F_A{n}" source="acr{n}">'
            f'<geometryInfo x="1.37" y="{y:.2f}" width="1.35" '
            f'height="0.18"/></field></repeatingFrame>'
            f'<repeatingFrame name="R_M{n}" source="G_M{n}">'
            f'<geometryInfo x="1.37" y="{y + 0.10:.2f}" width="1.35" '
            f'height="0.08"/>'
            f'<field name="F_V{n}" source="val{n}">'
            f'<geometryInfo x="1.37" y="{y + 0.10:.2f}" width="1.35" '
            f'height="0.08"/></field></repeatingFrame>'
            f'<matrix name="X{n}" horizontalFrame="R_D{n}" '
            f'verticalFrame="R_A{n}" xProductGroup="G_X{n}">'
            f'<geometryInfo x="0.0" y="{y:.2f}" width="4.1" '
            f'height="0.20"/></matrix>')

    def data(n):
        return (f'<group name="G_D{n}"><dataItem name="dim{n}" '
                f'datatype="vchar2"/></group>'
                f'<group name="G_A{n}"><dataItem name="acr{n}" '
                f'datatype="vchar2"/></group>'
                f'<group name="G_M{n}"><dataItem name="val{n}" '
                f'datatype="number"/></group>')
    return (
        '<?xml version="1.0"?><report name="XT2" DTDVersion="9.0.2.0.10">'
        '<data><dataSource name="Q_1"><select><![CDATA[SELECT dim1, acr1, '
        'val1, dim2, acr2, val2 FROM t]]></select>'
        f'{data(1)}{data(2)}</dataSource>'
        '<crossProduct name="G_X1"><dimension><group name="G_D1"/></dimension>'
        '<dimension><group name="G_A1"/></dimension></crossProduct>'
        '<crossProduct name="G_X2"><dimension><group name="G_D2"/></dimension>'
        '<dimension><group name="G_A2"/></dimension></crossProduct>'
        '</data><layout><section name="main"><body width="8" height="9">'
        + pivot(1, 0.60) + pivot(2, 0.85) +
        '</body></section></layout></report>').encode()


def test_each_pivot_takes_its_own_declared_banner():
    rep = parse_oracle_xml(_stacked_two_pivots())
    caps = [(_find_matrix_spec(rep, i) or {}).get("row_caption")
            for i in range(2)]
    assert caps == ["Head1", "Head2"], caps


def test_a_banner_already_headed_by_a_nearer_pivot_is_not_reclaimed():
    """MUTATION: delete the LOWER pivot's own banner. The upper pivot's
    banner is still the nearest text above it, but a nearer cross-tab sits
    between them -- so the lower pivot must fall back to its column name
    rather than print a duplicate of the other pivot's heading."""
    src = re.sub(rb'<text name="B_CAP2">.*?</text>', b'',
                 _stacked_two_pivots(), flags=re.S)
    assert b"Head2" not in src, "the mutation must actually remove the banner"
    rep = parse_oracle_xml(src)
    caps = [(_find_matrix_spec(rep, i) or {}).get("row_caption")
            for i in range(2)]
    assert caps[0] == "Head1", caps
    assert not caps[1], caps

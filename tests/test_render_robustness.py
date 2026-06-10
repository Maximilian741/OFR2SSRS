"""Regression locks for the preview-robustness work:

  * the parser accepts the SIMPLIFIED layout dialect (geometry as direct
    x/y attributes, and a <layout> with no <section> wrapper),
  * a preview is NEVER blank -- it falls back to a data table when there's
    data but no positional layout, and to an honest message when there's
    neither,
  * Oracle design-time pink/lavender fills (#FFE0FF, ...) don't leak into
    rendered bands.

All inputs are synthetic / name-agnostic.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter.parsers.oracle_xml import parse_oracle_xml          # noqa: E402
from converter.preview.html_mockup import (                        # noqa: E402
    render_mockup, _is_design_fill, _band_bg)


SIMPLE_DIALECT = b"""<?xml version="1.0"?>
<report name="SIMPLE_DIALECT" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_MAIN">
      <select><![CDATA[SELECT Code, Name FROM Sample_Tbl]]></select>
      <group name="G_MAIN">
        <dataItem name="Code" source="Code"/>
        <dataItem name="Name" source="Name"/>
      </group>
    </dataSource>
  </data>
  <layout>
    <field name="F_Title" source="P_Title" x="0.25" y="0.1" width="5.0" height="0.3"/>
    <repeatingFrame name="R_Main" source="G_MAIN" x="0.25" y="0.6" width="7.5" height="0.5">
      <field name="F_Code" source="Code" x="0.0" y="0.0" width="1.5" height="0.25"/>
      <field name="F_Name" source="Name" x="1.5" y="0.0" width="3.0" height="0.25"/>
    </repeatingFrame>
  </layout>
</report>"""

NO_LAYOUT_WITH_DATA = b"""<?xml version="1.0"?>
<report name="DATA_ONLY" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_MAIN">
      <select><![CDATA[SELECT A, B FROM T]]></select>
      <group name="G"><dataItem name="A" source="A"/><dataItem name="B" source="B"/></group>
    </dataSource>
  </data>
</report>"""

NO_LAYOUT_NO_DATA = b"""<?xml version="1.0"?>
<report name="EMPTY" DTDVersion="9.0.2.0.10"><data/></report>"""


def _walk(g):
    yield g
    for c in (g.children or []):
        yield from _walk(c)


def test_simplified_dialect_layout_parses():
    """attr-geometry + section-less <layout> -> a section_main with fields."""
    rep = parse_oracle_xml(SIMPLE_DIALECT)
    names = [g.name for g in (rep.layout or [])]
    assert "section_main" in names, f"no section_main; got {names}"
    nfields = sum(len(g.fields or [])
                  for sec in (rep.layout or []) for g in _walk(sec))
    assert nfields >= 1, "simplified-dialect fields were dropped"


def test_preview_never_blank_when_data_present():
    """A report with data but no positional layout renders its DATA as a
    table -- never a blank document page."""
    html = render_mockup(parse_oracle_xml(NO_LAYOUT_WITH_DATA), "frontend")
    assert "No renderable report content" not in html
    assert len(html) > 1200, "data-only report fell through to a blank page"


def test_preview_honest_message_when_nothing_to_render():
    """A file with neither layout nor data gets an honest message, not a
    blank page (e.g. a Word/PDF/SQL file mislabeled .xml)."""
    html = render_mockup(parse_oracle_xml(NO_LAYOUT_NO_DATA), "frontend")
    assert "No renderable report content" in html


def test_design_time_fill_detection_and_drop():
    """Oracle pink/lavender design-time fills are detected and dropped to the
    band default, while genuine light/dark bands are kept."""
    assert _is_design_fill("#FFE0FF")
    assert _is_design_fill("#FFBFFF")
    assert not _is_design_fill("#EEEEEE")   # light gray kept
    assert not _is_design_fill("#00008B")   # navy kept
    assert _band_bg("#FFE0FF", "#006400") == "#006400"   # dropped to default
    assert _band_bg("#123456", "#006400") == "#123456"   # genuine band kept

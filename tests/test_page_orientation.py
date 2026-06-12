"""Page orientation: a wide Oracle report -> a LANDSCAPE (wide) SSRS page.

The converter hardcoded PageWidth=8.5in and capped the table to portrait width,
so a wide multi-column Oracle grid (content spanning > portrait's usable area)
was forced into portrait and its columns COMPRESSED. These lock that a wide
content span now widens the page + columns, while a normal portrait report is
unchanged (byte-identical PageWidth + column widths -- the no-regression gate).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402


def _wide_report(ncol: int, span: float) -> bytes:
    cols = [f"C{i}" for i in range(ncol)]
    items = "".join(f'<dataItem name="{c}" datatype="vchar2"/>' for c in cols)
    cw = span / ncol
    fields = "".join(
        f'<field name="F{i}" source="{c}">'
        f'<geometryInfo x="{i*cw:.2f}" y="0.3" width="{cw-0.1:.2f}" height="0.2"/></field>'
        for i, c in enumerate(cols))
    return (
        f'<report name="W" DTDVersion="9.0.2.0.10"><data>'
        f'<dataSource name="Q_1"><select><![CDATA[SELECT {",".join(cols)} FROM t]]></select>'
        f'<group name="G">{items}</group></dataSource></data>'
        f'<layout><section name="main"><body width="{span}" height="9">'
        f'<repeatingFrame name="R" source="G">'
        f'<geometryInfo x="0" y="0.3" width="{span}" height="0.3"/>{fields}'
        f'</repeatingFrame></body></section></layout></report>'
    ).encode()


def _page_width(rdl: str) -> float:
    return float(re.search(r"<PageWidth>([\d.]+)in</PageWidth>", rdl).group(1))


def _first_col_width(rdl: str) -> float:
    return float(re.search(r"<TablixColumn>\s*<Width>([\d.]+)in</Width>", rdl).group(1))


def test_portrait_report_keeps_8_5_page_unchanged():
    # content span 7in < portrait usable -> portrait page (no landscape widen)
    rdl = convert(_wide_report(8, 7.0))["rdl_xml"]
    assert _page_width(rdl) == 8.5
    # columns follow the (narrow) Oracle field widths, not a stretched uniform
    assert _first_col_width(rdl) < 1.0


def test_wide_report_gets_landscape_page_and_wider_columns():
    portrait = convert(_wide_report(8, 7.0))["rdl_xml"]
    landscape = convert(_wide_report(8, 12.0))["rdl_xml"]
    # the wide content span pushes the page past portrait...
    assert _page_width(landscape) > 8.5
    # ...and the columns expand to use it (vs the compressed portrait width)
    assert _first_col_width(landscape) > _first_col_width(portrait)


def test_landscape_page_width_is_bounded():
    # an absurdly wide span is capped (no 80in pages)
    rdl = convert(_wide_report(40, 60.0))["rdl_xml"]
    assert 8.5 < _page_width(rdl) <= 17.0

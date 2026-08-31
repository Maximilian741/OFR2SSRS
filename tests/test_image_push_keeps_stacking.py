"""A header image pushes data regions below it as ONE uniform translation.

`_ensure_layout_images_emitted` appends body-direct images and keeps them
from painting over data regions by pushing overlapped Tablix/List items
down. Pushing EACH region independently to the same below-image line
collapsed a two-matrix stack onto one origin and interleaved both
cross-tabs' ink (render-measured on a wild two-pivot roll-up: paint gate
0 -> 12 the moment the page shrank to its declared size). The regions'
RELATIVE stacking is part of the declaration and must survive the push.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from types import SimpleNamespace

from converter.generators import rdl as R


def _mk_body(tablixes):
    root = ET.Element(R._q("Report"))
    body = ET.SubElement(root, R._q("Body"))
    ri = ET.SubElement(body, R._q("ReportItems"))
    h = ET.SubElement(body, R._q("Height"))
    h.text = "3.00in"
    for nm, top in tablixes:
        t = ET.SubElement(ri, R._q("Tablix"))
        t.set("Name", nm)
        for tag, v in (("Top", f"{top:.2f}in"), ("Left", "0.05in"),
                       ("Height", "1.15in"), ("Width", "3.3in")):
            e = ET.SubElement(t, R._q(tag))
            e.text = v
    return root


def _mk_report(img_h=7.14):
    img = SimpleNamespace(kind="image", image_id="LOGO", name="B_LOGO",
                          x=0.0, y=0.0, width=4.0, height=img_h)
    grp = SimpleNamespace(fields=[img], children=[])
    return SimpleNamespace(layout=[grp], _image_assets={"LOGO": "EMB_LOGO"})


def _tops(root):
    out = {}
    for t in root.iter(R._q("Tablix")):
        out[t.get("Name")] = float(
            (t.findtext(R._q("Top")) or "0in").replace("in", ""))
    return out


def test_push_preserves_relative_stacking_of_two_regions():
    root = _mk_body([("Tablix_Matrix", 0.10), ("Tablix_Matrix_2", 1.50)])
    R._ensure_layout_images_emitted(root, _mk_report())
    tops = _tops(root)
    # both cleared below the image bottom (7.14 + 0.05 pad)
    assert tops["Tablix_Matrix"] >= 7.14, tops
    # the declared 1.40in stack gap survives the push EXACTLY
    gap = tops["Tablix_Matrix_2"] - tops["Tablix_Matrix"]
    assert abs(gap - 1.40) < 0.005, (
        "the push collapsed the region stack -- both cross-tabs would "
        "interleave their ink", tops)


def test_single_region_still_lands_just_below_the_image():
    root = _mk_body([("Tablix_Matrix", 0.10)])
    R._ensure_layout_images_emitted(root, _mk_report())
    tops = _tops(root)
    assert abs(tops["Tablix_Matrix"] - (7.14 + 0.05)) < 0.005, tops


def test_region_clear_of_the_image_is_not_moved():
    root = _mk_body([("Tablix_Main", 8.00)])
    R._ensure_layout_images_emitted(root, _mk_report())
    tops = _tops(root)
    assert abs(tops["Tablix_Main"] - 8.00) < 0.005, tops

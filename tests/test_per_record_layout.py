"""Regression lock-in for per-record (certificate / letter) body layout.

Pins down two generic, name-agnostic behaviours added to the RDL
generator:

  A. _center_sibling_frame_rows -- horizontally centres each row of
     sibling frames in the body. A row is one main frame, or a strip of
     side-by-side cards (frames whose vertical extents overlap). Every
     frame keeps its width; gaps between frames in a row are preserved.
     Bug history: the certificate box and the two wallet cards rendered
     at their raw Oracle x-coordinates, so they sat off-centre once the
     8in Oracle body was mapped into the 7.5in RDL body.

  B. _image_field_binding -- a layout field bound to a database BLOB
     column (Oracle marks signature/photo columns datatype="blob") is
     recognised so it can render as a real <Image> instead of a textbox
     of raw bytes. Bug history: the bureau-chief signature rendered as
     an empty textbox and a stray page-footer copy.

Both are keyed purely off parsed geometry / datatype -- no report,
column, or parameter names are hard-coded.
"""
from __future__ import annotations

import pytest

from converter.generators.rdl import (
    _center_sibling_frame_rows,
    _image_field_binding,
)

BODY_W = 7.5


class _Frame:
    """Minimal stand-in for a parsed layout frame."""

    def __init__(self, x, y, width, height):
        self.x, self.y, self.width, self.height = x, y, width, height


def _gap(parent_w, x, width):
    """(left gap, right gap) of a box of `width` placed at `x`."""
    return round(x, 4), round(parent_w - x - width, 4)


# --- A. frame centering ----------------------------------------------------

def test_single_narrow_frame_is_centered():
    f = _Frame(x=1.0, y=0.0, width=3.0, height=1.0)
    deltas = _center_sibling_frame_rows([f], BODY_W)
    new_x = f.x + deltas[id(f)]
    left, right = _gap(BODY_W, new_x, f.width)
    assert left == pytest.approx(right), "narrow frame should be centered"


def test_overwide_frame_is_pushed_to_left_inset():
    # A frame wider than the body cannot be centered by shifting alone --
    # it is pushed flush to the inset so the downstream clamp centers it.
    f = _Frame(x=0.15, y=0.0, width=7.71, height=8.0)
    deltas = _center_sibling_frame_rows([f], BODY_W)
    assert (f.x + deltas[id(f)]) == pytest.approx(0.02, abs=1e-6)


def test_two_side_by_side_frames_centered_as_a_group():
    left = _Frame(x=0.05, y=8.8, width=3.18, height=1.9)
    right = _Frame(x=3.85, y=8.8, width=3.18, height=1.9)
    deltas = _center_sibling_frame_rows([left, right], BODY_W)
    # Same delta -> the row moves as a unit, inter-card gap unchanged.
    assert deltas[id(left)] == pytest.approx(deltas[id(right)])
    span_lo = left.x + deltas[id(left)]
    span_hi = right.x + deltas[id(right)] + right.width
    lgap, rgap = round(span_lo, 4), round(BODY_W - span_hi, 4)
    assert lgap == pytest.approx(rgap, abs=1e-3), "card row should be centered"


def test_card_gap_is_preserved():
    left = _Frame(x=0.05, y=8.8, width=3.18, height=1.9)
    right = _Frame(x=3.85, y=8.8, width=3.18, height=1.9)
    gap_before = right.x - (left.x + left.width)
    deltas = _center_sibling_frame_rows([left, right], BODY_W)
    gap_after = ((right.x + deltas[id(right)])
                 - (left.x + deltas[id(left)] + left.width))
    assert gap_after == pytest.approx(gap_before)


def test_non_overlapping_frames_are_separate_rows():
    # Vertically stacked frames do not share a row -- each centers alone.
    top = _Frame(x=1.0, y=0.0, width=3.0, height=2.0)
    bottom = _Frame(x=2.0, y=5.0, width=3.0, height=2.0)
    deltas = _center_sibling_frame_rows([top, bottom], BODY_W)
    assert (top.x + deltas[id(top)]) == pytest.approx(
        (bottom.x + deltas[id(bottom)])), "both rows should center to same x"


def test_already_centered_frame_gets_no_delta():
    f = _Frame(x=(BODY_W - 3.0) / 2.0, y=0.0, width=3.0, height=1.0)
    deltas = _center_sibling_frame_rows([f], BODY_W)
    assert id(f) not in deltas, "a centered frame should not be shifted"


# --- B. blob / image field detection --------------------------------------

class _Item:
    def __init__(self, name, datatype):
        self.name, self.datatype = name, datatype


class _Query:
    def __init__(self, name, items):
        self.name, self.items = name, items


class _Report:
    def __init__(self, queries):
        self.queries = queries


class _LayoutField:
    def __init__(self, source, kind="field"):
        self.source, self.kind = source, kind


def test_blob_column_is_recognized_as_image():
    rep = _Report([_Query("Q_SIGNATURE", [_Item("Signature", "blob")])])
    binding = _image_field_binding(_LayoutField("Signature"), rep)
    assert binding == ("Q_SIGNATURE", "Signature")


def test_text_column_is_not_an_image():
    rep = _Report([_Query("Q_MAIN", [_Item("Permittee", "character")])])
    assert _image_field_binding(_LayoutField("Permittee"), rep) is None


def test_non_field_kind_is_never_an_image():
    rep = _Report([_Query("Q_SIGNATURE", [_Item("Signature", "blob")])])
    # An embedded-image LayoutField (kind="image") is handled elsewhere.
    lf = _LayoutField("Signature", kind="image")
    assert _image_field_binding(lf, rep) is None

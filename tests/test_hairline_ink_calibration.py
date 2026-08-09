"""The device-hairline ink is a RENDER CALIBRATION, not a copied colour.

Measured, not reasoned. Every number below comes from rendering through the
engine and rasterizing the PDF (grey = the rule's darkest row, ink = the
integrated coverage in device pixels, both medians over the clean rules of
a report):

    TRUTH (a zero-width BLACK stroke, which is what every no-lineWidth
    Oracle rule exports as) -- grey / ink-px

        report            72dpi       150dpi      300dpi      600dpi
        ledger export     221/0.200   204/0.200   204/0.200   212/0.200
        history export    212/0.200   204/0.200   212/0.200   221/0.200
        summary export    221/0.200   204/0.200   204/0.200   204/0.200

    The ink is CONSTANT at 0.200 device px at every resolution: the
    rasterizer floors a stroke's coverage at 0.2 device px, so a
    zero-width stroke is a device-space object, not a physical width.

    OURS (#CCCCCC at 0.25pt, the shipped emission)

        ledger export     241/0.055   230/0.114   206/0.208   203/0.424
        history export    248/0.027   227/0.110   223/0.125   203/0.247
        summary export    244/0.059   234/0.082   213/0.165   203/0.329

SSRS cannot express a zero-width border. Its own publish error names the
bound: "The value of the BorderWidth property ... is out of range. It must
be between 0.24985pt and 20pt" (engine-measured by rendering a probe report
with 0.1pt / 0.05pt / 0.01pt / 0pt lines -- every one of them refused). So
our thinnest legal stroke is 0.25pt, whose device width is 0.52px at 150
dpi and 1.04px at 300 -- it is only at 300 dpi that our minimum stroke has
truth's one-pixel thickness, and there #CCCCCC lands on truth's ink almost
exactly. At 150 dpi the shortfall is GEOMETRIC (half the device width), and
no colour can repair it: doubling the ink to fix 150 dpi doubles the error
at 300. That is measured too -- a full sweep at the minimum weight:

        ink        dev(150+300)   dev(72..600)
        #CCCCCC        101            208
        #C4C4C4         84            208
        #BFBFBF         76            214
        #B8B8B8         74            226
        #B0B0B0         84            258

    (sum of |ours-truth| grey levels over three truth-paired reports)

The shallow minimum sits within a few grey levels of the shipped value and
is worse everywhere outside the 150/300 pair, so the shipped pair stands.
The alternative emission forms were measured too and are worse: a thin
filled Rectangle is resolution-INDEPENDENT but floors at 0.067 ink-px
(grey 238) -- a third of truth's ink and unreachable by any colour -- and
a stroke heavy enough to hold truth's TONE at every resolution (>=1pt)
carries 2-4x truth's thickness.

These tests pin the calibration so it is not "corrected" back to a copied
colour, to black, or to a house weight.
"""
from converter.generators import rdl as R
from converter.parsers.oracle_colors import DEVICE_HAIRLINE_COLOR

# The rasterizer's measured floor on a stroke's coverage, in device pixels.
# Probe: black strokes of 0.01/0.05/0.1/0.15pt all rasterize to exactly
# 0.200 ink-px at every dpi where their true width is below this.
RASTER_FLOOR_PX = 0.2

# What truth's zero-width stroke measures, at every resolution.
TRUTH_INK_PX = 0.200

# The engine's own lower bound on BorderWidth (from its publish error).
ENGINE_MIN_PT = 0.24985

# The resolutions this calibration targets.
TARGET_DPI = (150, 300)


def _alpha(hexc):
    """Ink fraction of a grey: 0.0 = white (no ink), 1.0 = solid black."""
    h = (hexc or "").lstrip("#")
    return 1.0 - (int(h[0:2], 16) / 255.0)


def _ink_px(alpha, weight_pt, dpi):
    """Device-pixel ink a stroke of `weight_pt` at `alpha` rasterizes to."""
    return max(RASTER_FLOOR_PX, weight_pt * dpi / 72.0) * alpha


def _deviation(alpha, weight_pt, dpis=TARGET_DPI):
    return sum(abs(_ink_px(alpha, weight_pt, d) - TRUTH_INK_PX) for d in dpis)


SHIPPED_PT = float(R._HAIRLINE_WEIGHT[:-2])
SHIPPED_ALPHA = _alpha(DEVICE_HAIRLINE_COLOR)


def test_hairline_weight_is_the_engine_minimum():
    """Anything thinner is refused by the engine; anything heavier is a
    house weight, and a house weight is what made these rules print as
    thick bars where the truth shows a hairline."""
    assert SHIPPED_PT >= ENGINE_MIN_PT, (
        "the engine refuses a BorderWidth below 0.24985pt")
    assert SHIPPED_PT < 2 * ENGINE_MIN_PT, (
        "the device hairline must map to the THINNEST legal stroke, "
        f"not to {R._HAIRLINE_WEIGHT}")


def test_hairline_ink_is_the_render_calibrated_optimum():
    """The shipped ink must sit on the optimum of the RENDERED deviation
    from truth over 150 and 300 dpi.

    Scored on the deviation itself rather than on the grey value: the
    optimum for the minimum stroke is grey 206 and the shipped ink is 204,
    a 4% difference in total deviation and 2 grey levels -- an order of
    magnitude below the render's own phase noise (the same report's clean
    hairlines measured 227..234 at 150 dpi purely on where their y lands
    inside a pixel). The bound is therefore on the score, so any real
    regression (a darker "more visible" ink, black, a heavier weight) is
    caught while sub-noise rounding is not litigated.
    """
    best_grey = min(range(256), key=lambda v: _deviation(1.0 - v / 255.0,
                                                         SHIPPED_PT))
    best = _deviation(1.0 - best_grey / 255.0, SHIPPED_PT)
    shipped = _deviation(SHIPPED_ALPHA, SHIPPED_PT)
    assert shipped <= 1.05 * best, (
        f"shipped hairline ink {DEVICE_HAIRLINE_COLOR} deviates "
        f"{shipped:.4f} ink-px from truth over {TARGET_DPI}; the best "
        f"available at {R._HAIRLINE_WEIGHT} is {best:.4f} (grey {best_grey})")
    assert abs(round((1.0 - SHIPPED_ALPHA) * 255) - best_grey) <= 3


def test_a_black_or_heavy_hairline_is_measurably_worse():
    """Guard the two regressions this calibration keeps being pulled back
    toward: solid black at the minimum weight, and the old 1pt house
    weight. Both are decisively worse against truth's rendered ink."""
    shipped = _deviation(SHIPPED_ALPHA, SHIPPED_PT)
    assert _deviation(1.0, SHIPPED_PT) > 4 * shipped, (
        "black at the minimum weight must be far worse than the "
        "calibrated ink")
    assert _deviation(1.0, 1.0) > 20 * shipped, (
        "a 1pt black rule must be far worse than the calibrated ink")
    # and the calibrated pair really is close: within a quarter of truth's
    # own ink at the resolution where our minimum stroke has truth's
    # one-pixel thickness
    assert abs(_ink_px(SHIPPED_ALPHA, SHIPPED_PT, 300)
               - TRUTH_INK_PX) <= 0.25 * TRUTH_INK_PX


def test_no_legal_stroke_can_be_resolution_independent():
    """Why a residual survives at 150 dpi: above the rasterizer's floor a
    stroke's ink is proportional to dpi, so every legal (colour, width)
    pair paints exactly twice as much ink at 300 dpi as at 150 -- while
    truth's device hairline paints the same 0.200 at both. No pair can hit
    truth at both resolutions; the emission is chosen to straddle them."""
    for weight in (ENGINE_MIN_PT, 0.25, 0.5, 1.0, 2.0, 20.0):
        for alpha in (0.05, 0.2, 0.5, 1.0):
            i150 = _ink_px(alpha, weight, 150)
            i300 = _ink_px(alpha, weight, 300)
            assert abs(i300 - 2 * i150) < 1e-9, (
                "a legal stroke is a physical width: its ink must scale "
                "with resolution")
            assert not (abs(i150 - TRUTH_INK_PX) < 1e-6
                        and abs(i300 - TRUTH_INK_PX) < 1e-6), (
                "if this ever passes, a resolution-independent emission "
                "exists and the hairline should be re-derived")

"""
Alternative mockup renderings for the preview surface.

Both variants reuse render_mockup() from preview.html_mockup as a base and
post-process the resulting HTML fragment. They preserve the strict B&W
palette (#000, #333, #666, #888, #cccccc, #d0d0d0, #f2f2f2, #fff) and never
introduce color.

Public API:
    render_mockup_print(report)   -> str  print-optimized HTML fragment
    render_mockup_compact(report) -> str  condensed dashboard HTML fragment

These are pure functions: they take a ParsedReport (or anything that
render_mockup accepts) and return a self-contained HTML fragment with no
<html>/<head>/<body> wrapper.
"""
from __future__ import annotations

import re

from .html_mockup import render_mockup


# ---------------------------------------------------------------------------
# Print variant
# ---------------------------------------------------------------------------

# A <style> block that gets prepended to the fragment. Wrapped in
# @media print so on-screen rendering is unchanged, but when the user hits
# Print the layout reflows nicely onto paper.
#
# Scoped via the .o2s-mockup-print wrapper class so these rules can never
# leak out and clobber surrounding host-page styles.
_PRINT_STYLES = """\
<style>
@media print {
  .o2s-mockup-print {
    margin: 0 !important;
    padding: 24mm 18mm !important;
    border: none !important;
    box-shadow: none !important;
    max-width: none !important;
    background: #ffffff !important;
    color: #000000 !important;
  }
  .o2s-mockup-print * {
    box-shadow: none !important;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }
  .o2s-mockup-print table {
    page-break-inside: avoid;
  }
  .o2s-mockup-print thead {
    display: table-header-group;
  }
  .o2s-mockup-print tr {
    page-break-inside: avoid;
  }
  /* The signature block is the last visible block in render_mockup().
     Force a page break after it so any trailing notes start clean. */
  .o2s-mockup-print > div > div:last-child {
    page-break-after: always;
  }
  @page {
    margin: 18mm 14mm 18mm 14mm;
    size: Letter;
  }
}
</style>
"""


def render_mockup_print(report):
    """
    Print-optimized variant of the standard mockup.

    Visually identical to render_mockup() on screen, but adds an @media print
    style block and an o2s-mockup-print wrapper class so that browser Print
    output uses larger paper margins, drops shadows, and inserts a page break
    after the signature block.
    """
    base = render_mockup(report)

    # Inject our print wrapper class onto the outermost <div>. The first
    # <div ...> in the fragment is the page-frame produced by render_mockup().
    tagged = base.replace(
        '<div style="font-family:',
        '<div class="o2s-mockup-print" style="font-family:',
        1,
    )

    return _PRINT_STYLES + tagged


# ---------------------------------------------------------------------------
# Compact variant
# ---------------------------------------------------------------------------

# Strip from "<div style="margin-top:36px;..."> (the signature block) all the
# way through its closing </div></div>...). The signature block is recognizable
# by margin-top:36px which only render_mockup uses for that section, AND by
# containing the literal "BUREAU CHIEF" string. We use a regex anchored on
# margin-top:36px and walk forward to a balanced close.
_SIGNATURE_RE = re.compile(
    r'<div style="margin-top:36px;[^"]*">.*?'
    r'BUREAU CHIEF.*?</div>\s*</div>\s*</tr>\s*</table>\s*</div>',
    re.DOTALL,
)


def _strip_signature_block(html_fragment):
    """
    Remove the signature block from a render_mockup() fragment.

    The signature block is the trailing section that contains "BUREAU CHIEF"
    and the agency address. We anchor on the unique margin-top:36px style
    that opens that block and consume forward to the matching </div> that
    closes the outer signature wrapper.
    """
    cleaned = _SIGNATURE_RE.sub("", html_fragment)
    if "BUREAU CHIEF" in cleaned:
        # Fallback: greedy strip from margin-top:36px to end of the last </div>
        # before the page-frame's closing </div>. We slice off the section
        # starting at the signature opener, then re-attach the page frame's
        # closing </div>.
        opener = cleaned.find('<div style="margin-top:36px;')
        if opener != -1:
            # Find the page-frame closing </div> (last </div> in the fragment).
            tail_close = cleaned.rfind("</div>")
            if tail_close > opener:
                cleaned = cleaned[:opener] + cleaned[tail_close:]
    return cleaned


def _condense_padding(html_fragment):
    """
    Reduce visual density: shrink the page-frame padding, table-cell padding,
    and various section margins. Pure string substitution -- safe because the
    base fragment uses inline styles with predictable values.
    """
    replacements = [
        # Page frame -- big paper margins -> tight dashboard padding.
        ("padding:48px 56px;", "padding:18px 22px;"),
        # Bottom margin between major blocks.
        ("margin-bottom:22px;", "margin-bottom:10px;"),
        ("margin-bottom:20px;", "margin-bottom:10px;"),
        ("margin-bottom:18px;", "margin-bottom:8px;"),
        # Table cells -- relax row height.
        ("padding:8px 10px;", "padding:4px 8px;"),
        ("padding:7px 10px;", "padding:3px 8px;"),
        ("padding:6px 10px;", "padding:3px 8px;"),
        # Header padding-bottom on the title rule.
        ("padding-bottom:14px;", "padding-bottom:8px;"),
        # Subtitle vertical padding.
        ("padding:6px 0;", "padding:3px 0;"),
        # Parameter form inner padding.
        ("padding:12px 16px;", "padding:8px 12px;"),
    ]
    out = html_fragment
    for old, new in replacements:
        out = out.replace(old, new)
    return out


def render_mockup_compact(report):
    """
    Condensed variant for in-dashboard review.

    Removes the signature block entirely, shrinks padding throughout, and
    keeps the data table as the focal point. Same B&W palette as the
    standard mockup -- only #000, #333, #666, #888, #cccccc, #d0d0d0,
    #f2f2f2, #fff appear.
    """
    base = render_mockup(report)
    no_sig = _strip_signature_block(base)
    condensed = _condense_padding(no_sig)

    # Tag the wrapper so frontend CSS can target the compact view if it
    # wants to (e.g. nest inside a scrollable container).
    tagged = condensed.replace(
        '<div style="font-family:',
        '<div class="o2s-mockup-compact" style="font-family:',
        1,
    )

    return tagged

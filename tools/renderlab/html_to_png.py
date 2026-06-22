"""Render an HTML file to a PNG using Playwright + the system Edge/Chrome.

Used by the per-report 1:1 convergence loop so an independent judge can SEE the
HTML mockup as an image (alongside the real-artifact PNG and the MS-engine RDL
render) rather than only its source.

Usage:
    python tools/renderlab/html_to_png.py <in.html> <out.png> [width_px]
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

_CHANNELS = ["msedge", "chrome"]


def render(html_path: str, out_png: str, width: int = 1100) -> None:
    url = Path(html_path).resolve().as_uri()
    last_err = None
    for channel in _CHANNELS:
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(channel=channel, headless=True)
                page = browser.new_page(viewport={"width": width, "height": 1400},
                                        device_scale_factor=2)
                page.goto(url, wait_until="networkidle")
                page.screenshot(path=out_png, full_page=True)
                browser.close()
            return
        except Exception as e:  # noqa: BLE001 - try the next channel
            last_err = e
    raise RuntimeError(f"no usable browser channel ({_CHANNELS}): {last_err}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    w = int(sys.argv[3]) if len(sys.argv) > 3 else 1100
    render(sys.argv[1], sys.argv[2], w)
    print("PNG:", sys.argv[2])

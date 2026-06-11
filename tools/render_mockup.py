"""Render an Oracle2SSRS HTML mockup to PNG(s) with Playwright/Chromium so the
mockup can be visually diffed against the real Oracle front-end screenshots.

    python tools/render_mockup.py <oracle.xml> <out_dir>

Writes out_dir/page_1.png, page_2.png, ... (one per .doc-page in the mockup)
plus full.png. Headless, deterministic. Used by the mockup-fidelity loop.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402


def render(xml_path: str, out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    data = convert(Path(xml_path).read_bytes())
    html = data["mockup_html"]
    doc = ("<!doctype html><html><head><meta charset='utf-8'>"
           "<style>body{background:#9aa0a6;margin:0;padding:24px;}</style>"
           "</head><body>" + html + "</body></html>")
    page_html = out / "_mockup.html"
    page_html.write_text(doc, encoding="utf-8")

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1000, "height": 1300},
                                device_scale_factor=2)
        page.goto(page_html.as_uri())
        page.wait_for_timeout(400)
        page.screenshot(path=str(out / "full.png"), full_page=True)
        # Each mockup page is the WHITE SHEET div that immediately follows a
        # "Page N of M" label. Screenshot that sibling so each crop is one
        # real report page.
        n = page.evaluate("""() => {
          const labels = [...document.querySelectorAll('div')]
            .filter(d => /^Page \\d+ of \\d+$/.test(d.textContent.trim())
                         && d.children.length === 0);
          window.__sheets = labels.map(l => l.nextElementSibling).filter(Boolean);
          return window.__sheets.length;
        }""")
        for i in range(int(n)):
            el = page.evaluate_handle(f"() => window.__sheets[{i}]")
            try:
                el.as_element().screenshot(path=str(out / f"page_{i + 1}.png"))
            except Exception:
                pass
        browser.close()
    print(f"rendered {data['report'].get('name')} -> {out}/full.png "
          f"(+{n} page crops)")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    render(sys.argv[1], sys.argv[2])

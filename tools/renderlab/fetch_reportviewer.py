"""Fetch the Microsoft ReportViewer 2015 runtime (RDL-2008 capable) from
nuget.org and unpack its DLLs into tools/renderlab/lib/ (gitignored).

These are official Microsoft packages; a .nupkg is a zip. We try a list of
known package ids so a renamed/unlisted package doesn't strand us.
"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path
from urllib.request import urlopen, Request

LIB = Path(__file__).resolve().parent / "lib"

# (package_id, version) candidates, in preference order. v12 = ReportViewer
# 2015 — processes RDL 2008/01 schema (v15/150.x dropped 2008 for 2016).
CANDIDATES = [
    ("Microsoft.ReportViewer.Runtime.WinForms", "12.0.2402.15"),
    ("Microsoft.ReportViewer.Runtime.Common", "12.0.2402.15"),
    # Spatial types assembly (v12.0.0.0) — the processing engine references
    # it unconditionally even for reports with no spatial data.
    ("Microsoft.SqlServer.Types", "12.0.5000.0"),
]

EXTRA_FALLBACKS = [
    ("Microsoft.ReportViewer.2015.Runtime", "12.0.2402.10"),
]


def _fetch(pkg: str, ver: str) -> bytes | None:
    url = f"https://www.nuget.org/api/v2/package/{pkg}/{ver}"
    try:
        req = Request(url, headers={"User-Agent": "renderlab/1.0"})
        with urlopen(req, timeout=120) as r:
            data = r.read()
        print(f"  ok  {pkg} {ver}  ({len(data)//1024} KB)")
        return data
    except Exception as e:  # noqa: BLE001
        print(f"  --  {pkg} {ver}: {type(e).__name__}: {e}")
        return None


def _unpack_dlls(nupkg: bytes) -> int:
    n = 0
    with zipfile.ZipFile(io.BytesIO(nupkg)) as z:
        for name in z.namelist():
            if not name.lower().endswith(".dll"):
                continue
            base = Path(name).name
            dest = LIB / base
            if dest.exists():
                continue
            dest.write_bytes(z.read(name))
            n += 1
            print(f"      -> {base}")
    return n


def main() -> int:
    LIB.mkdir(parents=True, exist_ok=True)
    got_any = False
    print("Fetching ReportViewer runtime packages from nuget.org ...")
    for pkg, ver in CANDIDATES:
        data = _fetch(pkg, ver)
        if data:
            _unpack_dlls(data)
            got_any = True
    needed = {"Microsoft.ReportViewer.WinForms.dll",
              "Microsoft.ReportViewer.Common.dll",
              "Microsoft.ReportViewer.ProcessingObjectModel.dll"}
    have = {p.name for p in LIB.glob("*.dll")}
    missing = needed - have
    if missing:
        print(f"Missing after primary fetch: {sorted(missing)} — trying fallbacks")
        for pkg, ver in EXTRA_FALLBACKS:
            data = _fetch(pkg, ver)
            if data:
                _unpack_dlls(data)
        have = {p.name for p in LIB.glob("*.dll")}
        missing = needed - have
    print(f"\nDLLs present: {sorted(have)}")
    if missing:
        print(f"STILL MISSING: {sorted(missing)}")
        return 1
    print("RenderLab ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
Oracle -> SSRS Converter
Flask web app entry point. Run with `python backend/app.py` from the project root.
"""
from __future__ import annotations

import os
import io
import json
import traceback
from pathlib import Path

from flask import Flask, request, jsonify, render_template, send_file, abort

# Make `from converter import ...` work whether you run from the repo root or backend/.
import sys
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from converter import convert, run_query  # noqa: E402
from converter.ingest import convert_bundle  # noqa: E402

ROOT = HERE.parent
SAMPLES = ROOT / "samples" / "oracle"

app = Flask(
    __name__,
    template_folder=str(ROOT / "frontend" / "templates"),
    static_folder=str(ROOT / "frontend" / "static"),
)

# In-memory cache of the most recent conversion (per-session would be nicer
# but a single demo machine doesn't need it).
_LAST = {"report": None, "rdl_xml": "", "oracle_xml": "", "mockup_html": ""}


@app.route("/")
def index():
    sample_files = sorted(p.name for p in SAMPLES.glob("*.xml")) if SAMPLES.exists() else []
    return render_template("index.html", samples=sample_files)


@app.post("/api/convert")
def api_convert():
    """Accept an uploaded .xml/.rdf file and return the full conversion payload."""
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "no file uploaded"}), 400
    try:
        data = convert(f.read())
        global _LAST
        _LAST = data
        return jsonify(data)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.post("/api/convert-bundle")
def api_convert_bundle():
    """Accept a folder / multi-file upload of mixed Oracle artifacts."""
    files = []
    for f in request.files.getlist("files"):
        try:
            files.append((f.filename, f.read()))
        except Exception:
            continue
    if not files:
        return jsonify({"error": "no files uploaded"}), 400
    try:
        data = convert_bundle(files)
        if "report" in data:
            global _LAST
            _LAST = data
        return jsonify(data)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.post("/api/convert-sample/<name>")
def api_convert_sample(name):
    """Run the converter against one of the bundled sample files."""
    safe = SAMPLES / name
    if not safe.exists() or safe.parent != SAMPLES:
        abort(404)
    try:
        data = convert(safe.read_bytes())
        global _LAST
        _LAST = data
        return jsonify(data)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.get("/api/download/rdl")
def api_download_rdl():
    """Download the most recently generated RDL."""
    rdl = _LAST.get("rdl_xml") or ""
    if not rdl:
        abort(404)
    name = (_LAST.get("report") or {}).get("name") or "report"
    return send_file(
        io.BytesIO(rdl.encode("utf-8")),
        mimetype="application/xml",
        as_attachment=True,
        download_name=f"{name}.rdl",
    )


@app.post("/api/run-query")
def api_run_query():
    """Run a (translated) T-SQL query against the bundled sample SQLite DB."""
    payload = request.get_json(silent=True) or {}
    sql = payload.get("sql") or ""
    params = payload.get("parameters") or {}
    try:
        rows, columns, warnings = run_query(sql, params)
        return jsonify({"rows": rows, "columns": columns, "warnings": warnings})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.get("/api/health")
def api_health():
    return jsonify({"ok": True, "samples": [p.name for p in SAMPLES.glob("*.xml")] if SAMPLES.exists() else []})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5057"))
    print("=" * 70)
    print(f"  Oracle -> SSRS Converter   http://127.0.0.1:{port}")
    print("=" * 70)
    app.run(host="127.0.0.1", port=port, debug=True)

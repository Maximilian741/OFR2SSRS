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

# Load .env from project root, BEFORE importing converter modules.
# This way the key is in os.environ no matter how Flask was launched.
try:
    from dotenv import load_dotenv
    from pathlib import Path as _Path
    load_dotenv(_Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

from flask import Flask, request, jsonify, render_template, send_file, abort

# Make `from converter import ...` work whether you run from the repo root or backend/.
import sys
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from converter import convert, run_query  # noqa: E402
from converter.ingest import convert_bundle  # noqa: E402
from converter.bundle_export import build_bundle_zip  # noqa: E402

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


def _asset_version():
    """Cache-busting token (mtime of app.js). Auto-bumps on every JS change."""
    try:
        return str(int((ROOT / "frontend" / "static" / "js" / "app.js").stat().st_mtime))
    except Exception:
        return "0"


@app.route("/")
def index():
    sample_files = sorted(p.name for p in SAMPLES.glob("*.xml")) if SAMPLES.exists() else []
    return render_template("index.html", samples=sample_files, asset_version=_asset_version())


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


@app.post("/api/compare")
def api_compare():
    """Compare two Oracle XML reports (file_a, file_b) and return a structured diff."""
    a = request.files.get("file_a")
    b = request.files.get("file_b")
    if not a or not b:
        return jsonify({"error": "need two files (file_a, file_b)"}), 400
    from converter.parsers.oracle_xml import parse_oracle_xml
    from converter.compare import compare_reports
    try:
        ra = parse_oracle_xml(a.read())
        rb = parse_oracle_xml(b.read())
        return jsonify(compare_reports(ra, rb))
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


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


@app.get("/api/download/bundle")
def api_download_bundle():
    """Download every artifact for the most recent conversion as a single zip."""
    data = _LAST
    if not data or not data.get("rdl_xml"):
        abort(404)
    blob = build_bundle_zip(data)
    name = (data.get("report") or {}).get("name") or "report"
    return send_file(
        io.BytesIO(blob),
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{name}_bundle.zip",
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


@app.get("/api/ai/test")
def api_ai_test():
    """One-shot test call to Anthropic to surface auth/model errors clearly."""
    from converter.ai_runner import _call_claude, DEFAULT_MODEL, _api_key, is_configured
    if not is_configured():
        return jsonify({"ok": False, "error": "ai_not_configured"}), 400
    try:
        out = _call_claude(
            "Reply with only the word: hello",
            api_key=_api_key(),
            model=DEFAULT_MODEL,
            max_tokens=20,
        )
        return jsonify({"ok": True, "model": DEFAULT_MODEL, "response": out[:200]})
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "ok": False,
            "error_type": type(e).__name__,
            "error": str(e)[:1000],
            "model": DEFAULT_MODEL,
        }), 500


@app.get("/api/ai/status")
def api_ai_status():
    """Returns whether Auto-AI is configured (API key present + SDK installed)."""
    from converter.ai_runner import is_configured, DEFAULT_MODEL
    return jsonify({"configured": is_configured(), "model": DEFAULT_MODEL})


@app.post("/api/auto-fix")
def api_auto_fix():
    """One-button: call Claude on every AI prompt, apply each valid result."""
    global _LAST
    data = _LAST
    if not data or not data.get("rdl_xml"):
        return jsonify({"error": "no report converted yet"}), 400
    from converter.ai_runner import auto_fix, is_configured
    if not is_configured():
        return jsonify({
            "error": "ai_not_configured",
            "hint": "Set ANTHROPIC_API_KEY in your .env (see .env.example)."
        }), 400
    try:
        updated = auto_fix(data)
        _LAST = updated
        return jsonify({
            "ok": True,
            "summary": updated.get("ai_summary", {}),
            "results": [
                {"id": r.get("id"), "name": r.get("target", {}).get("name"),
                 "ok": r.get("ok"), "applied": r.get("applied", False),
                 "error": r.get("error")}
                for r in updated.get("ai_results", [])
            ],
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.post("/api/apply-fix")
def api_apply_fix():
    """Apply a pasted AI translation back into the most recent conversion."""
    global _LAST
    payload = request.get_json(silent=True) or {}
    target = payload.get("target") or {}
    body = payload.get("new_body") or ""
    from converter.ai_apply import validate_udf_body, apply_fix
    ok, issues = validate_udf_body(body, target.get("name"))
    if not ok:
        return jsonify({"error": "validation_failed", "issues": issues}), 400
    data = _LAST
    if not data or not data.get("rdl_xml"):
        return jsonify({"error": "no report converted yet"}), 400
    try:
        updated_rdl, info = apply_fix(data["rdl_xml"], target, body)
        data["rdl_xml"] = updated_rdl
        data.setdefault("applied_fixes", []).append({"target": target, "info": info})
        _LAST = data
        return jsonify({"ok": True, "info": info, "warnings": issues})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.get("/api/mockup/<variant>")
def api_mockup_variant(variant):
    """Render the mockup in print or compact form."""
    if variant not in ("print", "compact"):
        abort(404)
    data = _LAST
    if not data or not data.get("report"):
        return jsonify({"error": "no report converted yet"}), 404
    try:
        from converter.preview.mockup_variants import render_mockup_print, render_mockup_compact
        from converter.parsers.oracle_xml import parse_oracle_xml
        oracle_xml = data.get("oracle_xml") or ""
        parsed = parse_oracle_xml(oracle_xml.encode("utf-8") if isinstance(oracle_xml, str) else oracle_xml)
        html = render_mockup_print(parsed) if variant == "print" else render_mockup_compact(parsed)
        return html, 200, {"Content-Type": "text/html; charset=utf-8"}
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5057"))
    print("=" * 70)
    print(f"  Oracle -> SSRS Converter   http://127.0.0.1:{port}")
    print("=" * 70)
    app.run(host="127.0.0.1", port=port, debug=True)

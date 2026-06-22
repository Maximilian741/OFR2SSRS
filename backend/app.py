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

import uuid
from flask import (Flask, request, jsonify, render_template, send_file, abort,
                   session)

# Make `from converter import ...` work whether you run from the repo root or backend/.
import sys
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from converter import convert, run_query  # noqa: E402
from converter.ingest import convert_bundle  # noqa: E402
from converter.bundle_export import build_bundle_zip  # noqa: E402
from converter.rdl_postprocess import (inject_connection_string,  # noqa: E402
                                       set_datasource_reference,
                                       relax_generate_all_drillthroughs,
                                       set_drillthrough_hyperlinks)
from converter import bursting as _bursting_mod  # noqa: E402
from converter.parsers.oracle_xml import parse_oracle_xml as _parse_oracle_xml  # noqa: E402

ROOT = HERE.parent
SAMPLES = ROOT / "samples" / "oracle"

app = Flask(
    __name__,
    template_folder=str(ROOT / "frontend" / "templates"),
    static_folder=str(ROOT / "frontend" / "static"),
)

# Cap upload size -- a public endpoint must never let one request exhaust RAM.
# Oracle Reports XML exports are small (tens to low-hundreds of KB); 16 MB is
# generous headroom. Flask returns HTTP 413 when exceeded. Override via the
# O2S_MAX_UPLOAD_MB env var if a deployment genuinely needs larger inputs.
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("O2S_MAX_UPLOAD_MB", "16")) * 1024 * 1024

# Secret key for signed session cookies -> per-browser state isolation. Set
# O2S_SECRET_KEY in production; a random per-process key is fine for the
# localhost demo (sessions just won't survive a restart).
app.secret_key = os.environ.get("O2S_SECRET_KEY") or uuid.uuid4().hex

# Per-SESSION cache of the most recent conversion. The previous single process
# global leaked one user's report to another (e.g. /api/download/rdl returned
# whoever converted last), so concurrent browsers must be isolated by sid.
_LAST_STORE: dict = {}

# Eviction cap for every per-session store in this module. Python dicts are
# insertion-ordered, so dropping the oldest key gives cheap FIFO eviction.
# Without this, an attacker minting fresh session cookies could grow the
# stores without bound (audit-confirmed memory-exhaustion vector). 50 live
# sessions is far beyond what a team instance sees; override via env.
_SESSION_CAP = int(os.environ.get("O2S_SESSION_CAP", "50"))


def _evict(store: dict) -> None:
    while len(store) > _SESSION_CAP:
        store.pop(next(iter(store)), None)


def _sid() -> str:
    sid = session.get("sid")
    if not sid:
        sid = session["sid"] = uuid.uuid4().hex
    return sid


def _last() -> dict:
    out = _LAST_STORE.setdefault(
        _sid(), {"report": None, "rdl_xml": "", "oracle_xml": "", "mockup_html": ""})
    _evict(_LAST_STORE)
    return out


def _set_last(data) -> None:
    _LAST_STORE[_sid()] = data
    _evict(_LAST_STORE)


# Per-session uploaded report images: {slot_name_or_*: (mime, base64)}.
# Filled by /api/report-images/upload; merged into every convert so seals /
# logos land in the RDL <EmbeddedImages> AND the HTML mockup.
_IMAGE_STORE: dict = {}


# Per-session deployment setting: the SHARED DATA SOURCE PATH on the user's
# report server (e.g. "/Data Sources/Oracle_Prod"). NOT a secret (it's a
# folder path, not credentials) so remembering it per-session is safe; it is
# applied to EVERY artifact this session generates (main RDL, sub-report
# RDLs, burst pack) so uploads bind to the data source automatically and
# the user never repoints by hand. Connection strings are NEVER stored --
# they are applied per-request and discarded (the UI promise).
_DS_PATH_STORE: dict = {}
# Per-session SSRS report-server URL (e.g. "http://host/ReportServer?/Folder").
# A folder URL, not a secret -> safe to remember per session. When set, every
# generated RDL's sub-report <Drillthrough> links become parameterized URL
# <Hyperlink>s pinging that server, so the links work in BOTH the SSRS viewer
# AND an exported PDF (a Drillthrough is interactive-only).
_REPORT_URL_STORE: dict = {}


def _apply_deploy_datasource(rdl_xml: str, req) -> str:
    """Apply the caller's data source + report-link settings to a generated RDL.

    Two independent transforms:
      1. Sub-report LINKS. The generate-all cover link (all-aggregate
         drill-through) is always relaxed to open the child UNFILTERED; and
         when a ``report_server_url`` is set, EVERY sub-report drill-through is
         rewritten into a parameterized URL hyperlink (works in viewer + PDF).
      2. DATA SOURCE. An explicit ``connection_string`` (embedded, per-request)
         wins; otherwise ``shared_ds_path`` (remembered for the session)
         rewrites the DataSourceReference so SSRS auto-binds at upload.
    Empty settings leave that transform a no-op.
    """
    if not rdl_xml:
        return rdl_xml
    form = req.form if req.form else {}
    body = {}
    if req.is_json:
        try:
            body = req.get_json(silent=True) or {}
        except Exception:
            body = {}
    cs = (form.get("connection_string") or body.get("connection_string")
          or req.values.get("connection_string") or "").strip()
    ds_path = (form.get("shared_ds_path") or body.get("shared_ds_path")
               or req.values.get("shared_ds_path") or "").strip()
    rsu = (form.get("report_server_url") or body.get("report_server_url")
           or req.values.get("report_server_url") or "").strip()
    if ds_path:
        _DS_PATH_STORE[_sid()] = ds_path
        _evict(_DS_PATH_STORE)
    else:
        ds_path = _DS_PATH_STORE.get(_sid(), "")
    if rsu:
        _REPORT_URL_STORE[_sid()] = rsu
        _evict(_REPORT_URL_STORE)
    else:
        rsu = _REPORT_URL_STORE.get(_sid(), "")

    # 1. Sub-report links. Relax the generate-all cover link unconditionally;
    #    switch all drill-throughs to URL hyperlinks when a server URL is known.
    rdl_xml = relax_generate_all_drillthroughs(rdl_xml)
    if rsu:
        rdl_xml = set_drillthrough_hyperlinks(rdl_xml, rsu)

    # 2. Data source binding.
    if cs:
        provider = "SQL" if _resolve_target_db(req) == "sqlserver" else "ORACLE"
        return inject_connection_string(rdl_xml, cs, provider=provider)
    if ds_path:
        return set_datasource_reference(rdl_xml, ds_path)
    return rdl_xml


def _asset_version():
    """Cache-busting token (mtime of app.js). Auto-bumps on every JS change."""
    try:
        return str(int((ROOT / "frontend" / "static" / "js" / "app.js").stat().st_mtime))
    except Exception:
        return "0"


def _valid_sample(p):
    """Only show a file in the sidebar if it's a non-trivial, well-formed
    Oracle Reports XML (has <report> root). Empty/junk files are hidden."""
    try:
        if p.stat().st_size < 200:
            return False
        head = p.read_bytes()[:600].decode("utf-8", "replace")
        return "<report" in head
    except Exception:
        return False


@app.route("/")
def index():
    sample_files = (
        sorted(p.name for p in SAMPLES.glob("*.xml") if _valid_sample(p))
        if SAMPLES.exists() else []
    )
    return render_template("index.html", samples=sample_files, asset_version=_asset_version())


def _resolve_target_db(req) -> str:
    """Pull the target_db toggle off a Flask request (form, JSON, or query).

    Default is ``"oracle"`` so users who never touch the toggle ship an RDL
    that matches their Oracle backend. ``"sqlserver"`` opts back into the
    translated T-SQL behavior. Anything else is normalized to ``"oracle"``.
    """
    val = (
        (req.form.get("target_db") if req.form else None)
        or req.values.get("target_db")
        or ""
    ).strip().lower()
    if val not in ("oracle", "sqlserver"):
        val = "oracle"
    return val


@app.post("/api/convert")
def api_convert():
    """Accept an uploaded .xml/.rdf file and return the full conversion payload."""
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "no file uploaded"}), 400
    try:
        target_db = _resolve_target_db(request)
        data = convert(f.read(), target_db=target_db,
                       images=_IMAGE_STORE.get(_sid()) or None)
        # Apply deployment data source settings (embedded connection string
        # per-request, or the session's shared data source path) so the RDL
        # binds to the right data source AT UPLOAD -- no manual repointing.
        data["rdl_xml"] = _apply_deploy_datasource(data["rdl_xml"], request)
        _set_last(data)
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
        target_db = _resolve_target_db(request)
        data = convert_bundle(files, target_db=target_db)
        if data.get("rdl_xml"):
            data["rdl_xml"] = _apply_deploy_datasource(data["rdl_xml"], request)
        if "report" in data:
            _set_last(data)
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
        target_db = _resolve_target_db(request)
        data = convert(safe.read_bytes(), target_db=target_db,
                       images=_IMAGE_STORE.get(_sid()) or None)
        data["rdl_xml"] = _apply_deploy_datasource(data["rdl_xml"], request)
        _set_last(data)
        return jsonify(data)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


# Per-session batch migration results (slim rows + the downloadable pack).
_BATCH_STORE: dict = {}


@app.post("/api/batch")
def api_batch():
    """Batch migration: convert MANY Oracle XMLs in one request and build
    the Migration Assessment + a zip of every RDL. Form: files[] (.xml),
    optional target_db, optional render=1 (verify each RDL through the
    local MS rendering engine when tools/renderlab is set up)."""
    from converter.batch import batch_convert, build_batch_zip
    items = []
    for f in request.files.getlist("files"):
        try:
            blob = f.read()
        except Exception:
            continue
        head = blob[:4096].decode("utf-8", "replace").lower()
        if "<report" in head and "reportdefinition" not in head:
            items.append((f.filename or "report.xml", blob))
    if not items:
        return jsonify({"error": "no Oracle Reports XML files found in the upload"}), 400
    target_db = _resolve_target_db(request)
    want_render = (request.form.get("render") or "").strip() in ("1", "true", "on")
    try:
        batch = batch_convert(items, target_db=target_db, render=want_render)
        # Session data source binding applies to every artifact we ship.
        for r in batch.get("results") or []:
            if r.get("rdl_xml"):
                r["rdl_xml"] = _apply_deploy_datasource(r["rdl_xml"], request)
        _BATCH_STORE[_sid()] = {"batch": batch,
                                "zip": build_batch_zip(batch)}
        _evict(_BATCH_STORE)
        slim = [{k: v for k, v in r.items() if k != "rdl_xml"}
                for r in batch.get("results") or []]
        return jsonify({"results": slim, "locked": batch.get("locked"),
                        "tier": batch.get("tier"),
                        "rendered": batch.get("rendered")})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.get("/api/download/batch-pack")
def api_download_batch_pack():
    """Stream the latest batch's zip (all RDLs + ASSESSMENT.html/json)."""
    entry = _BATCH_STORE.get(_sid())
    if not entry:
        abort(404)
    return send_file(
        io.BytesIO(entry["zip"]),
        mimetype="application/zip",
        as_attachment=True,
        download_name="migration_pack.zip",
    )


@app.post("/api/report-images/upload")
def api_report_image_upload():
    """Accept an image for a layout image placeholder (state seal, logo).

    Form fields: ``image`` (file), ``slot`` (the placeholder name from
    image_slots, or ``*`` to apply to every placeholder). The cached
    report is re-converted immediately so the RDL <EmbeddedImages> AND
    the HTML mockup reflect the image; the full conversion payload is
    returned so the UI refreshes in place.
    """
    f = request.files.get("image")
    slot = (request.form.get("slot") or "*").strip() or "*"
    if not f:
        return jsonify({"error": "no image uploaded"}), 400
    mime = (f.mimetype or "").lower()
    if not mime.startswith("image/"):
        return jsonify({"error": "file is not an image"}), 400
    import base64 as _b64
    blob = f.read()
    if len(blob) > 4 * 1024 * 1024:
        return jsonify({"error": "image too large (4 MB max)"}), 400
    store = _IMAGE_STORE.setdefault(_sid(), {})
    _evict(_IMAGE_STORE)
    store[slot] = (mime, _b64.b64encode(blob).decode("ascii"))
    # Cap the per-session TOTAL too (many 4 MB slots would still add up).
    while sum(len(v[1]) for v in store.values()) > 24 * 1024 * 1024 \
            and len(store) > 1:
        store.pop(next(iter(store)), None)
    last = _last()
    oracle_xml = (last or {}).get("oracle_xml") or ""
    if not oracle_xml:
        return jsonify({"ok": True,
                        "note": "image stored; it will apply to the next conversion"})
    target_db = (last or {}).get("target_db") or "oracle"
    try:
        data = convert(oracle_xml.encode("utf-8"), target_db=target_db,
                       images=store)
        data["rdl_xml"] = _apply_deploy_datasource(data["rdl_xml"], request)
        _set_last(data)
        return jsonify(data)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.get("/api/download/rdl")
def api_download_rdl():
    """Download the most recently generated RDL."""
    rdl = _last().get("rdl_xml") or ""
    if not rdl:
        abort(404)
    # Exit-point guarantee: whatever happened in between (AI fixes, etc.),
    # the artifact the user ships carries the session's data source binding.
    rdl = _apply_deploy_datasource(rdl, request)
    name = (_last().get("report") or {}).get("name") or "report"
    return send_file(
        io.BytesIO(rdl.encode("utf-8")),
        mimetype="application/xml",
        as_attachment=True,
        download_name=f"{name}.rdl",
    )


@app.get("/api/download/bundle")
def api_download_bundle():
    """Download every artifact for the most recent conversion as a single zip."""
    data = _last()
    if not data or not data.get("rdl_xml"):
        abort(404)
    data = dict(data)
    data["rdl_xml"] = _apply_deploy_datasource(data["rdl_xml"], request)
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
    return jsonify({"ok": True, "samples": [p.name for p in SAMPLES.glob("*.xml") if _valid_sample(p)] if SAMPLES.exists() else []})


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
    data = _last()
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
        if updated.get("rdl_xml"):
            updated["rdl_xml"] = _apply_deploy_datasource(
                updated["rdl_xml"], request)
        _set_last(updated)
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
    payload = request.get_json(silent=True) or {}
    target = payload.get("target") or {}
    body = payload.get("new_body") or ""
    from converter.ai_apply import validate_udf_body, apply_fix
    ok, issues = validate_udf_body(body, target.get("name"))
    if not ok:
        return jsonify({"error": "validation_failed", "issues": issues}), 400
    data = _last()
    if not data or not data.get("rdl_xml"):
        return jsonify({"error": "no report converted yet"}), 400
    try:
        updated_rdl, info = apply_fix(data["rdl_xml"], target, body)
        data["rdl_xml"] = _apply_deploy_datasource(updated_rdl, request)
        data.setdefault("applied_fixes", []).append({"target": target, "info": info})
        _set_last(data)
        return jsonify({"ok": True, "info": info, "warnings": issues})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.get("/api/mockup/<variant>")
def api_mockup_variant(variant):
    """Render the mockup in print or compact form."""
    if variant not in ("print", "compact"):
        abort(404)
    data = _last()
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


def _last_parsed_report():
    """Re-parse the last-converted Oracle XML into a ParsedReport.

    The bursting builders take a ParsedReport (object with .name/.queries/
    .parameters), not a dict. _LAST stores the dict shape returned by
    convert(), so we keep the raw XML in _LAST["oracle_xml"] and re-parse
    on demand here. Cheap and stateless.
    """
    data = _last()
    if not data:
        return None
    raw = data.get("oracle_xml") or ""
    if not raw:
        return None
    try:
        return _parse_oracle_xml(raw.encode("utf-8") if isinstance(raw, str) else raw)
    except Exception:
        return None


@app.post("/api/burst-preview")
def api_burst_preview():
    """Re-render the bursting tab's 4 collapsible blocks using UI-form values."""
    payload = request.get_json(silent=True) or {}
    overrides = payload.get("config_overrides") or {}
    parsed = _last_parsed_report()
    if parsed is None:
        return jsonify({"error": "no report converted yet"}), 400
    info = (_last().get("bursting") or {})

    try:
        sql_override = overrides.get("EmailBurstSql")
        email_sql = sql_override or _bursting_mod.build_email_burst_query(parsed, info)

        ps_src = _bursting_mod._EMAIL_PS_TEMPLATE
        rname = parsed.name or "report"
        ps_src = ps_src.replace("__REPORT_NAME__", rname)
        ps_src = ps_src.replace("__BURST_SQL__", email_sql.replace("\\", "\\\\"))

        cfg_template = _bursting_mod.build_email_config_template(parsed, info)
        json_overrides = {k: v for k, v in overrides.items() if k != "EmailBurstSql"}
        cfg_json = _bursting_mod._apply_config_overrides(cfg_template, json_overrides)

        checklist = _bursting_mod.build_service_account_checklist(parsed, info)
        return jsonify({
            "email_burst_query":        email_sql,
            "email_powershell_script":  ps_src,
            "email_config_template":    cfg_json,
            "service_account_checklist": checklist,
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.post("/api/download/burst-pack")
def api_download_burst_pack():
    """Build and stream a Burst Pack zip for the last-converted report,
    applying UI-form config overrides."""
    payload = request.get_json(silent=True) or {}
    overrides = payload.get("config_overrides") or {}
    parsed = _last_parsed_report()
    if parsed is None:
        return jsonify({"error": "no report converted yet"}), 400
    info = (_last().get("bursting") or {})
    rdl_xml = _last().get("rdl_xml") or ""
    # Exit-point guarantee: the packed RDL carries the session's data
    # source binding even if settings changed after the original convert.
    rdl_xml = _apply_deploy_datasource(rdl_xml, request)

    try:
        blob = _bursting_mod.build_burst_pack_zip(parsed, rdl_xml, info, overrides)
        rname = parsed.name or "report"
        return send_file(
            io.BytesIO(blob),
            mimetype="application/zip",
            as_attachment=True,
            download_name=f"{rname}_burst_pack.zip",
        )
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Sub-Reports endpoints
# ---------------------------------------------------------------------------

# In-memory artifact registry. SESSION-SCOPED (audit-confirmed: keying by
# child name alone let two concurrent browsers list/overwrite/download each
# other's artifacts when both had a child with the same name). Registry keys
# are "<sid>::<safe_child>"; files live under .../oracle2ssrs_subreports/<sid>/.
# The sid is server-generated uuid4 hex, so it is path-safe by construction.
import tempfile  # noqa: E402
_SUBREPORT_DIR = Path(tempfile.gettempdir()) / "oracle2ssrs_subreports"
_SUBREPORT_DIR.mkdir(exist_ok=True)
_SUBREPORT_ARTIFACTS = {}  # {sid::child: [{"name": str, "path": str}]}
# Last built RDL per child so the JSON /build response can power a live
# preview while a separate /download streams the file. {sid::child: {...}}
_SUBREPORT_BUILT = {}


def _sub_key(safe_child: str) -> str:
    return f"{_sid()}::{safe_child}"


def _sub_dir(safe_child: str) -> Path:
    d = _SUBREPORT_DIR / _sid() / safe_child
    d.mkdir(parents=True, exist_ok=True)
    return d


@app.get("/api/subreports")
def api_subreports_list():
    """Return the sub-report links detected in the most recent conversion,
    plus the artifacts currently uploaded for each."""
    parsed = _last_parsed_report()
    if parsed is None:
        return jsonify({"links": []})
    from converter.subreports import detect_subreport_links
    links = detect_subreport_links(parsed)
    # Attach uploaded-artifact metadata.
    for ln in links:
        cn = ln.get("child_name") or ""
        safe_cn = "".join(c for c in cn if c.isalnum() or c in ("_", "-"))
        ln["artifacts"] = [
            {"name": a["name"]}
            for a in _SUBREPORT_ARTIFACTS.get(_sub_key(safe_cn), [])
        ]
    return jsonify({"links": links})


@app.post("/api/subreport/<child_name>/upload")
def api_subreport_upload(child_name):
    """Accept one or more artifact files for a detected child report."""
    safe = "".join(c for c in child_name if c.isalnum() or c in ("_", "-"))
    if not safe:
        return jsonify({"error": "invalid child report name"}), 400
    child_dir = _sub_dir(safe)

    saved = []
    for f in request.files.getlist("artifact") or []:
        printable = "".join(c for c in (f.filename or "") if c.isprintable())
        # The uploaded filename is attacker-controlled. Strip every path
        # component (basename) so "..\..\x", "/etc/passwd", or "C:\Windows\x"
        # cannot escape child_dir. Normalize backslashes first so basename
        # behaves the same on Linux and Windows.
        fname = os.path.basename(printable.replace("\\", "/")).strip()
        if not fname or fname in (".", ".."):
            continue
        dest = child_dir / fname
        # Defense in depth: the resolved path MUST stay inside child_dir.
        try:
            dest.resolve().relative_to(child_dir.resolve())
        except ValueError:
            continue
        f.save(str(dest))
        saved.append({"name": fname, "path": str(dest)})

    if saved:
        _SUBREPORT_ARTIFACTS.setdefault(_sub_key(safe), []).extend(saved)
        _evict(_SUBREPORT_ARTIFACTS)
    return jsonify({"saved": [s["name"] for s in saved],
                    "artifacts": [a["name"] for a in
                                  _SUBREPORT_ARTIFACTS.get(_sub_key(safe), [])]})


@app.post("/api/subreport/<child_name>/clear")
def api_subreport_clear(child_name):
    """Remove all uploaded artifacts for a child report (UI 'reset' button)."""
    safe = "".join(c for c in child_name if c.isalnum() or c in ("_", "-"))
    child_dir = _SUBREPORT_DIR / _sid() / safe
    if child_dir.exists():
        for f in child_dir.iterdir():
            try:
                f.unlink()
            except Exception:
                pass
    _SUBREPORT_ARTIFACTS.pop(_sub_key(safe), None)
    _SUBREPORT_BUILT.pop(_sub_key(safe), None)
    return jsonify({"ok": True})


@app.post("/api/subreport/<child_name>/build")
def api_subreport_build(child_name):
    """Build a child report from the uploaded artifacts and return a rich
    JSON preview payload (RDL + HTML mockup + metadata), mirroring the main
    conversion. The built RDL is cached so /download can stream it.

    Works with ANY artifact: the child's Oracle XML, an existing .rdl, or its
    SQL (.sql/.docx/.txt). Parent param names are forwarded so the child RDL
    declares matching ReportParameters for drill-through."""
    safe = "".join(c for c in child_name if c.isalnum() or c in ("_", "-"))
    if not safe:
        return jsonify({"error": "invalid child report name"}), 400
    parsed = _last_parsed_report()
    parent_params = (
        [p.name for p in (parsed.parameters or [])] if parsed else []
    )
    paths = [a["path"] for a in _SUBREPORT_ARTIFACTS.get(_sub_key(safe), [])]
    from converter.subreports import build_subreport, forwarded_drillthrough_params
    # Parameters the parent's drill-through actually forwards to THIS child
    # (e.g. P_ORG_ID, P_SITE_ID) -- parsed from the parent's URL formula. The
    # child MUST declare each or SSRS errors "parameter not declared" the
    # instant the link is clicked. These are usually NOT in the parent's
    # declared <userParameter> list (they're built inside the URL formula), so
    # they must be passed explicitly alongside parent_params.
    dt_params = forwarded_drillthrough_params(parsed, child_name) if parsed else []
    try:
        result = build_subreport(child_name, paths,
                                 parent_param_names=parent_params,
                                 drillthrough_params=dt_params)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500
    # Child RDL gets the SAME data source settings as the parent so the
    # whole report family binds automatically on upload.
    result["rdl_xml"] = _apply_deploy_datasource(result.get("rdl_xml", ""), request)
    _SUBREPORT_BUILT[_sub_key(safe)] = {"rdl": result.get("rdl_xml", ""),
                                        "name": result.get("report_name") or safe}
    _evict(_SUBREPORT_BUILT)

    # ---- Chicken-and-egg killer: re-sync the PARENT to the child that was
    # ACTUALLY built. The parent's <Drillthrough><ReportName> references the
    # child by the name detected from the Oracle URL formula; if the
    # artifacts yield a different report name (e.g. the SQL doc names it
    # differently), the link 404s on the server. Patch the cached parent in
    # place so the NEXT parent download is the completed RDL — build order
    # no longer matters.
    actual = result.get("report_name") or safe
    issues = list(result.get("issues") or [])
    parent_rdl_out = None
    last = _last()
    prdl = (last or {}).get("rdl_xml") or ""
    ref = f"<ReportName>{child_name}</ReportName>"
    if prdl and ref in prdl:
        if actual != child_name:
            prdl = prdl.replace(ref, f"<ReportName>{actual}</ReportName>")
            last["rdl_xml"] = prdl
            _set_last(last)
            parent_rdl_out = prdl
            issues.append(
                f"PARENT RE-SYNCED: its drill-through now opens '{actual}' "
                f"(was '{child_name}'). Re-download the parent .rdl before "
                f"uploading — both files must sit in the same server folder.")
        else:
            issues.append(
                f"Drill-through link VERIFIED: the parent opens '{actual}' "
                f"and this child downloads as '{actual}.rdl'. Upload both "
                f"to the SAME server folder and the link works as-is.")
    elif prdl:
        issues.append(
            f"NOTE: the most recent converted report has no drill-through "
            f"referencing '{child_name}' — if this child belongs to a "
            f"different parent, convert that parent in this session so its "
            f"link can be verified.")

    return jsonify({
        "rdl_xml": result.get("rdl_xml", ""),
        "mockup_html": result.get("mockup_html", ""),
        "mockup_backend_html": result.get("mockup_backend_html", ""),
        "fields": result.get("fields", []),
        "binds": result.get("binds", []),
        "forwarded_params": result.get("forwarded_params", []),
        "sql": result.get("sql", ""),
        "issues": issues,
        "source": result.get("source", ""),
        "report_name": actual,
        "parent_rdl_xml": parent_rdl_out,
        "parent_synced": bool(parent_rdl_out),
        "artifacts": [a["name"] for a in
                      _SUBREPORT_ARTIFACTS.get(_sub_key(safe), [])],
    })


@app.get("/api/subreport/<child_name>/download")
def api_subreport_download(child_name):
    """Stream the most recently built child RDL as a download."""
    safe = "".join(c for c in child_name if c.isalnum() or c in ("_", "-"))
    built = _SUBREPORT_BUILT.get(_sub_key(safe))
    if not built or not built.get("rdl"):
        abort(404)
    # Exit-point guarantee: child RDL ships with the session's current
    # data source binding even if the setting changed after the build.
    rdl = _apply_deploy_datasource(built["rdl"], request)
    return send_file(
        io.BytesIO(rdl.encode("utf-8")),
        mimetype="application/xml",
        as_attachment=True,
        download_name=f"{safe}.rdl",
    )


@app.get("/api/recent/clear")
def api_recent_clear():
    """Clear the in-memory last-conversion cache + uploaded sub-report
    artifacts. Frontend calls this from the 'Clear' button on the
    Recent Reports list so old reports don't grow off one another."""
    _set_last({"report": None, "rdl_xml": "", "oracle_xml": "", "mockup_html": ""})
    # Clear ONLY this session's sub-report state (other sessions are
    # other users -- never touch their artifacts).
    prefix = _sid() + "::"
    for store in (_SUBREPORT_ARTIFACTS, _SUBREPORT_BUILT):
        for k in [k for k in store if k.startswith(prefix)]:
            store.pop(k, None)
    sess_dir = _SUBREPORT_DIR / _sid()
    for child_dir in sess_dir.iterdir() if sess_dir.exists() else []:
        if child_dir.is_dir():
            for f in child_dir.iterdir():
                try:
                    f.unlink()
                except Exception:
                    pass
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5057"))
    # debug=True serves the Werkzeug interactive debugger (arbitrary code
    # execution if reached) and leaks tracebacks -- NEVER on by default for a
    # tool that may be hosted publicly. Opt in locally with O2S_DEBUG=1.
    debug = os.environ.get("O2S_DEBUG", "").lower() in ("1", "true", "yes")
    print("=" * 70)
    print(f"  Oracle -> SSRS Converter   http://127.0.0.1:{port}")
    print("=" * 70)
    app.run(host="127.0.0.1", port=port, debug=debug)

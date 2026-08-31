"""
Oracle -> SSRS Converter
Flask web app entry point. Run with `python backend/app.py` from the project root.
"""
from __future__ import annotations

import os
import io
import json
import re
import traceback
from html import unescape as _unescape
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
                                       set_drillthrough_hyperlinks,
                                       align_drillthrough_sort_default,
                                       set_generate_all_link_text)
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
# A single Oracle Reports XML export is small (tens to low-hundreds of KB),
# but the dropzone accepts a whole ARTIFACT FOLDER, and those carry multi-MB
# XML exports alongside supporting SQL/docx artifacts. The old 16 MB ceiling
# was sized for the single-file case and a routine folder drop blew straight
# through it, so the default is 64 MB. Flask returns HTTP 413 when exceeded.
# O2S_MAX_UPLOAD_MB stays authoritative -- it overrides this default in both
# directions, and every client-side pre-flight check reads the effective
# value back off the server rather than assuming a number.
DEFAULT_MAX_UPLOAD_MB = 64


def _configured_max_upload_mb() -> int:
    """Effective per-request upload ceiling in MB.

    Bad/blank env values fall back to the default instead of crashing the
    process at import time -- a mistyped env var must never take the app down.
    """
    raw = (os.environ.get("O2S_MAX_UPLOAD_MB") or "").strip()
    if not raw:
        return DEFAULT_MAX_UPLOAD_MB
    try:
        val = int(float(raw))
    except (TypeError, ValueError):
        return DEFAULT_MAX_UPLOAD_MB
    return val if val > 0 else DEFAULT_MAX_UPLOAD_MB


app.config["MAX_CONTENT_LENGTH"] = _configured_max_upload_mb() * 1024 * 1024


def _max_upload_bytes() -> int:
    """The live cap, read from config so tests/deploys that patch it are honoured."""
    try:
        return int(app.config.get("MAX_CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        return 0


@app.errorhandler(413)
def _request_too_large(e):
    """Return the app's JSON error shape on an oversized upload.

    Without this, Werkzeug answers 413 with its stock HTML page; every
    frontend caller does ``res.json()`` on the response, which then throws
    and the user sees only a generic failure toast instead of the reason.
    """
    limit = _max_upload_bytes()
    limit_mb = limit / (1024 * 1024)
    msg = "upload too large: this server accepts at most %g MB per request" % limit_mb
    sent = request.content_length
    if sent:
        msg += " (this upload was %.1f MB)" % (sent / (1024 * 1024))
    msg += (". Conversion only needs the Oracle Reports XML export -- "
            "truth PDFs/screenshots don't have to be uploaded. To raise the "
            "cap, set the O2S_MAX_UPLOAD_MB environment variable.")
    return _err(msg, "upload_too_large", 413)

# Secret key for signed session cookies -> per-browser state isolation. Set
# O2S_SECRET_KEY in production; a random per-process key is fine for the
# localhost demo (sessions just won't survive a restart).
app.secret_key = os.environ.get("O2S_SECRET_KEY") or uuid.uuid4().hex


# ---------------------------------------------------------------------------
# STRUCTURAL ERROR KINDS
#
# Every JSON error carries a short, stable CODE beside its sentence. The
# frontend looks the "Do this next" step up from that code (see
# NEXT_ACTION_BY_KIND in frontend/static/js/app.js); it used to derive the
# step by matching English words in the message, so every rewrite of a
# sentence here silently changed the advice a user was given -- and had
# already turned two everyday failures into "try the same step again", which
# fails identically every time.
#
# The rule: the sentence is for the human and may be rewritten freely; the
# kind is an interface and changes only with the frontend's table.
ERROR_KINDS = {
    "upload_too_large",       # the payload exceeds MAX_CONTENT_LENGTH
    "no_report_yet",          # this endpoint needs a conversion first
    "no_report_in_drop",      # nothing convertible arrived
    "nothing_selected",       # no file part at all
    "render_engine_unavailable",   # the RDL is fine, the engine is not
    "image_rejected",         # not an image / too big
    "ai_not_configured",      # optional AI helper is not set up
    "validation_failed",      # the request was checked and refused
    "invalid_request",        # malformed / unusable request
    "server_error",           # unexpected exception
}


def _err(message, kind, status):
    """One JSON error shape: the sentence, plus the code that decides what
    the user is told to do about it."""
    assert kind in ERROR_KINDS, "unknown error kind: %r" % (kind,)
    return jsonify({"error": str(message), "error_kind": kind}), status


def _crash(e, status=500):
    """An unexpected exception. The trace stays for the console; the kind is
    what the status surface acts on."""
    return jsonify({"error": str(e), "error_kind": "server_error",
                    "trace": traceback.format_exc()}), status

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
_GEN_ALL_LABEL_STORE: dict = {}


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
    gen_all_label = (form.get("generate_all_label") or body.get("generate_all_label")
                     or req.values.get("generate_all_label") or "").strip()
    if gen_all_label:
        _GEN_ALL_LABEL_STORE[_sid()] = gen_all_label
        _evict(_GEN_ALL_LABEL_STORE)
    else:
        gen_all_label = _GEN_ALL_LABEL_STORE.get(_sid(), "")
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

    # 1. Sub-report links. Relax the generate-all cover link unconditionally,
    #    then ALWAYS switch drill-throughs to URL hyperlinks so the links also
    #    work in an exported PDF (a Drillthrough is dropped in static export).
    #    An explicit server URL is used verbatim; otherwise the hyperlink falls
    #    back to SSRS server globals (zero-config on SSRS 2016+).
    rdl_xml = relax_generate_all_drillthroughs(rdl_xml)
    rdl_xml = set_drillthrough_hyperlinks(rdl_xml, rsu)
    # Give the cover "generate all" link a friendly display label (e.g. "JV
    # Standard 12 x 9 Envelope") so the end user knows what they're generating.
    rdl_xml = set_generate_all_link_text(rdl_xml, gen_all_label)
    # Default the master's sort selector to SITE so its records line up 1:1 with
    # the sub-report's site-ordered bulk list -- no manual parameter setting.
    rdl_xml = align_drillthrough_sort_default(rdl_xml)

    # 2. Data source binding.
    if cs:
        provider = "SQL" if _resolve_target_db(req) == "sqlserver" else "ORACLE"
        return inject_connection_string(rdl_xml, cs, provider=provider)
    if ds_path:
        return set_datasource_reference(rdl_xml, ds_path)
    return rdl_xml


# ---------------------------------------------------------------------------
# Deploy checklist <-> artifact reconciliation
# ---------------------------------------------------------------------------
# THE DEFECT THIS EXISTS FOR, measured on the real page. The deploy checklist
# is built inside convert(), from the SOURCE report, BEFORE this module
# applies the operator's own deployment settings to the RDL. So it described
# a file that nobody ever downloaded. With "Which database the report will
# read" = Oracle and "/Data Sources/StateOracle" typed into the sidebar:
#
#   the .rdl that /api/download/rdl serves said
#       <DataSource Name="SharedDataSource">
#       <DataSourceReference>/Data Sources/StateOracle</DataSourceReference>
#       <QueryParameter Name=":P_SORT">          (Oracle bind variables)
#       <CommandText>SELECT ... TO_CHAR(...)     (Oracle SQL)
#   the checklist beside it said
#       "a placeholder shared DataSource named AppDb"
#       "Change connection string to Data Source=YOUR_SQL_SERVER;
#        Initial Catalog=AppDb;Integrated Security=SSPI;"
#       "pick /Data Sources/AppDb from the report server"
#       "Verify @P_* parameter bindings" / "@P_SORT (String)"
#       "Review T-SQL validation results"
#
# Five checkable claims, five of them wrong, on the screen the operator is
# told to follow step by step.
#
# So the steps that DESCRIBE THE ARTIFACT are no longer written from the
# source: they are written from the finished RDL -- the exact bytes the
# download serves, after every deployment setting has been applied to it.
# Nothing here reads the UI's intentions, and nothing states a fact that is
# not in the file. The steps that describe HUMAN PROCEDURE (open it in
# Report Builder, upload it, subscribe to it) are left as the converter
# wrote them.
_ART_DS_NAME_RE = re.compile(r'<DataSource\s+Name="([^"]*)"')
_ART_DS_REF_RE = re.compile(r"<DataSourceReference>([^<]*)</DataSourceReference>")
_ART_PROVIDER_RE = re.compile(r"<DataProvider>([^<]*)</DataProvider>")
_ART_CONNSTR_RE = re.compile(r"<ConnectString>([^<]*)</ConnectString>")
_ART_QPARAM_RE = re.compile(r'<QueryParameter\s+Name="([^"]*)"')
_ART_RPARAM_RE = re.compile(
    r'<ReportParameter\s+Name="([^"]*)">(.*?)</ReportParameter>', re.S)
_ART_CMD_RE = re.compile(r"<CommandText>(.*?)</CommandText>", re.S)
_ART_PKG_RE = re.compile(
    r"\b(Pkg_[A-Za-z0-9_]+|Utl_URL)\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)", re.I)
_ART_UDF_RE = re.compile(r"\bdbo\.fn_([A-Za-z_][A-Za-z0-9_]*)", re.I)
_ART_LEX_RE = re.compile(r"&([A-Za-z_][A-Za-z0-9_]*)")


def _art_tag(block: str, tag: str) -> str:
    m = re.search(r"<%s>([^<]*)</%s>" % (tag, tag), block or "")
    return _unescape(m.group(1)) if m else ""


def _artifact_facts(rdl_xml: str, target_db: str = "") -> dict:
    """Everything the deploy checklist is allowed to say, read back out of
    the finished RDL. Same bytes as the download; no UI state consulted."""
    rdl = rdl_xml or ""
    ds_names = [n for n in dict.fromkeys(_ART_DS_NAME_RE.findall(rdl)) if n]
    ds_refs = [r.strip() for r in dict.fromkeys(_ART_DS_REF_RE.findall(rdl))
               if r.strip()]
    providers = [p for p in dict.fromkeys(_ART_PROVIDER_RE.findall(rdl)) if p]
    embedded = any(c.strip() for c in _ART_CONNSTR_RE.findall(rdl))
    qparams = [q for q in dict.fromkeys(_ART_QPARAM_RE.findall(rdl)) if q]

    # The bind prefix is a FACT OF THE FILE, not of the toggle: an Oracle
    # dataset binds ":P_X", a SQL Server one binds "@P_X". Reading it here is
    # what stops the checklist printing a syntax the file never uses.
    bind = ""
    for q in qparams:
        if q[:1] in (":", "@"):
            bind = q[:1]
            break
    target = (target_db or "").strip().lower()
    if bind == ":":
        target = "oracle"
    elif bind == "@":
        target = "sqlserver"
    if target not in ("oracle", "sqlserver"):
        target = "oracle"
    if not bind:
        bind = ":" if target == "oracle" else "@"

    params = []
    for m in _ART_RPARAM_RE.finditer(rdl):
        blk = m.group(2)
        dv = re.search(r"<DefaultValue>\s*<Values>\s*<Value>([^<]*)</Value>",
                       blk)
        params.append({
            "name": _unescape(m.group(1)),
            "type": _art_tag(blk, "DataType") or "String",
            "prompt": _art_tag(blk, "Prompt"),
            "hidden": "<Hidden>true</Hidden>" in blk,
            "default": _unescape(dv.group(1)) if dv else "",
        })

    sql = "\n".join(_unescape(c) for c in _ART_CMD_RE.findall(rdl))
    # A lexical the converter could not finish is left in the query as a
    # /* breadcrumb */ beside where it belonged. That distinction is the
    # difference between "this query will not run" and "this query runs but
    # does not filter", and the checklist may not blur the two.
    runnable = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    pkgs = sorted({"%s.%s" % (m.group(1), m.group(2))
                   for m in _ART_PKG_RE.finditer(runnable)})
    udfs = sorted({"dbo.fn_%s" % m.group(1)
                   for m in _ART_UDF_RE.finditer(runnable)})
    lex = sorted({"&%s" % m.group(1) for m in _ART_LEX_RE.finditer(sql)})
    lex_live = sorted({"&%s" % m.group(1)
                       for m in _ART_LEX_RE.finditer(runnable)})

    ds_name = ds_names[0] if ds_names else ""
    ds_ref = ds_refs[0] if ds_refs else ""
    return {
        "ds_name": ds_name,
        "ds_ref": ds_ref,
        # The generator writes the data source's own NAME as the reference
        # when nothing has been set; a reference that DIFFERS from the name
        # is a real path the operator supplied.
        "ds_bound": bool(ds_ref) and ds_ref != ds_name,
        "embedded": embedded,
        "provider": providers[0] if providers else "",
        "target": target,
        "dialect": "Oracle SQL" if target == "oracle" else "T-SQL",
        "db_name": "Oracle" if target == "oracle" else "SQL Server",
        "bind": bind,
        "other_bind": "@" if bind == ":" else ":",
        "params": params,
        "pkg_calls": pkgs,
        "udf_calls": udfs,
        "lexicals": lex,
        "lexicals_live": lex_live,
    }


def _art_issue_summary(issues) -> str:
    if not issues:
        return ("**Nothing was flagged.** The checker believes every query in "
                "the file is sound. Still run the report once against real "
                "data before you hand it over.")
    by_sev = {}
    for it in issues:
        sev = (it.get("severity") or "info").lower()
        by_sev[sev] = by_sev.get(sev, 0) + 1
    parts = ["**%d %s%s**" % (by_sev[s], s, "" if by_sev[s] == 1 else "s")
             for s in ("error", "warning", "info") if by_sev.get(s)]
    out = ["Validation found " + ", ".join(parts) + ". The first of them:"]
    for it in issues[:8]:
        loc = ("L%s" % it["line"]) if it.get("line") else "-"
        out.append("* _%s_ `%s` (%s @ %s): %s"
                   % (it.get("severity", ""), it.get("rule", ""),
                      it.get("scope", ""), loc, it.get("message", "")))
    return "\n".join(out)


def _art_plural(n: int, word: str) -> str:
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def _deploy_step_datasource(f, issues) -> dict:
    name = f["ds_name"] or "the data source"
    head = ("**Everything in this step is read back out of the `.rdl` you "
            "just downloaded**, so it cannot describe a different file.\n\n"
            "* The data source in the file is called **%s**.\n" % name)
    if f["embedded"]:
        head += ("* It carries its own **embedded connection**%s - the one "
                 "you typed into **Embedded connection (optional)** in the "
                 "sidebar. This app writes it into the file and never stores "
                 "it.\n"
                 % ((", provider `%s`" % f["provider"]) if f["provider"] else ""))
    elif f["ds_bound"]:
        head += ("* It carries no connection of its own. It **refers** to a "
                 "shared data source on your report server: `%s`.\n"
                 % f["ds_ref"])
    elif f["ds_ref"]:
        head += ("* It refers to a shared data source called `%s`, which is "
                 "this tool's placeholder name and not a data source on your "
                 "server.\n" % f["ds_ref"])
    else:
        head += "* It carries neither a connection nor a reference to one.\n"
    head += ("* Its queries are written in **%s**, because **Which database "
             "the report will read** was set to **%s** when you converted "
             "this.\n\n" % (f["dialect"], f["db_name"]))

    if f["embedded"]:
        return {"status": "auto",
                "title": "Data source - the file carries your connection",
                "body_md": head + (
                    "**Nothing to repoint.** In Report Builder you can open "
                    "**Data Sources** in the Report Data pane and press **Test "
                    "connection** to prove it reaches the database.\n\n"
                    "If the connection is wrong, correct it in the sidebar and "
                    "convert again rather than retyping it inside Report "
                    "Builder: every file this tool makes is written from that "
                    "box, so a change made by hand is lost on the next "
                    "download.")}
    if f["ds_bound"]:
        return {"status": "auto",
                "title": "Data source - already pointed at %s" % f["ds_ref"],
                "body_md": head + (
                    "**Nothing to repoint.** Two things have to be true on the "
                    "server before you upload:\n\n"
                    "1. A shared data source exists at exactly `%s`.\n"
                    "2. Its saved credentials can read the %s database this "
                    "report queries.\n\n"
                    "When both hold, the upload binds to it on its own. Do not "
                    "repoint it by hand in Report Builder: that path comes from "
                    "**Shared data source on your report server "
                    "(recommended)** in the sidebar, so if it is wrong, fix it "
                    "there and convert again - this report, its sub-reports and "
                    "the burst pack are all written from that one box."
                    % (f["ds_ref"], f["db_name"]))}
    return {"status": "todo",
            "title": "Point the report at your data source",
            "body_md": head + (
                "**This one is not finished yet.** No shared data source was "
                "set, so the file points at a placeholder rather than at "
                "anything on your server. Two ways to finish it, best "
                "first:\n\n"
                "1. Type the path of your shared data source into **Shared "
                "data source on your report server (recommended)** in the "
                "sidebar and convert again. Every file this tool makes then "
                "points at it, and the upload binds on its own.\n"
                "2. Or repoint it once, by hand, in Report Builder: **Data "
                "Sources** in the Report Data pane, right-click **%s**, **Data "
                "Source Properties**, choose **Use a shared connection or data "
                "source**, and browse to yours. You have to do that again for "
                "every file you download.\n\n"
                "There is a third way if your server has no shared data source "
                "to point at: type the connection into **Embedded connection "
                "(optional)** in the sidebar and it is written into the file. "
                "A shared data source is still better - the credentials then "
                "live on the server rather than inside a file that gets passed "
                "around." % name)}


def _deploy_step_sql(f, issues) -> dict:
    errs = sum(1 for i in issues
               if (i.get("severity") or "").lower() == "error")
    warns = sum(1 for i in issues
                if (i.get("severity") or "").lower() == "warning")
    if f["target"] == "oracle":
        checks = ("* Unbalanced brackets and unterminated strings\n"
                  "* Names longer than the database will accept\n"
                  "* `SELECT *`, which is a warning: the table's bindings "
                  "break the day somebody renames a column\n"
                  "* `:P_` bind variables a query uses but never declares as "
                  "a report parameter")
    else:
        checks = ("* Oracle wording that survived the rewrite (DECODE, NVL, "
                  "ROWNUM, (+), MINUS, ...)\n"
                  "* Unbalanced brackets and unterminated strings\n"
                  "* Names over 128 characters, which SQL Server refuses\n"
                  "* `SELECT *`, which is a warning: the table's bindings "
                  "break the day somebody renames a column\n"
                  "* `@P_` parameters a query uses but never declares")
    return {"status": "caution" if errs else "auto",
            "title": "Read the %s the converter wrote (%s, %s)"
                     % (f["dialect"], _art_plural(errs, "error"),
                        _art_plural(warns, "warning")),
            "body_md": (
                "Every query in this `.rdl` is **%s** - that is what **Which "
                "database the report will read** was set to. A static checker "
                "read all of it; the **Validation** view lists what it found, "
                "line by line.\n\nWhat it looks for:\n\n%s\n\n%s"
                % (f["dialect"], checks, _art_issue_summary(issues)))}


def _deploy_step_udfs(f, issues) -> dict:
    pkgs, udfs = f["pkg_calls"], f["udf_calls"]
    if f["target"] == "oracle":
        if not pkgs:
            return {"status": "auto",
                    "title": "Database functions the queries call (none found)",
                    "body_md": "_No packaged Oracle function call is left in "
                               "this file's SQL, so there is nothing to check "
                               "here._"}
        return {"status": "caution",
                "title": "Database functions the queries call (%s)"
                         % _art_plural(len(pkgs), "found"),
                "body_md": (
                    "The queries in this file are Oracle SQL, and they still "
                    "call these packaged functions - exactly as the original "
                    "report did:\n\n%s\n\n**Nothing to port.** They stay in "
                    "the Oracle database. What to check is permission: the "
                    "account behind the data source above must be allowed to "
                    "`EXECUTE` each package, and must be able to see it (its "
                    "own schema, a synonym, or a schema-qualified name). A "
                    "missing grant does not show up at upload - it shows up "
                    "the first time somebody runs the report."
                    % "\n".join("* `%s`" % p for p in pkgs))}
    if not udfs and not pkgs:
        return {"status": "auto",
                "title": "Database functions the queries call (none found)",
                "body_md": "_The rewritten SQL in this file calls no "
                           "`dbo.fn_*` function, so there is nothing to "
                           "port._"}
    body = ""
    if udfs:
        body += ("The rewritten SQL in this file calls these functions, which "
                 "have to exist in your report database before the report "
                 "will run:\n\n%s\n\nThe converter writes stubs so the preview "
                 "can run; port each one against your real schema and deploy "
                 "it under that name. Each stub carries a `/* PORTING NOTE */` "
                 "block with the Oracle original, and a "
                 "`SESSION_CONTEXT(N'Oracle2SSRS_dev')` guard so calling a "
                 "stub in production raises an error instead of quietly "
                 "returning invented data."
                 % "\n".join("* `%s`" % u for u in udfs))
    if pkgs:
        body += (("\n\n" if body else "")
                 + ("**Also still in the file:**\n\n%s\n\nThose are Oracle "
                    "package calls the rewrite could not translate. SQL Server "
                    "cannot run them - either finish them by hand, or set "
                    "**Which database the report will read** to **Oracle** and "
                    "convert again."
                    % "\n".join("* `%s`" % p for p in pkgs)))
    return {"status": "todo",
            "title": "Port the database functions this file calls (%s)"
                     % _art_plural(len(udfs) + len(pkgs), "found"),
            "body_md": body}


def _deploy_step_lexicals(f, issues) -> dict:
    lex = f["lexicals"]
    if not lex:
        return {"status": "auto",
                "title": "Oracle lexical references left in the file (none)",
                "body_md": "_A **lexical** is a piece of SQL text an Oracle "
                           "report drops into its own query while it runs. "
                           "Every one of them either became a real report "
                           "parameter or was written into the query as a "
                           "fixed value. None is left in this `.rdl`, so "
                           "there is nothing to finish by hand._"}
    if f["target"] == "oracle":
        pattern_b = (
            "**B. Move the varying part into the database.** An SSRS dataset "
            "sends its query to Oracle as one statement, so there is nowhere "
            "to build SQL text inside it. Put the clause in a view, or in a "
            "stored procedure or function that takes the value as a bind "
            "variable, and have the dataset read from that.")
    else:
        pattern_b = (
            # The placeholder is deliberately NOT called @P_something: every
            # P_-shaped name printed anywhere in this checklist is meant to
            # be a parameter the file really declares, and an example that
            # borrowed the shape would be indistinguishable from one.
            "**B. Build the statement with `sp_executesql`.** Wrap the "
            "dataset's query:\n\n"
            "```sql\nDECLARE @sql NVARCHAR(MAX) = N'SELECT ... WHERE 1=1'\n"
            "  + CASE WHEN @Chosen IS NOT NULL THEN N' AND t.Col = "
            "@Chosen' ELSE N'' END;\nEXEC sp_executesql @sql, N'@Chosen "
            "INT', @Chosen = @Chosen;\n```\n\n"
            "This is the only choice when the lexical carries a column list "
            "or an ORDER BY rather than a single value.")
    live = f["lexicals_live"]
    if live:
        effect = ("**%d of them sit in SQL the database will try to run**, so "
                  "those queries fail until you finish them. The rest were "
                  "left as a comment beside the place they belonged: those "
                  "queries do run, but whatever the lexical carried - a WHERE "
                  "clause, a column list, an ORDER BY - is not being applied, "
                  "so the report can return more rows, or a different order, "
                  "than the Oracle original." % len(live))
    else:
        effect = ("The converter left each one as a comment beside the place "
                  "it belonged, so **the queries still run**. What they "
                  "carried - a WHERE clause, a column list, an ORDER BY - is "
                  "not being applied, so the report can return more rows, or "
                  "a different order, than the Oracle original. It will not "
                  "announce that; it simply prints the wrong rows.")
    return {"status": "caution",
            "title": "Finish the Oracle lexical references still in the "
                     "file (%d)" % len(lex),
            "body_md": (
                "A **lexical** is a piece of SQL text an Oracle report drops "
                "into its own query while it runs. SSRS has nothing like it. "
                "These names are still in this `.rdl`'s SQL:\n\n%s\n\n%s\n\n"
                "**A. Filter the table instead (best when it is a plain "
                "'equals').** Leave that column out of the query's WHERE "
                "clause and add a Filter on the table itself, comparing the "
                "field with the report parameter.\n\n%s"
                % ("\n".join("* `%s`" % r for r in lex), effect, pattern_b))}


def _deploy_step_params(f, issues) -> dict:
    ps = f["params"]
    if not ps:
        return {"status": "auto",
                "title": "Check the report's parameters (none declared)",
                "body_md": "_This file declares no report parameters, so "
                           "there is nothing to check here._"}
    rows = []
    for p in ps:
        bits = [p["type"]]
        if p["hidden"]:
            bits.append("hidden")
        if p["default"] and p["default"] != "=Nothing":
            bits.append("default `%s`" % p["default"])
        if p["prompt"] and p["prompt"] != p["name"]:
            bits.append("prompt \"%s\"" % p["prompt"])
        rows.append("* **%s** - %s" % (p["name"], ", ".join(bits)))
    return {"status": "auto",
            "title": "Check the report's parameters (%d declared)" % len(ps),
            "body_md": (
                "These are the parameters this `.rdl` declares. Its queries "
                "bind them as **`%sname`**, which is %s's way of writing a "
                "bind variable; **`%sname`** appears nowhere in the "
                "file.\n\n%s\n\nIn Report Builder open **Parameters** and "
                "check the type, the prompt and the default on each one. If "
                "the Oracle report offered a list to choose from, build a "
                "small lookup dataset and bind it to the parameter - an "
                "Oracle list of values cannot be carried across "
                "automatically.\n\n"
                "**Do not press Refresh Fields.** The field list in this file "
                "is already complete, and refreshing it makes Report Builder "
                "ask you for every parameter before it will talk to the "
                "database."
                % (f["bind"], f["db_name"], f["other_bind"],
                   "\n".join(rows)))}


# Which builder owns a step, decided by its SUBJECT. A step that describes
# the artifact is rewritten from the artifact; a step that describes what a
# human does (open it, upload it, subscribe to it) is left alone.
_ART_BUILDERS = {
    "datasource": _deploy_step_datasource,
    "params": _deploy_step_params,
    "lexicals": _deploy_step_lexicals,
    "udfs": _deploy_step_udfs,
    "sql": _deploy_step_sql,
}


def _checklist_subject(step) -> str:
    """Which of the five artifact-describing subjects a step is about.

    Every rule has to recognise BOTH wordings: the converter's own title, and
    the title this module rewrites it to. THE BUG THAT COSTS, measured: the
    bundle download reconciles a payload that convert() already reconciled,
    and while "Verify @P_* parameter bindings" was the only thing recognised
    as the parameter step, the second pass saw no parameter step, decided one
    was missing, and appended a TENTH step -- the same parameter list, twice,
    in the zip the operator unpacks. Recognising this module's own wording
    makes the pass idempotent.
    """
    title = (step.get("title") or "").lower()
    body = (step.get("body_md") or "").lower()
    if "data source" in title or "datasource" in title:
        return "datasource"
    if "parameter" in title or "<reportparameter>" in body:
        return "params"
    if "lexical" in title:
        return "lexicals"
    if ("udf" in title or "dbo.fn_" in title or "dbo.fn_" in body
            or "database functions" in title):
        return "udfs"
    if ("validation results" in title or "t-sql" in title
            or "the converter wrote" in title or "sql validator" in body):
        return "sql"
    return ""


def _fix_bind_prefix(text: str, facts) -> str:
    """The last net. Whatever else a step says, it may not spell a bind
    variable in a syntax the file does not use - `@P_Thing` in an Oracle
    file, or `:P_Thing` in a SQL Server one."""
    if not text:
        return text
    wrong, right = facts["other_bind"], facts["bind"]
    return re.sub(
        r"(?<![A-Za-z0-9_])%s(P_(?:\*|[A-Za-z0-9_]+))" % re.escape(wrong),
        right + r"\1", text)


# Words that belong to ONE engine. A step that prints the other engine's
# name, or the other engine's own column type, is describing a file that is
# not this one.
#
# THE LEAK THIS CLOSES, measured over the agency corpus: six reports carry a
# signature-image dataset, and the step that explains how to wire it ends
# "...the column must be a `varbinary(max)` in T-SQL." -- inside a checklist
# whose every other line says the file's queries are Oracle SQL. varbinary
# is SQL Server's type; the Oracle column that holds an image is a BLOB. The
# steps that DESCRIBE the artifact are rewritten from the artifact and never
# had this problem; this net catches the human-procedure steps, which the
# reconciler deliberately leaves alone.
_ENGINE_WORDS = {
    "oracle": (("T-SQL", "Oracle SQL"), ("`varbinary(max)`", "`BLOB`")),
    "sqlserver": (("Oracle SQL", "T-SQL"), ("`BLOB`", "`varbinary(max)`")),
}


def _fix_engine_words(text: str, facts) -> str:
    if not text:
        return text
    for wrong, right in _ENGINE_WORDS.get(facts["target"], ()):
        text = text.replace(wrong, right)
    return text


def _reconcile_checklist(data) -> None:
    """Rewrite every deploy step that describes the artifact so that it
    describes THIS artifact. Mutates ``data`` in place and never raises: a
    checklist that could not be reconciled is still better than a 500."""
    try:
        steps = data.get("deployment_checklist")
        if not isinstance(steps, list) or not steps:
            return
        facts = _artifact_facts(data.get("rdl_xml") or "",
                                data.get("target_db") or "")
        issues = (list(data.get("validation_issues") or [])
                  + list(data.get("rdl_issues") or []))
        out, seen = [], set()
        for step in steps:
            step = dict(step)
            subject = _checklist_subject(step)
            if subject in _ART_BUILDERS and subject not in seen:
                seen.add(subject)
                step.update(_ART_BUILDERS[subject](facts, issues))
            out.append(step)
        # A step that never arrived cannot be corrected, so supply it. The
        # data source and the parameters are the two the operator cannot
        # deploy without.
        for subject, where in (("datasource", 1), ("params", len(out))):
            if subject not in seen:
                out.insert(min(where, len(out)),
                           _ART_BUILDERS[subject](facts, issues))
        for n, step in enumerate(out, 1):
            step["step"] = n
            step["title"] = _fix_engine_words(
                _fix_bind_prefix(step.get("title") or "", facts), facts)
            step["body_md"] = _fix_engine_words(
                _fix_bind_prefix(step.get("body_md") or "", facts), facts)
        data["deployment_checklist"] = out
    except Exception:  # noqa: BLE001 -- an honesty pass must never sink a run
        traceback.print_exc()


def _resync_child_ref(prdl: str, child: str, actual: str):
    """Re-point the parent's reference to a built child report from ``child``
    to ``actual`` (the chicken-and-egg killer). Robust to BOTH forms the link
    can take in the cached/deployed parent:

      * ``<Drillthrough><ReportName>child</ReportName>``  (pre-deploy form)
      * a deploy-time ``<Hyperlink>`` URL ``.../child&rs:Command=Render``
        (the & is XML-escaped to &amp; in the RDL, but tolerate raw &)

    Returns ``(found, changed, new_prdl)``: ``found`` = the parent links to
    ``child`` at all; ``changed`` = the name actually differed and was rewritten.
    """
    if not prdl:
        return (False, False, prdl)
    dt_ref = f"<ReportName>{child}</ReportName>"
    hl_refs = (f"/{child}&amp;rs:Command=Render", f"/{child}&rs:Command=Render")
    found = (dt_ref in prdl) or any(h in prdl for h in hl_refs)
    if not found:
        return (False, False, prdl)
    if actual == child:
        return (True, False, prdl)
    new = prdl.replace(dt_ref, f"<ReportName>{actual}</ReportName>")
    new = new.replace(f"/{child}&amp;rs:Command=Render",
                      f"/{actual}&amp;rs:Command=Render")
    new = new.replace(f"/{child}&rs:Command=Render",
                      f"/{actual}&rs:Command=Render")
    return (True, new != prdl, new)


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
    # The upload ceiling is rendered INTO the page (not hardcoded in JS and
    # not fetched asynchronously): the client must be able to pre-flight the
    # very first folder drop, which can happen before any API round trip.
    return render_template("index.html", samples=sample_files,
                           asset_version=_asset_version(),
                           max_upload_bytes=_max_upload_bytes())


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
        return _err("no file uploaded", "nothing_selected", 400)
    try:
        target_db = _resolve_target_db(request)
        # Generic label overrides: {'<textbox name>'|'title': new_text}.
        # The available labels ship back as data['overridable_labels'].
        _lo = None
        _lo_raw = request.form.get("label_overrides")
        if _lo_raw:
            try:
                import json as _json
                _cand = _json.loads(_lo_raw)
                if isinstance(_cand, dict):
                    _lo = {str(k): str(v) for k, v in _cand.items()
                           if v is not None}
            except Exception:  # noqa: BLE001
                _lo = None
        data = convert(f.read(), target_db=target_db,
                       images=_IMAGE_STORE.get(_sid()) or None,
                       deep_verify=True, label_overrides=_lo)
        # Apply deployment data source settings (embedded connection string
        # per-request, or the session's shared data source path) so the RDL
        # binds to the right data source AT UPLOAD -- no manual repointing.
        data["rdl_xml"] = _apply_deploy_datasource(data["rdl_xml"], request)
        # ...and only THEN describe it: the deploy checklist is rewritten
        # from the finished file, so it can never teach a different one.
        _reconcile_checklist(data)
        _set_last(data)
        return jsonify(data)
    except Exception as e:
        traceback.print_exc()
        return _crash(e)


_RV_FETCH_DONE = False


def _autofetch_render_engine() -> None:
    """One-shot, best-effort fetch of the ReportViewer DLL folder.

    The DLLs are Microsoft redistributables downloaded into
    tools/renderlab/lib (gitignored) -- a project-folder download, not an
    install. Attempted at most once per process so an offline machine is
    not hammered on every render click; failure is fine, the caller falls
    back to the report-server path with an actionable message.
    """
    global _RV_FETCH_DONE
    if _RV_FETCH_DONE:
        return
    _RV_FETCH_DONE = True
    import subprocess
    script = HERE.parent / "tools" / "renderlab" / "fetch_reportviewer.py"
    try:
        subprocess.run([sys.executable, str(script)], capture_output=True,
                       text=True, timeout=300)
    except Exception:  # noqa: BLE001 - offline/proxy; caller reports it
        pass


# ---- what each rendered page SHOWS -------------------------------------
#
# The page images returned below are the deliverable, not decoration: they
# are the only place the operator sees what SSRS will print. They shipped
# with alt="Rendered page 3", which identifies the image and says nothing
# whatever about it (WCAG 2.2 SC 1.1.1 -- an alt that carries none of the
# image's information is the same defect as no alt at all).
#
# The engine has already written a PDF to make those images, so the words
# on each page are RIGHT THERE. Describing a page from its own text beats
# any guess made from the report definition: it is what the page actually
# prints, in the order it prints it.
#
# The catch, measured before: ReportViewer's PDF writer embeds non-Latin
# font subsets with no usable ToUnicode map, so extraction returns GLYPH
# IDS for Greek / Cyrillic / Arabic / CJK runs -- text that has a length
# and prints as garbage. A wrong description is worse than none, so a line
# is only used when it survives _line_is_readable(); a page that fails it
# still reports its structure (line and word counts), and the caller falls
# back to naming the report and the page.
def _line_is_readable(line: str) -> bool:
    """Is this extracted line WORDS, or the glyph-id garbage described above?"""
    if len(line) < 3:
        return False
    if sum(ch.isalnum() for ch in line) < 3:
        return False          # rules, page numbers, box-drawing: not a title
    for ch in line:
        if not ch.isprintable():
            return False                       # control chars = glyph ids
        if 0xE000 <= ord(ch) <= 0xF8FF:
            return False                       # private use area, ditto
    # A subset with no ToUnicode also lands inside ordinary Unicode ranges,
    # so a line that is mostly non-ASCII cannot be trusted to say what it
    # appears to say. Latin-1 accents stay welcome; whole non-Latin scripts
    # are described structurally instead of wrongly.
    exotic = sum(1 for ch in line if ord(ch) > 0x024F)
    return exotic * 3 <= len(line)


# A page's first line is not always worth quoting. Measured on the bundled
# sample: pages 2-4 open with the placeholder value "1,234" in the corner
# box, and "Begins '1,234'" describes nothing. A description has to be words
# -- so the opening line is only used when it reads like one, and a page
# with no such line is described by its structure alone rather than by a
# number that happens to be printed first.
def _reads_like_a_heading(line: str) -> bool:
    if not _line_is_readable(line):
        return False
    if re.match(r"(?i)^page\s+\d+(\s+of\s+\d+)?$", line.strip()):
        return False                        # page furniture, not content
    wordy = [w for w in line.split() if sum(c.isalpha() for c in w) >= 2]
    return len(wordy) >= 2


def _rendered_page_notes(pdf_path, count: int) -> list:
    """One short, honest note per rendered page: [{heading, lines, words}].

    Never raises: a description is a nicety, the pages are the product.
    """
    notes: list = []
    try:
        import fitz
        with fitz.open(str(pdf_path)) as doc:
            for i in range(min(count, doc.page_count)):
                raw = doc[i].get_text("text") or ""
                lines = [" ".join(ln.split()) for ln in raw.splitlines()]
                lines = [ln for ln in lines if ln]
                head = next((ln for ln in lines
                             if _reads_like_a_heading(ln)), "")
                notes.append({"heading": head[:72],
                              "lines": len(lines),
                              "words": len(raw.split())})
    except Exception:  # noqa: BLE001 - no fitz, an odd PDF, a locked file
        return []
    return notes


@app.post("/api/render-preview")
def api_render_preview():
    """Page images of what the generated RDL ACTUALLY prints.

    The HTML mockup re-implements Oracle's layout in the browser, so it can
    only approximate the report. This runs the generated RDL through
    Microsoft's own ReportViewer engine and rasterises the result, so the
    preview cannot disagree with the deliverable -- it IS the deliverable.

    Rendered in layout mode (expressions staticized): geometry, pagination
    and page count are faithful; computed values appear as placeholders.
    """
    import base64
    import tempfile

    rdl = (_last() or {}).get("rdl_xml")
    if not rdl:
        return _err("convert a report first", "no_report_yet", 400)
    tools = str(HERE.parent / "tools" / "renderlab")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    # OUT OF THE BOX, LOCAL FIRST. The local ReportViewer engine must work
    # on a fresh clone with no manual steps: its DLL folder (gitignored
    # Microsoft redistributables) is fetched AUTOMATICALLY on first use --
    # a download into the project folder, no install, no admin rights. The
    # customer's report server is the FALLBACK for machines that cannot
    # reach nuget.org, never a prerequisite (user: "it literally needs to
    # work out of the box").
    rsu = (request.form.get("report_server_url") or "").strip()
    server_note = ""

    def _try_server_render():
        """Render on the customer's SSRS; returns (response|None, note)."""
        if not rsu:
            return None, ""
        import subprocess
        ps1 = HERE.parent / "tools" / "ssrscheck" / "server_render.ps1"
        try:
            with tempfile.TemporaryDirectory() as d:
                rp = Path(d) / "preview.rdl"
                rp.write_text(rdl, encoding="utf-8")
                pdf = Path(d) / "preview.pdf"
                proc = subprocess.run(
                    ["powershell", "-NoProfile", "-ExecutionPolicy",
                     "Bypass", "-File", str(ps1), "-RdlPath", str(rp),
                     "-ServerUrl", rsu, "-OutPdf", str(pdf)],
                    capture_output=True, text=True, timeout=180)
                log = (proc.stdout or "") + (proc.stderr or "")
                if proc.returncode == 0 and pdf.exists()                         and pdf.stat().st_size > 100:
                    pdf_b64 = base64.b64encode(
                        pdf.read_bytes()).decode("ascii")
                    return jsonify({
                        "pdf": "data:application/pdf;base64," + pdf_b64,
                        "mode": "server",
                        "note": "Rendered by YOUR report server ("
                                + rsu + ") — real expression evaluation."}), ""
                return None, next(
                    (ln for ln in log.splitlines()
                     if ln.startswith("SERVER FAIL")), log[-200:].strip())
        except Exception as e:  # noqa: BLE001
            return None, f"server render unavailable: {e}"

    local_err = ""
    preview_pages = render_to_pdf = None
    try:
        from rdl_preview import preview_pages, render_to_pdf
        from render import lib_ready
        if not lib_ready():
            _autofetch_render_engine()
        if not lib_ready():
            local_err = ("the render engine's DLLs could not be fetched "
                         "(machine cannot reach nuget.org)")
    except Exception as e:  # noqa: BLE001 - renderlab is an optional extra
        local_err = f"render engine unavailable: {e}"

    if local_err:
        # local engine impossible on this machine -> the customer's own
        # report server is the remaining true-render path
        resp, server_note = _try_server_render()
        if resp is not None:
            return resp
        msg = local_err
        if server_note:
            msg += "; " + server_note
        elif not rsu:
            msg += ("; set your report server URL in the sidebar to render "
                    "through your own SSRS instead")
        return _err(msg, "render_engine_unavailable", 503)

    # Per-page PNGs need PyMuPDF (import name: fitz). It is a machine-local
    # rasteriser, NOT part of the render itself -- so when it is absent the
    # preview must not die with a bare ModuleNotFoundError (that shipped
    # once: the feature worked on the machine it was built on and 500'd on
    # the next machine that pulled the repo). Fall back to returning the
    # rendered PDF itself, which every browser can display natively.
    try:
        import fitz  # noqa: F401
        have_fitz = True
    except ImportError:
        have_fitz = False

    try:
        rows = max(1, min(25, int(request.form.get("rows") or 3)))
        with tempfile.TemporaryDirectory() as d:
            if have_fitz:
                pages = preview_pages(rdl, d, rows=rows)
                if not pages:
                    return jsonify({
                        "error": "the report engine could not render this RDL",
                        "error_kind": "render_engine_unavailable",
                        "pages": []}), 502
                imgs = ["data:image/png;base64," +
                        base64.b64encode(p.read_bytes()).decode("ascii")
                        for p in pages]
                note = ("Rendered locally (layout mode)."
                        + ((" Server render failed: " + server_note)
                           if server_note else ""))
                # preview_pages renders to report.pdf in this same dir and
                # rasterises it, so the page TEXT is already on disk: it is
                # what the alt text of each page image describes.
                return jsonify({"pages": imgs, "count": len(imgs),
                                "page_notes": _rendered_page_notes(
                                    Path(d) / "report.pdf", len(imgs)),
                                "mode": "layout", "note": note})
            pdf_path = Path(d) / "report.pdf"
            res = render_to_pdf(rdl, pdf_path, rows=rows)
            if not res["ok"]:
                # the render log carries the actionable reason (e.g. the
                # ReportViewer DLL fetch step) -- surface its tail, not a
                # generic failure
                tail = (res.get("log") or "")[-300:].strip()
                return _err("the report engine could not render this RDL: "
                            + tail, "render_engine_unavailable", 502)
            pdf_b64 = base64.b64encode(pdf_path.read_bytes()).decode("ascii")
        return jsonify({
            "pdf": "data:application/pdf;base64," + pdf_b64,
            "mode": "layout",
            "note": "Showing the locally rendered PDF directly. "
                    "(Optional: the PyMuPDF Python package turns this "
                    "into per-page images.)"
                    + ((" Server render failed: " + server_note)
                       if server_note else "")})
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        return _crash(e)


@app.post("/api/compare")
def api_compare():
    """Compare two Oracle XML reports (file_a, file_b) and return a structured diff."""
    a = request.files.get("file_a")
    b = request.files.get("file_b")
    if not a or not b:
        return _err("need two files (file_a, file_b)", "nothing_selected", 400)
    from converter.parsers.oracle_xml import parse_oracle_xml
    from converter.compare import compare_reports
    try:
        ra = parse_oracle_xml(a.read())
        rb = parse_oracle_xml(b.read())
        return jsonify(compare_reports(ra, rb))
    except Exception as e:
        traceback.print_exc()
        return _crash(e)


def _rdf_export_hint(name: str) -> str:
    """The rwconverter export command for one .rdf, in the INGEST'S OWN
    WORDS -- asking it what it would say about a drop containing only that
    filename.

    Re-typing the command here would make two copies of the one sentence
    this operator cannot work without, free to drift apart. Asking the
    builder that already owns it cannot drift: both drop shapes -- the .rdf
    alone, and the .rdf inside a folder that also converted -- are answered
    with the same words, forever.
    """
    if not name:
        return ""
    try:
        return (convert_bundle([(name, b"")]) or {}).get("rdf_hint") or ""
    except Exception:  # noqa: BLE001 -- guidance is a nicety, never a 500
        traceback.print_exc()
        return ""


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
        return _err("no files uploaded", "nothing_selected", 400)
    try:
        target_db = _resolve_target_db(request)
        data = convert_bundle(files, target_db=target_db)
        # "Nothing here is convertible" is answered with HTTP 200 and a
        # sentinel, because the ingest report that comes with it is useful.
        # Give it a kind too, so the frontend picks the next step out of a
        # code rather than out of the sentence: a compiled .rdf and a folder
        # with no report in it need completely different advice.
        if data.get("error") == "no_convertible_artifacts":
            data["error_kind"] = ("rdf_binary" if data.get("rdf_hint")
                                  else "no_report_in_drop")
        # A .rdf that arrives BESIDE something convertible still cannot be
        # read, and the operator still has to export it. The ingest only
        # writes that recovery command on the "nothing convertible" answer,
        # so the everyday shape -- drop the report's folder, which holds the
        # .xml AND the .rdf -- silently lost the one instruction that
        # unblocks it. Attach it to every answer whose ingest saw one.
        if not data.get("rdf_hint"):
            hint = _rdf_export_hint(
                (data.get("ingest_report") or {}).get("rdf_binary") or "")
            if hint:
                data["rdf_hint"] = hint
        if data.get("rdl_xml"):
            data["rdl_xml"] = _apply_deploy_datasource(data["rdl_xml"], request)
        _reconcile_checklist(data)
        if "report" in data:
            _set_last(data)
        return jsonify(data)
    except Exception as e:
        traceback.print_exc()
        return _crash(e)


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
        _reconcile_checklist(data)
        _set_last(data)
        return jsonify(data)
    except Exception as e:
        traceback.print_exc()
        return _crash(e)


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
        return _err("no Oracle Reports XML files found in the upload",
                    "no_report_in_drop", 400)
    target_db = _resolve_target_db(request)
    want_render = (request.form.get("render") or "").strip() in ("1", "true", "on")
    try:
        batch = batch_convert(items, target_db=target_db, render=want_render)
        # Session data source binding applies to every artifact we ship.
        for r in batch.get("results") or []:
            if r.get("rdl_xml"):
                r["rdl_xml"] = _apply_deploy_datasource(r["rdl_xml"], request)
                _reconcile_checklist(r)
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
        return _crash(e)


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
        return _err("no image uploaded", "nothing_selected", 400)
    mime = (f.mimetype or "").lower()
    if not mime.startswith("image/"):
        return _err("file is not an image", "image_rejected", 400)
    import base64 as _b64
    blob = f.read()
    if len(blob) > 4 * 1024 * 1024:
        return _err("image too large (4 MB max)", "image_rejected", 400)
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
        _reconcile_checklist(data)
        _set_last(data)
        return jsonify(data)
    except Exception as e:
        traceback.print_exc()
        return _crash(e)


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
    _reconcile_checklist(data)
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
        return _crash(e)


@app.get("/api/health")
def api_health():
    # max_upload_bytes is part of the health contract: the frontend uses it
    # both to pre-flight uploads and as its cheap "is the server actually
    # reachable?" probe when a fetch rejects at the network level.
    limit = _max_upload_bytes()
    return jsonify({
        "ok": True,
        "samples": [p.name for p in SAMPLES.glob("*.xml") if _valid_sample(p)] if SAMPLES.exists() else [],
        "max_upload_bytes": limit,
        "max_upload_mb": round(limit / (1024 * 1024), 2),
    })


@app.get("/api/ai/test")
def api_ai_test():
    """One-shot test call to Anthropic to surface auth/model errors clearly."""
    from converter.ai_runner import _call_claude, DEFAULT_MODEL, _api_key, is_configured
    if not is_configured():
        return jsonify({"ok": False, "error": "ai_not_configured",
                        "error_kind": "ai_not_configured"}), 400
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
            "error_kind": "server_error",
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
        return _err("no report converted yet", "no_report_yet", 400)
    from converter.ai_runner import auto_fix, is_configured
    if not is_configured():
        return jsonify({
            "error": "ai_not_configured",
            "error_kind": "ai_not_configured",
            "hint": "Set ANTHROPIC_API_KEY in your .env (see .env.example)."
        }), 400
    try:
        updated = auto_fix(data)
        if updated.get("rdl_xml"):
            updated["rdl_xml"] = _apply_deploy_datasource(
                updated["rdl_xml"], request)
        _reconcile_checklist(updated)
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
        return _crash(e)


@app.post("/api/apply-fix")
def api_apply_fix():
    """Apply a pasted AI translation back into the most recent conversion."""
    payload = request.get_json(silent=True) or {}
    target = payload.get("target") or {}
    body = payload.get("new_body") or ""
    from converter.ai_apply import validate_udf_body, apply_fix
    ok, issues = validate_udf_body(body, target.get("name"))
    if not ok:
        return jsonify({"error": "validation_failed",
                        "error_kind": "validation_failed",
                        "issues": issues}), 400
    data = _last()
    if not data or not data.get("rdl_xml"):
        return _err("no report converted yet", "no_report_yet", 400)
    try:
        updated_rdl, info = apply_fix(data["rdl_xml"], target, body)
        data["rdl_xml"] = _apply_deploy_datasource(updated_rdl, request)
        _reconcile_checklist(data)
        data.setdefault("applied_fixes", []).append({"target": target, "info": info})
        _set_last(data)
        return jsonify({"ok": True, "info": info, "warnings": issues})
    except Exception as e:
        traceback.print_exc()
        return _crash(e)


@app.get("/api/mockup/<variant>")
def api_mockup_variant(variant):
    """Render the mockup in print or compact form."""
    if variant not in ("print", "compact"):
        abort(404)
    data = _last()
    if not data or not data.get("report"):
        return _err("no report converted yet", "no_report_yet", 404)
    try:
        from converter.preview.mockup_variants import render_mockup_print, render_mockup_compact
        from converter.parsers.oracle_xml import parse_oracle_xml
        oracle_xml = data.get("oracle_xml") or ""
        parsed = parse_oracle_xml(oracle_xml.encode("utf-8") if isinstance(oracle_xml, str) else oracle_xml)
        html = render_mockup_print(parsed) if variant == "print" else render_mockup_compact(parsed)
        return html, 200, {"Content-Type": "text/html; charset=utf-8"}
    except Exception as e:
        traceback.print_exc()
        return _crash(e)


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
        return _err("no report converted yet", "no_report_yet", 400)
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
        return _crash(e)


@app.post("/api/download/burst-pack")
def api_download_burst_pack():
    """Build and stream a Burst Pack zip for the last-converted report,
    applying UI-form config overrides."""
    payload = request.get_json(silent=True) or {}
    overrides = payload.get("config_overrides") or {}
    parsed = _last_parsed_report()
    if parsed is None:
        return _err("no report converted yet", "no_report_yet", 400)
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
        return _crash(e)


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
        return _err("invalid child report name", "invalid_request", 400)
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
        return _err("invalid child report name", "invalid_request", 400)
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
    # Human display label for this child (e.g. "JV Standard 12 x 9 Envelope").
    # Sizes an envelope child (parses "12 x 9") AND is stored as the parent's
    # generate-all link text so the cover reads the label, not the report name.
    _body = request.get_json(silent=True) or {}
    label = (request.form.get("display_label") or _body.get("display_label")
             or request.values.get("display_label") or "").strip()
    if label:
        _GEN_ALL_LABEL_STORE[_sid()] = label
        _evict(_GEN_ALL_LABEL_STORE)
    try:
        result = build_subreport(child_name, paths,
                                 parent_param_names=parent_params,
                                 drillthrough_params=dt_params,
                                 display_label=label)
    except Exception as e:
        traceback.print_exc()
        return _crash(e)
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
    found, changed, newp = _resync_child_ref(prdl, child_name, actual)
    if found:
        if changed:
            last["rdl_xml"] = newp
            _set_last(last)
            parent_rdl_out = newp
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

"""A dropped artifact FOLDER must never fail as an unexplained upload error.

THE BUG (live, user-reported): the dropzone appended every file in a dropped
folder to the FormData. A real artifact folder is mostly REFERENCE material --
one folder carried ~36 MB of which a rendered truth PDF was ~35 MB, while the
only convertible input was a ~270 KB Oracle XML export. That blew the server's
MAX_CONTENT_LENGTH, and because the server answers before the upload has
finished streaming, the browser reported a bare network failure
("Failed to fetch") with a red ERROR badge and no reason: the JSON 413 body
never reached the JS that knows how to read it.

Four layers are guarded here, in the two ways this suite can prove things:

  * HTTP/JSON contract (a real Flask test client): the cap is exposed to the
    client, the 413 handler still answers in the app's error shape, and a
    filtered folder-shaped bundle converts.
  * STATIC SOURCE assertions on the shipped frontend (same pattern as
    tests/test_conditional_dashboard.py): the suite cannot execute a browser,
    so the load-bearing client behaviour is pinned at the source level.

Plus a real JS-execution layer when node is available -- partitionUploadList
and the pre-flight are pure functions, so their actual behaviour (not just
their presence) is exercised against a simulated folder listing.

All fixtures are synthetic and structural -- no client report names.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

FRONTEND = ROOT / "frontend"
APP_JS = FRONTEND / "static" / "js" / "app.js"
INDEX_HTML = FRONTEND / "templates" / "index.html"
APP_PY = ROOT / "backend" / "app.py"

MB = 1024 * 1024


@pytest.fixture()
def client():
    from app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _app_js() -> str:
    return APP_JS.read_text(encoding="utf-8")


def _index_html() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


# A minimal synthetic Oracle report -- the only convertible input in the
# simulated folder below.
_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<report name="SAMPLE_FOLDER_DROP" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_MAIN">
      <select><![CDATA[SELECT T.Doc_No, T.Amount FROM Docs T]]></select>
      <group name="G_MAIN">
        <dataItem name="Doc_No" datatype="number"/>
        <dataItem name="Amount" datatype="number"/>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="main">
    <body width="8.0" height="10.0">
      <text name="B_TITLE" x="0.5" y="0.4" width="7.0" height="0.4">
        <textsettings justify="center"/>
        <font face="Arial" size="14" bold="yes"/>
        <contents>Folder Drop Sample</contents>
      </text>
      <field name="F_DOC" x="0.5" y="1.2" width="3.0" height="0.3" source="Doc_No"/>
      <field name="F_AMT" x="4.0" y="1.2" width="3.0" height="0.3" source="Amount"/>
    </body>
  </section>
  </layout>
</report>
"""


# ---------------------------------------------------------------------------
# LAYER 3 -- the cap: sane default, env stays authoritative, 413 still JSON
# ---------------------------------------------------------------------------

def test_default_upload_cap_fits_a_folder_drop():
    """16 MB was sized for a single XML; a routine artifact folder exceeds it."""
    import app as app_mod
    assert app_mod.DEFAULT_MAX_UPLOAD_MB >= 64, (
        "the default cap must accommodate a real artifact folder drop")


def test_env_var_remains_authoritative_over_the_default(monkeypatch):
    import app as app_mod
    monkeypatch.setenv("O2S_MAX_UPLOAD_MB", "7")
    assert app_mod._configured_max_upload_mb() == 7
    monkeypatch.setenv("O2S_MAX_UPLOAD_MB", "512")
    assert app_mod._configured_max_upload_mb() == 512


def test_unusable_env_value_falls_back_instead_of_crashing(monkeypatch):
    """A mistyped env var must never take the process down at import time."""
    import app as app_mod
    for bad in ("", "   ", "sixteen", "0", "-5"):
        monkeypatch.setenv("O2S_MAX_UPLOAD_MB", bad)
        assert app_mod._configured_max_upload_mb() == app_mod.DEFAULT_MAX_UPLOAD_MB, (
            "env value %r must fall back to the default" % bad)


def test_413_handler_still_returns_the_apps_json_error_shape(client):
    """Raising the cap must not cost us the readable 413 when it DOES fire."""
    from app import app
    saved = app.config.get("MAX_CONTENT_LENGTH")
    app.config["MAX_CONTENT_LENGTH"] = 4096
    try:
        r = client.post(
            "/api/convert-bundle",
            data={"files": (io.BytesIO(b"x" * 200_000), "big.xml")},
            content_type="multipart/form-data")
        assert r.status_code == 413
        assert r.mimetype == "application/json", "413 leaked a non-JSON page"
        body = r.get_json()
        assert isinstance(body, dict) and body.get("error"), (
            "413 must use the app's {'error': ...} shape")
        assert "html" not in r.get_data(as_text=True).lower()[:200]
    finally:
        app.config["MAX_CONTENT_LENGTH"] = saved


# ---------------------------------------------------------------------------
# LAYER 2 -- the cap is DISCOVERABLE by the client (never hardcoded in JS)
# ---------------------------------------------------------------------------

def test_health_publishes_the_effective_upload_cap(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    j = r.get_json()
    assert "max_upload_bytes" in j, "the client cannot pre-flight without the cap"
    from app import app
    assert j["max_upload_bytes"] == app.config["MAX_CONTENT_LENGTH"]
    assert j["max_upload_bytes"] > 0


def test_health_cap_tracks_config_not_a_constant(client):
    """The published cap must follow the live config, not a frozen literal."""
    from app import app
    saved = app.config.get("MAX_CONTENT_LENGTH")
    app.config["MAX_CONTENT_LENGTH"] = 3 * MB
    try:
        j = client.get("/api/health").get_json()
        assert j["max_upload_bytes"] == 3 * MB
        assert abs(j["max_upload_mb"] - 3) < 0.01
    finally:
        app.config["MAX_CONTENT_LENGTH"] = saved


def test_page_renders_the_cap_as_a_data_attribute(client):
    """The FIRST drop happens before any API round trip -- the cap must already
    be in the page."""
    from app import app
    saved = app.config.get("MAX_CONTENT_LENGTH")
    app.config["MAX_CONTENT_LENGTH"] = 12 * MB
    try:
        html = client.get("/").get_data(as_text=True)
        m = re.search(r'data-max-upload-bytes="(\d+)"', html)
        assert m, "index page must carry data-max-upload-bytes"
        assert int(m.group(1)) == 12 * MB
    finally:
        app.config["MAX_CONTENT_LENGTH"] = saved


def test_frontend_never_hardcodes_a_megabyte_cap():
    """The number lives on the server. If JS ever re-hardcodes it, the client
    and server drift and the doomed-request bug comes straight back."""
    js = _app_js()
    assert "data-max-upload-bytes" in js, (
        "app.js must read the cap from the page")
    assert re.search(r"max_upload_bytes", js), (
        "app.js must read the cap from /api/health too")
    # No literal 16/64 MB byte constants standing in for the cap.
    for bad in ("16 * 1024 * 1024", "64 * 1024 * 1024", "16777216", "67108864"):
        assert bad not in js, (
            "app.js hardcodes an upload cap (%s) instead of reading the "
            "server's value" % bad)


# ---------------------------------------------------------------------------
# LAYER 1 -- the relevance filter exists in the shipped JS and is STRUCTURAL
# ---------------------------------------------------------------------------

def test_bundle_upload_partitions_before_sending():
    js = _app_js()
    assert "function partitionUploadList" in js
    assert "function describeSkipped" in js
    # The dropzone path must actually use it -- a filter nothing calls is a
    # filter that does not exist.
    body = js[js.index("async function uploadBundle("):]
    body = body[:body.index("\nasync function runSample")]
    assert "partitionUploadList(list)" in body, (
        "uploadBundle must partition the dropped list before uploading")
    assert "part.send.forEach" in body, (
        "uploadBundle must append only the filtered subset to the FormData")
    assert "list.forEach(f => fd.append" not in body, (
        "the unfiltered append is exactly the bug -- it must be gone")


def test_partition_rule_is_extension_and_size_only():
    """Never filename patterns: a rule keyed on report names is not general
    and would break on the next customer's folder."""
    js = _app_js()
    start = js.index("const UPLOAD_INPUT_EXTS")
    block = js[start:js.index("// ----- API calls -----")]
    assert "UPLOAD_CONTEXT_MAX_BYTES" in block
    # The decision function may look at extension + size, nothing else.
    part = js[js.index("function partitionUploadList"):]
    part = part[:part.index("\n// One short line")]
    assert "uploadExt(name)" in part and "f.size" in part
    for smell in ("indexOf(\"truth\")", "includes(\"truth\")", "/report/i",
                  "toLowerCase().includes(", "startsWith(\"Q_\")"):
        assert smell not in part, (
            "partitionUploadList must not branch on filename content: %s" % smell)


def test_skip_line_is_shown_to_the_user():
    js = _app_js()
    body = js[js.index("async function uploadBundle("):]
    body = body[:body.index("\nasync function runSample")]
    assert "describeSkipped(part.skipped" in body
    assert "toast(skipLine" in body, "the skip line must reach the UI, not just console"


def test_preflight_blocks_a_doomed_request():
    js = _app_js()
    assert "function uploadTooLargeMessage" in js
    body = js[js.index("async function uploadBundle("):]
    body = body[:body.index("\nasync function runSample")]
    pre = body.index("uploadTooLargeMessage(part.send)")
    post = body.index("postFormOrExplain(\"/api/convert-bundle\"")
    assert pre < post, "the size check must run BEFORE the request is fired"
    assert "return;" in body[pre:post], "an oversized payload must not be sent"


# ---------------------------------------------------------------------------
# LAYER 4 -- network-level rejections get an actionable message
# ---------------------------------------------------------------------------

def test_every_upload_post_routes_through_the_network_explainer():
    """A raw fetch on an upload path can reject with 'Failed to fetch'. Each
    one must go through the wrapper that turns that into a real message."""
    js = _app_js()
    assert "async function postFormOrExplain" in js
    assert "async function explainNetworkFailure" in js
    assert "async function serverReachable" in js
    upload_urls = ["/api/convert-bundle", "/api/convert\"", "/api/batch",
                   "/api/report-images/upload"]
    for url in upload_urls:
        for m in re.finditer(re.escape(url), js):
            line_start = js.rfind("\n", 0, m.start()) + 1
            line = js[line_start:js.index("\n", m.start())]
            if "fetch(" in line:
                assert "postFormOrExplain" in line, (
                    "raw fetch on an upload path can surface 'Failed to fetch': %s"
                    % line.strip())


def test_reachability_is_probed_before_blaming_size():
    js = _app_js()
    fn = js[js.index("async function explainNetworkFailure"):]
    fn = fn[:fn.index("\n// Every upload POST")]
    assert "await serverReachable()" in fn
    probe_at = fn.index("serverReachable()")
    size_at = fn.index("exceeds this server")
    assert probe_at < size_at, "must check the server is up before blaming size"
    assert "run.bat" in fn, "the unreachable-server message must be actionable"


def test_error_badge_carries_the_reason():
    """The user's screenshot showed a persistent red ERROR badge with no
    explanation. setStatus must accept and attach a reason.

    STRENGTHENED, not relaxed. This used to require ``pill.title = detail`` --
    i.e. the reason attached as a HOVER TOOLTIP. A title= needs a mouse: it
    never opens for a keyboard user, does not exist on touch, and is announced
    inconsistently, which is why the app forbids hover-only information
    page-wide (test_a11y_structure.py::
    test_no_control_hides_what_it_does_in_a_hover_tooltip). So the reason now
    has to reach the user through two carriers that need NO mouse, and both
    are checked here: the persistent on-screen status card, and the pill's
    accessible name. The tooltip assertion could be satisfied while the reason
    was unreachable for the operator this app is for; these cannot.
    """
    js = _app_js()
    fn = js[js.index("function setStatus(") - 400:]
    fn = fn[:fn.index("function toast(")]
    assert "function setStatus(text, kind, detail)" in fn
    # setStatus's OWN body, not the whole neighbourhood: the mirror function
    # is defined a few lines below, so searching the region for its name
    # passes whether or not anything actually calls it.
    body = js[js.index("function setStatus(text, kind, detail) {"):]
    body = body[:body.index("\n}\n")]
    assert "_mirrorStatus(" in body, (
        "setStatus must write the reason through to the persistent status "
        "surface; the badge alone is where this bug was reported from")
    assert "aria-label" in fn
    assert "currentStatus()" in fn, (
        "the pill must be rendered FROM the status surface, so the reason it "
        "shows is the reason the card shows")
    assert "pill.removeAttribute(\"title\")" in fn, (
        "the reason must not be parked in a hover-only tooltip")
    # And the failure sites must pass one.
    assert 'setStatus("Error", "err");' not in js, (
        "a bare Error badge with no reason is the reported defect")
    assert "setStatus('Error', 'err');" not in js


# ---------------------------------------------------------------------------
# HTTP: the FILTERED bundle actually converts
# ---------------------------------------------------------------------------

def test_filtered_folder_bundle_converts_over_http(client):
    """What the client sends after filtering must still be a full conversion:
    the XML plus the small SQL/doc artifacts, no reference PDF."""
    data = {
        "files": [
            (io.BytesIO(_XML), "FOLDER/report.xml"),
            (io.BytesIO(b"SELECT A.Doc_No FROM Docs A\n"), "FOLDER/Q_MAIN.sql"),
        ],
    }
    r = client.post("/api/convert-bundle", data=data,
                    content_type="multipart/form-data")
    assert r.status_code == 200, r.get_data(as_text=True)[:400]
    j = r.get_json()
    assert not j.get("error"), j.get("error")
    assert j.get("rdl_xml"), "the filtered bundle must still produce an RDL"
    assert "ingest_report" in j


def test_a_reference_pdf_changes_nothing_about_the_conversion(client):
    """The STRUCTURAL justification for skipping large PDFs.

    A rendered PDF is truth/reference material: the ingest only samples it for
    a page-count cross-check stat. If that ever stops being true -- if a PDF
    starts contributing to the generated RDL -- the relevance filter would
    silently cause data loss, and this gate goes red first.
    """
    def _post(files):
        r = client.post("/api/convert-bundle",
                        data={"files": files},
                        content_type="multipart/form-data")
        assert r.status_code == 200, r.get_data(as_text=True)[:300]
        return r.get_json()

    without = _post([(io.BytesIO(_XML), "FOLDER/report.xml")])
    with_pdf = _post([
        (io.BytesIO(_XML), "FOLDER/report.xml"),
        (io.BytesIO(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n" + b"0" * 4096), "FOLDER/report.pdf"),
    ])
    assert without.get("rdl_xml"), "control conversion produced no RDL"
    assert with_pdf["rdl_xml"] == without["rdl_xml"], (
        "a PDF changed the generated RDL -- it is no longer safe to skip one")
    assert (with_pdf.get("warnings") or []) == (without.get("warnings") or []), (
        "a PDF changed the conversion warnings -- re-examine the filter")


def test_unfiltered_folder_bundle_is_what_used_to_break(client):
    """Control + regression record: the SAME folder with its reference PDF
    attached exceeds a folder-sized cap. This is the request the client must
    now never send."""
    from app import app
    saved = app.config.get("MAX_CONTENT_LENGTH")
    app.config["MAX_CONTENT_LENGTH"] = 2 * MB
    try:
        data = {
            "files": [
                (io.BytesIO(_XML), "FOLDER/report.xml"),
                (io.BytesIO(b"%PDF-1.4\n" + b"0" * (3 * MB)), "FOLDER/report.pdf"),
            ],
        }
        r = client.post("/api/convert-bundle", data=data,
                        content_type="multipart/form-data")
        assert r.status_code == 413
    finally:
        app.config["MAX_CONTENT_LENGTH"] = saved


# ---------------------------------------------------------------------------
# JS EXECUTION -- run the real client functions against a simulated folder
# ---------------------------------------------------------------------------

_NODE = shutil.which("node")


def _run_js(snippet: str) -> dict:
    """Execute a snippet with app.js's pure upload helpers in scope.

    app.js is a browser script (touches document/window at load), so the
    helper block under test is extracted by name rather than the file being
    imported wholesale.
    """
    js = _app_js()
    start = js.index("const UPLOAD_INPUT_EXTS")
    end = js.index("// ----- API calls -----")
    helpers = js[start:end]
    # maxUploadBytes() reads the DOM; stub the two globals it touches.
    prelude = textwrap.dedent("""
        const state = { _maxUploadBytes: 0 };
        const document = { body: { getAttribute: (k) =>
            (k === 'data-max-upload-bytes' ? String(globalThis.__CAP__ || 0) : null) } };
    """)
    src = prelude + helpers + "\n" + snippet
    out = subprocess.run([_NODE, "-e", src], capture_output=True, text=True,
                         timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


# A simulated folder listing shaped like a real artifact folder: one modest
# Oracle XML, a couple of small supporting artifacts, and a big reference PDF.
_FOLDER_JS = """
const folder = [
  { name: 'report.xml',  _relPath: 'F/report.xml',  size: 268 * 1024 },
  { name: 'report.rdf',  _relPath: 'F/report.rdf',  size: 680 * 1024 },
  { name: 'queries.docx',_relPath: 'F/queries.docx',size: 40 * 1024 },
  { name: 'seal.png',    _relPath: 'F/seal.png',    size: 18 * 1024 },
  { name: 'report.pdf',  _relPath: 'F/report.pdf',  size: 35 * 1024 * 1024 },
];
"""


@pytest.mark.skipif(_NODE is None, reason="node not available")
def test_js_folder_drop_uploads_only_convertible_files():
    r = _run_js(_FOLDER_JS + """
const p = partitionUploadList(folder);
console.log(JSON.stringify({
  sent: p.send.map(f => f.name),
  skipped: p.skipped.map(s => s.name),
  skippedBytes: p.skippedBytes,
  line: describeSkipped(p.skipped, p.skippedBytes),
}));
""")
    assert r["sent"] == ["report.xml", "report.rdf", "queries.docx", "seal.png"]
    assert r["skipped"] == ["F/report.pdf"]
    assert r["skippedBytes"] == 35 * MB
    assert r["line"].startswith("Skipped 1 reference file (35.0 MB)")
    assert "aren't conversion inputs" in r["line"]


@pytest.mark.skipif(_NODE is None, reason="node not available")
def test_js_large_inputs_are_never_dropped_by_size():
    """Oracle XML exports genuinely run to several MB -- size must never be a
    reason to drop a real input, or the filter causes data loss."""
    r = _run_js("""
const p = partitionUploadList([
  { name: 'big.xml',  size: 9 * 1024 * 1024 },
  { name: 'big.docx', size: 8 * 1024 * 1024 },
  { name: 'big.sql',  size: 5 * 1024 * 1024 },
]);
console.log(JSON.stringify({ sent: p.send.map(f => f.name), skipped: p.skipped.length }));
""")
    assert r["skipped"] == 0
    assert r["sent"] == ["big.xml", "big.docx", "big.sql"]


@pytest.mark.skipif(_NODE is None, reason="node not available")
def test_js_small_context_artifacts_still_ride_along():
    """A small PDF contributes a page-count cross-check and a small image is a
    report asset -- those still go up."""
    r = _run_js("""
const p = partitionUploadList([
  { name: 'render.pdf', size: 300 * 1024 },
  { name: 'logo.png',   size: 20 * 1024 },
]);
console.log(JSON.stringify({ sent: p.send.map(f => f.name), skipped: p.skipped.length }));
""")
    assert r["skipped"] == 0
    assert r["sent"] == ["render.pdf", "logo.png"]


@pytest.mark.skipif(_NODE is None, reason="node not available")
def test_js_preflight_passes_the_filtered_folder_and_blocks_the_raw_one():
    r = _run_js(_FOLDER_JS + """
globalThis.__CAP__ = 64 * 1024 * 1024;
const p = partitionUploadList(folder);
console.log(JSON.stringify({
  filtered: uploadTooLargeMessage(p.send),
  raw: uploadTooLargeMessage(folder),
  cap: maxUploadBytes(),
}));
""")
    assert r["cap"] == 64 * MB
    assert r["filtered"] is None, "the filtered folder must upload cleanly"
    # A raw 36 MB folder fits under 64 MB -- that is the point of layer 3.
    assert r["raw"] is None


@pytest.mark.skipif(_NODE is None, reason="node not available")
def test_js_oversized_convertible_payload_gets_an_actionable_message():
    """Not 'Failed to fetch': name the offenders and the real cap."""
    r = _run_js("""
globalThis.__CAP__ = 64 * 1024 * 1024;
const huge = [
  { name: 'a.xml', size: 40 * 1024 * 1024 },
  { name: 'b.xml', size: 30 * 1024 * 1024 },
  { name: 'c.sql', size: 1024 },
];
console.log(JSON.stringify({ msg: uploadTooLargeMessage(huge) }));
""")
    msg = r["msg"]
    assert msg, "an over-cap convertible payload must be refused client-side"
    assert "70.0 MB" in msg and "64.0 MB" in msg, msg
    assert "a.xml (40.0 MB)" in msg and "b.xml (30.0 MB)" in msg, msg
    assert "O2S_MAX_UPLOAD_MB" in msg, msg
    assert "Failed to fetch" not in msg


@pytest.mark.skipif(_NODE is None, reason="node not available")
def test_js_preflight_is_silent_when_the_cap_is_unknown():
    """If the server never told us the cap, do not invent one and block the
    user -- let the request go and let the server answer."""
    r = _run_js("""
globalThis.__CAP__ = 0;
console.log(JSON.stringify({
  msg: uploadTooLargeMessage([{ name: 'a.xml', size: 900 * 1024 * 1024 }]),
}));
""")
    assert r["msg"] is None


@pytest.mark.skipif(_NODE is None, reason="node not available")
def test_js_multipart_overhead_is_budgeted():
    """Many small files are bigger on the wire than the sum of their sizes;
    squeaking under the cap and then being rejected is the whole bug."""
    r = _run_js("""
const many = Array.from({ length: 200 }, (_, i) => ({ name: 'f' + i + '.sql', size: 1000 }));
console.log(JSON.stringify({ est: estimateUploadBytes(many), raw: 200 * 1000 }));
""")
    assert r["est"] > r["raw"], "framing overhead must be accounted for"


# ---------------------------------------------------------------------------
# Generality guard
# ---------------------------------------------------------------------------

def test_no_client_identifiers_in_the_upload_gate():
    """Public repo, private reports -- the upload gate must name no real
    report and must not key on any file NAME at all.

    Scoped to the code this fix introduced: the client-side filter block and
    the server's upload-cap block.
    """
    js = _app_js()
    js_block = js[js.index("const UPLOAD_INPUT_EXTS"):js.index("// ----- API calls -----")]
    py = APP_PY.read_text(encoding="utf-8")
    py_block = py[py.index("DEFAULT_MAX_UPLOAD_MB"):py.index("# Secret key for signed")]
    blob = js_block + py_block
    # A bare ALL-CAPS token containing an underscore is the shape every real
    # report name in this domain takes.
    allowed = ("O2S_", "MAX_", "DEFAULT_", "UPLOAD_", "CONTENT_", "PDF")
    for tok in re.findall(r"\b[A-Z][A-Z0-9]{2,}_[A-Z0-9_]{2,}\b", blob):
        assert tok.startswith(allowed), (
            "possible client report identifier leaked into the upload gate: %s" % tok)
    # The filter is extension+size; no report/customer vocabulary at all.
    for word in ("invoice", "permit", "inspect", "complaint", "receipt", "grant"):
        assert word not in blob.lower(), (
            "upload gate mentions report-domain vocabulary %r -- not general" % word)

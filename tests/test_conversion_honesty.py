"""The app may not claim more than it did, and the checklist may not describe
a file that was never written.

Two honesty defects were measured on the real page and are guarded here.

  (a) THE HEADLINE OVERCLAIMED. A truncated Oracle export -- the first few
      hundred bytes of a real one -- converted, and the top of the screen
      said "Converted <that report's name>", with the corner pill saying the
      same, while the line underneath admitted "0 queries, 0 parameters and
      0 formulas came across". Nothing was hidden. But the HEADLINE, the one
      thing a reader takes away at a glance, announced a success that had
      not happened. The counts now decide the headline.

  (b) THE DEPLOY CHECKLIST DESCRIBED A DIFFERENT FILE. It is built inside
      convert(), from the SOURCE, before the operator's own deployment
      settings are applied to the RDL. With "Which database the report will
      read" = Oracle and "/Data Sources/StateOracle" typed into the sidebar,
      the .rdl that /api/download/rdl serves said:

          <DataSource Name="SharedDataSource">
          <DataSourceReference>/Data Sources/StateOracle</DataSourceReference>
          <QueryParameter Name=":P_SORT">
          <CommandText>SELECT ... TO_CHAR(...)

      and the checklist beside it said "a placeholder shared DataSource named
      AppDb", "Change connection string to Data Source=YOUR_SQL_SERVER;
      Initial Catalog=AppDb;...", "pick /Data Sources/AppDb", "Verify @P_*
      parameter bindings" and "Review T-SQL validation results". Five
      checkable claims; five of them wrong.

Everything below is asserted against THE RDL'S OWN CONTENT -- the bytes the
download serves -- never against what the UI meant to do. The contradiction
reader is itself mutation-proved: it is run over the exact pre-fix wording
and has to report every one of those five contradictions, or the tests built
on it are worthless.

Layer 1 (Flask test client) always runs. Layer 2 drives the real served page
and needs Playwright + Chromium; it skips without them.
"""
from __future__ import annotations

import re
import socket
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

SAMPLE = ROOT / "samples" / "oracle" / "SAMPLE_INSPECTION.xml"


# ---------------------------------------------------------------- the reader
# Every claim a deploy checklist makes that CAN be checked against the
# artifact, checked against the artifact. Returns a list of (code, detail).
# Codes exist so a test can insist the reader failed for the RIGHT reason
# rather than merely failing.

def _rdl_facts(rdl: str) -> dict:
    binds = re.findall(r'<QueryParameter\s+Name="([^"]*)"', rdl)
    prefix = ""
    for b in binds:
        if b[:1] in (":", "@"):
            prefix = b[:1]
            break
    return {
        "ds_names": [n for n in re.findall(r'<DataSource\s+Name="([^"]*)"', rdl) if n],
        "ds_refs": [r.strip() for r in
                    re.findall(r"<DataSourceReference>([^<]*)</DataSourceReference>", rdl)
                    if r.strip()],
        "embedded": bool(re.search(r"<ConnectString>\s*\S", rdl)),
        "params": re.findall(r'<ReportParameter\s+Name="([^"]*)"', rdl),
        # A prompt is text the FILE declares. When a parameter's prompt is
        # itself a P_-shaped name (P_REPORT_YEAR_ID prompting "P_REPORT_YEAR"
        # is real, in the corpus), the checklist quoting that prompt is
        # repeating the file, not inventing a parameter.
        "prompt_names": set(re.findall(
            r"P_[A-Za-z0-9_]+",
            " ".join(re.findall(r"<Prompt>([^<]*)</Prompt>", rdl)))),
        "bind": prefix,
        "other_bind": "@" if prefix == ":" else ":",
    }


def checklist_contradictions(steps, rdl: str):
    """What the checklist says that the .rdl says otherwise."""
    f = _rdl_facts(rdl)
    text = "\n".join((s.get("title") or "") + "\n" + (s.get("body_md") or "")
                     for s in steps)
    out = []

    # 1. BIND SYNTAX. An Oracle dataset binds ":P_X"; a SQL Server one binds
    #    "@P_X". The file uses exactly one of them.
    if f["bind"]:
        wrong = re.findall(
            r"(?<![A-Za-z0-9_])%s(P_[A-Za-z0-9_*]+)" % re.escape(f["other_bind"]),
            text)
        if wrong:
            out.append(("bind_syntax",
                        "checklist spells bind variables %s%s but the file "
                        "binds with %s" % (f["other_bind"], wrong[0],
                                           f["bind"])))

    # 2. CATALOG PATHS. Any backticked path the checklist prints has to be a
    #    data source reference the file actually carries.
    for path in re.findall(r"`(/[^`\n]+)`", text):
        if path not in f["ds_refs"]:
            out.append(("wrong_path",
                        "checklist sends the operator to %r; the file "
                        "references %r" % (path, f["ds_refs"])))

    # 3. DATA SOURCE IDENTITY. The thing it tells you to right-click has to
    #    be a data source the file declares.
    for named in re.findall(r"right-click \*\*([^*\n]+)\*\*", text, re.I):
        if named not in f["ds_names"]:
            out.append(("wrong_ds_name",
                        "checklist says right-click %r; the file declares %r"
                        % (named, f["ds_names"])))
    if f["ds_names"] and f["ds_names"][0] not in text:
        out.append(("ds_never_named",
                    "checklist never names the file's data source %r"
                    % f["ds_names"][0]))
    if f["ds_refs"] and f["ds_refs"][0] not in text:
        out.append(("ref_never_named",
                    "checklist never names what the file points at (%r)"
                    % f["ds_refs"][0]))

    # 4. DIALECT. The queries in the file are one flavour of SQL.
    if f["bind"] == ":" and "T-SQL" in text:
        out.append(("wrong_dialect",
                    "checklist calls the queries T-SQL; the file binds "
                    "Oracle-style and carries Oracle SQL"))
    if f["bind"] == "@" and "Oracle SQL" in text:
        out.append(("wrong_dialect",
                    "checklist calls the queries Oracle SQL; the file binds "
                    "SQL-Server-style and carries T-SQL"))

    # 5. CONNECTION STRINGS. A file that carries no embedded connection must
    #    not be described with one -- least of all one for the wrong engine.
    if not f["embedded"]:
        m = re.search(r"Initial Catalog=|Integrated Security=SSPI", text)
        if m:
            out.append(("invented_connection",
                        "checklist prints a connection string (%r) for a file "
                        "that carries none" % m.group(0)))

    # 5b. ...and a file that DOES carry one must not have it reprinted on
    #     screen. The connection is the one deployment setting that is a
    #     secret; the app's promise is that it goes into the file and
    #     nowhere else.
    for cs in re.findall(r"<ConnectString>([^<]*)</ConnectString>", rdl):
        if cs.strip() and cs.strip() in text:
            out.append(("leaked_connection",
                        "checklist reprints the connection string that was "
                        "typed into the sidebar"))

    # 6. PARAMETERS. Named ones must exist; existing ones must be named.
    #    The bold markers are NOT part of the match: this same reader is run
    #    over the rendered panel, where the markdown has already become
    #    <strong> and the asterisks are gone.
    #
    #    WHAT COUNTS AS "THE CHECKLIST LISTS THIS PARAMETER", and why it is
    #    not "any P_-shaped word anywhere". Measured over the 34-report
    #    agency corpus, the first wording of this rule reported 87 findings
    #    and every one of them was the reader's, not the checklist's:
    #
    #      * a parameter is claimed BARE -- "**P_SORT** - String" -- while
    #        the two things that are not claims carry their sigil. A quoted
    #        validator finding names a BIND (":P_ORG_ID ... the report has
    #        no matching parameter declaration") and the lexical step names
    #        a LEXICAL ("`&P_ORG_ID`"). Both are the checklist warning that
    #        a name is missing; counting them as "the checklist says this
    #        parameter exists" inverts their meaning and would push the app
    #        towards deleting its own warnings to look consistent.
    #      * Oracle reports declare parameters called Org_Id, PROG_ID,
    #        DESTINATION. A P_-only regex cannot see those names, so it
    #        called 52 of them unmentioned while they were on screen, one
    #        per bullet, in the parameter step. The question this check is
    #        for -- "is the file's own name on the checklist?" -- is asked
    #        with the file's own name.
    listed = set(re.findall(r"(?<![:@&A-Za-z0-9_])(P_[A-Za-z0-9_]+)\b", text))
    declared = set(f["params"])
    for ghost in sorted(listed - declared - set(f["prompt_names"])):
        out.append(("ghost_parameter",
                    "checklist lists parameter %r, which the file does not "
                    "declare" % ghost))
    for missing in sorted(declared):
        if not re.search(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])"
                         % re.escape(missing), text):
            out.append(("unlisted_parameter",
                        "the file declares parameter %r and the checklist "
                        "never mentions it" % missing))

    # 7. NUMBERING. The steps are followed in order, so they are numbered
    #    1..N with no gaps.
    numbers = [s.get("step") for s in steps]
    if numbers != list(range(1, len(steps) + 1)):
        out.append(("bad_numbering", "step numbers are %r" % numbers))
    return out


# ---- the proof the reader can fail ---------------------------------------
# The pre-fix checklist, word for word from the screen that was measured,
# beside the RDL that was actually being downloaded at the time. A reader
# that cannot report these is not measuring anything.
MEASURED_DEFECT_RDL = """<Report>
  <DataSources>
    <DataSource Name="SharedDataSource">
      <DataSourceReference>/Data Sources/StateOracle</DataSourceReference>
    </DataSource>
  </DataSources>
  <DataSets><DataSet Name="DS"><Query>
    <CommandText>SELECT TO_CHAR(P.Issued_On) FROM Permit P</CommandText>
    <QueryParameters><QueryParameter Name=":P_SORT"><Value>x</Value>
    </QueryParameter></QueryParameters>
  </Query></DataSet></DataSets>
  <ReportParameters><ReportParameter Name="P_SORT">
    <DataType>String</DataType></ReportParameter></ReportParameters>
</Report>"""

MEASURED_DEFECT_CHECKLIST = [
    {"step": 1, "status": "manual", "title": "Open the .rdl in SSRS Report Builder",
     "body_md": "Download the generated `.rdl` and open it."},
    {"step": 2, "status": "manual",
     "title": "Configure the DataSource against your report database",
     "body_md": (
         "The RDL ships with a placeholder shared `DataSource` named **AppDb**.\n\n"
         "1. In Report Builder open **Data Sources** in the Report Data pane.\n"
         "2. Right-click **AppDb** -> **Data Source Properties**.\n"
         "3. Change connection string to `Data Source=YOUR_SQL_SERVER;"
         "Initial Catalog=AppDb;Integrated Security=SSPI;`\n\n"
         "If you want to use a shared data source, pick `/Data Sources/AppDb` "
         "from the report server.")},
    {"step": 3, "status": "auto",
     "title": "Review T-SQL validation results (0 errors, 8 warnings)",
     "body_md": "The converter ran a static T-SQL validator against every "
                "generated dataset."},
    {"step": 4, "status": "auto",
     "title": "Verify @P_* parameter bindings (1 declared)",
     "body_md": "- **@P_SORT** (String)"},
]


def test_the_contradiction_reader_reports_the_defect_it_exists_for():
    """Mutation proof. Run the reader over the exact wording that was on
    screen, beside the file that was really being downloaded."""
    found = checklist_contradictions(MEASURED_DEFECT_CHECKLIST,
                                     MEASURED_DEFECT_RDL)
    codes = {c for c, _ in found}
    for expected in ("wrong_ds_name", "wrong_path", "wrong_dialect",
                     "invented_connection", "bind_syntax"):
        assert expected in codes, (
            "the reader missed the measured %s contradiction; it found %r"
            % (expected, sorted(codes)))
    assert "ref_never_named" in codes, (
        "the checklist never mentioned /Data Sources/StateOracle and the "
        "reader did not notice: %r" % sorted(codes))


def test_the_reader_still_catches_a_parameter_the_checklist_never_lists():
    """Mutation proof for the parameter rules, which were made PRECISE (a
    claim is a bare name; a bind or a lexical is a warning about a name).
    Precise must not mean toothless: drop a real parameter out of the
    listing and the reader has to notice it is gone."""
    rdl = MEASURED_DEFECT_RDL.replace(
        '<ReportParameter Name="P_SORT">',
        '<ReportParameter Name="P_SORT">'
        '</ReportParameter><ReportParameter Name="P_MISSING">'
        '<DataType>String</DataType>')
    steps = [{"step": 1, "status": "auto",
              "title": "Check the report's parameters (2 declared)",
              "body_md": "The data source in the file is called "
                         "**SharedDataSource** and it refers to "
                         "`/Data Sources/StateOracle`.\n\n* **P_SORT** - String"}]
    codes = {c for c, _ in checklist_contradictions(steps, rdl)}
    assert "unlisted_parameter" in codes, (
        "a parameter the file declares is missing from the checklist and "
        "the reader said nothing: %r" % sorted(codes))


def test_the_reader_still_catches_a_parameter_the_file_never_declares():
    """The other direction: a name presented AS a parameter that the file
    does not declare -- the shape of the original measured defect."""
    steps = [{"step": 1, "status": "auto",
              "title": "Check the report's parameters (2 declared)",
              "body_md": "The data source in the file is called "
                         "**SharedDataSource** and it refers to "
                         "`/Data Sources/StateOracle`.\n\n"
                         "* **P_SORT** - String\n* **P_INVENTED** - String"}]
    codes = {c for c, _ in checklist_contradictions(steps, MEASURED_DEFECT_RDL)}
    assert "ghost_parameter" in codes, (
        "the checklist listed a parameter the file does not declare and the "
        "reader waved it through: %r" % sorted(codes))


def test_reporting_a_missing_bind_is_not_read_as_claiming_it_exists():
    """...and the precision itself, stated as a test. The checklist quoting
    its validator ("Query references :P_ORG_ID but the report has no
    matching parameter declaration") and listing an unfinished Oracle
    lexical ("`&P_ORG_ID`") are WARNINGS that a name is missing. Read as
    claims that it exists, they would push the app towards deleting its own
    warnings to look consistent -- which is how 35 of the 93 contradictions
    measured over the agency corpus came about."""
    steps = [{"step": 1, "status": "caution",
              "title": "Read the Oracle SQL the converter wrote (1 error)",
              "body_md": "The data source in the file is called "
                         "**SharedDataSource** and it refers to "
                         "`/Data Sources/StateOracle`.\n\n"
                         "* _error_ `report.param_unbound` (Q_A @ L29): Query "
                         "references :P_ORG_ID but the report has no matching "
                         "parameter declaration; SSRS will fail to bind it."},
             {"step": 2, "status": "caution",
              "title": "Finish the Oracle lexical references still in the "
                       "file (1)",
              "body_md": "* `&P_SITE_ID`"},
             {"step": 3, "status": "auto",
              "title": "Check the report's parameters (1 declared)",
              "body_md": "* **P_SORT** - String"}]
    found = checklist_contradictions(steps, MEASURED_DEFECT_RDL)
    assert not found, (
        "the checklist was punished for reporting the problem it exists to "
        "report: %r" % found)


def test_the_contradiction_reader_is_quiet_when_there_is_nothing_to_report():
    """A reader that always fires is as useless as one that never does."""
    clean = [
        {"step": 1, "status": "manual", "title": "Open the .rdl",
         "body_md": "Open it in Report Builder."},
        {"step": 2, "status": "auto",
         "title": "Data source - already pointed at /Data Sources/StateOracle",
         "body_md": "The data source in the file is called "
                    "**SharedDataSource** and it refers to "
                    "`/Data Sources/StateOracle`."},
        {"step": 3, "status": "auto", "title": "Check the parameters",
         "body_md": "* **P_SORT** - String"},
    ]
    assert checklist_contradictions(clean, MEASURED_DEFECT_RDL) == []


# ================================================== layer 1: the real server
@pytest.fixture(scope="module")
def client():
    from app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _convert(client, xml_bytes, **settings):
    data = {"file": (__import__("io").BytesIO(xml_bytes), "REPORT.xml")}
    data.update(settings)
    r = client.post("/api/convert", data=data,
                    content_type="multipart/form-data")
    assert r.status_code == 200, r.status_code
    return r.get_json()


@pytest.mark.parametrize("target_db,ds_path", [
    ("oracle", "/Data Sources/StateOracle"),
    ("sqlserver", "/Data Sources/StateSql"),
    ("oracle", ""),
    ("sqlserver", ""),
])
def test_every_checklist_step_matches_the_rdl_that_was_emitted(
        client, target_db, ds_path):
    """The item under test: set the target database and a shared data source
    path, convert, and hold every step against the file that came back."""
    payload = _convert(client, SAMPLE.read_bytes(),
                       target_db=target_db, shared_ds_path=ds_path)
    steps = payload["deployment_checklist"]
    rdl = payload["rdl_xml"]
    assert steps, "no checklist came back"
    problems = checklist_contradictions(steps, rdl)
    assert not problems, (
        "the checklist describes a different file than the one downloaded "
        "(target_db=%s, shared data source=%r):\n  %s"
        % (target_db, ds_path,
           "\n  ".join("%s: %s" % p for p in problems)))


def test_the_checklist_states_the_operators_own_settings(client):
    """Not merely 'no contradiction': the two settings the operator chose
    have to be ON the checklist, in the steps that depend on them."""
    payload = _convert(client, SAMPLE.read_bytes(),
                       target_db="oracle",
                       shared_ds_path="/Data Sources/StateOracle")
    text = "\n".join(s["title"] + "\n" + s["body_md"]
                     for s in payload["deployment_checklist"])
    assert "/Data Sources/StateOracle" in text, (
        "the operator typed a shared data source path, the RDL was written "
        "with it, and the checklist never says so")
    assert "Oracle SQL" in text, (
        "the operator chose Oracle and the checklist never says which SQL "
        "the file carries")
    assert "T-SQL" not in text
    assert "AppDb" not in text, "the invented data source name is back"


def test_the_checklist_switches_with_the_target_database(client):
    """Same report, other setting: the claims move with it."""
    ora = _convert(client, SAMPLE.read_bytes(), target_db="oracle")
    sql = _convert(client, SAMPLE.read_bytes(), target_db="sqlserver")
    ora_text = "\n".join(s["title"] + s["body_md"]
                         for s in ora["deployment_checklist"])
    sql_text = "\n".join(s["title"] + s["body_md"]
                         for s in sql["deployment_checklist"])
    assert "Oracle SQL" in ora_text and "T-SQL" not in ora_text
    assert "T-SQL" in sql_text and "Oracle SQL" not in sql_text
    assert ":P_" in ora_text and "@P_" not in ora_text
    assert "@P_" in sql_text and ":P_" not in sql_text


def test_a_checklist_step_that_never_arrives_is_supplied(client):
    """The data source and the parameters are the two steps an operator
    cannot deploy without. If the generator ever stops emitting one, the
    reconciler has to notice and write it, not silently ship eight steps."""
    from app import _artifact_facts, _reconcile_checklist
    payload = _convert(client, SAMPLE.read_bytes(), target_db="oracle",
                       shared_ds_path="/Data Sources/StateOracle")
    stripped = dict(payload)
    stripped["deployment_checklist"] = [
        s for s in payload["deployment_checklist"]
        if "data source" not in s["title"].lower()
        and "parameter" not in s["title"].lower()]
    _reconcile_checklist(stripped)
    titles = "\n".join(s["title"] for s in stripped["deployment_checklist"])
    assert "/Data Sources/StateOracle" in titles, titles
    assert "parameters" in titles.lower(), titles
    assert not checklist_contradictions(stripped["deployment_checklist"],
                                        stripped["rdl_xml"])
    # And the facts really did come out of the file, not out of the request.
    facts = _artifact_facts(payload["rdl_xml"], "sqlserver")
    assert facts["bind"] == ":", (
        "the reconciler believed a target_db that contradicts the file's own "
        "bind variables: %r" % facts["bind"])


def test_an_embedded_connection_is_described_but_never_reprinted(client):
    """The third shape the data source can take. The checklist has to say the
    file carries its own connection -- and must not put the connection string
    back on screen: that is the one deployment setting that is a secret."""
    secret = "Data Source=ORCLPDB1;User Id=RPT;Password=hunter2;"
    payload = _convert(client, SAMPLE.read_bytes(), target_db="oracle",
                       connection_string=secret)
    rdl = payload["rdl_xml"]
    assert secret in rdl, "the connection never reached the file"
    steps = payload["deployment_checklist"]
    assert not checklist_contradictions(steps, rdl)
    text = "\n".join(s["title"] + "\n" + s["body_md"] for s in steps)
    assert "embedded connection" in text.lower(), (
        "the checklist does not say the file carries its own connection")
    assert "ORACLE" in text, (
        "the checklist does not name the provider the file declares")
    assert secret not in text and "hunter2" not in text, (
        "the checklist reprinted the connection string")


def test_the_reconciler_survives_a_report_it_cannot_read(client):
    """A truncated export still reaches this code. It may not 500."""
    from app import _reconcile_checklist
    payload = _convert(client, SAMPLE.read_bytes()[:300], target_db="oracle")
    assert isinstance(payload.get("deployment_checklist"), list)
    _reconcile_checklist(payload)          # twice is idempotent, and safe
    assert not checklist_contradictions(payload["deployment_checklist"],
                                        payload["rdl_xml"])


@pytest.mark.parametrize("target_db", ["oracle", "sqlserver"])
def test_reconciling_twice_changes_nothing(client, target_db):
    """Some payloads pass through here more than once -- the bundle download
    reconciles a payload convert() already reconciled.

    THE BUG THIS CAUGHT, measured before it shipped: the second pass did not
    recognise its own rewritten parameter step, concluded the step was
    missing, and appended it again -- so the downloaded zip's checklist.md
    listed the parameters twice, as steps 6 and 10."""
    from app import _reconcile_checklist
    import json
    payload = _convert(client, SAMPLE.read_bytes(), target_db=target_db,
                       shared_ds_path="/Data Sources/StateOracle")
    once = json.dumps(payload["deployment_checklist"], sort_keys=True)
    _reconcile_checklist(payload)
    twice = json.dumps(payload["deployment_checklist"], sort_keys=True)
    assert once == twice, (
        "a second pass rewrote the checklist: %d steps became %d"
        % (len(json.loads(once)), len(json.loads(twice))))
    titles = [s["title"] for s in payload["deployment_checklist"]]
    assert len(titles) == len(set(titles)), "a step is listed twice: %r" % titles


def test_only_the_steps_that_describe_the_artifact_are_rewritten():
    """The reconciler owns the five steps that make claims about the file.
    The steps that describe what a HUMAN does -- open it, upload it,
    subscribe to it -- are the converter's to write, and must come through
    untouched, word for word."""
    from converter import convert as raw_convert
    from app import _checklist_subject, _reconcile_checklist

    payload = raw_convert(SAMPLE.read_bytes(), target_db="oracle")
    before = [dict(s) for s in payload["deployment_checklist"]]
    _reconcile_checklist(payload)
    after = payload["deployment_checklist"]

    untouched = [s for s in before if not _checklist_subject(s)]
    assert untouched, (
        "every step was claimed by the reconciler; it is supposed to leave "
        "the human-procedure steps alone")
    titles_after = {s["title"]: s["body_md"] for s in after}
    for step in untouched:
        assert step["title"] in titles_after, (
            "a step the reconciler does not own disappeared: %r"
            % step["title"])
        assert titles_after[step["title"]] == step["body_md"], (
            "a step the reconciler does not own was rewritten: %r"
            % step["title"])
    assert len(after) == len(before), (
        "the reconciler changed how many steps there are: %d -> %d"
        % (len(before), len(after)))


def test_the_downloaded_bundle_describes_the_rdl_inside_it(client):
    """The strongest form of the claim: the zip an operator unpacks contains
    a .rdl and a checklist.md, and the checklist has to be about THAT .rdl."""
    import io as _io
    import zipfile
    _convert(client, SAMPLE.read_bytes(), target_db="oracle",
             shared_ds_path="/Data Sources/StateOracle")
    r = client.get("/api/download/bundle")
    assert r.status_code == 200, r.status_code
    z = zipfile.ZipFile(_io.BytesIO(r.get_data()))
    rdl_name = [n for n in z.namelist() if n.endswith(".rdl")][0]
    rdl = z.read(rdl_name).decode("utf-8")
    md = z.read("checklist.md").decode("utf-8")
    # checklist.md is one document, so read it as a single step for the
    # contradiction reader: every claim in it is still checkable.
    problems = [p for p in checklist_contradictions(
        [{"step": 1, "title": "", "body_md": md}], rdl)
        if p[0] != "bad_numbering"]
    assert not problems, (
        "the checklist inside the bundle describes something other than the "
        ".rdl beside it:\n  %s" % "\n  ".join("%s: %s" % p for p in problems))
    assert md.count("Check the report's parameters") <= 1, (
        "the parameter step is in the bundle twice")


# ================================================== layer 2: the served page
def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def live_url():
    pytest.importorskip("playwright.sync_api")
    from werkzeug.serving import make_server
    from app import app

    port = _free_port()
    srv = make_server("127.0.0.1", port, app, threaded=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield "http://127.0.0.1:%d/" % port
    finally:
        srv.shutdown()
        t.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    sp = pytest.importorskip("playwright.sync_api")
    with sp.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:                       # noqa: BLE001
            pytest.skip("chromium unavailable: %s" % exc)
        yield b
        b.close()


@pytest.fixture()
def page(browser, live_url):
    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    # The target machine is locked down: nothing but the app itself loads.
    pg.route("**/*", lambda route: route.continue_()
             if "127.0.0.1" in route.request.url else route.abort())
    pg.goto(live_url, wait_until="domcontentloaded")
    pg.wait_for_function("() => !!document.querySelector('.tab')")
    pg.wait_for_timeout(200)
    yield pg
    pg.close()


# What a reader can still SEE at the top of the screen: the corner pill and
# the status card, as words.
HEADLINE = """() => {
  const pill = document.getElementById('status-pill');
  const card = document.querySelector('#activity-now .activity-card')
            || document.querySelector('#activity-alert .activity-card');
  return {
    pill: pill ? pill.innerText.trim() : '',
    pillLabel: pill ? (pill.getAttribute('aria-label') || '') : '',
    headline: card ? (card.querySelector('.activity-headline') || {}).innerText || '' : '',
    card: card ? card.innerText : ''
  };
}"""


def _drop(page, tmp_path, name, blob):
    f = tmp_path / name
    f.write_bytes(blob)
    page.set_input_files("#file-input-files", str(f))
    page.wait_for_function(
        "() => (document.getElementById('activity-now').innerText||'')"
        ".indexOf('came across') !== -1", timeout=60000)
    page.wait_for_timeout(200)
    return page.evaluate(HEADLINE)


def test_a_truncated_export_says_so_where_the_success_would_have_been(
        page, tmp_path):
    """(a), end to end on the real page: drop an export that stops in the
    middle and read the top of the screen."""
    seen = _drop(page, tmp_path, "TRUNCATED.xml", SAMPLE.read_bytes()[:300])
    head = seen["headline"]
    assert "0 queries, 0 parameters and 0 formulas came across" in seen["card"], (
        "the counts under test are not on the card: %r" % seen["card"][:300])
    assert "came across" in head.lower() and "nothing" in head.lower(), (
        "the headline does not name the emptiness; it says %r" % head)
    assert not head.lower().startswith("converted"), (
        "the headline still announces a conversion that produced nothing: %r"
        % head)
    # The corner pill is a projection of that same card -- it may not go on
    # saying something friendlier than the card underneath it.
    assert "Converted" not in seen["pill"], (
        "the pill still reads %r beside a card that says %r"
        % (seen["pill"], head))
    assert "Nothing came across" in seen["pillLabel"], seen["pillLabel"]
    # And it says WHY, checkably: this file really does stop mid-way.
    assert "</report>" in seen["card"], (
        "nothing on screen explains that the export itself is cut short: %r"
        % seen["card"][:400])


def test_the_headline_is_driven_by_the_counts_and_nothing_else(page, tmp_path):
    """Mutation proof for (a). Neuter the one function that decides the
    shortfall, replay the SAME payload, and watch the old overclaim come
    straight back -- so the test above is measuring that rule and not some
    incidental difference between the two files."""
    _drop(page, tmp_path, "TRUNCATED.xml", SAMPLE.read_bytes()[:300])
    broken = page.evaluate("""() => {
      window.extractionShortfall = () => '';        // the pre-fix behaviour
      reportConversionStatus(state.data);
      const card = document.querySelector('#activity-now .activity-card');
      return card.querySelector('.activity-headline').innerText;
    }""")
    assert broken.lower().startswith("converted"), (
        "with the shortfall rule disabled the headline should be the old "
        "overclaim; it said %r, so the test above proves nothing" % broken)


def test_a_missing_measurement_is_not_read_as_a_missing_report(page):
    """The column and layout-field counts come from the fidelity report,
    which convert() builds inside a try/except and a folder drop may not
    build at all. A count that was never taken is not a count of zero: a
    healthy conversion must not be called empty because some unrelated
    measurement crashed."""
    verdicts = page.evaluate("""() => {
      const healthy = {
        report: {name: 'R', queries: [{}], parameters: [{}], formulas: []},
        fidelity_report: {categories: {columns: {total: 12},
                                       layout_fields: {total: 9}}}};
      const crashed = {report: healthy.report,
                       fidelity_report: {error: 'boom', categories: {}}};
      const absent = {report: healthy.report};
      const reallyEmpty = {report: {name: 'R', queries: [], parameters: [],
                                    formulas: []},
                           fidelity_report: {categories: {}}};
      return {healthy: extractionShortfall(healthy),
              crashed: extractionShortfall(crashed),
              absent: extractionShortfall(absent),
              reallyEmpty: extractionShortfall(reallyEmpty)};
    }""")
    assert verdicts["healthy"] == "", verdicts
    assert verdicts["crashed"] == "", (
        "a crashed fidelity measurement was read as an empty report: %r"
        % verdicts)
    assert verdicts["absent"] == "", (
        "a payload with no fidelity report at all was read as an empty "
        "report: %r" % verdicts)
    assert verdicts["reallyEmpty"] == "empty", verdicts


def test_a_real_report_is_still_called_converted(page):
    """The rule may not cry wolf: a report that really converted keeps the
    plain success headline."""
    if page.locator(".sample-chip").count() == 0:
        pytest.skip("no bundled sample to convert")
    page.click(".sample-chip")
    page.wait_for_function(
        "() => document.querySelectorAll('#mockup-host *').length > 0",
        timeout=60000)
    page.wait_for_timeout(500)
    seen = page.evaluate(HEADLINE)
    assert seen["headline"].startswith("Converted"), seen["headline"]
    assert "nothing" not in seen["headline"].lower()


def test_the_checklist_on_screen_matches_the_file_the_page_serves(page):
    """(b), end to end: choose the target database and type a shared data
    source path into the real sidebar controls, convert, then hold the
    checklist THAT IS ON SCREEN against the .rdl the download endpoint
    serves."""
    if page.locator(".sample-chip").count() == 0:
        pytest.skip("no bundled sample to convert")
    page.evaluate("""() => {
      const td = document.getElementById('target-db');
      td.value = 'oracle'; td.dispatchEvent(new Event('change'));
      const dsp = document.getElementById('shared-ds-path');
      dsp.value = '/Data Sources/StateOracle';
      dsp.dispatchEvent(new Event('change'));
    }""")
    page.click(".sample-chip")
    page.wait_for_function(
        "() => document.querySelectorAll('#deploy-host .deploy-step').length > 0",
        timeout=60000)
    page.wait_for_timeout(300)
    steps = page.evaluate("""() => Array.from(
      document.querySelectorAll('#deploy-host .deploy-step')).map((s, i) => ({
        step: i + 1,
        title: s.querySelector('.deploy-title').textContent,
        body_md: s.querySelector('.deploy-body').textContent}))""")
    rdl = page.evaluate(
        "async () => (await fetch('/api/download/rdl')).text()")
    assert steps and rdl.strip().startswith("<"), (len(steps), rdl[:80])
    problems = checklist_contradictions(steps, rdl)
    assert not problems, (
        "the checklist on screen describes a file other than the one this "
        "page will hand the operator:\n  %s"
        % "\n  ".join("%s: %s" % p for p in problems))
    onscreen = "\n".join(s["title"] + s["body_md"] for s in steps)
    assert "/Data Sources/StateOracle" in onscreen

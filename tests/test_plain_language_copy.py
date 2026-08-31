"""Plain-language copy: the app must teach, not jargon at, its reader.

The person who runs this tool every day is a competent professional who is
NOT an SSRS expert, and who is deaf -- so every explanation has to work as
ON-SCREEN TEXT, first time, with nobody to ask.

Three things are checked here, and each one broke in the real app before
these tests existed:

  1. THE TEACHING SURFACES DESCRIBE THE UI THAT IS ACTUALLY ON SCREEN.
     The How-it-works modal and the guided tour both taught views named
     "HTML Mockup" and "Deployment" while the tabs on screen said
     "Preview" and "Deploy Checklist", and the tour told the reader the
     fidelity score reads "1.00" while the sidebar rendered "73%".

  2. THE LOAD-BEARING FACTS SURVIVE ANY REWRITE. Three claims in this app
     are load-bearing and must never drift:
       * never click Refresh Fields (and WHY: refreshing makes Report
         Builder run the query, which is what makes it prompt);
       * Live Data runs against a bundled read-only SAMPLE database, not
         the user's own;
       * the four verdict words mean exactly what the validator means by
         them, and the fidelity figure is not a claim about looks.

  3. THE JARGON THAT WAS TRANSLATED STAYS TRANSLATED. "DTD",
     "Ingest Summary", "artifacts", "Target database backend" and
     "sibling name" were on screen with no explanation anywhere.

Layer 1 reads the markup the SERVER SENDS and the JS the browser runs.
Layer 2 drives the real page in a real browser: the tour's step counter
only exists once the tour has run, and the counter bug (1, 3, 4, 5 ...)
was invisible in source.
"""
from __future__ import annotations

import re
import socket
import sys
import threading
from pathlib import Path

import pytest
from lxml import html as lxml_html

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

APP_JS = ROOT / "frontend" / "static" / "js" / "app.js"
DEMO_JS = ROOT / "frontend" / "static" / "js" / "demo_mode.js"


# --------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def served_html() -> str:
    from app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        r = c.get("/")
        assert r.status_code == 200
        return r.get_data(as_text=True)


@pytest.fixture(scope="module")
def dom(served_html):
    return lxml_html.fromstring(served_html)


@pytest.fixture(scope="module")
def app_js() -> str:
    return APP_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def demo_js() -> str:
    return DEMO_JS.read_text(encoding="utf-8")


def visible_text(dom) -> str:
    """Text a reader actually sees: no <script>, no <style>, no comments.

    Reading the raw HTML instead would pass on jargon that only survives
    inside a developer comment, and fail on jargon that is only in one.
    """
    copy = lxml_html.fromstring(lxml_html.tostring(dom))
    for node in copy.xpath("//script | //style | //comment()"):
        node.getparent().remove(node)
    return " ".join(" ".join(copy.itertext()).split())


_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"^\s*//.*$", re.M)


def js_copy(js: str) -> str:
    """The JS with its comments removed.

    The comments in this codebase quote the wording they replaced ("used to
    say 'HTML Mockup'"), which is exactly what these tests hunt for. Reading
    the raw file would make every one of them fail on its own explanation.
    """
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", js))


def tab_labels(dom) -> list[str]:
    out = []
    for btn in dom.xpath("//div[@class='tablist']//button[@role='tab']"):
        # the label is the button's own text, minus the dot and the badge
        txt = " ".join(("".join(btn.itertext())).split())
        out.append(txt)
    return out


# ============================================ 1. the UI it describes exists
def test_howto_table_names_only_views_that_exist(dom, app_js):
    """Every view named in the How-it-works table is a real tab, and every
    real tab is in the table. The table used to teach "HTML Mockup" and
    "Deployment"; the tabs say "Preview" and "Deploy Checklist"."""
    labels = set(tab_labels(dom))
    assert labels, "no tabs found in the served markup"
    rows = re.findall(r"<tr><th scope=\"row\"><b>([^<]+)</b></th>", app_js)
    # the modal has two other tables (verdicts, glossary); keep the block
    # between the "What each view is for" heading and the end of its table
    start = app_js.index("What each view is for")
    end = app_js.index("</tbody></table>", start)
    rows = re.findall(r"<tr><th scope=\"row\"><b>([^<]+)</b></th>",
                      app_js[start:end])
    assert rows, "the 'What each view is for' table has no rows"
    unknown = [r for r in rows if r not in labels]
    assert not unknown, (
        "the How-it-works table names views that are not on screen: %r "
        "(the tabs are %r)" % (unknown, sorted(labels)))
    missing = [l for l in labels if l not in rows]
    assert not missing, (
        "these tabs are on screen but the How-it-works table never "
        "explains them: %r" % missing)


def test_tour_step_titles_use_the_real_view_names(demo_js, dom):
    """Each tour step that opens a tab must name that tab the way the tab
    names itself, or the reader is hunting for a view that does not exist."""
    labels = {}
    for btn in dom.xpath("//div[@class='tablist']//button[@role='tab']"):
        labels[btn.get("data-tab")] = " ".join(
            ("".join(btn.itertext())).split())
    # await next(clickTab("side"), "Side-by-Side — check the translation", ...
    pairs = re.findall(r'next\(clickTab\("(\w+)"\),\s*"([^"]+)"', demo_js)
    assert len(pairs) >= 8, "the tour stopped touring the views: %r" % pairs
    for tab, title in pairs:
        assert tab in labels, "the tour opens a tab that does not exist: %r" % tab
        assert title.startswith(labels[tab]), (
            "tour step for the %r tab is titled %r, but the tab on screen "
            "is labelled %r" % (tab, title, labels[tab]))


def test_teaching_copy_states_fidelity_the_way_the_ui_shows_it(demo_js, app_js):
    """The sidebar renders fidelity as a percentage. The tour used to tell
    the reader to look for "1.00"."""
    assert "1.00" not in js_copy(demo_js), (
        "the tour still describes the fidelity score as 1.00; the sidebar "
        "shows a percentage")
    assert "percentage" in demo_js
    # and the app itself never presents the figure as a claim about looks
    assert "never sees a picture of your original" in app_js


def test_dead_view_names_are_gone(demo_js, app_js):
    for js, name in ((js_copy(demo_js), "demo_mode.js"),
                     (js_copy(app_js), "app.js")):
        assert "HTML Mockup" not in js, (
            "%s still teaches a view called 'HTML Mockup'; the tab is "
            "'Preview'" % name)
        assert "<b>Deployment</b>" not in js, (
            "%s still teaches a view called 'Deployment'; the tab is "
            "'Deploy Checklist'" % name)


# ================================================ 2. load-bearing facts
REFRESH_REASON = "ask you for every parameter"


def test_refresh_fields_guidance_is_intact_and_explains_itself(app_js, demo_js, dom):
    """#1 rule of this project: the user must never hit Refresh Fields. The
    instruction has to survive every rewrite, and it has to say WHY -- an
    unexplained prohibition is the first thing a competent person ignores."""
    surfaces = {"app.js": js_copy(app_js), "demo_mode.js": js_copy(demo_js),
                "served page": visible_text(dom)}
    for name, text in surfaces.items():
        if "Refresh Fields" not in text:
            continue
        for m in re.finditer(r"Refresh Fields", text):
            window = text[max(0, m.start() - 220): m.end() + 40]
            assert re.search(r"(do not|don't|never|skip|why you)", window, re.I), (
                "%s mentions Refresh Fields without telling the reader not "
                "to use it: ...%s..." % (name, window))
    # the reason is on screen at least once in each teaching surface
    assert REFRESH_REASON in app_js, "the modal never says WHY not to refresh"
    assert REFRESH_REASON in demo_js, "the tour never says WHY not to refresh"


def test_live_data_copy_never_claims_to_touch_the_users_database(app_js, demo_js):
    """/api/run-query executes against the bundled read-only sample DB with
    missing tables stubbed empty. Every place that describes it must say so."""
    for name, js in (("app.js", app_js), ("demo_mode.js", demo_js)):
        assert "sample database" in js, "%s dropped the sample-DB fact" % name
        assert "baked into the downloaded RDL" in js, (
            "%s dropped what the connection string is actually for" % name)
        assert "real queries against your database" not in js
        assert "REAL queries against your database" not in js
    # and the Live Data view itself says it on screen, not only in the modal
    assert "It does <b>not</b> touch your database" in app_js, (
        "the Live Data view no longer says on screen that it leaves the "
        "user's database alone")


VERDICTS = ("BLOCKER", "RED", "AMBER", "READY")


def test_all_four_verdict_words_are_defined_where_the_reader_meets_them(
        app_js, dom):
    """The verdict legend used to exist ONLY inside the How-it-works modal,
    so the word on the banner was never explained on the page that shows it."""
    start = app_js.index("Reading the verdict")
    end = app_js.index("</tbody></table>", start)
    legend = app_js[start:end]
    for word in VERDICTS:
        assert word in legend, (
            "the How-it-works verdict table never defines %r" % word)
    panel = visible_text(dom)
    idx = panel.index("Everything this tool checked")
    intro = panel[idx: idx + 900]
    for word in VERDICTS:
        assert word in intro, (
            "the Validation view itself never defines %r -- the reader has "
            "to open a modal to learn what the word on their screen means"
            % word)
    # the meanings, not just the words
    assert "will not upload or open cleanly" in intro
    assert "something is likely to go wrong once the report runs" in intro


def test_the_tool_still_says_what_it_cannot_do(app_js):
    """This app's honesty is a feature. The modal must keep a section that
    says plainly where a human is needed."""
    assert "What it will not do" in app_js
    for claim in ("It does not connect to your database",
                  "It does not guess",
                  "It does not deploy for you"):
        assert claim in app_js, "the modal dropped the honest claim %r" % claim
    assert "names it in <b>Validation</b>" in app_js, (
        "the modal no longer says WHERE an untranslatable thing is named")


def test_lexicals_are_explained_not_just_named(app_js):
    """"Lexical" is Oracle's word for a named piece of SQL text spliced into
    a query at run time. It appears in findings the reader cannot avoid, so
    the modal has to define it."""
    assert "<b>Lexical</b>" in app_js, "the glossary lost the lexical entry"
    start = app_js.index("<b>Lexical</b>")
    entry = app_js[start:start + 400]
    assert "SQL text" in entry and "while it runs" in entry, (
        "the lexical entry no longer explains what a lexical IS: %r" % entry)
    assert "real SSRS parameter" in entry, (
        "the lexical entry no longer says what this tool does with them")


def test_glossary_defines_every_term_the_ui_leans_on(app_js):
    start = app_js.index("Words used in this tool")
    end = app_js.index("</tbody></table>", start)
    glossary = app_js[start:end]
    for term in ("SSRS", "RDL", "Report Builder", "Refresh Fields",
                 "Data source", "Parameter", "Lexical", "Sub-report",
                 "Bursting", "Fidelity", "T-SQL", "Burst pack"):
        assert ("<b>%s</b>" % term) in glossary, (
            "the glossary no longer defines %r" % term)


# ================================================ 3. translated jargon stays
# Each of these was on the page with no explanation anywhere near it.
RETIRED_JARGON = [
    ("DTD", "the Oracle export version row"),
    ("Ingest Summary", "the heading over the dropped-file list"),
    ("T-SQL Validation", "the Validation panel title"),
    ("Target database backend", "the database picker's label"),
    ("sibling name", "the shared data source help text"),
    ("artifacts", "the word for 'the files you dropped'"),
    ("Refresh dance", "the shared data source help text"),
]


def test_retired_jargon_does_not_come_back(dom):
    text = visible_text(dom)
    for term, where in RETIRED_JARGON:
        assert term not in text, (
            "%r is back in the visible page text (%s); it was replaced "
            "because nothing on screen explains it" % (term, where))


def test_placeholders_are_never_the_only_explanation(dom):
    """A placeholder disappears the moment someone types. Every text input
    that carries one must also have a real label."""
    for inp in dom.xpath("//input[@placeholder] | //textarea[@placeholder]"):
        _id = inp.get("id")
        named = bool(inp.get("aria-label"))
        if _id:
            named = named or bool(dom.xpath("//label[@for=$i]", i=_id))
        named = named or bool(inp.xpath("ancestor::label"))
        assert named, (
            "input %r explains itself only through its placeholder %r"
            % (_id or inp.get("class"), inp.get("placeholder")))


def test_deploy_checklist_status_words_say_who_does_the_work(app_js, dom):
    """The chips used to print the backend's own status keys -- "auto",
    "todo", "manual", "caution" -- which say nothing about who acts."""
    for key, word in (("auto", "Done for you"), ("todo", "Needs finishing"),
                      ("manual", "You do this"), ("caution", "Take care")):
        assert ('%s: "%s"' % (key, word)) in app_js, (
            "the deploy checklist no longer translates the %r status" % key)
    intro = visible_text(dom)
    idx = intro.index("The steps for putting this report on your")
    blurb = intro[idx: idx + 400]
    for word in ("Done for you", "You do this", "Needs finishing", "Take care"):
        assert word in blurb, (
            "the Deploy Checklist intro never explains the %r chip" % word)


def test_every_view_says_what_it_is_for_on_screen(dom):
    """Not only in the modal: someone who lands on a tab must be able to
    read what it is for without leaving it."""
    for panel_id in ("tab-rdl", "tab-side", "tab-validate", "tab-deploy",
                     "tab-extras"):
        node = dom.xpath("//div[@id=$i]", i=panel_id)
        assert node, "panel %r is gone" % panel_id
        intro = node[0].xpath(".//p[contains(@class,'panel-intro')]")
        assert intro, "panel %r has no plain-language introduction" % panel_id
        words = len(" ".join(intro[0].itertext()).split())
        assert words >= 15, (
            "panel %r's introduction is %d words -- too short to teach "
            "anything" % (panel_id, words))


# ============================================================ layer 2: live
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
        except Exception as exc:
            pytest.skip("chromium unavailable: %s" % exc)
        yield b
        b.close()


@pytest.fixture()
def page(browser, live_url):
    pg = browser.new_page(viewport={"width": 1280, "height": 900})

    def _route(route):
        url = route.request.url
        if "127.0.0.1" not in url:
            return route.abort()          # the machine may be offline
        # The Preview tab kicks off a real render through Microsoft's engine
        # the moment it opens, and the tour opens it. That is somebody
        # else's test: refusing it here keeps these copy checks fast and
        # keeps them passing on a machine with no engine at all. The app's
        # own fallback notice takes over, which is the path a locked-down
        # machine sees anyway.
        if "/api/render-preview" in url:
            return route.abort()
        return route.continue_()

    pg.route("**/*", _route)
    pg.goto(live_url, wait_until="domcontentloaded")
    pg.wait_for_function("() => !!document.querySelector('.tab')")
    yield pg
    pg.close()


def _walk_tour(pg, limit=20):
    """Run the tour to the end, returning each step's counter text."""
    seen = []
    # The braces matter: runTour() is async, and evaluate() AWAITS whatever
    # the expression returns. Handing back its promise deadlocks the test --
    # the tour only finishes once somebody clicks through it.
    pg.evaluate("() => { window.o2sRunTour(); }")
    for _ in range(limit):
        try:
            pg.wait_for_selector(".demo-tooltip", timeout=15000)
        except Exception:
            break
        seen.append(pg.eval_on_selector(".demo-tooltip .demo-step-n",
                                        "n => n.textContent.trim()"))
        pg.click(".demo-tooltip .demo-next")
        pg.wait_for_timeout(250)
        if not pg.query_selector(".demo-tooltip"):
            break
    return seen


def test_tour_counter_never_skips_a_number(page):
    """Measured bug: the tour hardcoded 13 and silently consumed step 2 when
    a conversion already existed, so the reader watched it count 1, 3, 4."""
    seen = _walk_tour(page)
    assert seen, "the tour showed no steps at all"
    nums = [int(s.split("/")[0]) for s in seen]
    totals = {int(s.split("/")[1]) for s in seen}
    assert nums == list(range(1, len(nums) + 1)), (
        "the tour skipped a step number: %r" % seen)
    assert len(totals) == 1, "the tour changed its own total mid-run: %r" % seen
    assert totals.pop() == len(nums), (
        "the tour promised %r steps and showed %d" % (seen[-1], len(nums)))


def test_tour_counter_is_still_honest_after_a_conversion(page):
    """With a report already converted the tour skips its sample step. The
    count has to shrink with it."""
    chip = page.query_selector("#samples-list .sample-chip")
    if not chip:
        pytest.skip("this build ships no sample reports")
    chip.click()
    page.wait_for_function(
        "() => document.getElementById('summary-section')"
        " && !document.getElementById('summary-section').hidden",
        timeout=30000)
    seen = _walk_tour(page)
    nums = [int(s.split("/")[0]) for s in seen]
    assert nums == list(range(1, len(nums) + 1)), (
        "the tour skipped a step number after a conversion: %r" % seen)
    assert int(seen[0].split("/")[1]) == len(nums)


def test_tour_ends_in_a_message_that_stays_on_screen(page):
    """It used to end in a three-second toast: someone who looked away never
    learned that it had finished, or that their views had been put back."""
    _walk_tour(page)
    page.wait_for_timeout(400)
    now = page.eval_on_selector("#activity-now", "n => n.innerText")
    assert "Guided tour finished" in now, (
        "the end of the tour left nothing on screen: %r" % now)
    assert "Preview" in now, (
        "the end-of-tour message never says where the reader has been "
        "returned to: %r" % now)


def test_howto_modal_reads_as_plain_prose(page):
    """No sentence in the modal may run past 40 words: the reader has no
    audio channel and no author to ask."""
    page.click("#howto-open")
    page.wait_for_selector("#howto-body h2")
    text = page.eval_on_selector("#howto-body", "n => n.innerText")
    assert "SQL Server Reporting Services" in text, (
        "SSRS is used without ever being spelled out")
    # A line break ends a thought as surely as a full stop does: the flow
    # diagram's steps are separate lines with no punctuation at all, and
    # gluing them together would measure a "sentence" nobody ever reads.
    long = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        words = [w for w in sentence.split() if w]
        if len(words) > 40:
            long.append(" ".join(words[:12]) + " ...")
    assert not long, "sentences over 40 words in the modal: %r" % long

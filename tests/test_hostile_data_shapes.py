"""
HOSTILE DATA-SHAPE RAIL — every convertible report at 0 / 1 / 3 / 25 rows.

Everything in this repo was proven at ONE sample size: three synthetic rows.
Three rows is the shape that HIDES bugs. It is enough rows to fill a band and
not enough to paginate, and it is never zero — so the two shapes that break
real reports were never rendered:

  0 rows   the most common production shape (a filter that matches nothing).
           Historically printed a completely BLANK sheet. An empty data
           region is legitimate; an empty PAGE is the defect.
  1 row    orphan shapes: a band that spills its header onto a page of its own.
  25 rows  pagination, band repetition and page-break logic that three rows
           never reach.

Per source and shape this rail measures the REAL MS engine render and asserts:

  L-1 RENDER      conversion and render both succeed at every shape (a source
                  that converts must render at every data shape, not just the
                  comfortable one).
  L-2 REFERENCE   the rows=3 render — the shape everything else is compared
                  against — prints no strict-blank sheet ITSELF.
  L-2b ZERO ROWS  the report still prints its declared chrome, and emits no
                  MORE blank sheets than the reference does.
  L-3 ONE ROW     no page inflation — one row can never need MORE sheets than
                  three (that is an orphaned band or header on a sheet of its
                  own) — and no blank sheet beyond the reference's.
  L-4 25 ROWS     pagination is monotonic (never FEWER sheets than three rows),
                  no blank sheet beyond the reference's, and every band the RDL
                  DECLARES as repeating actually reprints on the continuation
                  page.
  L-5 PAINT       the paint gate (render_overlap: buried words, strokes through
                  glyph ink) must not go from clean to dirty because of the row
                  count — a shape may not INTRODUCE painted-over text.
  L-6 SPARSE      no sheet is technically-not-blank and effectively EMPTY.
                  The blank column asks "is there ink?", and a sheet carrying
                  one stray word answers yes: six sheets of a production
                  letter were mailed-quality-broken inside a matrix this rail
                  called clean, because the letter's closing word was
                  re-emitted once per record and every extra record bought a
                  sheet printing that word alone. blank_measure section (G)
                  measures it — a sheet whose ink is ALL ECHO (every mark
                  painted on another sheet too, none of it a data region's)
                  and which is an order of magnitude STARVED beside the
                  fullest sheet of its own document. Same three-part shape as
                  the blank column: the reference is measured in its own
                  right, the other shapes are judged on COUNT against it. The
                  defect SCALED with the data (none at zero rows, one at
                  three, five at twenty-five), which is why it is measured at
                  every shape and not at one.
  L-7 ADDITIVITY  the shared measure never reports LESS than the rule it
                  replaced without a NAMED reason. The measure promised that
                  in prose, and the promise was false at zero rows: a
                  whole-render condition inside ``strict_blanks`` made three
                  furniture-only sheets that the historical rule flagged come
                  back clean, and the same static sheet got opposite verdicts
                  at different row counts. Prose cannot fail. This leg re-runs
                  the historical rule on every render measured here and fails
                  on any page the measure calls content without one of the
                  three named later rules accounting for it (short content
                  under the old character floor, undecodable ink, a raster).
                  Measured at EVERY shape, because the regression it exists
                  for was invisible at three of the four.

SCOPE — shape defects PLUS the reference the shapes are measured against. The
relative legs exist because a report printing the SAME blank sheet at every
shape has a shape-INDEPENDENT defect, and repeating it four times would bury
the shape signal. What that reasoning did NOT license — and what this rail did
for a whole campaign — was leaving the reference shape unmeasured: every blank
leg read `not ref["blanks"]`, so one blank sheet at rows=3 exempted a report
from every blank check at every row count. A judge found six production
letters printing blank sheets inside a matrix this rail called clean; the
letters were fixed, the HOLE was not, and the mutation proofs below reproduce
it from this file's own fixture. The reference now carries
its own leg, and the relative legs compare COUNTS (more blank sheets than the
reference = introduced by this row count), so a dirty reference no longer
switches them off. Both properties are mutation-proven below, ascii and
non-Latin.

The one thing exempt from the blank legs is an artifact that declares no body
content at all (``_declares_body_content``): with nothing in the body, the
empty sheet is not manufactured by any shape — it is the honest render of an
empty declaration. That carve-out is structural, and it pays for itself: the
rail ASSERTS that such an artifact is one the pipeline refuses to ship
(preflight verdict BLOCKER), so it can never become the reason a shippable
report goes unmeasured. Measured over the full matrix: 55 of 241 sources
(scraped documentation pages saved as .xml, layout fragments, customization
overlays) emit an RDL with an empty Body, and preflight BLOCKS all 55.

The repo-bundled corpora (samples + the i18n fixtures) and the synthetic
report carry the ABSOLUTE anchor: they are blank-free at every shape, with no
baseline available to them.

WHAT "BLANK" MEANS — tools/renderlab/blank_measure.py, shared by every rail.
A sheet is blank when a reader sees no CONTENT on it; page furniture is ink
but not content. The furniture is derived from the ARTIFACT — the wording the
RDL declares inside its PageHeader/PageFooter (literal, staticized and
expression-literal, so a Greek or Spanish report is measured in its own
language) plus the lines the document repeats on EVERY page — never from a
list of English words. The two literals the measure used to be built from
survive only as a fallback for callers that pass no RDL.

LAYOUT-MODE CAVEAT — honest limit of this rail, measured not assumed. When the
expression host cannot launch (Application-Control-blocked exe), render_rdl
falls back to the LAYOUT path, which renders a STATICIZED RDL: every
=expression is replaced by invented placeholder text. On such a render an
expression-only sheet ALWAYS carries ink, so a page that would come back BLANK
with real data cannot be detected there by any measure of that PDF — the ink is
the harness's, not the report's. What is expressible is the ink's PROVENANCE,
and the shared measure reports it: ``placeholder_only`` lists pages whose whole
content is staticizer-invented text. Those pages are exactly the ones that go
blank if their expressions evaluate empty. This rail PRINTS that list in its
failure table and does not gate on it: with the expression host up, the same
pages carry real values and the question answers itself; with it down, calling
them defects would be guessing. The zero-row leg is unaffected — at zero rows
there is nothing for an expression to return.

COST — 241 sources x 4 shapes is ~960 engine renders. The external corpora are
therefore DETERMINISTICALLY SHA-ROTATED (same contract as the upload gate's
compile leg): a different bucket each day, full coverage across the rotation.
The repo-bundled sources always run. O2S_SHAPE_MATRIX_ALL=1 renders the whole
matrix (the release-mode run whose full table is recorded in the campaign
notes).

KNOWN-OPEN RATCHET: an external corpus directory may carry a
HOSTILE_SHAPE_BASELINE.json (next to the sources, NEVER in the repo) mapping
rel-path -> known converter backlog on that shape. A file whose violations are
ALL baselined xfails (visible, tracked); any NEW violation anywhere is a hard
failure. An entry is either

    "rel/path.xml": ["shape3:blank_sheet"]                      (bare list)
    "rel/path.xml": {"violations": [...], "disposition": "..."} (documented)

and the documented form is the one to write: a ratchet entry without a named
disposition is a defect nobody can triage later. A dict entry whose
``disposition`` is missing or empty masks NOTHING — it is treated as absent,
so the violation stays fatal and the omission cannot pass silently.

THE RATCHET TURNS ONE WAY. An entry lives only while the violation it names
still happens: a baselined violation that no longer reproduces ends the case
RED (see ``_gate``). A stale entry is not dead weight, it is an armed mask —
it goes on excusing the NEXT violation of the same name on the same file, and
because the case passes green nothing in any run ever mentions it again. That
is exactly how this rail's last wild-corpus entry sat parked across a whole
campaign, so the condition is measured instead of trusted.

The mutation proofs below doctor a report into emitting the historical
zero-row blank sheet, a blank sheet hidden behind page furniture, and a blank
sheet at the REFERENCE shape, and show the rail goes RED on each.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

import pytest

from converter import convert

REPO_ROOT = Path(__file__).resolve().parent.parent
_RENDERLAB = REPO_ROOT / "tools" / "renderlab"
BASELINE_NAME = "HOSTILE_SHAPE_BASELINE.json"

SHAPES = (0, 1, 3, 25)
REFERENCE_SHAPE = 3  # the shape everything else was historically proven at

# Repo-bundled corpora always run; external ones rotate.
BUNDLED = ("samples", "i18n")
_ROTATION_BUCKETS = 24

# Same roster as the two fatal gates — absent directories contribute no cases.
CORPORA = {
    "samples": REPO_ROOT / "samples" / "oracle",
    "i18n": REPO_ROOT / "tests" / "fixtures" / "i18n",
    "agency": Path(os.environ.get(
        "O2S_AGENCY_CORPUS",
        "C:/Users/maxca/Downloads/OneDrive_2026-08-06/Artifact Folders")),
    "wild": Path(os.environ.get(
        "O2S_WILD_CORPUS",
        "C:/Users/maxca/Downloads/_o2s_scratch11/wild_corpus")),
    "wild2": Path(os.environ.get(
        "O2S_WILD_CORPUS2",
        "C:/Users/maxca/Downloads/_o2s_scratch11/wild_corpus2")),
    "wild3": Path(os.environ.get(
        "O2S_WILD_CORPUS3",
        "C:/Users/maxca/Downloads/_o2s_scratch11/wild_corpus3")),
    "wild4": Path(os.environ.get(
        "O2S_WILD_CORPUS4",
        "C:/Users/maxca/Downloads/_o2s_scratch11/wild_corpus4")),
    "wild5": Path(os.environ.get(
        "O2S_WILD_CORPUS5",
        "C:/Users/maxca/Downloads/_o2s_scratch11/wild_corpus5")),
    "wild6": Path(os.environ.get(
        "O2S_WILD_CORPUS6",
        "C:/Users/maxca/Downloads/_o2s_scratch11/wild_corpus6")),
    "wild7": Path(os.environ.get(
        "O2S_WILD_CORPUS7",
        "C:/Users/maxca/Downloads/_o2s_scratch11/wild_corpus7")),
    "wild8": Path(os.environ.get(
        "O2S_WILD_CORPUS8",
        "C:/Users/maxca/Downloads/_o2s_scratch11/wild_corpus8")),
}


def _collect_corpus(root: Path):
    """Every *.xml under the corpus root, skipping tooling/output dirs
    (underscore-prefixed: _results, _generated_rdl, _meta, _dups...)."""
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.rglob("*.xml")):
        if any(part.startswith("_") for part in p.relative_to(root).parts[:-1]):
            continue
        out.append(p)
    return out


def _corpus_params():
    params = []
    for corpus, root in CORPORA.items():
        for p in _collect_corpus(root):
            rel = p.relative_to(root).as_posix()
            params.append(pytest.param(corpus, root, p, id=f"{corpus}-{rel}"))
    return params


_PARAMS = _corpus_params()


def _load_baseline(root: Path):
    """rel-path -> baselined violation strings, from either entry form.

    A dict entry must carry a non-empty ``disposition``: the ratchet exists to
    keep known-open defects VISIBLE, and an entry nobody wrote a reason for is
    indistinguishable from one somebody forgot. Such an entry masks nothing."""
    bl = root / BASELINE_NAME
    if not bl.is_file():
        return {}
    try:
        raw = json.loads(bl.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — a corrupt baseline must never mask
        return {}      # violations; treat it as absent (everything fatal)
    out = {}
    for rel, entry in (raw or {}).items():
        if isinstance(entry, dict):
            if not str(entry.get("disposition") or "").strip():
                continue  # undocumented: nothing is masked
            entry = entry.get("violations") or ()
        out[rel] = tuple(entry or ())
    return out


# ---------------------------------------------------------------------------
# Sampling: deterministic, sha-rotated, provably rotating (see the test below)
# ---------------------------------------------------------------------------

def _in_todays_sample(corpus: str, case_id: str) -> bool:
    if os.environ.get("O2S_SHAPE_MATRIX_ALL") == "1":
        return True
    if corpus in BUNDLED:
        return True  # repo-bundled anchor: always measured, never sampled
    bucket = int(hashlib.sha256(case_id.encode()).hexdigest(), 16) % _ROTATION_BUCKETS
    return bucket == date.today().toordinal() % _ROTATION_BUCKETS


# ---------------------------------------------------------------------------
# The engine harness (engine-gated: absent harness SKIPS, never passes)
# ---------------------------------------------------------------------------

def _render_harness():
    if sys.platform != "win32":
        pytest.skip("MS ReportViewer engine harness is Windows-only")
    if str(_RENDERLAB) not in sys.path:
        sys.path.insert(0, str(_RENDERLAB))
    try:
        from render import lib_ready, render_rdl
        from render_overlap import pdf_overlaps
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"renderlab import failed: {exc}")
    if not lib_ready():
        pytest.skip("ReportViewer DLLs missing — shape matrix skipped, not passed")
    return render_rdl, pdf_overlaps


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------

# The strict blank measure lives in ONE place (tools/renderlab/blank_measure.py)
# and every rail imports it from there. It used to be copied into each caller
# as "strip the lines starting with 'page ' or 'report run on', a page with
# under 8 residual characters is blank" — two hardcoded ENGLISH literals as the
# whole definition of page furniture, which a judge proved blind: a mutation
# that kept the PageHeader intact hid a genuinely empty data sheet behind the
# repeated title, and this rail stayed green. The shared measure derives the
# furniture from the ARTIFACT (the RDL's own PageHeader/PageFooter wording, in
# whatever language it declares, plus the lines the document repeats on every
# page) and keeps the two literals only as a fallback.
if str(_RENDERLAB) not in sys.path:
    sys.path.insert(0, str(_RENDERLAB))
from blank_measure import measure_pdf  # noqa: E402


_NS = "{http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition}"


def _q(tag):
    return f"{_NS}{tag}"


def _declared_repeat_band_literals(rdl_xml: str):
    """Every STATIC literal the RDL declares must reprint on each page.

    Only FLAT row hierarchies are read (one top-level member per row, in
    order) — that is the shape where member->row mapping is unambiguous, so
    the check never guesses. Group bands are skipped: their captions are
    DATA, and data legitimately differs page to page."""
    try:
        root = ET.fromstring(rdl_xml)
    except ET.ParseError:
        return []
    out = []
    for tx in root.iter(_q("Tablix")):
        body = tx.find(_q("TablixBody"))
        hier = tx.find(_q("TablixRowHierarchy"))
        if body is None or hier is None:
            continue
        rows_el = body.find(_q("TablixRows"))
        members_el = hier.find(_q("TablixMembers"))
        if rows_el is None or members_el is None:
            continue
        rows, members = list(rows_el), list(members_el)
        if len(rows) != len(members):
            continue  # nested/ragged hierarchy: no unambiguous mapping
        if any(m.find(_q("TablixMembers")) is not None for m in members):
            continue
        for member, row in zip(members, rows):
            if (member.findtext(_q("RepeatOnNewPage")) or "").strip().lower() != "true":
                continue
            if member.find(_q("Group")) is not None and \
                    member.find(_q("Group")).find(_q("GroupExpressions")) is not None:
                continue  # dynamic band — its caption is data
            for val in row.iter(_q("Value")):
                text = (val.text or "").strip()
                if len(text) >= 3 and not text.startswith("="):
                    out.append(text)
    return sorted(set(out))


def _squash(s: str) -> str:
    return "".join(s.split()).lower()


# SSRS default cell padding: 2pt each side of every cell, which the declared
# column widths do not carry.
_CELL_PADDING_IN = 4 / 72.0


def _paginates_horizontally(rdl_xml: str) -> bool:
    """True when a table is WIDER than the paper, so the engine splits it by
    COLUMN. Its continuation pages carry the NEXT columns, not a repeat of the
    first page's — measured on three wide wild tables whose page 2 legitimately
    shows a different slice of the same declared header row."""
    try:
        root = ET.fromstring(rdl_xml)
    except ET.ParseError:
        return False
    page = root.find(_q("Page"))
    if page is None:
        return False

    def _in(el, tag):
        try:
            return float(((el.findtext(_q(tag)) or "0")).replace("in", ""))
        except ValueError:
            return 0.0

    printable = (_in(page, "PageWidth")
                 - _in(page, "LeftMargin") - _in(page, "RightMargin"))
    if printable <= 0:
        return False
    for tx in root.iter(_q("Tablix")):
        body = tx.find(_q("TablixBody"))
        cols = body.find(_q("TablixColumns")) if body is not None else None
        if cols is None:
            continue
        columns = list(cols)
        width = (_in(tx, "Left") + len(columns) * _CELL_PADDING_IN
                 + sum(_in(c, "Width") for c in columns))
        if width > printable:
            return True
    return False


def _declares_body_content(rdl_xml: str) -> bool:
    """Whether the artifact declares ANYTHING in its body that could paint.

    Read from the artifact, not from the render: an RDL whose ``Body`` holds
    no report item at all cannot print a sheet with content on it, so its
    empty sheet is not a manufactured one — it is the honest render of an
    empty declaration, and a CONTENT defect that a named fatal gate owns
    (see the carve-out's own assertion in the rail below). Structural and
    script-independent: it counts declared elements, never text."""
    try:
        root = ET.fromstring(rdl_xml)
    except ET.ParseError:
        return False
    body = root.find(_q("Body"))
    items = body.find(_q("ReportItems")) if body is not None else None
    return items is not None and len(list(items)) > 0


def _shape_violations(rdl_xml, render_rdl, pdf_overlaps, workdir, what="report"):
    """Render one RDL at every shape and return (violations, table).

    ``table`` is the per-shape measurement row this rail exists to produce:
    {shape: {"pages", "blanks", "paint", "chars"}}.
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    rdl_path = workdir / "shape.rdl"
    rdl_path.write_text(rdl_xml, encoding="utf-8")

    violations, table = [], {}
    for shape in SHAPES:
        pdf = workdir / f"shape_{shape}.pdf"
        try:
            res = render_rdl(rdl_path, pdf, rows=shape)
        except Exception as exc:  # noqa: BLE001
            violations.append(f"shape{shape}:render_exception.{type(exc).__name__}")
            continue
        if not res.get("ok"):
            violations.append(f"shape{shape}:render_fail")
            continue
        m = measure_pdf(pdf, rdl_xml=rdl_xml, mode=res.get("mode"))
        table[shape] = {
            "pages": m["pages"],
            "blanks": m["blank"],
            # A sheet that is technically not blank and effectively empty
            # (blank_measure section (G)). Disjoint from "blanks": a sparse
            # sheet is a CONTENT sheet, so the two legs never double-report.
            "sparse": m["sparse"],
            "paint": len(pdf_overlaps(pdf)),
            # "chars" is the FALLBACK residual (page furniture counts as ink):
            # the L-2 leg below asks "did the report print anything of its
            # own", for which furniture is a yes. Content ink is separate.
            "chars": m["legacy_chars"],
            "content_chars": m["content_chars"],
            "classes": m["classes"],
            "texts": m["texts"],
            # L-7's inputs: what the rule this measure REPLACED would flag on
            # the same render, and the named reason for each page the two
            # disagree on (blank_measure.additivity_exceptions).
            "legacy_blank": m["legacy_blank"],
            "additivity": m["additivity"],
            "printed_no_content": m["printed_no_content"],
            "placeholder_only": m["placeholder_only"],
        }
        # L-7 ADDITIVITY — the replacement may never see LESS than the rule it
        # replaced, on any shape, without a named reason.
        #
        # The measure's stated contract was that its new rules only ADD
        # detections, and it was prose: a whole-render condition inside
        # ``strict_blanks`` made three furniture-only sheets that the old rule
        # flagged come back clean, at ZERO ROWS, where the blank column matters
        # most. Prose cannot fail, so the contract is measured here on every
        # real render this rail makes — the exact place the regression lived —
        # and any page the comparison cannot name a reason for is fatal.
        unjustified = sorted(p for p, why in table[shape]["additivity"].items()
                             if why == "unjustified")
        if unjustified:
            violations.append(f"shape{shape}:additivity_regression")

    ref = table.get(REFERENCE_SHAPE)
    declares_content = _declares_body_content(rdl_xml)

    # L-2 REFERENCE SHAPE — the shape every other leg is judged against is a
    # measurement in its own right, not just a baseline.
    #
    # This leg was MISSING, and its absence disabled the whole blank column:
    # every blank leg below read `not ref["blanks"]`, so a report that printed
    # a blank sheet at three rows was exempt from every blank check at every
    # row count. A judge found it on production letters — sheets blank in the
    # delivered PDF while this rail reported a clean matrix — and the mutation
    # proof below reproduces it from this file's own fixture: a letter doctored
    # to blank at rows=1, 3 AND 25 scored ZERO violations.
    #
    # Blanks at the reference are therefore a violation on their own, and the
    # relative legs are re-derived as "MORE blank sheets than the reference"
    # so they keep catching shape-SPECIFIC regressions while a dirty reference
    # is being worked off, instead of being switched off by it.
    if ref is not None and ref["blanks"] and declares_content:
        violations.append(f"shape{REFERENCE_SHAPE}:blank_sheet")

    # L-6 SPARSE SHEETS — the companion leg, and the one the blank column
    # cannot supply: a sheet carrying one stray word is not blank, and six
    # sheets of a production letter went out that way while this matrix
    # reported clean. Same three-part structure as the blank column above,
    # for the same measured reason: the reference shape is a measurement in
    # its own right, and the other shapes are judged on COUNT against it, so
    # a dirty reference cannot switch the rest of the column off.
    if ref is not None and ref["sparse"] and declares_content:
        violations.append(f"shape{REFERENCE_SHAPE}:sparse_sheet")

    def _blank_regression(cell) -> bool:
        """A shape prints MORE blank sheets than the reference does.

        Count, not identity: page NUMBERS shift with the row count (three rows
        and twenty-five do not put the same content on sheet 4), so the only
        shape-independent statement is how many sheets came back blank. The
        reference's own count is reported by the leg above; anything beyond it
        was introduced by this row count."""
        return len(cell["blanks"]) > len(ref["blanks"])

    def _sparse_regression(cell) -> bool:
        """A shape prints MORE sparse sheets than the reference does.

        Count, for the same reason the blank leg compares counts: page
        NUMBERS move with the row count. This is also the shape the defect
        actually took — the production letter's sparse sheets SCALED with the
        data (none at zero rows, one at three, five at twenty-five), so a leg
        that only measured one shape would have called it clean at another."""
        return len(cell["sparse"]) > len(ref["sparse"])

    # L-2b ZERO ROWS — declared chrome prints, and no empty sheet is manufactured.
    zero = table.get(0)
    if zero is not None:
        if ref is not None and _sparse_regression(zero):
            violations.append("shape0:sparse_sheet")
        if ref is not None and _blank_regression(zero):
            violations.append("shape0:blank_sheet")
        elif zero["chars"] < 8 and ref and ref["chars"] >= 8:
            # Every page had ink at three rows and none has ink at zero:
            # the report lost its own declared chrome, not just its data.
            violations.append("shape0:no_declared_chrome")

    # L-3 ONE ROW — no orphan page, no blank sheet beyond the reference's.
    one = table.get(1)
    if one is not None and ref is not None:
        if one["pages"] > ref["pages"]:
            violations.append("shape1:page_inflation")
        if _blank_regression(one):
            violations.append("shape1:blank_sheet")
        if _sparse_regression(one):
            violations.append("shape1:sparse_sheet")

    # L-4 25 ROWS — pagination grows, no blank sheet between data pages, and
    # declared repeat bands reprint on the continuation page.
    many = table.get(25)
    if many is not None and ref is not None:
        if many["pages"] < ref["pages"]:
            violations.append("shape25:pagination_regress")
        if _blank_regression(many):
            violations.append("shape25:blank_sheet")
        if _sparse_regression(many):
            violations.append("shape25:sparse_sheet")
        # The first CONTINUATION sheet that carries something — a report whose
        # page 2 is blank has that reported already, and skipping the band
        # check entirely (what this leg used to do whenever any page was
        # blank) would silently drop the check for every such report.
        cont = next((i for i in range(1, many["pages"])
                     if i + 1 not in many["blanks"]), None)
        if cont is not None:
            # RAW page text, not the residual: the blank measure strips lines
            # the document repeats on every page, and a repeating band is
            # exactly that — stripping it would hide the band this leg looks
            # for and turn a healthy report into a false violation.
            page2 = _squash(many["texts"][cont])
            declared = _declared_repeat_band_literals(rdl_xml)
            missing = [t for t in declared if _squash(t) not in page2]
            # A table wider than the paper splits by COLUMN: its page 2 holds
            # the NEXT columns of the same declared header row, so only SOME
            # of the captions belong there. Losing the band entirely is still
            # a defect; losing the columns that moved is not.
            lost = (len(missing) == len(declared)
                    if _paginates_horizontally(rdl_xml) else bool(missing))
            if declared and lost:
                table[25]["repeat_band_missing"] = missing[:5]
                violations.append("shape25:repeat_band_missing_on_continuation")

    # L-5 PAINT — a row count may not INTRODUCE painted-over text.
    if ref is not None and ref["paint"] == 0:
        for shape, cell in table.items():
            if cell["paint"] > 0:
                violations.append(f"shape{shape}:paint_appears")

    return sorted(set(violations)), table


def _format_table(table):
    return " | ".join(
        f"rows={s}: {c['pages']}p blanks={c['blanks']} "
        f"sparse={c['sparse']} paint={c['paint']}"
        + (f" legacy_blank={c['legacy_blank']}"
           if c.get("legacy_blank") else "")
        + (f" additivity={c['additivity']}" if c.get("additivity") else "")
        + (" printed_no_content" if c.get("printed_no_content") else "")
        + (f" placeholder_only={c['placeholder_only']}"
           if c.get("placeholder_only") else "")
        for s, c in sorted(table.items()))


def _gate(violations, baselined, what, table):
    """Any NEW violation is fatal; an all-baselined file is a visible xfail;
    a baselined violation that NO LONGER REPRODUCES is fatal too.

    The last leg is what keeps the ratchet a ratchet. An entry whose violation
    has stopped happening reads like harmless dead weight and is nothing of
    the kind: it stays armed over that violation NAME on that file, so the
    next one is excused silently, and since the case now passes green no run
    ever names the entry again. Measured, not argued — this rail's last
    wild-corpus entry had stopped reproducing and sat parked for a campaign
    while its shape leg was masked."""
    base = set(baselined or ())
    new = [v for v in violations if v not in base]
    assert not new, (
        f"HOSTILE DATA-SHAPE RAIL — {what}: {new}\n  {_format_table(table)}")
    stale = sorted(base - set(violations))
    assert not stale, (
        f"HOSTILE DATA-SHAPE RAIL — {what}: baselined violation(s) {stale} no "
        f"longer reproduce, so the entry now masks nothing except the next "
        f"one of the same name. Delete it from {BASELINE_NAME}; if instead "
        f"this run simply cannot SEE the defect (a render mode the entry was "
        f"not measured in), say that in the disposition and re-measure "
        f"there.\n  {_format_table(table)}")
    if violations:
        pytest.xfail(f"{what}: baselined known-open shape violation(s) "
                     f"({BASELINE_NAME}): {sorted(violations)}")


# ---------------------------------------------------------------------------
# The rail
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _PARAMS, reason="no corpus sources present")
@pytest.mark.parametrize("corpus,corpus_root,source_path", _PARAMS)
def test_hostile_data_shapes(corpus, corpus_root, source_path, tmp_path):
    rel = source_path.relative_to(corpus_root).as_posix()
    if not _in_todays_sample(corpus, f"{corpus_root}|{rel}"):
        pytest.skip("not in today's sha-rotation shape sample "
                    "(O2S_SHAPE_MATRIX_ALL=1 renders the full matrix)")
    render_rdl, pdf_overlaps = _render_harness()

    res = convert(source_path.read_bytes(), source_path.name)
    rdl = (res or {}).get("rdl_xml") or ""
    if not rdl.strip():
        pytest.skip(f"not a convertible report ({res.get('kind', 'no RDL')})")
    assert not res.get("conversion_error"), (
        f"{rel}: convert() fell back to the crash-safety RDL "
        f"({res['conversion_error'][:200]}) — a fallback RDL is not a "
        "convertible source and must never reach a shape measurement")

    if not _declares_body_content(rdl):
        # The blank legs carve this artifact out (nothing declared can paint,
        # so no shape MANUFACTURED the empty sheet). The carve-out is only
        # honest while a named gate owns the case, so assert that gate here:
        # an artifact that declares no content must be one the pipeline
        # REFUSES to ship. If this ever passes as shippable, the carve-out is
        # hiding a blank report and this rail says so.
        verdict = ((res.get("preflight") or {}).get("verdict") or "").upper()
        assert verdict == "BLOCKER", (
            f"{rel}: the RDL declares NO body content — it can only render "
            f"empty sheets — yet preflight calls it {verdict or 'nothing'}. "
            "The shape rail exempts empty declarations from its blank legs "
            "only because a fatal gate refuses to ship them.")

    violations, table = _shape_violations(
        rdl, render_rdl, pdf_overlaps, tmp_path, what=rel)
    baselined = ()
    if corpus not in BUNDLED:
        baselined = _load_baseline(corpus_root).get(rel, ())
    _gate(violations, baselined, rel, table)


def test_synthetic_report_survives_every_shape(synthetic_xml_bytes, tmp_path):
    """The repo-bundled anchor: alive on any machine with the engine, with no
    baseline available to it."""
    render_rdl, pdf_overlaps = _render_harness()
    rdl = convert(synthetic_xml_bytes, target_db="oracle")["rdl_xml"]
    violations, table = _shape_violations(
        rdl, render_rdl, pdf_overlaps, tmp_path, what="synthetic")
    assert not violations, (
        f"synthetic report failed the shape matrix: {violations}\n"
        f"  {_format_table(table)}")
    assert table[0]["pages"] >= 1 and table[0]["chars"] >= 8, \
        "zero rows must still print the report's declared chrome"


def test_shape_sample_is_deterministic_and_rotates():
    """The external-corpus sample is a pure function of (case sha, day):
    same-day selection is stable, and across _ROTATION_BUCKETS consecutive
    days every case is selected at least once."""
    ids = [f"case-{i}" for i in range(600)]
    buckets = {c: int(hashlib.sha256(c.encode()).hexdigest(), 16)
               % _ROTATION_BUCKETS for c in ids}
    assert len(set(buckets.values())) == _ROTATION_BUCKETS  # spread, not clumped
    for b in range(_ROTATION_BUCKETS):
        assert any(v == b for v in buckets.values())
    # bundled corpora are never sampled out
    for corpus in BUNDLED:
        assert _in_todays_sample(corpus, "any|case")


def test_a_ratchet_entry_masks_only_while_it_names_its_disposition(tmp_path):
    """The ratchet keeps known-open defects VISIBLE, so an entry has to say
    what it is. Bare lists stay readable (legacy form), a documented entry
    masks exactly its own violations, and an entry with no disposition — the
    way a ratchet quietly becomes a dumping ground — masks nothing at all."""
    (tmp_path / BASELINE_NAME).write_text(json.dumps({
        "legacy.xml": ["shape0:blank_sheet"],
        "documented.xml": {"violations": ["shape3:blank_sheet"],
                           "disposition": "declared layout wider than paper"},
        "undocumented.xml": {"violations": ["shape3:blank_sheet"]},
        "blank_reason.xml": {"violations": ["shape3:blank_sheet"],
                             "disposition": "   "},
    }), encoding="utf-8")
    bl = _load_baseline(tmp_path)
    assert bl["legacy.xml"] == ("shape0:blank_sheet",)
    assert bl["documented.xml"] == ("shape3:blank_sheet",)
    assert "undocumented.xml" not in bl and "blank_reason.xml" not in bl

    # ...and that is not cosmetic: the undocumented entry stays FATAL, while
    # the documented one ends the case as a visible xfail.
    try:
        _gate(["shape3:blank_sheet"], bl.get("documented.xml"), "documented", {})
    except BaseException as exc:  # noqa: BLE001 — xfail() ends the case
        assert type(exc).__name__ == "XFailed", exc
    else:
        raise AssertionError("an all-baselined file must xfail, not pass")
    with pytest.raises(AssertionError):
        _gate(["shape3:blank_sheet"], bl.get("undocumented.xml"),
              "undocumented", {})


def test_a_ratchet_entry_dies_with_the_violation_it_names():
    """The ratchet turns ONE WAY: an entry survives only while its violation
    still reproduces.

    Without this leg a baselined violation that stops happening leaves the
    entry ARMED and INVISIBLE — it keeps excusing that violation name on that
    file, and the green case never mentions it, so nobody re-reads it. That is
    not a hypothetical: this rail's last wild-corpus entry named a zero-row
    blank sheet the artifact had stopped printing, and it sat parked over the
    whole shape0 blank leg of that file for a campaign."""
    table = {0: {"pages": 1, "blanks": [], "sparse": [], "paint": 0}}

    # (a) still reproducing -> masked, and VISIBLE as an xfail
    try:
        _gate(["shape0:blank_sheet"], ["shape0:blank_sheet"], "live", table)
    except BaseException as exc:  # noqa: BLE001 — xfail() ends the case
        assert type(exc).__name__ == "XFailed", exc
    else:
        raise AssertionError("a still-open baselined violation must xfail")

    # (b) no longer reproducing -> FATAL, and the message names the file to
    #     edit, because an entry nobody can find is an entry nobody removes.
    with pytest.raises(AssertionError) as err:
        _gate([], ["shape0:blank_sheet"], "stale", table)
    assert "no longer reproduce" in str(err.value)
    assert BASELINE_NAME in str(err.value)

    # (c) HALF stale: the live half still masks, the dead half is still fatal.
    #     A whole-entry rule would let a two-violation entry keep a dead name
    #     alive on the back of a live one.
    with pytest.raises(AssertionError) as err:
        _gate(["shape3:blank_sheet"],
              ["shape0:blank_sheet", "shape3:blank_sheet"], "half", table)
    assert "shape0:blank_sheet" in str(err.value)
    assert "shape3:blank_sheet" not in str(err.value)

    # (d) an unbaselined clean file is still just clean
    _gate([], (), "clean", table)


# ---------------------------------------------------------------------------
# MUTATION PROOF — the rail must go RED on the historical zero-row blank sheet
# ---------------------------------------------------------------------------

# A per-record letter: a masthead band, then a page-tall record sheet below it.
# This is the shape whose zero-row render manufactured an empty page 2 —
# reproduced here from declarations only, no customer text.
def _letter_xml(record_height: str = "9.00000",
                masthead: str = "Account Statement",
                standfirst: str = "Prepared for the selected period") -> bytes:
    """A per-record LETTER: a criteria/masthead section_header, then one
    record sheet per row. ``record_height`` decides whether the record
    region's box still fits the paper once the masthead sits above it.

    ``masthead``/``standfirst`` carry the report's declared wording, so the
    same fixture can be built in any script — the blank legs must reach the
    same verdict on a Greek or Arabic report as on an English one."""
    return (
        '<?xml version="1.0"?>'
        '<report name="SHAPE_LETTER" DTDVersion="9.0.2.0.10">'
        '<data><dataSource name="Q_LETTERS">'
        '<select><![CDATA[select acct_no, addressee from letter_queue]]>'
        '</select><group name="G_LETTER">'
        '<dataItem name="ACCT_NO" datatype="vchar2"/>'
        '<dataItem name="ADDRESSEE" datatype="vchar2"/>'
        '</group></dataSource></data>'
        '<layout>'
        '<section name="header" width="8.50000">'
        '<body width="8.00000" height="3.00000">'
        '<text name="H_TITLE"><geometryInfo x="0.50000" y="0.60000" '
        'width="5.00000" height="0.30000"/><textSegment>'
        '<font face="Arial" size="12"/>'
        f'<string><![CDATA[{masthead}]]></string></textSegment></text>'
        '<text name="H_SUB"><geometryInfo x="0.50000" y="1.10000" '
        'width="5.00000" height="0.30000"/><textSegment>'
        '<font face="Arial" size="10"/>'
        f'<string><![CDATA[{standfirst}]]></string>'
        '</textSegment></text>'
        '</body></section>'
        '<section name="main" width="8.50000">'
        '<body width="7.50000" height="9.60000">'
        '<repeatingFrame name="R_LETTER" source="G_LETTER" '
        'printDirection="down" maxRecordsPerPage="1" minWidowRecords="1" '
        'columnMode="no">'
        f'<geometryInfo x="0.00000" y="0.00000" width="7.50000" '
        f'height="{record_height}"/>'
        '<generalLayout verticalElasticity="variable"/>'
        '<field name="F_ADDR" source="ADDRESSEE">'
        '<geometryInfo x="0.30000" y="0.30000" width="4.00000" '
        'height="0.20000"/></field>'
        '<field name="F_ACCT" source="ACCT_NO">'
        '<geometryInfo x="0.30000" y="0.70000" width="4.00000" '
        'height="0.20000"/></field>'
        '</repeatingFrame>'
        '</body></section></layout></report>').encode()


_LETTER_XML = _letter_xml()


def _doctor_zero_row_blank_sheet(rdl_xml: str) -> str:
    """Put the historical defect BACK: give the page-tall record region a
    NoRowsMessage again. With zero rows the engine paints that message across
    the region's whole declared box, which runs off the paper — so page 1
    carries the notice and page 2 comes back completely EMPTY."""
    root = ET.fromstring(rdl_xml)
    ET.register_namespace("", _NS[1:-1])
    doctored = False
    for tx in root.iter(_q("Tablix")):
        if tx.find(_q("NoRowsMessage")) is not None:
            continue
        ds = tx.find(_q("DataSetName"))
        if ds is None:
            continue
        el = ET.Element(_q("NoRowsMessage"))
        el.text = "No data was returned for the selected criteria."
        tx.insert(list(tx).index(ds) + 1, el)
        doctored = True
    assert doctored, "mutation found no data region to doctor"
    return ET.tostring(root, encoding="unicode")


def test_rail_catches_a_zero_row_blank_sheet(tmp_path):
    """Break it on purpose, watch the gate go RED, restore, watch it go green.

    The A/B is the whole proof: the SAME report, the SAME rail, the only
    difference being the element the emitter now withholds from a region whose
    no-rows box would not fit the page."""
    render_rdl, pdf_overlaps = _render_harness()
    good = convert(_LETTER_XML)["rdl_xml"]

    clean, clean_table = _shape_violations(
        good, render_rdl, pdf_overlaps, tmp_path / "good", what="letter")
    assert not clean, f"the letter fixture must start rail-clean: {clean}"
    assert clean_table[0]["pages"] == 1 and not clean_table[0]["blanks"], \
        f"zero rows must print ONE clean sheet: {_format_table(clean_table)}"
    assert clean_table[0]["chars"] >= 8, "zero rows lost the declared chrome"

    broken, broken_table = _shape_violations(
        _doctor_zero_row_blank_sheet(good), render_rdl, pdf_overlaps,
        tmp_path / "broken", what="letter(doctored)")
    assert "shape0:blank_sheet" in broken, (
        "the rail did NOT catch a zero-row blank sheet — it is blind to the "
        f"defect it exists for: {broken} / {_format_table(broken_table)}")
    assert broken_table[0]["pages"] == 2 and broken_table[0]["blanks"] == [2], \
        f"mutation did not produce the defect: {_format_table(broken_table)}"

    # ...and the mutation is confined to zero rows: with data, the two RDLs
    # render identically, which is what makes withholding the notice safe.
    for shape in (1, 3, 25):
        assert clean_table[shape]["pages"] == broken_table[shape]["pages"], (
            f"rows={shape} differs between the two arms — the no-rows notice "
            "must be invisible to every shape that HAS rows")


# ---------------------------------------------------------------------------
# MUTATION PROOF — a blank sheet hiding behind PAGE FURNITURE
# ---------------------------------------------------------------------------

def _with_running_page_header(rdl_xml: str, caption: str) -> str:
    """Give the report a PageHeader that reprints ONE literal on every sheet.

    Element order copied from the emitter's own page band, which the engine
    accepts; the test renders it, so a wrong order fails loudly instead of
    quietly proving nothing."""
    root = ET.fromstring(rdl_xml)
    ET.register_namespace("", _NS[1:-1])
    page = next(root.iter(_q("Page")))
    assert page.find(_q("PageHeader")) is None, \
        "fixture already declares a page band — pick one that does not"
    hdr = ET.Element(_q("PageHeader"))
    ET.SubElement(hdr, _q("Height")).text = "0.400in"
    ET.SubElement(hdr, _q("PrintOnFirstPage")).text = "true"
    ET.SubElement(hdr, _q("PrintOnLastPage")).text = "true"
    items = ET.SubElement(hdr, _q("ReportItems"))
    tb = ET.SubElement(items, _q("Textbox"))
    tb.set("Name", "Tb_RunningTitle")
    paras = ET.SubElement(tb, _q("Paragraphs"))
    para = ET.SubElement(paras, _q("Paragraph"))
    runs = ET.SubElement(para, _q("TextRuns"))
    run = ET.SubElement(runs, _q("TextRun"))
    ET.SubElement(run, _q("Value")).text = caption
    ET.SubElement(run, _q("Style"))
    ET.SubElement(para, _q("Style"))
    ET.SubElement(tb, _q("Style"))
    ET.SubElement(tb, _q("CanGrow")).text = "false"
    ET.SubElement(tb, _q("Top")).text = "0.000in"
    ET.SubElement(tb, _q("Left")).text = "0.000in"
    ET.SubElement(tb, _q("Width")).text = "6.000in"
    ET.SubElement(tb, _q("Height")).text = "0.300in"
    page.insert(0, hdr)
    return ET.tostring(root, encoding="unicode")


def _two_literal_blanks(texts):
    """The measure this rail USED to carry, kept verbatim as the control arm:
    strip lines starting with one of two English literals, a page with under 8
    residual characters is blank. The A/B below is the whole point — the same
    PDF, the same page, called INKED by this rule and BLANK by the shared
    measure."""
    out = []
    for i, txt in enumerate(texts):
        residual = "".join(
            ln for ln in (txt or "").splitlines()
            if not ln.strip().lower().startswith(("page ", "report run on"))
        ).strip()
        if len(residual) < 8:
            out.append(i + 1)
    return out


def test_rail_catches_a_blank_sheet_hidden_behind_page_furniture(tmp_path):
    """THE hole this measure was rebuilt to close.

    A judge doctored a report into printing an empty data sheet while leaving
    the PageHeader intact, and every rail stayed green: the running title
    counted as residual content, so the empty sheet read as inked. Proof here
    is an A/B on ONE render — the old two-literal rule finds nothing, the
    shared measure flags the sheet, and the rail goes RED on it."""
    render_rdl, pdf_overlaps = _render_harness()
    caption = "Statement Of Account"

    # ARM 1 — healthy report + the same page furniture: nothing to flag.
    good = _with_running_page_header(convert(_LETTER_XML)["rdl_xml"], caption)
    clean, clean_table = _shape_violations(
        good, render_rdl, pdf_overlaps, tmp_path / "good", what="headered")
    assert not clean, f"the headered letter must start rail-clean: {clean}"
    assert not clean_table[0]["blanks"], (
        "page furniture alone is not a blank-sheet defect when the document "
        f"has no content anywhere: {_format_table(clean_table)}")

    # ARM 2 — the SAME report with the historical zero-row empty sheet back.
    broken_rdl = _with_running_page_header(
        _doctor_zero_row_blank_sheet(convert(_LETTER_XML)["rdl_xml"]), caption)
    broken, broken_table = _shape_violations(
        broken_rdl, render_rdl, pdf_overlaps, tmp_path / "broken",
        what="headered(doctored)")

    zero = broken_table[0]
    assert zero["pages"] == 2, \
        f"mutation did not produce the extra sheet: {_format_table(broken_table)}"
    # The furniture really is ON the blank sheet — otherwise this proves nothing.
    assert _squash(caption) in _squash(zero["texts"][1]), (
        "page 2 does not carry the page furniture, so it is not the defect "
        f"under test: {zero['texts'][1]!r}")
    # ...and the OLD rule is blind to it. This assertion IS the gate hole.
    assert _two_literal_blanks(zero["texts"]) == [], (
        "the two-literal rule was supposed to MISS this page; if it now sees "
        "it, this fixture no longer reproduces the reported hole")
    # ...while the shared measure classifies it for what it is.
    assert zero["classes"][1] == "chrome_only" and zero["blanks"] == [2], (
        "a sheet carrying only page furniture is a BLANK sheet: "
        f"{zero['classes']} / {_format_table(broken_table)}")
    assert "shape0:blank_sheet" in broken, (
        "the rail did not go red on a blank page hidden behind a page header: "
        f"{broken} / {_format_table(broken_table)}")


# ---------------------------------------------------------------------------
# MUTATION PROOF — the ZERO-ROW leg, where the blank column was INERT
# ---------------------------------------------------------------------------

def _record_only_letter_xml() -> bytes:
    """The per-record letter WITHOUT a masthead section: the report's only
    declared body ink is per-row, so at zero rows the body has nothing at all
    to print."""
    full = _letter_xml().decode()
    start = full.index('<section name="header"')
    end = full.index('<section name="main"')
    return (full[:start] + full[end:]).encode()


def _masthead_declared_in_the_page_band(rdl_xml: str, caption: str) -> str:
    """Declare the report's letterhead in a PAGE BAND and withhold the
    no-rows notice — the two halves of one production shape.

    They travel together in the artifact: the notice is withheld exactly when
    a region's reserved box will not fit AND something else on the page still
    prints, and a band masthead is what "something else" is for a report whose
    letterhead lives in ``<PageHeader>``. Two shipping reports in the corpus
    render precisely this and print, at zero rows, one sheet carrying a
    letterhead, a page number and nothing else: furniture on every sheet,
    content on none."""
    root = ET.fromstring(rdl_xml)
    ET.register_namespace("", _NS[1:-1])
    page = next(root.iter(_q("Page")))
    assert page.find(_q("PageHeader")) is None, \
        "fixture already declares a page band — pick one that does not"

    parents = {c: p for p in root.iter() for c in p}
    withheld = 0
    for el in list(root.iter(_q("NoRowsMessage"))):
        parents[el].remove(el)
        withheld += 1
    assert withheld, "fixture declares no notice, so withholding one proves nothing"

    hdr = ET.Element(_q("PageHeader"))
    ET.SubElement(hdr, _q("Height")).text = "0.500in"
    ET.SubElement(hdr, _q("PrintOnFirstPage")).text = "true"
    ET.SubElement(hdr, _q("PrintOnLastPage")).text = "true"
    items = ET.SubElement(hdr, _q("ReportItems"))
    tb = ET.SubElement(items, _q("Textbox"))
    tb.set("Name", "Tb_BandMasthead")
    paras = ET.SubElement(tb, _q("Paragraphs"))
    para = ET.SubElement(paras, _q("Paragraph"))
    runs = ET.SubElement(para, _q("TextRuns"))
    run = ET.SubElement(runs, _q("TextRun"))
    ET.SubElement(run, _q("Value")).text = caption
    ET.SubElement(run, _q("Style"))
    ET.SubElement(para, _q("Style"))
    ET.SubElement(tb, _q("Style"))
    ET.SubElement(tb, _q("CanGrow")).text = "false"
    ET.SubElement(tb, _q("Top")).text = "0.000in"
    ET.SubElement(tb, _q("Left")).text = "0.000in"
    ET.SubElement(tb, _q("Width")).text = "6.000in"
    ET.SubElement(tb, _q("Height")).text = "0.300in"
    page.insert(0, hdr)
    return ET.tostring(root, encoding="unicode")


def _row_count_gated_blanks(classes):
    """The shared measure's blank rule AS IT STOOD: ``chrome_only`` counted
    only when the document had content SOMEWHERE — a whole-render condition
    inside a per-page verdict."""
    has_content = any(c == "content" for c in classes)
    return [i + 1 for i, c in enumerate(classes)
            if c == "empty" or (c == "chrome_only" and has_content)]


def test_rail_catches_a_zero_row_sheet_that_is_all_page_furniture(
        tmp_path, monkeypatch):
    """THE ZERO-ROW GATE HOLE — the blank leg was INERT at the shape it exists
    for, and this A/B is the whole proof.

    At zero rows a report whose masthead is declared in its page band prints
    ONE sheet: the letterhead, the page number, nothing else. Every sheet of
    that render is ``chrome_only``, so the document has content nowhere — and
    the measure's carve-out ("chrome_only counts only when the document HAS
    content somewhere") switched the leg off exactly there. The rule this
    module replaced flagged the same sheet, so the replacement reported LESS
    than its predecessor while promising to report only more.

    Both arms are the SAME PDF measured two ways: once by the rule as it
    stands, once with the whole-render condition put back."""
    import blank_measure as _bm

    render_rdl, pdf_overlaps = _render_harness()
    caption = "Statement Of Account"
    doctored = _masthead_declared_in_the_page_band(
        convert(_record_only_letter_xml())["rdl_xml"], caption)

    violations, table = _shape_violations(
        doctored, render_rdl, pdf_overlaps, tmp_path / "band",
        what="band-masthead letter")
    zero = table[0]
    assert _squash(caption) in _squash(zero["texts"][0]), (
        "the zero-row sheet must carry the page furniture, or it is not the "
        f"shape under test: {zero['texts'][0]!r}")
    assert zero["classes"] == ["chrome_only"] * zero["pages"], (
        "the mutation must make EVERY zero-row sheet furniture-only — that is "
        f"the shape the leg went blind on: {_format_table(table)}")
    assert zero["blanks"] == list(range(1, zero["pages"] + 1)), (
        f"every furniture-only sheet is a blank sheet: {_format_table(table)}")
    assert zero["printed_no_content"] is True, (
        "the empty-REPORT fact must still be measurable beside the verdict")
    assert "shape0:blank_sheet" in violations, (
        f"the rail did not go red at zero rows: {violations} / "
        f"{_format_table(table)}")
    # ...and every shape that HAS rows stays clean, so the mutation is
    # confined to the row count the leg went blind on.
    for shape in (1, 3, 25):
        assert not table[shape]["blanks"], (
            f"rows={shape} must stay clean: {_format_table(table)}")
    # ADDITIVITY on the same render: nothing the historical rule flagged may
    # go unflagged here, and any page the two disagree on carries a reason.
    assert set(zero["legacy_blank"]) <= set(zero["blanks"]), (
        "additivity: every sheet the historical rule flagged must still be "
        f"flagged — legacy={zero['legacy_blank']} new={zero['blanks']}")
    assert "unjustified" not in zero["additivity"].values(), zero["additivity"]

    # THE HOLE, reproduced on the SAME renders: put the whole-render condition
    # back and the zero-row leg scores nothing at all.
    monkeypatch.setattr(_bm, "strict_blanks", _row_count_gated_blanks)
    holed, holed_table = _shape_violations(
        doctored, render_rdl, pdf_overlaps, tmp_path / "band_holed",
        what="band-masthead letter (row-count-gated)")
    assert holed_table[0]["blanks"] == [], (
        "the mutation must reproduce the reported hole — a furniture-only "
        f"zero-row render coming back clean: {_format_table(holed_table)}")
    assert "shape0:blank_sheet" not in holed, (
        f"the hole must disable the leg, or this proves nothing: {holed}")


# ---------------------------------------------------------------------------
# MUTATION PROOF — a blank sheet at the REFERENCE shape (the P0 rail hole)
# ---------------------------------------------------------------------------

def _spacer_below_the_last_sheet(rdl_xml: str) -> str:
    """Declare a blank textbox past the bottom of the sheet the body ends on.

    An item RESERVES its declared box whether or not it paints (settled engine
    fact), so the body grows onto one more sheet and nothing paints there.
    Unlike the zero-row mutation above, this blank sheet is shape-INDEPENDENT:
    it appears at every row count that has rows — the reference shape
    included, which is exactly the case the rail could not see."""
    from converter.generators.rdl import _printable_page_height

    root = ET.fromstring(rdl_xml)
    ET.register_namespace("", _NS[1:-1])
    body = root.find(_q("Body"))
    items = body.find(_q("ReportItems"))
    body_h = float((body.findtext(_q("Height")) or "0").replace("in", ""))
    # one inch past whichever is lower: the body's own end or the sheet's
    top = max(body_h, _printable_page_height(root)) + 1.0
    tb = ET.SubElement(items, _q("Textbox"))
    tb.set("Name", "Tb_ShapeSpacer")
    paras = ET.SubElement(tb, _q("Paragraphs"))
    para = ET.SubElement(paras, _q("Paragraph"))
    runs = ET.SubElement(para, _q("TextRuns"))
    run = ET.SubElement(runs, _q("TextRun"))
    ET.SubElement(run, _q("Value")).text = " "
    ET.SubElement(run, _q("Style"))
    ET.SubElement(para, _q("Style"))
    ET.SubElement(tb, _q("Style"))
    ET.SubElement(tb, _q("CanGrow")).text = "false"
    ET.SubElement(tb, _q("Top")).text = f"{top:.3f}in"
    ET.SubElement(tb, _q("Left")).text = "0.000in"
    ET.SubElement(tb, _q("Width")).text = "1.000in"
    ET.SubElement(tb, _q("Height")).text = "0.300in"
    body.find(_q("Height")).text = f"{top + 0.3:.3f}in"
    return ET.tostring(root, encoding="unicode")


def _grow_the_per_record_row(rdl_xml: str) -> str:
    """Make the per-record row taller than the sheet it prints on.

    Each record then spills onto a second sheet that carries no ink, so the
    blank sheets SCALE with the row count: one per record. The reference shape
    is dirty AND twenty-five rows are dirtier — the case the relative legs
    have to survive."""
    from converter.generators.rdl import _printable_page_height

    root = ET.fromstring(rdl_xml)
    ET.register_namespace("", _NS[1:-1])
    tall = _printable_page_height(root) + 1.0
    grown = 0
    for tx in root.iter(_q("Tablix")):
        if tx.find(_q("DataSetName")) is None:
            continue
        rows = tx.find(_q("TablixBody")).find(_q("TablixRows"))
        for row in list(rows):
            height = row.find(_q("Height"))
            if height is None:
                continue
            if float((height.text or "0").replace("in", "")) > 1.0:
                height.text = f"{tall:.3f}in"
                grown += 1
    assert grown, "mutation found no per-record row to grow"
    return ET.tostring(root, encoding="unicode")


def _reference_gated_blank_legs(table):
    """The derivation this rail USED to carry, kept verbatim as the control
    arm: every blank leg gated on ``not ref["blanks"]``. The A/B below is the
    whole proof — the same measured table, called CLEAN by this rule and RED
    by the legs the rail now runs."""
    ref = table.get(REFERENCE_SHAPE)
    if ref is None:
        return []
    return [f"shape{s}:blank_sheet" for s in (0, 1, 25)
            if s in table and table[s]["blanks"] and not ref["blanks"]]


# ascii, a composite subset that does not decode at all, and an RTL script.
# The leg compares COUNTS of blank sheets, so it cannot be script-sensitive by
# construction — this proves that end to end anyway, because the measurement
# feeding it once was (a blank Greek sheet read as inked while the identical
# English artifact read as blank).
_SCRIPTS = [
    ("ascii", "Account Statement", "Prepared for the selected period"),
    ("greek", "Κατάσταση Λογαριασμού", "Ετοιμάστηκε για την επιλεγμένη περίοδο"),
    ("arabic", "كشف الحساب", "أعد للفترة المحددة"),
]


@pytest.mark.parametrize("script,masthead,standfirst", _SCRIPTS)
def test_rail_catches_a_blank_sheet_at_the_reference_shape(
        script, masthead, standfirst, tmp_path):
    """THE P0 hole: a report that blanks at rows=3 was never flagged.

    Every blank leg was gated on ``not ref["blanks"]`` and no leg measured the
    reference itself, so one blank sheet at three rows exempted a report from
    every blank check at every row count. Six production letters printed blank
    sheets inside a matrix this rail called clean.

    A/B on the same fixture: healthy letter -> no violation; the SAME letter
    with a sheet that paints nothing -> RED at the reference shape, while the
    old derivation still sees nothing. Run in three scripts, because a rail
    that is only right for English is the defect this campaign exists to
    kill."""
    render_rdl, pdf_overlaps = _render_harness()
    letter = _letter_xml(masthead=masthead, standfirst=standfirst)

    good = convert(letter)["rdl_xml"]
    clean, clean_table = _shape_violations(
        good, render_rdl, pdf_overlaps, tmp_path / "good", what=script)
    assert not clean, f"the {script} letter must start rail-clean: {clean}"
    assert not clean_table[REFERENCE_SHAPE]["blanks"], (
        "the reference render of a healthy letter carries no blank sheet: "
        f"{_format_table(clean_table)}")

    broken, broken_table = _shape_violations(
        _spacer_below_the_last_sheet(good), render_rdl, pdf_overlaps,
        tmp_path / "broken", what=f"{script}(doctored)")

    ref = broken_table[REFERENCE_SHAPE]
    # the mutation really did manufacture an EMPTY sheet at the reference
    assert ref["blanks"] and ref["classes"][ref["blanks"][0] - 1] == "empty", (
        "mutation did not produce a blank sheet at the reference shape: "
        f"{_format_table(broken_table)}")
    # ...the old derivation is blind to it. This assertion IS the gate hole.
    assert _reference_gated_blank_legs(broken_table) == [], (
        "the reference-gated derivation was supposed to MISS this; if it now "
        "sees it, this fixture no longer reproduces the reported hole")
    # ...and the rail goes red, naming the shape that is dirty.
    assert f"shape{REFERENCE_SHAPE}:blank_sheet" in broken, (
        "the rail did NOT catch a blank sheet at the reference shape — the "
        f"P0 hole is open again: {broken} / {_format_table(broken_table)}")


def test_a_dirty_reference_does_not_disable_the_other_shape_legs(tmp_path):
    """The second half of the fix: the relative legs must still work while a
    report's reference render is dirty.

    The mutation makes each record spill onto a blank sheet of its own, so the
    blank count SCALES: 1 at one row, 3 at three, 25 at twenty-five. The
    reference is dirty and twenty-five rows are dirtier — under the old
    derivation the dirty reference switched every blank leg off and the whole
    matrix scored clean."""
    render_rdl, pdf_overlaps = _render_harness()
    good = convert(_LETTER_XML)["rdl_xml"]
    broken, table = _shape_violations(
        _grow_the_per_record_row(good), render_rdl, pdf_overlaps, tmp_path,
        what="per-record(doctored)")

    assert len(table[25]["blanks"]) > len(table[REFERENCE_SHAPE]["blanks"]) > 0, (
        "mutation did not scale the blank sheets with the row count: "
        f"{_format_table(table)}")
    assert _reference_gated_blank_legs(table) == [], (
        "the old derivation was supposed to be switched off by the dirty "
        "reference; if it now fires, this fixture no longer reproduces it")
    assert f"shape{REFERENCE_SHAPE}:blank_sheet" in broken, broken
    assert "shape25:blank_sheet" in broken, (
        "a shape that blanks MORE than the reference must still be caught "
        f"while the reference is dirty: {broken} / {_format_table(table)}")
    # ...and a shape that blanks LESS than the reference is not double-reported
    assert "shape1:blank_sheet" not in broken, (
        "one row blanks FEWER sheets than three — that is the reference's own "
        f"defect, already reported once: {broken} / {_format_table(table)}")


def test_empty_declaration_carve_out_is_structural():
    """The blank legs exempt an artifact that declares no body content. The
    predicate reads the artifact — element counts, no text — so it says the
    same thing in every script; the rail pairs it with a preflight BLOCKER
    assertion so the carve-out can never hide a shippable report."""
    rdl = convert(_LETTER_XML)["rdl_xml"]
    assert _declares_body_content(rdl)

    root = ET.fromstring(rdl)
    ET.register_namespace("", _NS[1:-1])
    items = root.find(_q("Body")).find(_q("ReportItems"))
    for child in list(items):
        items.remove(child)
    assert not _declares_body_content(ET.tostring(root, encoding="unicode"))
    assert not _declares_body_content("<not-an-rdl")


# ---------------------------------------------------------------------------
# The emitter rule the fix installed, measured without the engine
# ---------------------------------------------------------------------------

def test_no_rows_notice_only_where_its_box_fits_the_page():
    """A NoRowsMessage is painted across the data region's WHOLE declared box
    (render-measured, see the mutation proof above). So the notice goes on
    regions whose box fits the page, and is withheld from the page-tall
    per-record sheet whose box does not — otherwise it manufactures the empty
    sheet this rail exists to forbid.

    The fit is judged on the sheet the region RENDERS on, not on the top of
    the body — a region declared after a page break starts a fresh sheet
    (tests/test_no_rows_notice_page_origin.py). Asserting placement against
    the emitter's own predicate keeps this test measuring PLACEMENT rather
    than a stale copy of the arithmetic behind it."""
    from converter.generators.rdl import (_no_rows_notice_has_room,
                                          _no_rows_reserved_bottom,
                                          _printable_page_height)

    rdl = convert(_LETTER_XML)["rdl_xml"]
    root = ET.fromstring(rdl)
    fits = _printable_page_height(root)
    assert fits > 0, "page chrome arithmetic returned nothing to fit into"

    seen = 0
    for tx in root.iter(_q("Tablix")):
        if tx.find(_q("DataSetName")) is None:
            continue
        if (tx.get("Name") or "").startswith("Tablix_CoverSheets"):
            continue
        seen += 1
        has_room = _no_rows_notice_has_room(root, tx, None, fits)
        has_msg = tx.find(_q("NoRowsMessage")) is not None
        assert has_msg == has_room, (
            f"{tx.get('Name')}: reserved bottom "
            f"{_no_rows_reserved_bottom(root, tx):.2f}in vs page {fits:.2f}in "
            f"but NoRowsMessage={has_msg}")
        # on THIS report nothing declares a break, so the conservative
        # body-top reading and the placement rule still agree exactly
        assert has_room == (_no_rows_reserved_bottom(root, tx) <= fits)
    assert seen, "fixture emitted no dataset-bound region"

    # the letter's record sheet is exactly the overflowing case
    assert "<NoRowsMessage>" not in rdl

    # ...while THE SAME report with a record box that fits still gets its
    # notice, so the net that made zero rows readable is not lost. One
    # declaration changes between the two arms: the record's height.
    short = convert(_letter_xml(record_height="1.20000"))["rdl_xml"]
    assert "<NoRowsMessage>" in short, (
        "a page-fitting data region must still say it came back empty")


# ---------------------------------------------------------------------------
# MUTATION PROOF — a sheet that is NOT BLANK and effectively EMPTY (L-6)
# ---------------------------------------------------------------------------

_PROSE = ("Thank you for submitting the annual package with the itemized "
          "accounting form; the budget and the breakdown have been reviewed "
          "and approved as submitted for this reporting period.")
_ENCLOSURE = ("Enclosed with this notice is the schedule of fees that applies "
              "to the reporting period, together with the remittance advice "
              "and the filing instructions for the next cycle.")
_CLOSING = "Sincerely,"


def _prose_letter_xml() -> bytes:
    """A per-record letter with a REAL sheet of prose, closing word included.

    ``_letter_xml`` above is deliberately skeletal, and a document whose every
    sheet carries two short lines has no full sheet to be starved beside — the
    sparse rule declines on it, correctly and uselessly. This fixture prints
    what a letter prints, so the mutation below produces a sheet that is
    orders of magnitude emptier than the letter it belongs to."""
    def _txt(name, text, x, y, w, h):
        return (f'<text name="{name}"><geometryInfo x="{x}" y="{y}" '
                f'width="{w}" height="{h}"/><textSegment>'
                f'<string><![CDATA[{text}]]></string></textSegment></text>')

    def _fld(name, source, x, y, w, h):
        return (f'<field name="{name}" source="{source}"><geometryInfo '
                f'x="{x}" y="{y}" width="{w}" height="{h}"/></field>')

    return (
        '<?xml version="1.0"?>'
        '<report name="SHAPE_PROSE_LETTER" DTDVersion="9.0.2.0.10">'
        '<data><dataSource name="Q_LET">'
        '<select><![CDATA[select acct_no, addressee from letter_queue]]>'
        '</select><group name="G_LET">'
        '<dataItem name="ACCT_NO" datatype="vchar2"/>'
        '<dataItem name="ADDRESSEE" datatype="vchar2"/>'
        '</group></dataSource></data>'
        '<layout><section name="main" width="8.50000">'
        '<body width="7.50000" height="9.40000">'
        '<repeatingFrame name="R_LET" source="G_LET" printDirection="down" '
        'maxRecordsPerPage="1" minWidowRecords="1" columnMode="no">'
        '<geometryInfo x="0.00000" y="0.00000" width="7.50000" '
        'height="9.00000"/>'
        '<generalLayout verticalElasticity="variable"/>'
        + _txt("B_BODY", _PROSE, 0.3, 0.4, 6.0, 0.2)
        + _fld("F_ADDR", "ADDRESSEE", 0.3, 3.0, 4.0, 0.2)
        + _fld("F_ACCT", "ACCT_NO", 0.3, 3.4, 4.0, 0.2)
        + _txt("B_CLOSING", _CLOSING, 0.3, 6.4, 0.7, 0.2)
        + _txt("B_ENC", _ENCLOSURE, 0.3, 7.2, 6.0, 0.2)
        + '</repeatingFrame></body></section></layout></report>').encode()


def _word_only_block_per_record(rdl_xml: str, word: str = _CLOSING) -> str:
    """Put the production defect back: a SECOND per-record block whose row
    resolves nothing but a word the letter already prints, one record to a
    sheet.

    The word is a CLONE of the letter's own textbox, so the two arms differ in
    nothing but WHERE it is printed — and the ruined sheets scale with the
    data exactly as the delivered letter's did."""
    import copy
    import math

    ET.register_namespace("", _NS[1:-1])
    root = ET.fromstring(rdl_xml)
    body = root.find(_q("Body"))
    items = body.find(_q("ReportItems"))
    page = root.find(_q("Page"))

    def _in(el, tag, default=0.0):
        try:
            return float((el.findtext(_q(tag)) or "").replace("in", ""))
        except ValueError:
            return default

    sheet = (_in(page, "PageHeight", 11.0) - _in(page, "TopMargin")
             - _in(page, "BottomMargin"))
    body_h = _in(body, "Height", 9.0)
    record = next((tx for tx in items.iter(_q("Tablix"))
                   if tx.findtext(_q("DataSetName"))), None)
    assert record is not None, "the letter declares no per-record region"
    src = next((tb for tb in root.iter(_q("Textbox"))
                if any(word in (v.text or "") for v in tb.iter(_q("Value")))),
               None)
    assert src is not None, "the letter does not print that word"

    block = copy.deepcopy(record)
    block.set("Name", "Tablix_WordBlock")
    for el in block.iter():
        if el.get("Name"):
            el.set("Name", el.get("Name") + "_WB")
    for n, cell in enumerate(block.iter(_q("CellContents"))):
        for child in list(cell):
            cell.remove(child)
        box = copy.deepcopy(src)
        box.set("Name", f"Tb_WordOnly_{n}")
        for tag in ("Top", "Left"):
            el = box.find(_q(tag))
            if el is not None:
                el.text = "0.000in"
        cell.append(box)
    for row in block.iter(_q("TablixRow")):
        height = row.find(_q("Height"))
        if height is not None:
            height.text = f"{sheet - 0.2:.3f}in"
    start = math.ceil(body_h / sheet) * sheet
    for tag, value in (("Top", f"{start:.3f}in"),
                       ("Height", f"{sheet - 0.2:.3f}in")):
        el = block.find(_q(tag))
        if el is None:
            el = ET.SubElement(block, _q(tag))
        el.text = value
    items.append(block)
    body.find(_q("Height")).text = f"{start + sheet - 0.2:.3f}in"
    return ET.tostring(root, encoding="unicode")


def _blank_column(table):
    """The blank column alone, kept as the control arm: the same measured
    table judged only by the legs that ask 'is there ink?'."""
    ref = table.get(REFERENCE_SHAPE)
    if ref is None:
        return []
    out = [f"shape{REFERENCE_SHAPE}:blank_sheet"] if ref["blanks"] else []
    return out + [f"shape{s}:blank_sheet" for s in (0, 1, 25)
                  if s in table and len(table[s]["blanks"]) > len(ref["blanks"])]


def test_rail_catches_a_sheet_that_is_not_blank_and_effectively_empty(tmp_path):
    """L-6, and the reason it had to exist.

    A judge found six sheets of a production letter carrying nothing but its
    closing word, inside a matrix this rail called clean — because every sheet
    HAD ink, so no blank leg could ever name them. A/B on one artifact: the
    letter is rail-clean; the same letter with its closing word re-emitted
    once per record goes RED, while the blank column stays silent on exactly
    the same render."""
    render_rdl, pdf_overlaps = _render_harness()
    good = convert(_prose_letter_xml())["rdl_xml"]

    clean, clean_table = _shape_violations(
        good, render_rdl, pdf_overlaps, tmp_path / "good", what="prose letter")
    assert not clean, f"the prose letter must start rail-clean: {clean}"
    assert not any(c["sparse"] for c in clean_table.values()), (
        "a healthy per-record letter carries no sparse sheet: "
        f"{_format_table(clean_table)}")

    broken, broken_table = _shape_violations(
        _word_only_block_per_record(good), render_rdl, pdf_overlaps,
        tmp_path / "broken", what="prose letter(doctored)")

    # the mutation really did produce the defect, and it SCALES with the data
    counts = {s: len(c["sparse"]) for s, c in broken_table.items()}
    assert counts == {0: 0, 1: 1, 3: 3, 25: 25}, (
        "the mutation did not reproduce the production shape (none at zero "
        f"rows, one per record after that): {_format_table(broken_table)}")
    # ...and every one of those sheets carries INK, which is why no blank leg
    # can see them. This assertion IS the gate hole.
    assert _blank_column(broken_table) == [], (
        "the blank column was supposed to MISS these sheets; if it now sees "
        f"them, this fixture no longer reproduces the hole: {broken_table}")
    assert all(c["blanks"] == [] for c in broken_table.values())
    # ...while the sparse leg names the reference shape AND the shape that
    # ruins more sheets than the reference does.
    assert f"shape{REFERENCE_SHAPE}:sparse_sheet" in broken, (
        f"the rail did not catch it at the reference shape: {broken} / "
        f"{_format_table(broken_table)}")
    assert "shape25:sparse_sheet" in broken, (
        "a shape that ruins MORE sheets than the reference must be named too: "
        f"{broken} / {_format_table(broken_table)}")
    # ...and a shape that ruins FEWER is the reference's own defect, reported
    # once, exactly as the blank column behaves.
    assert "shape1:sparse_sheet" not in broken and \
        "shape0:sparse_sheet" not in broken, broken

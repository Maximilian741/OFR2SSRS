"""
GENERALITY PROPERTIES — tests that measure generality ITSELF.

Every other rail asks "is this output correct?". This file asks the meta
question: "does the converter behave like a DECLARATION-SHAPED program, or
like a corpus-shaped one?" — the difference between a general solution and
memorization. Five properties, parametrized over every available corpus:

  P-A  TOTALITY      convert() never raises on anything the parser classifies
                     as a convertible report, and always yields a well-formed
                     RDL. A crash on input N+1 is the signature of code fit
                     to inputs 1..N. STRICT arm: a CONVERTIBLE report must
                     never take the generation-fallback path either — the
                     fallback (near-empty, XSD-valid) is reserved for
                     declared-unsupported kinds.
  P-B  NO SILENT     every declared layout field whose source column exists
       DROPS         in a query either LANDS in the RDL or is NAMED in the
                     fidelity/preflight disclosures. Silent loss is the
                     failure a memorized converter hides best (it "works" on
                     the reports it was shaped on).
  P-C  ISOLATION     no RDL contains a literal that exists only in a
                     DIFFERENT source file. Cross-contamination between
                     conversions in the same process = the memorization
                     smell made visible.
  P-D  DETERMINISM   convert() is a pure function of its input bytes:
                     converting the same source twice is byte-identical on
                     every surface (RDL, both mockups, fidelity, preflight).
                     Hidden mutable state is how corpus-shaping sneaks in.
  P-E  NAMED         anything the parser REJECTS is rejected with a named
       REJECTION     reason (source_kind / source.unsupported_kind + plain
                     message), never an exception and never a silent shrug.

Corpora are optional (machines without them contribute no cases); the
synthetic reports below keep every property alive everywhere, and each gate
carries an in-file mutation proof (a doctored bad artifact the gate MUST go
red on) so a structurally-blind check can never certify anything.

READ-ONLY over the corpora and converter: this file imports and measures,
it never edits. No customer report/field/label name appears here — the
synthetic sources use invented ZZQX* tokens.
"""
from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytest

from converter import convert
from converter.parsers.oracle_xml import parse_oracle_xml

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Corpus roster — same conventions as the fatal gates: entirely optional,
# env-overridable, tooling/output dirs (underscore-prefixed) skipped.
# ---------------------------------------------------------------------------
CORPORA = {
    "samples": REPO_ROOT / "samples" / "oracle",
    # Synthesized non-Latin-script fixtures (Greek UTF-8, Cyrillic
    # WINDOWS-1251, Arabic WINDOWS-1256, CJK UTF-8) — repo-bundled like the
    # samples leg, so the gate holds over every script/encoding forever.
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
}


def _collect_corpus(root: Path) -> List[Path]:
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.rglob("*.xml")):
        rel_parts = p.relative_to(root).parts[:-1]
        if any(part.startswith("_") for part in rel_parts):
            continue
        out.append(p)
    return out


def _corpus_params():
    params = []
    for corpus, root in CORPORA.items():
        for p in _collect_corpus(root):
            rel = p.relative_to(root).as_posix()
            params.append(pytest.param(p, id=f"{corpus}-{rel}"))
    return params


_PARAMS = _corpus_params()
_ALL_FILES: List[Path] = [p.values[0] for p in _PARAMS]

# ---------------------------------------------------------------------------
# Pure helper functions — each is the actual GATE for one property, factored
# out so the mutation proofs can hand them doctored artifacts directly.
# ---------------------------------------------------------------------------

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{5,}")
_NAMEATTR = re.compile(r'(?:name|defaultLabel|label)="([^"]*)"', re.I)


def _safe(name: str) -> str:
    """Mirror of the generator's identifier mangling (incl. leading-digit
    prefix) so a legitimately-bound field never reads as dropped."""
    out = re.sub(r"[^A-Za-z0-9_]", "_", name or "")
    if out and out[0].isdigit():
        out = "_" + out
    return out


def _declared_layout_columns(parsed) -> List[str]:
    """Source names of every VISIBLE data-bound layout field whose source is
    a REAL query column (declared by a <dataItem>). These are the fields the
    no-silent-drop contract covers: Oracle explicitly placed them on the
    page AND declared their data."""
    cols = {(getattr(it, "name", "") or "").lower()
            for q in (getattr(parsed, "queries", None) or [])
            for it in (getattr(q, "items", None) or [])
            if getattr(it, "name", "")}
    srcs: set = set()

    def walk(groups):
        for g in groups or []:
            for f in (getattr(g, "fields", None) or []):
                if (getattr(f, "kind", "") or "") != "field":
                    continue
                if not getattr(f, "visible", True):
                    continue
                s = (getattr(f, "source", "") or "").strip()
                if s:
                    srcs.add(s)
            walk(getattr(g, "children", None) or [])

    walk(getattr(parsed, "layout", None) or [])
    return sorted(s for s in srcs if s.lower() in cols)


def _landed(src: str, rdl_lower: str) -> bool:
    """True when the field's name is present in the RDL in any capacity a
    reader could reach its data through (dataset field, display reference,
    parameter, element text, or a Name= attribute)."""
    for cand in {src.lower(), _safe(src).lower()}:
        esc = re.escape(cand)
        if re.search(rf"fields!{esc}\b", rdl_lower):
            return True
        if f"<datafield>{cand}</datafield>" in rdl_lower:
            return True
        if f'name="{cand}"' in rdl_lower:
            return True
        if f">{cand}<" in rdl_lower:
            return True
        if re.search(rf"parameters!(p_)?{esc}\b", rdl_lower):
            return True
    return False


def _silent_drop_offenders(parsed, rdl_xml: str,
                           disclosure_text: str) -> List[str]:
    """P-B gate. Every declared layout column must LAND in the RDL or be
    NAMED in the disclosures (fidelity payload + preflight issues). Returns
    the fields that did neither — the silent third category that must not
    exist."""
    rdl_lower = (rdl_xml or "").lower()
    disc_lower = (disclosure_text or "").lower()
    offenders = []
    for src in _declared_layout_columns(parsed):
        if _landed(src, rdl_lower):
            continue
        if src.lower() in disc_lower or _safe(src).lower() in disc_lower:
            continue
        offenders.append(src)
    return offenders


def _foreign_tokens(rdl_tokens: frozenset, own_source_lower: str,
                    unique_owner: Dict[str, str],
                    self_key: str) -> List[Tuple[str, str]]:
    """P-C gate. Tokens in this RDL that are globally unique to a DIFFERENT
    source file and are not (even as a substring, any casing) present in this
    report's own source — i.e. material the generator could only have gotten
    from somewhere other than its input."""
    out = []
    for t in rdl_tokens & set(unique_owner):
        owner = unique_owner[t]
        if owner == self_key:
            continue
        if t in own_source_lower:
            # Generator-derived recasing of the report's OWN material
            # (e.g. a humanized label built from one of its identifiers).
            continue
        out.append((t, owner))
    return sorted(out)


def _surface_diffs(r1: dict, r2: dict) -> List[str]:
    """P-D gate. The output surfaces that differ between two conversions of
    the same bytes. Empty = deterministic."""
    diffs = []
    for key in ("rdl_xml", "mockup_html", "mockup_backend_html"):
        if (r1.get(key) or "") != (r2.get(key) or ""):
            diffs.append(key)
    for key in ("fidelity_report", "preflight"):
        j1 = json.dumps(r1.get(key), sort_keys=True, default=str)
        j2 = json.dumps(r2.get(key), sort_keys=True, default=str)
        if j1 != j2:
            diffs.append(key)
    return diffs


def _is_rejected(result: dict) -> bool:
    """Did the pipeline classify this source as NOT a convertible report?"""
    pf = result.get("preflight") or {}
    if pf.get("source_kind"):
        return True
    return any((i.get("rule") or "").startswith("source.unsupported")
               for i in (pf.get("issues") or []))


def _fallback_violation(a: dict) -> Optional[str]:
    """P-A gate, STRICT arm. The reason string when a source the parser
    classified as a CONVERTIBLE report took the generation-fallback path,
    else None.

    The fallback (crash net -> minimal RDL + conversion_error) is reserved
    for sources the pipeline REJECTS by name (declared-unsupported kinds —
    P-E's territory). On a convertible report it ships near-empty output
    that is still XSD-valid, which is WORSE than failing loudly: four wild
    reports shipped that way when one tree walk crashed on an XML Comment
    node in the emitted tree."""
    ce = a.get("conversion_error")
    if ce and not a.get("rejected"):
        return str(ce)
    return None


def _named_rejection(result: dict) -> Optional[str]:
    """P-E gate. The NAMED reason a rejected source carries, or None when the
    rejection is unnamed (which is a failure: a reject must always explain
    itself in plain language)."""
    pf = result.get("preflight") or {}
    kind = (pf.get("source_kind") or "").strip()
    msg = (pf.get("source_kind_message") or "").strip()
    if kind and msg:
        return f"{kind}: {msg}"
    for issue in (pf.get("issues") or []):
        rule = (issue.get("rule") or "").strip()
        m = (issue.get("message") or "").strip()
        if rule.startswith("source.unsupported") and m:
            return f"{rule}: {m}"
    return None


# ---------------------------------------------------------------------------
# Per-file analysis: ONE pair of convert() calls per corpus file, shared by
# every property test. Only small derived data is retained (no corpus text
# and no RDLs are kept in memory).
# ---------------------------------------------------------------------------

_ANALYSIS: Dict[str, dict] = {}


def _analysis(path: Path) -> dict:
    key = str(path)
    if key in _ANALYSIS:
        return _ANALYSIS[key]
    data = path.read_bytes()
    a: dict = {"exception": None}
    try:
        r1 = convert(data, target_db="oracle")
        r2 = convert(data, target_db="oracle")
    except Exception as e:  # noqa: BLE001 — the property IS "never raises"
        a["exception"] = f"{type(e).__name__}: {e}"
        _ANALYSIS[key] = a
        return a

    rdl = r1.get("rdl_xml") or ""
    a["rdl_nonempty"] = bool(rdl.strip())
    try:
        ET.fromstring(rdl.encode("utf-8"))
        a["rdl_wellformed"] = True
    except Exception as e:  # noqa: BLE001
        a["rdl_wellformed"] = False
        a["rdl_parse_error"] = f"{type(e).__name__}: {e}"
    a["conversion_error"] = r1.get("conversion_error")
    a["surface_diffs"] = _surface_diffs(r1, r2)
    a["rejected"] = _is_rejected(r1)
    a["named_rejection"] = _named_rejection(r1)
    a["rdl_tokens"] = frozenset(
        t.lower() for t in _IDENT.findall(rdl))

    # P-B needs the DECLARED source truth: a fresh parse (convert() mutates
    # its own parse — cursor-formula columns, forwarded params — which is
    # exactly the derived state the contract must NOT be measured against).
    a["silent_drops"] = []
    a["n_declared_layout_columns"] = 0
    if not a["rejected"]:
        try:
            parsed = parse_oracle_xml(data)
        except Exception:  # noqa: BLE001 — P-A covers crashes; degrade here
            parsed = None
        if parsed is not None:
            disclosure = (
                json.dumps(r1.get("fidelity_report"), default=str)
                + json.dumps((r1.get("preflight") or {}).get("issues") or [],
                             default=str))
            a["n_declared_layout_columns"] = len(
                _declared_layout_columns(parsed))
            a["silent_drops"] = _silent_drop_offenders(parsed, rdl,
                                                       disclosure)
    _ANALYSIS[key] = a
    return a


# ---------------------------------------------------------------------------
# P-C support: a corpus-wide ownership map of name/label-derived identifier
# tokens. A token is FORBIDDEN for report B when it exists (even as a
# substring) in exactly ONE source file and that file is not B. Generator
# vocabulary (any token that literally appears in the converter's own code)
# is exempt — a separate repo guard already forbids customer names there.
# ---------------------------------------------------------------------------

_OWNER_MAP: Optional[Dict[str, str]] = None


def _repo_vocab() -> set:
    vocab: set = set()
    for py in (REPO_ROOT / "backend" / "converter").rglob("*.py"):
        try:
            txt = py.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        vocab.update(t.lower() for t in _IDENT.findall(txt))
    return vocab


def _owner_map() -> Dict[str, str]:
    """token -> str(path) for tokens whose SOURCE-TEXT presence (substring,
    case-insensitive) is unique to exactly one corpus file."""
    global _OWNER_MAP
    if _OWNER_MAP is not None:
        return _OWNER_MAP
    vocab = _repo_vocab()
    candidates: Dict[str, set] = {}
    texts: List[Tuple[str, str]] = []
    for p in _ALL_FILES:
        txt = p.read_bytes().decode("utf-8", errors="replace").lower()
        texts.append((str(p), txt))
        for value in _NAMEATTR.findall(txt):
            for t in _IDENT.findall(value):
                if t not in vocab:
                    candidates.setdefault(t, set())
    for key, txt in texts:
        for t, owners in candidates.items():
            if len(owners) < 2 and t in txt:
                owners.add(key)
    _OWNER_MAP = {t: next(iter(o))
                  for t, o in candidates.items() if len(o) == 1}
    return _OWNER_MAP


# ---------------------------------------------------------------------------
# Synthetic sources (invented ZZQX* names — customer-free) so every property
# stays alive on machines without any corpus, and so the isolation test can
# assert with KNOWN unique markers.
# ---------------------------------------------------------------------------

def _synth(report: str, marker: str) -> bytes:
    m = marker
    return f"""<?xml version="1.0"?>
<report name="{report}" DTDVersion="9.0.2.0.10">
<data>
 <userParameter name="P_{m}_FILTER" datatype="character" width="20"/>
 <dataSource name="Q_MAIN">
  <select><![CDATA[SELECT {m}_widget_code, {m}_gadget_label, {m}_amount
FROM {m}_things WHERE kind = :P_{m}_FILTER]]></select>
  <group name="G_MAIN">
   <dataItem name="{m}_widget_code" datatype="vchar2" defaultLabel="Widget"/>
   <dataItem name="{m}_gadget_label" datatype="vchar2" defaultLabel="Gadget"/>
   <dataItem name="{m}_amount" datatype="number" defaultLabel="Amount"/>
  </group>
 </dataSource>
</data>
<layout>
 <section name="main" width="8.5" height="11">
  <body>
   <frame name="M_frame" x="0" y="0" width="8" height="10">
    <repeatingFrame name="R_1" source="G_MAIN" x="0" y="0.5" width="8"
      height="0.5" printDirection="down">
     <field name="F_{m}_widget_code" source="{m}_widget_code"
       x="0" y="0" width="2.5" height="0.2"/>
     <field name="F_{m}_gadget_label" source="{m}_gadget_label"
       x="2.7" y="0" width="2.5" height="0.2"/>
     <field name="F_{m}_amount" source="{m}_amount"
       x="5.4" y="0" width="1.5" height="0.2"/>
    </repeatingFrame>
   </frame>
  </body>
 </section>
</layout>
</report>""".encode("utf-8")


SYNTH_A = _synth("ZZQXA_PROBE_REPORT", "zzqxa")
SYNTH_B = _synth("ZZQXB_PROBE_REPORT", "zzqxb")

_GARBAGE_INPUTS = [
    pytest.param(b"", id="empty-bytes"),
    pytest.param(b"this is not xml at all, just prose", id="prose"),
    pytest.param(b"<?xml version='1.0'?><notareport><x/></notareport>",
                 id="xml-not-a-report"),
    pytest.param(b"\x00\x01\x02\xfe\xff" * 64, id="binary-junk"),
    pytest.param(b"<report name='TRUNCATED' ", id="truncated-open-tag"),
]


# ===========================================================================
# P-A  TOTALITY — conversion never raises, output is well-formed
# ===========================================================================

@pytest.mark.parametrize("path", _PARAMS)
def test_pa_convert_never_raises_and_yields_wellformed_rdl(path: Path):
    a = _analysis(path)
    assert a["exception"] is None, (
        f"convert() RAISED on {path.name}: {a['exception']} — totality "
        f"broken; a general converter degrades, it never crashes")
    assert a["rdl_nonempty"], f"{path.name}: convert() returned an empty RDL"
    assert a["rdl_wellformed"], (
        f"{path.name}: emitted RDL is not well-formed XML "
        f"({a.get('rdl_parse_error')})")
    # A generation fallback is allowed ONLY as a disclosed degradation.
    ce = a["conversion_error"]
    assert ce is None or (isinstance(ce, str) and ce.strip()), (
        f"{path.name}: conversion_error must be None or a NAMED reason, "
        f"got {ce!r}")
    # STRICTER: ...and only for sources the parser REJECTS. A source
    # classified as a CONVERTIBLE report must never take the fallback path
    # at all — the fallback ships near-empty-but-XSD-valid output, which is
    # worse than failing loudly (see _fallback_violation).
    v = _fallback_violation(a)
    assert v is None, (
        f"{path.name}: classified CONVERTIBLE yet degraded to the fallback "
        f"RDL ({v}) — the fallback is legal only for declared-unsupported "
        f"kinds")


# ===========================================================================
# P-B  NO SILENT DROPS — declared layout columns land or are named
# ===========================================================================

@pytest.mark.parametrize("path", _PARAMS)
def test_pb_no_silent_layout_column_drops(path: Path):
    a = _analysis(path)
    if a["exception"] is not None:
        pytest.skip("P-A already fails this file")
    if a["rejected"]:
        pytest.skip("not classified as a convertible report (P-E covers it)")
    assert not a["silent_drops"], (
        f"{path.name}: {len(a['silent_drops'])} declared layout column(s) "
        f"neither land in the RDL nor are named in the fidelity/preflight "
        f"disclosures — SILENT drop(s): {a['silent_drops'][:10]}")


# ===========================================================================
# P-C  ISOLATION — no cross-source contamination
# ===========================================================================

@pytest.mark.parametrize("path", _PARAMS)
def test_pc_rdl_contains_no_other_sources_literals(path: Path):
    a = _analysis(path)
    if a["exception"] is not None:
        pytest.skip("P-A already fails this file")
    owners = _owner_map()
    own_lower = path.read_bytes().decode("utf-8", errors="replace").lower()
    foreign = _foreign_tokens(a["rdl_tokens"], own_lower, owners, str(path))
    assert not foreign, (
        f"{path.name}: RDL contains literal(s) unique to a DIFFERENT source "
        f"file — cross-contamination (the memorization smell): "
        f"{[(t, Path(o).name) for t, o in foreign[:8]]}")


def test_pc_sequential_conversions_are_isolated_and_stable():
    """Convert A, then B, then A again in one process: B must carry nothing
    of A, and A's output must not shift because B was converted in between
    (order-dependence = hidden shared state)."""
    ra1 = convert(SYNTH_A, target_db="oracle")["rdl_xml"]
    rb = convert(SYNTH_B, target_db="oracle")["rdl_xml"]
    ra2 = convert(SYNTH_A, target_db="oracle")["rdl_xml"]
    assert "zzqxa" in ra1.lower() and "zzqxb" in rb.lower(), \
        "sanity: each synthetic report must carry its own marker"
    assert "zzqxa" not in rb.lower(), \
        "report B's RDL contains report A's unique marker — contamination"
    assert "zzqxb" not in ra2.lower(), \
        "report A's RDL contains report B's unique marker — contamination"
    assert ra1 == ra2, \
        "converting B in between changed A's output — hidden shared state"


# ===========================================================================
# P-D  DETERMINISM — convert() is a pure function of its input bytes
# ===========================================================================

@pytest.mark.parametrize("path", _PARAMS)
def test_pd_convert_is_deterministic(path: Path):
    a = _analysis(path)
    if a["exception"] is not None:
        pytest.skip("P-A already fails this file")
    assert not a["surface_diffs"], (
        f"{path.name}: converting the same bytes twice differs on "
        f"{a['surface_diffs']} — convert() is not a pure function of its "
        f"input")


def test_pd_synthetic_deterministic():
    r1 = convert(SYNTH_A, target_db="oracle")
    r2 = convert(SYNTH_A, target_db="oracle")
    assert _surface_diffs(r1, r2) == []


# ===========================================================================
# P-E  NAMED REJECTION — rejects always explain themselves, never throw
# ===========================================================================

@pytest.mark.parametrize("path", _PARAMS)
def test_pe_corpus_rejections_carry_a_named_reason(path: Path):
    a = _analysis(path)
    if a["exception"] is not None:
        pytest.skip("P-A already fails this file")
    if not a["rejected"]:
        pytest.skip("classified as convertible")
    assert a["named_rejection"], (
        f"{path.name}: the parser rejected this source but gave NO named "
        f"reason — a reject must always explain itself")


@pytest.mark.parametrize("payload", _GARBAGE_INPUTS)
def test_pe_hostile_input_never_raises_and_is_named(payload: bytes):
    try:
        result = convert(payload, target_db="oracle")
    except Exception as e:  # noqa: BLE001
        pytest.fail(f"convert() RAISED on hostile input: "
                    f"{type(e).__name__}: {e}")
    assert isinstance(result, dict) and (result.get("rdl_xml") or "").strip()
    reason = _named_rejection(result)
    assert reason, (
        "hostile non-report input was neither converted nor rejected with a "
        "named reason — the silent shrug is exactly what this gate forbids")


def test_pe_truncated_real_source_never_raises():
    """Cut a real (or synthetic) report off mid-file: the converter must
    either still convert it or reject it BY NAME — never crash."""
    base = _ALL_FILES[0].read_bytes() if _ALL_FILES else SYNTH_A
    for cut in (len(base) // 3, len(base) // 2, len(base) - 7):
        try:
            result = convert(base[:cut], target_db="oracle")
        except Exception as e:  # noqa: BLE001
            pytest.fail(f"convert() RAISED on a truncated source (cut at "
                        f"{cut} bytes): {type(e).__name__}: {e}")
        assert isinstance(result, dict)
        ok_convert = bool((result.get("rdl_xml") or "").strip())
        assert ok_convert or _named_rejection(result), (
            f"truncated source (cut at {cut}) was neither converted nor "
            f"rejected by name")


# ===========================================================================
# MUTATION PROOFS — every gate above must go RED on a doctored bad artifact.
# A gate that cannot fail certifies nothing (feedback: prove the gate can
# fail before saying "fixed").
# ===========================================================================

def _synth_converted():
    parsed = parse_oracle_xml(SYNTH_A)
    result = convert(SYNTH_A, target_db="oracle")
    disclosure = (json.dumps(result.get("fidelity_report"), default=str)
                  + json.dumps((result.get("preflight") or {})
                               .get("issues") or [], default=str))
    return parsed, result["rdl_xml"], disclosure


def test_mutation_pb_gate_goes_red_on_a_silent_drop():
    parsed, rdl, disclosure = _synth_converted()
    # Honest output: clean (the synthetic report's columns all land).
    assert _silent_drop_offenders(parsed, rdl, disclosure) == []
    # Doctor: scrub one declared column out of the RDL *and* out of every
    # disclosure — the exact silent-loss shape the gate exists to catch.
    victim = "zzqxa_gadget_label"
    bad_rdl = re.sub(victim, "zz_gone", rdl, flags=re.I)
    bad_disclosure = re.sub(victim, "zz_gone", disclosure, flags=re.I)
    offenders = _silent_drop_offenders(parsed, bad_rdl, bad_disclosure)
    assert any(victim == o.lower() for o in offenders), (
        f"P-B gate FAILED TO GO RED on a doctored silent drop of {victim}: "
        f"offenders={offenders}")
    # Disclosure alone (column still absent from the RDL) must satisfy it:
    disclosed = bad_disclosure + f' {{"dropped": ["{victim}"]}}'
    assert not _silent_drop_offenders(parsed, bad_rdl, disclosed), \
        "P-B gate must accept a drop that IS disclosed by name"


def test_mutation_pc_gate_goes_red_on_injected_foreign_literal():
    tokens = frozenset({"zzqxb_gadget_label", "zzqxa_widget_code"})
    owners = {"zzqxb_gadget_label": "OTHER_FILE",
              "zzqxa_widget_code": "SELF_FILE"}
    own_src = "field zzqxa_widget_code only"
    hits = _foreign_tokens(tokens, own_src, owners, "SELF_FILE")
    assert hits == [("zzqxb_gadget_label", "OTHER_FILE")], (
        f"P-C gate FAILED TO GO RED on an injected foreign literal: {hits}")
    # Specificity: the same token is EXEMPT when the report's own source
    # contains it (any casing, even inside a longer identifier).
    own_src2 = own_src + " and per_zzqxb_gadget_label_total"
    assert _foreign_tokens(tokens, own_src2, owners, "SELF_FILE") == []


def test_mutation_pd_gate_goes_red_on_differing_surfaces():
    r1 = {"rdl_xml": "<Report/>", "mockup_html": "<div/>",
          "mockup_backend_html": "<div/>", "fidelity_report": {"score": 1.0},
          "preflight": {"verdict": "READY"}}
    r2 = dict(r1, rdl_xml="<Report>x</Report>",
              fidelity_report={"score": 0.9})
    diffs = _surface_diffs(r1, r2)
    assert diffs == ["rdl_xml", "fidelity_report"], (
        f"P-D gate FAILED TO GO RED on differing surfaces: {diffs}")
    assert _surface_diffs(r1, dict(r1)) == []


def test_mutation_pa_gate_goes_red_on_fallback_for_convertible(monkeypatch):
    """The strict P-A arm must catch THE EXACT CLASS it was written for: a
    generation crash on a convertible report degrading to the disclosed
    fallback RDL (e.g. a tree walk tripping over a non-element XML node)."""
    import converter as conv_pkg
    # Honest arm: the synthetic convertible report converts for real.
    honest = convert(SYNTH_A, target_db="oracle")
    assert not _is_rejected(honest), "sanity: SYNTH_A must be convertible"
    assert _fallback_violation(
        {"conversion_error": honest.get("conversion_error"),
         "rejected": _is_rejected(honest)}) is None
    # Doctored arm — reproduce the class end-to-end: generation raises the
    # very exception a Comment/PI node's function-valued .tag produces,
    # convert()'s crash net degrades to the minimal fallback, the output
    # stays XSD-valid... and the gate MUST go red anyway.
    def _boom(report, target_db="oracle"):
        raise AttributeError("'function' object has no attribute 'split'")
    monkeypatch.setattr(conv_pkg, "generate_rdl", _boom)
    degraded = conv_pkg.convert(SYNTH_A, target_db="oracle")
    a = {"conversion_error": degraded.get("conversion_error"),
         "rejected": _is_rejected(degraded)}
    assert a["conversion_error"], \
        "sanity: the crash net must disclose the generation failure"
    assert not a["rejected"], \
        "sanity: the source is still classified convertible"
    assert _fallback_violation(a), (
        "P-A strict gate FAILED TO GO RED on a convertible report degraded "
        "to the fallback RDL — near-empty XSD-valid output would ship "
        "silently")
    # Specificity: a DECLARED-unsupported kind may still disclose+degrade.
    assert _fallback_violation(
        {"conversion_error": "RDL generation: x", "rejected": True}) is None
    assert _fallback_violation(
        {"conversion_error": None, "rejected": False}) is None


def test_mutation_pe_gate_goes_red_on_an_unnamed_rejection():
    # A real hostile convert IS named...
    named = convert(b"not xml", target_db="oracle")
    assert _named_rejection(named)
    # ...but strip the reason out of the payload and the gate must go red.
    bad = {"preflight": {
        "verdict": "BLOCKER",
        "issues": [i for i in (named.get("preflight") or {}).get("issues", [])
                   if not (i.get("rule") or "").startswith("source.")],
    }}
    assert _named_rejection(bad) is None, (
        "P-E gate FAILED TO GO RED on a rejection stripped of its named "
        "reason")
    # A kind without a message is NOT a named reason either.
    assert _named_rejection(
        {"preflight": {"source_kind": "x", "issues": []}}) is None

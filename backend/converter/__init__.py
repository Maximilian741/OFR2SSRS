"""
Top-level conversion pipeline.

Each module is implemented in its own file so the agents can build them
independently. This file just glues them together and returns a single dict
that the frontend can consume.
"""
from __future__ import annotations

from typing import Dict, Any, Optional, Iterable

from .models import ParsedReport
from .parsers.oracle_xml import parse_oracle_xml
from .translators.plsql_to_tsql import translate_report
from .generators.rdl import generate_rdl
from .preview.html_mockup import render_mockup
from .preview.live_data import run_query
from .validators.tsql_check import validate_report
from .validators.rdl_check import validate_rdl
from .validators.preflight import preflight_audit
from .validators.sql_syntax import differential_issues as sql_differential_issues
from .validators.coverage import coverage_issues, unaccounted_fields
from .validators.layout_audit import audit_layout
from .deployment import build_checklist
from .audit import build_audit_trail
from .fidelity import build_fidelity_report
from .ai_assist import build_prompts
from .bursting import detect_bursting, build_burst_query, build_powershell_dds_script, build_email_burst_query, build_email_powershell_script, build_service_account_checklist, build_email_config_template
from .subreports import detect_subreport_links, is_drillthrough_only


def _dataset_command_texts(rdl_xml: str) -> dict:
    """``{dataset name: CommandText}`` read back out of the emitted RDL —
    the exact SQL that will run on the server, after every rewrite pass."""
    import xml.etree.ElementTree as _ET
    out: dict = {}
    try:
        root = _ET.fromstring(rdl_xml.encode("utf-8"))
    except Exception:  # noqa: BLE001
        return out
    ns = root.tag.split("}")[0][1:] if "}" in root.tag else ""

    def q(tag):
        return f"{{{ns}}}{tag}" if ns else tag

    for ds in root.iter(q("DataSet")):
        qe = ds.find(q("Query"))
        if qe is None:
            continue
        out[ds.get("Name") or ""] = qe.findtext(q("CommandText")) or ""
    return out


def _fallback_rdl(parsed, error: str) -> str:
    """A minimal, well-formed, uploadable RDL used when generation hits an
    unexpected layout -- the user still gets a downloadable .rdl plus a clear
    note instead of a hard failure."""
    import html as _html
    name = _html.escape(getattr(parsed, "name", "") or "Report")
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<Report xmlns="http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition">'
        '<Body><ReportItems>'
        '<Textbox Name="Tb_ConversionIncomplete"><Paragraphs><Paragraph><TextRuns><TextRun>'
        f'<Value>Automatic conversion of &quot;{name}&quot; was incomplete '
        '(an unfamiliar layout). Datasets and parameters may still be present; '
        'open this in Report Builder to finish the layout.</Value>'
        '</TextRun></TextRuns></Paragraph></Paragraphs>'
        '<Top>0.2in</Top><Left>0.2in</Left><Height>0.8in</Height><Width>6.5in</Width>'
        '</Textbox></ReportItems><Height>1.5in</Height></Body>'
        '<Width>7in</Width>'
        '<Page><PageHeight>11in</PageHeight><PageWidth>8.5in</PageWidth></Page>'
        '</Report>'
    )


def _fallback_preview(parsed, error: str) -> str:
    """A friendly 'preview unavailable' card used when the mockup renderer hits
    an unexpected layout. The RDL is still produced separately."""
    import html as _html
    name = _html.escape(getattr(parsed, "name", "") or "report")
    return (
        '<div style="padding:24px;font-family:system-ui,sans-serif;color:#334155;">'
        '<h3 style="color:#0a2540;margin:0 0 8px;">Preview unavailable</h3>'
        f'<p>The layout preview for <b>{name}</b> could not be rendered, but the '
        'RDL was still generated &mdash; open it in Report Builder to view the layout.</p>'
        '<pre style="background:#f8fafc;border:1px solid #e2e8f0;padding:8px 10px;'
        'border-radius:6px;font-size:12px;color:#64748b;overflow:auto;">'
        f'{_html.escape(error)}</pre></div>'
    )


def _merge_user_images(parsed: ParsedReport, images: Dict[str, Any]) -> None:
    """Fold user-uploaded images into the parsed report's embedded images.

    ``images`` maps a SLOT (the layout image placeholder name, or ``"*"``
    for "apply to every placeholder") to ``(mime_type, base64_data)``.
    Uploads REPLACE a same-named image parsed from the XML, so a user can
    swap a low-quality export asset for a clean one.
    """
    import base64 as _b64
    from .models import EmbeddedImage
    existing = {im.id.upper(): im for im in (parsed.embedded_images or [])}
    for slot, payload in (images or {}).items():
        try:
            mime, b64 = payload
            hex_data = _b64.b64decode(b64).hex()
        except Exception:  # noqa: BLE001 -- bad upload payloads are skipped
            continue
        if slot == "*":
            im = EmbeddedImage(id="USER_IMAGE_ALL", mime_type=mime,
                               hex_data=hex_data)
            im.wildcard = True
            parsed.embedded_images.append(im)
            continue
        key = slot.upper()
        if key in existing:
            existing[key].hex_data = hex_data
            existing[key].mime_type = mime
        else:
            parsed.embedded_images.append(
                EmbeddedImage(id=slot, mime_type=mime, hex_data=hex_data))


def _image_slots(parsed: ParsedReport) -> list:
    """Every image placeholder in the layout + whether bytes are available
    (from the XML export or an upload). Drives the 'Report images' UI."""
    have = {im.id.upper() for im in (parsed.embedded_images or [])
            if (im.hex_data or "").strip()}
    wildcard = any(getattr(im, "wildcard", False)
                   for im in (parsed.embedded_images or []))
    slots, seen = [], set()

    def walk(g):
        for f in (getattr(g, "fields", None) or []):
            if getattr(f, "kind", "") != "image":
                continue
            nm = getattr(f, "name", "") or getattr(f, "image_id", "")
            if not nm or nm.upper() in seen:
                continue
            seen.add(nm.upper())
            key = (getattr(f, "image_id", "") or nm).upper()
            slots.append({
                "name": nm,
                "width": round(float(getattr(f, "width", 0) or 0), 2),
                "height": round(float(getattr(f, "height", 0) or 0), 2),
                "has_data": key in have or wildcard,
            })
        for c in (getattr(g, "children", None) or []):
            walk(c)

    for lg in (parsed.layout or []):
        walk(lg)
    return slots


def _classify_source_artifact(parsed) -> Optional[Dict[str, str]]:
    """Recognize PARTIAL Oracle Reports XML — files that are NOT a complete,
    convertible report. These are real exports people drop in (wild-corpus
    verified) and deserve a plain answer, not a near-blank RDL.

      * customization overlay: <customize> blocks / a handful of styling
        fields, no <data>. Patches an existing report on the server.
      * data-model-only: <data> with queries but ZERO layout. The query
        side of a report saved without its paper layout.

    A normal report (has both data and layout) returns None — unchanged."""
    raw = (getattr(parsed, "raw_xml", "") or "")
    n_queries = len(getattr(parsed, "queries", None) or [])

    def _count_fields(g) -> int:
        t = len(getattr(g, "fields", None) or [])
        for c in (getattr(g, "children", None) or []):
            t += _count_fields(c)
        return t

    layout = getattr(parsed, "layout", None) or []
    total_fields = sum(_count_fields(lg) for lg in layout)
    has_data = "<data>" in raw or "<data " in raw
    has_customize = "<customize" in raw

    if has_customize and not has_data:
        return {
            "kind": "customization_overlay",
            "message": (
                "This is an Oracle Reports CUSTOMIZATION overlay (<customize> "
                "blocks), not a complete report — it patches an existing "
                "report's objects at runtime. Convert the FULL report XML "
                "instead; apply these customizations as edits in Report "
                "Builder afterward."),
        }
    if has_data and n_queries and total_fields == 0 and not layout:
        return {
            "kind": "data_model_only",
            "message": (
                "This file has a data model (queries/groups) but NO paper "
                "layout — it's the data side of a report saved without its "
                "layout. The dataset(s) converted, but there's nothing to "
                "render. Re-export the report WITH its layout for a 1:1 "
                "conversion."),
        }
    if not has_data and total_fields and total_fields <= 4 and not n_queries:
        return {
            "kind": "layout_fragment",
            "message": (
                "This looks like a layout fragment / advanced-layout example "
                "(a few fields, no data model). Drop the complete report XML "
                "for a full conversion."),
        }
    return None


def convert(xml_bytes: bytes, target_db: str = "oracle",
            images: Optional[Dict[str, Any]] = None,
            extra_param_names: Optional[Iterable[str]] = None,
            deep_verify: bool = False,
            label_overrides: Optional[Dict[str, str]] = None
            ) -> Dict[str, Any]:
    """End-to-end conversion. Returns a dict ready to ship to the frontend.

    Parameters
    ----------
    xml_bytes:
        The Oracle Reports XML payload.
    target_db:
        Which RDL backend variant to emit. ``"oracle"`` (default) preserves
        the original Oracle SQL inside <CommandText> with ``:P_PARAM`` bind
        vars and emits an ``OracleClient`` DataProvider so the user can host
        the report in SSRS but still query their Oracle backend. ``"sqlserver"``
        emits the translated T-SQL with ``@P_PARAM`` bind vars and a ``SQL``
        DataProvider, which is the legacy behavior.
    images:
        Optional user-uploaded images: {slot_name_or_*: (mime, base64)}.
        Merged with any images embedded in the Oracle export itself; both
        end up as RDL <EmbeddedImages> AND in the HTML mockup.
    """
    target_db = (target_db or "oracle").lower()
    if target_db not in ("oracle", "sqlserver"):
        target_db = "oracle"

    parsed: ParsedReport = parse_oracle_xml(xml_bytes)
    # EMPTY-PARSE GATE (wild-corpus): a document that parses but yields no
    # data model, no layout, and no parameters is not a convertible report
    # — without this, every crash-safety fallback compounds into a
    # confident empty RDL with a vacuous fidelity of 1.0. NEVER raise
    # (crash-safety contract) — the verdict/fidelity are forced honest
    # after the pipeline runs (see _unsupported_note below).
    _unsupported_note = ""
    if (not (parsed.queries or []) and not (parsed.layout or [])
            and not (parsed.parameters or [])):
        _unsupported_note = (
            "The XML parsed but contains no data model, layout, or "
            "parameters — it is not a convertible Oracle Reports "
            "definition."
            + (f" Parser notes: {'; '.join(parsed.warnings[:3])}"
               if parsed.warnings else ""))
    if images:
        _merge_user_images(parsed, images)

    # CURSOR FORMULAS -> REAL COLUMNS: a single-fetch CF_ cursor keyed on
    # current-row binds translates to a scalar correlated subquery appended
    # to its owning query (wrapped as SELECT O.*, (...) AS CF_X FROM (orig)
    # O — the alias makes outer correlation unambiguous). The formula then
    # COMPUTES from the real dataset instead of shipping a NULL stub.
    try:
        import re as _re
        from .translators.plsql_formula import cursor_formula_to_subquery
        from .models import DataItem as _DI
        _pn = [p.name for p in (parsed.parameters or []) if p.name]
        _byq: Dict[int, Any] = {}
        for _f in (parsed.formulas or []):
            if getattr(_f, "agg_function", "") or \
                    getattr(_f, "wired_as_column", False):
                continue
            _b = getattr(_f, "plsql_body", "") or ""
            if "cursor" not in _b.lower() or not (_f.name or "").strip():
                continue
            for _q in (parsed.queries or []):
                _cols = [it.name for it in (_q.items or []) if it.name]
                _sub = cursor_formula_to_subquery(_b, _cols, _pn)
                if _sub and "O." in _sub:
                    _byq.setdefault(id(_q), (_q, []))[1].append((_f, _sub))
                    break
        for _q, _pairs in _byq.values():
            _sels = ",\n  ".join(
                f"{s} AS {_re.sub(r'[^A-Za-z0-9_]', '_', f.name)}"
                for f, s in _pairs)
            _q.sql = ("SELECT O.*,\n  " + _sels + "\nFROM (\n"
                      + (_q.sql or "").rstrip().rstrip(";") + "\n) O")
            for _f, _s in _pairs:
                _q.items.append(_DI(name=_f.name))
                _f.wired_as_column = True
            _q.add_warning(
                f"{len(_pairs)} cursor formula(s) translated to correlated "
                f"subquery columns (first-fetch semantics preserved via "
                f"KEEP DENSE_RANK FIRST).")
    except Exception:  # noqa: BLE001 -- cursor wiring must never sink convert
        pass

    # Drill-through TARGET parameters the caller (e.g. a parent report linking
    # to this one as a sub-report) forwards. Declare each as a HIDDEN parameter
    # if the report doesn't already -- an undeclared target parameter is a hard
    # SSRS error the instant the parent's <Drillthrough> link is clicked.
    if extra_param_names:
        from .models import ReportParameter
        _have = {(p.name or "").upper() for p in (parsed.parameters or [])}
        for _p in extra_param_names:
            if _p and _p.upper() not in _have:
                parsed.parameters.append(
                    ReportParameter(name=_p, label=_p, display=False))
                _have.add(_p.upper())

    # Translation (Oracle SQL/PLSQL -> T-SQL) is an enhancement; a failure must
    # not sink the conversion -- the original Oracle SQL still passes through.
    try:
        translate_report(parsed)
    except Exception:  # noqa: BLE001
        pass

    # RDL generation + preview render are wrapped so an unfamiliar layout
    # degrades to a clear note + minimal-but-valid output instead of crashing.
    conversion_error = None
    try:
        rdl_xml = generate_rdl(parsed, target_db=target_db)
    except Exception as e:  # noqa: BLE001
        conversion_error = f"RDL generation: {type(e).__name__}: {e}"
        rdl_xml = _fallback_rdl(parsed, conversion_error)
    # Render BOTH preview modes so the UI can toggle between
    # frontend (filled with sample data) and backend (Report
    # Builder skeleton with field-name placeholders).
    try:
        mockup_html = render_mockup(parsed, mode="frontend")
    except Exception as e:  # noqa: BLE001
        mockup_html = _fallback_preview(parsed, f"{type(e).__name__}: {e}")
    try:
        mockup_backend_html = render_mockup(parsed, mode="backend")
    except Exception as e:  # noqa: BLE001
        mockup_backend_html = _fallback_preview(parsed, f"{type(e).__name__}: {e}")

    # Validation: T-SQL static + RDL structural
    validation_issues = validate_report(parsed, target_db=target_db)
    rdl_issues = []
    try:
        rdl_issues = validate_rdl(rdl_xml, target_db=target_db)
    except Exception as e:  # noqa: BLE001
        rdl_issues = [{"severity": "warning", "rule": "rdl.check_failed",
                       "message": f"RDL validator raised {type(e).__name__}: {e}",
                       "element": None}]

    # Deployment checklist
    deployment_checklist = build_checklist(parsed, rdl_xml, validation_issues + rdl_issues)

    # Audit trail (every translation decision)
    audit_trail = []
    try:
        audit_trail = build_audit_trail(parsed)
    except Exception as e:  # noqa: BLE001
        audit_trail = [{"step": 0, "stage": "audit", "scope": "audit",
                        "rule": "audit.failed",
                        "before": "", "after": "",
                        "rationale": f"audit raised {type(e).__name__}: {e}"}]

    # Fidelity report -- source->RDL coverage so the user knows EXACTLY what
    # was preserved vs what still needs manual wiring (the faithfulness
    # counterpart to the upload-safety preflight).
    try:
        fidelity_report = build_fidelity_report(parsed, rdl_xml)
    except Exception as e:  # noqa: BLE001
        fidelity_report = {"score": None, "summary": "",
                           "categories": {}, "needs_attention": [],
                           "error": f"{type(e).__name__}: {e}"}

    # AI-assist prompts for tricky bits
    ai_prompts = []
    try:
        ai_prompts = build_prompts(parsed)
    except Exception as e:  # noqa: BLE001
        ai_prompts = []

    # Sub-report (drill-through) detection. Surface to the frontend
    # so the Sub-Reports tab can list each detected link with a
    # per-link artifact drop zone.
    subreport_links = []
    try:
        subreport_links = detect_subreport_links(parsed)
    except Exception as e:  # noqa: BLE001
        subreport_links = []

    # Bursting / DDS detection
    bursting_info = {"is_bursting": False}
    try:
        bursting_info = detect_bursting(parsed)
        # Override: if the report is drill-through-only (hyperlink to a
        # child report) WITHOUT any per-row email/distribution markers,
        # it is NOT bursting -- it's just a sub-report link. Suppress
        # the bursting flag so the user gets the Sub-Reports tab
        # instead of the (irrelevant) Bursting tab content.
        if bursting_info.get("is_bursting") and is_drillthrough_only(parsed):
            bursting_info = {
                "is_bursting": False,
                "evidence": bursting_info.get("evidence", []) + [
                    "reclassified as drill-through (hyperlink to child report, no distribution markers)",
                ],
                "reclassified_as": "drillthrough",
            }
        if bursting_info.get("is_bursting"):
            bursting_info["burst_query"] = build_burst_query(parsed, bursting_info)
            bursting_info["email_burst_query"] = build_email_burst_query(parsed, bursting_info)
            bursting_info["email_powershell_script"] = build_email_powershell_script(parsed, bursting_info, f"{parsed.name or 'report'}.rdl")
            bursting_info["service_account_checklist"] = build_service_account_checklist(parsed, bursting_info)
            bursting_info["email_config_template"] = build_email_config_template(parsed, bursting_info)
            bursting_info["powershell_script"] = build_powershell_dds_script(
                parsed, bursting_info, f"{parsed.name or 'report'}.rdl"
            )
    except Exception as e:  # noqa: BLE001
        bursting_info = {"is_bursting": False, "error": f"{type(e).__name__}: {e}"}

    preflight = preflight_audit(rdl_xml, target_db=target_db)
    # REAL-GRAMMAR SQL validation. Every other check reasons about the
    # RDL; none of them ever asked whether the SQL we emit is SQL Oracle
    # would accept — and the render harness cannot tell, because it feeds
    # synthetic rows in by field name. Four classes of un-runnable SQL
    # reached production that way. Judged DIFFERENTIALLY (generated fails
    # to parse while the ORIGINAL parsed cleanly) so Oracle constructs the
    # grammar can't model never masquerade as findings. Degrades to "no
    # opinion" when the optional grammar backend is absent.
    try:
        _ds_sql = _dataset_command_texts(rdl_xml)
        _sql_issues = sql_differential_issues(parsed, _ds_sql,
                                              dialect=("tsql" if target_db
                                                       == "sqlserver"
                                                       else "oracle"))
        if _sql_issues:
            preflight = dict(preflight)
            preflight["issues"] = list(preflight.get("issues") or []) \
                + _sql_issues
            preflight["verdict"] = "BLOCKER"
    except Exception:  # noqa: BLE001 - a validator must never sink convert()
        pass
    # CONTENT-COVERAGE CONTRACT: name every visible source field whose
    # data did not reach the RDL. Content has vanished quietly before (a
    # title segment, a subtitle line, 21 margin fields, an entire header
    # frame); a field either lands in the artifact or the user is told it
    # did not. AMBER — a fidelity gap to disclose, not a reason to block
    # a report that otherwise deploys and runs.
    try:
        _cov = coverage_issues(parsed, rdl_xml)
        if _cov:
            preflight = dict(preflight)
            preflight["issues"] = list(preflight.get("issues") or []) + _cov
    except Exception:  # noqa: BLE001 - disclosure must never sink convert()
        pass
    # Honest verdict for PARTIAL Oracle artifacts (wild-corpus verified):
    # a customization overlay or a data-model-only export is not a full
    # report. Tell the user plainly instead of shipping a near-blank RDL
    # under a scary RED.
    source_kind = _classify_source_artifact(parsed)
    if source_kind:
        preflight = dict(preflight)
        preflight["source_kind"] = source_kind["kind"]
        preflight["source_kind_message"] = source_kind["message"]

    # Static layout audit: flag CanGrow=false textboxes whose declared content
    # can't fit (clip risk) -- the class the placeholder-data render is blind to.
    # Purely structural + data-independent. Surfaced as NON-BLOCKING AMBER notes
    # in the same pre-download verdict, never a BLOCKER (a clip is a fidelity nit,
    # not an upload/runtime failure). Wrapped so it can never sink a convert().
    try:
        _layout_flags = audit_layout(rdl_xml)
        if _layout_flags:
            preflight = dict(preflight)
            _issues = list(preflight.get("issues", []))
            for _lf in _layout_flags:
                _issues.append({
                    "severity": "AMBER",  # frontend only buckets BLOCKER/RED/AMBER
                    "rule": _lf.get("rule", "layout.height_overflow"),
                    "message": _lf.get("message", ""),
                })
            preflight["issues"] = _issues
            # Re-derive the worst verdict; AMBER lifts READY->AMBER but never
            # lowers an existing BLOCKER/RED.
            _sev = {"BLOCKER": 3, "RED": 2, "AMBER": 1}
            _worst = max((_sev.get(i.get("severity"), 0) for i in _issues), default=0)
            preflight["verdict"] = {3: "BLOCKER", 2: "RED", 1: "AMBER",
                                    0: "READY"}[_worst]
    except Exception:  # layout audit must never break a convert
        pass

    # Deep expression verification (opt-in via deep_verify). Compiles every
    # generated VB.NET expression -- and the report's own <Code> block --
    # through the real System.CodeDom VB compiler, the same compilation SSRS
    # performs at publish time. This catches the class of bug the static
    # preflight and the layout renderer are both blind to: an expression that
    # is syntactically invalid VB (bad IIf arity, trailing comma, unbalanced
    # parens, an undefined function, a leaked Oracle ||) renders as #Error in
    # real SSRS but passes a Fields!-reference check. Availability-gated and
    # wrapped so it can NEVER break a conversion: on a host without the
    # compiler (e.g. Linux CI) it is simply reported as not-run.
    if deep_verify:
        try:
            from .validators.vb_expr_check import check_rdl_expressions
            ev = check_rdl_expressions(rdl_xml, timeout=120)
            preflight = dict(preflight)
            preflight["expr_verify"] = {
                "available": bool(ev.get("available")),
                "summary": ev.get("summary", {}),
                "bad": [
                    {"location": b.get("location"), "expr": b.get("expr", "")[:160],
                     "error": (b.get("errors") or [""])[0][:200]}
                    for b in ev.get("bad", [])[:25]
                ],
            }
            # A non-compiling expression IS an upload-time blocker; merge each
            # into the issue list and re-derive the worst verdict. (No-op when
            # everything compiles, so existing clean reports are unaffected.)
            if ev.get("available") and ev.get("bad"):
                issues = list(preflight.get("issues", []))
                for b in ev["bad"][:25]:
                    err = (b.get("errors") or [""])[0]
                    issues.append({
                        "severity": "BLOCKER", "rule": "rdl.expr_compile",
                        "message": (
                            f"<{b.get('location')}> expression does not compile in "
                            f"VB.NET (SSRS renders #Error at run time): "
                            f"{b.get('expr', '')[:80]} -> {err[:140]}"),
                    })
                preflight["issues"] = issues
                _sev = {"BLOCKER": 3, "RED": 2, "AMBER": 1}
                _worst = max((_sev.get(i.get("severity"), 0) for i in issues), default=0)
                preflight["verdict"] = {3: "BLOCKER", 2: "RED", 1: "AMBER",
                                        0: "READY"}[_worst]
        except Exception:  # noqa: BLE001 -- deep verify must never sink a convert
            pass

    # LINKED-DETAIL HONESTY (wild-corpus: 5-link chains and parallel
    # sibling details silently lost rows — the child datasets existed but
    # nothing rendered them). A <link> child query whose dataset the RDL
    # never binds OR scopes is a RED verdict, not a footnote.
    try:
        import re as _re
        for _q in (parsed.queries or []):
            if not getattr(_q, "parent_group", "") or not (_q.items or []):
                continue
            _nm = _re.sub(r"[^A-Za-z0-9_]", "_", _q.name or "")
            if (_nm and rdl_xml
                    and f"<DataSetName>{_nm}</DataSetName>" not in rdl_xml
                    and f'"{_nm}")' not in rdl_xml):
                if isinstance(preflight, dict):
                    # AMBER, not RED: truth-verified reports legitimately
                    # carry linked aux queries whose rows Oracle's own
                    # output never printed — but the loss must be VISIBLE
                    # so a silently-dropped detail section (5-link chains)
                    # is never a surprise.
                    preflight.setdefault("issues", []).append({
                        "severity": "AMBER",
                        "rule": f"rdl.linked_detail_not_rendered.{_nm}",
                        "message": (
                            f"Linked detail query '{_q.name}' (child of "
                            f"{_q.parent_group}) is never bound to any data "
                            f"region — its detail rows will NOT render. "
                            f"Add a nested region or subreport for it, or "
                            f"confirm the data is intentionally unused."),
                    })
                    if preflight.get("verdict") in (None, "READY"):
                        preflight["verdict"] = "AMBER"
    except Exception:  # noqa: BLE001
        pass

    if _unsupported_note:
        # Force the verdict + fidelity honest on an unsupported/empty
        # source: everything downstream ran on crash-safety fallbacks and
        # the output is a shell, not a report.
        if isinstance(preflight, dict):
            preflight.setdefault("issues", []).insert(0, {
                "severity": "BLOCKER",
                "rule": "source.unsupported_kind",
                "message": _unsupported_note,
            })
            preflight["verdict"] = "BLOCKER"
        if isinstance(fidelity_report, dict):
            fidelity_report["score"] = 0.0
            fidelity_report.setdefault("needs_attention", []).append(
                _unsupported_note)

    # GENERIC LABEL OVERRIDES: any literal label the generator produced
    # (report/band titles, cover-hyperlink text, bundle titles a bare-SQL
    # source can't know) is overridable by textbox name — or 'title'. The
    # inventory ships in the result so any UI can offer a generic editor;
    # nothing here is scenario-specific.
    overridable_labels = []
    try:
        from .generators.rdl import (collect_overridable_labels,
                                     apply_label_overrides)
        if label_overrides:
            rdl_xml, _applied = apply_label_overrides(rdl_xml,
                                                      label_overrides)
            for _nm, _old, _new in _applied:
                if _old and mockup_html:
                    mockup_html = mockup_html.replace(
                        f">{_old}<", f">{_new}<")
        overridable_labels = collect_overridable_labels(rdl_xml)
    except Exception:  # noqa: BLE001
        pass

    return {
        "report": parsed.to_dict(),
        "rdl_xml": rdl_xml,
        "conversion_error": conversion_error,
        "oracle_xml": parsed.raw_xml,
        "mockup_html": mockup_html,
        "overridable_labels": overridable_labels,
        "mockup_backend_html": mockup_backend_html,
        "validation_issues": validation_issues,
        "rdl_issues": rdl_issues,
        "deployment_checklist": deployment_checklist,
        "audit_trail": audit_trail,
        "fidelity_report": fidelity_report,
        "preflight": preflight,
        "ai_prompts": ai_prompts,
        "bursting": bursting_info,
        "target_db": target_db,
        "subreport_links": subreport_links,
        "image_slots": _image_slots(parsed),
    }


__all__ = ["convert", "run_query"]

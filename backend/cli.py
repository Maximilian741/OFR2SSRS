"""
Oracle -> SSRS Converter: command-line batch interface.

Lets coworkers process one or many Oracle Reports XML files without launching
the Flask UI. For each input file we emit:

    out/<name>.rdl              - the generated SSRS RDL XML
    out/<name>.validation.md    - human-readable T-SQL validation report
    out/<name>.checklist.md     - deployment checklist (markdown)
    out/<name>.audit.json       - the full ParsedReport dict for debugging

Usage:
    python backend/cli.py <input> [--out OUTDIR] [--strict] [--quiet]

    <input> may be a single .xml file or a directory of .xml files.
    --strict : exit non-zero if any input had validation errors.
    --quiet  : suppress per-file progress output.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Make `from converter import ...` work whether you run from the repo root or
# from inside backend/ - mirrors what app.py does.
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from converter import convert  # noqa: E402


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _json_default(o: Any) -> Any:
    """Best-effort JSON encoder for objects that may include dataclasses
    or other non-trivial types coming back from ParsedReport.to_dict()."""
    if is_dataclass(o):
        return asdict(o)
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    if isinstance(o, bytes):
        try:
            return o.decode("utf-8", errors="replace")
        except Exception:
            return repr(o)
    if hasattr(o, "to_dict"):
        try:
            return o.to_dict()
        except Exception:
            pass
    return repr(o)


def _format_validation_md(report_name: str, issues: List[Dict[str, Any]]) -> str:
    """Render the validation issues list as a readable markdown document."""
    lines: List[str] = []
    lines.append(f"# Validation report: {report_name}")
    lines.append("")
    counts = {"error": 0, "warning": 0, "info": 0}
    for it in issues or []:
        sev = (it.get("severity") or "info").lower()
        counts[sev] = counts.get(sev, 0) + 1
    lines.append(
        f"**Summary:** {counts.get('error', 0)} error(s), "
        f"{counts.get('warning', 0)} warning(s), "
        f"{counts.get('info', 0)} info."
    )
    lines.append("")
    if not issues:
        lines.append("_No T-SQL issues detected by the static validator._")
        lines.append("")
        return "\n".join(lines)

    lines.append("| Severity | Scope | Line | Rule | Message |")
    lines.append("|---|---|---|---|---|")
    for it in issues:
        sev = it.get("severity", "")
        scope = it.get("scope", "") or ""
        line = it.get("line")
        line_s = str(line) if line is not None else ""
        rule = it.get("rule", "") or ""
        msg = (it.get("message", "") or "").replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {sev} | {scope} | {line_s} | {rule} | {msg} |")
    lines.append("")

    # Detail section with excerpts.
    lines.append("## Details")
    lines.append("")
    for i, it in enumerate(issues, 1):
        sev = it.get("severity", "info")
        scope = it.get("scope", "") or ""
        rule = it.get("rule", "") or ""
        msg = it.get("message", "") or ""
        line = it.get("line")
        excerpt = it.get("excerpt", "") or ""
        header = f"### {i}. [{sev}] {rule or 'rule'} - {scope}"
        if line is not None:
            header += f" (line {line})"
        lines.append(header)
        lines.append("")
        lines.append(msg)
        if excerpt:
            lines.append("")
            lines.append("```sql")
            lines.append(excerpt)
            lines.append("```")
        lines.append("")
    return "\n".join(lines)


def _format_checklist_md(report_name: str, checklist: List[Dict[str, Any]]) -> str:
    """Render the deployment checklist as a markdown document."""
    lines: List[str] = []
    lines.append(f"# Deployment checklist: {report_name}")
    lines.append("")
    if not checklist:
        lines.append("_No checklist items were generated._")
        lines.append("")
        return "\n".join(lines)

    status_box = {
        "auto": "[x]",
        "todo": "[ ]",
        "manual": "[ ]",
        "caution": "[!]",
    }
    for i, step in enumerate(checklist, 1):
        title = step.get("title") or step.get("name") or f"Step {i}"
        status = (step.get("status") or "todo").lower()
        body = step.get("body") or step.get("description") or ""
        box = status_box.get(status, "[ ]")
        lines.append(f"## {i}. {box} {title}  _({status})_")
        lines.append("")
        if body:
            lines.append(body)
            lines.append("")
    return "\n".join(lines)


def _safe_stem(path: Path, fallback: str = "report") -> str:
    s = path.stem
    if not s:
        return fallback
    # Strip the most common suffix Oracle Reports adds.
    return s


def _count_issues(issues: List[Dict[str, Any]]) -> Tuple[int, int]:
    e = sum(1 for it in (issues or []) if (it.get("severity") or "").lower() == "error")
    w = sum(1 for it in (issues or []) if (it.get("severity") or "").lower() == "warning")
    return e, w


# ---------------------------------------------------------------------------
# Per-file processing
# ---------------------------------------------------------------------------

def _process_file(xml_path: Path, out_dir: Path, quiet: bool) -> Dict[str, Any]:
    """Convert one Oracle XML file. Returns a summary row dict."""
    name = _safe_stem(xml_path)
    row: Dict[str, Any] = {
        "name": name,
        "input": str(xml_path),
        "errors": 0,
        "warnings": 0,
        "status": "FAIL",
        "outputs": [],
        "error": None,
    }
    try:
        if not quiet:
            print(f"[cli] converting {xml_path} ...")
        data = convert(xml_path.read_bytes())
    except Exception as exc:
        row["error"] = f"{exc.__class__.__name__}: {exc}"
        row["status"] = "FAIL"
        if not quiet:
            print(f"[cli] FAILED to convert {xml_path}: {row['error']}")
            traceback.print_exc()
        return row

    # Use the parsed report's name when available so output filenames match.
    parsed = data.get("report") or {}
    parsed_name = parsed.get("name") or name
    # Sanitize for filesystem.
    safe = "".join(ch if (ch.isalnum() or ch in "-_.") else "_" for ch in parsed_name) or name
    row["name"] = safe

    out_dir.mkdir(parents=True, exist_ok=True)

    rdl_path = out_dir / f"{safe}.rdl"
    val_path = out_dir / f"{safe}.validation.md"
    chk_path = out_dir / f"{safe}.checklist.md"
    audit_path = out_dir / f"{safe}.audit.json"

    rdl_xml = data.get("rdl_xml") or ""
    issues = data.get("validation_issues") or []
    checklist = data.get("deployment_checklist") or []

    rdl_path.write_text(rdl_xml, encoding="utf-8")
    val_path.write_text(_format_validation_md(parsed_name, issues), encoding="utf-8")
    chk_path.write_text(_format_checklist_md(parsed_name, checklist), encoding="utf-8")
    audit_path.write_text(
        json.dumps(parsed, indent=2, default=_json_default, ensure_ascii=False),
        encoding="utf-8",
    )

    e, w = _count_issues(issues)
    row["errors"] = e
    row["warnings"] = w
    row["status"] = "FAIL" if e > 0 else "PASS"
    row["outputs"] = [str(rdl_path), str(val_path), str(chk_path), str(audit_path)]

    if not quiet:
        print(
            f"[cli]   wrote {rdl_path.name}, {val_path.name}, "
            f"{chk_path.name}, {audit_path.name}  ({e}E/{w}W)"
        )

    return row


def _collect_inputs(input_path: Path) -> List[Path]:
    if input_path.is_file():
        return [input_path]
    if input_path.is_dir():
        return sorted(p for p in input_path.glob("*.xml") if p.is_file())
    return []


def _print_summary(rows: List[Dict[str, Any]]) -> None:
    if not rows:
        print("\nNo reports were processed.")
        return
    name_w = max(6, max(len(r["name"]) for r in rows))
    name_w = min(name_w, 40)
    issues_w = 8
    status_w = 6
    header = f"{'Report':<{name_w}}  {'Issues':<{issues_w}}  {'Status':<{status_w}}"
    sep = "-" * len(header)
    print()
    print(header)
    print(sep)
    for r in rows:
        nm = r["name"]
        if len(nm) > name_w:
            nm = nm[: name_w - 1] + "~"
        if r.get("error"):
            issues = "ERR"
        else:
            issues = f"{r['errors']}E/{r['warnings']}W"
        print(f"{nm:<{name_w}}  {issues:<{issues_w}}  {r['status']:<{status_w}}")
    print(sep)
    total_err = sum(r["errors"] for r in rows)
    total_warn = sum(r["warnings"] for r in rows)
    failed = sum(1 for r in rows if r["status"] != "PASS")
    print(
        f"{len(rows)} report(s); {total_err} error(s), {total_warn} warning(s); "
        f"{failed} failed."
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="oracle2ssrs",
        description="Batch-convert Oracle Reports XML files to SSRS RDL.",
    )
    p.add_argument(
        "input",
        help="Path to a single Oracle Reports .xml file OR a folder of them.",
    )
    p.add_argument(
        "--out",
        default="./out",
        help="Output directory (default: ./out).",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="Exit with code 2 if any report has validation errors.",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-file progress output.",
    )
    return p


def main(argv: List[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)

    in_path = Path(args.input).expanduser().resolve()
    out_dir = Path(args.out).expanduser().resolve()

    if not in_path.exists():
        print(f"error: input path does not exist: {in_path}", file=sys.stderr)
        return 1

    inputs = _collect_inputs(in_path)
    if not inputs:
        print(
            f"error: no .xml files found at {in_path}",
            file=sys.stderr,
        )
        return 1

    if not args.quiet:
        print(f"[cli] {len(inputs)} input file(s); output -> {out_dir}")

    rows: List[Dict[str, Any]] = []
    for f in inputs:
        rows.append(_process_file(f, out_dir, args.quiet))

    if not args.quiet:
        _print_summary(rows)

    any_failed = any(r["status"] != "PASS" for r in rows)
    any_errors = any(r["errors"] > 0 or r.get("error") for r in rows)

    if any(r.get("error") for r in rows):
        # A hard conversion crash is always non-zero.
        return 2
    if args.strict and any_errors:
        return 2
    return 0 if not any_failed or not args.strict else 2


if __name__ == "__main__":
    raise SystemExit(main())

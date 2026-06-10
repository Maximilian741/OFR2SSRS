"""
AI runner — automate the prompts emitted by ai_assist.py by calling the
Anthropic API (Claude Haiku 4.5 by default — fast, cheap). Falls back
gracefully if no API key is configured.

Flow:
    prompts = build_prompts(report)           # ai_assist.py
    results = run_prompts(prompts, ...)       # this module
    for r in results:
        apply_fix(rdl, r['target'], r['body'])

Requires:
    pip install anthropic
    ANTHROPIC_API_KEY=sk-ant-... in env  OR  passed in as api_key arg

Public API:
    is_configured() -> bool
    run_prompts(prompts: list[dict], *, model=..., max_tokens=..., on_progress=None) -> list[dict]
    auto_fix(conversion_data: dict) -> dict      # high-level: prompts -> calls -> apply -> return updated data
"""
from __future__ import annotations

import os
import re
import traceback
from typing import Any, Callable, Dict, List, Optional


# Default model: Haiku is fast + cheap and plenty for "translate this PL/SQL
# block to T-SQL". Override with O2S_AI_MODEL env or explicit arg.
DEFAULT_MODEL = os.environ.get("O2S_AI_MODEL", "claude-haiku-4-5-20251001")
DEFAULT_MAX_TOKENS = int(os.environ.get("O2S_AI_MAX_TOKENS", "1500"))


def _api_key() -> Optional[str]:
    return os.environ.get("ANTHROPIC_API_KEY") or None


def is_configured() -> bool:
    """True if an Anthropic API key is available AND the SDK is importable."""
    if not _api_key():
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


def _strip_markdown_fences(s: str) -> str:
    """Claude often wraps SQL in ```sql ... ```. Strip those."""
    if not s:
        return s
    s = s.strip()
    # Triple-backtick fence with optional language tag
    m = re.match(r"^```[a-zA-Z]*\s*\n?(.*?)\n?```\s*$", s, re.DOTALL)
    if m:
        return m.group(1).strip()
    return s


def _call_claude(
    prompt_text: str,
    *,
    api_key: str,
    model: str,
    max_tokens: int,
) -> str:
    """One-shot completion. Returns the raw text body Claude produced."""
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt_text}],
    )
    # Concatenate text blocks
    parts = []
    for block in msg.content:
        if hasattr(block, "text"):
            parts.append(block.text)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts)


def run_prompts(
    prompts: List[Dict[str, Any]],
    *,
    api_key: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    on_progress: Optional[Callable[[int, int, Dict[str, Any]], None]] = None,
) -> List[Dict[str, Any]]:
    """Call Claude for each prompt. Returns one result per prompt.

    Each result: {
        "id": prompt['id'],
        "target": {"kind": prompt['scope_kind'] or 'udf', "name": prompt['name']},
        "ok": bool,
        "body": str,        # the translated T-SQL (cleaned of markdown fences)
        "raw": str,         # raw model output
        "error": str|None,
    }
    """
    key = api_key or _api_key()
    if not key:
        return [
            {"id": p.get("id", ""), "target": {"kind": p.get("scope", "udf"), "name": p.get("name", "")},
             "ok": False, "body": "", "raw": "", "error": "ANTHROPIC_API_KEY not set"}
            for p in prompts
        ]
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return [
            {"id": p.get("id", ""), "target": {"kind": p.get("scope", "udf"), "name": p.get("name", "")},
             "ok": False, "body": "", "raw": "",
             "error": "anthropic package not installed (pip install anthropic)"}
            for p in prompts
        ]

    out: List[Dict[str, Any]] = []
    for idx, p in enumerate(prompts):
        target = {"kind": _kind_from_scope(p.get("scope", "udf")), "name": p.get("name", "")}
        result = {"id": p.get("id", ""), "target": target,
                  "ok": False, "body": "", "raw": "", "error": None}
        try:
            raw = _call_claude(
                p.get("prompt_template", ""),
                api_key=key, model=model, max_tokens=max_tokens,
            )
            cleaned = _strip_markdown_fences(raw)
            result.update(ok=True, body=cleaned, raw=raw)
        except Exception as e:  # noqa: BLE001
            result.update(error=f"{type(e).__name__}: {e}",
                          raw=traceback.format_exc())
        if on_progress:
            try:
                on_progress(idx + 1, len(prompts), result)
            except Exception:
                pass
        out.append(result)
    return out


def _kind_from_scope(scope: str) -> str:
    """ai_assist scopes are 'formula' / 'query' / 'package_fn'. apply_fix
    expects 'udf' / 'query' / 'formula'."""
    s = (scope or "").lower()
    if "package" in s or "fn" in s:
        return "udf"
    if "query" in s:
        return "query"
    return "udf"


def auto_fix(conversion_data: Dict[str, Any], *, api_key: Optional[str] = None,
             model: str = DEFAULT_MODEL) -> Dict[str, Any]:
    """High-level: take a convert() output, run all AI prompts, apply each
    successful result via ai_apply.apply_fix, return the updated dict.

    Returns dict with extra keys:
        ai_results: list of run_prompts results
        applied_fixes: list (extends existing)
        ai_summary: {"total": int, "applied": int, "failed": int, "rejected": int}
    """
    from .ai_apply import validate_udf_body, apply_fix

    prompts = conversion_data.get("ai_prompts") or []
    rdl = conversion_data.get("rdl_xml") or ""
    if not prompts or not rdl:
        return {**conversion_data,
                "ai_summary": {"total": 0, "applied": 0, "failed": 0, "rejected": 0,
                               "note": "no prompts or no RDL"},
                "ai_results": []}

    results = run_prompts(prompts, api_key=api_key, model=model)
    applied = list(conversion_data.get("applied_fixes") or [])
    rejected = 0
    succeeded = 0
    failed = 0
    current_rdl = rdl

    for r in results:
        if not r.get("ok"):
            failed += 1
            continue
        ok, issues = validate_udf_body(r["body"], r["target"].get("name"))
        if not ok:
            r["error"] = "validation_failed: " + "; ".join(issues)
            r["ok"] = False
            rejected += 1
            continue
        try:
            current_rdl, info = apply_fix(current_rdl, r["target"], r["body"])
            applied.append({"target": r["target"], "info": info, "ai_id": r["id"]})
            succeeded += 1
            r["applied"] = True
            r["info"] = info
        except Exception as e:  # noqa: BLE001
            r["error"] = f"apply failed: {e}"
            r["ok"] = False
            failed += 1

    return {
        **conversion_data,
        "rdl_xml": current_rdl,
        "applied_fixes": applied,
        "ai_results": results,
        "ai_summary": {
            "total": len(prompts),
            "applied": succeeded,
            "failed": failed,
            "rejected": rejected,
            "model": model,
        },
    }

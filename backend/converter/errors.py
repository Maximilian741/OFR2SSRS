"""Structured error system for the Oracle2SSRS conversion pipeline.

Provides a uniform way to wrap pipeline stages, capture failures with full
context (stage name, exception type, traceback, partial result), and format
those failures for both server-side logs and end-user-facing JSON responses.

Stdlib only: uuid, traceback, dataclasses, sys.
"""

from __future__ import annotations

import sys
import traceback as _tb
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Optional, Tuple


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class StageError:
    """A structured record of a failure inside one pipeline stage."""

    stage: str
    type_name: str
    message: str
    log_id: str
    traceback: str
    partial_result: Any = None

    def to_dict(self) -> dict:
        """Plain dict for JSON serialization (full detail)."""
        d = asdict(self)
        # partial_result may not be JSON-serializable; coerce to repr fallback
        try:
            import json
            json.dumps(d["partial_result"])
        except Exception:
            d["partial_result"] = repr(self.partial_result)
        return d


# ---------------------------------------------------------------------------
# Hint table — maps exception type-name to a user-actionable suggestion.
# ---------------------------------------------------------------------------

_HINTS = {
    "XMLSyntaxError": (
        "The XML appears malformed. Check that the file is a valid Oracle "
        "Reports export, not a binary .rdf."
    ),
    "ParseError": (
        "The XML could not be parsed. Check that the file is a valid Oracle "
        "Reports export, not a binary .rdf."
    ),
    "ExpatError": (
        "The XML could not be parsed. Check that the file is a valid Oracle "
        "Reports export, not a binary .rdf."
    ),
    "KeyError": (
        "An expected element was missing from the report. The file may be "
        "incomplete or use a non-standard layout."
    ),
    "AttributeError": (
        "An expected attribute was missing on a report element. The file may "
        "be incomplete or use a non-standard layout."
    ),
    "ValueError": (
        "A value in the report could not be interpreted. The file may use a "
        "non-standard layout or unsupported feature."
    ),
    "FileNotFoundError": (
        "A required file was not found. Verify the upload completed and the "
        "path is correct."
    ),
    "UnicodeDecodeError": (
        "The file could not be decoded as text. Make sure it is a UTF-8 or "
        "Latin-1 encoded XML export, not a binary .rdf."
    ),
}

_DEFAULT_HINT_FMT = (
    "Internal converter error. Check flask.log for log_id {log_id}."
)


# ---------------------------------------------------------------------------
# Severity classification — low-effort heuristic by stage + type.
# ---------------------------------------------------------------------------

_USER_INPUT_TYPES = {
    "XMLSyntaxError",
    "ParseError",
    "ExpatError",
    "UnicodeDecodeError",
    "FileNotFoundError",
}


def _severity_for(stage: str, type_name: str) -> str:
    """Classify severity. user_input < warning < error."""
    if type_name in _USER_INPUT_TYPES:
        return "user_input"
    if stage in ("validate",):
        return "warning"
    return "error"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _short_log_id() -> str:
    """Return a 5-char hex id like 'a4f3b'."""
    return uuid.uuid4().hex[:5]


def safe_stage(
    stage_name: str,
    fn: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Tuple[Any, Optional[StageError]]:
    """Run fn(*args, **kwargs) inside a try/except.

    Returns (result, None) on success, or (None, StageError) on failure. The
    StageError carries a unique short hex log_id; the full traceback is also
    written to stderr so operators can grep flask.log by that id.
    """
    try:
        result = fn(*args, **kwargs)
        return result, None
    except BaseException as exc:  # capture KeyboardInterrupt etc. too
        log_id = _short_log_id()
        tb_text = _tb.format_exc()
        type_name = type(exc).__name__
        message = str(exc) if str(exc) else type_name

        # Try to recover a partial_result attribute that the failing fn may
        # have stashed on the exception (idiom: raise SomeErr(...).with_partial(x))
        partial = getattr(exc, "partial_result", None)

        # Emit to stderr so the line ends up in flask.log; tag with log_id.
        try:
            sys.stderr.write(
                f"[stage={stage_name}] [log_id={log_id}] "
                f"{type_name}: {message}\n{tb_text}"
            )
            sys.stderr.flush()
        except Exception:
            # Never let logging itself crash the caller
            pass

        err = StageError(
            stage=stage_name,
            type_name=type_name,
            message=message,
            log_id=log_id,
            traceback=tb_text,
            partial_result=partial,
        )

        # Don't swallow KeyboardInterrupt / SystemExit silently — re-raise
        # after logging so server shutdown still works as expected.
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return None, err


def format_error_for_user(err: StageError) -> dict:
    """Return a JSON-friendly dict for end-user display.

    Includes severity, stage, message, log_id, and a 'next_step' hint chosen
    by exception type. Does NOT include the full traceback (that stays in
    flask.log, retrievable by log_id).
    """
    hint = _HINTS.get(err.type_name)
    if hint is None:
        hint = _DEFAULT_HINT_FMT.format(log_id=err.log_id)

    severity = _severity_for(err.stage, err.type_name)

    return {
        "severity": severity,
        "stage": err.stage,
        "type": err.type_name,
        "message": err.message,
        "log_id": err.log_id,
        "next_step": hint,
    }


__all__ = ["StageError", "safe_stage", "format_error_for_user"]

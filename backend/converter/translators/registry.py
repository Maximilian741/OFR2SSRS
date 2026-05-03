"""
Translator Registry — plugin system for org-specific Oracle->T-SQL translation rules.

This module is a parallel/optional system: other converters can opt into it to
register custom rewrites for org-specific Pkg_* functions, DECODE patterns,
date helpers, etc., without forking the main converter codebase.

Pure stdlib. No dependency on plsql_to_tsql.py.

Usage:
    from converter.translators.registry import translation_rule, global_registry

    @translation_rule('decode_to_case', r'DECODE\(([^)]+)\)', priority=50)
    def rewrite_decode(match):
        return ('CASE_FROM_DECODE(' + match.group(1) + ')', 'DECODE rewritten')

    translated, warnings = global_registry.apply(sql_text)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple


# A rule's replace callable receives an re.Match and returns
# (replacement_string, warning_string_or_empty).
ReplaceFn = Callable[[re.Match], Tuple[str, str]]


@dataclass
class TranslationRule:
    """A single registered Oracle->T-SQL rewrite rule.

    Attributes:
        name: Unique identifier for the rule (used for unregister()).
        pattern: Compiled regex matched against the SQL text.
        replace: Callable taking an re.Match and returning
                 (replacement_string, warning_string).  An empty warning
                 string means "no warning".
        priority: Lower numbers are applied first.  Default 100.
    """

    name: str
    pattern: "re.Pattern[str]"
    replace: ReplaceFn
    priority: int = 100


class TranslatorRegistry:
    """Holds a collection of TranslationRule objects and applies them in order."""

    def __init__(self) -> None:
        self._rules: List[TranslationRule] = []

    # ------------------------------------------------------------------ #
    # Registration                                                        #
    # ------------------------------------------------------------------ #
    def register(self, rule: TranslationRule) -> None:
        """Register a rule.  Replaces any existing rule with the same name."""
        if not isinstance(rule, TranslationRule):
            raise TypeError("register() expects a TranslationRule instance")
        # Replace existing rule with same name to keep registration idempotent.
        self._rules = [r for r in self._rules if r.name != rule.name]
        self._rules.append(rule)
        # Keep rules sorted by priority for predictable ordering.
        self._rules.sort(key=lambda r: (r.priority, r.name))

    def unregister(self, name: str) -> None:
        """Remove the rule with the given name.  No-op if not present."""
        self._rules = [r for r in self._rules if r.name != name]

    def list_rules(self) -> List[TranslationRule]:
        """Return a shallow copy of the rules list, in priority order."""
        return list(self._rules)

    def clear(self) -> None:
        """Remove all rules.  Useful for tests."""
        self._rules = []

    def get(self, name: str) -> Optional[TranslationRule]:
        """Return the rule with the given name, or None."""
        for r in self._rules:
            if r.name == name:
                return r
        return None

    # ------------------------------------------------------------------ #
    # Application                                                         #
    # ------------------------------------------------------------------ #
    def apply(self, sql: str) -> Tuple[str, List[str]]:
        """Apply all registered rules to sql in priority order.

        Returns a tuple (translated_sql, warnings).  Warnings are collected
        from each rule's replace callable; empty strings are filtered out.
        Each warning is annotated with the rule name that produced it.
        """
        if sql is None:
            return ("", [])

        warnings: List[str] = []
        current = sql

        for rule in self._rules:
            rule_name = rule.name
            replace_fn = rule.replace

            def _sub(match: "re.Match[str]", _rn: str = rule_name,
                     _fn: ReplaceFn = replace_fn) -> str:
                try:
                    result = _fn(match)
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"[{_rn}] error: {exc!r}")
                    return match.group(0)

                if isinstance(result, tuple):
                    if len(result) == 2:
                        replacement, warn = result
                    elif len(result) == 1:
                        replacement, warn = result[0], ""
                    else:
                        warnings.append(
                            f"[{_rn}] replace fn returned tuple of unexpected length"
                        )
                        return match.group(0)
                else:
                    # Allow plain string returns for convenience.
                    replacement, warn = result, ""

                if replacement is None:
                    replacement = match.group(0)
                if warn:
                    warnings.append(f"[{_rn}] {warn}")
                return str(replacement)

            try:
                current = rule.pattern.sub(_sub, current)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"[{rule_name}] regex sub failed: {exc!r}")

        return current, warnings


# ---------------------------------------------------------------------- #
# Module-level singleton + convenience decorator                          #
# ---------------------------------------------------------------------- #
global_registry = TranslatorRegistry()


def translation_rule(name: str, pattern: str, priority: int = 100):
    """Decorator: register the wrapped function as a TranslationRule.

    The wrapped function must accept an re.Match and return either a
    replacement string or a (replacement_string, warning_string) tuple.
    """
    compiled = re.compile(pattern, re.IGNORECASE)

    def deco(fn: ReplaceFn) -> ReplaceFn:
        global_registry.register(
            TranslationRule(
                name=name,
                pattern=compiled,
                replace=fn,
                priority=priority,
            )
        )
        return fn

    return deco


__all__ = [
    "TranslationRule",
    "TranslatorRegistry",
    "global_registry",
    "translation_rule",
]

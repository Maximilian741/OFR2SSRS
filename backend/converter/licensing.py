"""Edition / licensing seam.

The converter core stays fully functional in every edition — the gates
only shape VOLUME features (batch size) and branding on generated
assessment artifacts. Honest by design: nothing already converted is
ever held hostage, and single-report conversion is always unlimited.

Tier resolution (no phone-home, no key server yet — the seam exists so a
real license check can be dropped in later without touching callers):

    O2S_LICENSE=pro          -> Pro
    O2S_LICENSE=enterprise   -> Enterprise
    (unset)                  -> Community

``O2S_BATCH_LIMIT`` overrides the batch cap (mainly for tests).
"""
from __future__ import annotations

import os
from typing import Optional

_TIERS = {
    "community": {
        "label": "Community Edition",
        "batch_limit": 10,
    },
    "pro": {
        "label": "Pro",
        "batch_limit": None,          # unlimited
    },
    "enterprise": {
        "label": "Enterprise",
        "batch_limit": None,
    },
}


def current_tier() -> str:
    t = (os.environ.get("O2S_LICENSE") or "").strip().lower()
    return t if t in _TIERS else "community"


def tier_label() -> str:
    return _TIERS[current_tier()]["label"]


def batch_limit() -> Optional[int]:
    """Max reports per batch run; None = unlimited."""
    override = (os.environ.get("O2S_BATCH_LIMIT") or "").strip()
    if override.isdigit():
        return int(override)
    return _TIERS[current_tier()]["batch_limit"]


__all__ = ["current_tier", "tier_label", "batch_limit"]

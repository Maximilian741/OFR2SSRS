"""Shared pytest fixtures for the Oracle2SSRS test suite.

The expensive bit is parsing the COMPLEX_REPORT.xml sample, so we cache it
once at session scope and hand it out to anyone that asks for
``parsed_report``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup -- make the converter package importable.
# ---------------------------------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
SAMPLES_DIR = ROOT_DIR / "samples" / "oracle"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture(scope="session")
def root_dir() -> Path:
    return ROOT_DIR


@pytest.fixture(scope="session")
def samples_dir() -> Path:
    return SAMPLES_DIR


@pytest.fixture(scope="session")
def mvwf_xml_path(samples_dir: Path) -> Path:
    p = samples_dir / "COMPLEX_REPORT.xml"
    if not p.exists():
        pytest.skip(f"Sample Oracle XML not present: {p}")
    return p


@pytest.fixture(scope="session")
def mvwf_xml_bytes(mvwf_xml_path: Path) -> bytes:
    return mvwf_xml_path.read_bytes()


@pytest.fixture(scope="session")
def parsed_report(mvwf_xml_bytes: bytes):
    """Parse MVWF_PERMIT once and share the result across tests."""
    from converter.parsers.oracle_xml import parse_oracle_xml
    return parse_oracle_xml(mvwf_xml_bytes)


@pytest.fixture(scope="session")
def translated_report(mvwf_xml_bytes: bytes):
    """Parse + translate; useful for downstream generator/validator tests."""
    from converter.parsers.oracle_xml import parse_oracle_xml
    from converter.translators.plsql_to_tsql import translate_report
    rep = parse_oracle_xml(mvwf_xml_bytes)
    translate_report(rep)
    return rep

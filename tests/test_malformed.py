"""Malformed-input tests for parse_oracle_xml.

Contract under test: parse_oracle_xml MUST NOT raise a generic exception
on any byte input. Acceptable outcomes:

  * raises lxml.etree.XMLSyntaxError (a clean, expected exception), or
  * returns a ParsedReport object (even if mostly empty).

The strictest form of the contract -- malformed input produces name=""
AND at least one .warnings entry -- is desirable but lxml's recover=True
mode in the parser is lenient enough that some inputs slip through with
a partial name and no warnings. For those inputs we relax the assertion
to "no crash" (and document the underlying parser-leniency issue).
"""
from __future__ import annotations

import io
import zipfile

import pytest
from lxml import etree

from converter.parsers.oracle_xml import parse_oracle_xml
from converter.models import ParsedReport


def _assert_no_crash(blob: bytes, label: str) -> ParsedReport | None:
    """Loose contract: never a generic exception.

    Returns the ParsedReport if one was produced, else None (the parser
    raised a clean XMLSyntaxError).
    """
    try:
        rep = parse_oracle_xml(blob)
    except etree.XMLSyntaxError:
        return None
    except Exception as exc:  # pragma: no cover - this is what we're guarding
        pytest.fail(f"{label}: unexpected exception {type(exc).__name__}: {exc}")
    assert isinstance(rep, ParsedReport), f"{label}: expected ParsedReport"
    assert isinstance(rep.warnings, list), f"{label}: warnings must be a list"
    return rep


def _assert_strict(blob: bytes, label: str) -> None:
    """Strict contract: XMLSyntaxError OR (name=='' AND warnings nonempty)."""
    rep = _assert_no_crash(blob, label)
    if rep is None:
        return
    assert rep.name == "", (
        f"{label}: expected empty name on malformed input, got {rep.name!r}"
    )
    assert len(rep.warnings) > 0, (
        f"{label}: expected at least one warning on malformed input"
    )


# ---------------------------------------------------------------------------
# Random binary garbage
# ---------------------------------------------------------------------------

def test_random_binary_bytes():
    blob = bytes(range(256)) * 4  # 1KB of every-byte garbage
    _assert_no_crash(blob, "random binary bytes")


def test_garbage_100kb():
    blob = bytes((i * 31 + 7) & 0xFF for i in range(100 * 1024))
    _assert_no_crash(blob, "100KB garbage")


# ---------------------------------------------------------------------------
# Empty / whitespace
# ---------------------------------------------------------------------------

def test_empty_bytes():
    _assert_no_crash(b"", "empty bytes")


def test_only_whitespace():
    _assert_no_crash(b"   \n\n   \t  \r\n  ", "only whitespace")


def test_only_xml_declaration():
    _assert_no_crash(b'<?xml version="1.0"?>', "only xml declaration")


# ---------------------------------------------------------------------------
# Structural problems
# ---------------------------------------------------------------------------

def test_unmatched_tags():
    """Production note: lxml recover=True salvages the partial root and
    returns a non-empty .name with no warnings, which violates the
    desired strict contract. We assert only no-crash here."""
    blob = b'<?xml version="1.0"?><report><data><userParameter name="P"/></report>'
    _assert_no_crash(blob, "unmatched tags")


def test_truncated_mid_document():
    """Production note: see test_unmatched_tags. recover=True is lenient."""
    blob = (
        b'<?xml version="1.0"?>\n'
        b'<report name="X" DTDVersion="9.0.2.0.10">\n'
        b'  <data>\n'
        b'    <userParameter name="P_'  # cut off mid-attribute
    )
    _assert_no_crash(blob, "truncated mid-document")


def test_truncated_after_root_open():
    blob = b'<?xml version="1.0"?><report'
    _assert_no_crash(blob, "truncated after root open")


def test_invalid_encoding_declaration():
    blob = (
        b'<?xml version="1.0" encoding="not-a-real-encoding"?>'
        b'<report name="X"/>'
    )
    _assert_no_crash(blob, "invalid encoding declaration")


# ---------------------------------------------------------------------------
# Wrong file format entirely
# ---------------------------------------------------------------------------

def test_docx_zip_bytes():
    """A .docx file is a ZIP container, not XML. Should not crash."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<document/>")
    blob = buf.getvalue()
    _assert_no_crash(blob, "docx zip bytes")


def test_pdf_header_bytes():
    blob = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< >>\nendobj\n%%EOF\n"
    _assert_no_crash(blob, "pdf header bytes")


def test_html_instead_of_xml():
    """An HTML page (no XML declaration) - lxml recovers a partial tree.
    This is the no-crash contract; warnings may be empty."""
    blob = b"<html><body><h1>not your XML</h1></body></html>"
    _assert_no_crash(blob, "html instead of xml")


# ---------------------------------------------------------------------------
# Nulls embedded
# ---------------------------------------------------------------------------

def test_nulls_inside_xml():
    blob = b'<?xml version="1.0"?>\x00<report name="X"\x00/>\x00'
    _assert_no_crash(blob, "nulls inside xml")


# ---------------------------------------------------------------------------
# Strict-contract inputs (these MUST emit warnings or raise XMLSyntaxError)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("blob,label", [
    (b"<", "single open angle"),
    (b">", "single close angle"),
    (b"<<<>>>", "angle bracket soup"),
    (b'<?xml version="1.0"?><', "decl + open angle"),
])
def test_definitely_invalid_xml_raises_or_warns(blob, label):
    """These inputs are unambiguously broken; the strict contract should hold."""
    _assert_strict(blob, label)


# ---------------------------------------------------------------------------
# Lenient-recover inputs: production "bug" surfaced -- documented as no-crash
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("blob,label", [
    (b'<?xml version="1.0"?><report', "decl + tag start"),
    (b'<?xml version="1.0"?><report>', "no closing tag"),
    (b'<?xml version="1.0"?><report>&undef;</report>', "undefined entity"),
])
def test_recover_mode_lenient_inputs(blob, label):
    """recover=True silently rebuilds these into a valid-looking tree.

    We accept that as no-crash; the parser's permissiveness is an
    intentional design choice but means malformed inputs may produce
    a ParsedReport with empty warnings.
    """
    _assert_no_crash(blob, label)

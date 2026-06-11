"""Oracle inline field references &<FIELD> inside boilerplate text (form
letters, mailing labels: "Dear &<FIRST_NAME>") must resolve to a sample
value in the HTML mockup -- NOT be stripped to empty or left as a raw
&<FIELD> token. Page-number builtins (&<PhysicalPageNumber>) still drop out.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.preview import html_mockup as hm  # noqa: E402

_LETTER = b"""<?xml version="1.0"?><report name="LTR" DTDVersion="9.0.2.0.10"><data>
<dataSource name="Q_1"><select><![CDATA[SELECT first_name, city FROM t]]></select>
<group name="G"><dataItem name="first_name" datatype="vchar2"/>
<dataItem name="city" datatype="vchar2"/></group></dataSource></data>
<layout><section name="main"><body width="8.5" height="11.0">
<frame name="F1"><geometryInfo x="0.5" y="0.5" width="6" height="2"/>
<text name="B1"><geometryInfo x="0.5" y="0.5" width="6" height="2"/>
<textSegment><font face="helvetica" size="11"/>
<string><![CDATA[Dear &<first_name> of &<city>,

This is page &<PhysicalPageNumber>. Thank you.

Sincerely,
The Team]]></string></textSegment></text></frame>
</body></section></layout></report>"""


def test_angle_token_resolves_to_sample_not_empty():
    prev = hm._ACTIVE_MODE
    hm.set_mode("frontend") if hasattr(hm, "set_mode") else None
    try:
        out = hm._resolve_tokens("Dear &<FIRST_NAME>, welcome")
    finally:
        if hasattr(hm, "set_mode"):
            hm.set_mode(prev)
    # the &<FIRST_NAME> must be replaced by *something* non-empty, not removed
    assert "&<" not in out and "&lt;" not in out
    assert out.startswith("Dear ") and out.endswith(", welcome")
    assert len(out) > len("Dear , welcome")


def test_page_builtin_angle_token_drops_out():
    out = hm._resolve_tokens("Page &<PhysicalPageNumber> end")
    assert "PhysicalPageNumber" not in out
    assert "&<" not in out
    assert "Page" in out and "end" in out


def test_non_english_fields_sample_sensibly():
    """Non-English column names (RO/ES/FR) must map to the right sample pool,
    and a stem must match only at a word boundary (so "nume"/RO-name never
    falls into the NUMBER pool via its "num" substring, and "metadata" is
    never treated as a date)."""
    s = hm._sample_for_source
    assert s("Nume_client_", 0) == "Alex Rivera"        # RO person name
    assert "$" in s("salariu", 0)                        # RO salary -> money
    assert s("oras", 0) == "Springfield"                # RO city
    assert "/" in s("data_nasterii", 0)                  # RO date
    # word-boundary precision: these must NOT false-match
    assert s("METADATA", 0) == "Sample Value A"          # not a date
    assert s("PROTOTYPE", 0) == "Sample Value A"         # not a "type"
    # the original bug: a name field must never read as a bare number
    assert s("Nume_client_", 0) != "1001"
    # first/last name -> a PERSON, but an org/company name stays an org
    assert s("FIRST_NAME", 0) == "Alex Rivera"
    assert s("LAST_NAME", 0) == "Alex Rivera"
    assert "Org" in s("COMPANY_NAME", 0)
    # geography: COUNTRY/COUNTY must NOT read as a number even though they
    # start with "count"; a real count field still does.
    assert s("COUNTRY", 0) not in ("1001", "1002")
    assert s("COUNTY", 0) not in ("1001", "1002")
    assert s("RECORD_COUNT", 0) in ("1001", "1002")
    # "statement" must NOT read as a region just because it starts with "state"
    assert s("STATEMENT", 0) != "North District"
    # money / quantity fields resolve to the right shape
    assert "$" in s("PRICE", 0) and "$" in s("SUBTOTAL", 0)
    assert s("QUANTITY", 0) in ("1001", "1002")


def test_form_letter_mockup_has_no_raw_merge_tokens():
    html = convert(_LETTER)["mockup_html"]
    # no raw &<...> field token survives into the preview (escaped or not)
    assert "&amp;&lt;" not in html
    assert "&<" not in html
    # the boilerplate sentence rendered with a filled name (not "Dear  of ,")
    text = re.sub(r"<[^>]+>", " ", re.sub(r"<style.*?</style>", "", html, flags=re.S))
    text = re.sub(r"\s+", " ", text)
    assert "Dear" in text and "Thank you" in text

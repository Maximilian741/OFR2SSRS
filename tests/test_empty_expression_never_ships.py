"""A bare ``=`` is not a value — and must never reach an RDL.

``<Value>=</Value>`` is an expression with no body. The Report Server
rejects it at publish (the publish gate's ``publish.expression_empty``
class), so it is upload-fatal in exactly the way this project treats as
P0. It is NOT hypothetical: the first-ever gating of a newly-wired
corpus found 12 real reports shipping four of them each, produced by the
breakdown text path when every token inside a boilerplate string
resolved to nothing in that dataset's scope — the resolver emitted its
``=`` prefix around an empty body and the caller's ``if not val.strip()``
guard let it through, because ``"="`` is truthy.

Two layers are guarded here, mirroring the internal-marker pair:
  * the SITE — an empty-bodied expression is treated like empty text;
  * the CHOKE POINT — a final sweep plus a generate-time invariant, so
    no future resolver can ship one regardless of which pass built it.
"""
from __future__ import annotations

import re

import pytest

from converter.generators import rdl as R


# --------------------------------------------------------------------------
# the predicate itself
# --------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["=", " = ", "=\n", "=\t "])
def test_a_bare_equals_is_recognised_as_an_empty_expression(value):
    assert R._is_empty_expression(value)


@pytest.mark.parametrize("value", [
    "=Fields!X.Value",
    "=Nothing",
    '="literal"',
    "plain text",          # not an expression at all
    "",                    # empty text is empty, but not an EXPRESSION
    None,
    "= 0",                 # a body of "0" is a real expression
])
def test_real_values_are_not_mistaken_for_empty_expressions(value):
    assert not R._is_empty_expression(value)


# --------------------------------------------------------------------------
# the choke point
# --------------------------------------------------------------------------

def _doc_with(values):
    """A minimal element tree carrying one <Value> per supplied string."""
    root = R.ET.Element("Report")
    for i, v in enumerate(values):
        el = R.ET.SubElement(root, "Textbox")
        el.set("Name", f"Tb_{i}")
        val = R.ET.SubElement(el, "Value")
        val.text = v
    return root


def test_the_sweep_drops_empty_expressions_and_keeps_every_real_value():
    root = _doc_with(["=", "=Fields!KEEP.Value", " = ", "plain", '="lit"'])
    R._strip_empty_expressions(root)
    got = [e.text for e in root.iter("Value")]
    assert got == [None, "=Fields!KEEP.Value", None, "plain", '="lit"'], got


def test_the_invariant_raises_on_a_serialized_empty_expression():
    body = "<Report><Textbox><Value>=</Value></Textbox></Report>"
    with pytest.raises(AssertionError, match="empty expression"):
        R._assert_no_empty_expressions(body)


def test_the_invariant_passes_a_clean_document():
    body = ("<Report><Textbox><Value>=Fields!A.Value</Value></Textbox>"
            "<Textbox><Value>text</Value></Textbox></Report>")
    R._assert_no_empty_expressions(body)          # must not raise


# --------------------------------------------------------------------------
# gate-can-fail: the sweep is load-bearing, not decorative
# --------------------------------------------------------------------------

def test_the_invariant_catches_what_the_sweep_would_have_missed():
    """If the sweep is ever bypassed, the invariant must still refuse.

    This is the proof the pair cannot BOTH be inert: a document built
    without the sweep still fails the serialized check, so a future pass
    that forgets to clean up cannot ship silently.
    """
    root = _doc_with(["="])
    body = R.ET.tostring(root, encoding="unicode")   # sweep deliberately skipped
    assert re.search(r"<Value>\s*=\s*</Value>", body)
    with pytest.raises(AssertionError):
        R._assert_no_empty_expressions(body)

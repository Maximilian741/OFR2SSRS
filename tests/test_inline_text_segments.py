"""Oracle <textSegment>s flow INLINE, and declared indentation is ink.

Two rules, both truth-measured on a real Oracle-produced letter PDF.

1. INLINE FLOW.  The segments of ONE Oracle <text> object are runs of the
   same flowing paragraph, not lines.  Truth page: the plain run ends its
   line at y=3.8818in and the BOLD run that continues the sentence opens at
   x=4.9310in on that same baseline (y=3.8825in), mid-line.  The converter
   used to emit one <Paragraph> per segment, so an 11-segment boilerplate
   object printed as 11 stacked lines with every styled phrase on a line of
   its own.  A new paragraph may start only where the DECLARATION breaks the
   line -- a newline inside the CDATA payload, or an inline <br>.

2. DECLARED INDENTATION SURVIVES.  The same letter declares a lettered
   clause list with leading spaces INSIDE the CDATA: 5 spaces before each
   clause letter, 10 before each of that clause's continuation lines.  Truth
   first-glyph x, measured per glyph on a box whose left edge is 0.5000in:

       lead-in line       (0 declared spaces)   0.5000in
       each clause letter (5 declared spaces)   0.6931in
       each wrap line     (10 declared spaces)  0.8861in

   i.e. exactly 5 and 10 space-widths in from the box's left edge, and the
   MS engine advances by every one of them (probe-measured on a static RDL:
   a run declared with 5 leading spaces starts its first glyph 5 space-
   widths in).  Ours printed all three flush left at 0.5000in.

   This does NOT reopen the settled CDATA-normalization rule.  The exporter
   pretty-prints every literal onto its own indented line, and THAT frame is
   still normalized away -- see _strip_export_indent_frame, which removes the
   frame and only the frame.  The distinguishing structure is not a guess
   about "label vs prose": it is where the whitespace physically sits.
   Whitespace OUTSIDE the CDATA is the exporter's and goes; whitespace INSIDE
   it is the author's and stays.  A single-line caption therefore still
   normalizes to a bare label (asserted below), because all of its edge
   whitespace is outside the CDATA.

Synthetic fixture only -- no client data.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from converter import convert
from converter.parsers.oracle_xml import _strip_export_indent_frame

NS = ("{http://schemas.microsoft.com/sqlserver/reporting/"
      "2008/01/reportdefinition}")

# The declared indentation of the two clause levels in the fixture below.
CLAUSE_INDENT = 5
WRAP_INDENT = 10


def _seg(text: str, size: int = 10, bold: bool = False) -> str:
    """One <textSegment>, written the way a real export pretty-prints it:
    the CDATA payload on its own line, framed by the element's indent."""
    b = ' bold="yes"' if bold else ""
    return (
        '            <textSegment>\n'
        f'              <font face="Arial" size="{size}"{b}/>\n'
        '              <string>\n'
        f'              <![CDATA[{text}]]>\n'
        '              </string>\n'
        '            </textSegment>\n'
    )


def _source_xml() -> str:
    caption = _seg("Widget Holder:")
    # A ONE-LINE box (0.16in @ 8pt) whose declaration carries a line break:
    # Oracle prints one line there, so the break is export formatting.
    oneline = _seg("expires\n", size=8) + _seg("&ROW_AMT", size=8, bold=True)
    # A growable, full-width prose body: a bold phrase opens MID-SENTENCE and
    # must continue the same line.
    prose = (_seg("You may mail your payment or pay at <b>")
             + _seg("Widget Payment Desk", bold=True)
             + _seg("</b> for account &ROW_KEY today."))
    # Declared line breaks + declared indentation, uniform font (the segments
    # are all one style, so this exercises the whole-text path).
    clauses = (_seg("The charge consists of:\n")
               + _seg("     a)  a base charge of &ROW_AMT plus,\n")
               + _seg("          any handling levied on the account."))
    # Indented prose carrying NO &token at all -- a different emission branch
    # (a literal line list, no expression resolution) under the same rule.
    plain = (_seg("Terms of the arrangement:\n")
             + _seg("     i)  no deposit is required,\n")
             + _seg("          nor any renewal notice."))
    # Same clause shape, but MIXED styles: the clause marker is bold and the
    # rest of the clause continues inline on that line.
    # The indented runs carry &tokens on purpose: that is what routes them
    # through the token resolver, whose line normalizer would otherwise
    # collapse the declared indent to a single space.
    mixed = (_seg("The filing fee consists of:\n")
             + _seg("     a)  a filing charge of &ROW_AMT", bold=True)
             + _seg(" plus,\n")
             + _seg("          any surcharge levied on &ROW_KEY."))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<report name="INLINESEG" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_MAIN">
      <select><![CDATA[SELECT ROW_KEY, ROW_AMT FROM T_MAIN]]></select>
      <group name="G_MAIN">
        <dataItem name="ROW_KEY" datatype="vchar2" width="60"
         defaultLabel="Row Key">
          <dataDescriptor expression="ROW_KEY" order="1" width="60"/>
        </dataItem>
        <dataItem name="ROW_AMT" datatype="vchar2" width="20"
         defaultLabel="Row Amt">
          <dataDescriptor expression="ROW_AMT" order="2" width="20"/>
        </dataItem>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="main">
    <body height="9.6">
      <frame name="M_FRAME">
        <geometryInfo x="0.00" y="0.00" width="7.50" height="5.00"/>
        <text name="B_CAPTION" minWidowLines="1">
          <textSettings spacing="0"/>
          <geometryInfo x="0.00" y="0.10" width="3.00" height="0.16"/>
{caption}        </text>
        <text name="B_ONELINE" minWidowLines="1">
          <textSettings spacing="0"/>
          <geometryInfo x="0.00" y="0.40" width="3.00" height="0.16"/>
{oneline}        </text>
        <text name="B_PROSE" minWidowLines="1">
          <textSettings spacing="0"/>
          <geometryInfo x="0.00" y="0.80" width="7.50" height="0.95"/>
          <generalLayout verticalElasticity="variable"/>
{prose}        </text>
        <text name="B_CLAUSES" minWidowLines="1">
          <textSettings spacing="0"/>
          <geometryInfo x="0.00" y="2.00" width="7.50" height="1.20"/>
          <generalLayout verticalElasticity="variable"/>
{clauses}        </text>
        <text name="B_PLAINCLAUSE" minWidowLines="1">
          <textSettings spacing="0"/>
          <geometryInfo x="0.00" y="3.40" width="7.50" height="0.60"/>
          <generalLayout verticalElasticity="variable"/>
{plain}        </text>
        <text name="B_MIXCLAUSE" minWidowLines="1">
          <textSettings spacing="0"/>
          <geometryInfo x="0.00" y="4.20" width="7.50" height="0.60"/>
          <generalLayout verticalElasticity="variable"/>
{mixed}        </text>
      </frame>
      <repeatingFrame name="R_MAIN" source="G_MAIN" printDirection="down">
        <geometryInfo x="0.00" y="5.20" width="7.50" height="0.20"/>
        <field name="F_KEY" source="ROW_KEY" alignment="start">
          <font face="Arial" size="10"/>
          <geometryInfo x="0.00" y="5.20" width="3.00" height="0.19"/>
        </field>
      </repeatingFrame>
    </body>
  </section>
  </layout>
</report>
"""


@pytest.fixture(scope="module")
def boxes():
    """{declared literal fragment -> [[run value, ...], ...]} for every
    emitted Textbox, keyed by a fragment of its own declared text so no
    generated element name is hard-coded."""
    root = ET.fromstring(convert(_source_xml().encode(),
                                 "INLINESEG.xml")["rdl_xml"].encode())
    out = []
    for tb in root.iter(NS + "Textbox"):
        paras = []
        for p in tb.findall(".//" + NS + "Paragraph"):
            runs = []
            for r in p.findall(".//" + NS + "TextRun"):
                runs.append({
                    "value": r.find(NS + "Value").text or "",
                    "bold": r.find(".//" + NS + "FontWeight") is not None,
                })
            paras.append(runs)
        out.append(paras)
    return out


def _by_fragment(boxes, fragment):
    hits = [p for p in boxes
            if any(fragment in r["value"] for para in p for r in para)]
    assert len(hits) == 1, (
        f"expected exactly one emitted box carrying {fragment!r}, got "
        f"{len(hits)}")
    return hits[0]


# --------------------------------------------------------------------------
# 1. inline flow
# --------------------------------------------------------------------------

def test_mid_sentence_styled_segment_stays_on_its_line(boxes):
    """Three declared segments, one sentence, no declared break -> ONE
    paragraph of three runs, the middle one bold."""
    paras = _by_fragment(boxes, "You may mail your payment")
    assert len(paras) == 1, (
        f"a sentence with no declared line break must emit ONE paragraph; "
        f"got {len(paras)} (one per segment = the defect: every styled "
        f"phrase starts its own line)")
    runs = paras[0]
    assert len(runs) == 3, (
        f"each declared segment must survive as its own run; got {len(runs)}")
    assert [r["bold"] for r in runs] == [False, True, False], (
        "the mid-sentence segment must keep its declared weight as an INLINE "
        "run")
    # The run-boundary spaces are declared ("... pay at <b>" / "</b> for
    # account ..."); losing them glues the words together on the page.
    assert runs[0]["value"].endswith(' at "'), runs[0]["value"]
    assert runs[2]["value"].startswith('=" for account'), runs[2]["value"]


def test_declared_line_break_starts_a_new_paragraph(boxes):
    """A newline the CDATA actually carries is a real break: three declared
    lines -> three paragraphs, with the bold clause marker and the plain
    remainder of that clause flowing INLINE inside the middle one."""
    paras = _by_fragment(boxes, "a filing charge")
    assert len(paras) == 3, (
        f"declared lines must map 1:1 to paragraphs; got {len(paras)}")
    assert [len(p) for p in paras] == [1, 2, 1], (
        f"the middle declared line carries two styled segments and they must "
        f"flow inline; got runs-per-paragraph {[len(p) for p in paras]}")
    assert paras[1][0]["bold"] and not paras[1][1]["bold"]


def test_one_line_box_keeps_its_segments_on_one_line(boxes):
    """Oracle's geometry is the discriminator: a box only one line tall
    renders one line, so a declared break inside it is export formatting.
    It must NOT become a second paragraph that overflows and clips the box
    (the label+value defect this rule was first written for)."""
    paras = _by_fragment(boxes, "expires")
    assert len(paras) == 1, (
        f"a one-line-tall box must emit ONE paragraph; got {len(paras)} — "
        f"stacked paragraphs overflow a CanGrow=false box and clip the value")
    assert any(r["bold"] for r in paras[0]), (
        "collapsing onto one line must not cost the segment its declared "
        "weight")
    joined = "".join(r["value"] for r in paras[0])
    assert '" "' in joined or " " in joined, (
        "the two collapsed segments need a separator between them")


# --------------------------------------------------------------------------
# 2. declared indentation
# --------------------------------------------------------------------------

@pytest.mark.parametrize("marker,indent", [
    ("a)  a base charge", CLAUSE_INDENT),
    ("any handling levied on the account.", WRAP_INDENT),
])
def test_declared_indentation_reaches_the_rdl_uniform_text(boxes, marker,
                                                           indent):
    """Whole-text path (all segments one style)."""
    paras = _by_fragment(boxes, marker)
    joined = "".join(r["value"] for para in paras for r in para)
    assert ('"' + " " * indent + marker) in joined, (
        f"the declared {indent}-space indent before {marker!r} was stripped; "
        f"Oracle prints it (truth first-glyph x sits exactly {indent} "
        f"space-widths in from the box's left edge)\n{joined}")


@pytest.mark.parametrize("marker,indent", [
    ("i)  no deposit is required,", CLAUSE_INDENT),
    ("nor any renewal notice.", WRAP_INDENT),
])
def test_declared_indentation_reaches_the_rdl_token_free_text(boxes, marker,
                                                              indent):
    """Token-free prose takes the literal-line branch, not the expression
    resolver; the same declared indentation must survive there too."""
    paras = _by_fragment(boxes, marker)
    joined = "".join(r["value"] for para in paras for r in para)
    assert ('"' + " " * indent + marker + '"') in joined, (
        f"the declared {indent}-space indent before {marker!r} was stripped "
        f"on the literal-line branch\n{joined}")


def test_declared_indentation_reaches_the_rdl_inline_runs(boxes):
    """Inline-runs path: the indent leads the paragraph's FIRST run and must
    not be handed to the token resolver, which normalizes a leading run to a
    single space."""
    paras = _by_fragment(boxes, "a filing charge")
    first = paras[1][0]["value"]
    assert first.startswith('="' + " " * CLAUSE_INDENT), first
    last = paras[2][0]["value"]
    assert last.startswith('="' + " " * WRAP_INDENT), last


def test_declared_interior_double_space_survives_a_token_boundary(boxes):
    """"a)  a base charge of &TOKEN plus," -- the two spaces after the clause
    letter and the single space that abuts the token are both declared, and
    Oracle prints them verbatim."""
    paras = _by_fragment(boxes, "a base charge of")
    joined = "".join(r["value"] for para in paras for r in para)
    assert "a)  a base charge" in joined, joined
    assert '" & Fields!ROW_AMT.Value & " plus,"' in joined.replace(
        'of "', 'of "'), joined


# --------------------------------------------------------------------------
# 3. the label rule this must NOT break
# --------------------------------------------------------------------------

def test_single_line_caption_still_normalizes_the_exporters_frame(boxes):
    """The exporter's pretty-print frame around a one-line caption is still
    normalized away -- it lives OUTSIDE the CDATA, so it is not ink."""
    paras = _by_fragment(boxes, "Widget Holder")
    assert len(paras) == 1 and len(paras[0]) == 1
    assert paras[0][0]["value"] == '="Widget Holder:"', (
        "a single-line caption must still emit as a bare label, with the "
        "exporter's newline+indent normalized away")


def test_strip_export_indent_frame_unit():
    """Unit leg: the frame goes, the payload -- indentation, interior
    newlines, trailing declared break -- stays."""
    f = _strip_export_indent_frame
    # the shape a real export writes
    assert f("\n              Widget Holder:\n              ") \
        == "Widget Holder:"
    assert f("\n                   a)  clause,\n\n              ") \
        == "     a)  clause,\n"
    assert f("\n              \n\n              ") == "\n"
    assert f("\n               \n              ") == " "
    # no frame at all -> untouched
    assert f("Widget Holder:") == "Widget Holder:"
    assert f("Line one\nLine two") == "Line one\nLine two"
    assert f("") == ""

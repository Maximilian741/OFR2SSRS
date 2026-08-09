"""Conditional print is DECLARATION-GATED, both ways.

Two halves of one contract, each a real production failure class:

1. A BOILERPLATE TEXT object (not just a field) that declares
   ``<advancedLayout formatTrigger="..."/>`` must carry the translated
   PL/SQL boolean as a real ``<Hidden>``.  Oracle suppresses that object
   on the happy path; a converter that drops the trigger prints a
   permanent "ERROR - ..." banner on every page of a customer-facing
   document.

2. A SIBLING object in the same region that declares NO format trigger --
   anywhere in its ancestor chain -- must carry NO ``<Hidden>``, neither
   on itself nor on an enclosing rectangle.  The tempting "it looks like
   an error box, hide it" heuristic is data loss: such an object prints
   unconditionally in Oracle and its text is empty exactly when the query
   supplies no error string.  Borrowing a neighbour's trigger would
   silently swallow the warning the report exists to surface.

Both halves are mutation-proved in-test comments (see the module docstring
of the sibling trigger tests): removing the emitter tag kills half 1,
falling back to "any translated trigger" kills half 2.
"""

import xml.etree.ElementTree as ET

NS = "{http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition}"


def _source() -> bytes:
    """One record region, three items: a trigger-gated notice, an
    ungated two-field warning concat, and a plain data field."""
    return (
        b'<?xml version="1.0"?>'
        b'<report name="GATEDVIS" DTDVersion="9.0.2.0.10">'
        b'<data><dataSource name="Q_Main"><select><![CDATA['
        b'select rec_id, rec_name, warn_txt, warn_note, side_flag from recs'
        b']]></select><group name="G_Rec">'
        b'<dataItem name="REC_ID" datatype="number"/>'
        b'<dataItem name="REC_NAME" datatype="vchar2"/>'
        b'<dataItem name="WARN_TXT" datatype="vchar2"/>'
        b'<dataItem name="WARN_NOTE" datatype="vchar2"/>'
        b'<dataItem name="SIDE_FLAG" datatype="vchar2"/>'
        b'</group></dataSource></data>'
        b'<programUnits><function name="f_gate_ft"><textSource><![CDATA['
        b'FUNCTION F_Gate_FT RETURN BOOLEAN IS BEGIN '
        b"IF :side_flag = 'Y' THEN RETURN(TRUE); ELSE RETURN(FALSE); "
        b'END IF; END;'
        b']]></textSource></function></programUnits>'
        b'<layout><section name="main"><body width="8.5" height="11.0">'
        b'<repeatingFrame name="R_Rec" source="G_Rec" printDirection="down">'
        b'<geometryInfo x="0" y="0" width="7.5" height="3.0"/>'
        b'<text name="B_Gated"><textSettings spacing="single"/>'
        b'<geometryInfo x="0.2" y="0.2" width="4.0" height="0.30"/>'
        b'<advancedLayout formatTrigger="f_gate_ft"/>'
        b'<textSegment><font face="Arial" size="10"/><string><![CDATA['
        b'GATED NOTICE]]></string></textSegment></text>'
        b'<text name="B_Plain"><textSettings spacing="single"/>'
        b'<geometryInfo x="0.2" y="0.7" width="4.0" height="0.30"/>'
        b'<textSegment><font face="Arial" size="10"/><string><![CDATA['
        b'&WARN_TXT &WARN_NOTE]]></string></textSegment></text>'
        b'<field name="F_N" source="REC_NAME">'
        b'<geometryInfo x="0.2" y="1.2" width="4.0" height="0.19"/></field>'
        b'</repeatingFrame></body></section></layout></report>')


def _hidden_for(rdl: str, needle: str):
    """``(owner_name, hidden_expr)`` for the nearest ancestor-or-self of the
    textbox whose Value contains ``needle``; ``None`` when nothing in the
    chain declares Visibility."""
    root = ET.fromstring(rdl.encode())
    parent = {c: p for p in root.iter() for c in p}
    for tb in root.iter(NS + "Textbox"):
        val = "".join((v.text or "") for v in tb.iter(NS + "Value"))
        if needle not in val:
            continue
        cur = tb
        while cur is not None:
            vis = cur.find(NS + "Visibility")
            if vis is not None:
                return (cur.get("Name"), vis.findtext(NS + "Hidden") or "")
            cur = parent.get(cur)
        return None
    raise AssertionError(f"no textbox carries {needle!r}: {rdl[:400]}")


def test_declared_trigger_reaches_a_boilerplate_text_hidden():
    """Half 1: the trigger on a TEXT object becomes a real <Hidden>."""
    from converter import convert

    rdl = convert(_source())["rdl_xml"]
    got = _hidden_for(rdl, "GATED NOTICE")
    assert got is not None, (
        "a boilerplate text declaring a format trigger lost its conditional "
        "print; it will now print on every page")
    owner, expr = got
    assert expr.startswith("="), (owner, expr)
    assert "SIDE_FLAG" in expr, (
        "the Hidden must be the TRANSLATED trigger body, not a placeholder: "
        f"{owner} -> {expr}")
    # Oracle RETURN TRUE = print, so the SSRS Hidden is the negation.
    assert "Not(" in expr, (
        f"Oracle's print-when boolean must invert into hide-when: {expr}")


def test_an_undeclared_sibling_never_borrows_a_neighbours_trigger():
    """Half 2: no trigger declared anywhere in the chain -> no <Hidden>.

    The sibling here is exactly the shape that invites a heuristic: a
    concat of two nullable message columns in a region that also holds a
    genuinely gated notice.  Suppressing it would hide the only warning a
    reader gets when the underlying query substitutes its error text.
    """
    from converter import convert

    rdl = convert(_source())["rdl_xml"]
    got = _hidden_for(rdl, "WARN_TXT")
    assert got is None, (
        "an object with NO declared format trigger (and none on any "
        f"ancestor) was given conditional visibility: {got}")
    # The plain data field in the same region is likewise unconditional.
    assert _hidden_for(rdl, "REC_NAME") is None


def test_undeclared_and_declared_siblings_coexist_in_one_region():
    """Both halves at once: the region emits exactly ONE conditional item.

    Guards the pairing directly -- a wiring bug that shifts the Hidden
    from the declared object onto its neighbour would keep the total at
    one and still be wrong, so assert WHICH item owns it.
    """
    from converter import convert

    rdl = convert(_source())["rdl_xml"]
    root = ET.fromstring(rdl.encode())
    conditional = [el.get("Name") for el in root.iter()
                   if el.find(NS + "Visibility") is not None]
    assert len(conditional) == 1, (
        f"exactly one declared-conditional item expected, got {conditional}")
    gated = _hidden_for(rdl, "GATED NOTICE")
    assert gated is not None and gated[0] == conditional[0], (
        "the Hidden landed on the wrong item: "
        f"owner={conditional[0]} gated={gated}")
    assert "data-ft" not in rdl and "data-cf" not in rdl, (
        "internal tags must always strip -- they are not schema-valid RDL")

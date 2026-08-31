"""EACH COVER OBJECT KEEPS ITS OWN DECLARED FORMAT TRIGGER.

Oracle evaluates a layout object's ``formatTrigger`` per OBJECT, and an
enclosing frame's trigger gates every member on top of it -- the two AND
(print only when both pass).  SSRS ``Hidden`` is the negation, so the two
conditions OR.

The failure this guards, measured on a real conditional criteria cover: a
declared form row's LABEL box was handed its neighbouring VALUE object's
trigger chain -- leaf trigger included -- so both boxes emitted a
byte-identical ``<Hidden>`` and the whole row appeared/disappeared on a
condition only ONE of its two objects declares.  The mirror failure is just
as wrong: dropping the object's own leaf and emitting only the frame's
condition makes every member of a variant frame print together.

Both cover emitters are exercised:

* a single declared form row routes through the SYNTHESIZED cover
  (``_attach_cover_hidden``);
* three or more objects on two or more declared rows route through the
  DECLARED-geometry cover (``_cover_hidden_expr``).

MUTATION PROOF (run by hand, both must go RED):

1.  In ``_attach_cover_hidden``, make the donor contribute its full chain
    again (``_eff_ft.get(id(_f))`` for every candidate instead of
    ``_frame_ft`` for donors) -> ``test_label_box_does_not_borrow...``
    fails: the label's Hidden picks up P_SHOW.
2.  At the label call site, restore ``_attach_cover_hidden(ltb,
    value_field, label_field)`` -> the same test fails for the same reason.
3.  In ``_cover_hidden_expr``, drop the leaf link from the chain (skip
    ``chain[0]``) -> ``test_declared_cover_value_box_ands...`` fails: the
    value box loses P_SHOW and matches its frame byte for byte.
"""

import xml.etree.ElementTree as ET

NS = "{http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition}"


def _source(nrows: int) -> bytes:
    """A criteria cover of ``nrows`` declared label:value rows inside ONE
    frame that declares its own format trigger.  Only the FIRST row's value
    object declares a trigger of its own."""
    rows, y = [], 1.0
    for i in range(nrows):
        own = ('<advancedLayout formatTrigger="f_val_ft"/>' if i == 0 else "")
        rows.append(
            f'<text name="B_LBL{i}"><textSettings spacing="single"/>'
            f'<geometryInfo x="0.50" y="{y}" width="1.80" height="0.20"/>'
            f'<textSegment><font face="Arial" size="10"/><string><![CDATA['
            f'Label {i}:]]></string></textSegment></text>'
            f'<field name="F_VAL{i}" source="SITE_NM">'
            f'<geometryInfo x="3.00" y="{y}" width="2.50" height="0.20"/>'
            f'{own}</field>')
        y += 0.40
    return (
        '<?xml version="1.0"?>'
        '<report name="COVFT" DTDVersion="9.0.2.0.10">'
        '<data><dataSource name="Q_Main"><select><![CDATA['
        'select site_id, site_nm from sites]]></select><group name="G_Site">'
        '<dataItem name="SITE_ID" datatype="number"/>'
        '<dataItem name="SITE_NM" datatype="vchar2"/>'
        '</group></dataSource>'
        '<userParameter name="P_MODE" datatype="character"/>'
        '<userParameter name="P_SHOW" datatype="character"/>'
        '</data>'
        '<programUnits>'
        '<function name="f_frame_ft"><textSource><![CDATA['
        "FUNCTION F_Frame_FT RETURN BOOLEAN IS BEGIN IF :P_MODE = 'A' THEN "
        'RETURN(TRUE); ELSE RETURN(FALSE); END IF; END;'
        ']]></textSource></function>'
        '<function name="f_val_ft"><textSource><![CDATA['
        "FUNCTION F_Val_FT RETURN BOOLEAN IS BEGIN IF :P_SHOW = 'Y' THEN "
        'RETURN(TRUE); ELSE RETURN(FALSE); END IF; END;'
        ']]></textSource></function>'
        '</programUnits>'
        '<layout><section name="header"><body width="8.5" height="11.0">'
        '<frame name="M_Cover">'
        '<geometryInfo x="0.25" y="0.50" width="7.0" height="4.0"/>'
        '<advancedLayout formatTrigger="f_frame_ft"/>'
        + "".join(rows) +
        '</frame></body></section>'
        '<section name="main"><body width="8.5" height="11.0">'
        '<repeatingFrame name="R_Site" source="G_Site" printDirection="down">'
        '<geometryInfo x="0" y="0" width="7.5" height="0.4"/>'
        '<field name="F_S" source="SITE_NM">'
        '<geometryInfo x="0.2" y="0.05" width="4.0" height="0.19"/></field>'
        '</repeatingFrame></body></section>'
        '</layout></report>').encode()


def _cover_hidden(nrows: int) -> dict:
    """{item name: Hidden expression or None} for every emitted cover box."""
    from converter import convert

    root = ET.fromstring(convert(_source(nrows))["rdl_xml"].encode())
    out = {}
    for el in root.iter():
        name = el.get("Name") or ""
        if not name.startswith("LcCov_"):
            continue
        vis = el.find(NS + "Visibility")
        out[name] = vis.findtext(NS + "Hidden") if vis is not None else None
    assert out, "the cover emitted no boxes at all"
    return out


def test_label_box_does_not_borrow_its_value_neighbours_trigger():
    """Synthesized cover: the label declares NO trigger, so it carries the
    FRAME's condition alone -- never the value object's own."""
    hid = _cover_hidden(1)
    lbl, val = hid["LcCov_Lbl_0"], hid["LcCov_Val_0"]
    assert lbl and val, (
        f"both boxes sit inside a trigger-gated frame: {hid}")
    assert "P_SHOW" not in lbl, (
        "the label box borrowed its neighbouring value object's per-object "
        f"format trigger: {lbl}")
    assert "P_MODE" in lbl, (
        f"the label lost the enclosing frame's condition: {lbl}")
    assert lbl != val, (
        "objects with DIFFERENT declarations emitted byte-identical Hidden "
        f"expressions: {lbl}")


def test_value_box_ands_its_own_trigger_with_the_frames():
    """Synthesized cover: the value declares its own trigger, so BOTH
    conditions appear -- ORed, because Hidden is the negation of the
    ANDed print conditions."""
    val = _cover_hidden(1)["LcCov_Val_0"]
    assert "P_SHOW" in val and "P_MODE" in val, (
        "an object's own format trigger must AND with its frame's, not "
        f"replace it (and vice versa): {val}")
    assert " Or " in val, (
        f"print-when AND must invert into hide-when OR: {val}")


def test_declared_cover_value_box_ands_its_own_trigger_with_the_frames():
    """Declared-geometry cover (>=3 objects on >=2 rows): same contract."""
    hid = _cover_hidden(4)
    lbl0, val0 = hid["LcCov_Lbl_0"], hid["LcCov_Val_0"]
    assert "P_SHOW" in val0 and "P_MODE" in val0, (
        f"the declaring object lost its own trigger: {val0}")
    assert "P_SHOW" not in (lbl0 or ""), (
        f"a non-declaring object was given its neighbour's trigger: {lbl0}")
    assert lbl0 != val0, (lbl0, val0)


def _split_frame_source() -> bytes:
    """The same declared form row, but the label and the value live in
    SEPARATE frames: only the value object declares a trigger, and no frame
    declares one at all.  The label therefore has no trigger chain of its
    own, which is exactly when a neighbour is consulted."""
    return (
        '<?xml version="1.0"?>'
        '<report name="COVFT2" DTDVersion="9.0.2.0.10">'
        '<data><dataSource name="Q_Main"><select><![CDATA['
        'select site_id, site_nm from sites]]></select><group name="G_Site">'
        '<dataItem name="SITE_ID" datatype="number"/>'
        '<dataItem name="SITE_NM" datatype="vchar2"/>'
        '</group></dataSource>'
        '<userParameter name="P_MODE" datatype="character"/>'
        '</data>'
        '<programUnits>'
        '<function name="f_val_ft"><textSource><![CDATA['
        "FUNCTION F_Val_FT RETURN BOOLEAN IS BEGIN IF :P_MODE = 'Y' THEN "
        'RETURN(TRUE); ELSE RETURN(FALSE); END IF; END;'
        ']]></textSource></function>'
        '</programUnits>'
        '<layout><section name="header"><body width="8.5" height="11.0">'
        '<frame name="M_Plain">'
        '<geometryInfo x="0.25" y="0.50" width="7.0" height="1.0"/>'
        '<text name="B_LBL0"><textSettings spacing="single"/>'
        '<geometryInfo x="0.50" y="1.00" width="1.80" height="0.20"/>'
        '<textSegment><font face="Arial" size="10"/><string><![CDATA['
        'Label 0:]]></string></textSegment></text>'
        '</frame>'
        '<frame name="M_Other">'
        '<geometryInfo x="2.90" y="0.50" width="4.0" height="1.0"/>'
        '<field name="F_VAL0" source="SITE_NM">'
        '<geometryInfo x="3.00" y="1.00" width="2.50" height="0.20"/>'
        '<advancedLayout formatTrigger="f_val_ft"/></field>'
        '</frame>'
        '</body></section>'
        '<section name="main"><body width="8.5" height="11.0">'
        '<repeatingFrame name="R_Site" source="G_Site" printDirection="down">'
        '<geometryInfo x="0" y="0" width="7.5" height="0.4"/>'
        '<field name="F_S" source="SITE_NM">'
        '<geometryInfo x="0.2" y="0.05" width="4.0" height="0.19"/></field>'
        '</repeatingFrame></body></section>'
        '</layout></report>').encode()


def test_a_neighbour_donates_only_its_frames_links_never_its_own():
    """A box whose object declares nothing AND sits under no gated frame
    stays unconditional, even when the object beside it on the same
    declared row carries a per-object trigger."""
    from converter import convert

    root = ET.fromstring(convert(_split_frame_source())["rdl_xml"].encode())
    hid = {}
    for el in root.iter():
        name = el.get("Name") or ""
        if not name.startswith("LcCov_"):
            continue
        vis = el.find(NS + "Visibility")
        hid[name] = vis.findtext(NS + "Hidden") if vis is not None else None
    assert hid.get("LcCov_Val_0"), (
        f"the declaring object lost its own trigger entirely: {hid}")
    assert hid.get("LcCov_Lbl_0") is None, (
        "an object that declares no trigger -- and whose frames declare "
        "none either -- was given its row neighbour's per-object "
        f"condition: {hid}")


def _formula_ref_source() -> bytes:
    """A criteria cover whose per-object trigger gates on FORMULA-dataset
    values: a report-scoped <summary> over a <placeholder> column plus a
    <formula> column tested with LIKE.  A second frame is gated on the
    POSTFIX ``NOT LIKE`` idiom (a success block that prints only when no
    error text exists anywhere).  This is the measured production shape:
    both trigger classes DECLINED before, so every object in the frame
    collapsed to the frame's condition -- byte-identical Hidden where the
    declarations differ."""
    return (
        '<?xml version="1.0"?>'
        '<report name="COVFT3" DTDVersion="9.0.2.0.10">'
        '<data><dataSource name="Q_Main"><select><![CDATA['
        'select site_id, site_nm from sites]]></select><group name="G_Site">'
        '<dataItem name="SITE_ID" datatype="number"/>'
        '<dataItem name="SITE_NM" datatype="vchar2"/>'
        '</group></dataSource>'
        '<userParameter name="P_MODE" datatype="character"/>'
        '<formula name="CF_NOTE" datatype="character"/>'
        '<placeholder name="CP_ERR_CT" datatype="number"/>'
        '<summary name="CS_ERR_CT" datatype="number" function="sum" '
        'source="CP_ERR_CT" reset="report"/>'
        '</data>'
        '<programUnits>'
        '<function name="f_frame_ft"><textSource><![CDATA['
        "FUNCTION F_Frame_FT RETURN BOOLEAN IS BEGIN IF :P_MODE = 'A' THEN "
        'RETURN(TRUE); ELSE RETURN(FALSE); END IF; END;'
        ']]></textSource></function>'
        '<function name="f_err_ft"><textSource><![CDATA['
        "FUNCTION F_Err_FT RETURN BOOLEAN IS BEGIN "
        "IF :CS_ERR_CT > 0 OR UPPER(:CF_NOTE) LIKE '%ERROR%' THEN "
        'RETURN(TRUE); ELSE RETURN(FALSE); END IF; END;'
        ']]></textSource></function>'
        '<function name="f_ok_ft"><textSource><![CDATA['
        "FUNCTION F_Ok_FT RETURN BOOLEAN IS BEGIN "
        "IF UPPER(:CF_NOTE) NOT LIKE '%ERROR%' THEN "
        'RETURN(TRUE); ELSE RETURN(FALSE); END IF; END;'
        ']]></textSource></function>'
        '</programUnits>'
        '<layout><section name="header"><body width="8.5" height="11.0">'
        '<frame name="M_Cover">'
        '<geometryInfo x="0.25" y="0.50" width="7.0" height="2.0"/>'
        '<advancedLayout formatTrigger="f_frame_ft"/>'
        '<text name="B_LBL0"><textSettings spacing="single"/>'
        '<geometryInfo x="0.50" y="1.00" width="1.80" height="0.20"/>'
        '<textSegment><font face="Arial" size="10"/><string><![CDATA['
        'Label 0:]]></string></textSegment></text>'
        '<field name="F_VAL0" source="SITE_NM">'
        '<geometryInfo x="3.00" y="1.00" width="2.50" height="0.20"/>'
        '<advancedLayout formatTrigger="f_err_ft"/></field>'
        '<text name="B_LBL1"><textSettings spacing="single"/>'
        '<geometryInfo x="0.50" y="1.40" width="1.80" height="0.20"/>'
        '<textSegment><font face="Arial" size="10"/><string><![CDATA['
        'Label 1:]]></string></textSegment></text>'
        '<field name="F_VAL1" source="SITE_NM">'
        '<geometryInfo x="3.00" y="1.40" width="2.50" height="0.20"/>'
        '</field>'
        '</frame>'
        '<frame name="M_Success">'
        '<geometryInfo x="0.25" y="2.60" width="7.0" height="0.5"/>'
        '<advancedLayout formatTrigger="f_ok_ft"/>'
        '<text name="B_OK0"><textSettings spacing="single"/>'
        '<geometryInfo x="0.50" y="2.70" width="4.00" height="0.20"/>'
        '<textSegment><font face="Arial" size="10"/><string><![CDATA['
        'All rows passed.]]></string></textSegment></text>'
        '</frame>'
        '</body></section>'
        '<section name="main"><body width="8.5" height="11.0">'
        '<repeatingFrame name="R_Site" source="G_Site" printDirection="down">'
        '<geometryInfo x="0" y="0" width="7.5" height="0.4"/>'
        '<field name="F_S" source="SITE_NM">'
        '<geometryInfo x="0.2" y="0.05" width="4.0" height="0.19"/></field>'
        '</repeatingFrame></body></section>'
        '</layout></report>').encode()


def _hidden_by_name(src: bytes) -> dict:
    from converter import convert

    root = ET.fromstring(convert(src)["rdl_xml"].encode())
    out = {}
    for el in root.iter():
        name = el.get("Name") or ""
        if not name.startswith("LcCov_"):
            continue
        vis = el.find(NS + "Visibility")
        out[name] = vis.findtext(NS + "Hidden") if vis is not None else None
    assert out, "the cover emitted no boxes at all"
    return out


def test_formula_column_leaf_trigger_translates_and_differs():
    """A per-object trigger gating on a report-scoped summary over a
    placeholder column AND a formula column resolves against the synthetic
    formula-resolution dataset -- exactly the way its value textboxes bind
    -- and ORs onto the frame's condition.  Before, both refs declined and
    the object emitted the frame's Hidden byte-for-byte (the measured
    seven-identical-objects cover)."""
    hid = _hidden_by_name(_formula_ref_source())
    val = hid.get("LcCov_Val_0") or ""
    lbl = hid.get("LcCov_Lbl_0") or ""
    assert "DS_REPORT_FORMULAS" in val, (
        "the object's own formula-referencing trigger declined -- its "
        f"condition collapsed to the frame's links alone: {val!r}")
    assert "CP_ERR_CT" in val and "CF_NOTE" in val, (
        f"the summary's SOURCE column and the formula column must both "
        f"resolve: {val!r}")
    assert "P_MODE" in val and " Or " in val, (
        "an object's own trigger must AND with its frame's (Hidden ORs), "
        f"never replace it: {val!r}")
    assert lbl and "P_MODE" in lbl and "DS_REPORT_FORMULAS" not in lbl, (
        "a sibling that declares NO trigger carries the frame's condition "
        f"alone: {lbl!r}")
    assert lbl != val, (
        "objects with DIFFERENT declarations emitted byte-identical "
        f"Hidden expressions: {val!r}")


def test_postfix_not_like_trigger_translates():
    """Oracle's postfix ``expr NOT LIKE pattern`` (the success-variant
    idiom: print only when no error text exists) must translate -- the
    dangling NOT was a trailing token and the whole trigger declined, so
    the success frame printed unconditionally."""
    hid = _hidden_by_name(_formula_ref_source())
    ok = next((h for n, h in hid.items()
               if h and "CF_NOTE" in h and "P_MODE" not in h), None)
    assert ok, (
        f"no cover box carries the NOT LIKE frame's condition: {hid}")
    assert "Like" in ok and "*ERROR*" in ok and "Not" in ok, (
        f"NOT LIKE must translate to a negated VB Like: {ok!r}")


def test_only_the_declaring_object_differs_from_its_frame():
    """Every OTHER object on the cover declares nothing, so all of them
    carry exactly the frame's condition -- and exactly one box differs.

    This is the whole-cover shape of the measured failure: seven boxes with
    one byte-identical Hidden between them, when the declarations differ.
    """
    hid = _cover_hidden(4)
    frame_only = hid["LcCov_Lbl_1"]
    assert frame_only and "P_MODE" in frame_only and "P_SHOW" not in frame_only
    differs = [n for n, h in hid.items() if h != frame_only]
    assert differs == ["LcCov_Val_0"], (
        "exactly the one object that declares its own format trigger may "
        f"differ from its frame's condition; got {differs} out of {hid}")

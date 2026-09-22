"""
PUBLISH-SCOPE EMITTER GUARDS.

Project fatal error #1 recurred because the generator could emit two
expressions that are perfectly happy locally and are REFUSED by Report
Server when it compiles the definition at upload:

  1. a scoped call whose dataset-side reference names a column that dataset
     does not declare --
       [SCOPE] "...refers to the field `X'. Report item expressions can only
       refer to fields within the current dataset scope or, if inside an
       aggregate, the specified dataset scope."
  2. a Lookup nested inside another Lookup's source key --
       [AGGREF] "Only one level of lookup is supported. A source,
       destination, or result expression can't include a reference to a
       lookup function."

Neither is visible to the local rails. The XSD encodes shape, not meaning.
The upload gate's engine leg runs in LAYOUT mode (expressions staticized),
and even with a live expression host ReportViewer renders both: it
evaluates an undeclared field as Nothing, and it happily evaluates the
nested call. Only the server's publish-time compile refuses them.

So both constructs are now unbuildable at the source: the correlated-lookup
builders return None instead, the caller reaches its honest-blank path, and
the decline is DISCLOSED through preflight. Every test here asserts the
generated RDL through the publish-semantics gate, which is the measure that
would have caught the rejected artifact.

Fixtures are synthetic and structural -- no customer report text.
"""
from __future__ import annotations

import re

from converter import convert
from converter.validators.publish_semantics import publish_violations


def _report(summary_source: str) -> bytes:
    """A three-level Oracle <link> chain Q_A -> Q_B -> Q_C, with a
    <summary> object CS_FAR always declared in Q_B's GROUP TREE and read
    from the Q_A scope.

    ``summary_source`` is the column that summary names:

      "B_NM"  the declared owner really owns it -> a legal one-hop
              correlated Lookup into Q_B must still be emitted;
      "C_VAL" the column belongs to the DEEPER child Q_C -> declared owner
              and true owner are different datasets, and no dataset
              declares both the correlation key and the wanted column.
              That is the real-world shape that produced the publish-fatal
              scope.
    """
    summary = ('<summary name="CS_FAR" source="' + summary_source +
               '" function="first"/>')
    gb = ('<group name="G_B"><dataItem name="B_KEY" datatype="number"/>'
          '<dataItem name="B_NM" datatype="vchar2"/>'
          + summary + '</group>')
    gc = ('<group name="G_C"><dataItem name="C_KEY" datatype="number"/>'
          '<dataItem name="C_VAL" datatype="vchar2"/></group>')
    return (
        '<?xml version="1.0"?><report name="SCOPE_T" DTDVersion="9.0.2.0.10">'
        '<data>'
        '<dataSource name="Q_A"><select><![CDATA[select a_key, a_nm '
        'from a]]></select>'
        '<group name="G_A"><dataItem name="A_KEY" datatype="number"/>'
        '<dataItem name="A_NM" datatype="vchar2"/></group></dataSource>'
        '<dataSource name="Q_B"><select><![CDATA[select b_key, b_nm '
        'from b]]></select>' + gb + '</dataSource>'
        '<dataSource name="Q_C"><select><![CDATA[select c_key, c_val '
        'from c]]></select>' + gc + '</dataSource>'
        '<link parentGroup="G_A" childQuery="Q_B" parentColumn="A_KEY" '
        'childColumn="B_KEY" sqlClause="where"/>'
        '<link parentGroup="G_B" childQuery="Q_C" parentColumn="B_NM" '
        'childColumn="C_KEY" sqlClause="where"/>'
        '</data>'
        '<layout><section name="main">'
        '<frame name="M_B"><geometryInfo x="0" y="0" width="8" height="6"/>'
        '<repeatingFrame name="R_A" source="G_A" printDirection="down">'
        '<geometryInfo x="0.2" y="1.3" width="7.5" height="1.6"/>'
        '<field name="F_NM" source="A_NM"><geometryInfo x="0.2" y="1.3" '
        'width="3" height="0.2"/></field>'
        '<field name="F_FAR" source="CS_FAR"><geometryInfo x="0.2" y="1.6" '
        'width="3" height="0.2"/></field>'
        '</repeatingFrame></frame></section></layout></report>'
    ).encode()


def _lookups(rdl: str):
    return re.findall(r'Lookup(?:Set)?\([^<]*?"\w+"\)', rdl)


# ---------------------------------------------------------------------------
# 1. the scope rule
# ---------------------------------------------------------------------------

def test_summary_whose_source_lives_in_a_deeper_child_is_never_misscoped():
    """The publish-fatal shape: an Oracle <summary> declared in Q_B's group
    tree whose source column belongs to Q_C. Scoping the correlated Lookup
    to the DECLARED owner ships
    ``Lookup(key, key, Fields!C_VAL.Value, "Q_B")`` -- Q_B does not declare
    C_VAL, so the server rejects the report at upload while every local
    rail stays green. The builder must decline instead."""
    rdl = convert(_report("C_VAL"))["rdl_xml"]
    assert publish_violations(rdl) == []
    for lk in _lookups(rdl):
        # no Lookup may name Q_B as its scope while pulling a Q_C column
        assert not ("C_VAL" in lk and '"Q_B"' in lk), lk


def test_declined_value_is_blank_and_disclosed_not_silently_dropped():
    """A blank cell nobody is told about is a lie by omission. The decline
    must reach the operator through preflight, naming the value, both
    scopes and a remedy."""
    res = convert(_report("C_VAL"))
    declined = [i for i in ((res.get("preflight") or {}).get("issues") or [])
                if (i.get("rule") or "").startswith(
                    "rdl.correlation_declined")]
    assert declined, (res.get("preflight") or {}).get("issues")
    msg = declined[0]["message"]
    assert "CS_FAR" in msg and "Q_A" in msg
    assert "BLANK" in msg
    assert "Lookup" in msg or "subreport" in msg  # a remedy is offered
    assert (res.get("preflight") or {}).get("verdict") in ("AMBER", "RED",
                                                           "BLOCKER")


def test_a_correctly_declared_summary_still_resolves():
    """The guard must not blanket-decline: when the <summary>'s source column
    really does belong to the query that declares it, the correlated Lookup
    is legal and must still be emitted -- SAME topology, SAME code path,
    only the source column differs. Without this the fix would be a
    regression dressed as a safety net."""
    rdl = convert(_report("B_NM"))["rdl_xml"]
    assert publish_violations(rdl) == []
    assert any("B_NM" in lk and '"Q_B"' in lk for lk in _lookups(rdl)), \
        _lookups(rdl)


def test_the_scope_rule_itself():
    """The rule every correlated-lookup builder asks, tested directly.

    Two of the four builders are reached with a DECLARED owner (the Oracle
    <summary> path) and are exercised end-to-end by the tests above; the
    other two are reached only with a column's TRUE owner today, so they
    satisfy the rule by construction. Testing the shared rule here keeps
    all four call sites honest instead of leaving two of them unproven."""
    from converter.generators.rdl import _ds_declares

    fields = {"Q_A": {"A_KEY": "A_Key"}, "Q_B": {"B_KEY": "B_Key"}}
    assert _ds_declares(fields, "Q_A", "A_KEY")
    assert _ds_declares(fields, "q_a", "a_key")       # case-insensitive
    assert not _ds_declares(fields, "Q_A", "B_KEY")   # the publish failure
    assert not _ds_declares(fields, "Q_MISSING", "A_KEY")
    assert not _ds_declares(fields, "", "A_KEY")
    assert not _ds_declares(fields, "Q_A", "")
    assert not _ds_declares({}, "Q_A", "A_KEY")       # never crashes empty


# ---------------------------------------------------------------------------
# 2. the one-level-of-lookup rule
# ---------------------------------------------------------------------------

def test_no_generated_report_ever_nests_a_lookup():
    """Corpus-independent invariant: whatever the link topology, a nested
    Lookup is never emitted."""
    for source in ("B_NM", "C_VAL"):
        rdl = convert(_report(source))["rdl_xml"]
        assert "Lookup(Lookup(" not in rdl.replace(" ", "")
        assert publish_violations(rdl) == []


# ---------------------------------------------------------------------------
# 2b. the verdict the operator reads must carry the publish rules
# ---------------------------------------------------------------------------

def test_the_publish_rules_reach_the_pre_download_verdict(monkeypatch):
    """A gate only protects the repo. The person who uploads the RDL runs
    convert() on a report no gate has ever seen, and the pre-download
    verdict they read said READY for the file the customer's server then
    refused. So convert()'s preflight now carries the publish-semantics
    findings itself, as BLOCKER ("the file will not upload or open
    cleanly" -- the words the UI already puts next to that severity).

    No source in any corpus produces a violation any more, which is
    precisely why the WIRING needs its own proof that it can fail: the
    rule engine is stubbed to report one, and the finding must appear in
    the verdict the operator reads."""
    import converter.validators.publish_semantics as ps

    monkeypatch.setattr(ps, "audit_publish_semantics", lambda _x: {
        "violations": [{"rule": "publish.stubbed_rule", "where": "Tb_1/Value",
                        "message": "stubbed publish violation",
                        "severity": "BLOCKER"}]})

    pf = convert(_report("B_NM")).get("preflight") or {}
    hits = [i for i in (pf.get("issues") or [])
            if i.get("rule") == "rdl.publish.stubbed_rule"]
    assert hits, pf.get("issues")
    assert hits[0]["severity"] == "BLOCKER"
    assert "Tb_1/Value" in hits[0]["message"]
    assert "REJECTS this at upload" in hits[0]["message"]
    assert pf.get("verdict") == "BLOCKER"


def test_a_clean_conversion_gets_no_publish_blocker():
    """The wiring must not manufacture findings: a report the rule engine
    passes carries no publish BLOCKER at all."""
    res = convert(_report("B_NM"))
    pf = res.get("preflight") or {}
    assert not [i for i in (pf.get("issues") or [])
                if (i.get("rule") or "").startswith("rdl.publish.")], \
        pf.get("issues")


# ---------------------------------------------------------------------------
# 3. the scope-retarget repair must not trade one publish failure for another
# ---------------------------------------------------------------------------

_RDL_HEAD = (
    '<Report xmlns="http://schemas.microsoft.com/sqlserver/reporting/'
    '2008/01/reportdefinition">'
    '<DataSets>'
    '<DataSet Name="D1"><Fields>'
    '<Field Name="K1"><DataField>K1</DataField></Field>'
    '<Field Name="K2"><DataField>K2</DataField></Field>'
    '</Fields></DataSet>'
    '<DataSet Name="D2"><Fields>'
    '<Field Name="K1"><DataField>K1</DataField></Field>'
    '<Field Name="RES"><DataField>RES</DataField></Field>'
    '</Fields></DataSet>'
    '<DataSet Name="D3"><Fields>'
    '<Field Name="K1"><DataField>K1</DataField></Field>'
    '<Field Name="K2"><DataField>K2</DataField></Field>'
    '<Field Name="OK"><DataField>OK</DataField></Field>'
    '</Fields></DataSet>'
    '</DataSets><Body><ReportItems><Textbox Name="T"><Paragraphs>'
    '<Paragraph><TextRuns><TextRun><Value>')
_RDL_TAIL = ('</Value></TextRun></TextRuns></Paragraph></Paragraphs>'
             '</Textbox></ReportItems></Body></Report>')


def _retarget(expr: str) -> str:
    import xml.etree.ElementTree as ET

    from converter.generators.rdl import _repair_misscoped_aggregate_refs
    root = ET.fromstring(_RDL_HEAD + expr.replace("&", "&amp;") + _RDL_TAIL)
    _repair_misscoped_aggregate_refs(root)
    for el in root.iter():
        if el.tag.split("}")[-1] == "Value":
            return el.text or ""
    raise AssertionError("no <Value> found")


def test_lookup_retarget_refuses_when_the_new_scope_loses_the_key():
    """Moving a Lookup's scope to the dataset that declares its RESULT is
    only a repair if that dataset also declares the DESTINATION key: the
    key (argument 2) is evaluated in the named dataset too. D2 declares RES
    but not K2, so retargeting would swap a rejection on RES for a
    rejection on K2 -- and hide it, because the expression now *looks*
    consistent. Leave it alone so the gate and the preflight can report
    it."""
    expr = ('=Lookup(Fields!K1.Value & "|" & Fields!K2.Value, '
            'Fields!K1.Value & "|" & Fields!K2.Value, '
            'Fields!RES.Value, "D1")')
    assert _retarget(expr) == expr


def test_lookup_retarget_still_repairs_when_the_new_scope_is_complete():
    """The repair itself must survive: D3 declares the result AND both key
    columns, so the retarget is a genuine fix and still happens."""
    expr = ('=Lookup(Fields!K1.Value & "|" & Fields!K2.Value, '
            'Fields!K1.Value & "|" & Fields!K2.Value, '
            'Fields!OK.Value, "D1")')
    assert _retarget(expr).endswith('"D3")')


def test_plain_aggregate_retarget_is_untouched_by_the_lookup_guard():
    """A single-field aggregate has no destination key, so the original
    repair (a drill-through parameter scoped to the wrong dataset) keeps
    working exactly as before."""
    assert _retarget('=First(Fields!RES.Value, "D1")') == \
        '=First(Fields!RES.Value, "D2")'

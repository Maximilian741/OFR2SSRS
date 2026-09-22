"""A two-hop Oracle <link> chain collapses to ONE legal Lookup only on proof.

Oracle chains links (permit -> permittee -> contact). SSRS forbids nesting a
Lookup inside another's key -- "Only one level of lookup is supported" is a
publish-time refusal the local engine never shows -- so a value two hops
away used to be declined and printed blank (disclosed, but blank). On a
customer's permit letter that blank was the contact org id the envelope
link forwards, so every envelope opened unfiltered by organisation.

The chain collapses when the far child's keys are values the MIDDLE query
merely relays from the bound row. That is now PROVEN from the middle
query's own SQL, two ways (rdl._declared_alias_relays):

    SAME EXPRESSION   ``SA.Site_Id AS Site_Id`` and ``SA.Site_Id AS
                      Permittee_Site_Id`` are the same column, so equal on
                      every row by SQL semantics;
    BIND EQUALITY     ``SELECT T.C AS Alias ... WHERE T.C = :k`` (or the
                      generator's own relaxed ``(:k IS NULL OR T.C = :k)``)
                      at the top level of the WHERE, where k is a key the
                      bound row supplies.

Nothing looser: a wrong relay would paint ANOTHER record's value on every
record, which is worse than the blank it replaces. So the file also pins
the refusals -- a middle query contributing a key of its OWN (an
application id between an applicant and its courses) must still decline --
and the ownership rule the fix exposed: a grandchild may never be joined by
_lookup_for_child on whichever of its binds happens to name a grandparent
column (a partial key = the first grandchild row on every record).

Every fixture is synthetic; no real report, column or parameter name
appears here.
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.generators import rdl as R  # noqa: E402
from converter.validators.publish_semantics import publish_violations  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures -- one three-query chain in three declared shapes
# ---------------------------------------------------------------------------

def _chain_xml(mid_select: str, mid_where: str, far_where: str) -> bytes:
    """root (prog, site) -> mid -> far. The far child's ``<summary>`` is
    declared in the MIDDLE query's group tree but sources a FAR column --
    the exact declared shape of the customer's report. A root-bound
    repeating frame prints that summary, so the resolver must reach two
    hops from the root scope."""
    return (
        '<?xml version="1.0"?><report name="CHAIN" DTDVersion="9.0.2.0.10"><data>'
        '<dataSource name="Q_ROOT"><select><![CDATA[SELECT p.prog prog, p.site site, p.label label FROM p]]></select>'
        '<group name="G_ROOT"><dataItem name="prog" datatype="number"/>'
        '<dataItem name="site" datatype="number"/>'
        '<dataItem name="label" datatype="vchar2"/></group></dataSource>'
        '<dataSource name="Q_MID"><select><![CDATA[' + mid_select + ' ' + mid_where + ']]></select>'
        '<group name="G_MID"><dataItem name="mid_site" datatype="number"/>'
        '<dataItem name="mid_org" datatype="number"/>'
        '<summary name="CS_FAR" source="far_org" function="first" reset="G_MID" compute="report"/>'
        '</group></dataSource>'
        '<dataSource name="Q_FAR"><select><![CDATA[SELECT c.org far_org FROM c ' + far_where + ']]></select>'
        '<group name="G_FAR"><dataItem name="far_org" datatype="number"/></group></dataSource>'
        '<link name="L1" parentGroup="G_ROOT" childQuery="Q_MID" condition="eq" sqlClause="where"/>'
        '<link name="L2" parentGroup="G_MID" childQuery="Q_FAR" condition="eq" sqlClause="where"/>'
        '</data><layout><section name="main"><body width="8" height="9">'
        '<repeatingFrame name="R" source="G_ROOT"><geometryInfo x="0" y="0" width="6" height="0.6"/>'
        '<field name="F_label" source="label"><geometryInfo x="0" y="0" width="2" height="0.2"/></field>'
        '<field name="F_far" source="CS_FAR"><geometryInfo x="0" y="0.3" width="2" height="0.2"/></field>'
        '</repeatingFrame></body></section></layout></report>'
    ).encode("utf-8")


# The customer's shape: the middle query RELAYS the root's site under a new
# alias, and the far child keys on that alias.
RELAY_XML = _chain_xml(
    "SELECT sa.site mid_site, sa.org mid_org FROM sa",
    "WHERE sa.prog = :prog AND sa.site = :site",
    "WHERE c.prog = :prog AND c.site = :mid_site")

# A REAL two-hop chain: the middle query contributes its OWN id (an
# application id) and the far child keys on it. Nothing relays; must decline.
OWN_KEY_XML = _chain_xml(
    "SELECT sa.app_id mid_site, sa.org mid_org FROM sa",
    "WHERE sa.prog = :prog AND sa.owner = :site",
    "WHERE c.prog = :prog AND c.app_id = :mid_site")

# The alias LOOKS like a relay but the WHERE equates a DIFFERENT column to
# the bound key, and no other alias selects the same expression: no proof.
UNPROVEN_XML = _chain_xml(
    "SELECT sa.site mid_site, sa.org mid_org FROM sa",
    "WHERE sa.prog = :prog AND sa.other = :site",
    "WHERE c.prog = :prog AND c.site = :mid_site")


def _far_lookups(rdl: str):
    """Every Lookup body targeting the far child, XML-unescaped (the RDL
    serialises the VB ``&`` concatenation as ``&amp;``)."""
    return [html.unescape(b) for b in re.findall(r'=Lookup\(([^<]*?), "Q_FAR"\)', rdl)]


# ---------------------------------------------------------------------------
# The collapse
# ---------------------------------------------------------------------------

def test_a_relayed_chain_collapses_to_one_lookup_with_the_full_composite_key():
    rdl = convert(RELAY_XML, target_db="oracle")["rdl_xml"]
    hits = _far_lookups(rdl)
    assert hits, "the far column was declined instead of resolved:\n" + rdl[-1500:]
    body = hits[0]
    # source key = the ROOT row's own keys; destination = the far child's
    # matching columns, one of them the relayed alias
    assert re.search(r'Fields!prog\.Value & "\|" & Fields!site\.Value', body, re.I), body
    assert re.search(r'Fields!prog\.Value & "\|" & Fields!mid_site\.Value', body, re.I), body
    assert "Fields!far_org.Value" in body


def test_the_collapsed_lookup_is_publish_legal():
    rdl = convert(RELAY_XML, target_db="oracle")["rdl_xml"]
    assert publish_violations(rdl) == []


def test_the_collapsed_lookup_never_nests_another_lookup():
    rdl = convert(RELAY_XML, target_db="oracle")["rdl_xml"]
    for body in _far_lookups(rdl):
        assert "Lookup(" not in body, "nested Lookup is a publish refusal: " + body


# ---------------------------------------------------------------------------
# The refusals -- proof or nothing
# ---------------------------------------------------------------------------

def test_a_real_two_hop_chain_still_declines_instead_of_guessing():
    """The middle query supplies a key of its OWN; there is no legal single
    expression for the far value. It must stay blank -- and be disclosed."""
    res = convert(OWN_KEY_XML, target_db="oracle")
    rdl = res["rdl_xml"]
    assert not _far_lookups(rdl), "a chain with no relay proof was collapsed:\n" + \
        "\n".join(_far_lookups(rdl))
    assert publish_violations(rdl) == []


def test_an_unproven_alias_declines():
    res = convert(UNPROVEN_XML, target_db="oracle")
    assert not _far_lookups(res["rdl_xml"])


def test_a_grandchild_is_never_joined_on_a_partial_key():
    """THE OWNERSHIP RULE THE FIX EXPOSED. Before it, _lookup_for_child took
    the far child (a grandchild of the bound row) as a direct child and keyed
    it on whichever of its binds named a grandparent column -- here just
    ``prog``, the same value on every row, i.e. the FIRST far row painted on
    every record. Any Lookup into the far child must carry BOTH keys."""
    for xml in (RELAY_XML, OWN_KEY_XML, UNPROVEN_XML):
        rdl = convert(xml, target_db="oracle")["rdl_xml"]
        for body in _far_lookups(rdl):
            assert '"|"' in body, "single-key lookup into a grandchild: " + body


# ---------------------------------------------------------------------------
# The proof helper itself
# ---------------------------------------------------------------------------

# carriers  = the middle query's own columns already known to carry a bound
#             key (SAME EXPRESSION namespace: middle aliases)
# binds     = the BOUND row's column names a ``:k`` may resolve to (BIND
#             EQUALITY namespace: the parent's columns, as Oracle resolves it)
_MID = {"K": "root_k"}
_BINDS = {"K": "root_k"}


def test_same_expression_aliases_relay_each_other():
    got = R._declared_alias_relays(
        "SELECT t.k AS k, t.k AS k_again, t.v AS v FROM t WHERE 1 = 1", _MID)
    assert got == {"K_AGAIN": "root_k"}


def test_a_top_level_bind_equality_proves_a_relay():
    got = R._declared_alias_relays(
        "SELECT t.c AS alias_c FROM t WHERE t.x = 1 AND t.c = :k", {}, _BINDS)
    assert got == {"ALIAS_C": "root_k"}


def test_the_generators_relaxed_equality_form_is_accepted():
    got = R._declared_alias_relays(
        "SELECT t.c AS alias_c FROM t WHERE (:k IS NULL OR t.c = :k)", {}, _BINDS)
    assert got == {"ALIAS_C": "root_k"}


@pytest.mark.parametrize("sql", [
    # a DISTINCT / UNIQUE modifier is not part of the first item
    "SELECT DISTINCT t.c AS alias_c, t.v FROM t WHERE t.c = :k",
    "SELECT UNIQUE t.c AS alias_c FROM t WHERE t.c = :k",
    # a statement terminator is not part of the predicate
    "SELECT t.c AS alias_c FROM t WHERE t.c = :k;",
    # the WHERE ends at ORDER BY
    "SELECT t.c AS alias_c FROM t WHERE t.c = :k ORDER BY t.c",
])
def test_harmless_syntax_around_a_valid_proof_is_still_a_proof(sql):
    assert R._declared_alias_relays(sql, {}, _BINDS) == {"ALIAS_C": "root_k"}


@pytest.mark.parametrize("sql", [
    # an OR branch: the equality holds on one branch only
    "SELECT t.c AS alias_c FROM t WHERE t.c = :k OR t.d = 1",
    # AND binds tighter than OR: ONE depth-0 OR anywhere voids every
    # conjunct (two judges' counterexamples, both orders)
    "SELECT t.c AS alias_c FROM t WHERE t.f = 1 OR t.g = :k AND t.c = :k",
    "SELECT t.c AS alias_c FROM t WHERE t.g = :k AND t.c = :k OR t.f = 1",
    "SELECT t.c AS alias_c FROM t WHERE t.c = :k AND t.d = 1 OR t.e = 2",
    # inside a subquery: says nothing about the outer rows
    "SELECT t.c AS alias_c FROM t WHERE EXISTS (SELECT 1 FROM u WHERE u.c = :k)",
    "SELECT u.c AS alias_c FROM u WHERE NOT EXISTS (SELECT 1 FROM v WHERE v.x = 1 AND u.c = :k)",
    # a function around the column: not the column
    "SELECT NVL(t.c, 0) AS alias_c FROM t WHERE t.c = :k",
    # a DIFFERENT column is equated
    "SELECT t.c AS alias_c FROM t WHERE t.d = :k",
    # the equality is only inside a comment
    "SELECT t.c AS alias_c FROM t WHERE 1 = 1 -- AND t.c = :k",
    # the relaxed form with mismatched binds
    "SELECT t.c AS alias_c FROM t WHERE (:j IS NULL OR t.c = :k)",
    # a qualifier mismatch: another table's same-named column
    "SELECT t.c AS alias_c FROM t, u WHERE u.c = :k",
    # UNION / UNION ALL: a second branch puts another expression under the
    # alias, or supplies the only WHERE (judges' counterexamples)
    "SELECT t.c AS alias_c FROM t WHERE t.c = :k UNION ALL SELECT x.z AS alias_c FROM x",
    "SELECT t.c AS alias_c FROM t WHERE t.c = :k UNION ALL SELECT t.c FROM u t",
    "SELECT t.c AS alias_c FROM t UNION ALL SELECT t.c AS alias_c FROM u t WHERE t.c = :k",
    "SELECT t.c AS alias_c FROM t MINUS SELECT t.c AS alias_c FROM t WHERE t.c = :k",
    "SELECT t.c AS alias_c FROM t INTERSECT SELECT t.c AS alias_c FROM t WHERE t.c = :k",
    # an Oracle q'[...]' literal the blanker cannot delimit: refuse outright
    "SELECT t.c AS alias_c FROM t WHERE t.c = :k AND (t.note = q'[a'b]' OR (t.kind = 'z' AND t.w = 1))",
    "SELECT t.c AS alias_c FROM t WHERE t.n = q'[x]' AND t.c = :k",
    # a parenthesis hidden in a quoted identifier must not break depth
    'SELECT u.c AS alias_c FROM u WHERE NOT EXISTS (SELECT 1 FROM v WHERE v.")" = 1 AND u.c = :k AND v.z = 2)',
    # a length-changing upper-case (sharp s) must not shift the WHERE window
    'SELECT t.c AS alias_c FROM t, "Straße" ut, "Größe" g WHERE ut.c = :k',
])
def test_anything_looser_than_proof_is_refused(sql):
    assert R._declared_alias_relays(sql, _MID, _BINDS) == {}


def test_union_also_voids_the_same_expression_proof():
    got = R._declared_alias_relays(
        "SELECT t.k AS k, t.k AS k_again FROM t UNION ALL SELECT u.k AS k, u.z AS k_again FROM u",
        _MID)
    assert got == {}


def test_a_bind_resolves_in_the_parents_namespace_never_the_middles():
    """A judge's counterexample: hop1 is ``<link parentColumn="site"
    childColumn="home_site">``, so the middle column HOME_SITE carries
    ``site``. The middle SQL then filters ``sa.z = :home_site`` -- but Oracle
    resolves ``:home_site`` against the PARENT (or a parameter), never the
    middle's own alias. Nothing proves sa.z carries ``site``."""
    sql = "SELECT sa.home_site home_site, sa.z mid_site, sa.org mid_org FROM sa WHERE sa.z = :home_site"
    # middle namespace knows HOME_SITE carries site; the parent namespace
    # knows only SITE (the bound column's own name)
    assert R._declared_alias_relays(sql, {"HOME_SITE": "site"}, {"SITE": "site"}) == {}


def test_a_proven_alias_is_never_reused_as_a_bind_name():
    """Second judge variant: OWNER is proven by SAME EXPRESSION, then
    ``t.region = :owner`` must NOT ride on that proof -- ``:owner`` is the
    parent's OWNER column, not the middle's alias."""
    sql = "SELECT t.site AS site, t.site AS owner, t.region AS alias_r FROM t WHERE t.region = :owner"
    got = R._declared_alias_relays(sql, {"SITE": "site"}, {"SITE": "site"})
    assert got == {"OWNER": "site"}


def test_a_carrier_that_is_not_a_bound_key_proves_nothing():
    """Only aliases equal to a KNOWN carrier relay; an equality to a bind
    nobody supplies is not a chain key."""
    got = R._declared_alias_relays(
        "SELECT t.c AS alias_c FROM t WHERE t.c = :unknown", _MID, _BINDS)
    assert got == {}


# ---------------------------------------------------------------------------
# End-to-end refusals the judges reproduced through convert()
# ---------------------------------------------------------------------------

# The middle query is a UNION: the second branch aliases a different column.
UNION_XML = _chain_xml(
    "SELECT sa.site mid_site, sa.org mid_org FROM sa",
    "WHERE sa.prog = :prog AND sa.site = :site "
    "UNION ALL SELECT sb.other_site mid_site, sb.org mid_org FROM sb "
    "WHERE sb.prog = :prog AND sb.owner = :site",
    "WHERE c.prog = :prog AND c.site = :mid_site")

# A top-level OR in the middle WHERE: the equalities hold on one branch only.
OR_XML = _chain_xml(
    "SELECT sa.site mid_site, sa.org mid_org FROM sa",
    "WHERE sa.flag = 'Y' OR sa.prog = :prog AND sa.site = :site",
    "WHERE c.prog = :prog AND c.site = :mid_site")

# The bound key never enters the middle query as a plain column (wrapped in
# NVL), so the join-key injector cannot add one and _link_key_pairs' last
# resort would match ``mid_site`` (= sa.website) to ``:site`` by SUFFIX.
SUFFIX_XML = _chain_xml(
    "SELECT sa.website mid_site, sa.org mid_org FROM sa",
    "WHERE sa.prog = :prog AND NVL(sa.site, 0) = :site",
    "WHERE c.prog = :prog AND c.site = :mid_site")


@pytest.mark.parametrize("xml", [UNION_XML, OR_XML, SUFFIX_XML],
                         ids=["union", "or-precedence", "suffix-key"])
def test_judge_counterexamples_decline_end_to_end(xml):
    rdl = convert(xml, target_db="oracle")["rdl_xml"]
    assert not _far_lookups(rdl), "collapsed on an unproven chain:\n" + "\n".join(_far_lookups(rdl))
    assert publish_violations(rdl) == []


# ---------------------------------------------------------------------------
# Mutation proofs -- the relay is load-bearing in BOTH directions
# ---------------------------------------------------------------------------

def test_without_the_proof_helper_the_customer_shape_goes_blank(monkeypatch):
    """Turn the proof off: the collapse must vanish (so the collapse test
    above is testing the relay, not something else)."""
    monkeypatch.setattr(R, "_declared_alias_relays",
                        lambda sql, carriers, bind_carriers=None: {})
    rdl = convert(RELAY_XML, target_db="oracle")["rdl_xml"]
    assert not _far_lookups(rdl), "the lookup survived without any relay proof"


def test_a_lying_proof_helper_would_be_caught_by_the_decline_test(monkeypatch):
    """Make the helper claim the middle's OWN id relays the root site: the
    real-two-hop fixture would then collapse -- and that is exactly what
    test_a_real_two_hop_chain_still_declines_instead_of_guessing forbids.
    Proves that test can fail, so it is guarding something."""
    monkeypatch.setattr(R, "_declared_alias_relays",
                        lambda sql, carriers, bind_carriers=None:
                        {"MID_SITE": carriers.get("SITE", "site")})
    rdl = convert(OWN_KEY_XML, target_db="oracle")["rdl_xml"]
    assert _far_lookups(rdl), "the decline test would not have fired on a lying proof"

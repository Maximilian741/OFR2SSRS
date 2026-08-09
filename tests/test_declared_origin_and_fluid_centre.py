"""A DECLARED x IS THE x -- for a stacked list's columns and for a
fluid-width centred boilerplate.

Two residual-geometry rules, both instances of "use the declared number",
both truth-PDF measured against a landscape inspection list whose section
declares ``<body><location x="0.30212">`` (= its sheet margin, which the RDL
page margin carries) and whose caption/value columns declare
x = 0.00000 / 4.29395 / 6.30127 / 8.39722:

1. THE LIST STARTS AT THE DECLARED ORIGIN.  The truth prints the x=0.00000
   caption at 21.76pt -- exactly the page margin.  The converter used to add
   a cosmetic 0.06in tablix indent AND floor every box's Left at 0.02in, so
   that caption printed at 27.50pt (+0.08in) and every other column at
   +0.06in.  Engine-render measured, before -> after on that report:

       column x     truth x     ours before   ours after
       0.00000       22.52pt      27.50pt       22.46pt
       4.29395      330.92pt     334.94pt      330.91pt
       6.30127      475.44pt     479.66pt      475.42pt
       8.39722      626.36pt     630.86pt      626.33pt

   (The 2-decimal Left quantisation the floor rode on was worth a further
   +/-0.36pt per column -- 4.29395in printed as 4.29in, 8.39722in as 8.40in.)

2. A FLUID CENTRED BOILERPLATE ANCHORS LEFT.  A box declared
   ``justify="center" horizontalElasticity="variable"`` contracts onto the
   text it holds, so the centre line it would justify against collapses onto
   the declared left edge.  Truth: that report's title, declared at
   x=3.31970in w=4.37061in (box 239.02..553.70pt), prints its glyphs at
   239.00..511.03pt -- flush left, 21.35pt left of the box centre.  A page
   counter in the same margin band keeps centring, because Oracle
   substitutes &PageNumber after the page is composed and the box therefore
   never contracts (see test_declared_alignment_elasticity.py).

Both directions are asserted so neither rule can be "solved" by moving
everything left.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from converter import convert

NS = ("{http://schemas.microsoft.com/sqlserver/reporting/"
      "2008/01/reportdefinition}")

# The declared column origins of the fixture list, in inches.
COL_X = (0.00000, 4.29395, 6.30127, 8.39722)
TITLE_X, TITLE_W = 3.31970, 4.37061
PAGE_X, PAGE_W = 4.26111, 2.47913


def _source_xml() -> str:
    """A landscape two-line stacked record list with a fluid centred title
    and a fluid centred page counter in the same margin band."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<report name="ORIGINCASE" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_MAIN">
      <select><![CDATA[SELECT SITE_NM, PERMIT_NO, VISIT_DT, RESULT_CD,
       PARTY_NM, PARTY_TP, CHECK_DT, OK_FLAG FROM T_VISIT]]></select>
      <group name="G_SITE">
        <dataItem name="SITE_NM" datatype="vchar2" width="60"
         defaultLabel="Site Nm">
          <dataDescriptor expression="SITE_NM" order="1" width="60"/>
        </dataItem>
        <dataItem name="PERMIT_NO" datatype="vchar2" width="30"
         defaultLabel="Permit No">
          <dataDescriptor expression="PERMIT_NO" order="2" width="30"/>
        </dataItem>
        <dataItem name="VISIT_DT" datatype="vchar2" width="30"
         defaultLabel="Visit Dt">
          <dataDescriptor expression="VISIT_DT" order="3" width="30"/>
        </dataItem>
        <dataItem name="RESULT_CD" datatype="vchar2" width="30"
         defaultLabel="Result Cd">
          <dataDescriptor expression="RESULT_CD" order="4" width="30"/>
        </dataItem>
        <dataItem name="PARTY_NM" datatype="vchar2" width="60"
         defaultLabel="Party Nm">
          <dataDescriptor expression="PARTY_NM" order="5" width="60"/>
        </dataItem>
        <dataItem name="PARTY_TP" datatype="vchar2" width="30"
         defaultLabel="Party Tp">
          <dataDescriptor expression="PARTY_TP" order="6" width="30"/>
        </dataItem>
        <dataItem name="CHECK_DT" datatype="vchar2" width="30"
         defaultLabel="Check Dt">
          <dataDescriptor expression="CHECK_DT" order="7" width="30"/>
        </dataItem>
        <dataItem name="OK_FLAG" datatype="vchar2" width="30"
         defaultLabel="Ok Flag">
          <dataDescriptor expression="OK_FLAG" order="8" width="30"/>
        </dataItem>
      </group>
    </dataSource>
  </data>
  <layout>
    <section name="main" width="11.00000" height="8.50000"
     orientation="landscape">
      <body width="10.40625" height="7.14587">
        <location x="0.30212" y="0.85413"/>
        <frame name="M_BODY">
          <geometryInfo x="0.00000" y="0.00000" width="10.39587"
           height="1.60413"/>
          <generalLayout verticalElasticity="variable"/>
          <repeatingFrame name="R_SITE" source="G_SITE" printDirection="down"
           minWidowRecords="1" columnMode="no" vertSpaceBetweenFrames="0.0500">
            <geometryInfo x="0.00000" y="0.49939" width="10.39587"
             height="0.41199"/>
            <generalLayout verticalElasticity="expand"/>
            <field name="F_SITE_NM" source="SITE_NM" minWidowLines="1"
             spacing="0" alignment="start">
              <font face="Arial" size="10"/>
              <geometryInfo x="{COL_X[0]:.5f}" y="0.49939" width="4.29993"
               height="0.18848"/>
            </field>
            <field name="F_PERMIT_NO" source="PERMIT_NO" minWidowLines="1"
             spacing="0" alignment="start">
              <font face="Arial" size="10"/>
              <geometryInfo x="{COL_X[1]:.5f}" y="0.49939" width="1.90173"
               height="0.18848"/>
            </field>
            <field name="F_VISIT_DT" source="VISIT_DT" minWidowLines="1"
             spacing="0" alignment="start">
              <font face="Arial" size="10"/>
              <geometryInfo x="{COL_X[2]:.5f}" y="0.49939" width="2.00000"
               height="0.18750"/>
            </field>
            <field name="F_RESULT_CD" source="RESULT_CD" minWidowLines="1"
             spacing="0" alignment="start">
              <font face="Arial" size="10"/>
              <geometryInfo x="{COL_X[3]:.5f}" y="0.49939" width="1.99866"
               height="0.20740"/>
            </field>
            <frame name="M_ROW2">
              <geometryInfo x="0.00000" y="0.72131" width="10.39587"
               height="0.19006"/>
              <generalLayout verticalElasticity="variable"/>
              <field name="F_PARTY_NM" source="PARTY_NM" minWidowLines="1"
               spacing="0" alignment="start">
                <font face="Arial" size="10"/>
                <geometryInfo x="{COL_X[0]:.5f}" y="0.72375" width="4.29993"
                 height="0.18762"/>
              </field>
              <field name="F_PARTY_TP" source="PARTY_TP" minWidowLines="1"
               spacing="0" alignment="start">
                <font face="Arial" size="10"/>
                <geometryInfo x="{COL_X[1]:.5f}" y="0.72327" width="1.89990"
                 height="0.18750"/>
              </field>
              <field name="F_CHECK_DT" source="CHECK_DT" minWidowLines="1"
               spacing="0" alignment="start">
                <font face="Arial" size="10"/>
                <geometryInfo x="{COL_X[2]:.5f}" y="0.72144" width="2.00000"
                 height="0.18750"/>
              </field>
              <field name="F_OK_FLAG" source="OK_FLAG" minWidowLines="1"
               spacing="0" alignment="start">
                <font face="Arial" size="10"/>
                <geometryInfo x="{COL_X[3]:.5f}" y="0.72131" width="1.99866"
                 height="0.19006"/>
              </field>
            </frame>
          </repeatingFrame>
        </frame>
      </body>
      <margin>
        <text name="B_HDR_A0" minWidowLines="1">
          <textSettings spacing="0"/>
          <geometryInfo x="{COL_X[0]:.5f}" y="0.00000" width="4.29993"
           height="0.19812"/>
          <textSegment>
            <font face="Arial" size="10"/>
            <string><![CDATA[Site Nm]]></string>
          </textSegment>
        </text>
        <text name="B_HDR_A1" minWidowLines="1">
          <textSettings spacing="0"/>
          <geometryInfo x="{COL_X[1]:.5f}" y="0.00000" width="1.89990"
           height="0.18750"/>
          <textSegment>
            <font face="Arial" size="10"/>
            <string><![CDATA[Permit No]]></string>
          </textSegment>
        </text>
        <text name="B_HDR_A2" minWidowLines="1">
          <textSettings spacing="0"/>
          <geometryInfo x="{COL_X[2]:.5f}" y="0.00000" width="2.00000"
           height="0.18750"/>
          <textSegment>
            <font face="Arial" size="10"/>
            <string><![CDATA[Visit Dt]]></string>
          </textSegment>
        </text>
        <text name="B_HDR_A3" minWidowLines="1">
          <textSettings spacing="0"/>
          <geometryInfo x="{COL_X[3]:.5f}" y="0.00000" width="1.99866"
           height="0.18750"/>
          <textSegment>
            <font face="Arial" size="10"/>
            <string><![CDATA[Result Cd]]></string>
          </textSegment>
        </text>
        <text name="B_HDR_B0" minWidowLines="1">
          <textSettings spacing="0"/>
          <geometryInfo x="{COL_X[0]:.5f}" y="0.20850" width="4.29993"
           height="0.18994"/>
          <textSegment>
            <font face="Arial" size="10"/>
            <string><![CDATA[Party Nm]]></string>
          </textSegment>
        </text>
        <text name="B_HDR_B1" minWidowLines="1">
          <textSettings spacing="0"/>
          <geometryInfo x="{COL_X[1]:.5f}" y="0.20850" width="1.89990"
           height="0.18994"/>
          <textSegment>
            <font face="Arial" size="10"/>
            <string><![CDATA[Party Tp]]></string>
          </textSegment>
        </text>
        <text name="B_HDR_B2" minWidowLines="1">
          <textSettings spacing="0"/>
          <geometryInfo x="{COL_X[2]:.5f}" y="0.20850" width="2.00000"
           height="0.18994"/>
          <textSegment>
            <font face="Arial" size="10"/>
            <string><![CDATA[Check Dt]]></string>
          </textSegment>
        </text>
        <text name="B_HDR_B3" minWidowLines="1">
          <textSettings spacing="0"/>
          <geometryInfo x="{COL_X[3]:.5f}" y="0.20850" width="1.99866"
           height="0.18994"/>
          <textSegment>
            <font face="Arial" size="10"/>
            <string><![CDATA[Ok Flag]]></string>
          </textSegment>
        </text>
        <text name="B_TITLE" minWidowLines="1">
          <textSettings justify="center" spacing="0"/>
          <geometryInfo x="{TITLE_X:.5f}" y="0.24902" width="{TITLE_W:.5f}"
           height="0.25098"/>
          <generalLayout horizontalElasticity="variable"/>
          <textSegment>
            <font face="Arial" size="12" bold="yes"/>
            <string><![CDATA[Regional Site Visit Register]]></string>
          </textSegment>
        </text>
        <text name="B_PAGE" minWidowLines="1" templateSection="main">
          <textSettings justify="center" spacing="0"/>
          <geometryInfo x="{PAGE_X:.5f}" y="8.06177" width="{PAGE_W:.5f}"
           height="0.17859"/>
          <generalLayout horizontalElasticity="variable"/>
          <textSegment>
            <font face="Arial" size="8"/>
            <string><![CDATA[Page &PageNumber of &TotalPages]]></string>
          </textSegment>
        </text>
      </margin>
    </section>
  </layout>
</report>
"""


def _tree():
    return ET.fromstring(convert(_source_xml().encode("utf-8"))["rdl_xml"]
                         .encode("utf-8"))


def _inches(el, tag, default=None):
    node = el.find(NS + tag)
    if node is None or not (node.text or "").endswith("in"):
        return default
    return float(node.text[:-2])


def _stacked_list(root):
    for tab in root.iter(NS + "Tablix"):
        if tab.get("Name") == "Tablix_StackedList":
            return tab
    return None


def test_stacked_list_starts_at_the_declared_origin():
    """No cosmetic indent: the tablix's own Left is the declared 0."""
    tab = _stacked_list(_tree())
    assert tab is not None, "fixture must take the stacked-list path"
    left = _inches(tab, "Left")
    assert left is not None and abs(left) < 1e-6, (
        f"stacked list indented by {left}in; the declared body origin is "
        f"already the page margin")


def test_stacked_list_columns_keep_their_declared_x():
    """Every caption/value box sits at its DECLARED x -- including the one
    declared at x=0.00000, which the 0.02in floor used to move."""
    tab = _stacked_list(_tree())
    assert tab is not None
    seen = {}
    caps = {}
    for tb in tab.iter(NS + "Textbox"):
        nm = tb.get("Name") or ""
        left = _inches(tb, "Left")
        if left is None:
            continue
        if nm.startswith("Tb_SLDet_"):
            seen.setdefault(round(left, 4), []).append(nm)
        elif nm.startswith("Tb_SLHdr_"):
            caps.setdefault(round(left, 4), []).append(nm)
    assert seen, "no stacked-list record boxes emitted"
    for left in seen:
        near = min(abs(left - x) for x in COL_X)
        assert near < 0.0005, (
            f"box(es) {seen[left]} at Left={left}in match no declared "
            f"column x {COL_X} (nearest is {near}in away) -- the 0.1in "
            f"column BUCKET KEY is not the declared coordinate")
    # the x=0.00000 column must actually be present at 0 -- not floored
    assert 0.0 in seen, (
        f"the record column declared at x=0.00000 was moved; emitted "
        f"lefts {sorted(seen)}")
    assert 0.0 in caps, (
        f"the caption declared at x=0.00000 was moved; emitted "
        f"lefts {sorted(caps)}")


def test_fluid_centred_title_anchors_left_but_page_counter_still_centres():
    root = _tree()
    aligns = {}
    for tb in root.iter(NS + "Textbox"):
        nm = tb.get("Name") or ""
        if "B_TITLE" in nm or "B_PAGE" in nm:
            ta = tb.find(".//" + NS + "TextAlign")
            aligns[nm] = ta.text if ta is not None else None
    title = [v for k, v in aligns.items() if "B_TITLE" in k]
    counter = [v for k, v in aligns.items() if "B_PAGE" in k]
    assert title, f"declared title not emitted: {sorted(aligns)}"
    assert counter, f"declared page counter not emitted: {sorted(aligns)}"
    assert all(v == "Left" for v in title), (
        f"a fluid-width centred STATIC boilerplate must anchor at its "
        f"declared x; got {title}")
    assert all(v == "Center" for v in counter), (
        f"a fluid-width centred PAGE COUNTER keeps its declared box "
        f"centre; got {counter}")

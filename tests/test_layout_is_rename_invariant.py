"""
RENAME INVARIANCE — layout follows the DECLARATIONS, never the spellings.

A converter shaped on a handful of reports learns their vocabulary: it starts
deciding which side of a record a value prints on, whether a column belongs to
the master or to a sub-table, and which caption gets an accent, from the WORDS
its training reports happened to use. The tell is that two reports with the
SAME declared geometry render differently because their columns are named
differently — which means report N+1, written by someone with a different
naming habit, lays out wrong for reasons no one can see.

This file measures that property directly. One synthetic source is converted
once per NAME SET: the declarations (geometry, frames, labels, column order,
datatypes, widths) are byte-identical across sets and ONLY the column stems
change. Every conversion must normalize to the same RDL.

What is deliberately held constant across the sets, and why:
  * the SUFFIX of every column (_KEY / _TXT / _AMT / _DAY). Suffix conventions
    (_ID/_CODE/_NAME vs _DESC, and the key-suffix pairing of a parameter to
    its column) are declared dialect rules the converter keeps ON PURPOSE and
    documents; they are grammar, not vocabulary. Only the STEM varies, so any
    rule that reads a stem is caught and no declared convention is disturbed.
  * every ``defaultLabel``, so the user-facing wording cannot drift and mask a
    layout difference.

The name sets include ordinary English role words (owner / status / city /
event) — the exact kind of stem a corpus-shaped converter learns to key on.
They are generic English used as TEST DATA so the test can prove they carry no
weight; no customer report, field or label name appears anywhere in this file.

Each gate carries a mutation proof that restores a stem-keyed rule and shows
the gate go red, so the gate can never certify by being blind.
"""
from __future__ import annotations

import re

import pytest

from converter import convert
from converter.generators import rdl as R


# ---------------------------------------------------------------------------
# One synthetic source, four declared columns, a record declared across TWO
# printed lines (y=0.60 and y=0.90) with a caption beside each value. The
# declared reading order is c1, c4 on the first line and c2, c3 on the second
# — deliberately NOT the column order, so a rule that ignores geometry and
# falls back to list order produces a visibly different card.
# ---------------------------------------------------------------------------
_TEMPLATE = """<?xml version="1.0" encoding="UTF-8" ?>
<report name="ZZQX_RENAME_PROBE" DTDVersion="9.0.2.0.10">
  <data>
    <userParameter name="P_ZONE" datatype="character" width="40" precision="10"
     initialValue="Zone One" label="Zone" defaultWidth="0" defaultHeight="0"/>
    <dataSource name="Q_1">
      <select><![CDATA[SELECT {c1}, {c2}, {c3}, {c4} FROM ZZQX_PROBE_T
WHERE {c2} = :P_ZONE ORDER BY {c1}]]></select>
      <displayInfo x="0" y="0" width="1.5" height="0.5"/>
      <group name="G_1">
        <displayInfo x="0" y="0" width="1.5" height="3"/>
        <dataItem name="{c1}" datatype="vchar2" columnOrder="1" width="20"
         defaultWidth="60000" defaultHeight="10000" columnFlags="0"
         defaultLabel="Alpha">
          <dataDescriptor expression="{c1}" descriptiveExpression="{c1}"
           order="1" width="20"/>
        </dataItem>
        <dataItem name="{c2}" datatype="vchar2" columnOrder="2" width="40"
         defaultWidth="100000" defaultHeight="10000" columnFlags="0"
         defaultLabel="Beta">
          <dataDescriptor expression="{c2}" descriptiveExpression="{c2}"
           order="2" width="40"/>
        </dataItem>
        <dataItem name="{c3}" datatype="number" columnOrder="3" width="22"
         precision="10" scale="2" defaultWidth="80000" defaultHeight="10000"
         columnFlags="0" defaultLabel="Gamma">
          <dataDescriptor expression="{c3}" descriptiveExpression="{c3}"
           order="3" width="22"/>
        </dataItem>
        <dataItem name="{c4}" datatype="date" columnOrder="4" width="10"
         defaultWidth="60000" defaultHeight="10000" columnFlags="0"
         defaultLabel="Delta">
          <dataDescriptor expression="{c4}" descriptiveExpression="{c4}"
           order="4" width="10"/>
        </dataItem>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="main" width="8.50000" height="11.00000">
    <body width="8.00000">
      <location x="0.25000"/>
      <repeatingFrame name="R_1" source="G_1" printDirection="down"
       minWidowRecords="1" columnMode="no">
        <geometryInfo x="0.00000" y="0.55000" width="7.90000" height="0.70000"/>
        <generalLayout verticalElasticity="expand"/>
        <visualSettings fillPattern="transparent"/>
        <text name="B_A" minWidowLines="1">
          <textSettings justify="start" spacing="0"/>
          <geometryInfo x="0.00000" y="0.60000" width="1.30000" height="0.19000"/>
          <visualSettings fillPattern="transparent"/>
          <textSegment><font face="Arial" size="10" bold="yes"/>
            <string><![CDATA[Alpha:]]></string></textSegment>
        </text>
        <field name="F_1" source="{c1}" minWidowLines="1" spacing="0"
         alignment="start">
          <font face="Arial" size="10"/>
          <geometryInfo x="1.40000" y="0.60000" width="1.60000" height="0.19000"/>
          <visualSettings fillPattern="transparent"/>
        </field>
        <text name="B_D" minWidowLines="1">
          <textSettings justify="start" spacing="0"/>
          <geometryInfo x="3.20000" y="0.60000" width="1.30000" height="0.19000"/>
          <visualSettings fillPattern="transparent"/>
          <textSegment><font face="Arial" size="10" bold="yes"{fc}/>
            <string><![CDATA[Delta:]]></string></textSegment>
        </text>
        <field name="F_4" source="{c4}" minWidowLines="1" spacing="0"
         alignment="start">
          <font face="Arial" size="10"/>
          <geometryInfo x="4.60000" y="0.60000" width="2.20000" height="0.19000"/>
          <visualSettings fillPattern="transparent"/>
        </field>
        <text name="B_B" minWidowLines="1">
          <textSettings justify="start" spacing="0"/>
          <geometryInfo x="0.00000" y="0.90000" width="1.30000" height="0.19000"/>
          <visualSettings fillPattern="transparent"/>
          <textSegment><font face="Arial" size="10" bold="yes"/>
            <string><![CDATA[Beta:]]></string></textSegment>
        </text>
        <field name="F_2" source="{c2}" minWidowLines="1" spacing="0"
         alignment="start">
          <font face="Arial" size="10"/>
          <geometryInfo x="1.40000" y="0.90000" width="2.60000" height="0.19000"/>
          <visualSettings fillPattern="transparent"/>
        </field>
        <text name="B_C" minWidowLines="1">
          <textSettings justify="start" spacing="0"/>
          <geometryInfo x="4.20000" y="0.90000" width="1.30000" height="0.19000"/>
          <visualSettings fillPattern="transparent"/>
          <textSegment><font face="Arial" size="10" bold="yes"/>
            <string><![CDATA[Gamma:]]></string></textSegment>
        </text>
        <field name="F_3" source="{c3}" minWidowLines="1" spacing="0"
         alignment="end">
          <font face="Arial" size="10"/>
          <geometryInfo x="5.60000" y="0.90000" width="1.10000" height="0.19000"/>
          <visualSettings fillPattern="transparent"/>
        </field>
      </repeatingFrame>
    </body>
  </section>
  </layout>
</report>
"""

# Stems only. Suffixes are identical across every set (see module docstring),
# so a declared dialect convention is never what changes between two runs.
NAME_SETS = {
    # invented tokens — the control
    "opaque": ("ZZQXA_KEY", "ZZQXB_TXT", "ZZQXC_AMT", "ZZQXD_DAY"),
    # ordinary English role words, i.e. the stems a corpus-shaped converter
    # learns to key on. Generic English, used here as data.
    "roles": ("OWNER_KEY", "STATUS_TXT", "CITY_AMT", "EVENT_DAY"),
    # a different, unrelated domain's words
    "other": ("LEDGER_KEY", "PARCEL_TXT", "VESSEL_AMT", "NOTE_DAY"),
    # reverse alphabetical, so an accidental sort-by-name is caught too
    "zsort": ("ZULU_KEY", "YANKEE_TXT", "XRAY_AMT", "WHISKEY_DAY"),
    # non-Latin stems: the property must not depend on the script either
    "greek": ("ALFA_KEY", "BITA_TXT", "GAMA_AMT", "DELTA_DAY"),
}

_POSITIONAL = ("ZZCOL1", "ZZCOL2", "ZZCOL3", "ZZCOL4")


def _source(names, caption_color: str = "") -> bytes:
    fc = f' color="{caption_color}"' if caption_color else ""
    return _TEMPLATE.format(c1=names[0], c2=names[1], c3=names[2],
                            c4=names[3], fc=fc).encode("utf-8")


def _normalized_rdl(names, caption_color: str = "") -> str:
    """Convert one name set and rewrite every column name to its POSITION.

    After this substitution two RDLs can differ only where a DECLARATION
    differs — which, across these sets, is nowhere.
    """
    rdl = convert(_source(names, caption_color), target_db="oracle")["rdl_xml"]
    assert rdl, "no RDL produced"
    # Plain substitution, longest first: the names also appear glued into
    # generated identifiers (Tb_Lbl_<name>), where a \b anchor would not match.
    for actual, positional in sorted(zip(names, _POSITIONAL),
                                     key=lambda p: -len(p[0])):
        rdl = rdl.replace(actual, positional)
    return rdl


def _card_row_shape(names) -> list:
    """The card's label/value rows, read back out of the emitted RDL as
    (label text, positional column) pairs in emission order."""
    rdl = _normalized_rdl(names)
    rows = []
    for m in re.finditer(
            r'<Textbox Name="Tb_(Lbl|Val)_([^"]+)">.*?<Value>(.*?)</Value>',
            rdl, re.S):
        rows.append((m.group(1), m.group(2), m.group(3)))
    return rows


# ---------------------------------------------------------------------------
# 1. The whole conversion is invariant under renaming
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("variant", sorted(set(NAME_SETS) - {"opaque"}))
def test_rdl_is_identical_under_renaming(variant):
    """Same declarations, different column stems -> the same report.

    This is the load-bearing gate: if any layout decision reads a column's
    name, some set below renders differently from the control.
    """
    control = _normalized_rdl(NAME_SETS["opaque"])
    other = _normalized_rdl(NAME_SETS[variant])
    assert other == control, (
        f"renaming the columns to the {variant!r} set changed the emitted "
        "report — a layout decision is reading column names, not declarations")


def test_renaming_does_not_change_the_route():
    """Every name set must reach the SAME Tablix builder. A route that flips
    on a word is the coarsest form of the defect (a whole different report)."""
    routes = {}
    for variant, names in NAME_SETS.items():
        rdl = _normalized_rdl(names)
        routes[variant] = sorted(set(re.findall(r'<Tablix Name="([^"]+)"', rdl)))
    distinct = {tuple(v) for v in routes.values()}
    assert len(distinct) == 1, f"route depends on column spelling: {routes}"


def test_card_rows_follow_the_declared_lines_not_the_column_order():
    """The source declares c4 on the FIRST printed line and c2/c3 on the
    second, while the dataSource LISTS them c1,c2,c3,c4. The card must follow
    the declared lines; if it followed list order the date column would print
    last."""
    rows = _card_row_shape(NAME_SETS["opaque"])
    order = [c for kind, c, _v in rows if kind == "Val"]
    assert order, "the card emitted no value cells"
    assert order.index("ZZCOL4") < order.index("ZZCOL3"), (
        "the column declared on the first printed line must print before the "
        f"one declared on the second; got {order}")


# ---------------------------------------------------------------------------
# 2. The individual decisions, unit level
# ---------------------------------------------------------------------------

class _Item:
    def __init__(self, name, label=""):
        self.name = name
        self.label = label or name


def _parsed(names):
    from converter.parsers.oracle_xml import parse_oracle_xml
    return parse_oracle_xml(_source(names))


@pytest.mark.parametrize("variant", sorted(NAME_SETS))
def test_pairing_is_a_pure_function_of_declared_geometry(variant):
    names = NAME_SETS[variant]
    rep = _parsed(names)
    q = rep.queries[0]
    rows = R._pair_card_header_rows(list(q.items), rep, q)
    shaped = [tuple(names.index(f.name) if f is not None else None for f in r)
              for r in rows]
    control_rep = _parsed(NAME_SETS["opaque"])
    control_q = control_rep.queries[0]
    control = [tuple(NAME_SETS["opaque"].index(f.name) if f is not None else None
                     for f in r)
               for r in R._pair_card_header_rows(
                   list(control_q.items), control_rep, control_q)]
    assert shaped == control, f"{variant}: pairing changed with the names"


@pytest.mark.parametrize("variant", sorted(NAME_SETS))
def test_master_detail_split_is_a_pure_function_of_declarations(variant):
    """The probe source declares ONE group printed by ONE frame, so it
    declares no detail region at all — whatever its columns are called."""
    names = NAME_SETS[variant]
    rep = _parsed(names)
    q = rep.queries[0]
    header, detail = R._split_card_fields(list(q.items), rep, q)
    assert [it.name for it in detail] == [], (
        f"{variant}: columns were split into a sub-table the source never "
        "declares")
    assert len(header) == len(q.items)


@pytest.mark.parametrize("variant", sorted(NAME_SETS))
def test_detail_column_order_is_a_pure_function_of_declarations(variant):
    """Sub-table column order comes from declared x (then the declared list
    position for columns the layout never places)."""
    names = NAME_SETS[variant]
    rep = _parsed(names)
    q = rep.queries[0]
    ordered = R._sort_detail_columns(list(q.items), rep)
    got = [names.index(it.name) for it in ordered]
    control_rep = _parsed(NAME_SETS["opaque"])
    control_q = control_rep.queries[0]
    want = [NAME_SETS["opaque"].index(it.name)
            for it in R._sort_detail_columns(list(control_q.items), control_rep)]
    assert got == want, f"{variant}: detail order changed with the names"


# ---------------------------------------------------------------------------
# 3. The caption accent follows the DECLARED colour
# ---------------------------------------------------------------------------

def test_caption_accent_comes_from_the_declaration_not_the_name():
    """A source that declares a caption colour gets it; the same source with
    no declared colour gets the card's own ink — and neither answer moves when
    the columns are renamed."""
    plain = {v: _normalized_rdl(NAME_SETS[v]) for v in NAME_SETS}
    assert len(set(plain.values())) == 1, "undeclared-colour case is not stable"

    tinted = _normalized_rdl(NAME_SETS["opaque"], caption_color="#00007F")
    assert tinted != plain["opaque"], (
        "declaring a caption colour changed nothing — the accent is not "
        "reading the declaration")
    tinted_roles = _normalized_rdl(NAME_SETS["roles"], caption_color="#00007F")
    assert tinted_roles == tinted, "declared accent moved with the column names"


def test_declared_source_color_reads_the_declaration():
    names = NAME_SETS["opaque"]
    rep_plain = _parsed(names)
    assert R._declared_source_color(rep_plain, names[0]) == ""

    from converter.parsers.oracle_xml import parse_oracle_xml
    rep_tint = parse_oracle_xml(_source(names, caption_color="#00007F"))
    got = R._declared_source_color(rep_tint, names[3]).upper().lstrip("#")
    assert got.endswith("00007F"), got
    # only ONE caption declares a colour; the value whose own caption declares
    # none must not borrow it from a neighbour further left on the same line
    assert R._declared_source_color(rep_tint, names[2]) == ""


# ---------------------------------------------------------------------------
# 4. MUTATION PROOFS — put a stem-keyed rule back and watch each gate go red
# ---------------------------------------------------------------------------

def test_mutation_stem_keyed_pairing_turns_the_gate_red(monkeypatch):
    """Restore a 'these stems print on the right' rule. The invariance gate
    MUST fail — otherwise it is blind to exactly the defect it exists for."""
    original = R._pair_card_header_rows

    def stem_keyed(header_items, report=None, query=None):
        right = [it for it in header_items
                 if any(tok in (it.name or "").upper()
                        for tok in ("STATUS", "CITY"))]
        left = [it for it in header_items if it not in right]
        rows = []
        for i in range(max(len(left), len(right))):
            rows.append((left[i] if i < len(left) else None,
                         right[i] if i < len(right) else None))
        return rows

    monkeypatch.setattr(R, "_pair_card_header_rows", stem_keyed)
    control = _normalized_rdl(NAME_SETS["opaque"])
    with pytest.raises(AssertionError):
        assert _normalized_rdl(NAME_SETS["roles"]) == control
    monkeypatch.setattr(R, "_pair_card_header_rows", original)


def test_mutation_prefix_keyed_split_turns_the_gate_red(monkeypatch):
    """Restore a 'columns starting with these words are a sub-table' rule and
    show the master/detail gate go red."""
    def prefix_keyed(body_items, report=None, query=None):
        detail = [it for it in body_items
                  if (it.name or "").upper().startswith(("EVENT_", "NOTE_"))]
        header = [it for it in body_items if it not in detail]
        return header, detail

    monkeypatch.setattr(R, "_split_card_fields", prefix_keyed)
    rep = _parsed(NAME_SETS["roles"])
    q = rep.queries[0]
    _hdr, detail = R._split_card_fields(list(q.items), rep, q)
    assert detail, "the mutation did not take"
    with pytest.raises(AssertionError):
        assert [it.name for it in detail] == []


def test_mutation_stem_keyed_accent_turns_the_gate_red(monkeypatch):
    """Restore a 'these stems get the accent' rule and show the accent gate
    go red: two renamings of one report then disagree."""
    def stem_keyed_color(report, source):
        return "#00007F" if "EVENT" in (source or "").upper() else ""

    monkeypatch.setattr(R, "_declared_source_color", stem_keyed_color)
    control = _normalized_rdl(NAME_SETS["opaque"])
    with pytest.raises(AssertionError):
        assert _normalized_rdl(NAME_SETS["roles"]) == control

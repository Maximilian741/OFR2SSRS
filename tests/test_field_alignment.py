"""Regression test for the "Tablix cell references a field that doesn't
exist in the dataset" class of bug.

Triggered in Report Builder when:
  1. The user uploads the RDL (parses, uploads fine).
  2. The user repoints the data source and clicks "Refresh Fields".
  3. Report Builder rebuilds the Fields collection from Oracle's actual
     column-name response.
  4. Any unaliased SELECT expression (UPPER(...), CONCAT(...), etc.)
     comes back with Oracle's verbose default column name that DOES
     NOT match the <Field Name="..."> we declared.
  5. Every Tablix cell that references the now-missing field becomes
     orphan. Save fails with:
       "The Value expression for the text box 'Cell_X' refers to the
        field 'X'. ... Letters in the names of fields must use the
        correct case."

The generator must add an explicit ``AS <name>`` to every unaliased
SELECT expression so Oracle returns the column name we already
declared. This test asserts that contract for every dataset in every
discovered case fixture.

Name-agnostic: walks tests/fixtures/source_of_truth/ and runs against
whatever case_NNN subdirectories exist. NO report names anywhere.
"""
from __future__ import annotations
import html
import re
from pathlib import Path

import pytest


FIXTURES = Path(__file__).parent / "fixtures" / "source_of_truth"


def _cases():
    if not FIXTURES.exists():
        return []
    out = []
    for d in sorted(FIXTURES.iterdir()):
        src = d / "source.xml"
        if src.exists():
            out.append(pytest.param(d.name, src, id=d.name))
    return out


def _command_texts(rdl: str):
    return [html.unescape(c)
            for c in re.findall(r"<CommandText>(.*?)</CommandText>", rdl, re.DOTALL)]


def _field_decls(rdl: str):
    return set(re.findall(r'<Field Name="([^"]+)"', rdl))


def _field_refs(rdl: str):
    return set(re.findall(r"Fields!([A-Za-z0-9_]+)\.Value", rdl))


def _split_select_items(sql: str):
    """Yield each top-level SELECT item (between SELECT and FROM)."""
    upper = sql.upper()
    sel = upper.find("SELECT")
    if sel < 0:
        return
    # Find top-level FROM.
    depth = 0
    i = sel + len("SELECT")
    fm = -1
    while i < len(sql):
        ch = sql[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and upper[i:i + 4] == "FROM" and (
            i + 4 == len(sql) or not sql[i + 4].isalnum()
        ):
            fm = i
            break
        i += 1
    if fm < 0:
        return
    body = sql[sel + len("SELECT"):fm]
    cur = []
    depth = 0
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            yield "".join(cur).strip()
            cur = []
        else:
            cur.append(ch)
    if cur:
        yield "".join(cur).strip()


@pytest.mark.parametrize("case_name,src_path", _cases())
def test_no_orphan_field_refs(case_name, src_path):
    """Every Fields!X.Value reference in the RDL body must have a
    matching <Field Name="X"> declaration in some dataset."""
    from converter import convert
    rdl = convert(src_path.read_bytes())["rdl_xml"]
    declared = _field_decls(rdl)
    refs = _field_refs(rdl)
    orphans = refs - declared
    assert not orphans, (
        f"[{case_name}] Tablix cells reference fields not declared in any "
        f"dataset: {sorted(orphans)} -- Report Builder will reject Save."
    )


@pytest.mark.parametrize("case_name,src_path", _cases())
def test_every_unaliased_expression_has_an_alias_added(case_name, src_path):
    """For every SELECT item that is a non-bare expression, the emitted
    CommandText must include either an explicit AS alias or an implicit
    trailing-identifier alias. Catches the "expression returns verbose
    Oracle column name" bug at the SQL level."""
    from converter import convert
    rdl = convert(src_path.read_bytes())["rdl_xml"]

    bare_col = re.compile(
        r"^\s*[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?\s*$"
    )
    explicit_alias = re.compile(
        r"\bAS\b\s+[A-Za-z_][A-Za-z0-9_]*\s*$", re.IGNORECASE
    )
    implicit_alias = re.compile(
        r"(?:\)|[A-Za-z0-9_])\s+[A-Za-z_][A-Za-z0-9_]*\s*$"
    )

    offenders = []
    for ct in _command_texts(rdl):
        for item in _split_select_items(ct):
            if not item or bare_col.match(item):
                continue
            if explicit_alias.search(item) or implicit_alias.search(item):
                continue
            offenders.append(item[:80])
    assert not offenders, (
        f"[{case_name}] SELECT expression(s) with no alias -- Refresh "
        f"Fields will produce verbose Oracle column names that don't "
        f"match our <Field> declarations and Save will fail:\n"
        + "\n".join(f"  {o}" for o in offenders[:5])
    )


@pytest.mark.parametrize("case_name,src_path", _cases())
def test_no_double_alias_inserted(case_name, src_path):
    """SQL must never end up with two AS clauses in a row (would happen
    if the aliaser failed to detect an existing implicit alias)."""
    from converter import convert
    rdl = convert(src_path.read_bytes())["rdl_xml"]
    for ct in _command_texts(rdl):
        dbl = re.findall(r"AS\s+[A-Za-z_][A-Za-z0-9_]*\s+AS\s+",
                         ct, re.IGNORECASE)
        assert not dbl, (
            f"[{case_name}] double-AS sequence in CommandText -- the "
            f"aliaser failed to detect an existing alias: {dbl[:3]}"
        )

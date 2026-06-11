"""
Oracle Reports XML parser.

Public API:
    parse_oracle_xml(xml_bytes: bytes) -> ParsedReport

Reads an Oracle Reports XML artifact (DTD 9.0.x) and produces a ParsedReport
matching the shared contract in converter/models.py.

The parser is intentionally defensive: missing attributes default to empty
strings/zero, missing elements yield empty lists, and unparseable nodes are
recorded as warnings rather than raised.
"""
from __future__ import annotations

import math
import re
from typing import List, Optional

from lxml import etree

from converter.models import (
    DataItem,
    DataQuery,
    EmbeddedImage,
    FormulaColumn,
    LayoutField,
    LayoutGroup,
    ParsedReport,
    QueryGroup,
    ReportParameter,
    TriggerCode,
)
from converter.parsers.oracle_colors import resolve_color


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decode(xml_bytes: bytes) -> str:
    """Return a string view of the raw XML for the side-by-side panel."""
    for enc in ("utf-8", "windows-1252", "latin-1"):
        try:
            return xml_bytes.decode(enc)
        except UnicodeDecodeError:
            continue
    return xml_bytes.decode("utf-8", errors="replace")


def _attr(el, name: str, default: str = "") -> str:
    val = el.get(name)
    if val is None:
        return default
    return val.strip()


def _int_attr(el, name: str, default: int = 0) -> int:
    val = el.get(name)
    if val is None or val == "":
        return default
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return default


def _float_attr(el, name: str, default: float = 0.0) -> float:
    val = el.get(name)
    if val is None or val == "":
        return default
    try:
        f = float(val)
    except (TypeError, ValueError):
        return default
    # Reject inf/-inf/nan: Python float() accepts "inf"/"nan", but they break
    # JSON serialization of the report dict (browsers reject Infinity/NaN) and
    # produce invalid RDL dimensions. Fall back to the default.
    if not math.isfinite(f):
        return default
    return f


def _localname(el) -> str:
    """Return the element's tag without any XML namespace prefix."""
    tag = el.tag
    if isinstance(tag, str) and "}" in tag:
        return tag.split("}", 1)[1]
    return tag if isinstance(tag, str) else ""


def _find(parent, name: str):
    """Find direct child by local name (namespace-agnostic)."""
    for child in parent:
        if _localname(child) == name:
            return child
    return None


def _findall(parent, name: str):
    """Find all direct children matching local name."""
    return [c for c in parent if _localname(c) == name]


def _iter_descendants(parent, name: str):
    """Iterate descendants matching local name (namespace-agnostic)."""
    for el in parent.iter():
        if _localname(el) == name:
            yield el


def _text_of(el) -> str:
    """Concatenate text + tail of an element's children. Returns empty if None."""
    if el is None:
        return ""
    parts: List[str] = []
    if el.text:
        parts.append(el.text)
    for child in el:
        if child.text:
            parts.append(child.text)
        if child.tail:
            parts.append(child.tail)
    return "".join(parts).strip()


def _safe_id(s: str) -> str:
    if not s:
        return "_"
    out = []
    for ch in s:
        if ch.isalnum() or ch == "_":
            out.append(ch)
        else:
            out.append("_")
    result = "".join(out)
    if result and result[0].isdigit():
        result = "_" + result
    return result or "_"


def _guess_image_mime(hex_str: str, fallback: str = "image/gif") -> str:
    if not hex_str:
        return fallback
    cleaned = "".join(hex_str.split()).lower()
    if cleaned.startswith("47494638"):
        return "image/gif"
    if cleaned.startswith("89504e47"):
        return "image/png"
    if cleaned.startswith("ffd8ff"):
        return "image/jpeg"
    return fallback


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------

def _parse_parameters(data_el, warnings: List[str]) -> List[ReportParameter]:
    params: List[ReportParameter] = []
    if data_el is None:
        return params
    for up in _findall(data_el, "userParameter"):
        try:
            display_attr = _attr(up, "display", "yes").lower()
            params.append(
                ReportParameter(
                    name=_attr(up, "name"),
                    label=_attr(up, "label"),
                    datatype=_attr(up, "datatype", "character"),
                    width=_int_attr(up, "width"),
                    precision=_int_attr(up, "precision"),
                    initial_value=up.get("initialValue"),
                    input_mask=up.get("inputMask"),
                    display=(display_attr != "no"),
                )
            )
        except Exception as exc:  # pragma: no cover - defensive
            warnings.append(f"failed to parse userParameter: {exc}")
    return params


# ---------------------------------------------------------------------------
# Queries / DataSources
# ---------------------------------------------------------------------------

def _parse_data_items(group_el, warnings: List[str]) -> List[DataItem]:
    items: List[DataItem] = []
    if group_el is None:
        return items
    for di in _findall(group_el, "dataItem"):
        try:
            descriptor = _find(di, "dataDescriptor")
            expression = _attr(descriptor, "expression") if descriptor is not None else ""
            datatype = _attr(di, "datatype") or _attr(di, "oracleDatatype", "vchar2")
            # Oracle NUMBER scale/precision (on the descriptor, occasionally the
            # dataItem) decide Int32 vs Decimal -- see DataItem.ssrs_datatype.
            scale = precision = None
            for src in (descriptor, di):
                if src is None:
                    continue
                if scale is None and (src.get("scale") or "") != "":
                    scale = _int_attr(src, "scale", 0)
                if precision is None and (src.get("precision") or "") != "":
                    precision = _int_attr(src, "precision", 0)
            items.append(
                DataItem(
                    name=_attr(di, "name"),
                    expression=expression,
                    datatype=datatype,
                    width=_int_attr(di, "width"),
                    label=_attr(di, "defaultLabel"),
                    scale=scale,
                    precision=precision,
                )
            )
        except Exception as exc:  # pragma: no cover - defensive
            warnings.append(f"failed to parse dataItem: {exc}")
    return items


def _collect_data_items_recursive(group_el, warnings: List[str]) -> List[DataItem]:
    """Walk a <group> tree and collect dataItems from it and any nested <group>s."""
    items: List[DataItem] = []
    if group_el is None:
        return items
    items.extend(_parse_data_items(group_el, warnings))
    for child in _findall(group_el, "group"):
        items.extend(_collect_data_items_recursive(child, warnings))
    return items


def _parse_queries(data_el, warnings: List[str]) -> List[DataQuery]:
    queries: List[DataQuery] = []
    if data_el is None:
        return queries
    for ds in _findall(data_el, "dataSource"):
        try:
            select_el = _find(ds, "select")
            sql = ""
            if select_el is not None:
                sql = (select_el.text or "").strip()
            # Oracle Reports lets a single <dataSource> hold multiple sibling
            # <group> elements (e.g. G_Site + G_VISIT under Q_VISIT). Walk
            # ALL of them — not just the first — and harvest dataItems
            # recursively from each. De-dup by item name to avoid double-counting
            # if Oracle ever emits the same item under two groups.
            items: List[DataItem] = []
            seen: set[str] = set()
            group_names: List[str] = []

            def _collect_group_names(group_el):
                gn = _attr(group_el, "name")
                if gn:
                    group_names.append(gn)
                for child in _findall(group_el, "group"):
                    _collect_group_names(child)

            for top_group in _findall(ds, "group"):
                _collect_group_names(top_group)
                for di in _collect_data_items_recursive(top_group, warnings):
                    if di.name and di.name in seen:
                        continue
                    if di.name:
                        seen.add(di.name)
                    items.append(di)

            # Build the NESTED group tree (master-detail hierarchy + summaries).
            # Only the DIRECT children/items of each <group> -- nesting is the
            # structure we need to render a real master-detail Tablix.
            def _build_group_tree(group_el) -> QueryGroup:
                direct_items = _parse_data_items(group_el, warnings)
                summaries = []
                for sm in _findall(group_el, "summary"):
                    summaries.append({
                        "name": _attr(sm, "name"),
                        "source": _attr(sm, "source"),
                        "function": _attr(sm, "function") or "sum",
                        "label": _attr(sm, "defaultLabel"),
                    })
                qg = QueryGroup(
                    name=_attr(group_el, "name"),
                    items=direct_items,
                    summaries=summaries,
                    break_col=(direct_items[0].name if direct_items else ""),
                )
                for child in _findall(group_el, "group"):
                    qg.children.append(_build_group_tree(child))
                return qg

            groups = [_build_group_tree(g) for g in _findall(ds, "group")]

            queries.append(
                DataQuery(
                    name=_attr(ds, "name"),
                    sql=sql,
                    items=items,
                    group_names=group_names,
                    groups=groups,
                )
            )
        except Exception as exc:  # pragma: no cover - defensive
            warnings.append(f"failed to parse dataSource: {exc}")

    # Oracle master-detail links: <link parentGroup="G_X" childQuery="Q_Y"
    # condition="eq" sqlClause="where"/>. Attach the relation to the CHILD
    # query so the generator can resolve cross-dataset fields via Lookup()
    # instead of blanking them to =Nothing.
    by_name = {(q.name or "").upper(): q for q in queries}
    # Map every group name -> the query that owns it, so a link's parentGroup
    # resolves to the master DataQuery.
    group_owner = {}
    for q in queries:
        for gn in (q.group_names or []):
            group_owner[gn.upper()] = q
    for ln in _findall(data_el, "link"):
        child = _attr(ln, "childQuery")
        parent_group = _attr(ln, "parentGroup")
        q = by_name.get((child or "").upper())
        if q is not None and parent_group:
            q.parent_group = parent_group
            q.link_condition = _attr(ln, "condition")
            # Make the child's join key SELECTABLE so a Lookup() back to the
            # master is valid. Oracle child queries FILTER by the parent value
            # (e.g. ":SITE_ID" in the WHERE) but often don't RETURN it as a
            # column -- so SSRS has no child-side key column to Lookup against,
            # and the field falls back to =Nothing. We add each join-key column
            # (a child bind that names a master column, not already selected) to
            # BOTH the child SELECT and its declared items. Deterministic,
            # structural (driven by the parsed <link> + bind vars) -- no
            # report-specific names. Only the child datasets are touched.
            master = group_owner.get((parent_group or "").upper())
            if master is not None:
                _augment_child_join_keys(q, master, warnings)
    return queries


def _image_magic_ok(b: bytes) -> bool:
    return (b[:4] == b"GIF8" or b[:4] == b"\x89PNG"
            or b[:3] == b"\xff\xd8\xff" or b[:2] == b"BM")


def _normalize_image_hex(raw_hex: str) -> str:
    """Normalize Oracle's ``hexidecimal`` image payloads to standard hex.

    Some Oracle Reports exports write each byte's two hex NIBBLES in
    swapped order (``GIF89a`` = 47 49 46 38 39 61 arrives as
    74 94 64 83 93 16). Detect via image magic bytes: if the plain decode
    has no known magic but the nibble-swapped decode does, return the
    swapped form. Purely structural — no format is assumed up front.
    """
    h = re.sub(r"\s+", "", raw_hex or "")
    if not h or len(h) % 2:
        return h
    try:
        if _image_magic_ok(bytes.fromhex(h[:64])):
            return h
    except ValueError:
        return h
    swapped = "".join(h[i + 1] + h[i] for i in range(0, len(h), 2))
    try:
        if _image_magic_ok(bytes.fromhex(swapped[:64])):
            return swapped
    except ValueError:
        pass
    return h


def _augment_child_join_keys(child, master, warnings: List[str]) -> None:
    """Make the child's join-key column SELECT-able so a Lookup() can bind to
    it. A join key is a bind variable that the child SQL EQUATES to a real
    column (``col = :bind`` / ``:bind = col``) AND whose name matches a master
    column not already selected by the child. We inject the actual qualified
    column, aliased to the key name (e.g. ``SA.Prog_Id Prog_Id``).

    CRITICAL: a bind that appears ONLY inside filter expressions
    (``NVL(:P, ...)``, date ranges, function args) is NOT a column. Injecting
    it into the SELECT as a bare identifier raises ``ORA-00904: invalid
    identifier``, which blocks the user from refreshing the dataset. Such binds
    are deliberately skipped -- correctness of the SELECT beats Lookup coverage
    (an unresolved Lookup falls back to =Nothing; an invalid SELECT is fatal)."""
    try:
        sql = child.sql or ""
        child_cols = {(it.name or "").upper() for it in (child.items or [])}
        master_cols = {(it.name or "").upper(): it.name
                       for it in (master.items or [])}
        # bind (UPPER) -> the qualified column it is equated to in the child SQL
        eq_col = {}
        for col, bind in re.findall(r"([A-Za-z_][\w.]*)\s*=\s*:([A-Za-z_]\w*)", sql):
            eq_col.setdefault(bind.upper(), col)
        for bind, col in re.findall(r":([A-Za-z_]\w*)\s*=\s*([A-Za-z_][\w.]*)", sql):
            eq_col.setdefault(bind.upper(), col)
        # Ordered, de-duplicated genuine join keys: matches a master column,
        # not already a child column, AND equated to a real column.
        keys = []  # (alias_name, qualified_column)
        seen = set()
        widen = []  # ALL correlation binds (incl. already-selected columns)
        for b in re.findall(r":([A-Za-z_][A-Za-z0-9_]*)", sql):
            ub = b.upper()
            if ub in master_cols and ub in eq_col and b not in widen:
                widen.append(b)
            if ub in seen:
                continue
            if ub in master_cols and ub not in child_cols and ub in eq_col:
                seen.add(ub)
                keys.append((master_cols[ub], eq_col[ub]))

        # DECORRELATE the link predicates. In Oracle Reports the <link>
        # re-executes this child query PER MASTER ROW with the bind set
        # from that row. SSRS runs the dataset ONCE, with the bind coming
        # from a report parameter that defaults to NULL — so a raw
        # ``col = :bind`` returns ZERO rows on the server (blank permittee
        # block; invisible with synthetic data that happens to match).
        # Widening to ``(:bind IS NULL OR col = :bind)`` makes a NULL bind
        # return the FULL set, and the Lookup() join re-applies the
        # correlation per row client-side.
        def _widen(text: str) -> str:
            for b in widen:
                text = re.sub(
                    rf"([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)?)\s*=\s*"
                    rf":({re.escape(b)})\b",
                    lambda m: (f"(:{m.group(2)} IS NULL OR "
                               f"{m.group(1)} = :{m.group(2)})"),
                    text, flags=re.IGNORECASE)
            return text

        if widen:
            child.sql = _widen(child.sql or "")
            if child.tsql:
                child.tsql = _widen(child.tsql)
            sql = child.sql or ""

        if not keys:
            return

        def _inject(text: str) -> str:
            # Insert "col alias, " right after the first top-level SELECT
            # (after DISTINCT if present). Oracle column-alias form, no AS --
            # matches the report's own style ("SA.Site_Id SA_Site_Id").
            m = re.search(r"\bSELECT\b", text, re.IGNORECASE)
            if not m:
                return text
            at = m.end()
            dm = re.match(r"\s+DISTINCT\b", text[at:], re.IGNORECASE)
            if dm:
                at += dm.end()
            frag = " " + ", ".join(
                (col if col.upper() == alias.upper() else f"{col} {alias}")
                for alias, col in keys) + ","
            return text[:at] + frag + text[at:]

        child.sql = _inject(child.sql or "")
        if child.tsql:
            child.tsql = _inject(child.tsql)
        # Declare the new columns so the dataset <Fields> includes them.
        for alias, _col in keys:
            child.items.append(DataItem(name=alias, expression=alias,
                                        datatype="vchar2"))
    except Exception as exc:  # pragma: no cover - defensive
        warnings.append(f"join-key augment failed for {child.name}: {exc}")


# ---------------------------------------------------------------------------
# Formulas
# ---------------------------------------------------------------------------

def _parse_formulas(data_el, program_units_index: dict, warnings: List[str]) -> List[FormulaColumn]:
    """Walk the entire <data> subtree and pick up every <formula> element.

    Formula elements may live directly under <data> OR nested inside a
    <group> (where they sit alongside <dataItem>s). Use a descendants walk
    to capture both forms.

    The PL/SQL body for a formula lives in <programUnits>/<function name="...">
    where the function name matches the formula's @source attribute. If a
    nested <plsql> CDATA child exists on the formula itself, prefer that.
    """
    formulas: List[FormulaColumn] = []
    if data_el is None:
        return formulas
    for f in _iter_descendants(data_el, "formula"):
        try:
            name = _attr(f, "name")
            return_type = _attr(f, "datatype", "VARCHAR2")
            source_fn = _attr(f, "source")
            # Try inline <plsql> first (some Oracle Reports versions inline it)
            plsql_inline = _find(f, "plsql")
            if plsql_inline is not None and (plsql_inline.text or "").strip():
                body = (plsql_inline.text or "").strip()
            else:
                body = program_units_index.get(source_fn.lower(), "") if source_fn else ""
                if not body and source_fn:
                    # Fall back to a case-insensitive lookup
                    for k, v in program_units_index.items():
                        if k.lower() == source_fn.lower():
                            body = v
                            break
            formulas.append(
                FormulaColumn(
                    name=name,
                    return_type=return_type,
                    plsql_body=body,
                )
            )
        except Exception as exc:  # pragma: no cover - defensive
            warnings.append(f"failed to parse formula: {exc}")
    # Oracle Reports <placeholder> elements are derived/computed values just
    # like <formula>, but they're populated by a sibling formula's PL/SQL via
    # ``:CP_X := ...`` assignments instead of having their own body. The RDL
    # generator needs to know about them so that &CP_X tokens in layout text
    # don't fall through to a phantom Fields! reference. Treat them as
    # formulas with an empty body (the user re-implements the assignment
    # logic in SSRS as a calculated field).
    for ph in _iter_descendants(data_el, "placeholder"):
        try:
            name = _attr(ph, "name")
            if not name:
                continue
            # Skip if a real <formula> with the same name was already collected.
            if any((f.name or "").upper() == name.upper() for f in formulas):
                continue
            return_type = _attr(ph, "datatype", "VARCHAR2")
            formulas.append(
                FormulaColumn(
                    name=name,
                    return_type=return_type,
                    plsql_body="",
                )
            )
        except Exception as exc:  # pragma: no cover - defensive
            warnings.append(f"failed to parse placeholder: {exc}")
    # Oracle Reports <summary> elements are aggregate columns (count, sum, etc.).
    # Like formulas and placeholders, they're computed at run time and don't
    # appear in any base dataset's column list, so &SUMMARY_NAME tokens in
    # layout text need to resolve to a literal SSRS expression instead of a
    # phantom Fields! reference. Re-implementation lands as an SSRS aggregate
    # function (Sum/Count/Avg) in a calculated field.
    for sm in _iter_descendants(data_el, "summary"):
        try:
            name = _attr(sm, "name")
            if not name:
                continue
            if any((f.name or "").upper() == name.upper() for f in formulas):
                continue
            return_type = _attr(sm, "datatype", "NUMBER")
            func = _attr(sm, "function", "")
            source_col = _attr(sm, "source", "")
            note_body = (
                f"Oracle <summary function={func!r} source={source_col!r}>; "
                f"re-implement as SSRS aggregate expression"
                if func or source_col else ""
            )
            formulas.append(
                FormulaColumn(
                    name=name,
                    return_type=return_type,
                    plsql_body=note_body,
                )
            )
        except Exception as exc:  # pragma: no cover - defensive
            warnings.append(f"failed to parse summary: {exc}")
    return formulas


# ---------------------------------------------------------------------------
# Triggers / programUnits
# ---------------------------------------------------------------------------

def _parse_program_units(root, warnings: List[str]) -> (List[TriggerCode], dict):
    """Return (triggers list, name->body index).

    Oracle Reports stores PL/SQL bodies inside <programUnits> as either
    <function name="..."><textSource><![CDATA[...]]></textSource></function>
    or <procedure name="..."><textSource>...</textSource></procedure>, or
    occasionally a <programUnit name="..."><![CDATA[...]]></programUnit>.
    Capture all forms and expose the body text indexed by name.
    """
    triggers: List[TriggerCode] = []
    index: dict = {}

    pu_root = _find(root, "programUnits")
    if pu_root is None:
        return triggers, index

    for pu in pu_root:
        try:
            name = _attr(pu, "name")
            if not name:
                continue
            tag = _localname(pu)
            body = ""
            ts = _find(pu, "textSource")
            if ts is not None and (ts.text or "").strip():
                body = (ts.text or "").strip()
            elif (pu.text or "").strip():
                body = (pu.text or "").strip()
            else:
                # Some variants use <plsql> inside
                plsql = _find(pu, "plsql")
                if plsql is not None:
                    body = (plsql.text or "").strip()

            if name:
                index[name.lower()] = body
            triggers.append(TriggerCode(name=name, body=body))
            if tag not in ("function", "procedure", "programUnit"):
                warnings.append(f"unexpected programUnit child <{tag}>")
        except Exception as exc:  # pragma: no cover - defensive
            warnings.append(f"failed to parse programUnit: {exc}")
    return triggers, index


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def _apply_geometry(target, el) -> None:
    geom = _find(el, "geometryInfo")
    if geom is None:
        # Simplified layout dialect: geometry as direct x/y/width/height
        # attributes on the element itself (no <geometryInfo> child).
        if any(_attr(el, a) for a in ("x", "y", "width", "height")):
            target.x = _float_attr(el, "x")
            target.y = _float_attr(el, "y")
            target.width = _float_attr(el, "width")
            target.height = _float_attr(el, "height")
        return
    target.x = _float_attr(geom, "x")
    target.y = _float_attr(geom, "y")
    target.width = _float_attr(geom, "width")
    target.height = _float_attr(geom, "height")


def _apply_visual_settings(target, el) -> None:
    vs = _find(el, "visualSettings")
    if vs is None:
        return
    target.border_width = _float_attr(vs, "lineWidth")
    target.border_pattern = _attr(vs, "linePattern")
    # Color/style attributes (mapped to CSS by oracle_colors.resolve_color)
    target.background_color = resolve_color(_attr(vs, "fillBackgroundColor"))
    target.foreground_color = resolve_color(_attr(vs, "fillForegroundColor"))
    target.fill_pattern = _attr(vs, "fillPattern").lower()
    line_color = _attr(vs, "lineColor")
    edge_color = _attr(vs, "edgeLineColor") or line_color
    target.border_color = resolve_color(edge_color)


def _parse_visual_settings(el):
    """Extract color/style attributes from <visualSettings> child of `el`.

    Returns a dict with keys: background_color, foreground_color,
    fill_pattern, line_color, edge_line_color. Missing/unknown values
    resolve to empty strings.
    """
    vs = _find(el, "visualSettings")
    if vs is None:
        return {
            "background_color": "",
            "foreground_color": "",
            "fill_pattern": "",
            "line_color": "",
            "edge_line_color": "",
        }
    line_color = _attr(vs, "lineColor")
    edge_color_raw = _attr(vs, "edgeLineColor") or line_color
    return {
        "background_color": resolve_color(_attr(vs, "fillBackgroundColor")),
        "foreground_color": resolve_color(_attr(vs, "fillForegroundColor")),
        "fill_pattern": _attr(vs, "fillPattern").lower(),
        "line_color": resolve_color(line_color),
        "edge_line_color": resolve_color(edge_color_raw),
    }


def _format_trigger_of(el) -> str:
    direct = _attr(el, "formatTrigger")
    if direct:
        return direct
    al = _find(el, "advancedLayout")
    if al is not None:
        return _attr(al, "formatTrigger")
    return ""


def _layout_field_from_element(el) -> LayoutField:
    """Build a LayoutField from a <field> or <text> element."""
    tag = _localname(el)
    name = _attr(el, "name")
    source = _attr(el, "source")
    geom = _find(el, "geometryInfo")
    # Real Oracle exports nest geometry in <geometryInfo>; a simplified dialect
    # puts x/y/width/height directly on the <field>/<text>. Accept either.
    geo_src = geom if geom is not None else el
    x = _float_attr(geo_src, "x")
    y = _float_attr(geo_src, "y")
    width = _float_attr(geo_src, "width")
    height = _float_attr(geo_src, "height")

    text_value = source
    bold = False
    italic = False
    font_size = 10
    font_family = ""
    color = ""
    align = _attr(el, "alignment")
    text_settings = _find(el, "textSettings")
    if text_settings is not None and not align:
        align = _attr(text_settings, "justify")

    if tag == "text":
        segs: List[str] = []
        first_font = None
        for ts in _iter_descendants(el, "textSegment"):
            if first_font is None:
                first_font = _find(ts, "font")
            for s in _findall(ts, "string"):
                if s.text:
                    segs.append(s.text)
        text_value = "".join(segs).strip()
        if first_font is not None:
            font_size = _int_attr(first_font, "size", 10) or 10
            font_family = _attr(first_font, "face")
            bold = _attr(first_font, "weight").lower() == "bold" or _attr(first_font, "bold").lower() == "yes"
            style = _attr(first_font, "style").lower()
            italic = style == "italic" or _attr(first_font, "italic").lower() == "yes"
            color = _attr(first_font, "color") or _attr(first_font, "foreground")

    font = _find(el, "font")
    if font is not None:
        font_size = _int_attr(font, "size", font_size) or font_size
        if not font_family:
            font_family = _attr(font, "face")
        if not bold:
            bold = _attr(font, "bold").lower() == "yes" or _attr(font, "weight").lower() == "bold"
        if not italic:
            italic = _attr(font, "italic").lower() == "yes" or _attr(font, "style").lower() == "italic"
        if not color:
            color = _attr(font, "color") or _attr(font, "foreground")

    kind = "text" if tag == "text" else "field"

    vs_attrs = _parse_visual_settings(el)

    # Drill-through hyperlink: <webSettings hyperlink="&CF_URL_X">. Strip the
    # leading & so it matches the formula/placeholder name the resolver knows.
    ws = _find(el, "webSettings")
    hyperlink = _attr(ws, "hyperlink").lstrip("&").strip() if ws is not None else ""

    # Oracle display format mask (numeric/date) -> SSRS <Format>. Oracle puts
    # it on the field directly or inside <advancedLayout>.
    format_mask = _attr(el, "formatMask")
    if not format_mask:
        al = _find(el, "advancedLayout")
        if al is not None:
            format_mask = _attr(al, "formatMask")

    return LayoutField(
        name=name,
        source=source,
        text=text_value or source,
        kind=kind,
        bold=bold,
        italic=italic,
        font_size=font_size,
        font_family=font_family,
        color=color,
        align=align,
        x=x,
        y=y,
        width=width,
        height=height,
        format_trigger=_format_trigger_of(el),
        background_color=vs_attrs["background_color"],
        foreground_color=vs_attrs["foreground_color"],
        fill_pattern=vs_attrs["fill_pattern"],
        border_color=vs_attrs["edge_line_color"] or vs_attrs["line_color"],
        hyperlink=hyperlink,
        format_mask=format_mask,
    )


def _walk_layout_node(node, current_group: Optional[LayoutGroup],
                     groups_by_name: dict, root_groups: List[LayoutGroup],
                     warnings: List[str],
                     embedded_images: List[EmbeddedImage]) -> None:
    """Walk the layout subtree, attaching fields to the most specific group."""
    def _page_break_before(el) -> bool:
        # Oracle stores it on a child <generalLayout pageBreakBefore="yes"/>.
        for gl in el:
            if _localname(gl) == "generalLayout":
                if _attr(gl, "pageBreakBefore").lower() in ("yes", "true"):
                    return True
        return False

    for child in node:
        tag = _localname(child)
        if tag == "repeatingFrame":
            try:
                rf_name = _attr(child, "name")
                rf_source = _attr(child, "source")
                key = rf_source or rf_name
                grp = groups_by_name.get(key)
                if grp is None:
                    grp = LayoutGroup(
                        name=rf_name or rf_source or "group",
                        kind="repeating_frame",
                        source_query=rf_source,
                        format_trigger=_format_trigger_of(child),
                        page_break_before=_page_break_before(child),
                    )
                    _apply_geometry(grp, child)
                    _apply_visual_settings(grp, child)
                    groups_by_name[key] = grp
                    if current_group is None:
                        root_groups.append(grp)
                    else:
                        current_group.children.append(grp)
                _walk_layout_node(child, grp, groups_by_name, root_groups,
                                  warnings, embedded_images)
            except Exception as exc:  # pragma: no cover - defensive
                warnings.append(f"failed to parse repeatingFrame: {exc}")
        elif tag == "frame":
            try:
                fr_name = _attr(child, "name") or "frame"
                grp = LayoutGroup(
                    name=fr_name,
                    kind="frame",
                    format_trigger=_format_trigger_of(child),
                    page_break_before=_page_break_before(child),
                )
                _apply_geometry(grp, child)
                _apply_visual_settings(grp, child)
                if current_group is None:
                    root_groups.append(grp)
                else:
                    current_group.children.append(grp)
                _walk_layout_node(child, grp, groups_by_name, root_groups,
                                  warnings, embedded_images)
            except Exception as exc:  # pragma: no cover - defensive
                warnings.append(f"failed to parse frame: {exc}")
        elif tag in ("field", "text"):
            try:
                lf = _layout_field_from_element(child)
                target = current_group
                if target is None:
                    key = "_default"
                    target = groups_by_name.get(key)
                    if target is None:
                        target = LayoutGroup(name="_default", kind="_default",
                                             source_query="")
                        groups_by_name[key] = target
                        root_groups.append(target)
                target.fields.append(lf)
            except Exception as exc:  # pragma: no cover - defensive
                warnings.append(f"failed to parse layout field: {exc}")
            _walk_layout_node(child, current_group, groups_by_name, root_groups,
                              warnings, embedded_images)
        elif tag == "image":
            try:
                name = _attr(child, "name")
                geom = _find(child, "geometryInfo")
                _gsrc = geom if geom is not None else child
                x = _float_attr(_gsrc, "x")
                y = _float_attr(_gsrc, "y")
                width = _float_attr(_gsrc, "width")
                height = _float_attr(_gsrc, "height")
                vs_attrs = _parse_visual_settings(child)
                bin_data = _find(child, "binaryData")
                image_id = ""
                if bin_data is not None and (bin_data.text or "").strip():
                    raw_hex = _normalize_image_hex((bin_data.text or "").strip())
                    declared_mime = _attr(bin_data, "format")
                    mime = declared_mime or _guess_image_mime(raw_hex)
                    image_id = _safe_id(name or f"image_{len(embedded_images)}")
                    embedded_images.append(
                        EmbeddedImage(
                            id=image_id,
                            mime_type=mime,
                            hex_data=raw_hex,
                        )
                    )
                    lf = LayoutField(
                        name=name,
                        kind="image",
                        image_id=image_id,
                        x=x, y=y, width=width, height=height,
                        format_trigger=_format_trigger_of(child),
                        background_color=vs_attrs["background_color"],
                        foreground_color=vs_attrs["foreground_color"],
                        fill_pattern=vs_attrs["fill_pattern"],
                        border_color=vs_attrs["edge_line_color"] or vs_attrs["line_color"],
                    )
                else:
                    lf = LayoutField(
                        name=name,
                        kind="image",
                        source=name,
                        x=x, y=y, width=width, height=height,
                        format_trigger=_format_trigger_of(child),
                        background_color=vs_attrs["background_color"],
                        foreground_color=vs_attrs["foreground_color"],
                        fill_pattern=vs_attrs["fill_pattern"],
                        border_color=vs_attrs["edge_line_color"] or vs_attrs["line_color"],
                    )
                target = current_group
                if target is None:
                    key = "_default"
                    target = groups_by_name.get(key)
                    if target is None:
                        target = LayoutGroup(name="_default", kind="_default",
                                             source_query="")
                        groups_by_name[key] = target
                        root_groups.append(target)
                target.fields.append(lf)
            except Exception as exc:  # pragma: no cover - defensive
                warnings.append(f"failed to parse image: {exc}")
        elif tag in ("matrix", "matrixCol", "matrixRow", "matrixCell"):
            # Oracle cross-tab. Two dialects (wild-corpus verified):
            #   * 6i: <matrix> nests <matrixCol>/<matrixRow>/<matrixCell>,
            #     each holding the dimension/cell <field>s directly.
            #   * 9.0.2+: <matrix horizontalFrame=... verticalFrame=...
            #     xProductGroup=...> references repeatingFrames parsed
            #     elsewhere; dimensions come from those frames.
            try:
                kindmap = {"matrix": "matrix", "matrixCol": "matrix_col",
                           "matrixRow": "matrix_row",
                           "matrixCell": "matrix_cell"}
                grp = LayoutGroup(
                    name=_attr(child, "name") or tag,
                    kind=kindmap[tag],
                    matrix_attrs={k: v for k, v in child.attrib.items()
                                  if k in ("horizontalFrame", "verticalFrame",
                                           "xProductGroup", "crossProduct",
                                           "template")},
                )
                if current_group is not None:
                    current_group.children.append(grp)
                else:
                    root_groups.append(grp)
                _walk_layout_node(child, grp, groups_by_name, root_groups,
                                  warnings, embedded_images)
            except Exception as exc:  # pragma: no cover - defensive
                warnings.append(f"failed to parse matrix: {exc}")
        else:
            _walk_layout_node(child, current_group, groups_by_name, root_groups,
                              warnings, embedded_images)


def _parse_layout(root, warnings: List[str],
                  embedded_images: List[EmbeddedImage]) -> List[LayoutGroup]:
    layout_el = _find(root, "layout")
    if layout_el is None:
        return []
    root_groups: List[LayoutGroup] = []
    groups_by_name: dict = {}
    sections = _findall(layout_el, "section")
    if not sections:
        # Simplified dialect: <layout> directly contains fields / frames with
        # no <section><body> wrapper -- treat the whole layout as section_main
        # so the renderer and detect_report_kind can find its content.
        main = LayoutGroup(name="section_main", kind="section_main")
        groups_by_name["__section__main"] = main
        root_groups.append(main)
        _walk_layout_node(layout_el, main, groups_by_name, root_groups,
                          warnings, embedded_images)
    for section in sections:
        section_name = _attr(section, "name") or "section"
        repeat_on = _attr(section, "repeatOn")
        section_group = LayoutGroup(
            name=f"section_{section_name}",
            kind=f"section_{section_name}",
            source_query=repeat_on,
            repeat_on=repeat_on,
        )
        groups_by_name[f"__section__{section_name}"] = section_group
        root_groups.append(section_group)
        _walk_layout_node(section, section_group, groups_by_name,
                          root_groups, warnings, embedded_images)
    pruned: List[LayoutGroup] = []
    for g in root_groups:
        if g.fields or g.children:
            pruned.append(g)
    return pruned or root_groups


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------

def _extract_embedded_report(raw_xml: str) -> Optional[str]:
    """Pull the real ``<report ...>...</report>`` out of a wrapper document.

    Oracle Reports is VERY commonly stored as a ``.jsp`` "Reports Web
    Source" file: the full report definition sits verbatim inside an HTML
    comment between ``<rw:report>/<rw:objects>`` tags, followed by the
    paper-layout HTML template. Feeding the whole .jsp to the XML parser
    yields junk (blank conversion). Also handles a stray XML prolog or
    leading ``<%@ ... %>`` JSP directives before the report.

    Returns the report substring when the document is NOT already a clean
    ``<report>`` root, else None (normal path untouched). Generic — keyed
    only on the standard tag, no per-report logic."""
    s = raw_xml.lstrip()
    low = s.lower()
    # Already a clean report doc (optionally with an <?xml?> prolog).
    if low.startswith("<report") or low.startswith("<?xml"):
        # But a prolog could still precede a JSP wrapper, so only fast-path
        # when a <report> actually heads the content after the prolog.
        after = low.split("?>", 1)[-1].lstrip() if low.startswith("<?xml") else low
        if after.startswith("<report"):
            return None
    m = re.search(r"<report\b", raw_xml, re.IGNORECASE)
    if not m:
        return None
    end = raw_xml.lower().rfind("</report>")
    if end == -1 or end < m.start():
        return None
    return raw_xml[m.start():end + len("</report>")]


def parse_oracle_xml(xml_bytes: bytes) -> ParsedReport:
    """Parse an Oracle Reports XML byte string into a ParsedReport."""
    warnings: List[str] = []
    raw_xml = _decode(xml_bytes)

    # Unwrap a .jsp "Reports Web Source" (or any wrapper) down to the real
    # <report> block before parsing. No-op for clean report documents.
    embedded = _extract_embedded_report(raw_xml)
    if embedded is not None:
        warnings.append("extracted the <report> definition from a wrapper "
                        "document (e.g. a .jsp Reports Web Source)")
        raw_xml = embedded
        xml_bytes = embedded.encode("utf-8")

    # Use a tolerant parser; recover=True keeps going past malformed bits.
    parser = etree.XMLParser(recover=True, huge_tree=True, resolve_entities=False)
    try:
        root = etree.fromstring(xml_bytes, parser=parser)
    except etree.XMLSyntaxError:
        # Non-UTF-8 bytes (Oracle Reports frequently exported ISO-8859-1 /
        # Windows-1252) fail the byte-level parse even with recover=True.
        # _decode() already produced correct text via an encoding-fallback
        # chain -- retry on that, re-encoded as UTF-8, before giving up.
        try:
            root = etree.fromstring(raw_xml.encode("utf-8"), parser=parser)
        except etree.XMLSyntaxError as exc:
            return ParsedReport(
                name="",
                raw_xml=raw_xml,
                warnings=[f"XML parse failure: {exc}"],
            )
    if root is None:
        return ParsedReport(
            name="",
            raw_xml=raw_xml,
            warnings=["XML parse returned no root element"],
        )

    # Top-level metadata
    name = _attr(root, "name")
    dtd_version = _attr(root, "DTDVersion")

    # Sections
    data_el = _find(root, "data")

    triggers, program_units_index = _parse_program_units(root, warnings)
    parameters = _parse_parameters(data_el, warnings)
    queries = _parse_queries(data_el, warnings)
    formulas = _parse_formulas(data_el, program_units_index, warnings)

    embedded_images = []
    layout = _parse_layout(root, warnings, embedded_images)

    # Document-level image payloads. Oracle's XML export has TWO styles:
    # <binaryData> nested inside the <image> element (handled during the
    # layout walk above) and document-level <binaryData dataId="image.X">
    # blocks that the <image name="X"> elements reference by name. Collect
    # the latter so state seals / logos embedded in the export render in
    # both the RDL (<EmbeddedImages>) and the HTML mockup with zero manual
    # uploads. Purely structural -- keyed on the dataId convention.
    have_ids = {im.id.upper() for im in embedded_images}
    for bd in _iter_descendants(root, "binaryData"):
        data_id = _attr(bd, "dataId")
        raw_hex = re.sub(r"\s+", "", bd.text or "")
        if not data_id or not raw_hex:
            continue
        ref = data_id.split(".", 1)[1] if "." in data_id else data_id
        safe_id = _safe_id(ref)
        if not safe_id or safe_id.upper() in have_ids:
            continue
        raw_hex = _normalize_image_hex(raw_hex)
        declared_mime = _attr(bd, "format")
        try:
            mime = declared_mime or _guess_image_mime(raw_hex)
        except Exception:  # noqa: BLE001
            mime = declared_mime or "image/gif"
        embedded_images.append(
            EmbeddedImage(id=safe_id, mime_type=mime, hex_data=raw_hex))
        have_ids.add(safe_id.upper())

    # Charts / graphs. Oracle stores these as <graph>/<chart>/<rw:graph>
    # (paper layout) or <rw:graph src=.. series=.. dataValues=..> (web).
    # We don't auto-build an SSRS Chart (a different model), but a chart
    # must NEVER be silently dropped -- capture it so the user is told to
    # recreate it. Generic: keyed on the standard element/attribute names.
    charts = []
    _CHART_TAGS = ("graph", "chart", "graphobject", "chartobject")
    for el in root.iter():
        ln = _localname(el).lower()
        if ln not in _CHART_TAGS:
            continue
        # A chart object nests an inner config element (Oracle's <Graph>
        # inside <graph>); count the OUTERMOST object once -- skip any
        # graph/chart whose ancestor is also a graph/chart.
        anc = el.getparent()
        nested = False
        while anc is not None:
            if _localname(anc).lower() in _CHART_TAGS:
                nested = True
                break
            anc = anc.getparent()
        if nested:
            continue
        title = ""
        for sub in el.iter():
            if _localname(sub).lower() == "title":
                title = _attr(sub, "text") or (sub.text or "").strip()
                if title:
                    break
        charts.append({
            "title": title,
            "category": _attr(el, "series") or _attr(el, "src") or "",
            "plot_value": _attr(el, "dataValues") or _attr(el, "value") or "",
            "type": _attr(el, "graphType") or _attr(el, "chartType")
                    or _attr(el, "type") or "chart",
        })
    if charts:
        warnings.append(
            f"{len(charts)} chart/graph object(s) detected -- not auto-built "
            "(recreate as an SSRS Chart in Report Builder)")

    return ParsedReport(
        name=name,
        dtd_version=dtd_version,
        parameters=parameters,
        queries=queries,
        formulas=formulas,
        layout=layout,
        triggers=triggers,
        embedded_images=embedded_images,
        charts=charts,
        raw_xml=raw_xml,
        warnings=warnings,
    )

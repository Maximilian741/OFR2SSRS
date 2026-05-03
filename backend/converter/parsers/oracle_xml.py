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

from typing import List, Optional

from lxml import etree

from converter.models import (
    DataItem,
    DataQuery,
    FormulaColumn,
    LayoutField,
    LayoutGroup,
    ParsedReport,
    ReportParameter,
    TriggerCode,
)


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
        return float(val)
    except (TypeError, ValueError):
        return default


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
            items.append(
                DataItem(
                    name=_attr(di, "name"),
                    expression=expression,
                    datatype=datatype,
                    width=_int_attr(di, "width"),
                    label=_attr(di, "defaultLabel"),
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
            top_group = _find(ds, "group")
            items = _collect_data_items_recursive(top_group, warnings)
            queries.append(
                DataQuery(
                    name=_attr(ds, "name"),
                    sql=sql,
                    items=items,
                )
            )
        except Exception as exc:  # pragma: no cover - defensive
            warnings.append(f"failed to parse dataSource: {exc}")
    return queries


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

def _layout_field_from_element(el) -> LayoutField:
    """Build a LayoutField from a <field> or <text> element."""
    name = _attr(el, "name")
    source = _attr(el, "source")
    geom = _find(el, "geometryInfo")
    x = _float_attr(geom, "x") if geom is not None else 0.0
    y = _float_attr(geom, "y") if geom is not None else 0.0
    width = _float_attr(geom, "width") if geom is not None else 0.0
    height = _float_attr(geom, "height") if geom is not None else 0.0

    # If <text>, pull boilerplate text from textSegment/string CDATA
    text_value = source
    bold = False
    font_size = 10
    if _localname(el) == "text":
        segs: List[str] = []
        for ts in _iter_descendants(el, "textSegment"):
            for s in _findall(ts, "string"):
                if s.text:
                    segs.append(s.text)
        text_value = "".join(segs).strip()
    # Pick up font info if present
    font = _find(el, "font")
    if font is not None:
        font_size = _int_attr(font, "size", 10) or 10
        bold = _attr(font, "bold").lower() == "yes"

    return LayoutField(
        name=name,
        source=source,
        text=text_value or source,
        bold=bold,
        font_size=font_size,
        x=x,
        y=y,
        width=width,
        height=height,
    )


def _walk_layout_node(node, current_group: Optional[LayoutGroup],
                     groups_by_name: dict, root_groups: List[LayoutGroup],
                     warnings: List[str]) -> None:
    """Walk the layout subtree, attaching fields to the most specific group.

    Whenever we encounter a <repeatingFrame>, we open a new LayoutGroup keyed
    by its @source (the data group / query name). Fields/texts inside go into
    that group. <field> / <text> outside any repeatingFrame go into a default
    group keyed by the section name passed via current_group.
    """
    for child in node:
        tag = _localname(child)
        if tag == "repeatingFrame":
            rf_name = _attr(child, "name")
            rf_source = _attr(child, "source")
            # Group key uses the data source name when available
            key = rf_source or rf_name
            grp = groups_by_name.get(key)
            if grp is None:
                grp = LayoutGroup(name=rf_name or rf_source or "group",
                                  source_query=rf_source)
                groups_by_name[key] = grp
                if current_group is None:
                    root_groups.append(grp)
                else:
                    current_group.children.append(grp)
            _walk_layout_node(child, grp, groups_by_name, root_groups, warnings)
        elif tag in ("field", "text"):
            try:
                lf = _layout_field_from_element(child)
                target = current_group
                if target is None:
                    # Fall back to a synthetic group named after the section
                    key = "_default"
                    target = groups_by_name.get(key)
                    if target is None:
                        target = LayoutGroup(name="_default", source_query="")
                        groups_by_name[key] = target
                        root_groups.append(target)
                target.fields.append(lf)
            except Exception as exc:  # pragma: no cover - defensive
                warnings.append(f"failed to parse layout field: {exc}")
            # Fields can themselves contain nested elements (rare); keep walking
            _walk_layout_node(child, current_group, groups_by_name, root_groups, warnings)
        elif tag == "image":
            # Treat images as fields with empty source so they show up in mockups
            try:
                lf = _layout_field_from_element(child)
                if current_group is not None:
                    current_group.fields.append(lf)
            except Exception:
                pass
            _walk_layout_node(child, current_group, groups_by_name, root_groups, warnings)
        else:
            # Recurse through containers (section, body, frame, etc.)
            _walk_layout_node(child, current_group, groups_by_name, root_groups, warnings)


def _parse_layout(root, warnings: List[str]) -> List[LayoutGroup]:
    layout_el = _find(root, "layout")
    if layout_el is None:
        return []
    root_groups: List[LayoutGroup] = []
    groups_by_name: dict = {}
    for section in _findall(layout_el, "section"):
        section_name = _attr(section, "name") or "section"
        repeat_on = _attr(section, "repeatOn")
        # Open a synthetic top-level group for each section so loose
        # boilerplate has a home.
        section_group = LayoutGroup(
            name=f"section_{section_name}",
            source_query=repeat_on,
        )
        groups_by_name[f"__section__{section_name}"] = section_group
        root_groups.append(section_group)
        _walk_layout_node(section, section_group, groups_by_name,
                          root_groups, warnings)
    # Drop empty synthetic groups that got nothing attached
    pruned: List[LayoutGroup] = []
    for g in root_groups:
        if g.fields or g.children:
            pruned.append(g)
    return pruned or root_groups


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------

def parse_oracle_xml(xml_bytes: bytes) -> ParsedReport:
    """Parse an Oracle Reports XML byte string into a ParsedReport."""
    warnings: List[str] = []
    raw_xml = _decode(xml_bytes)

    # Use a tolerant parser; recover=True keeps going past malformed bits.
    parser = etree.XMLParser(recover=True, huge_tree=True, resolve_entities=False)
    try:
        root = etree.fromstring(xml_bytes, parser=parser)
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
    layout = _parse_layout(root, warnings)

    return ParsedReport(
        name=name,
        dtd_version=dtd_version,
        parameters=parameters,
        queries=queries,
        formulas=formulas,
        layout=layout,
        triggers=triggers,
        raw_xml=raw_xml,
        warnings=warnings,
    )

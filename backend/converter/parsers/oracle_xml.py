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
    EmbeddedImage,
    FormulaColumn,
    LayoutField,
    LayoutGroup,
    ParsedReport,
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
            # Oracle Reports lets a single <dataSource> hold multiple sibling
            # <group> elements (e.g. G_Site + G_VISIT under Q_VISIT). Walk
            # ALL of them — not just the first — and harvest dataItems
            # recursively from each. De-dup by item name to avoid double-counting
            # if Oracle ever emits the same item under two groups.
            items: List[DataItem] = []
            seen: set[str] = set()
            for top_group in _findall(ds, "group"):
                for di in _collect_data_items_recursive(top_group, warnings):
                    if di.name and di.name in seen:
                        continue
                    if di.name:
                        seen.add(di.name)
                    items.append(di)
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

def _apply_geometry(target, el) -> None:
    geom = _find(el, "geometryInfo")
    if geom is None:
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
    x = _float_attr(geom, "x") if geom is not None else 0.0
    y = _float_attr(geom, "y") if geom is not None else 0.0
    width = _float_attr(geom, "width") if geom is not None else 0.0
    height = _float_attr(geom, "height") if geom is not None else 0.0

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
    )


def _walk_layout_node(node, current_group: Optional[LayoutGroup],
                     groups_by_name: dict, root_groups: List[LayoutGroup],
                     warnings: List[str],
                     embedded_images: List[EmbeddedImage]) -> None:
    """Walk the layout subtree, attaching fields to the most specific group."""
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
                x = _float_attr(geom, "x") if geom is not None else 0.0
                y = _float_attr(geom, "y") if geom is not None else 0.0
                width = _float_attr(geom, "width") if geom is not None else 0.0
                height = _float_attr(geom, "height") if geom is not None else 0.0
                vs_attrs = _parse_visual_settings(child)
                bin_data = _find(child, "binaryData")
                image_id = ""
                if bin_data is not None and (bin_data.text or "").strip():
                    raw_hex = (bin_data.text or "").strip()
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
    for section in _findall(layout_el, "section"):
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

    embedded_images = []
    layout = _parse_layout(root, warnings, embedded_images)

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

"""
Post-processing helpers for the generated RDL. Provides:

  set_datasource_reference(rdl_xml, path)        -> rdl_xml
  inject_connection_string(rdl_xml, conn_str)    -> rdl_xml

The generator emits every DataSource as a SHARED reference:

    <DataSource Name="SharedDataSource">
      <DataSourceReference>SharedDataSource</DataSourceReference>
      ...
    </DataSource>

``set_datasource_reference`` rewrites the reference target to the caller's
real shared-data-source path on their report server (e.g. ``/Data
Sources/Oracle_Prod`` or a sibling name like ``BETA``). When the path
matches an existing shared data source, SSRS binds it AT UPLOAD — the
user never has to open Data Source Properties and repoint manually.

``inject_connection_string`` switches the data source to an EMBEDDED
connection instead (``<ConnectionProperties>``). NOTE: the shared-reference
form is the recommended one — embedded connections make SSRS evaluate
query parameters at design time, which is the path that historically
popped the "Define Query Parameters" prompt. Embedded stays available
for servers without a shared data source.

Pure string substitution. No logging, no caching, no inspection: secrets
stay in request memory only.
"""
from __future__ import annotations

import html
import re


_DEFAULT_CONN_STR_RE = re.compile(
    r"(<ConnectString>)([^<]*)(</ConnectString>)",
    flags=re.IGNORECASE,
)

_DS_REFERENCE_RE = re.compile(
    r"(<DataSourceReference>)([^<]*)(</DataSourceReference>)",
    flags=re.IGNORECASE,
)


def set_datasource_reference(rdl_xml: str, path: str) -> str:
    """Point every <DataSourceReference> at the caller's shared data source.

    ``path`` is the shared data source's location on the report server:
    either an absolute folder path (``/Data Sources/Oracle_Prod``) or a
    bare name resolved relative to the report's folder (``Oracle_Prod``).
    When it matches an existing shared data source, the upload binds
    automatically — zero manual repointing.

    Returns rdl_xml unchanged when path is empty. Replaces ALL
    occurrences so multi-datasource RDLs (and subreport stubs) are
    covered.
    """
    if not (path or "").strip():
        return rdl_xml
    safe = html.escape(path.strip(), quote=False)
    return _DS_REFERENCE_RE.sub(rf"\g<1>{safe}\g<3>", rdl_xml)


def inject_connection_string(rdl_xml: str, conn_str: str,
                             provider: str = "ORACLE") -> str:
    """Embed a connection string into the RDL's data source.

    Two shapes are handled:
      * RDL already has ``<ConnectString>`` (legacy embedded form):
        the value is swapped in place.
      * RDL uses the generated ``<DataSourceReference>`` form: the
        reference element is REPLACED by ``<ConnectionProperties>``
        carrying the provider + connection string, converting the data
        source from shared to embedded.

    Returns rdl_xml unchanged if conn_str is empty/None. XML-escapes the
    value so quotes/ampersands don't break the document. The original
    string is NEVER logged or stored.
    """
    if not conn_str:
        return rdl_xml
    safe = html.escape(conn_str.strip(), quote=False)
    if _DEFAULT_CONN_STR_RE.search(rdl_xml):
        return _DEFAULT_CONN_STR_RE.sub(rf"\g<1>{safe}\g<3>", rdl_xml, count=1)
    prov = html.escape((provider or "ORACLE").strip(), quote=False) or "ORACLE"
    replacement = (
        "<ConnectionProperties>"
        f"<DataProvider>{prov}</DataProvider>"
        f"<ConnectString>{safe}</ConnectString>"
        "</ConnectionProperties>"
    )
    return _DS_REFERENCE_RE.sub(replacement, rdl_xml)


_DRILLTHROUGH_RE = re.compile(r"<Drillthrough\b.*?</Drillthrough>",
                              flags=re.IGNORECASE | re.DOTALL)
_REPORTNAME_RE = re.compile(r"<ReportName>([^<]*)</ReportName>", flags=re.IGNORECASE)
_PARAM_RE = re.compile(
    r'<Parameter\s+Name="([^"]+)"\s*>\s*<Value>(.*?)</Value>\s*</Parameter>',
    flags=re.IGNORECASE | re.DOTALL)


def _is_aggregate_expr(expr: str) -> bool:
    """True when a drill-through value is a DATASET aggregate (First/Sum/...),
    i.e. NOT a per-record value. Such a link is a "generate ALL / show all"
    surface (the cover "Generate Envelopes" line), not a per-record link, so it
    must open the child UNFILTERED rather than filtering to one arbitrary row."""
    return bool(re.search(r"\b(?:First|Last|Sum|Min|Max|Avg|Count)\s*\(",
                          expr or "", re.IGNORECASE))


def relax_generate_all_drillthroughs(rdl_xml: str) -> str:
    """A <Drillthrough> whose EVERY parameter value is a dataset aggregate
    (all First()/Sum()/... -> a "generate all" cover link, no per-record key)
    has its <Parameters> stripped, so it opens the child report UNFILTERED
    instead of filtering to the aggregate's single row (which now -- once the
    child filters on the forwarded param -- shows one wrong record or a blank).
    A per-record link (Fields!/Lookup() value) is left untouched. No-op when
    there are no such links. Generic; nothing report-specific."""
    if not rdl_xml or "<Drillthrough" not in rdl_xml:
        return rdl_xml

    def _repl(m):
        block = m.group(0)
        params = _PARAM_RE.findall(block)
        if params and all(_is_aggregate_expr(v) for _, v in params):
            return re.sub(r"<Parameters>.*?</Parameters>", "", block,
                          flags=re.IGNORECASE | re.DOTALL)
        return block

    return _DRILLTHROUGH_RE.sub(_repl, rdl_xml)


def set_drillthrough_hyperlinks(rdl_xml: str, server_url: str) -> str:
    """Rewrite every sub-report <Drillthrough> into a parameterized URL
    <Hyperlink> that pings the report server -- so the link works in BOTH the
    SSRS interactive viewer AND an exported PDF (a Drillthrough is interactive-
    only and dies in a static PDF). Returns rdl_xml unchanged when server_url is
    empty (the report keeps its Drillthrough actions for in-viewer use).

    ``server_url`` is the SSRS URL-access base, up to the folder, e.g.
    ``http://host/ReportServer?/MyFolder`` (or just ``http://host/ReportServer``
    for the root). Each drill-through becomes:

        =\"<base>/<ReportName>&rs:Command=Render\"
            & \"&P_ORG_ID=\" & CStr(<row expr>) & ...

    Per-record params (Fields!/Lookup() expressions) are appended so the link
    opens THAT record; aggregate (First()/...) params -- the generate-all cover
    link -- are omitted so the URL renders the child unfiltered. Generic: the
    report name + value expressions come straight from the existing drill-through
    the generator already resolved."""
    if not (server_url or "").strip() or not rdl_xml:
        return rdl_xml
    base = server_url.strip().rstrip("/")
    sep = "/" if "?" in base else "?/"

    def _repl(m):
        block = m.group(0)
        rn = _REPORTNAME_RE.search(block)
        report = (rn.group(1).strip() if rn else "")
        if not report:
            return block
        # Raw (un-escaped) VB.NET expression; XML-escaped once at the end.
        url_path = f"{base}{sep}{report}&rs:Command=Render"
        raw = f'="{url_path}"'
        for name, val in _PARAM_RE.findall(block):
            v = html.unescape(val).strip()
            if _is_aggregate_expr(v):
                continue  # generate-all link: no per-record key in the URL
            inner = v[1:] if v.startswith("=") else '"' + v.replace('"', '""') + '"'
            raw += f' & "&{name}=" & CStr({inner})'
        value = raw.replace("&", "&amp;").replace("<", "&lt;")
        return f"<Hyperlink>{value}</Hyperlink>"

    return _DRILLTHROUGH_RE.sub(_repl, rdl_xml)


__all__ = [
    "inject_connection_string",
    "set_datasource_reference",
    "relax_generate_all_drillthroughs",
    "set_drillthrough_hyperlinks",
]

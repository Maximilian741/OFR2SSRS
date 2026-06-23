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


def set_drillthrough_hyperlinks(rdl_xml: str, server_url: str | None = None) -> str:
    """Rewrite every sub-report <Drillthrough> into a parameterized URL
    <Hyperlink> that pings the report server -- so the link works in BOTH the
    SSRS interactive viewer AND an exported PDF (a Drillthrough is interactive-
    only and is silently DROPPED in a static PDF/Word/Excel export). This is the
    fix for "the links do nothing in the downloaded PDF": a <Hyperlink> action
    survives PDF export as a real clickable link annotation.

    URL source, in order of preference:

      * ``server_url`` given (e.g. ``http://host/ReportServer?/MyFolder``):
        a literal absolute base. Works on EVERY SSRS version. Recommended when
        the report server version is unknown or pre-2016.
      * ``server_url`` empty -> ZERO-CONFIG via SSRS built-in globals:
        ``Globals!ReportServerUrl`` (the running server's URL, 2008 R2+) and
        ``Globals!ReportFolder`` (this report's folder, 2016+). The child is
        assumed to live in the SAME folder as the parent (the documented deploy
        layout), so no manual URL is needed on a modern server.

    Each drill-through becomes (explicit-URL form shown):

        =\"<base>/<ReportName>&rs:Command=Render\"
            & \"&<ParamName>=\" & CStr(<row expr>) & ...

    Per-record params (Fields!/Lookup() expressions) are appended so the link
    opens THAT record; aggregate (First()/...) params -- the generate-all cover
    link -- are omitted so the URL renders the child unfiltered. Generic: the
    report name + value expressions come straight from the existing drill-through
    the generator already resolved. No-op when there are no drill-throughs."""
    if not rdl_xml or "<Drillthrough" not in rdl_xml:
        return rdl_xml
    explicit = (server_url or "").strip().rstrip("/")
    if explicit:
        sep = "/" if "?" in explicit else "?/"

    def _repl(m):
        block = m.group(0)
        rn = _REPORTNAME_RE.search(block)
        report = (rn.group(1).strip() if rn else "")
        if not report:
            return block
        # Raw (un-escaped) VB.NET expression; XML-escaped once at the end.
        if explicit:
            raw = f'="{explicit}{sep}{report}&rs:Command=Render"'
        else:
            # Zero-config: build the absolute URL from server globals at runtime.
            raw = ('=Globals!ReportServerUrl & "?" & Globals!ReportFolder '
                   f'& "/{report}&rs:Command=Render"')
        for name, val in _PARAM_RE.findall(block):
            v = html.unescape(val).strip()
            if _is_aggregate_expr(v):
                continue  # generate-all link: no per-record key in the URL
            inner = v[1:] if v.startswith("=") else '"' + v.replace('"', '""') + '"'
            raw += f' & "&{name}=" & CStr({inner})'
        value = raw.replace("&", "&amp;").replace("<", "&lt;")
        return f"<Hyperlink>{value}</Hyperlink>"

    return _DRILLTHROUGH_RE.sub(_repl, rdl_xml)


_SORT_PARAM_NAME_RE = re.compile(r"(?i)(^|_)sort$")
_REPORTPARAM_RE = re.compile(r'<ReportParameter Name="([^"]+)">.*?</ReportParameter>',
                             flags=re.DOTALL)
_COMMANDTEXT_RE = re.compile(r"<CommandText>(.*?)</CommandText>",
                            flags=re.DOTALL | re.IGNORECASE)


def align_drillthrough_sort_default(rdl_xml: str) -> str:
    """Make a master report's records come out in the SAME order as its
    sub-report's bulk "generate all" list -- with ZERO manual parameter setting.

    The envelope/sub-report child sorts by site (its site-include toggle now
    defaults YES). For the master's records to line up 1:1, the master must also
    sort by site. The master already has a SORT-selector parameter wired to a
    ``DECODE(:P_SORT, ... 'SITE', S.Site_Name ...)`` ORDER BY -- but it defaults to
    the neutral ``=Nothing`` (which falls to the DECODE's default branch, a
    DIFFERENT order). This defaults that selector to ``SITE`` so the master prints
    in site order out of the box.

    Generic + safe: only acts when (a) the report links to a sub-report
    (Drillthrough or an ``rs:Command=Render`` hyperlink), AND (b) it has a
    parameter whose prompt/name is a sort selector, AND (c) that parameter is
    DECODEd with a literal ``'SITE'`` branch in a dataset query. No-op otherwise;
    the parameter stays user-overridable (not hidden)."""
    if not rdl_xml:
        return rdl_xml
    if "<Drillthrough" not in rdl_xml and "rs:Command=Render" not in rdl_xml:
        return rdl_xml
    cmd_texts = _COMMANDTEXT_RE.findall(rdl_xml)

    def _site_decoded(param: str) -> bool:
        bind = re.compile(r":\s*" + re.escape(param) + r"\b", re.IGNORECASE)
        for ct in cmd_texts:
            if bind.search(ct) and re.search(r"DECODE\s*\(.*?'SITE'", ct,
                                             re.IGNORECASE | re.DOTALL):
                return True
        return False

    def _repl(m):
        block, name = m.group(0), m.group(1)
        pm = re.search(r"<Prompt>([^<]*)</Prompt>", block)
        prompt = (pm.group(1) if pm else "").strip().lower()
        is_sort = prompt in ("sort", "sort order") or _SORT_PARAM_NAME_RE.search(name)
        if (is_sort and "<Value>=Nothing</Value>" in block
                and _site_decoded(name)):
            return block.replace("<Value>=Nothing</Value>",
                                 "<Value>SITE</Value>", 1)
        return block

    return _REPORTPARAM_RE.sub(_repl, rdl_xml)


__all__ = [
    "inject_connection_string",
    "set_datasource_reference",
    "relax_generate_all_drillthroughs",
    "set_drillthrough_hyperlinks",
    "align_drillthrough_sort_default",
]

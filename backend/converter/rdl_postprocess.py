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


__all__ = ["inject_connection_string", "set_datasource_reference"]

"""
Post-processing helpers for the generated RDL. Currently provides:

  inject_connection_string(rdl_xml, conn_str) -> rdl_xml

Replaces the default <ConnectString>...</ConnectString> the generator emits
with the caller-supplied value. Pure string-substitution, no logging, no
caching, no inspection. The caller is responsible for keeping the secret
out of disk and out of the audit trail.
"""
from __future__ import annotations

import html
import re


_DEFAULT_CONN_STR_RE = re.compile(
    r"(<ConnectString>)([^<]*)(</ConnectString>)",
    flags=re.IGNORECASE,
)


def inject_connection_string(rdl_xml: str, conn_str: str) -> str:
    """Swap the embedded ConnectString for the caller-supplied value.

    Returns rdl_xml unchanged if conn_str is empty/None.
    XML-escapes the value so quotes/ampersands in the connection string
    don't break the document. The original string is NEVER logged.
    """
    if not conn_str:
        return rdl_xml
    safe = html.escape(conn_str.strip(), quote=False)
    return _DEFAULT_CONN_STR_RE.sub(rf"\g<1>{safe}\g<3>", rdl_xml, count=1)


__all__ = ["inject_connection_string"]

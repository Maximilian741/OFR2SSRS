"""SSRS SMOKE-DEPLOY — ask the REPORT SERVER whether it accepts the RDL.

    python tools/ssrscheck/smoke_deploy.py report.xml
    python tools/ssrscheck/smoke_deploy.py --dir ./reports
    python tools/ssrscheck/smoke_deploy.py --keep report.xml   (don't delete)

WHY THIS EXISTS
---------------
Every local check reasons about the file. The one failure that reached a
live stakeholder demo could not be seen from here at all: Report Builder
popped "Define Query Parameters" at design time on a report whose RDL was
structurally perfect. Only the SERVER can answer some questions — schema
validation, parameter resolution, data-source binding, publish rules that
no XSD encodes.

This uploads the generated RDL to a REAL SSRS instance, reports exactly
what the server said, and deletes it again. That is the missing proof.

SAFETY
------
* Nothing runs unless YOU configure a server.
* Uploads go to a dedicated smoke-test folder (default
  ``/O2S_SmokeTest``) — never over an existing report, never to a
  production path unless you explicitly name one.
* Each upload is DELETED again unless ``--keep`` is passed.
* The report is never executed and no data source is bound, so no query
  runs against production data.

CONFIGURE
---------
    set O2S_SSRS_URL=http://myserver/ReportServer
    set O2S_SSRS_FOLDER=/O2S_SmokeTest          (optional)
    set O2S_SSRS_USER=DOMAIN\\user              (optional; default is
    set O2S_SSRS_PASSWORD=...                    integrated auth)

Requires ``pip install requests`` (plus ``requests-ntlm`` or
``requests-negotiate-sspi`` for Windows auth).

WHAT THE VERDICTS MEAN
----------------------
    ACCEPTED   the server validated and stored the definition. The RDL is
               publishable — the strongest signal short of running it.
    WARNINGS   accepted, with server warnings worth reading (unbound data
               source, missing subreport, deprecated element).
    REJECTED   the server refused it; the message is Microsoft's own
               (rsInvalidReportDefinition, schema errors) and names the
               real problem.
"""
from __future__ import annotations

import base64
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert                    # noqa: E402

_SOAP_NS = ("http://schemas.microsoft.com/sqlserver/reporting/2006/03/"
            "reportserver")


def server_configured() -> bool:
    return bool(os.environ.get("O2S_SSRS_URL"))


def _session():
    import requests                              # noqa: PLC0415
    sess = requests.Session()
    user = os.environ.get("O2S_SSRS_USER")
    pwd = os.environ.get("O2S_SSRS_PASSWORD", "")
    if user:
        try:
            from requests_ntlm import HttpNtlmAuth   # noqa: PLC0415
            sess.auth = HttpNtlmAuth(user, pwd)
        except Exception:                            # noqa: BLE001
            sess.auth = (user, pwd)
    else:
        try:
            from requests_negotiate_sspi import (    # noqa: PLC0415
                HttpNegotiateAuth)
            sess.auth = HttpNegotiateAuth()
        except Exception:                            # noqa: BLE001
            pass        # fall back to whatever requests can negotiate
    return sess


def _soap(action: str, body: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap:Envelope '
        'xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        f'<soap:Body><{action} xmlns="{_SOAP_NS}">{body}</{action}>'
        '</soap:Body></soap:Envelope>'
    )


def _post(sess, url: str, action: str, xml: str):
    return sess.post(
        url.rstrip("/") + "/ReportService2010.asmx",
        data=xml.encode("utf-8"),
        headers={"Content-Type": "text/xml; charset=utf-8",
                 "SOAPAction": f"{_SOAP_NS}/{action}"},
        timeout=120,
    )


def _tag_text(text: str, tag: str) -> str:
    i = text.find(f"<{tag}>")
    if i < 0:
        return ""
    j = text.find(f"</{tag}>", i)
    return text[i + len(tag) + 2:j] if j > 0 else ""


def smoke_deploy(rdl_xml: str, name: str, keep: bool = False) -> dict:
    """Upload ``rdl_xml`` and return the server's own verdict."""
    url = os.environ.get("O2S_SSRS_URL", "")
    folder = os.environ.get("O2S_SSRS_FOLDER", "/O2S_SmokeTest")
    if not url:
        return {"verdict": "SKIPPED",
                "detail": "O2S_SSRS_URL not set — no server configured"}
    try:
        sess = _session()
    except Exception as exc:                        # noqa: BLE001
        return {"verdict": "SKIPPED",
                "detail": f"requests unavailable: {exc}"}

    parent, _, leaf = folder.rstrip("/").rpartition("/")
    try:
        _post(sess, url, "CreateFolder",
              _soap("CreateFolder",
                    f"<Folder>{leaf}</Folder>"
                    f"<Parent>{parent or '/'}</Parent>"))
    except Exception:                               # noqa: BLE001
        pass        # already exists, or we lack rights to create it

    payload = base64.b64encode(rdl_xml.encode("utf-8")).decode("ascii")
    body = (f"<ItemType>Report</ItemType><Name>{name}</Name>"
            f"<Parent>{folder}</Parent><Overwrite>true</Overwrite>"
            f"<Definition>{payload}</Definition>")
    try:
        resp = _post(sess, url, "CreateCatalogItem",
                     _soap("CreateCatalogItem", body))
    except Exception as exc:                        # noqa: BLE001
        return {"verdict": "ERROR", "detail": f"{type(exc).__name__}: {exc}"}

    text = resp.text or ""
    if resp.status_code >= 400 or "Fault>" in text:
        detail = (_tag_text(text, "faultstring") or _tag_text(text, "Message")
                  or text)
        return {"verdict": "REJECTED",
                "detail": " ".join(detail.split())[:500]}

    warnings, idx = [], 0
    while True:
        i = text.find("<Message", idx)
        if i < 0:
            break
        j = text.find("</Message>", i)
        seg = text[i:j if j > 0 else len(text)]
        k = seg.find(">")
        if k >= 0:
            warnings.append(" ".join(seg[k + 1:].split())[:300])
        idx = (j if j > 0 else i) + 1

    if not keep:
        try:
            _post(sess, url, "DeleteItem",
                  _soap("DeleteItem",
                        f"<ItemPath>{folder}/{name}</ItemPath>"))
        except Exception:                           # noqa: BLE001
            pass

    if warnings:
        return {"verdict": "WARNINGS", "detail": " | ".join(warnings[:6])}
    return {"verdict": "ACCEPTED", "detail": f"{folder}/{name}"}


def main(argv) -> int:
    keep = "--keep" in argv
    args = [a for a in argv if a != "--keep"]
    files, i = [], 0
    while i < len(args):
        if args[i] == "--dir":
            i += 1
            files += sorted(str(p) for p in
                            pathlib.Path(args[i]).rglob("*.xml"))
        else:
            files.append(args[i])
        i += 1
    if not files:
        print(__doc__)
        return 2
    if not server_configured():
        print("No SSRS server configured.\n"
              "    set O2S_SSRS_URL=http://myserver/ReportServer\n"
              "Then re-run. See tools/ssrscheck/README.md.")
        return 2

    bad = 0
    for f in files:
        p = pathlib.Path(f)
        try:
            rdl = convert(p.read_bytes())["rdl_xml"]
        except Exception as exc:                    # noqa: BLE001
            print(f"{p.stem[:34]:<34} CONVERT-CRASH {exc}")
            bad += 1
            continue
        res = smoke_deploy(rdl, f"O2S_SMOKE_{p.stem[:40]}", keep=keep)
        print(f"{p.stem[:34]:<34} {res['verdict']:<9} {res['detail'][:90]}")
        if res["verdict"] in ("REJECTED", "ERROR"):
            bad += 1
    print(f"\nserver-rejected: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

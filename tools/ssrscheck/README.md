# ssrscheck — let the report server be the judge

Every other check in this repo reasons about the **file**. The failure
that reached a live stakeholder demo could not be seen from here at all:
Report Builder popped *"Define Query Parameters"* on an RDL that was
structurally perfect by every local measure.

Some questions only the **server** can answer — schema validation,
parameter resolution, data-source binding, and publish rules that no XSD
encodes. This uploads the generated RDL to a real SSRS instance, reports
exactly what the server said, and deletes it again.

## Configure

```bash
set O2S_SSRS_URL=http://myserver/ReportServer
```

Optional:

```bash
set O2S_SSRS_FOLDER=/O2S_SmokeTest
set O2S_SSRS_USER=DOMAIN\myuser
set O2S_SSRS_PASSWORD=...
```

Use the **ReportServer** URL (the SOAP endpoint), not the `/Reports`
portal URL. Install the client:

```bash
pip install requests requests-negotiate-sspi
```

`requests-negotiate-sspi` gives you integrated Windows auth; use
`requests-ntlm` with `O2S_SSRS_USER` instead if you need explicit
credentials.

## Run

```bash
python tools/ssrscheck/smoke_deploy.py report.xml
```

```bash
python tools/ssrscheck/smoke_deploy.py --dir ./my_reports
```

Exit code `0` means the server accepted every report.

## Verdicts

| Verdict | Meaning |
|---|---|
| `ACCEPTED` | The server validated and stored the definition. Publishable — the strongest signal short of running it. |
| `WARNINGS` | Accepted, with server warnings worth reading (unbound data source, missing subreport). |
| `REJECTED` | The server refused it. The message is Microsoft's own and names the real problem. |
| `SKIPPED` | No server configured, or the HTTP client is missing. |

## Safety

- Nothing happens unless you configure a server.
- Uploads land in a dedicated smoke-test folder (default
  `/O2S_SmokeTest`), never over an existing report.
- Every upload is **deleted again** unless you pass `--keep`.
- The report is never executed and no data source is bound, so no query
  runs against production data.

Point this at a **test** report server first. A read-only look at what
the server says costs nothing; a surprise in production costs a demo.

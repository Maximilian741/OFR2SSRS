# RDL 2008/01 schema (for validation only)

`ReportDefinition_2008.xsd` is Microsoft's **Report Definition Language** schema
for the `2008/01` namespace:

    http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition

It is published by Microsoft for exactly this purpose — validating `.rdl`
files — and is served at the canonical URL
`https://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition/ReportDefinition.xsd`.
This copy was obtained from the public mirror
`rjankovic/DLS` (`CD.BIDoc.Core.Parse.Mssql/Ssrs/Rdl_200801.xsd`); it is a
self-contained single file (no `<xsd:import>`/`<xsd:include>`).

It is used **only** by `tests/test_rdl_schema_xsd.py` to assert that every RDL
the converter emits is genuinely schema-valid — the definitive "it will upload
to SSRS" gate, stronger than the structural preflight.

If you would rather not redistribute Microsoft's schema in this repo, delete
this folder and point the test at your own copy via the `O2S_RDL_XSD`
environment variable (the test skips cleanly when neither is present).

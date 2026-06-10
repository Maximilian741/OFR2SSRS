"""Embedded images (seals / logos / watermarks): Oracle binaryData parsing
(both export styles + the nibble-swapped hex quirk), user uploads, RDL
<EmbeddedImages> emission, and the mockup data-URI preview.
"""
from __future__ import annotations

import base64
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.parsers.oracle_xml import (  # noqa: E402
    parse_oracle_xml, _normalize_image_hex)

# A real, valid 1x1 GIF.
_GIF = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!"
        b"\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00"
        b"\x00\x02\x02D\x01\x00;")
_GIF_HEX = _GIF.hex()
# The Oracle quirk: each byte's nibbles swapped.
_GIF_HEX_SWAPPED = "".join(
    _GIF_HEX[i + 1] + _GIF_HEX[i] for i in range(0, len(_GIF_HEX), 2))


def _xml(binary_block: str) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<report name="SAMPLE_IMG" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_MAIN">
      <select><![CDATA[SELECT T.Title FROM Things T]]></select>
      <group name="G_MAIN">
        <dataItem name="Title" datatype="vchar2"/>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="main">
    <body width="8.0" height="10.0">
      <image name="B_LOGO" x="1.0" y="1.0" width="2.0" height="2.0"/>
      <field name="F_TITLE" x="0.5" y="4.0" width="7.0" height="0.4"
             source="Title"/>
    </body>
  </section>
  </layout>
  {binary_block}
</report>
""".encode()


def test_normalize_image_hex_detects_nibble_swap():
    assert _normalize_image_hex(_GIF_HEX) == _GIF_HEX
    assert _normalize_image_hex(_GIF_HEX_SWAPPED) == _GIF_HEX
    # garbage stays untouched (fails open)
    assert _normalize_image_hex("zz") == "zz"


def test_document_level_binary_data_flows_to_rdl_and_mockup():
    xml = _xml(f'<binaryData encoding="hexidecimal" dataId="image.B_LOGO">'
               f"{_GIF_HEX}</binaryData>")
    out = convert(xml)
    rdl = out["rdl_xml"]
    assert '<EmbeddedImage Name="B_LOGO">' in rdl
    assert "<ImageData>" in rdl
    assert base64.b64encode(_GIF).decode("ascii") in rdl
    assert "<Source>Embedded</Source>" in rdl
    slots = out["image_slots"]
    assert slots and slots[0]["name"] == "B_LOGO" and slots[0]["has_data"]
    assert "data:image/" in out["mockup_html"]


def test_swapped_hex_export_style_also_decodes():
    xml = _xml(f'<binaryData encoding="hexidecimal" dataId="image.B_LOGO">'
               f"{_GIF_HEX_SWAPPED}</binaryData>")
    rep = parse_oracle_xml(xml)
    assert rep.embedded_images, "swapped-hex image not collected"
    assert bytes.fromhex(rep.embedded_images[0].hex_data)[:4] == b"GIF8"


def test_user_upload_fills_empty_slot_and_wildcard():
    xml = _xml("")  # no binaryData in the export at all
    out_plain = convert(xml)
    assert out_plain["image_slots"][0]["has_data"] is False
    b64 = base64.b64encode(_GIF).decode("ascii")
    # exact slot
    out = convert(xml, images={"B_LOGO": ("image/gif", b64)})
    assert out["image_slots"][0]["has_data"] is True
    assert '<EmbeddedImage Name="B_LOGO">' in out["rdl_xml"]
    # wildcard slot
    out_w = convert(xml, images={"*": ("image/gif", b64)})
    assert out_w["image_slots"][0]["has_data"] is True
    assert "<Source>Embedded</Source>" in out_w["rdl_xml"]


def test_rdl_with_embedded_image_passes_real_xsd():
    xsd = ROOT / "tests" / "fixtures" / "schema" / "ReportDefinition_2008.xsd"
    if not xsd.exists():
        pytest.skip("XSD not bundled")
    etree = pytest.importorskip("lxml.etree")
    xml = _xml(f'<binaryData encoding="hexidecimal" dataId="image.B_LOGO">'
               f"{_GIF_HEX}</binaryData>")
    rdl = convert(xml)["rdl_xml"]
    schema = etree.XMLSchema(etree.parse(str(xsd)))
    doc = etree.fromstring(rdl.encode("utf-8"))
    assert schema.validate(doc), "\n".join(
        e.message for e in schema.error_log[:5])


def test_image_upload_endpoint_reconverts_in_place():
    from app import app
    app.config["TESTING"] = True
    import io as _io
    with app.test_client() as client:
        xml = _xml("")
        r = client.post("/api/convert",
                        data={"file": (_io.BytesIO(xml), "img.xml")},
                        content_type="multipart/form-data")
        assert r.status_code == 200
        assert r.get_json()["image_slots"][0]["has_data"] is False
        up = client.post("/api/report-images/upload",
                         data={"slot": "B_LOGO",
                               "image": (_io.BytesIO(_GIF), "seal.gif")},
                         content_type="multipart/form-data")
        assert up.status_code == 200, up.get_data(as_text=True)[:300]
        j = up.get_json()
        assert j.get("image_slots") and j["image_slots"][0]["has_data"] is True
        assert '<EmbeddedImage Name="B_LOGO">' in j["rdl_xml"]
        # The download endpoint streams the image-bearing RDL.
        d = client.get("/api/download/rdl")
        assert "<EmbeddedImage" in d.get_data(as_text=True)

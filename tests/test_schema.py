from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from local_gaze.ipc import schema

_REPO = Path(__file__).resolve().parents[1]

_EXPECTED_METHODS = {
    "Ping",
    "GetStatus",
    "GetWindows",
    "SetEnabled",
    "SwitchWorkspace",
    "FocusWindowAt",
    "ShowCalibrationTarget",
    "HideOverlay",
    "ShowStatus",
}


def _iface() -> ET.Element:
    root = ET.fromstring(schema.INTROSPECTION_XML)
    iface = root.find(f"./interface[@name='{schema.INTERFACE}']")
    assert iface is not None
    return iface


def test_constants() -> None:
    assert schema.BUS_NAME == "com.eturkes.LocalGaze"
    assert schema.OBJECT_PATH == "/com/eturkes/LocalGaze"
    assert schema.INTERFACE == "com.eturkes.LocalGaze"


def test_xml_parses() -> None:
    # Must be valid XML and contain exactly one matching interface node.
    assert _iface().attrib["name"] == schema.INTERFACE


def test_method_set_matches() -> None:
    assert schema.method_names() == _EXPECTED_METHODS


def test_every_method_last_in_arg_is_token_s() -> None:
    iface = _iface()
    for m in iface.findall("./method"):
        in_args = [a for a in m.findall("./arg") if a.attrib.get("direction") == "in"]
        assert in_args, f"{m.attrib['name']} has no in-args"
        last = in_args[-1]
        assert last.attrib["name"] == "token", m.attrib["name"]
        assert last.attrib["type"] == "s", m.attrib["name"]


def test_js_iface_byte_identical() -> None:
    # The extension embeds the same contract; the two copies must not drift.
    src = (_REPO / "extension" / "lib" / "service.js").read_text(encoding="utf-8")
    m = re.search(r"IFACE\s*=\s*`(.*?)`", src, re.DOTALL)
    assert m, "IFACE template literal not found in service.js"
    assert m.group(1) == schema.INTROSPECTION_XML


def test_properties_and_signal_present() -> None:
    iface = _iface()
    props = {p.attrib["name"]: p.attrib for p in iface.findall("./property")}
    assert set(props) == {"Enabled", "Supported", "Version"}
    assert all(p["access"] == "read" for p in props.values())
    signals = {s.attrib["name"] for s in iface.findall("./signal")}
    assert signals == {"EnabledChanged"}

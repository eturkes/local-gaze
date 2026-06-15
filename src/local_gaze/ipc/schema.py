from __future__ import annotations

import xml.etree.ElementTree as ET

BUS_NAME = "com.eturkes.LocalGaze"
OBJECT_PATH = "/com/eturkes/LocalGaze"
INTERFACE = "com.eturkes.LocalGaze"

# Canonical interface contract (build-spec §2). Byte-identical to the copy embedded in
# extension/lib/service.js; tests/test_schema.py asserts this copy parses and matches the
# method set. Keep in sync with the extension by hand if the contract ever changes.
INTROSPECTION_XML = """<node>
  <interface name="com.eturkes.LocalGaze">
    <!-- liveness; returns "pong:<version>" -->
    <method name="Ping">
      <arg type="s" direction="in"  name="token"/>
      <arg type="s" direction="out" name="reply"/>
    </method>
    <!-- JSON status: {enabled,supported,version,session,n_workspaces,active_ws,n_monitors} -->
    <method name="GetStatus">
      <arg type="s" direction="in"  name="token"/>
      <arg type="s" direction="out" name="json"/>
    </method>
    <!-- JSON array of normal windows on active ws: [{id,title,wm_class,monitor,
         frame:{x,y,w,h}, nx, ny, focus}] ; coords are GLOBAL logical px + normalized -->
    <method name="GetWindows">
      <arg type="s" direction="in"  name="token"/>
      <arg type="s" direction="out" name="json"/>
    </method>
    <!-- enable/disable acting; mirrors gsetting 'active'; always honored -->
    <method name="SetEnabled">
      <arg type="b" direction="in"  name="enabled"/>
      <arg type="s" direction="in"  name="token"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <!-- relative workspace move; direction clamped to {-1,+1}; index-math wrap -->
    <method name="SwitchWorkspace">
      <arg type="i" direction="in"  name="direction"/>
      <arg type="s" direction="in"  name="token"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <!-- focus topmost normal window under normalized point (0..1, NaN/oob rejected) -->
    <method name="FocusWindowAt">
      <arg type="d" direction="in"  name="nx"/>
      <arg type="d" direction="in"  name="ny"/>
      <arg type="s" direction="in"  name="token"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <!-- show/hide a calibration dot at normalized point on its monitor -->
    <method name="ShowCalibrationTarget">
      <arg type="d" direction="in"  name="nx"/>
      <arg type="d" direction="in"  name="ny"/>
      <arg type="b" direction="in"  name="visible"/>
      <arg type="s" direction="in"  name="token"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <!-- tear down any overlay (calibration / debug) -->
    <method name="HideOverlay">
      <arg type="s" direction="in"  name="token"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <!-- transient OSD text; level is an advisory int (e.g. 0..2) -->
    <method name="ShowStatus">
      <arg type="s" direction="in"  name="text"/>
      <arg type="i" direction="in"  name="level"/>
      <arg type="s" direction="in"  name="token"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <!-- read-only props -->
    <property name="Enabled"   type="b" access="read"/>
    <property name="Supported" type="b" access="read"/>
    <property name="Version"   type="s" access="read"/>
    <!-- emitted when 'active' gsetting / Enabled changes (UI toggle, CLI, daemon) -->
    <signal name="EnabledChanged"><arg type="b" name="enabled"/></signal>
  </interface>
</node>"""


def method_names() -> set[str]:
    """Method names declared on the canonical interface, parsed from the XML."""
    root = ET.fromstring(INTROSPECTION_XML)
    iface = root.find(f"./interface[@name='{INTERFACE}']")
    if iface is None:
        return set()
    return {m.attrib["name"] for m in iface.findall("./method")}

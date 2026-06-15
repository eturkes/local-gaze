from __future__ import annotations

import json
import math

from dbus_fast import PropertyAccess
from dbus_fast.service import ServiceInterface, dbus_property, method, signal

from local_gaze import __version__
from local_gaze.ipc.schema import BUS_NAME, INTERFACE, OBJECT_PATH

# D-Bus type codes used as annotations below (s/b/i/d). `from __future__ import annotations`
# keeps them unevaluated, so they are plain strings dbus-fast reads for the signature; no
# real names `s/b/i/d` are referenced at runtime and ruff leaves them untouched.


class FakeExtension(ServiceInterface):
    """Real dbus-fast ServiceInterface mirroring the build-spec §2 contract.

    Enough behavior for the REAL ExtensionClient to round-trip: token is accepted
    (stored, never required), Enabled is mirrored via SetEnabled and gates the action
    methods loosely (SwitchWorkspace/FocusWindowAt return False while disabled), GetWindows
    returns a plausible JSON array, and EnabledChanged is emitted on every Enabled flip.
    Out-of-range FocusWindowAt points (NaN / outside [0,1]) are rejected with False.
    """

    bus_name = BUS_NAME
    path = OBJECT_PATH

    def __init__(self) -> None:
        super().__init__(INTERFACE)
        self._enabled = False
        self._supported = True
        self._n_workspaces = 4
        self._active_ws = 0
        self.last_token: str = ""
        self.calls: list[str] = []

    @method(name="Ping")
    def ping(self, token: s) -> s:  # type: ignore[name-defined]  # noqa: F821
        self.last_token = token
        self.calls.append("Ping")
        return f"pong:{__version__}"

    @method(name="GetStatus")
    def get_status(self, token: s) -> s:  # type: ignore[name-defined]  # noqa: F821
        self.last_token = token
        self.calls.append("GetStatus")
        return json.dumps(
            {
                "enabled": self._enabled,
                "supported": self._supported,
                "version": __version__,
                "session": "gnome-wayland",
                "n_workspaces": self._n_workspaces,
                "active_ws": self._active_ws,
                "n_monitors": 1,
            }
        )

    @method(name="GetWindows")
    def get_windows(self, token: s) -> s:  # type: ignore[name-defined]  # noqa: F821
        self.last_token = token
        self.calls.append("GetWindows")
        return json.dumps(
            [
                {
                    "id": 101,
                    "title": "Terminal",
                    "wm_class": "Console",
                    "monitor": 0,
                    "frame": {"x": 0, "y": 0, "w": 800, "h": 600},
                    "nx": 0.25,
                    "ny": 0.25,
                    "focus": True,
                },
                {
                    "id": 102,
                    "title": "Browser",
                    "wm_class": "Firefox",
                    "monitor": 0,
                    "frame": {"x": 800, "y": 0, "w": 800, "h": 600},
                    "nx": 0.75,
                    "ny": 0.25,
                    "focus": False,
                },
            ]
        )

    @method(name="SetEnabled")
    def set_enabled(self, enabled: b, token: s) -> b:  # type: ignore[name-defined]  # noqa: F821
        self.last_token = token
        self.calls.append("SetEnabled")
        changed = enabled != self._enabled
        self._enabled = enabled
        if changed:
            self.EnabledChanged(enabled)
        return True

    @method(name="SwitchWorkspace")
    def switch_workspace(self, direction: i, token: s) -> b:  # type: ignore[name-defined]  # noqa: F821
        self.last_token = token
        self.calls.append("SwitchWorkspace")
        if not self._enabled:
            return False
        d = -1 if direction < 0 else 1
        self._active_ws = (self._active_ws + d + self._n_workspaces) % self._n_workspaces
        return True

    @method(name="FocusWindowAt")
    def focus_window_at(self, nx: d, ny: d, token: s) -> b:  # type: ignore[name-defined]  # noqa: F821
        self.last_token = token
        self.calls.append("FocusWindowAt")
        if not self._enabled:
            return False
        if math.isnan(nx) or math.isnan(ny):
            return False
        return 0.0 <= nx <= 1.0 and 0.0 <= ny <= 1.0

    @method(name="ShowCalibrationTarget")
    def show_calibration_target(self, nx: d, ny: d, visible: b, token: s) -> b:  # type: ignore[name-defined]  # noqa: F821
        self.last_token = token
        self.calls.append("ShowCalibrationTarget")
        return True

    @method(name="HideOverlay")
    def hide_overlay(self, token: s) -> b:  # type: ignore[name-defined]  # noqa: F821
        self.last_token = token
        self.calls.append("HideOverlay")
        return True

    @method(name="ShowStatus")
    def show_status(self, text: s, level: i, token: s) -> b:  # type: ignore[name-defined]  # noqa: F821
        self.last_token = token
        self.calls.append("ShowStatus")
        return True

    @dbus_property(access=PropertyAccess.READ, name="Enabled")
    def enabled(self) -> b:  # type: ignore[name-defined]  # noqa: F821
        return self._enabled

    @dbus_property(access=PropertyAccess.READ, name="Supported")
    def supported(self) -> b:  # type: ignore[name-defined]  # noqa: F821
        return self._supported

    @dbus_property(access=PropertyAccess.READ, name="Version")
    def version(self) -> s:  # type: ignore[name-defined]  # noqa: F821
        return __version__

    @signal(name="EnabledChanged")
    def EnabledChanged(self, enabled: b) -> b:  # type: ignore[name-defined]  # noqa: F821,N802
        return enabled

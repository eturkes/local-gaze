from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SessionInfo:
    is_gnome: bool
    is_wayland: bool
    supported: bool
    detail: str


def _loginctl_self() -> dict[str, str]:
    """Best-effort `loginctl show-session self` parse; never raises."""
    try:
        out = subprocess.run(
            ["loginctl", "show-session", "self", "--property=Type", "--property=Desktop"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    props: dict[str, str] = {}
    for line in out.splitlines():
        key, sep, val = line.partition("=")
        if sep:
            props[key.strip()] = val.strip()
    return props


def detect_session() -> SessionInfo:
    desktops = {
        tok.strip().upper()
        for tok in os.environ.get("XDG_CURRENT_DESKTOP", "").split(":")
        if tok.strip()
    }
    session_type = os.environ.get("XDG_SESSION_TYPE", "").strip().lower()
    wayland_display = os.environ.get("WAYLAND_DISPLAY", "").strip()

    info = _loginctl_self()
    li_type = info.get("Type", "").strip().lower()
    li_desktop = info.get("Desktop", "").strip().upper()

    is_gnome = "GNOME" in desktops or li_desktop == "GNOME"

    # Fail closed: a non-wayland session_type (e.g. "x11") contradicts wayland even if
    # WAYLAND_DISPLAY leaks through; only treat as wayland on positive evidence.
    if session_type == "wayland" or li_type == "wayland":
        is_wayland = True
    elif session_type or li_type:
        is_wayland = False  # explicit non-wayland type present
    else:
        is_wayland = bool(wayland_display)  # last-resort heuristic

    # Ambiguous (no env + no loginctl signal at all) => unsupported.
    have_signal = bool(desktops or session_type or wayland_display or info)
    supported = is_gnome and is_wayland and have_signal

    detail = (
        f"desktop={sorted(desktops) or '-'} type={session_type or '-'} "
        f"wayland_display={'set' if wayland_display else '-'} "
        f"loginctl={{type:{li_type or '-'},desktop:{li_desktop or '-'}}}"
    )
    return SessionInfo(is_gnome=is_gnome, is_wayland=is_wayland, supported=supported, detail=detail)

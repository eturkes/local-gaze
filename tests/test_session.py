from __future__ import annotations

import pytest

from local_gaze import session
from local_gaze.session import detect_session

_ENV_KEYS = ("XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE", "WAYLAND_DISPLAY")


@pytest.fixture(autouse=True)
def _isolate_session(monkeypatch: pytest.MonkeyPatch) -> None:
    # Neutralize loginctl so only the env mapping is under test (deterministic in any host).
    monkeypatch.setattr(session, "_loginctl_self", lambda: {})
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)


def _set(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    for k, v in env.items():
        monkeypatch.setenv(k, v)


@pytest.mark.parametrize(
    ("env", "is_gnome", "is_wayland", "supported"),
    [
        (
            {"XDG_CURRENT_DESKTOP": "GNOME", "XDG_SESSION_TYPE": "wayland"},
            True,
            True,
            True,
        ),
        # multi-token desktop string, GNOME present
        (
            {"XDG_CURRENT_DESKTOP": "ubuntu:GNOME", "XDG_SESSION_TYPE": "wayland"},
            True,
            True,
            True,
        ),
        # GNOME but X11 -> fail closed even if WAYLAND_DISPLAY leaks
        (
            {"XDG_CURRENT_DESKTOP": "GNOME", "XDG_SESSION_TYPE": "x11", "WAYLAND_DISPLAY": "wl-0"},
            True,
            False,
            False,
        ),
        # wrong desktop
        (
            {"XDG_CURRENT_DESKTOP": "KDE", "XDG_SESSION_TYPE": "wayland"},
            False,
            True,
            False,
        ),
        # GNOME, no explicit type, but WAYLAND_DISPLAY heuristic
        (
            {"XDG_CURRENT_DESKTOP": "GNOME", "WAYLAND_DISPLAY": "wayland-0"},
            True,
            True,
            True,
        ),
    ],
)
def test_env_mapping(
    monkeypatch: pytest.MonkeyPatch,
    env: dict[str, str],
    is_gnome: bool,
    is_wayland: bool,
    supported: bool,
) -> None:
    _set(monkeypatch, **env)
    info = detect_session()
    assert info.is_gnome is is_gnome
    assert info.is_wayland is is_wayland
    assert info.supported is supported


def test_empty_env_is_ambiguous_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    # No env signals and no loginctl => ambiguous => unsupported (fail closed).
    info = detect_session()
    assert info.supported is False
    assert info.is_gnome is False
    assert info.is_wayland is False


def test_loginctl_supplies_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    # No env at all, but loginctl reports a GNOME wayland session -> supported.
    monkeypatch.setattr(
        session, "_loginctl_self", lambda: {"Type": "wayland", "Desktop": "GNOME"}
    )
    info = detect_session()
    assert info.supported is True


def test_detail_is_descriptive(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, XDG_CURRENT_DESKTOP="GNOME", XDG_SESSION_TYPE="wayland")
    info = detect_session()
    assert "GNOME" in info.detail and "wayland" in info.detail

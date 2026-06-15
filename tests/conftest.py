from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from local_gaze.config import Config, default_config


@pytest.fixture
def xdg_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point every XDG base (and HOME) at an isolated tmp tree so paths.py writes there.

    paths.py honors XDG_* first and falls back to HOME; set both to keep the suite from
    touching the real user dirs regardless of which branch a path helper takes.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return tmp_path


@pytest.fixture
def cfg() -> Config:
    return default_config()


@pytest_asyncio.fixture
async def fake_extension() -> AsyncIterator[object]:
    """Export the fake GNOME extension service on the session bus and yield its impl.

    The whole suite runs under `dbus-run-session`, so MessageBus() here connects to that
    private bus. Tests that need IPC depend on this fixture; the export is torn down after.
    """
    from dbus_fast.aio import MessageBus
    from fake_extension import FakeExtension  # sibling module (pytest prepend import mode)

    bus = await MessageBus().connect()
    impl = FakeExtension()
    bus.export(impl.path, impl)
    await bus.request_name(impl.bus_name)
    try:
        yield impl
    finally:
        bus.disconnect()

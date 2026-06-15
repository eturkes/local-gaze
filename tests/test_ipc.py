from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fake_extension import FakeExtension  # sibling module (pytest prepend import mode)

from local_gaze import __version__
from local_gaze.ipc.client import ExtensionClient


@pytest_asyncio.fixture
async def client(fake_extension: FakeExtension) -> AsyncIterator[ExtensionClient]:
    c = ExtensionClient("test-token")
    await c.connect()
    try:
        yield c
    finally:
        await c.close()


async def test_connect_sets_connected(client: ExtensionClient) -> None:
    assert client.connected is True


async def test_ping_round_trips(client: ExtensionClient) -> None:
    assert await client.ping() == f"pong:{__version__}"


async def test_get_status_parses_json(client: ExtensionClient) -> None:
    status = await client.get_status()
    assert status["version"] == __version__
    assert status["supported"] is True
    assert status["enabled"] is False
    assert status["n_workspaces"] == 4


async def test_get_windows_returns_list(client: ExtensionClient) -> None:
    wins = await client.get_windows()
    assert isinstance(wins, list) and len(wins) == 2
    w = wins[0]
    assert {"id", "title", "wm_class", "monitor", "frame", "nx", "ny", "focus"} <= set(w)
    assert set(w["frame"]) == {"x", "y", "w", "h"}


async def test_set_enabled_round_trips_and_mirrors(
    client: ExtensionClient, fake_extension: FakeExtension
) -> None:
    assert await client.set_enabled(True) is True
    assert (await client.get_status())["enabled"] is True
    assert await client.set_enabled(False) is True
    assert (await client.get_status())["enabled"] is False


async def test_switch_workspace_gated_on_enabled(client: ExtensionClient) -> None:
    # Disabled by default -> action methods return False (fail-closed mirror).
    assert await client.switch_workspace(1) is False
    await client.set_enabled(True)
    assert await client.switch_workspace(1) is True
    assert await client.switch_workspace(-1) is True


async def test_focus_window_at_gated_and_validated(client: ExtensionClient) -> None:
    assert await client.focus_window_at(0.5, 0.5) is False  # disabled
    await client.set_enabled(True)
    assert await client.focus_window_at(0.5, 0.5) is True
    assert await client.focus_window_at(2.0, 0.5) is False  # out of range rejected
    assert await client.focus_window_at(float("nan"), 0.5) is False  # NaN rejected


async def test_token_is_forwarded(
    client: ExtensionClient, fake_extension: FakeExtension
) -> None:
    await client.ping()
    assert fake_extension.last_token == "test-token"


async def test_show_status_and_overlay_round_trip(client: ExtensionClient) -> None:
    await client.set_enabled(True)
    assert await client.show_status("hi", 1) is True
    assert await client.show_calibration_target(0.5, 0.5, True) is True
    assert await client.hide_overlay() is True


async def test_on_enabled_changed_signal_fires(
    client: ExtensionClient, fake_extension: FakeExtension
) -> None:
    received: asyncio.Future[bool] = asyncio.get_running_loop().create_future()

    def _cb(enabled: bool) -> None:
        if not received.done():
            received.set_result(enabled)

    client.on_enabled_changed(_cb)
    assert await client.set_enabled(True) is True
    got = await asyncio.wait_for(received, timeout=2.0)
    assert got is True


async def test_methods_raise_when_not_connected() -> None:
    c = ExtensionClient()
    with pytest.raises(RuntimeError):
        await c.ping()


async def test_close_is_idempotent(client: ExtensionClient) -> None:
    await client.close()
    assert client.connected is False
    await client.close()  # second close must not raise

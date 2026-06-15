from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .schema import BUS_NAME, INTERFACE, OBJECT_PATH

if TYPE_CHECKING:
    from dbus_fast.aio import MessageBus
    from dbus_fast.aio.proxy_object import ProxyInterface


class ExtensionClient:
    """Async dbus-fast wrapper over the GNOME extension's `com.eturkes.LocalGaze` interface.

    Hides the dbus-fast snake_case proxy naming (`call_<method>`, `on_<signal>`) and appends
    the auth token as the trailing argument of every method call. Reconnect/backoff lives in
    the daemon: methods raise on a dead bus and the daemon catches/reconnects.
    """

    def __init__(
        self,
        token: str = "",
        *,
        bus_name: str = BUS_NAME,
        path: str = OBJECT_PATH,
        interface: str = INTERFACE,
    ) -> None:
        self._token = token
        self._bus_name = bus_name
        self._path = path
        self._interface = interface
        self._bus: MessageBus | None = None
        self._iface: ProxyInterface | None = None

    async def connect(self) -> None:
        from dbus_fast import BusType
        from dbus_fast.aio import MessageBus

        bus = await MessageBus(bus_type=BusType.SESSION).connect()
        node = await bus.introspect(self._bus_name, self._path)
        proxy = bus.get_proxy_object(self._bus_name, self._path, node)
        self._bus = bus
        self._iface = proxy.get_interface(self._interface)

    async def close(self) -> None:
        bus, self._bus, self._iface = self._bus, None, None
        if bus is not None:
            bus.disconnect()

    @property
    def connected(self) -> bool:
        return self._bus is not None and self._iface is not None and self._bus.connected

    def _proxy(self) -> Any:
        # dbus-fast generates call_<method>/on_<signal> on the proxy dynamically from
        # introspection XML, so the interface is necessarily Any to the type checker.
        if self._iface is None:
            raise RuntimeError("ExtensionClient is not connected")
        return self._iface

    async def ping(self) -> str:
        return await self._proxy().call_ping(self._token)

    async def get_status(self) -> dict:
        return json.loads(await self._proxy().call_get_status(self._token))

    async def get_windows(self) -> list[dict]:
        return json.loads(await self._proxy().call_get_windows(self._token))

    async def set_enabled(self, enabled: bool) -> bool:
        return await self._proxy().call_set_enabled(enabled, self._token)

    async def switch_workspace(self, direction: int) -> bool:
        return await self._proxy().call_switch_workspace(direction, self._token)

    async def focus_window_at(self, nx: float, ny: float) -> bool:
        return await self._proxy().call_focus_window_at(nx, ny, self._token)

    async def show_calibration_target(self, nx: float, ny: float, visible: bool) -> bool:
        return await self._proxy().call_show_calibration_target(nx, ny, visible, self._token)

    async def hide_overlay(self) -> bool:
        return await self._proxy().call_hide_overlay(self._token)

    async def show_status(self, text: str, level: int = 0) -> bool:
        return await self._proxy().call_show_status(text, level, self._token)

    def on_enabled_changed(self, cb: Callable[[bool], None]) -> None:
        self._proxy().on_enabled_changed(cb)

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from typing import TYPE_CHECKING

from . import paths, session
from .config import Config
from .ipc.client import ExtensionClient
from .logging_setup import configure
from .types import ActionKind

if TYPE_CHECKING:
    from .interpret.interpreter import Interpreter
    from .perception.base import PerceptionBackend
    from .types import Action, PerceptionResult

_log = logging.getLogger("local_gaze.daemon")

_BACKOFF_MIN = 0.5
_BACKOFF_MAX = 10.0


def _read_token(cfg: Config) -> str:
    """Return the IPC token, provisioning a fresh 0600 one if require_token and none exists."""
    if not cfg.ipc.require_token:
        return ""
    from .ipc.token import ensure_token

    return ensure_token()


class Daemon:
    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._client = ExtensionClient(_read_token(cfg), bus_name=cfg.ipc.bus_name,
                                       path=cfg.ipc.object_path, interface=cfg.ipc.interface)
        self._enabled = cfg.general.enabled_default
        # Daemon-side global rate-limit ceiling (last gate before the wire). Sliding 1s window.
        self._stamps: deque[float] = deque()

    def _on_enabled_changed(self, enabled: bool) -> None:
        self._enabled = enabled
        _log.info("EnabledChanged -> %s", enabled)

    def _rate_ok(self, ts: float) -> bool:
        ceiling = self._cfg.limits.max_actions_per_sec
        stamps = self._stamps
        while stamps and ts - stamps[0] >= 1.0:
            stamps.popleft()
        if len(stamps) >= ceiling:
            return False
        stamps.append(ts)
        return True

    async def _connect_with_backoff(self) -> None:
        delay = _BACKOFF_MIN
        while True:
            try:
                await self._client.connect()
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — any bus failure retries with backoff
                _log.warning("connect failed (%s); retrying in %.1fs", exc, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, _BACKOFF_MAX)

    async def _dispatch(self, action: Action) -> None:
        client = self._client
        if action.kind is ActionKind.FOCUS:
            await client.focus_window_at(action.nx, action.ny)
        elif action.kind is ActionKind.SWITCH:
            await client.switch_workspace(action.direction)

    async def run(self) -> int:
        cfg = self._cfg

        # 1) Session guard: fail closed on non-GNOME-Wayland.
        sess = session.detect_session()
        if not sess.supported:
            _log.error("unsupported session (%s); refusing to start", sess.detail)
            return 1

        # 2) Connect with backoff; treat Supported==false as fail-closed.
        await self._connect_with_backoff()
        try:
            status = await self._client.get_status()
        except asyncio.CancelledError:
            await self._client.close()
            raise
        except Exception as exc:  # noqa: BLE001
            _log.error("GetStatus failed: %s", exc)
            await self._client.close()
            return 1
        if not status.get("supported", False):
            _log.error("extension reports Supported=false; refusing to act")
            await self._client.close()
            return 1
        self._enabled = bool(status.get("enabled", self._enabled))
        self._client.on_enabled_changed(self._on_enabled_changed)

        # 3) Build + start the perception backend (lazy import: keeps openvino out of base path).
        from .perception.base import make_backend
        backend: PerceptionBackend = make_backend(cfg)
        interpreter = self._make_interpreter(cfg)
        backend.start()

        try:
            return await self._loop(backend, interpreter)
        finally:
            backend.stop()
            await self._client.close()

    def _make_interpreter(self, cfg: Config) -> Interpreter:
        from .calibration import model as calib_model
        from .interpret.interpreter import Interpreter

        calib = calib_model.load(paths.calibration_file())
        return Interpreter(cfg, calib)

    async def _loop(self, backend: PerceptionBackend, interpreter: Interpreter) -> int:
        cfg = self._cfg
        loop = asyncio.get_running_loop()
        period = 1.0 / cfg.general.fps
        dry_run = cfg.general.dry_run
        backoff = _BACKOFF_MIN

        while True:
            tick = loop.time()
            result: PerceptionResult = await loop.run_in_executor(None, backend.read)
            action = interpreter.step(result)

            if action.kind is not ActionKind.NONE:
                if not self._enabled:
                    _log.debug("gated (disabled): %s", action.kind.name)
                elif dry_run:
                    _log.info("dry-run decision: %s", action)
                elif not self._rate_ok(result.ts):
                    _log.debug("rate-limited: %s", action.kind.name)
                else:
                    try:
                        await self._dispatch(action)
                        backoff = _BACKOFF_MIN
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 — dead bus -> reconnect with backoff
                        _log.warning("dispatch failed (%s); reconnecting in %.1fs", exc, backoff)
                        await self._client.close()
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, _BACKOFF_MAX)
                        with contextlib.suppress(Exception):
                            await self._connect_with_backoff()
                            # Re-apply our own fail-closed gate: a missed EnabledChanged
                            # during the outage must not leave us acting on stale state.
                            status = await self._client.get_status()
                            if not status.get("supported", False):
                                _log.error("extension Supported=false after reconnect; not acting")
                                self._enabled = False
                            else:
                                self._enabled = bool(status.get("enabled", self._enabled))
                            self._client.on_enabled_changed(self._on_enabled_changed)

            elapsed = loop.time() - tick
            if elapsed < period:
                await asyncio.sleep(period - elapsed)


async def main(cfg: Config) -> int:
    configure(cfg.logging.level, cfg.logging.log_gaze)
    daemon = Daemon(cfg)
    try:
        return await daemon.run()
    except asyncio.CancelledError:
        _log.info("shutting down")
        return 0

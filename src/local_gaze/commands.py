from __future__ import annotations

import argparse
import asyncio
import logging
import subprocess
import sys
from importlib.util import find_spec
from pathlib import Path
from shlex import quote
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config
    from .ipc.client import ExtensionClient

_log = logging.getLogger("local_gaze.commands")

UUID = "local-gaze@eturkes.com"
SERVICE = "local-gaze.service"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOST_MOUNT = "/run/host"


def dispatch(args: argparse.Namespace) -> int:
    handler = _HANDLERS.get(args.cmd)
    if handler is None:
        _log.error("unknown command: %s", args.cmd)
        return 2
    return handler(args)


# --- container/host bridging ------------------------------------------------------------

def _in_container() -> bool:
    """True when running inside the Distrobox/podman container (per environment-facts)."""
    return Path("/run/.containerenv").exists() or Path("/run/host").is_dir()


def _host_path(p: Path) -> str:
    """Translate a container path to the host's view by stripping the /run/host mount."""
    s = str(p)
    if s.startswith(_HOST_MOUNT + "/"):
        return s[len(_HOST_MOUNT):]
    return s


def _host_exec(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Run argv on the HOST (via scripts/host-exec.sh) when containerized, else directly."""
    if _in_container():
        bridge = _REPO_ROOT / "scripts" / "host-exec.sh"
        argv = ["sh", str(bridge), *argv]
    return subprocess.run(argv, capture_output=True, text=True, check=False)


# --- commands ---------------------------------------------------------------------------

def cmd_check(args: argparse.Namespace) -> int:
    from . import session
    from .config import load_config
    from .ipc.client import ExtensionClient

    ok = True
    sess = session.detect_session()
    print(f"session: supported={sess.supported} ({sess.detail})")
    ok = ok and sess.supported

    cfg = load_config()
    token = _check_token(cfg)
    status = asyncio.run(_probe_bus(ExtensionClient(token, bus_name=cfg.ipc.bus_name,
                                                    path=cfg.ipc.object_path,
                                                    interface=cfg.ipc.interface)))
    if status is None:
        print("bus: unreachable (extension not exporting on the session bus)")
        ok = False
    else:
        print(f"bus: reachable (version={status.get('version', '?')})")
        enabled = bool(status.get("enabled", False))
        supported = bool(status.get("supported", False))
        print(f"extension: enabled={enabled} supported={supported}")
        ok = ok and supported

    for dep in ("dbus_fast", "numpy", "openvino", "cv2"):
        present = find_spec(dep) is not None
        required = dep in {"dbus_fast", "numpy"}
        print(f"dep {dep}: {'present' if present else 'absent'}"
              f"{'' if present or not required else ' (REQUIRED)'}")
        if required and not present:
            ok = False

    return 0 if ok else 1


def _check_token(cfg: Config) -> str:
    from .ipc import token

    return token.load_token() if cfg.ipc.require_token else ""


async def _probe_bus(client: ExtensionClient) -> dict | None:
    try:
        await client.connect()
        await client.ping()
        return await client.get_status()
    except Exception:  # noqa: BLE001 — any failure means the bus/extension is unreachable
        return None
    finally:
        await client.close()


def cmd_probe(args: argparse.Namespace) -> int:
    probe = _REPO_ROOT / "scripts" / "host-probe"
    if not probe.exists():
        _log.error("probe script missing: %s", probe)
        return 1
    extra = ["--human"] if getattr(args, "human", False) else []
    if _in_container():
        bridge = _REPO_ROOT / "scripts" / "host-exec.sh"
        # host-probe is Python; run it with python3, not sh.
        argv = ["sh", str(bridge), "python3", _host_path(probe), *extra]
    else:
        argv = ["python3", str(probe), *extra]
    proc = subprocess.run(argv, check=False)
    return proc.returncode


def cmd_run(args: argparse.Namespace) -> int:
    from . import daemon
    from .config import load_config

    return asyncio.run(daemon.main(load_config()))


def cmd_enable(args: argparse.Namespace) -> int:
    proc = _host_exec(["systemctl", "--user", "enable", "--now", SERVICE])
    _emit(proc)
    return proc.returncode


def cmd_disable(args: argparse.Namespace) -> int:
    from .config import load_config
    from .ipc.client import ExtensionClient

    cfg = load_config()
    token = _check_token(cfg)
    # Best-effort: tell the extension to stop acting before the unit goes down.
    asyncio.run(_best_effort_disable(ExtensionClient(token, bus_name=cfg.ipc.bus_name,
                                                     path=cfg.ipc.object_path,
                                                     interface=cfg.ipc.interface)))
    proc = _host_exec(["systemctl", "--user", "disable", "--now", SERVICE])
    _emit(proc)
    return proc.returncode


async def _best_effort_disable(client: ExtensionClient) -> None:
    try:
        await client.connect()
        await client.set_enabled(False)
    except Exception as exc:  # noqa: BLE001 — best effort; the unit stop is authoritative
        _log.debug("SetEnabled(False) skipped: %s", exc)
    finally:
        await client.close()


def cmd_calibrate(args: argparse.Namespace) -> int:
    import contextlib

    from . import paths
    from .calibration import run as calib_run
    from .config import load_config
    from .ipc import token as token_mod
    from .ipc.client import ExtensionClient
    from .logging_setup import configure
    from .perception.base import make_backend

    cfg = load_config()
    configure(cfg.logging.level, cfg.logging.log_gaze)  # install gaze redaction filter
    # Calibration drives the extension, so provision the token if it is missing.
    token = token_mod.ensure_token() if cfg.ipc.require_token else ""
    client = ExtensionClient(token, bus_name=cfg.ipc.bus_name, path=cfg.ipc.object_path,
                             interface=cfg.ipc.interface)
    backend = make_backend(cfg)

    async def _drive() -> int:
        await client.connect()
        # The calibration overlay is Enabled-gated; enable for the session and
        # restore the prior state afterward so calibration does not silently no-op.
        prev = bool((await client.get_status()).get("enabled", False))
        await client.set_enabled(True)
        backend.start()
        try:
            await calib_run.calibrate(cfg, client, backend)
        finally:
            backend.stop()
            if not prev:
                with contextlib.suppress(Exception):
                    await client.set_enabled(False)
            await client.close()
        print(f"calibration saved to {paths.calibration_file()}")
        return 0

    return asyncio.run(_drive())


def cmd_provision_token(args: argparse.Namespace) -> int:
    """Create the shared 0600 IPC token if absent (idempotent). Host-run alongside the extension."""
    from . import paths
    from .config import load_config
    from .ipc import token as token_mod

    cfg = load_config()
    if not cfg.ipc.require_token:
        print("ipc.require_token=false; the token is disabled (empty token accepted).")
        return 0
    token_mod.ensure_token()
    print(f"IPC token ready at {paths.token_file()} (mode 0600).")
    print("The GNOME extension reads this same file; no further action needed.")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """Container-safe: synthetic backend + dry-run interpreter; print decided actions."""
    import time
    from dataclasses import replace

    from .config import load_config
    from .interpret.interpreter import Interpreter
    from .perception.synthetic import SyntheticBackend
    from .types import ActionKind

    base = load_config()
    cfg = replace(base, general=replace(base.general, backend="synthetic", dry_run=True))
    backend = SyntheticBackend(cfg)
    interpreter = Interpreter(cfg, None)

    backend.start()
    print("demo: synthetic backend, dry-run (no camera, no D-Bus). Decided actions:")
    n_actions = 0
    try:
        deadline = time.monotonic() + 3.0
        period = 1.0 / cfg.general.fps
        while time.monotonic() < deadline:
            result = backend.read()
            action = interpreter.step(result)
            if action.kind is not ActionKind.NONE:
                n_actions += 1
                print(f"  [{result.frame_id:04d}] {action.kind.name} "
                      f"nx={action.nx:.3f} ny={action.ny:.3f} dir={action.direction:+d}")
            time.sleep(period)
    finally:
        backend.stop()
    print(f"demo: done ({n_actions} action(s) decided).")
    return 0


def cmd_install_extension(args: argparse.Namespace) -> int:
    src = _REPO_ROOT / "extension"
    if not src.is_dir():
        _log.error("extension directory missing: %s", src)
        return 1

    ext_root = Path.home() / ".local/share/gnome-shell/extensions"
    link = ext_root / UUID
    src_host = _host_path(src)
    link_host = _host_path(link)
    schemas_host = _host_path(src / "schemas")

    ext_root.mkdir(parents=True, exist_ok=True)
    # Symlink the repo extension dir (host view) so dev edits stay live.
    sh = (
        f"mkdir -p {quote(_host_path(ext_root))} && "
        f"ln -sfn {quote(src_host)} {quote(link_host)} && "
        f"glib-compile-schemas {quote(schemas_host)}"
    )
    proc = _host_exec(["sh", "-c", sh])
    _emit(proc)
    if proc.returncode != 0:
        return proc.returncode

    print(f"installed: {link_host} -> {src_host}")
    print("enable it on the host with:")
    print(f"  gnome-extensions enable {UUID}")
    print("Wayland (GNOME 50): log out and back in to load the extension "
          "(Alt+F2 'r' does not work on Wayland).")
    return 0


# --- helpers ----------------------------------------------------------------------------

def _emit(proc: subprocess.CompletedProcess[str]) -> None:
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)


_HANDLERS = {
    "check": cmd_check,
    "probe": cmd_probe,
    "run": cmd_run,
    "enable": cmd_enable,
    "disable": cmd_disable,
    "calibrate": cmd_calibrate,
    "provision-token": cmd_provision_token,
    "demo": cmd_demo,
    "install-extension": cmd_install_extension,
}

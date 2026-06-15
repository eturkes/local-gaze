# Lane: packaging, host/daemon runtime, D-Bus client, Distrobox-safe dev

Research date 2026-06-15. `[V]`=verified from official docs/source, `[A]`=assumed/recommended.
Aligns to existing repo skeleton: `src/ systemd/ extension/(schemas/) scripts/ tests/` and
`.gitignore` (which already reserves `.venv-host/`, `extension/schemas/gschemas.compiled`).

## TL;DR decisions
- uv `src/` layout, console entry `local-gaze`. openvino = **optional extra** (`[host]`), never a
  hard dep -> container `uv sync` + ruff/mypy/pytest pass with no openvino/camera. `[V]`
- D-Bus: **dbus-fast** (async, pure-py, v5.0.22 Jun-2026, cp3.14 wheels). PyGObject absent in
  container so this is mandatory. Fallback: **jeepney** 0.9.0. `[V]`
- CLI: **argparse** (stdlib, zero deps) over typer/click — honors KISS/few-deps. `[A]`
- systemd **user** service, default **disabled** (fail-safe). `[V]`
- Extension install = symlink into `~/.local/share/gnome-shell/extensions/<UUID>`
  (**not** `gnome-extensions/extensions` — that path in the brief is wrong). `[V]`

---

## 1. uv project layout + openvino-optional

`pyproject.toml`:
```toml
[project]
name = "local-gaze"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = ["dbus-fast>=2.21", "numpy>=2"]   # NO openvino here

[project.optional-dependencies]          # PEP 621 extras -> publishable, opt-in
host = ["openvino>=2026.2", "opencv-python>=4.10"]  # host-only; container never installs

[project.scripts]
local-gaze = "local_gaze.cli:main"       # console entry -> src/local_gaze/cli.py:main

[dependency-groups]                       # PEP 735; never published; "dev" is default
dev = ["ruff>=0.6", "mypy>=1.11", "pytest>=8", "pytest-asyncio>=0.24", "jeepney>=0.9"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
[tool.hatch.build.targets.wheel]
packages = ["src/local_gaze"]
```
Layout: `src/local_gaze/{__init__,cli,daemon,dbus_client,interpret,...}.py`, `tests/`.

Container dev (no openvino): `uv sync` (installs deps + default `dev` group; extra `host`
omitted -> openvino skipped). `[V]` Checks: `uv run ruff check . && uv run mypy src && uv run pytest`.

Host runtime (needs system openvino 2026.2 from `/usr/lib64/python3.13/site-packages`):
```bash
cd /home/eturkes/Projects/local-gaze            # host-valid cwd (host-exec gotcha)
uv venv --system-site-packages .venv-host       # inherit system openvino [V flag exists]
uv pip install -e . --python .venv-host          # installs dbus-fast/numpy; openvino already visible
# verify: .venv-host/bin/python -c "import openvino, local_gaze"
```
**Critical**: backend MUST lazy-`import openvino` inside the inference module (not top-level), so
container import of `local_gaze` never touches it. Tests mock `openvino`/camera (per env-facts).

---

## 2. dbus-fast client pattern (async, session bus) `[V]`

Imports + connect + introspect + call + property + signal:
```python
from dbus_fast.aio import MessageBus
from dbus_fast import BusType
import asyncio

async def main():
    bus = await MessageBus(bus_type=BusType.SESSION).connect()      # default is SESSION
    node = await bus.introspect("org.localgaze.Extension", "/org/localgaze/Extension")
    obj  = bus.get_proxy_object("org.localgaze.Extension", "/org/localgaze/Extension", node)
    ext  = obj.get_interface("org.localgaze.Extension1")
    await ext.call_switch_workspace(1)            # method  Foo -> call_foo (snake_case)
    enabled = await ext.get_enabled()             # property Enabled -> get_enabled
    await ext.set_active_window(12345)            # set_<prop>
    def on_focus(wid): print("focused", wid)
    ext.on_window_focused(on_focus)               # signal WindowFocused -> on_window_focused(cb)
    # ext.off_window_focused(on_focus) to unsubscribe
    await bus.wait_for_disconnect()
asyncio.run(main())
```
Naming rule (verified): method `Frobate`->`call_frobate`; prop `Bar`->`get_bar`/`set_bar`;
signal `Changed`->`on_changed(cb)`/`off_changed(cb)`. Multiple return args come back as a list.
`MessageBus(bus_address=..., bus_type=..., negotiate_unix_fd=False)`; socket connects in
`connect()` (v5.x). `introspect(name, path, timeout=30.0)` -> `Node`.

Fallback **jeepney** 0.9.0: pure-py, I/O-free core + `jeepney.io.asyncio` / `jeepney.io.blocking`;
non-magical (you hand-write message generators, no runtime introspection). Use only if dbus-fast
ever breaks; not needed now. `[V]`

---

## 3. Container IPC test: FAKE extension service under dbus-run-session `[V]`

No GNOME Shell in container -> stand up a real dbus-fast `ServiceInterface` exposing the SAME
interface, run client against it on an isolated bus. Decorators: short aliases `@method`/`@signal`/
`@dbus_property` + bare D-Bus type-string annotations (`'s' 'i' 'b' 'u' 'a{sv}'`) — the stable form.

`tests/fake_extension.py`:
```python
from dbus_fast.aio import MessageBus
from dbus_fast.service import ServiceInterface, method, signal, dbus_property
from dbus_fast.constants import PropertyAccess
import asyncio

class FakeExt(ServiceInterface):
    def __init__(self): super().__init__("org.localgaze.Extension1"); self._enabled = True
    @method()
    def SwitchWorkspace(self, idx: 'i') -> 'b': self.WindowFocused(idx); return True
    @dbus_property(access=PropertyAccess.READ)
    def Enabled(self) -> 'b': return self._enabled
    @signal()
    def WindowFocused(self, wid: 'i') -> 'i': return wid     # return value is broadcast

async def serve():
    bus = await MessageBus(bus_type=__import__('dbus_fast').BusType.SESSION).connect()
    bus.export("/org/localgaze/Extension", FakeExt())
    await bus.request_name("org.localgaze.Extension")
    await bus.wait_for_disconnect()
```
Run both under one ephemeral bus so client+fake share it:
```bash
uv run dbus-run-session -- python -c "import asyncio,tests.fake_extension as f,tests.client_smoke as c; \
  asyncio.run(c.run())"   # or: dbus-run-session -- pytest tests/test_ipc.py
```
pytest pattern: a `pytest-asyncio` fixture spawns `serve()` as a background task (or the whole
module under `dbus-run-session -- pytest`), client introspects the LIVE fake (no XML file needed),
asserts `call_switch_workspace`/`get_enabled`/signal round-trip. This exercises the REAL client lib
against the REAL wire protocol; only the GNOME side is faked. `[A]` pattern, `[V]` APIs.

---

## 4. systemd USER service `[V]`

Repo file `systemd/local-gaze.service` (installed via symlink/copy to user dir):
```ini
[Unit]
Description=local-gaze host daemon (camera+inference+D-Bus client)
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
# absolute venv interpreter — do NOT 'activate'; avoids uv sys.executable pitfall
ExecStart=/home/eturkes/Projects/local-gaze/.venv-host/bin/python -m local_gaze.cli run
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-%h/.config/local-gaze/env      # optional (- = ignore if missing); openvino tuning
Restart=on-failure
RestartSec=5
StartLimitIntervalSec=60
StartLimitBurst=3

[Install]
WantedBy=graphical-session.target               # user-session target (not multi-user)
```
Notes: `[V]` ExecStart must be the **`.venv-host` absolute python** (uv's `sys.executable` points at
the base interpreter, not the venv -> bare env crashes). No `User=`/`Group=` (user unit runs as you).
openvino is already importable via `--system-site-packages`, so no `PATH`/`LD_LIBRARY_PATH` needed;
put any device tuning (e.g. `OV_CACHE_DIR`) in the optional EnvironmentFile.

Install (host): symlink repo unit so edits propagate.
```bash
mkdir -p ~/.config/systemd/user
ln -sf /home/eturkes/Projects/local-gaze/systemd/local-gaze.service ~/.config/systemd/user/
systemctl --user daemon-reload
# default-disabled & safe: do NOT enable by default. enable explicitly when desired:
systemctl --user enable --now local-gaze.service     # 'local-gaze enable' wraps this
systemctl --user disable --now local-gaze.service    # 'local-gaze disable'
journalctl --user -u local-gaze -f
```
Default-disabled is the fail-closed posture (matches "fail closed on non-GNOME-Wayland"): unit ships
present but inert; the daemon itself must also exit non-zero if `XDG_SESSION_TYPE != wayland` or
`XDG_CURRENT_DESKTOP` lacks GNOME, so an accidental `enable` on a bad session won't grab the camera.

---

## 5. Extension install/update from repo (host) `[V]`

Canonical per-user path: `~/.local/share/gnome-shell/extensions/<UUID>` (UUID = `metadata.json`
`uuid`). Symlink the repo `extension/` dir so dev edits are live.
```bash
UUID=$(jq -r .uuid /home/eturkes/Projects/local-gaze/extension/metadata.json)
mkdir -p ~/.local/share/gnome-shell/extensions
ln -sfn /home/eturkes/Projects/local-gaze/extension ~/.local/share/gnome-shell/extensions/$UUID
glib-compile-schemas /home/eturkes/Projects/local-gaze/extension/schemas/   # -> gschemas.compiled (gitignored)
gnome-extensions enable $UUID
# Wayland (GNOME 50 default): MUST log out + back in to load new/changed extension code.
#   Alt+F2 'r' restart does NOT work on Wayland.
#   Dev-only nested shell to iterate w/o relogin: dbus-run-session -- gnome-shell --nested --wayland
```
`metadata.json` must list `"shell-version": ["50"]` (host is GNOME Shell 50.2; 50 postdates training
cutoff — verify extension APIs from web, not memory). glib-compile-schemas IS present in container,
so schema compile is a valid container-side static check even though enable/runtime is host-only.

---

## 6. CLI design (argparse) `[A]`

`src/local_gaze/cli.py` — single `main()`, argparse subparsers, lazy imports so each subcommand only
pulls what it needs (e.g. `run`/`calibrate` import openvino-backed code; `enable`/`install-extension`
don't). Subcommands:
| cmd | action |
|-----|--------|
| `probe` | run host probe (openvino devices, NPU, cameras) -> JSON; container=mock |
| `check` | env preflight: GNOME-Wayland? bus reachable? ext enabled? deps import? exit!=0 on fail |
| `run` | start daemon (camera+inference+interpret -> D-Bus client). systemd ExecStart target |
| `enable`/`disable` | `systemctl --user enable/disable --now local-gaze.service` |
| `install-extension` | symlink+glib-compile-schemas+`gnome-extensions enable` (host); prints relogin note |
| `calibrate` | interactive gaze calibration, persist to `~/.config/local-gaze/` |
| `demo` | headless/synthetic-frame dry run (no camera) for container verification |

Skeleton:
```python
import argparse
def main(argv=None):
    p = argparse.ArgumentParser(prog="local-gaze")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check"); sub.add_parser("probe"); sub.add_parser("run")
    sub.add_parser("enable"); sub.add_parser("disable"); sub.add_parser("demo")
    sub.add_parser("calibrate"); sub.add_parser("install-extension")
    a = p.parse_args(argv)
    from . import commands                      # lazy: avoids importing heavy deps at startup
    return commands.dispatch(a)                 # returns int exit code
```
Rationale: argparse = stdlib, **zero** added deps -> container `uv sync` stays lean; typer would pull
click+rich+typer. Reconsider typer only if rich help/completions become worth 3 deps.

---

## Justfile targets (`justfile` at repo root) `[A]`
```make
sync:        uv sync                                   # container dev env (no openvino)
host-venv:   uv venv --system-site-packages .venv-host && uv pip install -e . --python .venv-host
check:       uv run ruff check . && uv run mypy src && uv run pytest -q
ipc-test:    uv run dbus-run-session -- pytest tests/test_ipc.py -q
install-ext: # symlink + glib-compile-schemas + gnome-extensions enable (host; see §5)
svc-install: ln -sf $PWD/systemd/local-gaze.service ~/.config/systemd/user/ && systemctl --user daemon-reload
```
(justfile chosen over Makefile: no tab-sensitivity, cleaner recipes; `just` is a single static binary.
Confirm `just` is installed or fall back to a `Makefile` with `.PHONY` targets.)

## Gotchas
- `gnome-extensions/extensions` (brief) is WRONG; real path is `gnome-shell/extensions`. `[V]`
- uv `sys.executable` != venv python -> ALWAYS use absolute `.venv-host/bin/python` in ExecStart. `[V]`
- Wayland: no live shell reload; relogin required (or `--nested` dev shell). `[V]`
- Lazy-import openvino/cv2; top-level import breaks container `uv sync`/mypy/pytest. `[V]`
- dbus-fast snake_case proxy names (`call_`/`get_`/`set_`/`on_`/`off_`) — easy to mis-call. `[V]`
- host-exec gotcha (env-facts): `cd /` host-valid path before container->host commands. `[V]`

# Build Spec — local-gaze implementation contract

The precise contract parallel coding agents implement against. Every signature
below is load-bearing: implement your file to match it exactly so disjoint files
compose without conflict. `[H]` = host-validate; never assert from container.

Canonical identifiers (do not change):
- D-Bus: bus name `com.eturkes.LocalGaze`, path `/com/eturkes/LocalGaze`,
  interface `com.eturkes.LocalGaze`.
- Extension UUID `local-gaze@eturkes.com`; gschema
  `org.gnome.shell.extensions.local-gaze`; gschema path
  `/org/gnome/shell/extensions/local-gaze/`.
- Python package `local_gaze` (src layout). Python ≥3.13. Line length 100.
  Full type hints; `from __future__ import annotations` in every module.

---

## 1. Repo layout (final)

```
pyproject.toml          justfile          LICENSE          CLAUDE.md
docs/{adr.md,build-spec.md,environment-facts.md,research/*.md}
systemd/local-gaze.service
scripts/{host-exec.sh,host-probe,fetch-models.sh,probe.xml}
extension/
  metadata.json   extension.js   prefs.js   stylesheet.css
  schemas/org.gnome.shell.extensions.local-gaze.gschema.xml
  lib/{service.js,windows.js,workspace.js,overlay.js,quicktoggle.js,ratelimit.js,token.js,session.js}
src/local_gaze/
  __init__.py  __main__.py  cli.py  commands.py  daemon.py
  types.py  config.py  paths.py  session.py  logging_setup.py
  ipc/{__init__.py,schema.py,client.py}
  perception/{__init__.py,base.py,synthetic.py,mock.py,openvino_backend.py,camera.py,models.py}
  interpret/{__init__.py,smoothing.py,gestures.py,gaze.py,interpreter.py}
  calibration/{__init__.py,model.py,run.py}
tests/
  conftest.py  fake_extension.py
  test_config.py  test_types.py  test_smoothing.py  test_gestures.py
  test_gaze.py  test_interpreter.py  test_calibration.py
  test_ipc.py  test_synthetic.py  test_session.py  test_schema.py
models/                 # gitignored; fetch-models.sh populates; MANIFEST tracked
  MANIFEST              # (committed) name,url,sha256,license,revision per artifact
```

`extension/` is split into `lib/*.js` ES modules imported by `extension.js` to keep
each file single-responsibility; all run in the Shell process except `prefs.js`.

---

## 2. D-Bus interface XML (canonical — narrow, no eval)

This exact XML is the contract. It is embedded as a string in BOTH
`extension/lib/service.js` and `src/local_gaze/ipc/schema.py` (keep byte-identical;
`tests/test_schema.py` asserts the daemon-side copy parses and matches the method set).
Every method takes a **trailing `token s`** (empty string allowed when
`require_token=false`). All actions are gated on `Enabled==true`; out-of-range inputs
are rejected (clamped where noted), not sanitized.

```xml
<node>
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
</node>
```

Notes: token is the **last in-arg** of every method (uniform). `Enabled` is read-only
over D-Bus — the daemon changes state via `SetEnabled` (a method, gated + logged); the
extension also flips it from the quick-toggle and emits `EnabledChanged`. `Supported`
reflects the extension's own GNOME-Wayland check; daemon treats `Supported==false` as
fail-closed.

---

## 3. Config schema + file paths/perms

Config file `~/.config/local-gaze/config.toml` (dir `0700`, file `0600`). Token file
`~/.local/state/local-gaze/token` (`0600`). Calibration
`~/.local/state/local-gaze/calibration.json` (`0600`). Model cache
`~/.cache/local-gaze/ov/` (`0700`). All resolved in `paths.py` (XDG-aware, honors
`$XDG_*`). Missing config → defaults below (typed dataclass; TOML overrides by section).

```
[general]
backend = "synthetic"        # "synthetic" | "openvino" ("mock" is test-only)
enabled_default = false       # daemon never auto-enables actions
dry_run = false               # suppress D-Bus action calls, log decisions
fps = 30                      # capture/inference target

[ipc]
require_token = true          # false => empty token accepted
bus_name = "com.eturkes.LocalGaze"
object_path = "/com/eturkes/LocalGaze"
interface = "com.eturkes.LocalGaze"

[camera]
device = "/dev/video0"
width = 640
height = 480

[openvino]
device_order = ["NPU", "GPU", "CPU"]
performance_hint = "LATENCY"
cache_dir = ""                # "" => paths.model_cache_dir()
models_dir = ""               # "" => <repo>/models  or  paths.data_dir()/models

[gaze]
smoothing = "one_euro"        # "one_euro" | "ema"
ema_alpha = 0.4
min_confidence = 0.5
dwell_ms = 400                # continuous fixation before FocusWindowAt
stability_px = 0.04           # max normalized jitter radius during dwell

[flick]
v_on = 0.9                    # arm threshold (normalized x units / s)
v_off = 0.3                   # disarm threshold
refractory_ms = 600           # min gap between switches
min_present_frames = 3        # hand must be present this long before arming

[limits]
max_actions_per_sec = 4       # daemon global ceiling (extension has its own backstop)

[logging]
level = "INFO"
log_gaze = false              # true => include gaze coords/landmarks at DEBUG
dump_frames = false           # true => write frames to 0700 dir, WARN each session
```

Concrete numeric thresholds (`dwell_ms`, `v_on/v_off`, `refractory_ms`, `stability_px`)
are placeholders pending `[H]` empirical tuning; keep them config-driven.

---

## 4. Python modules — responsibility + key signatures

Common: every module starts `from __future__ import annotations`. Public types live in
`types.py`; no module redefines them. Keep imports of openvino/cv2 **inside**
`perception/openvino_backend.py`, `perception/camera.py`, `perception/models.py` (lazy),
never at package import time.

### `types.py` — shared dataclasses/enums (no heavy imports)
```python
from dataclasses import dataclass, field
from enum import Enum

class Hand(Enum): NONE=0; LEFT=1; RIGHT=2          # flick direction / handedness
class ActionKind(Enum): NONE=0; FOCUS=1; SWITCH=2

@dataclass(frozen=True, slots=True)
class GazePoint:                # raw model output, pre-calibration
    nx: float; ny: float        # model-space normalized gaze, may be uncalibrated
    confidence: float
    yaw: float = 0.0; pitch: float = 0.0   # head pose (deg), optional

@dataclass(frozen=True, slots=True)
class HandSample:
    present: bool
    cx: float = 0.5             # normalized hand-center x (0..1)
    cy: float = 0.5
    confidence: float = 0.0

@dataclass(frozen=True, slots=True)
class PerceptionResult:         # one per frame from a backend
    ts: float                   # monotonic seconds
    gaze: GazePoint | None
    hand: HandSample
    frame_id: int = 0

@dataclass(frozen=True, slots=True)
class Action:                   # interpreter output the daemon dispatches
    kind: ActionKind
    nx: float = 0.0; ny: float = 0.0          # for FOCUS
    direction: int = 0                         # for SWITCH (-1/+1)
```

### `paths.py` — XDG path resolution + secure dir creation
```python
from pathlib import Path
def config_dir() -> Path: ...        # ~/.config/local-gaze (created 0700)
def state_dir() -> Path: ...         # ~/.local/state/local-gaze (0700)
def model_cache_dir() -> Path: ...   # ~/.cache/local-gaze/ov (0700)
def config_file() -> Path: ...       # config_dir()/config.toml
def token_file() -> Path: ...        # state_dir()/token
def calibration_file() -> Path: ...  # state_dir()/calibration.json
def ensure_dir(p: Path, mode: int = 0o700) -> Path: ...   # mkdir parents, chmod, verify
```

### `config.py` — typed config load/validate
```python
from dataclasses import dataclass
@dataclass(frozen=True, slots=True)
class Config:                  # nested frozen dataclasses per section (General, Ipc, ...)
    general: "GeneralCfg"; ipc: "IpcCfg"; camera: "CameraCfg"
    openvino: "OpenvinoCfg"; gaze: "GazeCfg"; flick: "FlickCfg"
    limits: "LimitsCfg"; logging: "LoggingCfg"

def load_config(path: Path | None = None) -> Config: ...   # defaults + TOML(tomllib) merge
def default_config() -> Config: ...
DEFAULTS: dict   # section -> field -> default (single source for docs/tests)
```
Rules: use stdlib `tomllib` (read-only). Unknown keys → log warning, ignore. Validate
ranges (fps>0, 0<=alpha<=1, v_off<v_on, direction order subset of {NPU,GPU,CPU}); raise
`ConfigError` on invalid.

### `session.py` — fail-closed session detection (shared by daemon + cli check)
```python
from dataclasses import dataclass
@dataclass(frozen=True, slots=True)
class SessionInfo:
    is_gnome: bool; is_wayland: bool; supported: bool; detail: str
def detect_session() -> SessionInfo: ...   # env (XDG_CURRENT_DESKTOP split ':',
    # WAYLAND_DISPLAY, XDG_SESSION_TYPE) + best-effort `loginctl show-session self`;
    # ambiguous => supported=False. Does NOT probe D-Bus (that's the daemon's job).
```

### `logging_setup.py` — stdlib logging config + gaze redaction
```python
import logging
def configure(level: str, log_gaze: bool) -> None: ...   # root logger, fmt, redaction flag
def redact_gaze(enabled: bool) -> "Callable": ...        # filter/formatter helper
```

### `ipc/schema.py` — canonical interface constants + introspection XML
```python
BUS_NAME = "com.eturkes.LocalGaze"
OBJECT_PATH = "/com/eturkes/LocalGaze"
INTERFACE = "com.eturkes.LocalGaze"
INTROSPECTION_XML: str          # the §2 XML, byte-identical to extension copy
def method_names() -> set[str]: ...   # parsed from XML (used by tests + client guard)
```

### `ipc/client.py` — async dbus-fast client wrapper (the daemon's only IPC surface)
```python
from dbus_fast.aio import MessageBus
class ExtensionClient:
    def __init__(self, token: str = "", *, bus_name=BUS_NAME, path=OBJECT_PATH,
                 interface=INTERFACE) -> None: ...
    async def connect(self) -> None: ...      # MessageBus(SESSION).connect()+introspect+proxy
    async def close(self) -> None: ...
    @property
    def connected(self) -> bool: ...
    async def ping(self) -> str: ...                       # -> "pong:<ver>"
    async def get_status(self) -> dict: ...                # json.loads(GetStatus)
    async def get_windows(self) -> list[dict]: ...
    async def set_enabled(self, enabled: bool) -> bool: ...
    async def switch_workspace(self, direction: int) -> bool: ...
    async def focus_window_at(self, nx: float, ny: float) -> bool: ...
    async def show_calibration_target(self, nx: float, ny: float, visible: bool) -> bool: ...
    async def hide_overlay(self) -> bool: ...
    async def show_status(self, text: str, level: int = 0) -> bool: ...
    def on_enabled_changed(self, cb: "Callable[[bool], None]") -> None: ...
```
Notes: dbus-fast snake_cases the proxy (`call_focus_window_at`, `get_enabled`,
`on_enabled_changed`); this wrapper hides that. Token is appended as the trailing arg of
every `call_*`. Reconnect/backoff lives in the daemon, not here; methods raise on a
dead bus and the daemon catches.

### `perception/base.py` — backend Protocol + factory
```python
from typing import Protocol, runtime_checkable
@runtime_checkable
class PerceptionBackend(Protocol):
    def start(self) -> None: ...      # open camera / compile models (host); no-op for synthetic
    def read(self) -> "PerceptionResult": ...   # one frame's perception (blocking, bounded)
    def stop(self) -> None: ...
    @property
    def info(self) -> dict: ...       # {backend, device, models, camera} for status/probe

def make_backend(cfg: "Config") -> PerceptionBackend: ...   # dispatch on cfg.general.backend;
    # "openvino" import is performed INSIDE this function's openvino branch (lazy).
```

### `perception/synthetic.py` — deterministic, dependency-free backend (container default)
```python
class SyntheticBackend:           # structurally satisfies PerceptionBackend
    def __init__(self, cfg: "Config", *, script: "Sequence[PerceptionResult] | None" = None): ...
    # generates a repeatable gaze sweep + scripted flicks; advances a monotonic clock.
```

### `perception/mock.py` — test-injected backend
```python
class MockBackend:
    def __init__(self, frames: "list[PerceptionResult]"): ...   # yields frames in order, then NONE
```

### `perception/openvino_backend.py` — real CV (lazy openvino+cv2)  [H]
```python
class OpenVinoBackend:
    def __init__(self, cfg: "Config"): ...     # stores cfg; imports nothing heavy yet
    def start(self) -> None: ...                # import openvino,cv2; Core(); set CACHE_DIR;
        # compile gaze chain + hand models via models.compile_with_fallback; open camera
    def read(self) -> "PerceptionResult": ...   # capture -> preprocess -> infer chain ->
        # numpy post-process (NMS/decode host-side) -> GazePoint+HandSample
    def stop(self) -> None: ...
    @property
    def info(self) -> dict: ...                 # chosen device per model, FULL_DEVICE_NAME
```

### `perception/models.py` — OV device selection + static reshape + cache (lazy)  [H]
```python
def make_core(cache_dir: str): ...             # import openvino; Core(); set CACHE_DIR
def compile_with_fallback(core, model_path: str, static_shapes: dict,
                          device_order: list[str], hint: str = "LATENCY"): ...
    # reshape to static, try device_order, return (compiled, device); log choice
def npu_probe(core) -> tuple[bool, str]: ...   # compile+infer tiny static model on "NPU"
```

### `perception/camera.py` — V4L2/OpenCV capture (lazy cv2)  [H]
```python
class Camera:
    def __init__(self, device: str, width: int, height: int): ...
    def open(self) -> None: ...; def read(self) -> "np.ndarray": ...; def close(self) -> None: ...
```

### `interpret/smoothing.py` — filters (pure numpy/math, container-testable)
```python
class OneEuroFilter:
    def __init__(self, freq: float, min_cutoff: float = 1.0, beta: float = 0.0,
                 d_cutoff: float = 1.0): ...
    def __call__(self, x: float, t: float) -> float: ...
class Ema:
    def __init__(self, alpha: float): ...
    def __call__(self, x: float) -> float: ...
def make_smoother(cfg: "GazeCfg", freq: float): ...   # returns per-axis smoother pair
```

### `interpret/gestures.py` — flick detection (hysteresis + refractory)
```python
class FlickDetector:
    def __init__(self, cfg: "FlickCfg"): ...
    def update(self, hand: "HandSample", ts: float) -> int: ...   # returns -1/+1 once per
        # flick (debounced), else 0. Maintains ring buffer of (cx,ts), arm/disarm state.
    def reset(self) -> None: ...
```

### `interpret/gaze.py` — calibrated gaze → screen point + dwell
```python
class GazeTracker:
    def __init__(self, cfg: "GazeCfg", calib: "CalibrationModel | None", freq: float): ...
    def update(self, gaze: "GazePoint | None", ts: float) -> "tuple[float,float] | None": ...
        # smooth -> apply calib map -> clamp [0,1]; returns (nx,ny) or None (low-conf)
    def dwell_point(self) -> "tuple[float,float] | None": ...  # (nx,ny) if dwell+stability met
    def reset(self) -> None: ...
```

### `interpret/interpreter.py` — fuse gaze+flick → gated Action (+ rate limit)
```python
class Interpreter:
    def __init__(self, cfg: "Config", calib: "CalibrationModel | None"): ...
    def step(self, result: "PerceptionResult") -> "Action": ...   # drives GazeTracker +
        # FlickDetector; applies global rate limit (max_actions_per_sec); returns Action
        # (NONE if gated/rate-limited). Pure: no I/O, no D-Bus.
```

### `calibration/model.py` — affine/polynomial gaze map persistence
```python
from dataclasses import dataclass
@dataclass(frozen=True, slots=True)
class CalibrationModel:
    coeffs: list[float]          # affine 2x3 (or poly) raw->screen, row-major
    created: str                 # ISO ts
    def apply(self, nx: float, ny: float) -> tuple[float, float]: ...
def fit(samples: "list[tuple[GazePoint, tuple[float,float]]]") -> CalibrationModel: ...
    # least-squares affine fit (numpy); >= n_targets points
def load(path: "Path") -> "CalibrationModel | None": ...
def save(model: CalibrationModel, path: "Path") -> None: ...   # 0600 atomic write
```

### `calibration/run.py` — interactive calibration flow (host)  [H]
```python
async def calibrate(cfg: "Config", client: "ExtensionClient",
                    backend: "PerceptionBackend") -> "CalibrationModel": ...
    # for each target nx,ny in a grid: ShowCalibrationTarget(visible=True),
    # collect stable gaze, HideOverlay; fit(); save to paths.calibration_file()
TARGETS: "list[tuple[float,float]]"   # e.g. 3x3 normalized grid
```

### `ipc/__init__.py`, `perception/__init__.py`, etc. — re-export public names only.

### `daemon.py` — single asyncio loop, fail-closed, kill-switch-aware
```python
class Daemon:
    def __init__(self, cfg: "Config"): ...
    async def run(self) -> int: ...     # 1) session guard (exit!=0 if unsupported)
        # 2) connect ExtensionClient (backoff) + check Supported
        # 3) make_backend; backend.start()
        # 4) loop @ fps: result=backend.read(); action=interpreter.step(result);
        #    if enabled and not dry_run: dispatch action via client (try/except->backoff)
        # 5) honor EnabledChanged signal + SetEnabled; stop capture when disabled
        # 6) clean shutdown: backend.stop(), client.close()
async def main(cfg: "Config") -> int: ...
```
Dispatch maps `Action.kind`: FOCUS→`focus_window_at`, SWITCH→`switch_workspace`. The
daemon owns reconnect/backoff and the global rate-limit ceiling enforcement check (the
interpreter computes intent; the daemon is the last gate before the wire).

### `cli.py` + `commands.py` — argparse entry + dispatch
```python
# cli.py
def main(argv: "list[str] | None" = None) -> int: ...   # argparse, subparsers (required),
    # lazy `from . import commands`; returns commands.dispatch(args)
# commands.py  (each returns int exit code; lazy-imports heavy deps per command)
def dispatch(args) -> int: ...
def cmd_check(args) -> int: ...           # session + bus reachable + ext enabled + deps
def cmd_probe(args) -> int: ...           # invoke scripts/host-probe; container=>"unverified"
def cmd_run(args) -> int: ...             # asyncio.run(daemon.main(load_config()))
def cmd_enable(args) -> int: ...          # systemctl --user enable --now (host)
def cmd_disable(args) -> int: ...         # systemctl --user disable --now + SetEnabled(False)
def cmd_calibrate(args) -> int: ...       # asyncio.run(calibration.run.calibrate(...))
def cmd_demo(args) -> int: ...            # synthetic backend dry-run, no camera/D-Bus action
def cmd_install_extension(args) -> int: ... # symlink + glib-compile-schemas + enable (host)
```
Subcommands: `check probe run enable disable calibrate demo install-extension`.

### `__main__.py`
```python
from .cli import main
import sys; sys.exit(main())
```

---

## 5. GNOME extension files — responsibility

All under `extension/`. `extension.js` is the ESM default-export `Extension` subclass;
`lib/*.js` are plain ESM helpers it imports. Strict symmetry enable()/disable():
disable() unexports D-Bus, removes ALL timers/signals, destroys ALL actors, nulls refs.
Fail closed: if not Wayland (`Meta.is_wayland_compositor()` `[H]`), set `Supported=false`,
export nothing actionable, no-op.

- `metadata.json` — `shell-version` exactly `["50"]` `[H]`, `session-modes` `["user"]`,
  `settings-schema` `org.gnome.shell.extensions.local-gaze`, uuid `local-gaze@eturkes.com`.
- `schemas/org.gnome.shell.extensions.local-gaze.gschema.xml` — keys: `active` (b, default
  false; the single Enabled source of truth), `require-token` (b, default true). Compiled
  by `glib-compile-schemas` (container static check OK).
- `extension.js` — lifecycle; instantiate `GazeService`, `QuickToggleIndicator`; wire
  `settings 'changed::active'` → service `Enabled` + emit `EnabledChanged`; guard Wayland.
- `lib/service.js` — `GazeService` JS object + the §2 IFACE string; exported via
  `Gio.DBusExportedObject.wrapJSObject(IFACE, svc)`; assign `svc._impl` BEFORE
  `.export(Gio.DBus.session, '/com/eturkes/LocalGaze')`; `unexport()` in teardown. Each
  method: token check (`lib/token.js`) → Enabled gate (except Ping/GetStatus/SetEnabled) →
  rate-limit (`lib/ratelimit.js`) → input clamp/validate → delegate to windows/workspace/
  overlay; emit `EnabledChanged` via `_impl.emit_signal`. NO eval/exec method.
- `lib/windows.js` — `getWindows()` (JSON model: tab_list NORMAL_ALL on active ws,
  sort_windows_by_stacking, frame_rect→global px + normalized) and
  `focusAt(nx,ny)` (map normalized→global px via monitor, reverse-stacking hit-test,
  `Main.activateWindow`).
- `lib/workspace.js` — `switchRelative(dir)`: clamp dir∈{-1,+1}, index math
  `(idx+dir+n)%n`, `get_workspace_by_index(t).activate(time)` (explicit wrap).
- `lib/overlay.js` — `showTarget(nx,ny)`/`hide()`: St.Widget chrome per monitor via
  `Main.layoutManager.addChrome({affectsInputRegion:true})`; dot at `nx*m.width`;
  `removeChrome()+destroy()` teardown. `showStatus(text,level)` via
  `Main.osdWindowManager.showOne`.
- `lib/quicktoggle.js` — `QuickToggle`+`SystemIndicator` bound to gsetting `active`
  (kill switch); panel icon visible while active (our own camera-active indicator).
- `lib/ratelimit.js` — JS token-bucket (`GLib.get_monotonic_time`); extension-side
  backstop ceiling; every timer id stored, `GLib.Source.remove` in teardown.
- `lib/token.js` — read `~/.local/state/local-gaze/token` (0600); constant-time-ish
  compare; if `require-token` false, accept empty. Refuse group/world-readable file.
- `lib/session.js` — `isSupported()` = Wayland check + GNOME (always true in-Shell);
  feeds `Supported` property.
- `prefs.js` — Adw page binding `active` + `require-token` switches; NO Shell imports.
- `stylesheet.css` — overlay/dot styling.

---

## 6. Model onboarding plan

`scripts/fetch-models.sh` (POSIX sh; host-run): for each artifact → TLS download to
`models/<group>/` → `sha256sum -c` against pinned value in `models/MANIFEST` → on
mismatch abort. Then convert MediaPipe TFLite in-house. Reject any archive containing
executable payloads. `models/` is gitignored; `models/MANIFEST` is committed.

Gaze (OMZ, FP16, Apache-2.0), base
`https://storage.openvinotoolkit.org/repositories/open_model_zoo/2023.0/models_bin/1`:
`face-detection-retail-0004`, `head-pose-estimation-adas-0001`,
`facial-landmarks-35-adas-0002`, `gaze-estimation-adas-0002` — each `.xml`+`.bin` under
`<NAME>/FP16/`. Hand (MediaPipe, Apache-2.0): download pinned
`.../hand_landmarker/float16/1/hand_landmarker.task` (zip) → extract
`hand_detector.tflite` + `hand_landmarks_detector.tflite` (the palm + 21-landmark
"full" models, renamed inside the Tasks bundle) →
`ov.convert_model(...)`+`ov.save_model(...)` → `models/hand/{palm,landmark}.{xml,bin}`,
then `model.reshape(...)` to static at load (verified 2026-06-15: inputs are already
static — palm `input_1`=[1,192,192,3], landmark `input_1`=[1,224,224,3]). `models/MANIFEST` records
name,url,sha256,license,revision; **all sha256 are TODO until first `[H]` host download**.

Static shapes for reshape (NCHW unless noted):
- face-detect `data`=[1,3,300,300]; head-pose `data`=[1,3,60,60]; landmarks-35
  `data`=[1,3,60,60]; gaze `left_eye_image`/`right_eye_image`=[1,3,60,60],
  `head_pose_angles`=[1,3]; palm=[1,192,192,3] NHWC; hand-landmark=[1,224,224,3] NHWC.
Per-model NPU compile (host-verified 2026-06-15 via `compile_with_fallback`): **all 6 models
compile+infer on NPU with no CPU/GPU fallback and zero NPU-unsupported ops** — the 4 OMZ gaze
models (face-detect SSD `DetectionOutput` did NOT block NPU) and both MediaPipe hand models
(`palm` 815 ops, `landmark` 548 ops; the expected `Interpolate` fallback did NOT occur).
`CACHE_DIR` blob caching verified (`LOADED_FROM_CACHE=True` on recompile; cold 235–709 ms →
warm 16–29 ms). See environment-facts.md / README Verification status.

---

## 7. Tests (container-safe) + the check command

All tests run with **no openvino installed** (mock/synthetic only). pytest + pytest-asyncio.

- `tests/test_types.py` — dataclass construction/immutability defaults.
- `tests/test_config.py` — defaults, TOML override, range validation raises `ConfigError`,
  unknown-key warning.
- `tests/test_session.py` — `detect_session` maps env combinations → supported flag
  (monkeypatch env); ambiguous ⇒ unsupported.
- `tests/test_smoothing.py` — OneEuro/EMA numeric behavior (step response, monotonic lag).
- `tests/test_gestures.py` — `FlickDetector`: arms on |vx|>v_on, fires correct sign once,
  refractory suppresses double-fire, returns to neutral re-arms.
- `tests/test_gaze.py` — `GazeTracker`: low-confidence→None, dwell+stability gating,
  calib map applied + clamped.
- `tests/test_calibration.py` — `fit` recovers a known affine; `save`/`load` round-trip;
  file mode 0600.
- `tests/test_interpreter.py` — synthetic/mock `PerceptionResult` stream → expected
  `Action`s; rate limit caps actions/sec; dry-run/enabled gating.
- `tests/test_synthetic.py` — `SyntheticBackend` is deterministic + satisfies the Protocol
  (`isinstance(b, PerceptionBackend)`).
- `tests/test_schema.py` — `ipc/schema.py` XML parses, method set == expected set, every
  method's last in-arg is `token s`.
- `tests/test_ipc.py` — under `dbus-run-session`: `fake_extension.py` (real dbus-fast
  `ServiceInterface` of the §2 interface) exported on `com.eturkes.LocalGaze`; real
  `ExtensionClient` connects, round-trips `ping/get_status/get_windows/set_enabled/
  switch_workspace/focus_window_at`, and receives `EnabledChanged`.
- `tests/conftest.py` — fixtures: tmp XDG dirs, default `Config`, fake-extension bus
  fixture (spawns `serve()` task on the session bus provided by `dbus-run-session`).
- `tests/fake_extension.py` — the fake `ServiceInterface` (token accepted, gates mirror the
  real contract enough for client round-trip).

**Exact container-safe check command** (the canonical gate; also `just check` + `just ipc-test`):
```
uv sync && uv run ruff check . && uv run mypy src && \
  uv run dbus-run-session -- pytest -q
```
Running the whole suite under `dbus-run-session` gives `test_ipc.py` a private session bus
while every other test ignores it. openvino/camera/GNOME-Shell are never touched by this
command; their validation is host-only (`local-gaze probe`, extension relogin, e2e).

---

## 8. Build/dev runner (justfile targets)

`sync` (container env, no openvino) · `host-venv` (`python3 -m venv --system-site-packages
.venv-host && .venv-host/bin/pip install -e .` — host has no uv; verified to expose system
openvino) · `check` (ruff+mypy+pytest under
dbus-run-session) · `ipc-test` · `install-ext` (symlink + glib-compile-schemas +
`gnome-extensions enable`, host) · `svc-install` (symlink unit + `systemctl --user
daemon-reload`) · `fetch-models` · `probe` (`scripts/host-probe`). Fall back to a Makefile
only if `just` is absent on host `[H]`.

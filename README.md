# local-gaze

Local, NPU-first **eye-tracking + hand-gesture control** for the **GNOME 50 Wayland**
desktop. A host daemon runs camera capture and OpenVINO inference (Intel NPU first,
GPU/CPU fallback) entirely on-device, interprets gaze + hand motion into intent, and
drives a narrow GNOME Shell extension over session-bus D-Bus to **focus the window you
dwell on** and **switch workspaces with a hand flick**. No cloud, no network inference,
no synthetic-input or screen-capture portals.

- **Gaze → focus**: smoothed, calibrated gaze + a dwell timer focuses the topmost window
  under your gaze point.
- **Hand flick → workspace switch**: horizontal hand-center velocity with hysteresis +
  refractory debounce switches workspace left/right.
- **Fail-closed + default-disabled**: refuses to act off GNOME-Wayland and until you flip
  the Quick Settings kill switch; the camera opens only while enabled.

Design rationale lives in `docs/adr.md` (10 ADRs); the exact implementation contract is
`docs/build-spec.md`; probed environment truth is `docs/environment-facts.md`; the
security review is `docs/security-privacy.md`.

> **Status:** logic, IPC, and the container test path are implemented and container-tested;
> the host NPU smoke is verified; **gaze/hand accuracy, real-camera capture, and the live
> extension runtime are NOT yet hardware-validated.** See [Verification status](#verification-status).

## What it does (pipeline)

```
camera ─▶ OpenVINO perception ─▶ interpret (smooth · dwell · flick · gate · rate-limit)
 (host)    gaze chain + hand        │
                                    ▼ Action (FOCUS nx,ny | SWITCH ±1)
                          D-Bus client ─▶ GNOME Shell extension ─▶ Mutter
                          com.eturkes.LocalGaze   (focus window / switch workspace / overlay)
```

- **Gaze** = OMZ 4-model chain (face-detect → head-pose + 35-landmarks → gaze-estimation),
  FP16 IR. **Hand** = MediaPipe palm + hand-landmark converted in-house to OpenVINO IR.
  **Flick** uses no extra model (hand-center velocity + hysteresis). See ADR-006.
- The daemon is the only camera consumer; the extension is the only component that touches
  windows/workspaces (ADR-001). They speak the fixed interface `com.eturkes.LocalGaze` on
  the session bus (ADR-002), method/property/signal-only — **no eval/exec surface**.

## Supported environment

| Requirement | Value |
|---|---|
| Desktop | **GNOME Shell 50** (Wayland session only; X11 unsupported by design — ADR-001) |
| GPU/NPU stack | **Intel NPU via OpenVINO** (NPU→GPU→CPU fallback); validated on Intel Core Ultra (Lunar Lake), NPU = "Intel AI Boost" |
| Runtime | **OpenVINO 2026.2** + a webcam (`/dev/video*`) on the host |
| Python | **≥3.13** |

The development/test environment is a Distrobox **container** (no OpenVINO, no GNOME Shell,
no real camera); the **runtime target is the host desktop**. This split is load-bearing
(see [HOST vs CONTAINER](#host-vs-container)).

## HOST vs CONTAINER

The repo runs in two views of the same files (container path
`/run/host/home/<you>/Projects/local-gaze` == host path `/home/<you>/Projects/local-gaze`).
What runs where:

| Component / capability | Container (dev/test) | Host (runtime) |
|---|---|---|
| Python logic, interpreter, calibration math | ✅ unit-tested | ✅ |
| D-Bus client round-trip vs **fake** extension (`dbus-run-session`) | ✅ | ✅ |
| ruff / mypy / gschema-compile / JSON+JS lint | ✅ | ✅ |
| `local-gaze demo` (synthetic, dry-run) | ✅ | ✅ |
| OpenVINO import / device select / **NPU compile+infer** | ❌ (mock) | ✅ (`local-gaze probe`) |
| Real camera capture | ❌ (synthetic) | ✅ |
| **GNOME Shell extension** runtime (focus / workspace / overlay) | ❌ | ✅ |
| The **daemon** (`local-gaze run`) end-to-end | ❌ | ✅ |

Container→host commands bridge through `scripts/host-exec.sh`, which `cd /` first to dodge
the `host-spawn` "exit 127, no output when cwd is missing on the host" gotcha
(`docs/environment-facts.md`, ADR-007). `local-gaze enable/disable/probe/install-extension`
auto-detect the container and bridge for you.

## Install / dev setup

```sh
# Container (dev/test): deps only, NO openvino.
just sync            # == uv sync

# Host (runtime, run ON THE HOST): venv inheriting system OpenVINO 2026.2.
# The host has no uv, so this uses the system python3 stdlib venv (verified to
# expose system openvino 2026.2 under --system-site-packages).
just host-venv       # == python3 -m venv --system-site-packages .venv-host && .venv-host/bin/pip install -e .
```

`openvino`/`opencv-python` are an optional `[host]` extra, never a container dependency
(ADR-010). The systemd unit's `ExecStart` uses the absolute `.venv-host/bin/python`.

## Container-safe checks (the canonical gate)

Run the full container gate — lint, type-check, and the live D-Bus IPC round-trip against a
fake extension on a private session bus — with:

```sh
just check
# == uv sync && uv run ruff check . && uv run mypy src && \
#    uv run dbus-run-session -- pytest -q
```

`dbus-run-session` gives `tests/test_ipc.py` an ephemeral session bus (real `dbus-fast`
client vs `tests/fake_extension.py`); every other test ignores it. OpenVINO, the camera,
and GNOME Shell are never touched by this command — their validation is host-only.
IPC-only: `just ipc-test`.

## Host probe (NPU / cameras / extension state)

`scripts/host-probe` enumerates OpenVINO devices, proves NPU usability by **compiling and
inferring a tiny static model on `"NPU"`** (device-listing alone is not trusted — ADR-007),
and lists `/dev/video*`, GNOME Shell version, and the extension's installed/enabled state.
It emits JSON (`--human` for a summary) and always exits 0 — read the top-level `supported`
verdict.

```sh
# On the host:
scripts/host-probe              # JSON report
scripts/host-probe --human      # human summary

# From the container (bridges to the host; both equivalent):
scripts/host-exec.sh scripts/host-probe
local-gaze probe
```

Inside the container every host-only field reports `"unverified"` rather than a false
positive (`supported` is `false`: the target runtime is the host desktop).

## GNOME extension: install / enable / disable

The extension lives in `extension/` (UUID `local-gaze@eturkes.com`, gschema
`org.gnome.shell.extensions.local-gaze`). Installing symlinks the repo dir (so edits stay
live), compiles the gschema, and prints the enable instructions:

```sh
local-gaze install-extension      # symlink + glib-compile-schemas (+ enable hint); bridges from container
# or, on the host directly:
just install-ext                  # symlink + glib-compile-schemas + gnome-extensions enable
```

Then on the host:

```sh
gnome-extensions enable local-gaze@eturkes.com
# Wayland (GNOME 50): LOG OUT and back in to load the extension.
#   (Alt+F2 'r' does NOT reload the Shell on Wayland.)

gnome-extensions disable local-gaze@eturkes.com   # disable later
```

The extension is **fail-closed**: off Wayland it sets `Supported=false` and exports nothing
actionable. The Quick Settings **"Gaze Control"** toggle is the kill switch (the `active`
gsetting is the single source of truth for `Enabled`); its panel icon is visible only while
active, as our own camera-in-use indicator.

## Host daemon: start / stop

Shipped as a **default-disabled** systemd **user** unit
(`systemd/local-gaze.service`, `WantedBy=graphical-session.target`). Even once started it
refuses to act until the extension reports `Enabled==true`.

```sh
just svc-install                  # symlink the unit + systemctl --user daemon-reload (host)

local-gaze enable                 # systemctl --user enable --now local-gaze.service (bridges from container)
local-gaze disable                # SetEnabled(false) then systemctl --user disable --now
```

Three independent kill paths (ADR-003): the Quick Settings toggle, `local-gaze disable`, and
the daemon honoring `SetEnabled(false)`; killing the process also fails closed.

With the default `ipc.require_token=true`, daemon ↔ extension calls need a shared `0600`
token. The daemon (and `local-gaze calibrate`) **auto-provision** it on startup; run
`local-gaze provision-token` to create it explicitly beforehand. The extension reads the same
`~/.local/state/local-gaze/token`. Set `ipc.require_token=false` to disable the token check.

## Dry-run + synthetic demo

`local-gaze demo` runs the **synthetic** backend through the real interpreter in **dry-run**
for a few seconds and prints the decided actions — no camera, no D-Bus actions. This is the
container-safe way to exercise the full decision pipeline:

```sh
local-gaze demo        # or: just demo
```

Dry-run more broadly (`[general] dry_run = true`) runs the full host pipeline but logs
decisions instead of dispatching D-Bus actions — used for safe threshold tuning.

## Calibration

`local-gaze calibrate` runs an interactive **3×3 grid** calibration (targets inset 10% from
the edges, `calibration/run.py:TARGETS`). For each target the extension shows a dot
(`ShowCalibrationTarget`), the daemon collects confident, spatially-stable raw gaze samples,
then hides the dot. A least-squares **affine** map (raw gaze → normalized screen `[0,1]²`)
is fit and saved `0600` to `~/.local/state/local-gaze/calibration.json` (ADR-009). At
runtime the map is applied after smoothing, before the dwell gate; with no calibration file
the raw (uncalibrated) gaze is used.

```sh
local-gaze calibrate   # host; needs the extension enabled + a camera
```

## Configuration

`~/.config/local-gaze/config.toml` (dir `0700`, file `0600`); missing keys fall back to
typed defaults (`config.py:DEFAULTS`, mirroring `docs/build-spec.md §3`). Notable knobs:
`general.backend` (`synthetic`|`openvino`), `general.enabled_default` (always `false` —
the daemon never auto-enables), `general.dry_run`, `ipc.require_token`, `gaze.dwell_ms` /
`gaze.stability_px`, `flick.v_on`/`v_off`/`refractory_ms`, `limits.max_actions_per_sec`,
`logging.log_gaze`. The gaze/flick thresholds are placeholders pending host tuning.

## CLI summary

```
local-gaze check               # preflight: session + bus reachable + extension enabled + deps
local-gaze probe               # host probe (openvino devices, NPU smoke, cameras); unverified in container
local-gaze run                 # start the daemon (host)
local-gaze enable | disable    # systemctl --user (un)install + run the unit (host)
local-gaze calibrate           # interactive gaze calibration (host)
local-gaze demo                # synthetic + dry-run; print decided actions (container-safe)
local-gaze install-extension   # symlink extension + compile schemas + enable hint
```

## Verification status

Honest split per `docs/environment-facts.md` — host capabilities are **never** asserted from
the container. As of 2026-06-15:

**Implemented (code complete):**
- Python package (`src/local_gaze/`): config, paths, session detection, logging, IPC
  schema + `dbus-fast` client, perception Protocol (synthetic / mock / openvino), interpret
  (smoothing / gestures / gaze / interpreter), calibration (fit / save / load / run), daemon,
  CLI. OpenVINO/cv2 imports are lazy.
- GNOME extension (`extension/`): D-Bus service, windows/workspace/overlay, Quick Settings
  kill switch, token + rate-limit, Wayland fail-closed guard, prefs.
- Host scripts: `host-probe`, `host-exec.sh`, `fetch-models.sh`; systemd unit; justfile.

**Container-tested (green in the container, no OpenVINO):**
- ruff + mypy clean; unit suite (config, types, smoothing, gestures, gaze, interpreter,
  calibration, session, synthetic, schema).
- **Live D-Bus IPC round-trip**: the real `ExtensionClient` against a real `dbus-fast` fake
  extension under `dbus-run-session` (`test_ipc.py`).
- `local-gaze demo` (synthetic + dry-run decision pipeline).

**Host-probe-tested (verified on the host):**
- OpenVINO 2026.2 imports; devices `['CPU','GPU','NPU']` (NPU = "Intel AI Boost").
- **NPU smoke verified** (2026-06-15): tiny static-shape model compiled + inferred on `NPU`
  (~97 ms compile / ~14.6 ms infer); `CACHE_DIR`/`CACHE_MODE` present. This is the exact
  check `local-gaze probe` reproduces.
- **Gaze chain on NPU verified** (2026-06-15): all four OMZ gaze models download (live URLs)
  and compile + infer on `NPU` with **no** CPU/GPU fallback — face-detection-retail-0004
  (216/21.8 ms), head-pose-estimation-adas-0001 (160/56.8), facial-landmarks-35-adas-0002
  (547/56.8), gaze-estimation-adas-0002 (202/52.6). Inputs already static; cold first-infers.
  (face-detect `DetectionOutput` did **not** force a fallback.)
- NPU driver present (`/dev/accel/accel0`, `intel_vpu`); `/dev/video0..3` enumerated.

**NOT yet hardware-validated (host-pending):**
- **Hand (MediaPipe) models on NPU**: TFLite→IR conversion (`scripts/fetch-models.sh`) plus
  per-op NPU compile (which ops force GPU/CPU fallback — e.g. `Interpolate`). The gaze chain
  is already NPU-verified above; only the hand path remains.
- **Model fetch + sha256 pinning** — `models/MANIFEST` ships `sha256=TODO`; first host
  `scripts/fetch-models.sh` run downloads, pins, and re-verifies.
- **Real-camera gaze/hand accuracy** and empirical tuning of dwell / flick thresholds.
- **GNOME Shell extension runtime** end-to-end: install + Wayland relogin + live
  focus-window / switch-workspace / overlay; OSD/overlay-above-fullscreen specifics.

Remaining host-only steps are the checklist in the `/session-prompt` slash command
(`.claude/commands/session-prompt.md`).

## License

MIT (`LICENSE`). Bundled/downloaded CV models are Apache-2.0 (see `models/MANIFEST`).

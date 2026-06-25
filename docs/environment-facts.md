# Environment Facts (probed 2026-06-15; dev-local container-shim addendum 2026-06-25)

Authoritative, machine-probed facts about THIS dev/runtime environment. Treat as
ground truth for design. Re-probe with `scripts/host-probe` before trusting at
runtime; values here are a snapshot, not a runtime contract.

## Topology

- Dev shell runs INSIDE a Distrobox/toolbx **Debian 13 (trixie)** container
  (podman 5.8, rootless). `id -un` = `eturkes`, uid 1000.
- The container mounts the host root at `/run/host`. Therefore:
  - Container path `/run/host/home/eturkes/Projects/local-gaze`
  - == Host path `/home/eturkes/Projects/local-gaze` (SAME files, two views).
  - `/home/eturkes/intel` -> `/var/home/eturkes/intel` (host uses
    `/var/home` with a `/home` symlink; openSUSE MicroOS/Aeon layout).
- Target runtime = **the HOST desktop session**, not the container.

## Container -> Host execution bridge

- `distrobox-host-exec` wraps `host-spawn` (v1.6.0), which calls
  `org.freedesktop.Flatpak.Development.HostCommand` on the host session bus.
- **CRITICAL GOTCHA**: `host-spawn` returns exit 127 with NO output when the
  current working directory does not exist on the HOST. The container cwd
  `/run/host/...` does NOT exist on the host, so naive `distrobox-host-exec`
  calls silently fail. **Fix: `cd /` (or to a host-valid path) before calling.**
  All container->host tooling MUST set a host-valid cwd first. See
  `scripts/host-exec.sh`.
- Host session bus is directly reachable from the container at
  `/run/host/run/user/1000/bus` (set `DBUS_SESSION_BUS_ADDRESS` +
  `XDG_RUNTIME_DIR` to the `/run/host/...` variants). GNOME Mutter and portal
  names are visible there. This is an alternative to host-spawn for pure D-Bus.

## Host (runtime target)

- Hostname `2in1-g10`. CPU **Intel(R) Core(TM) Ultra 7 268V** (Lunar Lake / Series 2).
- Session: `XDG_SESSION_TYPE=wayland`, `XDG_CURRENT_DESKTOP=GNOME`,
  `WAYLAND_DISPLAY=wayland-0`. **GNOME Shell 50.2** (gnome-extensions 50.2).
  NOTE: GNOME 50 postdates the model training cutoff -> verify extension APIs
  via web research, do not assume GNOME 45/46 patterns.
- **OpenVINO 2026.2.0** + **OpenVINO GenAI 2026.2.0** installed system-wide
  (`/usr/lib64/libopenvino*.so*`, python pkg at
  `/usr/lib64/python3.13/site-packages/openvino`). Importable from system
  `python3` (3.13.13) with no extra env.
- OpenVINO devices: **`['CPU', 'GPU', 'NPU']`**
  - CPU => Intel(R) Core(TM) Ultra 7 268V
  - GPU => Intel(R) Arc(TM) Graphics (iGPU)
  - **NPU => Intel(R) AI Boost**  (works; this is the NPU-first target)
  - VERIFIED host smoke (2026-06-15): compiled+inferred a tiny static-shape
    model (matmul+relu, [1,8]) on each device — `NPU: compile=97ms infer=14.6ms`,
    `GPU: compile=686ms infer=3.8ms`, `CPU: compile=47ms infer=3.5ms`, all OK.
    NPU exposes `CACHE_DIR`/`CACHE_MODE` (compiled-blob cache). This is the
    shape the host-probe NPU smoke test reproduces.
  - VERIFIED real CV models on NPU (2026-06-15): all four OMZ gaze models
    download (live URLs, 200) and compile+infer on the NPU with NO CPU/GPU
    fallback — face-detection-retail-0004 (compile 216ms / infer 21.8ms, SSD
    DetectionOutput did NOT block NPU), head-pose-estimation-adas-0001 (160/56.8),
    facial-landmarks-35-adas-0002 (547/56.8), gaze-estimation-adas-0002 (202/52.6).
    Inputs are already static (no reshape needed). Times are cold first-infers;
    CACHE_DIR + warmup + async pipelining lower steady-state. Hand (MediaPipe)
    models still need TFLite->IR conversion + per-op NPU validation.
- NPU driver present: `/dev/accel/accel0` (render group), `intel_vpu` kernel
  module loaded. `level-zero` user stack present (genai links it).
- Cameras: `/dev/video0..3` on host (UVC webcam, multiple nodes).
- Host has its own session bus; host `python3` is the externally-managed system
  interpreter (openSUSE). Do NOT pip-install into it. Use a venv with
  `--system-site-packages` to inherit system `openvino` while adding our deps.

## Container (dev/test target)

- System `python3` 3.13.5, `uv` 0.11.21, `node`, `pnpm`, `npx`. No `python` alias.
  Note: `uv` provisions the project `.venv` with its own managed CPython (3.14.x
  observed); `requires-python>=3.13` holds. The HOST daemon venv instead uses
  `--system-site-packages` to reach system openvino, so it stays on host 3.13.
- D-Bus: `dbus-run-session` + `dbus-daemon` present -> can spin an ISOLATED
  session bus for IPC tests (fake extension service vs. real daemon client).
  Container's own bus at `unix:path=/run/user/1000/bus`.
- ABSENT in container (by design / expected):
  - `openvino` (bare import fails: no numpy, no GPU/NPU userspace) -> backend
    must lazy-import; the portable suite mocks it. (Dev-local exception: an accel
    shim makes it real in-container -> see the testing-split † note + `CLAUDE.local.md`.)
  - `gjs`, `gnome-extensions`, GNOME Shell -> extension is HOST-validated only;
    container does static checks (eslint, gschema compile via
    `glib-compile-schemas` which IS present, JSON validation).
  - PyGObject (`gi`) not importable -> daemon D-Bus MUST use a pure-python lib
    (`dbus-fast`), not `pydbus`/`gi`.
  - `v4l2-ctl`. `/dev/video0..3` device nodes ARE visible in the container but
    treat camera capture as host-validated only; default to synthetic frames.
- Present and useful in container: `dbus-send`, `gdbus`, `glib-compile-schemas`,
  `jq`.

## Testing split (container vs host)

| Capability                         | Container | Host |
|------------------------------------|-----------|------|
| Python logic / interpreter / unit  | YES       | YES  |
| D-Bus client vs FAKE service       | YES (dbus-run-session) | YES |
| gschema compile, JSON/JS lint      | YES       | YES  |
| OpenVINO import / device select    | mock†     | YES  |
| NPU compile/inference smoke        | no†       | YES (host-probe) |
| Real camera capture                | NO (synthetic) | YES |
| GNOME Shell extension runtime      | NO        | YES  |
| End-to-end gaze/gesture -> desktop | NO        | YES  |

**The portable suite mocks OpenVINO; never let a mock backend pass as real
verification, and never claim camera/extension verification from inside the
container.**
† Dev-local exception (this box, machine-specific, not portable): a developer accel
shim makes real CPU/GPU/NPU OpenVINO work in-container -> see `CLAUDE.local.md`;
such in-container NPU runs are legitimate but get dated as dev-local in the proof
(verified 2026-06-25, OpenVINO 2026.2.1, all three devices compile+infer OK).

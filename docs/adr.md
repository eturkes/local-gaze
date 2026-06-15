# Architecture Decision Records — local-gaze

Format: each ADR = Decision / Rationale / Rejected. Synthesized 2026-06-15 from
`docs/research/*.md` + `docs/environment-facts.md`. `[H]` = needs HOST validation
(NPU/camera/GNOME-Shell), never claimed verified from the container.

Fixed identifiers (all ADRs assume these):
- D-Bus bus name `com.eturkes.LocalGaze`, object path `/com/eturkes/LocalGaze`,
  interface `com.eturkes.LocalGaze`.
- Extension UUID `local-gaze@eturkes.com`, gschema
  `org.gnome.shell.extensions.local-gaze`.

---

## ADR-001 — Compositor actions go through a GNOME Shell extension, not portals/CLI

**Decision.** A single GNOME Shell 50 extension (`local-gaze@eturkes.com`) is the
*only* component that enumerates windows, reads geometry, focuses windows, switches
workspaces, and draws calibration overlays. It exposes these as a narrow session-bus
D-Bus service the host daemon calls. No `wmctrl`/`xdotool`/portal is used for these
actions.

**Rationale.** GNOME 50 is Wayland-only `[H-doc]`; X11 automation tools (`xdotool`,
`wmctrl`) do not work. No xdg-desktop-portal interface exposes per-window geometry +
arbitrary focus + workspace switching for normal apps. Mutter's privileged window
model (`global.display.get_tab_list`, `Main.activateWindow`, `Meta.Workspace.activate`,
`Main.layoutManager`) is reachable only from inside the Shell process. An extension is
the supported, in-process API for exactly these operations and lets us keep one tight
trust boundary.

**Rejected.** (a) X11 tools — Wayland-incompatible. (b) `org.gnome.Shell.Eval` — removed/
locked-down and is the exact arbitrary-code surface the architecture forbids. (c)
xdg-desktop-portal RemoteDesktop/ScreenCast — gives synthetic input + capture, not
window enumeration/geometry/targeted focus; adds an interactive grant per session.
(d) libei/input emulation — emits raw input events, cannot resolve "the window under
this gaze point" without the compositor's stacking list.

---

## ADR-002 — IPC is a session-bus D-Bus interface with a same-UID trust boundary + optional accident-token

**Decision.** Daemon→extension IPC is session-bus D-Bus on the fixed name/path/interface
above. The interface is method/property/signal-only with **no eval/exec**. The real
security boundary is the **OS user account** (session bus authenticates UID only). A
per-call optional `token` string (trailing arg on every method) is compared against a
`0600` state file; empty string disables the check (`require_token=false`). Token is
**defense-in-depth only** (prevents accidental cross-app calls, adds intent/audit), never
access control.

**Rationale.** `[H-doc]` The session bus authenticates only the Unix UID; any same-user
process can call any method, and can also `read()` the token file. So the token cannot be
sold as access control — documenting it as such would be dishonest. It still cheaply
rejects stray/fuzzing callers and gives an audit/pairing signal. A trailing-arg token
works with the simplest GJS export (`wrapJSObject`), which does **not** expose the sender;
we therefore do not depend on `get_sender()` for auth.

**Rejected.** (a) Token as access control — false security claim (same-UID read). (b)
Low-level `Gio.DBusConnection.register_object` for `get_sender()` auth — sender names
aren't a trust boundary; reserve it only if per-sender rate-limit bucketing is later
wanted. (c) Private AF_UNIX socket — re-implements D-Bus marshalling/introspection for
no isolation gain over the session bus (same UID either way). (d) System bus — wrong
scope (per-user desktop), needs polkit, more attack surface.

---

## ADR-003 — Daemon is a single asyncio process, default-disabled, fail-closed

**Decision.** One Python process (`local_gaze.daemon`) runs a single asyncio event loop
hosting: camera capture → perception backend → interpretation (smoothing/gesture/gaze) →
D-Bus client calls. Shipped as a **default-disabled** systemd *user* unit
(`WantedBy=graphical-session.target`). The daemon exits non-zero when
`XDG_SESSION_TYPE!=wayland` or `XDG_CURRENT_DESKTOP` lacks `GNOME`, and refuses to act
unless the extension reports `Enabled==true`. Three kill paths: GNOME quick-toggle, CLI
`disable`, daemon honoring `SetEnabled(false)`; killing the process also fails closed.

**Rationale.** The workload is one camera at modest FPS feeding sequential inference —
no need for multiprocess/threads beyond OpenVINO's internal async queue. A single loop is
the simplest correct model (KISS), keeps state coherent, and makes the kill switch
trivial (stop emitting). Default-disabled + session guards satisfy "fail closed on
non-GNOME-Wayland" so an accidental enable cannot grab the camera. `[H]` exact session
detection (combine env + `loginctl show-session self` + extension `Supported`) is
host-validated.

**Rejected.** (a) Multiprocess capture/inference/IPC — premature; serialization overhead,
harder shutdown, no measured need. (b) Threads for the pipeline — OpenVINO `AsyncInferQueue`
already overlaps inference with capture; extra threads invite GIL/lifetime bugs. (c)
Enabled-by-default — violates fail-closed/privacy. (d) Persistent always-grabbed camera —
privacy risk; capture starts only when enabled.

---

## ADR-004 — Perception backends behind a `PerceptionBackend` Protocol (synthetic / mock / openvino)

**Decision.** Define a typed `PerceptionBackend` Protocol (`perception/base.py`) returning
a single `PerceptionResult` per frame. Three implementations: `synthetic` (deterministic
scripted gaze/hand, no deps — container default + `demo`), `mock` (test-injected
sequences for assertions), `openvino` (real CV, **lazy-imports openvino+cv2** only inside
its module). Backend is selected by config; container never constructs `openvino`.

**Rationale.** The container has no openvino/opencv/camera, yet all interpretation,
calibration, gating, and IPC logic must be unit-testable there (env-facts testing split).
A Protocol with a synthetic backend lets the entire decision pipeline run and be asserted
with zero host deps, while the openvino backend stays an isolated, lazy-imported seam.
This also makes `demo` (synthetic dry-run) a first-class container verification path.

**Rejected.** (a) Hard-import openvino in the package — breaks container `uv sync`/mypy/
pytest (env-fact). (b) ABC base class — Protocol gives structural typing without forcing
inheritance, cleaner for the synthetic/mock stand-ins. (c) Branching on a global flag
instead of polymorphism — scatters host-only code through the hot path; harder to test.

---

## ADR-005 — OpenVINO **Runtime** for CV, NPU-first with explicit CPU fallback; GenAI is a future seam

**Decision.** Use OpenVINO **Runtime** (`ov.Core`/`compile_model`/`InferRequest`/
`AsyncInferQueue`) for all gaze+hand inference. Device selection is **explicit
NPU→GPU→CPU** try/except with `PERFORMANCE_HINT=LATENCY`, logging the chosen device per
model. Models are reshaped to **fully static, batch=1** before NPU compile. Use
`CACHE_DIR` (set on Core *before* compile) for blob caching; verify with
`LOADED_FROM_CACHE` `[H]`. OpenVINO **GenAI** is explicitly *not* used now; a future
`VlmBackend` (`openvino_genai.VLMPipeline`) may be added off the per-frame hot path.

**Rationale.** Gaze/hand models are single-shot regressors/detectors: one image tensor →
one numpy result. GenAI is an autoregressive token-loop API (LLM/VLM/Whisper/SD) with
≥100 ms/token cost and no per-frame latency path — wrong tool. Explicit device selection
(not `AUTO`) is chosen because `AUTO` excludes NPU from its default candidates and runs the
*first* inference on CPU while the accelerator compiles, muddying which device served a
frame; explicit try/except lets us log + fail-soft deterministically. NPU mandates static
shapes; `CACHE_DIR` cuts the slow first compile. `[H]` whether each model compiles on NPU
and INT8 is supported.

**Rejected.** (a) OpenVINO GenAI for CV — no per-frame latency path. (b) `AUTO:NPU,CPU` —
nondeterministic first-frame device + hidden NPU exclusion; less control/logging. (c)
Hand-rolled `export_model`/`import_model` blobs — docs mark them dev-only (version/platform
specific); `CACHE_DIR` is the supported path. (d) Dynamic shapes — NPU rejects them for CV
graphs (the 2025.3 dynamic relaxation is LLM-pipeline-only).

---

## ADR-006 — CV models: OMZ classic gaze chain + in-house-converted MediaPipe hand, with declared fallbacks

**Decision.** **Gaze primary** = OMZ 4-model chain (`face-detection-retail-0004` →
`head-pose-estimation-adas-0001` + `facial-landmarks-35-adas-0002` →
`gaze-estimation-adas-0002`), FP16 IR, Apache-2.0, static by construction. **Hand primary**
= MediaPipe `palm_detection_full` (192×192) + `hand_landmark_full` (224×224, 21 kpts),
converted in-house from official `.tflite` via OpenVINO 2026 `ov.convert_model`, reshaped
static. **Flick** uses no extra model (normalized hand-center velocity + hysteresis).
Declared fallbacks: gaze → L2CS/MobileGaze single-net; hand → palm-detect-only centroid,
or PINTO_model_zoo 033 pre-converted IR. Onboarding via `scripts/fetch-models.sh`:
TLS download → pinned sha256 → `models/MANIFEST` (url, sha256, license, revision); never
exec downloaded payloads.

**Rationale.** OMZ gives ready static IR + permissive license + tiny size (~16 MB FP16),
ideal for NPU and adequate for dwell-focus (gaze MAE ~7° ≈ a few-cm cursor on a desktop).
MediaPipe is the best open hand pipeline and OMZ has no hand-landmark model; OpenVINO 2026's
native TFLite frontend removes the legacy PINTO/Docker toolchain. Flick needs only
horizontal motion, so model-free velocity logic is simplest. Fallbacks de-risk per-model
NPU op rejection. `[H]` per-model NPU compile, real-camera accuracy, flick thresholds, and
all sha256 are host-filled.

**Rejected.** (a) `omz_downloader` — depends on removed `openvino-dev`; fetch frozen 2023.0
storage URLs directly. (b) `geaxgx/openvino_hand_tracker` `.blob` — Myriad/OV-2021.2, not
NPU-usable (keep only for normalization params). (c) MediaPipe FaceMesh-iris for gaze —
heavier conversion; reserve only if landmarks-35 eye crops prove too coarse `[H]`. (d)
NPU-compiling NMS/anchor decode — unsupported; post-process stays host numpy.

---

## ADR-007 — Host-probe strategy: compile+infer a tiny NPU model; honor the host-spawn cwd gotcha

**Decision.** `scripts/host-probe` (invoked by `local-gaze probe`) runs **on the host**:
imports openvino, enumerates `core.available_devices`, and proves NPU usability by
**compiling and running one tiny static model on `"NPU"`** (not merely checking `"NPU" in
available_devices`); also enumerates `/dev/video*`. It writes JSON. From inside the
container the same command reports every host-only capability as `"unverified"`, never a
false positive. All container→host shells first `cd /` (host-valid cwd) because `host-spawn`
returns exit 127 with no output when the container cwd (`/run/host/...`) doesn't exist on
the host.

**Rationale.** Device listing ≠ usability (driver/op support can still fail compile); a
real compile+infer is the only honest NPU check. The host-spawn cwd gotcha (env-facts) is a
silent failure mode that must be encoded once in `scripts/host-exec.sh` and reused. The
container must never assert NPU/camera health (env-facts).

**Rejected.** (a) Trusting `available_devices` alone — false positive when an op is rejected
at compile. (b) Probing from the container — visible `/dev/accel`,`/dev/video` nodes mislead;
no openvino present. (c) Naive `distrobox-host-exec` without `cd /` — silent exit 127.

---

## ADR-008 — Container vs host testing split: logic+IPC in container, NPU/camera/extension host-only

**Decision.** CI-style container checks (`just check`) cover: Python unit logic, mypy/ruff,
and a **live D-Bus IPC round-trip** against a *fake* extension (`tests/fake_extension.py`, a
real `dbus-fast` `ServiceInterface` exporting the canonical interface) under
`dbus-run-session`. openvino/camera/GNOME-Shell are mocked or stubbed in-container and
validated **only** on the host (probe + manual extension relogin + e2e). Tests must run
green with no openvino installed.

**Rationale.** Env-facts fix this split: the container lacks openvino, PyGObject, GNOME
Shell, and real camera. Faking only the GNOME side while running the *real* client lib over
the *real* wire protocol gives high-value IPC coverage without GNOME. Mocking the backend
keeps the decision pipeline fully testable. Overclaiming host capabilities from the container
is explicitly forbidden.

**Rejected.** (a) Skipping IPC tests until host — loses cheap, high-value protocol coverage.
(b) Mocking the D-Bus layer itself — wouldn't catch dbus-fast snake_case/signature mistakes.
(c) Requiring openvino in CI — breaks container env (env-fact).

---

## ADR-009 — Calibration + action gating: affine gaze map, smoothing, dwell, hysteresis, debounce, rate-limit

**Decision.** Calibration shows N on-screen targets (extension `ShowCalibrationTarget`),
collects raw gaze, and fits a small **affine/polynomial map** (raw gaze → normalized screen
`[0,1]²`), persisted to `~/.local/state/local-gaze/calibration.json` (0600). Runtime gating:
**gaze** = EMA/One-Euro smoothing → spatial-stability + per-frame confidence → **dwell**
(≥ ~300–500 ms continuous fixation) before `FocusWindowAt`; **flick** = hand-center velocity
with **hysteresis** (arm at |vx|>V_on, fire on sign, disarm until |vx|<V_off) +
**refractory/debounce** (≥ ~600 ms, require return-to-neutral) before `SwitchWorkspace`. A
**two-layer rate limit** (daemon global actions/sec ceiling + extension-side token-bucket
backstop) caps action frequency. `--dry-run` logs decisions and suppresses D-Bus actions.
All thresholds live in config; concrete values are `[H]` empirically tuned.

**Rationale.** Raw appearance-gaze is noisy and per-user/per-camera biased; an affine map +
smoothing + dwell converts it into stable focus intent and rejects saccade jitter. Flick
hysteresis+refractory rejects double-fires and micro-jitter. Defense-in-depth rate limiting
(both sides) bounds worst-case action spam even if one layer mis-tunes. Dry-run enables safe
tuning without moving windows. Keeping thresholds in config (not code) lets host tuning
proceed without edits.

**Rejected.** (a) Raw gaze → focus with no smoothing/dwell — unusable focus thrash. (b)
Per-frame focus (no dwell) — fires on every saccade. (c) Single-sided rate limit — one
mis-tune removes the ceiling. (d) Hard-coded thresholds — blocks host tuning; needs code
edits per camera.

---

## ADR-010 — Language/runtime: Python 3.13 + uv (src layout), dbus-fast, argparse, hatchling

**Decision.** Daemon is Python ≥3.13 managed by **uv** (`src/` layout, hatchling backend).
D-Bus uses **dbus-fast** (async, pure-python) since PyGObject is absent in the container;
jeepney is the documented dev-group fallback. openvino+opencv are an **optional `[host]`
extra**, never a hard dep. CLI is stdlib **argparse** with lazy per-subcommand imports.
Host runtime uses a `--system-site-packages` venv to inherit system openvino 2026.2; venv
numpy is left **unpinned/system-inherited** to match openvino's ABI. systemd `ExecStart`
uses the absolute `.venv-host/bin/python`. Dev runner = `justfile`.
**Correction (host-verified 2026-06-15):** the host has **no `uv`**, so `host-venv` uses the
**system `python3 -m venv --system-site-packages .venv-host`** (verified to expose system
openvino 2026.2); the container dev env still uses `uv`. The systemd unit also sets
`Environment=PYTHONPATH=` so a host shell's standalone openvino_genai bundle cannot shadow
the venv's system openvino.

**Rationale.** uv is the user's standard and gives fast, reproducible, group/extra-aware
envs. dbus-fast is mandatory (no `gi` in container) and is the actively-maintained pure-py
option with cp3.14 wheels. argparse keeps the container env lean (no click/rich/typer).
`--system-site-packages` is the only way to inherit the externally-managed system openvino
without pip-installing into the system interpreter; leaving numpy unpinned avoids ABI
shadowing of the numpy openvino was built against. The absolute venv interpreter avoids the
documented uv `sys.executable` crash.

**Rejected.** (a) PyGObject/pydbus D-Bus — not importable in the container (env-fact). (b)
openvino as a hard dep — breaks container install/test. (c) typer/click — extra deps for no
KISS benefit. (d) Pinning numpy in the venv — risks shadowing system numpy → openvino ABI
errors. (e) Relying on `sys.executable` in the unit — base interpreter, no packages → crash.

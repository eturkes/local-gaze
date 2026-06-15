---
description: Bootstrap a fresh local-gaze session — no args steers to the next roadmap item; args are the steering
argument-hint: [optional steering text]
---
You are continuing **local-gaze**: local, NPU-first eye-tracking + hand-gesture control for
**GNOME 50 Wayland**, on an Intel NPU via OpenVINO. Repo root (your cwd) is the project's only
root-level directory; constrain all work to it. You run inside a Distrobox **container**; the
**runtime target is the HOST desktop** (same files, two views:
`/run/host/home/<you>/Projects/local-gaze` == host `/home/<you>/Projects/local-gaze`).

## Read first (the authoritative contract + truth)
- `docs/build-spec.md` — THE contract: exact module signatures, the D-Bus interface XML, config
  schema, repo layout, the test list, and the canonical check command. Match it byte-for-byte
  where it says so.
- `docs/adr.md` — 10 ADRs (rationale + rejected alternatives).
- `docs/environment-facts.md` — probed env truth (container vs host split, the host-spawn `cd /`
  gotcha, verified NPU smoke numbers). **Never claim NPU/camera/extension verification from
  inside the container.**
- `docs/security-privacy.md` — the security review (token is defense-in-depth, fail-closed table,
  file perms, model integrity).
- Root `CLAUDE.md` — conventions: Python ≥3.13, `from __future__ import annotations` in every
  `.py`, full type hints, line length ≤100, lazy openvino/cv2 imports, KISS/UNIX, minimal
  comments, scoped commits, token efficiency.
- `README.md` `## Verification status` — **the authoritative, dated record of what is verified**;
  re-confirm any host claim with the probe before trusting it.

## Re-probe before trusting anything host-side
```sh
scripts/host-probe --human                 # on the host
local-gaze probe                           # from the container (bridges via scripts/host-exec.sh)
```
`supported` is the verdict; in-container every host-only field is `"unverified"` by design.

## Container-safe gate — must stay green (no OpenVINO needed)
```sh
just check
# == uv sync && uv run ruff check . && uv run mypy src && \
#    uv run dbus-run-session -- pytest -q
```
Per-file while iterating: `uv run ruff check <file> && uv run python -m py_compile <file>`.
Keep OpenVINO/camera/GNOME-Shell deps off the container path — their imports stay lazy.
Container-safe end-to-end of the decision pipeline: `local-gaze demo` (synthetic + dry-run).

## Verification baseline (README `## Verification status` is authoritative)
Container gate green: ruff+mypy, unit suite, the live D-Bus IPC round-trip, `local-gaze demo`.
Host-probe-verified 2026-06-15: OpenVINO 2026.2; devices `['CPU','GPU','NPU']` (NPU = "Intel AI
Boost"); NPU smoke OK (~97 ms compile / ~14.6 ms infer); the **full OMZ gaze chain compiles +
infers on NPU with no CPU/GPU fallback**; driver `/dev/accel/accel0` + `intel_vpu`;
`/dev/video0..3`.

## Host-only remaining steps (the roadmap)
Each runs on the host; bridge from the container with `scripts/host-exec.sh <cmd>` (it `cd /`
first to avoid the silent exit-127 gotcha). **The next roadmap item is the first unchecked box,
top to bottom.** When you finish and host-validate a step, check its box here.

- [x] **1) Host venv** (inherits system OpenVINO 2026.2) — DONE 2026-06-15:
  ```sh
  # `just`/`uv` are container-only (the host has neither); run the recipe's raw
  # commands ON THE HOST, bridged from the container:
  scripts/host-exec.sh sh -c 'cd ~/Projects/local-gaze \
    && python3 -m venv --system-site-packages .venv-host && .venv-host/bin/pip install -e .'
  ```
  Verified: openvino 2026.2 + venv numpy 2.4.6 interop (`scripts/host-probe` NPU smoke
  `correct=true` under `.venv-host/bin/python`); installed `local-gaze probe` runs natively.
- [x] **2) Fetch + pin models** — DONE 2026-06-15: downloaded 8 OMZ gaze FP16 files + the
  MediaPipe `.task`, pinned 9 `sha256` digests, converted TFLite → OpenVINO IR
  (`models/hand/{palm,landmark}.{xml,bin}`):
  ```sh
  # `just` is container-only; run the script ON THE HOST, bridged from the container:
  scripts/host-exec.sh sh -c 'cd ~/Projects/local-gaze && sh scripts/fetch-models.sh'
  git add models/MANIFEST    # commit the now-pinned digests (models/ itself is gitignored)
  ```
  Note: the `.task` bundle members are `hand_detector.tflite` + `hand_landmarks_detector.tflite`
  (not the standalone `*_full` names) — fixed in `scripts/fetch-models.sh` + build-spec §6.
  Converted IR inputs already static: palm=[1,192,192,3], landmark=[1,224,224,3].
- [x] **3) Per-model NPU compile validation** — DONE 2026-06-15: via `compile_with_fallback`
  (device_order `[NPU,GPU,CPU]`), **all 6 models compile+infer on NPU, no CPU/GPU fallback,
  zero NPU-unsupported ops** — incl. both hand models (`palm` 815 ops / `landmark` 548 ops);
  the expected `Interpolate` fallback did NOT occur. `CACHE_DIR` caching verified
  (`LOADED_FROM_CACHE=True`, ~16–29 ms warm vs 235–709 ms cold). Fixed the backend hand reshape
  key (`input`→`input_1`, the real `convert_model` input name) in
  `perception/openvino_backend.py`. Re-validate any time via `local-gaze run` logs
  (`openvino backend started; devices=...`).
- [ ] **4) Install + enable the extension, then RELOG IN** (Wayland: Alt+F2 'r' does NOT reload):
  ```sh
  local-gaze install-extension                  # symlink + glib-compile-schemas + enable hint
  gnome-extensions enable local-gaze@eturkes.com
  # LOG OUT and back in.
  gnome-extensions info local-gaze@eturkes.com  # expect State: ACTIVE
  ```
  Then validate the live D-Bus surface against the REAL extension (not the fake):
  `gdbus call --session --dest com.eturkes.LocalGaze --object-path /com/eturkes/LocalGaze
  --method com.eturkes.LocalGaze.Ping ""` (empty token works only when `require-token=false`;
  otherwise pass the provisioned token). Confirm `GetWindows`/`GetStatus`, fail-closed
  (`Supported`, Enabled gate), the Quick Settings "Gaze Control" kill switch + panel icon,
  `FocusWindowAt`, `SwitchWorkspace` wrap, and overlay/OSD behavior (incl. above-fullscreen).
- [ ] **5) Real-camera gaze/flick tuning** — run `local-gaze run` with `[general] dry_run = true`
  and a webcam; observe decisions; tune `gaze.dwell_ms`/`gaze.stability_px`/`gaze.min_confidence`
  and `flick.v_on`/`v_off`/`refractory_ms`/`min_present_frames` in
  `~/.config/local-gaze/config.toml`. Then `local-gaze calibrate` (3×3 grid) and validate
  end-to-end with dry-run off:
  ```sh
  just svc-install
  local-gaze enable   # default-disabled unit; daemon no-ops until the toggle is ON
  ```

## Rules of engagement
- Keep the container gate green after every change; commit each cohesive unit with one scoped
  commit (end messages with the `CLAUDE.md` Co-Authored-By trailer).
- Match `docs/build-spec.md` signatures/XML/identifiers exactly; the D-Bus XML is byte-identical
  in `extension/lib/service.js` and `src/local_gaze/ipc/schema.py`.
- Stop and ask the user when blocked, ambiguous, or a remote/host action is needed that you
  cannot safely perform (the user handles anything touching the remote repo).
- When you finish a host-validated step: **check its box** in "Host-only remaining steps" above,
  update README's `## Verification status`, and tell the user.

## This session
$ARGUMENTS

If the line above this paragraph is **empty**, no custom steering was given: follow "Read first"
and "Re-probe", then drive the **next roadmap item** — the first unchecked `[ ]` box under
"Host-only remaining steps", top to bottom — to host-validated completion. If every box is
checked, tell the user the roadmap is complete and there is no remaining host work.

If the line is **non-empty**, treat it as the authoritative steering for this session and pursue
it (the roadmap above remains your background context).

# Roadmap — local-gaze

Contract: `docs/build-spec.md`. Authoritative dated verification proof: README §"Verification status".
This file tracks step status + next actions only — defer architecture to build-spec, proof to README.

Container baseline (re-verify each session with `just check`): GREEN — ruff + mypy + pytest pass on
synthetic/mock backends. Steps below run on the openSUSE host outside the Distrobox container, where
`just`/`uv` are absent and the real NPU/camera/GNOME-Shell live; bridge per `docs/environment-facts.md`.

- [x] 1 Host runtime venv (`.venv-host`, system-site OpenVINO) — `2724263`
- [x] 2 Fetch + pin models; hand `.task` → OpenVINO IR — `363c35d`
- [x] 3 Per-model NPU compile + infer (all 6 models, production path, no CPU fallback) — `0280f24`
- [ ] 4 Install + enable extension, RELOG IN, validate live D-Bus
- [ ] 5 Real-camera tuning + calibrate + enable service  ← terminal step

## Step 4 — extension + live D-Bus  [host · GNOME 50 Wayland]
```sh
local-gaze install-extension                 # symlink + glib-compile-schemas + enable hint
gnome-extensions enable local-gaze@eturkes.com
```
RELOG IN (log out/in) — Wayland ignores Alt+F2 'r'; the Shell must restart to load the extension.
Validate the REAL surface (not `tests/fake_extension.py`) on bus `com.eturkes.LocalGaze`
(path `/com/eturkes/LocalGaze`): `Ping`, `GetStatus`, `GetWindows`, `FocusWindowAt`,
`SwitchWorkspace` (wrap-around), overlay/OSD incl. above-fullscreen. An empty token authenticates
only when `require-token=false`; otherwise run `local-gaze provision-token` first. Confirm the
Quick Settings "Gaze Control" kill-switch + panel icon and fail-closed gates (Supported/Enabled).

## Step 5 — camera tuning + service  [host]
`local-gaze run` with `[general] dry_run=true` + a webcam; observe decisions; tune in
`~/.config/local-gaze/config.toml`: `gaze.dwell_ms`/`stability_px`/`min_confidence` and
`flick.v_on`/`v_off`/`refractory_ms`/`min_present_frames`. Then `local-gaze calibrate` (3×3 grid)
and validate end-to-end with dry-run off:
```sh
just svc-install
local-gaze enable     # unit ships disabled; daemon no-ops until the toggle is ON
```
Done ⇒ end-to-end live on real hardware; no further roadmap steps.

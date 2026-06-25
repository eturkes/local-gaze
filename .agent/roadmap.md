# Roadmap — local-gaze

Contract: `docs/build-spec.md`. Authoritative dated verification proof: README §"Verification status".
Milestone ledger + active-milestone detail only — architecture defers to build-spec, proof to README.

Container baseline (re-verify each session with `just check`): GREEN — ruff + mypy + pytest pass on
synthetic/mock backends. M2+ run on the openSUSE host OUTSIDE the Distrobox container, where `just`/`uv`
are absent and the real camera + GNOME-Shell live (the NPU now ALSO runs in-container via the dev-local
shim — `CLAUDE.local.md`); bridge per `docs/environment-facts.md`. That host surface (camera + Shell) is
the standing gate on M2 and M3 — confirm it functionally before planning either.

## Ledger
- M1 container + inference baseline — DONE
  - u1 host runtime venv (`.venv-host`, system-site OpenVINO) — `2724263`
  - u2 fetch + pin models; hand `.task` → OpenVINO IR — `363c35d`
  - u3 per-model NPU compile + infer (all 6 models, production path, no CPU fallback) — `0280f24`
- M2 extension + live D-Bus — UNPLANNED, gated:host  ← active
- M3 camera tuning + service — UNPLANNED, gated:host  (terminal milestone)

## M2 — extension + live D-Bus  [host · GNOME 50 Wayland]  ← active
Standing block: the host surface (real camera + GNOME Shell/Wayland session, outside the container) is
unavailable here; the NPU is no longer part of this gate — it runs in-container via the dev-local shim
(`CLAUDE.local.md`). PLANNING confirms the host surface functionally, then splits the scope below into units.
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

## M3 — camera tuning + service  [host] (terminal)
Scope: `local-gaze run` with `[general] dry_run=true` + a webcam; observe decisions; tune in
`~/.config/local-gaze/config.toml`: `gaze.dwell_ms`/`stability_px`/`min_confidence` and
`flick.v_on`/`v_off`/`refractory_ms`/`min_present_frames`. Then `local-gaze calibrate` (3×3 grid)
and validate end-to-end with dry-run off:
```sh
just svc-install
local-gaze enable     # unit ships disabled; daemon no-ops until the toggle is ON
```
Done ⇒ end-to-end live on real hardware; no further milestones.

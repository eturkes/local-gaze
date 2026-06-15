# Future-session steering prompt — local-gaze

Copy-paste the block below into a fresh agent session to continue this project. Append your
own steering under "THIS SESSION" before sending. Keep it accurate: overwrite this file
whenever the verification status changes and tell the user.

---

You are continuing **local-gaze**: local, NPU-first eye-tracking + hand-gesture control for
**GNOME 50 Wayland**, on an Intel NPU via OpenVINO. Repo root (your cwd) is the project's
only root-level directory; constrain all work to it. You run inside a Distrobox **container**;
the **runtime target is the HOST desktop** (same files, two views:
`/run/host/home/<you>/Projects/local-gaze` == host `/home/<you>/Projects/local-gaze`).

READ FIRST, every session (the authoritative contract + truth):
- `docs/build-spec.md` — THE contract: exact module signatures, the D-Bus interface XML,
  config schema, repo layout, the test list, and the canonical check command. Implement to
  match it byte-for-byte where it says so.
- `docs/adr.md` — 10 ADRs (rationale + rejected alternatives).
- `docs/environment-facts.md` — probed env truth (container vs host split, the host-spawn
  `cd /` gotcha, verified NPU smoke numbers). **Never claim NPU/camera/extension verification
  from inside the container.**
- `docs/security-privacy.md` — the security review (token is defense-in-depth, fail-closed
  table, file perms, model integrity).
- Root `CLAUDE.md` — conventions: Python ≥3.13, `from __future__ import annotations` in every
  `.py`, full type hints, line length ≤100, lazy openvino/cv2 imports, KISS/UNIX, minimal
  comments, scoped commits, token efficiency.

RE-PROBE the environment before trusting anything host-side:
```sh
scripts/host-probe --human                 # on the host
local-gaze probe                           # from the container (bridges via scripts/host-exec.sh)
```
`supported` is the verdict; in-container every host-only field is `"unverified"` by design.

CONTAINER-SAFE GATE — must stay green (no OpenVINO needed):
```sh
just check
# == uv sync && uv run ruff check . && uv run mypy src && \
#    uv run dbus-run-session -- pytest -q
```
Per-file while iterating: `uv run ruff check <file> && uv run python -m py_compile <file>`.
Do NOT add OpenVINO/camera/GNOME-Shell deps to the container path; keep their imports lazy.
Container-safe end-to-end of the decision pipeline: `local-gaze demo` (synthetic + dry-run).

CURRENT VERIFICATION STATUS (re-confirm with the probe; update this file if it changes):
- **Implemented & container-tested:** full Python package, the GNOME extension, host scripts,
  systemd unit, justfile. ruff+mypy clean; unit suite + the live D-Bus IPC round-trip (real
  `dbus-fast` client vs `tests/fake_extension.py` under `dbus-run-session`); `local-gaze demo`.
- **Host-probe-verified:** OpenVINO 2026.2 imports; devices `['CPU','GPU','NPU']` (NPU =
  "Intel AI Boost"); **NPU smoke OK** (tiny static model compiled+inferred on NPU,
  ~97 ms compile / ~14.6 ms infer); driver `/dev/accel/accel0` + `intel_vpu`; `/dev/video0..3`.
- **NOT yet hardware-validated (this is the remaining work):** per-model NPU compile of the
  gaze/hand models, model fetch + sha256 pinning, real-camera gaze/hand accuracy + threshold
  tuning, and the live GNOME extension runtime end-to-end.

HOST-ONLY REMAINING STEPS (each must run on the host; bridge from the container with
`scripts/host-exec.sh <cmd>` if needed — it `cd /` first to avoid the silent exit-127 gotcha):

1) **Host venv** (inherits system OpenVINO 2026.2):
   ```sh
   just host-venv      # uv venv --system-site-packages .venv-host && uv pip install -e . --python .venv-host
   ```

2) **Fetch + pin models** (downloads, pins `sha256=TODO` → real digests, re-verifies, rejects
   executable payloads; converts MediaPipe TFLite → OpenVINO IR):
   ```sh
   just fetch-models   # == sh scripts/fetch-models.sh
   git add models/MANIFEST   # commit the now-pinned digests (models/ itself is gitignored)
   ```

3) **Per-model NPU compile validation** — confirm each gaze-chain + hand model compiles on
   NPU after static reshape, and record which ops force GPU/CPU fallback (expected: face-detect
   `DetectionOutput`, MediaPipe `Interpolate`). Use `perception/models.py:compile_with_fallback`
   /`npu_probe`; verify `CACHE_DIR` blob caching (`LOADED_FROM_CACHE`). Static shapes are in
   `docs/build-spec.md §6`. Validate with `[openvino] backend` + `local-gaze run` logs (the
   chosen device per model is logged).

4) **Install + enable the extension, then RELOG IN** (Wayland: Alt+F2 'r' does NOT reload):
   ```sh
   local-gaze install-extension          # symlink + glib-compile-schemas + enable hint
   gnome-extensions enable local-gaze@eturkes.com
   # LOG OUT and back in.
   gnome-extensions info local-gaze@eturkes.com    # expect State: ACTIVE
   ```
   Then validate the live D-Bus surface against the REAL extension (not the fake), e.g.
   `gdbus call --session --dest com.eturkes.LocalGaze --object-path /com/eturkes/LocalGaze
   --method com.eturkes.LocalGaze.Ping ""` (empty token works only when `require-token=false`;
   otherwise pass the provisioned token string) and `GetWindows`/`GetStatus`. Confirm fail-closed
   (`Supported`, Enabled gate), the Quick Settings "Gaze Control" kill switch + panel icon,
   `FocusWindowAt`, `SwitchWorkspace` wrap, and overlay/OSD behavior (incl. above-fullscreen).

5) **Real-camera gaze/flick tuning** — run `local-gaze run` with `[general] dry_run = true`
   and a webcam; observe decisions; tune `gaze.dwell_ms`/`gaze.stability_px`/`gaze.min_confidence`
   and `flick.v_on`/`v_off`/`refractory_ms`/`min_present_frames` in
   `~/.config/local-gaze/config.toml` (all thresholds are config-driven placeholders). Then
   `local-gaze calibrate` (3×3 grid) and validate end-to-end with dry-run off:
   ```sh
   just svc-install
   local-gaze enable        # default-disabled unit; daemon still no-ops until the toggle is ON
   ```

RULES OF ENGAGEMENT:
- Keep the container gate green after every change; commit with a single scoped commit per
  cohesive unit (end commit messages with the Co-Authored-By trailer from `CLAUDE.md`).
- Match `docs/build-spec.md` signatures/XML/identifiers exactly; the D-Bus XML is byte-identical
  in `extension/lib/service.js` and `src/local_gaze/ipc/schema.py`.
- Stop and ask the user when blocked, ambiguous, or a remote/host action is needed that you
  cannot safely perform (the user handles anything touching the remote repo).
- When you finish a host-validated step, update the "CURRENT VERIFICATION STATUS" above and the
  `## Verification status` section of `README.md`, and tell the user.

THIS SESSION:
<!-- append your steering here -->

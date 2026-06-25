# Security & Privacy Review — local-gaze

Security review note for the implemented system. Scope: same-user desktop automation
driven by an on-device camera ML pipeline. Threat-model rationale is ADR-002 / ADR-003 /
ADR-009 and the lane research `docs/research/safety-security.md`; this file states what the
**shipped code** does and its honest limits. `[H]` = host-only validation pending.

The real trust boundary is the **OS user account**. The session bus authenticates the Unix
UID only — every control below hardens against *accidents, fuzzing, and misuse*, not against
a hostile process already running as this user. We claim nothing stronger.

## 1. D-Bus attack surface (narrow, no eval)

The daemon→extension channel is the fixed session-bus interface `com.eturkes.LocalGaze`
(bus name / object path / interface in `docs/build-spec.md §2`). Properties: it is
**method / property / signal only — there is NO `Eval`/`Exec`/arbitrary-code method**
(ADR-001 rejects `org.gnome.Shell.Eval` precisely because it is that surface). The full
method set is `Ping`, `GetStatus`, `GetWindows`, `SetEnabled`, `SwitchWorkspace`,
`FocusWindowAt`, `ShowCalibrationTarget`, `HideOverlay`, `ShowStatus` — concrete,
single-purpose, typed.

Per-method controls, in order, in `extension/lib/service.js`:

1. **Token check** (`lib/token.js`) — trailing `token s` on every method.
2. **Enabled gate** — every action method no-ops unless the `active` gsetting is `true`
   (exempt: `Ping`, `GetStatus`, `SetEnabled` — liveness/status/kill must work while
   disabled). `SetEnabled(false)` is always honored.
3. **Rate-limit backstop** (`lib/ratelimit.js`) — JS token-bucket on
   `GLib.get_monotonic_time()`; excess calls dropped. Independent of the daemon's ceiling.
4. **Input clamp / validate** — `SwitchWorkspace` direction clamped to `{-1,+1}` with
   index-math wrap; `FocusWindowAt`/`ShowCalibrationTarget` reject `NaN`/out-of-`[0,1]`
   coords. **Reject, do not sanitize.**

**Token = defense-in-depth, not access control.** It is compared (constant-time-ish,
`lib/token.js:safeEqual`) against a `0600` state file. Because any same-UID process can also
`read()` that file, the token is **not** an access-control boundary. What it buys, and the
only claims we make:
- rejects **accidental** cross-app calls (a stray client that never read the token can't
  trigger desktop actions by coincidence/fuzz);
- a weak **intent / pairing** signal (the caller demonstrably read our state dir) usable for
  audit logging;
- a cheap early reject before the extension does work.

`require_token=false` accepts only the empty token (check disabled). The
`Gio.DBusExportedObject.wrapJSObject` export does not expose the D-Bus sender, so auth never
depends on `get_sender()` (same-UID sender names are not a trust boundary anyway — ADR-002).

## 2. Camera privacy

- **Local-only.** Frames never leave the host; all inference is on-device (NPU/GPU/CPU).
  There is **no network path** in the perception or interpret modules.
- **No biometric/frame persistence by default.** No raw frames, face crops, landmark
  vectors, or gaze coordinates are written to disk. Gaze coordinates are redacted from logs
  unless `logging.log_gaze=true` re-enables them at DEBUG (`logging_setup.redact_gaze`).
  A debug frame dump (`logging.dump_frames`) is **opt-in**, writes to a `0700` dir, and must
  log a WARN each session it is active.
- **Capture only while enabled.** The daemon starts the backend only after the extension
  reports `Enabled==true`; it is default-disabled and never auto-enables (ADR-003). The
  camera is not held open while idle.
- **Visible-indicator caveat `[H]`.** GNOME's built-in top-bar camera indicator fires only
  for **xdg-desktop-portal / PipeWire** camera consumers. Direct V4L2 `/dev/videoN` capture
  (our OpenVINO/OpenCV path) **bypasses the portal and does not light the OS indicator**.
  Our mitigation is the extension's own panel icon, visible only while `active`
  (`lib/quicktoggle.js`) — **best-effort UI, not a tamper-proof guarantee** (a compromised
  Shell could hide it). Routing capture through the PipeWire camera portal to get the native
  indicator is a documented future upgrade (adds a portal dep + interactive grant).

## 3. Local file permissions

`paths.py` creates every app directory **`0700`** and verifies the mode after `chmod`
(`ensure_dir` raises `PermissionError` on mismatch). Sensitive files are **`0600`**:

| Path | Mode | Holds |
|---|---|---|
| `~/.config/local-gaze/` + `config.toml` | 0700 / 0600 | config |
| `~/.local/state/local-gaze/` | 0700 | state |
| `~/.local/state/local-gaze/token` | 0600 | IPC token |
| `~/.local/state/local-gaze/calibration.json` | 0600 | gaze map |
| `~/.cache/local-gaze/ov/` | 0700 | OpenVINO blob cache |

- **Calibration** is written via an atomic `mkstemp(dir=…)` + `chmod 0600` + `os.replace` on
  the same filesystem (`calibration/model.py:save`), avoiding partial/symlink races.
- **Loose-mode refusal (both sides).** The extension's `lib/token.js` queries the token file
  mode (`NOFOLLOW_SYMLINKS`) and **refuses any group/world-accessible file** (`mode & 0o077`),
  failing closed as if no token exists; the daemon/CLI read it expecting `0600`
  (`paths.token_file()` resolves under the `0700` state dir).
- **Token provisioning.** `ipc/token.py:ensure_token()` writes
  `secrets.token_urlsafe(32)` to the `0600` token file when absent (open with
  `O_CREAT|O_TRUNC`, then `chmod 0600`). The daemon auto-provisions on startup, and
  `local-gaze provision-token` creates it explicitly (idempotent); the GNOME extension
  reads the same file. `load_token()` (daemon/CLI) mirrors the extension's loose-mode
  refusal (`mode & 0o077` ⇒ treated as absent). With no token file and
  `require_token=true`, token-gated calls fail closed until provisioned.

## 4. Model download integrity

`scripts/fetch-models.sh` (host-only) is the only path that fetches weights:
- **TLS-pinned transport** — `curl --proto '=https' --tlsv1.2` (wget `--https-only`
  fallback); official OpenVINO storage + MediaPipe URLs only.
- **sha256 verification** — each artifact is checked against a pinned digest in
  `models/MANIFEST` (`name, url, sha256, license, revision`). Trust-on-first-use: digests
  ship as `TODO`, get pinned on the first host download, and are **re-verified (abort on
  mismatch)** thereafter. `[H]` all digests are unpinned until the first host fetch.
- **No executable payloads.** Downloaded archives are scanned and **rejected if any entry
  has an execute bit or an executable-looking extension** (`.sh/.py/.so/.exe/…`,
  `reject_executable_archive`). Models are weights/IR only; no `trust_remote_code`, nothing
  downloaded is ever executed. The MediaPipe `.tflite` → OpenVINO IR conversion runs locally
  via `ov.convert_model`.
- `models/` is gitignored; only `models/MANIFEST` is committed.

## 5. Accidental-action gating

Defense-in-depth before any desktop action moves (ADR-009):
- **Gaze** → EMA/One-Euro smoothing → per-frame confidence threshold → spatial stability →
  **dwell timer** before `FocusWindowAt` (rejects saccade thrash).
- **Flick** → hand-center velocity with **hysteresis** (arm `|vx|>v_on`, fire on sign,
  disarm until `|vx|<v_off`) + **refractory debounce** requiring return-to-neutral before
  `SwitchWorkspace` (rejects double-fire/jitter).
- **Two-layer rate limit** — the daemon enforces a global `max_actions_per_sec` ceiling as
  the last gate before the wire (`daemon.py:_rate_ok`, sliding 1 s window) **and** the
  extension keeps an independent token-bucket backstop. One mis-tuned layer cannot remove
  the ceiling.
- **`--dry-run`** runs the full pipeline and logs decisions while suppressing all D-Bus
  action calls — safe for tuning.

## 6. Failure modes — every row fails closed (NO desktop action)

| Condition | Detection | Behavior |
|---|---|---|
| Non-GNOME-Wayland session | `session.detect_session()` (XDG_CURRENT_DESKTOP split `:`, XDG_SESSION_TYPE, WAYLAND_DISPLAY, best-effort `loginctl`); ambiguous ⇒ unsupported. Extension also reports `Supported=false` off Wayland (`Meta.is_wayland_compositor()` `[H]`). | Daemon exits non-zero before the loop; extension exports nothing actionable. |
| Extension missing / disabled | bus name unowned / `ServiceUnknown` on call | Daemon idles, reconnects with backoff, emits no actions. |
| Bus unreachable | connect/send error | Same: idle + backoff-reconnect, no actions. |
| `Supported==false` from extension | `GetStatus` after connect | Daemon refuses to act, closes, exits non-zero. |
| Low confidence / dwell unmet | per-frame confidence < threshold or no dwell | Decision dropped; no action. |
| Camera lost (unplug / EBUSY / read fail) `[H]` | capture error/timeout | Pause pipeline, stop acting, log, reopen with backoff. |
| Rate limit exceeded | daemon ceiling + extension backstop | Excess actions silently dropped (logged DEBUG). |
| Token mismatch (when required) | extension compares arg vs `0600` file | Call rejected; loose-mode token file refused. |
| Loose-mode token file | `mode & 0o077` (both sides) | Treated as no token ⇒ token-required calls rejected. |
| Process killed | n/a | Extension idle ⇒ fails closed. |

## 7. Honest limitations

- **No same-user isolation is possible on the session bus.** A malicious process running as
  this user can read the token and drive the extension exactly as the daemon does. The token
  prevents accidents and aids audit; it is **not** access control. Harden the OS account;
  do not run untrusted code as this user.
- **Direct-V4L2 capture does not light the OS camera indicator.** Our panel icon is
  best-effort UI, not a tamper-proof privacy signal; a compromised Shell could suppress it.
- **Timing is approximate.** GLib/asyncio timers and dwell/refractory windows may be delayed;
  treat thresholds as conservative, not exact. They are config-driven and `[H]` host-tuned.
- **sha256 pinning protects integrity, not behavior.** A correctly-signed model can still
  mispredict — hence confidence + dwell gating. Until the first host fetch, digests are
  unpinned (`TODO`).
- **Host-pending validation `[H]`:** the Wayland guard, real-camera capture/accuracy, and
  the live extension runtime are validated on the host (probe + relogin + e2e), never
  asserted from the container. (The full 6-model NPU compile chain — gaze + hand — is
  already host-verified, 2026-06-15.)

## Control checklist

- [x] No eval/arbitrary-code D-Bus method; ≤9 concrete typed methods.
- [x] `Enabled` gates all actions; default OFF; `SetEnabled(false)` always wins.
- [x] Optional per-call token vs `0600` file; `require_token` configurable; constant-time-ish
      compare; loose-mode file refused (both sides).
- [x] Daemon global rate ceiling + extension token-bucket backstop.
- [x] Input clamp/validate (workspace ∈ {-1,+1}; coords ∈ [0,1]; reject NaN).
- [x] Fail-closed when session unsupported / extension absent / bus down / `Supported=false`.
- [x] Dwell + confidence + stability gate (gaze); hysteresis + refractory (flick).
- [x] Three kill paths (Quick Settings toggle, CLI `disable`, daemon honors SetEnabled);
      dry-run mode.
- [x] No frame/crop/landmark/gaze persistence by default; opt-in frame dump WARNs; gaze
      coords redacted from logs by default.
- [x] 0700 dirs / 0600 files; atomic mkstemp+replace; mode verified.
- [x] Models: TLS + sha256-pinned MANIFEST + recorded license/revision; executable payloads
      rejected; nothing executed.
- [x] Extension `disable()` removes all timers/signals + unexports the D-Bus object.
- [ ] `[H]` Wayland guard, real-camera, live extension e2e validated on the host
      (6-model NPU compile chain — gaze + hand — host-verified 2026-06-15).

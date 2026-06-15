# Safety & Security — Threat Model + Controls (lane note)

Scope: same-user desktop automation driven by camera ML. Research date 2026-06-15.
Marking: [V]=verified from docs/source, [A]=assumed/reasoned, [!]=load-bearing gotcha.

## 1. Trust boundary reality (D-Bus session bus)

[V] The session bus authenticates only the **Unix UID**, not the app. ANY process
running as uid 1000 can call any method on any name on that bus. There is NO
per-app isolation on the raw session bus (only cross-user + Flatpak-proxy give
real isolation). So the extension's D-Bus interface is reachable by every
same-user process by design.

[!] **Honest token analysis.** An optional shared token stored in a 0600 file is
NOT a security boundary against same-user processes: anything that can call the
bus can also `read()` the token file (same UID). What a per-call token *does*
buy, and the only claims we may make:
- Prevents **accidental** cross-app calls (a stray client that doesn't know to
  read the token can't trigger desktop actions by fuzz/coincidence).
- Adds **intent / proof-of-collaboration**: the caller demonstrably read our
  state dir, useful as a weak liveness/pairing check + for audit logging.
- Lets the extension reject obviously-unpaired callers cheaply before doing work.
=> Treat it as a **defense-in-depth convenience**, never as access control.
Document this limitation in user-facing docs; do not over-promise.

[!] **GJS sender-identity caveat.** The convenient `Gio.DBusExportedObject.
wrapJSObject(xml, instance)` passes ONLY the declared method args to handlers —
it does NOT expose the D-Bus sender or a `Gio.DBusMethodInvocation`. To read the
caller's unique bus name you must use the lower-level
`Gio.DBusConnection.register_object(...)` (VTable/closures form); the method-call
callback receives a `Gio.DBusMethodInvocation` and `invocation.get_sender()`
returns `:1.NN`. [V] Since same-user sender names are not a trust boundary,
`get_sender()` is useful for **rate-limit bucketing + logging only**, not auth.
Recommendation: ship `wrapJSObject` + token-in-args (simplest); reserve the
register_object path only if per-sender bucketing is later wanted.

### Realistic control set (extension side)
- [V] **Minimal method surface, NO eval / no arbitrary code.** Concrete methods
  only: `Ping()->s`, `GetState()->a{sv}`, `SetEnabled(b)`, `WorkspaceRelative(i)`
  (clamp i∈{-1,+1}), `FocusWindowAt(d x,d y)` (normalized 0..1) or
  `FocusWindowById(t id)`. Properties: `Enabled b`, `Supported b`, `Version s`.
- [A] **Enabled-state gating**: every action method returns a typed error (e.g.
  `org.freedesktop.DBus.Error.AccessDenied` / custom `Disabled`) unless
  `Enabled==true`. `SetEnabled(false)` is always honored; default OFF.
- [A] **Per-call optional token**: methods accept trailing `token s`; compare with
  `crypto`-style constant-time-ish equality to contents of the 0600 token file.
  Empty/absent token allowed only if config `require_token=false`.
- [V] **Rate limiting on the extension side** via `GLib.timeout`/token-bucket in
  JS (`GLib.get_monotonic_time()` for refill math). No GJS built-in; implement
  manually. [!] Store every timer ID and `GLib.Source.remove()` it in
  `disable()` or it fires after teardown (use-after-free / GC-sweep crash).
- [A] **Input validation/clamping** inside each handler (XML signature gives base
  types; clamp ranges, reject NaN/out-of-[0,1] coords, allow-list workspace
  deltas). Reject, don't sanitize.
- [V] **Fail-closed on unsupported session**: expose `Supported=false` and refuse
  all action methods when not GNOME-Wayland (see §6).

### Lifecycle hygiene (extension)
- [V] Create bus name + export in `enable()`, **unexport + unown + remove all
  timers/signals** in `disable()`. Use returned IDs:
  `Gio.bus_unown_name(ownerId)`, `exported.unexport()`. [V] GJS guards pending
  property emits on last-connection unexport (≥ recent), but still null refs.

## 2. Camera privacy

- [A] **Local-only processing**; frames never leave the host, never written to
  disk by default. No raw frames, face crops, biometric templates, or landmark
  traces persisted by default.
- [A] **Debug frame-dump = explicit opt-in** (config flag + CLI `--dump-frames`),
  writes to 0700 dir, and logs a clear WARNING line each session it's active.
- [!] **Visible active indicator is on US, not the OS.** [V] GNOME's built-in
  top-bar camera indicator (since GNOME 45; present on 50.2) only fires for apps
  going through **xdg-desktop-portal / PipeWire camera**. Direct V4L2 `/dev/videoN`
  opens (OpenCV/V4L2 capture) **bypass the portal and do NOT light the
  indicator**. So if the daemon uses direct V4L2 we must provide our own visible
  signal. Options: (a) route capture via the PipeWire **Camera portal** to get
  the native indicator for free (preferred for privacy UX), or (b) keep V4L2 and
  have the extension draw a small always-visible "gaze active" panel icon while
  enabled. Recommend (b) as baseline (no portal dep), document (a) as upgrade.
- [A] **Default gaze-coordinate redaction in logs**: log levels/decisions, not
  raw (x,y) or landmark vectors. A `--log-gaze` opt-in re-enables coordinates at
  DEBUG only.

## 3. Accidental-action safety

- [A] **Start DISABLED/safe by default** (extension toggle OFF, daemon honors
  persisted enabled=false).
- [A] **Kill switch, three independent paths**: (1) extension toggle in
  GNOME UI; (2) CLI `local-gaze disable` (sets state + calls `SetEnabled(false)`);
  (3) daemon honors `SetEnabled(false)` and stops emitting actions immediately.
  Any one suffices; killing the daemon process also fails closed (extension idle).
- [A] **Gaze gating**: dwell time (e.g. ≥ ~300–500 ms continuous fixation) +
  per-frame confidence threshold + spatial stability before `FocusWindowAt`.
- [A] **Flick gating**: hysteresis (enter/exit velocity thresholds) + debounce /
  refractory window (e.g. ≥ ~600 ms) between workspace switches; require a
  return-to-neutral before next flick.
- [A] **GLOBAL rate limit** on the daemon side (actions/sec ceiling) AND the
  independent extension-side limiter (§1) as backstop — two layers.
- [A] **Dry-run mode** (`--dry-run`): full pipeline + decisions logged, D-Bus
  action calls suppressed (or routed to a no-op `Ping`), for safe tuning.

## 4. File permissions (XDG)

- [A] **Dirs 0700, files 0600**, owner-only, no group/other.
  - `~/.config/local-gaze/` (config.toml) — 0700.
  - `~/.local/state/local-gaze/` (calibration, token, logs) — 0700.
- [V] **Create securely / race-free**: token via
  `os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)` (fails if exists,
  symlink-safe), or atomic rewrite via `tempfile.mkstemp(dir=...)` (0600) +
  `os.fchmod(0o600)` + `fsync` + `os.replace()` on the SAME filesystem.
- [V] Generate token with `secrets.token_urlsafe(32)`.
- [A] On startup, **verify** dir/file mode + ownership; if token file is
  group/world-readable, refuse to use it (warn + regenerate). `os.umask(0o077)`
  early as belt-and-suspenders. Never world-readable token.

## 5. Model supply chain

- [A] **Download over TLS from official URLs only.** [V] OMZ models historically
  ship a `model.yml` per model carrying source URLs + **sha256** + Apache-2.0
  license; verify each artifact's sha256 against a pinned value in our repo
  (don't trust the downloaded manifest alone). [V] OMZ is being superseded by
  HF-Hub IR + `optimum-cli export openvino`; if pulling from HF, pin by **repo +
  revision/commit + per-file sha256**.
- [A] **Record license** per model in a `models/MANIFEST` (name, url, sha256,
  license, revision).
- [!] **Never auto-exec downloaded code.** Models are weights/IR only; reject any
  bundle containing executable/script payloads. No `trust_remote_code`.

## 6. Failure-modes table (every row => fail closed, NO desktop action)

| Condition | Detection | Behavior |
|---|---|---|
| Unsupported session (not GNOME-Wayland) | [V] parse `XDG_CURRENT_DESKTOP` split `:` for `GNOME`; confirm Wayland via `WAYLAND_DISPLAY` → `XDG_SESSION_TYPE=wayland` → `loginctl show-session self -p Type --value`; **AND** probe extension `Supported`. Ambiguous => not supported. | Daemon refuses to start action loop; extension `Supported=false` refuses methods. |
| Extension missing/disabled | D-Bus name not owned / `org.freedesktop.DBus.Error.ServiceUnknown` on call | Daemon stays in idle/no-op; retries with backoff; emits no actions. |
| Bus unreachable | connect fails / send error | Same: idle, backoff-reconnect, no actions. |
| Low model confidence | per-frame confidence < threshold or dwell not met | Drop the decision; no `FocusWindowAt`/`WorkspaceRelative`. |
| Camera lost (unplug / EBUSY / read fail) | capture error / timeout | Pause pipeline, auto-`SetEnabled(false)` semantics for actions, log, attempt reopen with backoff. |
| Rate limit exceeded | global + extension limiter | Silently drop excess actions (log at DEBUG). |
| Token mismatch (when required) | extension compares arg vs 0600 file | Reject call with typed error; rate-limit repeated failures; log. |

## Concrete control CHECKLIST
- [ ] No eval/arbitrary-code D-Bus method; surface ≤ ~6 typed methods.
- [ ] `Enabled` gates all actions; default OFF; `SetEnabled(false)` always wins.
- [ ] Optional per-call token vs 0600 file; `require_token` configurable; constant-time compare.
- [ ] Extension-side token-bucket rate limit; daemon-side GLOBAL rate limit.
- [ ] Input clamp/validate (workspace ∈ {-1,+1}; coords ∈ [0,1]; reject NaN).
- [ ] Fail-closed when session unsupported / extension absent / bus down.
- [ ] Dwell + confidence gate (gaze); hysteresis + debounce (flick).
- [ ] Three kill paths (UI toggle, CLI, daemon honors SetEnabled); dry-run mode.
- [ ] No frame/crop/template/landmark persistence by default; opt-in dump logs WARN.
- [ ] Visible active indicator (extension panel icon; portal path lights native one).
- [ ] Gaze coords redacted from logs by default.
- [ ] 0700 dirs / 0600 files; O_EXCL or mkstemp+replace; umask 0077; verify modes.
- [ ] Models: TLS + pinned sha256 + recorded license + revision; never exec payloads.
- [ ] Extension `disable()` removes all timers/signals + unexports + unowns name.

## Honest LIMITATIONS
- [!] No same-user isolation possible on the session bus: a malicious same-user
  process can read the token and drive the extension exactly as we do. The token
  prevents accidents and aids audit; it is NOT access control. The real boundary
  is the OS user account; harden that (don't run untrusted code as this user).
- [V] Direct-V4L2 capture won't trigger the OS camera indicator; our own
  indicator is best-effort UI, not a tamper-proof privacy guarantee.
- [A] Rate-limit timers/dwell timing are not precise (GLib sources may be
  delayed); treat thresholds as conservative, not exact.
- [A] sha256 pinning protects integrity but not model *behavioral* trust; a
  legitimately-signed model can still mispredict — hence confidence gating.
- [A] If we adopt the PipeWire camera portal for the indicator, that adds a
  portal dependency + interactive grant flow (host-only validation).

## Key API references
- Export: `Gio.DBusExportedObject.wrapJSObject(xml, instance)` → `.export(conn, path)` / `.unexport()`; name via `Gio.bus_own_name(Gio.BusType.SESSION,...)` / `Gio.bus_unown_name(id)`.
- Sender (only via low-level): `Gio.DBusConnection.register_object(...)` → callback gets `Gio.DBusMethodInvocation`; `invocation.get_sender()`.
- Timers: `GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, fn→GLib.SOURCE_REMOVE)`; `GLib.Source.remove(id)`; `GLib.get_monotonic_time()`.
- Python token: `secrets.token_urlsafe(32)`; `os.open(p, O_WRONLY|O_CREAT|O_EXCL, 0o600)`; `tempfile.mkstemp` + `os.fchmod` + `os.fsync` + `os.replace`.
- Session detect: `XDG_CURRENT_DESKTOP`(split `:`), `WAYLAND_DISPLAY`, `XDG_SESSION_TYPE`, `loginctl show-session self -p Type --value`.

## Sources
- SUSE D-Bus session-bus / same-UID model: https://security.opensuse.org/2024/05/22/gnome-remote-desktop-system-dbus.html
- GNOME RDP handover CVE (broad-call lesson): CVE-2024-5148 (same page).
- GJS D-Bus guide (wrapJSObject, export/unexport): https://gjs.guide/guides/gio/dbus.html
- Gio.DBusConnection.register_object / invocation.get_sender: https://docs.gtk.org/gio/method.DBusConnection.register_object.html , https://docs.gtk.org/gio/class.DBusMethodInvocation.html
- GJS review guidelines (enable/disable contract): https://gjs.guide/extensions/review-guidelines/review-guidelines.html
- GJS memory mgmt (GSource leak → use-after-free): https://gjs.guide/guides/gjs/memory-management.html
- GNOME 45 camera indicator: https://release.gnome.org/45/ , https://www.gamingonlinux.com/2023/09/gnome-45-released-with-dynamic-workspace-indicator-camera-indicator-and-much-more/
- Indicator = portal/PipeWire, not raw V4L2: https://blogs.gnome.org/uraeus/2024/03/15/pipewire-camera-handling-is-now-happening/
- Secure file perms (O_EXCL 0600): https://security.openstack.org/guidelines/dg_apply-restrictive-file-permissions.html
- Python secrets: https://docs.python.org/3/library/secrets.html
- OMZ → optimum-intel / HF IR migration + deprecations: https://github.com/openvinotoolkit/open_model_zoo , https://github.com/huggingface/optimum-intel
- Wayland/GNOME session detect signals: https://forum.manjaro.org/t/howto-check-a-user-session-type-wayland-x11-you-are-using-on-gnome-kde/87165

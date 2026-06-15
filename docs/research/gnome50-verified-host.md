# GNOME 50 API facts — VERIFIED on the actual host (2026-06-15)

Ground-truth, higher trust than web research: grepped from the installed GNOME
Shell **50.2** and its enabled extensions on the host
(`/run/host/home/eturkes/.local/share/gnome-shell/extensions/...`, readable from
the container). When implementing `extension/`, prefer these. Real working
reference extensions to read for patterns:
- `caffeine@patapon.info` — Quick Settings toggle + SystemIndicator.
- `appindicatorsupport@rgcjonas.gmail.com`, `blur-my-shell@aunetx` — D-Bus export.
- `dash-to-panel@jderose9.github.com` — addChrome, activateWindow, is_wayland_compositor.

## Verified

- **metadata.json `shell-version`**: a list of major-version strings. Installed
  exts use `["45","46","47","48","49","50"]`; Shell-50-only ones use `["50"]`.
  => use `["49", "50"]`. (gnome-extensions CLI is 50.2.)
- **D-Bus export (sync, returns values directly)** — confirmed exact calls:
  ```js
  this._dbusImpl = Gio.DBusExportedObject.wrapJSObject(IFACE_XML, this);
  this._dbusImpl.export(Gio.DBus.session, '/com/eturkes/LocalGaze');
  // teardown:
  this._dbusImpl.unexport();                 // <-- teardown method is unexport()
  // signal:
  this._dbusImpl.emit_signal('EnabledChanged', new GLib.Variant('(b)', [v]));
  ```
  Use SYNCHRONOUS method bodies that `return` the out value(s) (as
  appindicatorsupport/blur-my-shell do). Avoids the unresolved server-side-async
  shape. A JS method returning multiple out-args returns an array.
- **Quick Settings (GNOME 50)** — confirmed in caffeine:
  ```js
  import * as QuickSettings from 'resource:///org/gnome/shell/ui/quickSettings.js';
  const Toggle = GObject.registerClass(class extends QuickSettings.QuickToggle {...});
  class Indicator extends QuickSettings.SystemIndicator {...}   // add toggle via this.quickSettingsItems.push(toggle); Main.panel.statusArea.quickSettings.addExternalIndicator(indicator)
  ```
  Use `QuickToggle` (simple on/off, no menu) bound to gsetting `active`.
- **`Meta.is_wayland_compositor()`** exists (dash-to-panel uses it). Valid
  fail-closed guard (always true on Wayland-only GNOME 50, but keep it).
- **`Main.activateWindow(win)`** — confirmed, the way to focus/raise a window.
- **`Main.layoutManager.addChrome(actor, params)`** — confirmed; params object
  e.g. `{ affectsInputRegion: false, trackFullscreen: false }`. Pair with
  `Main.layoutManager.removeChrome(actor)` + `actor.destroy()` in teardown.

## Coordinate model (use this)

- `Main.layoutManager.monitors` => array of `{ index, x, y, width, height }` in
  **logical pixels**. `Meta.Window.get_frame_rect()` also returns logical px in
  the same global space. So:
  - window -> normalized: `nx = (frame.x + frame.w/2 - mon.x) / mon.width` (per
    its monitor), or a global-space normalization — pick global-primary for MVP
    and document. Daemon calibration maps gaze into the SAME normalized space.
  - normalized -> px (focus/overlay): `px = mon.x + nx*mon.width`,
    `py = mon.y + ny*mon.height`. No manual fractional-scale division needed
    because monitors geometry is already logical px (HiDPI handled by Mutter).
- Window hit-test for `FocusWindowAt`: `global.display.get_tab_list(Meta.TabList.NORMAL_ALL, ws)`
  then `global.display.sort_windows_by_stacking(list)`, iterate top-of-stack
  first, return first whose `get_frame_rect()` contains the px point.

## Workspace switch

```js
const wsm = global.workspace_manager;
const n = wsm.get_n_workspaces();
const idx = wsm.get_active_workspace_index();
const target = (idx + dir + n) % n;          // explicit wrap, dir in {-1,+1}
wsm.get_workspace_by_index(target).activate(global.get_current_time());
```

## Still host-only to confirm at runtime (non-blocking)

- `Main.osdWindowManager.showOne(...)` exact signature for `ShowStatus` (fallback:
  `Main.notify(text)`).
- Exact `QuickToggle` title/icon wiring (read caffeine for the current shape).
- Overlay stacking above fullscreen windows (tune addChrome params live).

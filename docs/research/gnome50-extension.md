# GNOME Shell 50 (Wayland) Extension API — Lane Note

Research date 2026-06-15. Target: GNOME Shell **50.2**, Wayland-only. Sources are
gjs.guide + mutter.gnome.org gi-docgen. `[V]`=verified-from-docs, `[A]`=assumed/
inferred. GNOME 50 postdates model cutoff — everything below is web-sourced.

## 0. Hard facts that shape design (GNOME 49→50)
- **[V] GNOME 50 is Wayland-only.** X11 backend removed from Mutter/Shell. The
  `restart` + `show-restart-message` signals on `global.display` are gone;
  `RunDialog._restart()` removed. No Alt+F2 → `restart`.
- **[V] No nested mode flag `--nested`.** Test with `--devkit` (see §7).
- **[V] `Meta.Rectangle` removed since 45 → use `Mtk.Rectangle`.** All geometry
  rects are `Mtk.Rectangle` (`{x,y,width,height}`).
- **[V] `Clutter.ClickAction`/`TapAction` removed (49)** → use
  `Clutter.ClickGesture()` / `Clutter.LongPressGesture()`.
- **[V] `Meta.Window.get_maximized()` removed (49)** → `is_maximized()`.
- **[V] 50: no relevant changes to `metadata.json`, `extension.js`, `prefs.js`.**
  Skeleton/prefs patterns below are stable 45→50.

## 1. ESM extension skeleton (GNOME 50)
`metadata.json` (strict version check — `"50"` MUST be present or it won't load):
```json
{
  "uuid": "local-gaze@eturkes.com",
  "name": "Local Gaze",
  "description": "Gaze/gesture desktop control bridge.",
  "shell-version": ["50"],
  "settings-schema": "org.gnome.shell.extensions.local-gaze",
  "session-modes": ["user"],
  "version": 1, "version-name": "0.1"
}
```
Notes: `[V]` `shell-version` is an array of exact major strings; ESM files are not
back-compatible so keep only `"50"` (e.mo. supports multi-version submission).
`[V]` `session-modes` defaults to `["user"]`; add `"unlock-dialog"` only if you
must run while locked (review-gated, needs justification + keyboard-signal
teardown) — for fail-closed gaze control, stay `["user"]`.

`extension.js`:
```js
import GObject from 'gi://GObject';
import Gio from 'gi://Gio';
import Meta from 'gi://Meta';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

export default class LocalGazeExtension extends Extension {
  enable() {  /* create state, export D-Bus, add quick toggle */ }
  disable() { /* unexport, destroy ALL actors/objects, null refs */ }
}
```
`[V]` Rules: `enable()`/`disable()` must be symmetric; in `disable()` destroy every
actor, disconnect every signal, unexport D-Bus, drop all refs (GC + lockscreen
correctness). Fail closed: in `enable()` guard on
`Meta.is_wayland_compositor()` `[A]` and bail (log + no-op) if not Wayland.

## 2. Export a session-bus D-Bus service FROM the extension
`[V]` Use `Gio.DBusExportedObject.wrapJSObject(xml, jsObj)` then `.export(...)`.
Two separate objects: wrapper must be stored on the instance to emit signals/props.
For an extension, export onto the **existing default session connection**
(`Gio.DBus.session`) — simplest. To also claim a well-known name use
`Gio.bus_own_name(Gio.BusType.SESSION, name, NONE, onBusAcquired, ...)` and export
in the **bus-acquired** callback (too late in name-acquired).

```js
const IFACE = `
<node><interface name="com.eturkes.LocalGaze">
  <method name="FocusWindowAt">
    <arg type="d" direction="in" name="nx"/>
    <arg type="d" direction="in" name="ny"/>
    <arg type="b" direction="out" name="ok"/>
  </method>
  <method name="SwitchWorkspace"><arg type="i" direction="in" name="dir"/></method>
  <property name="Active" type="b" access="readwrite"/>
  <signal name="StateChanged"><arg type="b" name="active"/></signal>
</interface></node>`;

class GazeService {
  get Active() { return this._active ?? false; }
  set Active(v) { if (v===this._active) return; this._active=v;
    this._impl.emit_property_changed('Active', GLib.Variant.new_boolean(v)); }
  FocusWindowAt(nx, ny) { /* §4 */ return true; }   // return native or Variant
  SwitchWorkspace(dir) { /* §3 */ }
  emitState(a){ this._impl.emit_signal('StateChanged', new GLib.Variant('(b)',[a])); }
}
// enable():
this._svc = new GazeService();
this._impl = Gio.DBusExportedObject.wrapJSObject(IFACE, this._svc);
this._svc._impl = this._impl;                       // BEFORE export
this._impl.export(Gio.DBus.session, '/com/eturkes/LocalGaze');
// disable():
this._impl.unexport(); this._impl = null; this._svc = null;
```
`[V]` emit signals: `impl.emit_signal(name, GLib.Variant)`. `[V]` emit prop change:
`impl.emit_property_changed(name, GLib.Variant)`. `[V]` method returns: plain JS
value or matching `GLib.Variant`. `[V]` property set receives `deepUnpack()`ed value.
`[A]` `unexport()` exists (mirror of `export`); also `unexport_from_connection(conn)`.
NO eval/exec method — interface is method-typed only, matching architecture rule.
Async server methods: append `Async` to method name and call
`invocation.return_value(variant)` `[A]` (verify on gjs overrides ref).

## 3. Workspace switching (flick left/right → prev/next)
```js
const wsm = global.workspace_manager;            // [V] Shell.Global prop, Meta.WorkspaceManager
const ws = wsm.get_active_workspace();           // [V]
const dir = Meta.MotionDirection.RIGHT;          // LEFT/RIGHT/UP/DOWN [V] enum MetaMotionDirection
ws.get_neighbor(dir).activate(global.get_current_time()); // [V]
```
`[V]` signatures: `meta_workspace_get_neighbor(MetaMotionDirection) -> MetaWorkspace`;
returns **the workspace itself if neighbor would be outside layout** (i.e. **no
wrap-around** by default — clamps at edges). `Meta.Workspace.activate(uint32 time)`.
Helpers: `wsm.get_active_workspace_index()`, `wsm.get_n_workspaces()`,
`wsm.get_workspace_by_index(i)` (returns null if out of range, safe).
`[A]` For explicit wrap: compute `(idx ± 1 + n) % n` and
`get_workspace_by_index(t).activate(time)`. Horizontal vs vertical layout affects
which of LEFT/RIGHT vs UP/DOWN moves between workspaces — for flick gestures prefer
index math (`±1`) over `get_neighbor` to be layout-independent.

## 4. Window enumeration + geometry + focus under a coordinate
```js
// list windows on the active workspace (or null = all):
const wins = global.display.get_tab_list(Meta.TabList.NORMAL_ALL, wsm.get_active_workspace());
const stacked = global.display.sort_windows_by_stacking(wins); // bottom→top
let hit = null;
for (let i = stacked.length - 1; i >= 0; i--) {     // top→bottom
  const r = stacked[i].get_frame_rect();            // Mtk.Rectangle {x,y,width,height}
  if (px >= r.x && px < r.x + r.width && py >= r.y && py < r.y + r.height) { hit = stacked[i]; break; }
}
if (hit) Main.activateWindow(hit);                  // raises + focuses
```
`[V]` `meta_display_get_tab_list(MetaTabList type, MetaWorkspace|null) -> GList<MetaWindow>`
(GJS returns array). `[V]` `Meta.TabList`: `NORMAL`=0,`DOCKS`=1,`GROUP`=2,
`NORMAL_ALL`=3,`NORMAL_ALL_MRU`=4. (Note: enum doc tagged "since 51" but values
hold on 50.) `[V]` `meta_window_get_frame_rect(out MtkRectangle)` — in GJS the
out-param is the **return value** (`r = win.get_frame_rect()`), `r` has `.x .y
.width .height`. `get_buffer_rect()` = full surface incl. shadow. `[V]`
`win.get_monitor()` → monitor index. `[V]` `Main.activateWindow(win, time?, ws?)`.
Alt source: `global.get_window_actors()` → `.meta_window` (actors, includes
override-redirect). Prefer `get_tab_list` for normal windows.

**Coord → monitor → window:** map gaze normalized `[0,1]` to global px first (§5),
then run the stacking hit-test above. To pick monitor for a point:
`Main.layoutManager.monitors[i]` each has `{x,y,width,height}`; point is in monitor
`i` if inside that rect. `[V]` `global.display.get_monitor_geometry(i)` →
`Mtk.Rectangle`; `get_current_monitor()` = monitor under pointer; `get_n_monitors()`.

## 5. Monitor layout + fullscreen calibration overlay
`[V]` Monitors: `Main.layoutManager.monitors` (array), `.primaryMonitor`,
`.primaryIndex`. Each monitor object: `index,x,y,width,height,geometryScale` `[A]`
(geometryScale = fractional-scale factor). All geometry is **logical px** (post-
scale); per-monitor `(x,y)` is the offset within the global logical coord space.

Map normalized → global px for monitor `m`:
`gx = m.x + nx*m.width; gy = m.y + ny*m.height;`

Overlay actor (full-screen, input-capable, visible over fullscreen windows):
```js
import St from 'gi://St';
import Clutter from 'gi://Clutter';
const m = Main.layoutManager.primaryMonitor;
this._overlay = new St.Widget({ reactive: true,
  x: m.x, y: m.y, width: m.width, height: m.height,
  style: 'background-color: rgba(0,0,0,0.6);' });
Main.layoutManager.addChrome(this._overlay, { affectsInputRegion: true });
// target dot at normalized (nx,ny):
this._dot = new St.Widget({ width: 24, height: 24,
  style: 'background-color:#f00; border-radius:12px;' });
this._dot.set_position(Math.round(nx*m.width - 12), Math.round(ny*m.height - 12));
this._overlay.add_child(this._dot);
// teardown (disable() / end calibration):
Main.layoutManager.removeChrome(this._overlay); this._overlay.destroy(); this._overlay=null;
```
`[V]` `Main.layoutManager.addChrome(actor, params)` adds to stage + lets actor get
events; `params` incl. `affectsInputRegion`, `affectsStruts`, `trackFullscreen`.
For an overlay that must persist over a fullscreen app use `trackFullscreen:false`
`[A]` (or the legacy `{visibleInFullscreen:true}` semantics). `removeChrome` /
`untrackChrome` for cleanup. Dot position is relative to the overlay (its origin =
monitor origin), so use `nx*m.width` not global px inside it. OSD/prompt during
calibration: `Main.osdWindowManager.showOne(monitorIndex, icon, label, level?)`
(`[V]` 49 renamed to show/showOne/showAll) or simple `Main.notify(title, body)`.

## 6. Quick Settings toggle (enable/disable + kill switch) — GNOME 50 pattern
`[V]` Use `resource:///org/gnome/shell/ui/quickSettings.js`:
`QuickToggle`, `SystemIndicator`, (`QuickMenuToggle`, `QuickSlider`).
```js
import * as QuickSettings from 'resource:///org/gnome/shell/ui/quickSettings.js';
const GazeToggle = GObject.registerClass(class extends QuickSettings.QuickToggle {
  constructor(ext) {
    super({ title: _('Local Gaze'), iconName: 'view-reveal-symbolic', toggleMode: true });
    ext.getSettings().bind('active', this, 'checked', Gio.SettingsBindFlags.DEFAULT);
  }});
const GazeIndicator = GObject.registerClass(class extends QuickSettings.SystemIndicator {
  constructor(ext) { super(); this.quickSettingsItems.push(new GazeToggle(ext)); }
  destroy() { this.quickSettingsItems.forEach(i => i.destroy()); super.destroy(); }
});
// enable(): this._ind = new GazeIndicator(this);
//   Main.panel.statusArea.quickSettings.addExternalIndicator(this._ind);
// disable(): this._ind.destroy(); this._ind = null;
```
`[V]` `QuickToggle` ctor params: `title, subtitle, iconName, toggleMode`. `[V]` bind
gsettings key ↔ `checked` via `Gio.Settings.bind`. `[V]` register via
`Main.panel.statusArea.quickSettings.addExternalIndicator(indicator[, colSpan])`.
The bound `active` gsetting is the kill switch: daemon stops acting when false;
watch it in the extension (`settings.connect('changed::active', …)`) to teardown
camera-driven behavior. Legacy panel button (`PanelMenu.Button`) still works but
Quick Settings is the GNOME 50 convention.

## 7. Logs + testing under Wayland
- `[V]` Live shell logs: `journalctl -f -o cat /usr/bin/gnome-shell` (or
  `journalctl --user-unit org.gnome.Shell -f` on systemd-user-session setups).
  `console.log/console.error` in extension → these logs.
- `[V]` Nested test (does NOT disturb live session):
  `dbus-run-session gnome-shell --devkit --wayland` (needs `mutter-devkit`).
  Replaces removed `--nested`. Install/enable inside it.
- `[V]` Live-session enable on Wayland requires **logout/login** (cannot restart
  gnome-shell in place; no Alt+F2 restart). Plan relogin to load/iterate.
- `[V]` `gnome-shell-test-tool --extension <zip>` installs+enables a zip before
  start (50). `gnome-extensions install --print-uuid` / new `upload` cmd (49).
- Looking Glass (Alt+F2 `lg`) for live introspection — available but Wayland LG is
  limited; prefer journalctl. `[A]`

## 8. prefs.js (libadwaita, GNOME 50)
`[V]` Stable since 42. Override `fillPreferencesWindow(window)` (takes
`Adw.PreferencesWindow`, returns void; higher priority than `buildPrefsWidget`).
```js
import Adw from 'gi://Adw';
import Gio from 'gi://Gio';
import {ExtensionPreferences, gettext as _}
  from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';
export default class GazePrefs extends ExtensionPreferences {
  fillPreferencesWindow(window) {
    const page = new Adw.PreferencesPage();
    const group = new Adw.PreferencesGroup({ title: _('General') });
    const row = new Adw.SwitchRow({ title: _('Active') });
    group.add(row); page.add(group); window.add(page);
    window._settings = this.getSettings();
    window._settings.bind('active', row, 'active', Gio.SettingsBindFlags.DEFAULT);
  }
}
```
Hierarchy: `Adw.PreferencesWindow → PreferencesPage → PreferencesGroup → rows`
(`Adw.SwitchRow`, `Adw.ActionRow`, `Adw.SpinRow`). PreferencesPage has built-in
scroll. Resize via `window.set_default_size(w,h)`. `prefs.js` runs in a separate
Gtk4 process — NO `Main`/Shell/Clutter imports there.

## Open verification items (host-only)
- `unexport()` / `unexport_from_connection()` exact name on the wrapper object.
- Async D-Bus server method `invocation.return_value(...)` exact shape.
- `monitor.geometryScale` field name + whether overlay coords need scale division.
- `addChrome` param to keep overlay above fullscreen (`trackFullscreen` vs
  `visibleInFullscreen`) — confirm current LayoutManager source.
- `Meta.is_wayland_compositor()` availability for fail-closed guard.
- TabList "since 51" tag vs working on 50 (likely doc lag; enum stable).

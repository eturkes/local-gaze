import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

import { checkToken } from './token.js';
import { RateLimiter } from './ratelimit.js';
import { Overlay } from './overlay.js';
import * as Windows from './windows.js';
import * as Workspace from './workspace.js';

// Canonical interface — byte-identical to docs/build-spec.md §2 and the daemon
// copy in src/local_gaze/ipc/schema.py (tests assert the daemon copy parses and
// matches this method set). Narrow by design: methods/properties/signal only,
// NO eval/exec surface.
export const IFACE = `<node>
  <interface name="com.eturkes.LocalGaze">
    <!-- liveness; returns "pong:<version>" -->
    <method name="Ping">
      <arg type="s" direction="in"  name="token"/>
      <arg type="s" direction="out" name="reply"/>
    </method>
    <!-- JSON status: {enabled,supported,version,session,n_workspaces,active_ws,n_monitors} -->
    <method name="GetStatus">
      <arg type="s" direction="in"  name="token"/>
      <arg type="s" direction="out" name="json"/>
    </method>
    <!-- JSON array of normal windows on active ws: [{id,title,wm_class,monitor,
         frame:{x,y,w,h}, nx, ny, focus}] ; coords are GLOBAL logical px + normalized -->
    <method name="GetWindows">
      <arg type="s" direction="in"  name="token"/>
      <arg type="s" direction="out" name="json"/>
    </method>
    <!-- enable/disable acting; mirrors gsetting 'active'; always honored -->
    <method name="SetEnabled">
      <arg type="b" direction="in"  name="enabled"/>
      <arg type="s" direction="in"  name="token"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <!-- relative workspace move; direction clamped to {-1,+1}; index-math wrap -->
    <method name="SwitchWorkspace">
      <arg type="i" direction="in"  name="direction"/>
      <arg type="s" direction="in"  name="token"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <!-- focus topmost normal window under normalized point (0..1, NaN/oob rejected) -->
    <method name="FocusWindowAt">
      <arg type="d" direction="in"  name="nx"/>
      <arg type="d" direction="in"  name="ny"/>
      <arg type="s" direction="in"  name="token"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <!-- show/hide a calibration dot at normalized point on its monitor -->
    <method name="ShowCalibrationTarget">
      <arg type="d" direction="in"  name="nx"/>
      <arg type="d" direction="in"  name="ny"/>
      <arg type="b" direction="in"  name="visible"/>
      <arg type="s" direction="in"  name="token"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <!-- tear down any overlay (calibration / debug) -->
    <method name="HideOverlay">
      <arg type="s" direction="in"  name="token"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <!-- transient OSD text; level is an advisory int (e.g. 0..2) -->
    <method name="ShowStatus">
      <arg type="s" direction="in"  name="text"/>
      <arg type="i" direction="in"  name="level"/>
      <arg type="s" direction="in"  name="token"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <!-- read-only props -->
    <property name="Enabled"   type="b" access="read"/>
    <property name="Supported" type="b" access="read"/>
    <property name="Version"   type="s" access="read"/>
    <!-- emitted when 'active' gsetting / Enabled changes (UI toggle, CLI, daemon) -->
    <signal name="EnabledChanged"><arg type="b" name="enabled"/></signal>
  </interface>
</node>`;

const OBJECT_PATH = '/com/eturkes/LocalGaze';

export class GazeService {
    constructor({ settings, version, supported }) {
        this._settings = settings;
        this._version = version;
        this._supported = supported;
        this._enabled = settings.get_boolean('active');
        this._limiter = new RateLimiter(4);
        this._overlay = new Overlay();
        this._impl = null;
    }

    export() {
        this._impl = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._impl.export(Gio.DBus.session, OBJECT_PATH);
    }

    teardown() {
        if (this._impl !== null) {
            this._impl.unexport();
            this._impl = null;
        }
        this._overlay.destroy();
        this._limiter.destroy();
        this._settings = null;
    }

    // --- state plumbing (called by extension.js on changed::active) ---

    setEnabled(v) {
        this._enabled = !!v;
    }

    emitEnabledChanged(v) {
        if (this._impl !== null) {
            this._impl.emit_signal('EnabledChanged', new GLib.Variant('(b)', [!!v]));
        }
    }

    // --- gate helpers ---

    _requireToken() {
        return this._settings ? this._settings.get_boolean('require-token') : true;
    }

    _auth(token) {
        return checkToken(token ?? '', this._requireToken());
    }

    _validNorm(v) {
        return typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= 1;
    }

    // Acting gate: every method that performs a compositor action requires the
    // token, Enabled==true, Supported==true, and a rate-limit token. Returns
    // true when the call may proceed.
    _gateAction(token) {
        if (!this._auth(token)) {
            return false;
        }
        if (!this._supported || !this._enabled) {
            return false;
        }
        return this._limiter.allow();
    }

    // --- read-only D-Bus properties ---

    get Enabled() {
        return this._enabled;
    }

    get Supported() {
        return this._supported;
    }

    get Version() {
        return this._version;
    }

    // --- D-Bus methods (synchronous; return the out value(s) directly) ---

    Ping(token) {
        if (!this._auth(token)) {
            return '';
        }
        return `pong:${this._version}`;
    }

    GetStatus(token) {
        if (!this._auth(token)) {
            return '{}';
        }
        const wsm = global.workspace_manager;
        const status = {
            enabled: this._enabled,
            supported: this._supported,
            version: this._version,
            session: this._supported ? 'gnome-wayland' : 'unsupported',
            n_workspaces: wsm.get_n_workspaces(),
            active_ws: wsm.get_active_workspace_index(),
            n_monitors: global.display.get_n_monitors(),
        };
        return JSON.stringify(status);
    }

    SetEnabled(enabled, token) {
        // Always honored (gated only on token). Writing the gsetting is the
        // single source of truth; extension.js' changed::active handler updates
        // the cache and emits EnabledChanged.
        if (!this._auth(token)) {
            return false;
        }
        if (this._settings) {
            this._settings.set_boolean('active', !!enabled);
        }
        return true;
    }

    GetWindows(token) {
        // Enabled-gated: window titles/geometry are sensitive and must not be
        // readable while the kill switch is off. (Not rate-limited: it is a read.)
        if (!this._auth(token)) {
            return '[]';
        }
        if (!this._supported || !this._enabled) {
            return '[]';
        }
        return JSON.stringify(Windows.getWindows());
    }

    SwitchWorkspace(direction, token) {
        if (!this._gateAction(token)) {
            return false;
        }
        const dir = direction < 0 ? -1 : 1;
        return Workspace.switchRelative(dir);
    }

    FocusWindowAt(nx, ny, token) {
        if (!this._gateAction(token)) {
            return false;
        }
        if (!this._validNorm(nx) || !this._validNorm(ny)) {
            return false;
        }
        return Windows.focusAt(nx, ny);
    }

    ShowCalibrationTarget(nx, ny, visible, token) {
        if (!this._gateAction(token)) {
            return false;
        }
        if (!visible) {
            return this._overlay.hide();
        }
        if (!this._validNorm(nx) || !this._validNorm(ny)) {
            return false;
        }
        return this._overlay.showTarget(nx, ny);
    }

    HideOverlay(token) {
        if (!this._gateAction(token)) {
            return false;
        }
        return this._overlay.hide();
    }

    ShowStatus(text, level, token) {
        if (!this._gateAction(token)) {
            return false;
        }
        const lvl = Number.isFinite(level) ? level : 0;
        return this._overlay.showStatus(String(text ?? ''), lvl);
    }
}

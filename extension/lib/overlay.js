import St from 'gi://St';
import Gio from 'gi://Gio';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const DOT_SIZE = 28;

// Calibration / debug overlay. A single St.Widget dot is shown at a normalized
// point on the primary monitor (the same basis the daemon calibrates against).
// addChrome makes it a chrome actor stacked above normal windows; teardown is
// removeChrome + destroy so disable() leaves nothing behind.
export class Overlay {
    constructor() {
        this._dot = null;
    }

    _primary() {
        const mons = Main.layoutManager.monitors;
        return mons[Main.layoutManager.primaryIndex] ?? mons[0] ?? null;
    }

    showTarget(nx, ny) {
        const m = this._primary();
        if (m === null) {
            return false;
        }
        if (this._dot === null) {
            this._dot = new St.Widget({
                style_class: 'local-gaze-dot',
                width: DOT_SIZE,
                height: DOT_SIZE,
                reactive: false,
                can_focus: false,
                track_hover: false,
            });
            // trackFullscreen keeps the dot above a fullscreen window during
            // calibration; affectsInputRegion is intentionally omitted (removed
            // from chrome params on GNOME >= 50).
            Main.layoutManager.addChrome(this._dot, { trackFullscreen: true });
        }
        const px = m.x + nx * m.width - DOT_SIZE / 2;
        const py = m.y + ny * m.height - DOT_SIZE / 2;
        this._dot.set_position(Math.round(px), Math.round(py));
        this._dot.show();
        return true;
    }

    hide() {
        if (this._dot !== null) {
            Main.layoutManager.removeChrome(this._dot);
            this._dot.destroy();
            this._dot = null;
        }
        return true;
    }

    // Transient OSD text. level is advisory; osdWindowManager.showOne takes
    // (monitorIndex, icon, label, level?). Falls back to a notification.
    showStatus(text, level) {
        const monitorIndex = Main.layoutManager.primaryIndex;
        const osd = Main.osdWindowManager;
        if (osd && typeof osd.showOne === 'function') {
            const icon = new Gio.ThemedIcon({ name: 'camera-web-symbolic' });
            osd.showOne(monitorIndex, icon, text, level);
        } else {
            Main.notify(text);
        }
        return true;
    }

    destroy() {
        this.hide();
    }
}

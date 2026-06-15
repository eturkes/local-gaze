import { Extension } from 'resource:///org/gnome/shell/extensions/extension.js';

import { GazeService } from './lib/service.js';
import { GazeIndicator } from './lib/quicktoggle.js';
import { isSupported } from './lib/session.js';

export default class LocalGazeExtension extends Extension {
    enable() {
        this._settings = this.getSettings();
        const supported = isSupported();
        const version = this.metadata['version-name'] ?? String(this.metadata.version);

        this._service = new GazeService({ settings: this._settings, version, supported });
        this._service.export();

        // 'active' gsetting is the single Enabled source of truth. Any flip
        // (Quick Settings toggle, prefs, CLI, or the daemon's SetEnabled which
        // writes this key) lands here: sync the service cache + emit the signal.
        this._activeId = this._settings.connect('changed::active', () => {
            const v = this._settings.get_boolean('active');
            this._service.setEnabled(v);
            this._service.emitEnabledChanged(v);
        });

        // Quick Settings kill switch + camera-active panel hint.
        this._indicator = new GazeIndicator(this._settings);
    }

    disable() {
        // Strict inverse of enable(): disconnect signals, destroy actors, unexport
        // D-Bus, null refs. (RateLimiter/Overlay timers + chrome are torn down
        // inside GazeService.teardown / GazeIndicator.destroy.)
        if (this._activeId) {
            this._settings.disconnect(this._activeId);
            this._activeId = 0;
        }
        if (this._indicator) {
            this._indicator.destroy();
            this._indicator = null;
        }
        if (this._service) {
            this._service.teardown();
            this._service = null;
        }
        this._settings = null;
    }
}

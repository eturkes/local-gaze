import Gio from 'gi://Gio';
import GObject from 'gi://GObject';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as QuickSettings from 'resource:///org/gnome/shell/ui/quickSettings.js';

const ICON = 'camera-web-symbolic';

const GazeToggle = GObject.registerClass(
    class GazeToggle extends QuickSettings.QuickToggle {
        _init(settings) {
            super._init({
                title: 'Gaze Control',
                iconName: ICON,
                toggleMode: true,
            });
            // 'active' gsetting is the single Enabled source of truth; binding
            // makes the toggle the kill switch (and reflects CLI/daemon flips).
            settings.bind('active', this, 'checked', Gio.SettingsBindFlags.DEFAULT);
        }
    }
);

// SystemIndicator hosting the toggle. The panel icon is visible only while
// active — our own "camera in use" hint (privacy signal).
export const GazeIndicator = GObject.registerClass(
    class GazeIndicator extends QuickSettings.SystemIndicator {
        _init(settings) {
            super._init();
            this._settings = settings;

            this._indicator = this._addIndicator();
            this._indicator.iconName = ICON;

            this._toggle = new GazeToggle(settings);
            this.quickSettingsItems.push(this._toggle);

            this._syncId = settings.connect('changed::active', () => this._sync());
            this._sync();

            Main.panel.statusArea.quickSettings.addExternalIndicator(this);
        }

        _sync() {
            this._indicator.visible = this._settings.get_boolean('active');
        }

        destroy() {
            if (this._syncId) {
                this._settings.disconnect(this._syncId);
                this._syncId = 0;
            }
            this.quickSettingsItems.forEach((item) => item.destroy());
            this.quickSettingsItems.length = 0;
            this._settings = null;
            super.destroy();
        }
    }
);

import Gio from 'gi://Gio';
import Adw from 'gi://Adw';

import { ExtensionPreferences } from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

export default class LocalGazePrefs extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const settings = this.getSettings();

        const page = new Adw.PreferencesPage({
            title: 'General',
            icon_name: 'camera-web-symbolic',
        });

        const group = new Adw.PreferencesGroup({
            title: 'Gaze Control',
            description: 'Local eye-tracking + hand-gesture desktop control.',
        });

        const activeRow = new Adw.SwitchRow({
            title: 'Enabled',
            subtitle: 'Allow gaze/gesture actions. This is the kill switch.',
        });
        settings.bind('active', activeRow, 'active', Gio.SettingsBindFlags.DEFAULT);
        group.add(activeRow);

        const tokenRow = new Adw.SwitchRow({
            title: 'Require token',
            subtitle: 'Reject D-Bus calls without the accident-token (defense-in-depth).',
        });
        settings.bind('require-token', tokenRow, 'active', Gio.SettingsBindFlags.DEFAULT);
        group.add(tokenRow);

        page.add(group);
        window.add(page);
    }
}

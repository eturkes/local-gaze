import Meta from 'gi://Meta';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

// Coordinate model (see docs/research/gnome50-verified-host.md):
// Main.layoutManager.monitors and Meta.Window.get_frame_rect() are both in the
// same global *logical* pixel space (Mutter handles HiDPI), so no fractional
// scale division is needed here.
//   window -> normalized: per the window's own monitor.
//   normalized -> px (FocusWindowAt): against the PRIMARY monitor — the daemon's
//     gaze calibration maps into the primary monitor's normalized space (MVP).

function monitors() {
    return Main.layoutManager.monitors;
}

function monitorFor(index) {
    const mons = monitors();
    return mons[index] ?? mons[Main.layoutManager.primaryIndex] ?? mons[0] ?? null;
}

function stackedTopFirst(ws) {
    const list = global.display.get_tab_list(Meta.TabList.NORMAL_ALL, ws);
    return global.display.sort_windows_by_stacking(list).reverse();
}

// JSON model of normal windows on the active workspace, top-of-stack first.
export function getWindows() {
    const ws = global.workspace_manager.get_active_workspace();
    const wins = stackedTopFirst(ws);
    const focus = global.display.get_focus_window();
    const out = [];
    for (const w of wins) {
        const r = w.get_frame_rect();
        const mi = w.get_monitor();
        const m = monitorFor(mi);
        const nx = m ? (r.x + r.width / 2 - m.x) / m.width : 0;
        const ny = m ? (r.y + r.height / 2 - m.y) / m.height : 0;
        out.push({
            id: w.get_id(),
            title: w.get_title() ?? '',
            wm_class: w.get_wm_class() ?? '',
            monitor: mi,
            frame: { x: r.x, y: r.y, w: r.width, h: r.height },
            nx,
            ny,
            focus: w === focus,
        });
    }
    return out;
}

// Focus the topmost normal window under a normalized point. Caller has already
// validated nx,ny are finite in [0,1].
export function focusAt(nx, ny) {
    const mons = monitors();
    const m = mons[Main.layoutManager.primaryIndex] ?? mons[0] ?? null;
    if (m === null) {
        return false;
    }
    const px = m.x + nx * m.width;
    const py = m.y + ny * m.height;
    const ws = global.workspace_manager.get_active_workspace();
    for (const w of stackedTopFirst(ws)) {
        const r = w.get_frame_rect();
        if (px >= r.x && px < r.x + r.width && py >= r.y && py < r.y + r.height) {
            Main.activateWindow(w);
            return true;
        }
    }
    return false;
}

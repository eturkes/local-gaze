// Relative workspace switching with explicit wrap. dir is clamped to {-1,+1}
// (the contract: SwitchWorkspace direction is clamped, not validated away).
export function switchRelative(dir) {
    const step = dir < 0 ? -1 : 1;
    const wsm = global.workspace_manager;
    const n = wsm.get_n_workspaces();
    if (n <= 0) {
        return false;
    }
    const idx = wsm.get_active_workspace_index();
    const target = (idx + step + n) % n;
    wsm.get_workspace_by_index(target).activate(global.get_current_time());
    return true;
}

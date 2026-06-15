import Meta from 'gi://Meta';

// Inside the Shell process the desktop is always GNOME; the only fail-closed
// gate is whether the compositor is Wayland (the architecture targets Wayland
// only — X11 automation paths are unsupported).
export function isSupported() {
    return Meta.is_wayland_compositor();
}

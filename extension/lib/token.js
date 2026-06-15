import GLib from 'gi://GLib';
import Gio from 'gi://Gio';

function tokenPath() {
    return GLib.build_filenamev([GLib.get_home_dir(), '.local', 'state', 'local-gaze', 'token']);
}

// Read the 0600 token file. Returns the trimmed contents, or null when the file
// is absent / unreadable / group- or world-accessible (fail closed: a loose-mode
// file is treated as if no token exists).
function readToken() {
    const file = Gio.File.new_for_path(tokenPath());
    let info;
    try {
        info = file.query_info(
            Gio.FILE_ATTRIBUTE_UNIX_MODE,
            Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS,
            null
        );
    } catch {
        return null;
    }
    const mode = info.get_attribute_uint32(Gio.FILE_ATTRIBUTE_UNIX_MODE);
    if (mode & 0o077) {
        // group/other readable or writable -> refuse.
        return null;
    }
    let bytes;
    try {
        [, bytes] = file.load_contents(null);
    } catch {
        return null;
    }
    return new TextDecoder().decode(bytes).trim();
}

// Length-independent constant-time-ish comparison.
function safeEqual(a, b) {
    let diff = a.length ^ b.length;
    const n = Math.max(a.length, b.length);
    for (let i = 0; i < n; i++) {
        const ca = i < a.length ? a.charCodeAt(i) : 0;
        const cb = i < b.length ? b.charCodeAt(i) : 0;
        diff |= ca ^ cb;
    }
    return diff === 0;
}

// Validate a caller-supplied token.
//   requireToken=false -> only the empty token is accepted (token disabled).
//   requireToken=true  -> a readable 0600 token file must exist and match.
export function checkToken(supplied, requireToken) {
    if (!requireToken) {
        return supplied === '';
    }
    const expected = readToken();
    if (expected === null || expected === '') {
        return false;
    }
    return safeEqual(String(supplied), expected);
}

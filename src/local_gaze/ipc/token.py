from __future__ import annotations

import logging
import os
import secrets

from .. import paths

_log = logging.getLogger("local_gaze.ipc.token")


def load_token() -> str:
    """Return the IPC token, or "" if absent / unreadable / loose-perms.

    Mirrors the extension's `token.js`: a group/other-accessible file is treated as
    absent (fail closed) rather than trusted.
    """
    path = paths.token_file()
    try:
        mode = path.stat().st_mode
    except OSError:
        return ""
    if mode & 0o077:
        _log.warning("token file %s is group/other-accessible; ignoring it", path)
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def ensure_token() -> str:
    """Return the existing token, provisioning a fresh 0600 one if none is present.

    Both the daemon and the GNOME extension read this same file (same user); the daemon
    provisions it on startup so the default `require_token=true` config works out of the
    box. The token is defense-in-depth, not access control (see docs/security-privacy.md).
    """
    existing = load_token()
    if existing:
        return existing
    paths.ensure_dir(paths.state_dir(), 0o700)
    path = paths.token_file()
    token = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (token + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(path, 0o600)
    _log.info("provisioned IPC token at %s", path)
    return token

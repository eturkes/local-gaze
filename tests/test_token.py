from __future__ import annotations

import stat
from pathlib import Path

from local_gaze import paths
from local_gaze.ipc import token


def test_ensure_token_creates_0600(xdg_tmp: Path) -> None:
    tok = token.ensure_token()
    assert tok
    tf = paths.token_file()
    assert stat.S_IMODE(tf.stat().st_mode) == 0o600
    assert token.load_token() == tok


def test_ensure_token_idempotent(xdg_tmp: Path) -> None:
    assert token.ensure_token() == token.ensure_token()


def test_load_token_absent_returns_empty(xdg_tmp: Path) -> None:
    assert token.load_token() == ""


def test_loose_perms_treated_as_absent(xdg_tmp: Path) -> None:
    token.ensure_token()
    paths.token_file().chmod(0o644)
    assert token.load_token() == ""

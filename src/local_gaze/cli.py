from __future__ import annotations

import argparse

_SUBCOMMANDS = (
    ("check", "preflight: session + bus reachable + extension enabled + deps present"),
    ("probe", "run the host probe (openvino devices, NPU, cameras); unverified in container"),
    ("run", "start the daemon (capture + inference + interpret -> D-Bus client)"),
    ("enable", "systemctl --user enable --now the local-gaze service (host)"),
    ("disable", "systemctl --user disable --now the local-gaze service (host)"),
    ("calibrate", "interactive gaze calibration; persists the calibration model"),
    ("provision-token", "create the 0600 IPC token shared with the extension (idempotent)"),
    ("demo", "synthetic + dry-run; print decided actions for a few seconds (no camera/D-Bus)"),
    ("install-extension", "symlink the extension, compile schemas, print enable instructions"),
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="local-gaze", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, help_text in _SUBCOMMANDS:
        p = sub.add_parser(name, help=help_text)
        if name == "probe":
            p.add_argument("--human", action="store_true",
                           help="human-readable probe output instead of JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    from . import commands

    return commands.dispatch(args)

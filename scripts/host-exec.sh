#!/bin/sh
# host-exec.sh — run a command on the HOST from inside the Distrobox container.
#
# GOTCHA (see docs/environment-facts.md): host-spawn returns exit 127 with NO
# output when the current working directory does not exist on the HOST. The
# container cwd is typically /run/host/... which the host does not have, so a
# naive distrobox-host-exec call silently fails. Fix: cd to a host-valid path
# (/ always exists) BEFORE bridging to the host.
#
# Usage: scripts/host-exec.sh <cmd> [args...]
set -eu

cd / || exit 1

if command -v distrobox-host-exec >/dev/null 2>&1; then
	exec distrobox-host-exec "$@"
fi
exec host-spawn --no-pty "$@"

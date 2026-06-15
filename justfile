root := justfile_directory()

# Container dev env (no openvino extra): deps + default dev group.
sync:
    uv sync

# Host runtime venv inheriting system openvino 2026.2, then editable install.
# The host has no uv, so use the SYSTEM python3 stdlib venv (verified to expose
# system openvino under --system-site-packages); see docs/environment-facts.md.
# Run this ON THE HOST (e.g. `distrobox-host-exec just host-venv`).
host-venv:
    python3 -m venv --system-site-packages .venv-host
    .venv-host/bin/pip install -e .

# Canonical container gate: lint + type-check + tests on a private session bus.
# `env -u PYTHONPATH` neutralizes any host shell export (e.g. a standalone
# openvino_genai bundle) that could shadow the venv; see docs/environment-facts.md.
check:
    uv sync && env -u PYTHONPATH uv run ruff check . && env -u PYTHONPATH uv run mypy src && env -u PYTHONPATH uv run dbus-run-session -- pytest -q

# IPC round-trip only (real client vs fake extension on an ephemeral bus).
ipc-test:
    env -u PYTHONPATH uv run dbus-run-session -- pytest tests/test_ipc.py -q

# Install/refresh the GNOME extension (host): symlink, compile schemas, enable.
install-ext:
    #!/usr/bin/env sh
    set -eu
    uuid=$(jq -r .uuid "{{root}}/extension/metadata.json")
    mkdir -p "$HOME/.local/share/gnome-shell/extensions"
    ln -sfn "{{root}}/extension" "$HOME/.local/share/gnome-shell/extensions/$uuid"
    glib-compile-schemas "{{root}}/extension/schemas/"
    gnome-extensions enable "$uuid"
    echo "Wayland: log out and back in to load extension code changes."

# Install the systemd user unit (host); ships disabled (fail-closed).
svc-install:
    mkdir -p "$HOME/.config/systemd/user"
    ln -sf "{{root}}/systemd/local-gaze.service" "$HOME/.config/systemd/user/"
    systemctl --user daemon-reload

# Download + verify CV models against models/MANIFEST (host).
fetch-models:
    sh "{{root}}/scripts/fetch-models.sh"

# Host probe: openvino devices, NPU compile+infer smoke, cameras -> JSON.
probe:
    "{{root}}/scripts/host-probe"

# Synthetic dry-run demo (container-safe; no camera, no D-Bus actions).
demo:
    env -u PYTHONPATH uv run local-gaze demo

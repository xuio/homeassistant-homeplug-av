#!/usr/bin/env bash
set -euo pipefail

# Start the host-side HomePlug relay needed only when the QEMU/vmnet bridge is
# backed by macOS Wi-Fi MACNAT. The guest and Home Assistant still use their
# normal bridged Ethernet interface.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
WIFI_IF="${WIFI_IF:-en1}"
VM_MAC="${VM_MAC:-52:54:b0:16:85:da}"
BRIDGE="${BRIDGE:-bridge100}"
REPORT_INTERVAL="${REPORT_INTERVAL:-30}"
RELAY_VENV="${RELAY_VENV:-$HOME/.local/share/homeplug-wifi-relay/venv}"
PYTHON="${PYTHON:-python3}"
RELAY_CONTROL_FILE="${RELAY_CONTROL_FILE:-}"

if [[ ! -x "$RELAY_VENV/bin/python" ]]; then
  "$PYTHON" -m venv "$RELAY_VENV"
fi

if ! "$RELAY_VENV/bin/python" -c 'import scapy' >/dev/null 2>&1; then
  "$RELAY_VENV/bin/python" -m pip install --upgrade pip scapy
fi

args=(
  "$SCRIPT_DIR/homeplug_wifi_macnat_relay.py"
  --wifi-if "$WIFI_IF"
  --bridge "$BRIDGE"
  --vm-mac "$VM_MAC"
  --report-interval "$REPORT_INTERVAL"
)

if [[ -n "$RELAY_CONTROL_FILE" ]]; then
  args+=(--control-file "$RELAY_CONTROL_FILE")
fi

exec sudo "$RELAY_VENV/bin/python" "${args[@]}"

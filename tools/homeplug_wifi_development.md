# HomePlug Wi-Fi VM Development

The development VM is launched with the same guest-visible network shape as
the later Ethernet Home Assistant setup:

- one `virtio-net-pci` NIC
- fixed guest MAC `52:54:b0:16:85:da`
- macOS `vmnet-bridged`
- guest interface `enp0s1`
- Home Assistant / `pla-util-py` configured against `enp0s1`

On wired Ethernet, adapters reply directly to the VM MAC and no host helper is
needed. On macOS Wi-Fi, `vmnet-bridged` uses MACNAT: HomePlug AV requests leave
the guest, but adapter replies are addressed to the host Wi-Fi MAC. The
`homeplug_wifi_macnat_relay.py` helper copies only HomePlug AV EtherType
`0x88e1` replies from the Wi-Fi side to the VM-side `vmenet` interface and
rewrites the destination to the VM MAC.

This keeps the integration environment identical inside the VM. The relay is a
host-only development workaround; it should not be installed in the later wired
Home Assistant setup.

## Start The VM

```bash
tools/run_homeplug_direct_wifi_vm.sh
```

The defaults match the current local VM image in
`/Users/moritz/Virtual Machines/HomeplugDev`. Override `WIFI_IF`, `VM_MAC`,
`VM_DIR`, `DISK_IMAGE`, or `EFI_VARS` if the local host changes.

## Start The Relay

Start the VM first so macOS creates `bridge100` and its `vmenet` member, then:

```bash
tools/run_homeplug_wifi_macnat_relay.sh
```

The script creates a small local Python virtualenv under
`~/.local/share/homeplug-wifi-relay/venv` and installs Scapy if needed. It uses
`sudo` because BPF sniffing and raw frame injection require root privileges.
For hardening tests, start the relay with a writable control file so the VM
harness can inject packet loss without changing the Home Assistant guest:

```bash
RELAY_CONTROL_FILE=/tmp/homeplug-relay-control.json tools/run_homeplug_wifi_macnat_relay.sh
```

## Verify From The Guest

Inside the VM, HomePlug discovery should use the normal guest interface:

```bash
cd ~/homeassistant-powerline-stats
sudo .venv/bin/python - <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, str(Path("custom_components/homeplug_av/pla-util-py")))
from pla_util_py import PLAUtil

pla = PLAUtil(interface="enp0s1")
print(pla.discover(timeout=2.5))
PY
```

## Run Home Assistant Hardening Tests

The harness syncs the repository into the VM, creates a clean Home Assistant
config entry against `enp0s1`, starts Home Assistant, samples the entity
registry and recorder database, and optionally restarts Home Assistant during
the run:

```bash
tools/homeplug_vm_harness.py \
  --duration-minutes 30 \
  --sample-interval-seconds 60 \
  --restart-interval-minutes 10
```

By default the harness uses a 10 second scan interval, 1 hour adapter
retention, 5 minute link retention, and 5 minute QCA diagnostic interval. Override
`--scan-interval-seconds`, `--adapter-retention-seconds`, or
`--link-retention-seconds` when intentionally testing faster expiry. Override
`--qca-diagnostic-interval-seconds` when intentionally testing QCA link
counters faster than the production-like default.

Fault scenarios require the relay control file shown above:

```bash
tools/homeplug_vm_harness.py \
  --duration-minutes 5 \
  --sample-interval-seconds 30 \
  --relay-control-file /tmp/homeplug-relay-control.json \
  --fault-scenario drop-all-2m
```

The harness fails if Home Assistant stops, entity unique IDs duplicate, new
HomePlug entities are registered after startup, zero link-rate states are
recorded, or Home Assistant logs errors/tracebacks.

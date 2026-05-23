# pla-util-py

A partial Python port of [pla-util](https://github.com/serock/pla-util), a utility
for managing HomePlug AV2/Broadcom-based power-line adapters (PLA) under Linux.

This implementation focuses on the most commonly used diagnostic commands and
is meant for quick scripting/debugging rather than full feature parity. This
vendored copy is used by the Home Assistant HomePlug AV integration and also
contains Qualcomm Atheros vendor commands needed for QCA-based adapters.

## Features

* Discover adapters on the local network interface
* Query capabilities, discover-list, network-stats
* Retrieve human-friendly ID (HFID) and basic identification info
* Show network-info (member scope) and minimal station-info
* Query Qualcomm Atheros software version, network info, network-info stats,
  link stats, restart, and operational attributes
* Reset / restart adapter

## Quick start

1.  Clone the repository and install the single runtime dependency:

   ```bash
   pip install -r requirements.txt
   ```

2.  Run a command (requires *raw-socket* capability or root):

   ```bash
   sudo python -m pla_util_py --interface eth0 discover
   ```

   Typical output:

   ```text
   20:23:51:aa:bb:cc via MII1 interface, HFID: tpver_701J11_200417_901
   20:23:51:dd:ee:01 via PLC interface,  HFID: tpver_701J11_200417_901
   20:23:51:02:03:04 via PLC interface,  HFID: tpver_701J11_200417_901
   20:23:51:05:06:07 via PLC interface,  HFID: tpver_701J11_200417_901
   ```

3.  See available commands:

   ```bash
   python -m pla_util_py --help
   ```

## QCA examples

```bash
sudo python -m pla_util_py --interface eth0 --pla-mac 58:d6:1f:00:00:01 qca-get-network-info
sudo python -m pla_util_py --interface eth0 --pla-mac 58:d6:1f:00:00:01 qca-get-op-attributes
sudo python -m pla_util_py --interface eth0 --pla-mac 58:d6:1f:00:00:01 qca-get-link-stats --peer-mac 58:d6:1f:00:00:02
```

Optional commands return empty structured values when a target adapter does not
reply. This lets callers distinguish "supported but no data" from parser
failures.

## License

This port is released under the **GNU General Public License, version 3 or
(later)**, the same license as the original C/Ada implementation.

The code was adapted from John Serock's [pla-util] project and therefore
remains GPL-compatible.

[pla-util]: https://github.com/serock/pla-util

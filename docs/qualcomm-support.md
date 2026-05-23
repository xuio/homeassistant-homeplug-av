# Qualcomm Atheros Support

This integration treats Qualcomm Atheros support as a read-first backend. The
normal polling path uses safe management requests for discovery, network
membership, link rates, and diagnostics. The only write-like operation exposed
by Home Assistant is the explicit restart button.

The current implementation is intentionally not a release boundary. Use this
matrix to keep new QCA work scoped and reviewable while the mixed-adapter soak
tests are still running.

## Command Matrix

| Command | MMV / MMType | pla-util-py API | Home Assistant use | Stability |
| --- | --- | --- | --- | --- |
| Software version | `0x00 / VS_SW_VER 0xA000` | `qca_sw_version()` | Adapter firmware, chipset vendor, HFID | Enabled during discovery |
| Network info | `0x01 / VS_NW_INFO 0xA038` | `qca_network_info()` | Discover-list parity and mesh TX/RX rates | Enabled during normal polling |
| Network info stats | `0x01 / VS_NW_INFO_STATS 0xA074` | `qca_network_info_stats()` | Diagnostics service payload | On-demand only |
| Operational attributes | `0x00 / VS_OP_ATTRIBUTES 0xA068` | `qca_op_attributes()` | Adapter hardware and firmware diagnostics | Enabled during discovery |
| Link stats | `0x00 / VS_LNK_STATS 0xA030` | `qca_link_stats(peer_mac)` | Disabled-by-default per-link diagnostic entities; diagnostics service payload | Throttled during normal polling |
| Restart device | `0x00 / VS_RS_DEV 0xA01C` | `restart()` | Restart button | Explicit user action only |

## Entity Coverage

QCA adapters expose the same basic surfaces as the other supported backends:

- Adapter devices with stable MAC-based unique IDs.
- Online state from discovery.
- Firmware, chipset, HomePlug OUI, and capability diagnostics.
- Discover-list membership fields such as TEI, SNID, CCo, role, and network ID.
- Mesh TX/RX PHY rates between adapters.

QCA-only link counters are added as disabled-by-default diagnostic entities on
QCA-to-QCA links. They include MPDU/PBS counters, calculated error rates, FEC
bit error rate, and the latest reported RX PHY rate from `VS_LNK_STATS`.

## Polling Policy

`VS_NW_INFO` remains the source of normal mesh rate updates because it is
lightweight and already needed for QCA parity. `VS_LNK_STATS` is throttled by
the `qca_diagnostic_interval_seconds` option, defaulting to five minutes, so
extra counter polling does not compete with the 30-second mesh-rate refresh.
Set that option to `0` to disable automatic QCA link diagnostics while keeping
on-demand diagnostics available through the service.

## Out of Scope Until Stable

- Key management, network admission, pairing, encryption changes, or other
  state-changing vendor commands.
- Releasing the QCA backend as stable before the mixed QCA/HomePlug network has
  completed longer soak runs.
- Inferring support for unrelated chipsets from QCA-specific replies.

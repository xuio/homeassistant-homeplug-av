from __future__ import annotations

import argparse
import logging
import sys
from typing import Any, List, Dict

from . import __version__
from . import commands


LOG_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pla-util-py",
        description="Python port of the pla-util HomePlug AV2 utility",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("-i", "--interface", help="Network interface to use (e.g. eth0)")
    p.add_argument("-p", "--pla", dest="pla_mac", help="Power line adapter MAC address (unicast)")
    p.add_argument(
        "--log-level",
        choices=LOG_LEVELS,
        default="info",
        help="Set log verbosity (default: info)",
    )
    p.add_argument(
        "-t",
        "--timeout",
        type=float,
        default=0.5,
        help="Network timeout in seconds (default: 0.5)",
    )

    sub = p.add_subparsers(dest="command", required=True, metavar="<command>")

    # Map of command → callable in pla_util_py.commands
    for cmd in (
        "discover",
        "get-capabilities",
        "get-discover-list",
        "get-network-stats",
        "reset",
        "restart",
        "get-hfid",
        "get-id-info",
        "get-network-info",
        "get-station-info",
    ):
        sub.add_parser(cmd, help=f"Run the '{cmd}' request")

    return p


_COMMAND_MAP = {
    "discover": commands.discover,
    "get-capabilities": commands.get_capabilities,
    "get-discover-list": commands.get_discover_list,
    "get-hfid": commands.get_hfid,
    "get-id-info": commands.get_id_info,
    "get-network-info": commands.get_network_info,
    "get-network-stats": commands.get_network_stats,
    "get-station-info": commands.get_station_info,
    "reset": commands.reset,
    "restart": commands.restart,
}


def main(argv: list[str] | None = None):
    args = _build_parser().parse_args(argv)

    logging.basicConfig(level=LOG_LEVELS[args.log_level], format="%(levelname)s: %(message)s")

    func = _COMMAND_MAP[args.command]

    try:
        reply = func(interface=args.interface, pla_mac=args.pla_mac, timeout=args.timeout)  # type: ignore[arg-type]

        if reply is None:
            print("No reply – the adapter did not respond within the timeout.")
            return

        # Dispatch to a formatter per command ------------------------------
        formatter_map = {
            "discover": _fmt_discover,
            "get-capabilities": _fmt_capabilities,
            "get-discover-list": _fmt_discover_list,
            "get-hfid": _fmt_hfid,
            "get-id-info": _fmt_id_info,
            "get-network-info": _fmt_network_info,
            "get-network-stats": _fmt_network_stats,
            "get-station-info": _fmt_station_info,
        }

        fmt_func = formatter_map.get(args.command)

        if fmt_func is None:
            # Fallback: raw Scapy print as before
            if isinstance(reply, list):
                for pkt in reply:
                    print("\n=== Ethernet frame ===")
                    pkt.show()
            else:
                print("\n=== Ethernet frame ===")
                reply.show()
            return

        fmt_func(reply)

    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:  # pylint: disable=broad-except
        logging.error("%s", exc)
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover
    main()

# ---------------------------------------------------------------------------
# Parsers / Formatters
# ---------------------------------------------------------------------------


def _mac_bytes_to_str(b: bytes) -> str:
    return ":".join(f"{x:02x}" for x in b)


def _av_version_text(val: int) -> str:
    mapping = {0: "1.1", 1: "2.0"}
    return mapping.get(val, "Unknown")


def _fmt_discover(pkts: Any):
    """Pretty-print adapter information from *discover* replies."""

    if not isinstance(pkts, list):
        pkts = [pkts]

    from scapy.layers.l2 import Ether  # type: ignore
    from scapy.packet import Raw  # type: ignore

    seen = set()

    iface_map = {0: "MII0", 1: "MII1", 2: "PLC", 3: "PLC", 4: "SDR"}

    for pkt in pkts:
        mac = pkt[Ether].src.lower()
        if mac in seen:
            continue
        seen.add(mac)

        payload: bytes = bytes(pkt[Raw].load)
        if len(payload) < 12:
            continue

        iface_code = payload[9]
        interface = iface_map.get(iface_code, f"Unknown({iface_code})")

        hfid_len = payload[10]
        hfid_bytes = payload[11 : 11 + hfid_len]
        try:
            hfid = hfid_bytes.decode("ascii", errors="replace").rstrip("\x00")
        except Exception:
            hfid = "<un-decodable>"

        print(f"{mac} via {interface} interface, HFID: {hfid}")


def _fmt_capabilities(pkt: Any):
    from scapy.layers.l2 import Ether  # type: ignore
    from scapy.packet import Raw  # type: ignore

    payload: bytes = bytes(pkt[Raw].load)

    if len(payload) < 31:
        print("Reply payload too short – cannot parse capabilities.")
        return

    av_version = _av_version_text(payload[5])
    mac_addr = _mac_bytes_to_str(payload[6:12])
    oui = (payload[12] << 16) | (payload[13] << 8) | payload[14]
    proxy = "CAPABLE" if payload[18] else "NOT_CAPABLE"
    bcco = "CAPABLE" if payload[19] else "NOT_CAPABLE"
    impl_ver = payload[28] | (payload[29] << 8)

    def _row(label: str, value: str):
        print(f"{label:<23}{value}")

    _row("AV Version:", av_version)
    _row("MAC Address:", mac_addr)
    _row("OUI:", f"{oui:06x}")
    _row("Backup CCo:", bcco)
    _row("Proxy:", proxy)
    _row("Implementation Version:", str(impl_ver))


def _fmt_network_stats(pkt: Any):
    from scapy.packet import Raw  # type: ignore

    payload: bytes = bytes(pkt[Raw].load)

    if len(payload) < 11:
        print("Payload too short – cannot parse network stats.")
        return

    reported_stations = payload[9]
    print(f"Number of stations (reported): {reported_stations}")

    # The reply may be split across multiple confirmations; we can only parse
    # the entries present in *this* frame.
    available = (len(payload) - 10) // 10
    if available != reported_stations:
        print(f"Entries in this frame: {available} (partial)")

    offset = 10
    for idx in range(1, available + 1):
        mac = _mac_bytes_to_str(payload[offset : offset + 6])
        to_rate = payload[offset + 6] | ((payload[offset + 7] & 0x07) << 8)
        from_rate = payload[offset + 8] | ((payload[offset + 9] & 0x07) << 8)
        
        print(f"Station {idx}:")
        print(f"  Destination Address (DA): {mac}")
        print(f"  Avg PHY Data Rate to DA:   {to_rate:3d} Mbps")
        print(f"  Avg PHY Data Rate from DA: {from_rate:3d} Mbps")

        offset += 10


def _fmt_discover_list(pkt: Any):
    from scapy.packet import Raw  # type: ignore

    payload: bytes = bytes(pkt[Raw].load)

    if len(payload) < 7:
        print("Payload too short – cannot parse discover list.")
        return

    station_count = payload[5]

    print(f"Number of stations: {station_count}")

    octets_per_station = 12
    offset = 6

    def yes_no(val: bool) -> str:
        return "YES" if val else "NO"

    def signal_level_desc(level: int) -> str:
        if level == 0:
            return "not available"
        if level == 15:
            return "SL <= -75 dB"
        if level == 1:
            upper = 0
            lower = -5 * (level + 1)
            return f"{lower} dB < SL <= {upper} dB"
        upper = -5 * level
        lower = -5 * (level + 1)
        return f"{lower} dB < SL <= {upper} dB"

    for idx in range(station_count):
        base = offset + idx * octets_per_station
        if base + octets_per_station > len(payload):
            print("Truncated payload – aborting parse.")
            break

        mac = _mac_bytes_to_str(payload[base : base + 6])
        tei = payload[base + 6]
        same_network = payload[base + 7] != 0
        snid = payload[base + 8] & 0x0F
        role_byte = payload[base + 9]
        cco = (role_byte & 0x20) != 0
        pco = (role_byte & 0x40) != 0
        bcco = (role_byte & 0x80) != 0
        signal_level = payload[base + 10]

        print(f"Station {idx+1}:")
        print(f"  MAC Address:         {mac}")
        print(f"  TEI:                 {tei}")
        print(f"  Same Network:        {yes_no(same_network)}")
        print(f"  SNID:                {snid}")
        print(f"  CCo:                 {yes_no(cco)}")
        print(f"  PCo:                 {yes_no(pco)}")
        print(f"  Backup CCo:          {yes_no(bcco)}")
        print(f"  Signal Level:        {signal_level_desc(signal_level)}")

    # Networks count is after stations
    networks_idx = 6 + station_count * octets_per_station
    if networks_idx >= len(payload):
        return

    network_count = payload[networks_idx]
    print(f"Number of Networks: {network_count}")
    # Parsing network details omitted for brevity.


def _fmt_hfid(pkt: Any):
    from scapy.packet import Raw  # type: ignore

    payload: bytes = bytes(pkt[Raw].load)
    if len(payload) < 14:
        print("Payload too short – cannot parse HFID.")
        return
    hfid = payload[12:].decode("ascii", errors="replace").rstrip("\x00")
    print(hfid)


def _fmt_id_info(pkt: Any):
    from scapy.packet import Raw  # type: ignore

    payload: bytes = bytes(pkt[Raw].load)
    if len(payload) < 13:
        print("Payload too short – cannot parse id-info.")
        return
    hpav_map = {0: "1.1", 1: "2.0", 0xFF: "Not a HomePlug AV device"}
    version = hpav_map.get(payload[9], "Unknown")
    mcs_map = {0: "MIMO_NOT_SUPPORTED", 1: "SELECTION_DIVERSITY", 2: "MIMO_WITH_BEAM_FORMING"}
    mcs = mcs_map.get(payload[11], "UNKNOWN") if version == "2.0" else "MIMO_NOT_SUPPORTED"

    print("HomePlug AV Version:", version)
    print("MCS:                 ", mcs)


def _fmt_network_info(pkt: Any):
    from scapy.packet import Raw  # type: ignore

    payload: bytes = bytes(pkt[Raw].load)
    if len(payload) < 11:
        print("Payload too short – cannot parse network-info.")
        return

    networks = payload[9]
    print(f"Number of networks: {networks}")

    info_entries = []
    offset = 10
    for _ in range(networks):
        if offset + 19 > len(payload):
            break
        entry = {
            "nid": int.from_bytes(payload[offset : offset + 7], "little") & 0x3FFFFFFFFFFFF,
            "snid": payload[offset + 7] & 0x0F,
            "tei": payload[offset + 8],
            "station_role": payload[offset + 9],
            "cco_mac": _mac_bytes_to_str(payload[offset + 10 : offset + 16]),
            "network_kind": payload[offset + 16],
            "num_coord": payload[offset + 17],
            "status": payload[offset + 18],
        }
        info_entries.append(entry)
        offset += 19

    # Now parse Backup CCo MAC addresses list
    for idx, entry in enumerate(info_entries):
        bcco_start = 10 + 19 * networks + idx * 6
        if bcco_start + 6 <= len(payload):
            entry["bcco_mac"] = _mac_bytes_to_str(payload[bcco_start : bcco_start + 6])
        else:
            entry["bcco_mac"] = "--"

    # Print
    status_map = {0: "JOINED", 1: "NOT_JOINED_HAVE_NMK", 2: "NOT_JOINED_NO_NMK"}
    station_role_map = {0: "UNASSOC_STA", 1: "UNASSOC_CCO", 2: "STA", 3: "CCO", 4: "BACKUP_CCO"}

    for idx, e in enumerate(info_entries, 1):
        nid_hex = f"{e['nid']:014x}"
        sl = 'SL-SC' if (e['nid'] & 0x1000000000000)==0 else 'SL-HS'
        print(f"Network {idx}:")
        print(f"  NID:                             {nid_hex} ({sl})")
        print(f"  SNID:                            {e['snid']}")
        print(f"  TEI:                             {e['tei']}")
        print(f"  CCo MAC Address:                 {e['cco_mac']}")
        print(f"  Backup CCo MAC Address:          {e['bcco_mac']}")
        print(f"  Number of Coordinating Networks: {e['num_coord']}")
        print(f"  Station Role:                    {station_role_map.get(e['station_role'],'UNKNOWN')}")
        print(f"  Network Kind:                    { 'IN_HOME_NETWORK' if e['network_kind']==0 else 'ACCESS_NETWORK'}")
        print(f"  Status:                          {status_map.get(e['status'],'UNKNOWN')}")


def _fmt_station_info(pkt: Any):
    from scapy.packet import Raw  # type: ignore

    payload: bytes = bytes(pkt[Raw].load)
    if len(payload) < 18:
        print("Payload too short – cannot parse station info.")
        return
    chip_version_id = int.from_bytes(payload[9:13], "little")
    chip_version = "UNKNOWN"
    mapping = {
        0x017F0000: "BCM60500_A0",
        0x017F024E: "BCM60500_A1",
        0x117F024E: "BCM60500_B0",
        0x017F024F: "BCM60333_A1",
        0x117F024F: "BCM60333_B0",
        0x017F025A: "BCM60335_A0",
    }
    chip_version = mapping.get(chip_version_id, "UNKNOWN")
    hw_version = int.from_bytes(payload[13:17], "little")

    print("Chip Version:                    ", chip_version)
    print("Hardware Version:                ", f"0x{hw_version:08x}")

from __future__ import annotations

"""Utility functions that turn raw Scapy reply packets into Python data
structures.  They are used by :pymod:`pla_util_py.api` and may also be handy
when writing your own scripts.
"""

from typing import Any, List, Dict
from scapy.layers.l2 import Ether  # type: ignore
from scapy.packet import Raw  # type: ignore

__all__ = [
    "parse_discover",
    "parse_capabilities",
    "parse_hfid",
    "parse_id_info",
    "parse_discover_list",
    "parse_network_stats",
    "parse_network_info",
    "parse_station_info",
]


def _mac_bytes_to_str(b: bytes) -> str:
    return ":".join(f"{x:02x}" for x in b)


# ---------------------------------------------------------------------------
# Individual parsers
# ---------------------------------------------------------------------------


def parse_discover(pkts: List[Any]) -> List[Dict[str, str]]:
    """Return a list of discovered adapters."""

    adapters: List[Dict[str, str]] = []
    seen = set()
    iface_map = {0: "MII0", 1: "MII1", 2: "PLC", 3: "PLC", 4: "SDR"}

    for pkt in pkts:
        mac = pkt[Ether].src.lower()
        if mac in seen:
            continue
        seen.add(mac)

        payload = bytes(pkt[Raw].load)
        if len(payload) < 12:
            continue
        iface_code = payload[9]
        hfid_len = payload[10]
        hfid = payload[11 : 11 + hfid_len].decode("ascii", errors="replace").rstrip("\x00")

        adapters.append({
            "mac": mac,
            "interface": iface_map.get(iface_code, "UNKNOWN"),
            "hfid": hfid,
        })

    return adapters


def parse_capabilities(pkt: Any) -> Dict[str, Any]:
    payload = bytes(pkt[Raw].load)
    if len(payload) < 31:
        raise ValueError("payload too short")

    return {
        "av_version": {0: "1.1", 1: "2.0"}.get(payload[5], "Unknown"),
        "mac_address": _mac_bytes_to_str(payload[6:12]),
        "oui": f"{(payload[12]<<16)|(payload[13]<<8)|payload[14]:06x}",
        "backup_cco": bool(payload[19]),
        "proxy": bool(payload[18]),
        "implementation_version": (payload[28] | (payload[29] << 8)),
    }


def parse_hfid(pkt: Any) -> str:
    payload = bytes(pkt[Raw].load)
    if len(payload) < 14:
        raise ValueError("payload too short")
    return payload[12:].decode("ascii", errors="replace").rstrip("\x00")


def parse_id_info(pkt: Any) -> Dict[str, str]:
    payload = bytes(pkt[Raw].load)
    ver = {0: "1.1", 1: "2.0", 0xFF: "Not HPAV"}.get(payload[9], "Unknown")
    mcs = "MIMO_NOT_SUPPORTED" if ver != "2.0" else {0: "MIMO_NOT_SUPPORTED", 1: "SELECTION_DIVERSITY", 2: "MIMO_WITH_BEAM_FORMING"}.get(payload[11], "UNKNOWN")
    return {"hpav_version": ver, "mcs": mcs}


def parse_network_stats(pkt: Any) -> List[Dict[str, int]]:
    payload = bytes(pkt[Raw].load)
    available = (len(payload) - 10) // 10
    stats = []
    offset = 10
    for _ in range(available):
        stats.append({
            "mac": _mac_bytes_to_str(payload[offset : offset + 6]),
            "to_rate": payload[offset + 6] | ((payload[offset + 7] & 0x07) << 8),
            "from_rate": payload[offset + 8] | ((payload[offset + 9] & 0x07) << 8),
        })
        offset += 10
    return stats


def parse_discover_list(pkt: Any) -> Dict[str, Any]:
    payload = bytes(pkt[Raw].load)
    station_count = payload[5]
    octets_per_station = 12
    offset = 6
    stations = []
    for _ in range(station_count):
        base = offset
        stations.append({
            "mac": _mac_bytes_to_str(payload[base : base + 6]),
            "tei": payload[base + 6],
            "same_network": payload[base + 7] != 0,
        })
        offset += octets_per_station
    return {"stations": stations}


def parse_network_info(pkt: Any) -> List[Dict[str, Any]]:
    payload = bytes(pkt[Raw].load)
    networks = payload[9]
    entries = []
    offset = 10
    for idx in range(networks):
        entry = {
            "nid": int.from_bytes(payload[offset : offset + 7], "little") & 0x3FFFFFFFFFFFF,
            "cco_mac": _mac_bytes_to_str(payload[offset + 10 : offset + 16]),
        }
        entries.append(entry)
        offset += 19
    # backup CCo macs
    for idx in range(networks):
        bcco_start = 10 + 19 * networks + idx * 6
        if bcco_start + 6 <= len(payload):
            entries[idx]["bcco_mac"] = _mac_bytes_to_str(payload[bcco_start : bcco_start + 6])
    return entries


def parse_station_info(pkt: Any) -> Dict[str, Any]:
    payload = bytes(pkt[Raw].load)
    chip_id = int.from_bytes(payload[9:13], "little")
    return {
        "chip_version_id": chip_id,
    }

from __future__ import annotations

"""Utility functions that turn raw Scapy reply packets into Python data
structures.  They are used by :pymod:`pla_util_py.api` and may also be handy
when writing your own scripts.
"""

import re
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
    "parse_qca_sw_version",
    "parse_qca_network_info",
    "parse_qca_network_info_stats",
    "parse_qca_network_stats",
    "parse_qca_op_attributes",
    "parse_qca_link_stats",
]


def _mac_bytes_to_str(b: bytes) -> str:
    return ":".join(f"{x:02x}" for x in b)


def _payload(pkt: Any) -> bytes:
    if pkt is None:
        raise ValueError("missing packet")
    return bytes(pkt[Raw].load)


def _source_mac(pkt: Any) -> str | None:
    if pkt is None or Ether not in pkt:
        return None
    return pkt[Ether].src.lower()


def _fixed_ascii(data: bytes) -> str:
    return data.split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()


def _u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "little")


def _u64(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 8], "little")


def _error_rate_percent(passed: int, failed: int) -> float:
    if passed or failed:
        return round((failed * 100) / (passed + failed), 4)
    return 0.0


_QCA_CHIPSET_BY_IDENT = {
    0x001B587C: "QCA7005",
    0x001B589C: "QCA7000",
    0x001B58AC: "QCA7006AQ",
    0x001B58BC: "QCA6411",
    0x001B58DC: "QCA7000",
    0x001B58EC: "QCA6410",
    0x001CFC00: "QCA7420",
    0x001CFCFC: "QCA7420",
    0x001D4C00: "QCA7500",
    0x001D4C0F: "QCA7500",
    0x0E001D1A: "QCA7451",
    0x0F001D1A: "QCA7450",
}

_QCA_CHIPSET_BY_CLASS = {
    0x01: "INT6000",
    0x02: "INT6300",
    0x03: "INT6400/AR7400/AR6405",
    0x06: "PANTHER/LYNX",
    0x07: "QCA7450",
    0x08: "QCA7451",
    0x20: "QCA7420",
    0x21: "QCA6410/QCA6411/QCA7006AQ",
    0x22: "QCA7000/QCA7005",
    0x30: "QCA7500",
}


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
        # Confirm this is a discover confirmation: first two bytes 0x02 0x71
        if len(payload) < 12 or payload[0] != 0x02 or payload[1] != 0x71:
            continue
        iface_code = payload[9]
        # HFID starts at byte 11 (offset 12 in Ada, which is 11 here) and runs to end
        hfid = payload[11:].decode("ascii", errors="replace").rstrip("\x00")

        adapters.append({
            "mac": mac,
            "interface": iface_map.get(iface_code, "UNKNOWN"),
            "hfid": hfid,
        })

    return adapters


def parse_capabilities(pkt: Any) -> Dict[str, Any]:
    payload = _payload(pkt)
    if len(payload) < 31:
        raise ValueError("payload too short")

    return {
        "av_version": {0: "1.1", 1: "2.0"}.get(payload[5], "Unknown"),
        "mac_address": _mac_bytes_to_str(payload[6:12]),
        "oui": f"{(payload[12]<<16)|(payload[13]<<8)|payload[14]:06x}",
        "source_mac": _source_mac(pkt),
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


def parse_network_stats(pkts: list[Any] | Any) -> List[Dict[str, int]]:
    if not isinstance(pkts, list):
        pkts = [pkts]

    combined: Dict[str, Dict[str, int]] = {}

    for pkt in pkts:
        payload = bytes(pkt[Raw].load)
        offset = 10
        while offset + 10 <= len(payload):
            mac = _mac_bytes_to_str(payload[offset : offset + 6])
            to_rate = payload[offset + 6] | ((payload[offset + 7] & 0x07) << 8)
            from_rate = payload[offset + 8] | ((payload[offset + 9] & 0x07) << 8)
            combined[mac] = {"mac": mac, "to_rate": to_rate, "from_rate": from_rate}
            offset += 10

    return list(combined.values())


def parse_discover_list(pkt: Any) -> Dict[str, Any]:
    payload = bytes(pkt[Raw].load)
    station_count = payload[5]
    octets_per_station = 12
    offset = 6
    stations = []
    for _ in range(station_count):
        base = offset
        role_byte = payload[base + 9]
        stations.append({
            "mac": _mac_bytes_to_str(payload[base : base + 6]),
            "tei": payload[base + 6],
            "same_network": payload[base + 7] != 0,
            "snid": payload[base + 8] & 0x0F,
            "cco": (role_byte & 0x20) != 0,
            "pco": (role_byte & 0x40) != 0,
            "bcco": (role_byte & 0x80) != 0,
            "signal_level": payload[base + 10],
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


def parse_qca_sw_version(pkt: Any) -> Dict[str, Any]:
    """Parse a Qualcomm/Atheros VS_SW_VER confirmation."""

    payload = _payload(pkt)
    if len(payload) < 9:
        raise ValueError("payload too short")
    if payload[0] != 0x00 or int.from_bytes(payload[1:3], "little") != 0xA001:
        raise ValueError("not a VS_SW_VER confirmation")

    status = payload[6]
    device_class = payload[7]
    version_length = payload[8]
    version_end = min(len(payload), 9 + version_length)
    version = payload[9:version_end].decode("ascii", errors="replace").rstrip("\x00")

    ident = None
    if len(payload) >= 9 + 254 + 4:
        ident = int.from_bytes(payload[9 + 254 : 9 + 254 + 4], "little")

    chipset = _QCA_CHIPSET_BY_IDENT.get(ident or -1)
    if chipset is None:
        match = re.search(r"\b(QCA[0-9A-Z]+|AR[0-9A-Z]+|INT[0-9A-Z]+)\b", version)
        chipset = match.group(1) if match else _QCA_CHIPSET_BY_CLASS.get(device_class, "UNKNOWN")

    return {
        "backend": "qca",
        "vendor": "Qualcomm Atheros",
        "chipset": chipset,
        "firmware": version,
        "device_class": device_class,
        "ident": ident,
        "status": status,
        "source_mac": _source_mac(pkt),
    }


def _qca_coupling_name(value: int) -> str:
    return "Alternate" if value else "Primary"


def _qca_role_name(role: int) -> str:
    return {0x00: "STA", 0x01: "PROXY_STA", 0x02: "CCO"}.get(role, f"UNKNOWN({role})")


def parse_qca_network_info(pkt: Any) -> Dict[str, Any]:
    """Parse a Qualcomm/Atheros VS_NW_INFO confirmation."""

    payload = _payload(pkt)
    if len(payload) < 12:
        raise ValueError("payload too short")
    if payload[0] != 0x01 or int.from_bytes(payload[1:3], "little") != 0xA039:
        raise ValueError("not a VS_NW_INFO confirmation")

    data_length = int.from_bytes(payload[10:12], "little")
    data = payload[12 : 12 + data_length] if data_length else payload[12:]
    if len(data) < 2:
        raise ValueError("network data too short")

    result: Dict[str, Any] = {
        "source": _source_mac(pkt),
        "sub_version": payload[8],
        "networks": [],
        "stations": [],
    }

    offset = 0
    network_count = data[offset + 1]
    offset += 2

    for _ in range(network_count):
        if offset + 32 > len(data):
            break

        network = {
            "nid": data[offset : offset + 7].hex(":"),
            "snid": data[offset + 9],
            "tei": data[offset + 10],
            "role": _qca_role_name(data[offset + 15]),
            "role_code": data[offset + 15],
            "cco_mac": _mac_bytes_to_str(data[offset + 16 : offset + 22]),
            "cco_tei": data[offset + 22],
            "station_count": data[offset + 26],
            "stations": [],
        }
        offset += 32

        for _ in range(network["station_count"]):
            if offset + 24 > len(data):
                break

            coupling = data[offset + 18]
            station = {
                "mac": _mac_bytes_to_str(data[offset : offset + 6]),
                "tei": data[offset + 6],
                "bda": _mac_bytes_to_str(data[offset + 10 : offset + 16]),
                "to_rate": int.from_bytes(data[offset + 16 : offset + 18], "little"),
                "from_rate": int.from_bytes(data[offset + 20 : offset + 22], "little"),
                "tx_coupling": _qca_coupling_name(coupling & 0x0F),
                "rx_coupling": _qca_coupling_name((coupling >> 4) & 0x0F),
                "role": "CCO" if data[offset + 6] == network["cco_tei"] else "STA",
            }
            network["stations"].append(station)
            result["stations"].append(station)
            offset += 24

        result["networks"].append(network)

    return result


def parse_qca_network_info_stats(pkt: Any) -> Dict[str, Any]:
    """Parse a Qualcomm/Atheros VS_NW_INFO_STATS confirmation."""

    payload = _payload(pkt)
    if len(payload) < 17:
        raise ValueError("payload too short")
    if payload[0] != 0x01 or int.from_bytes(payload[1:3], "little") != 0xA075:
        raise ValueError("not a VS_NW_INFO_STATS confirmation")

    data_length = int.from_bytes(payload[10:12], "little")
    data_end = min(len(payload), 12 + data_length) if data_length else len(payload)
    if data_end <= 17:
        data_end = len(payload)
    data = payload[17:data_end]

    result: Dict[str, Any] = {
        "source": _source_mac(pkt),
        "sub_version": payload[8],
        "first_tei": payload[12],
        "num_stations_reported": payload[13],
        "in_avln": payload[16],
        "networks": [],
        "stations": [],
    }

    offset = 0
    for _ in range(result["in_avln"]):
        if offset + 29 > len(data):
            break

        station_count = data[offset + 29] if offset + 30 <= len(data) else 0
        network = {
            "nid": data[offset : offset + 7].hex(":"),
            "snid": data[offset + 8],
            "tei": data[offset + 9],
            "role": _qca_role_name(data[offset + 12]),
            "role_code": data[offset + 12],
            "cco_mac": _mac_bytes_to_str(data[offset + 13 : offset + 19]),
            "access": data[offset + 19],
            "neighbor_networks": data[offset + 20],
            "cco_tei": data[offset + 21],
            "station_count": station_count,
            "stations": [],
        }
        offset += 35 if offset + 35 <= len(data) else 29

        for _ in range(network["station_count"]):
            if offset + 24 > len(data):
                break

            coupling = data[offset + 18]
            station = {
                "mac": _mac_bytes_to_str(data[offset : offset + 6]),
                "tei": data[offset + 6],
                "bda": _mac_bytes_to_str(data[offset + 10 : offset + 16]),
                "to_rate": int.from_bytes(data[offset + 16 : offset + 18], "little"),
                "from_rate": int.from_bytes(data[offset + 20 : offset + 22], "little"),
                "tx_coupling": _qca_coupling_name(coupling & 0x0F),
                "rx_coupling": _qca_coupling_name((coupling >> 4) & 0x0F),
                "role": "CCO" if data[offset + 6] == network["cco_tei"] else "STA",
            }
            network["stations"].append(station)
            result["stations"].append(station)
            offset += 24

        result["networks"].append(network)

    return result


def parse_qca_network_stats(pkt: Any) -> List[Dict[str, Any]]:
    """Return QCA VS_NW_INFO peer rates in the existing stats shape."""

    info = parse_qca_network_info(pkt)
    return [
        {
            "mac": station["mac"],
            "to_rate": station["to_rate"],
            "from_rate": station["from_rate"],
            "bda": station.get("bda"),
            "tx_coupling": station.get("tx_coupling"),
            "rx_coupling": station.get("rx_coupling"),
            "role": station.get("role"),
        }
        for station in info["stations"]
    ]


def _parse_qca_tx_link_stats(data: bytes) -> dict[str, Any]:
    if len(data) < 40:
        raise ValueError("TX link statistics payload too short")
    tx_mpdu_acked = _u64(data, 0)
    tx_mpdu_collisions = _u64(data, 8)
    tx_mpdu_failed = _u64(data, 16)
    tx_pbs_passed = _u64(data, 24)
    tx_pbs_failed = _u64(data, 32)
    return {
        "tx_mpdu_acked": tx_mpdu_acked,
        "tx_mpdu_collisions": tx_mpdu_collisions,
        "tx_mpdu_failed": tx_mpdu_failed,
        "tx_pbs_passed": tx_pbs_passed,
        "tx_pbs_failed": tx_pbs_failed,
        "tx_pbs_error_rate_percent": _error_rate_percent(tx_pbs_passed, tx_pbs_failed),
        "tx_mpdu_error_rate_percent": _error_rate_percent(tx_mpdu_acked, tx_mpdu_failed),
    }


def _parse_qca_rx_link_stats(data: bytes) -> dict[str, Any]:
    if len(data) < 49:
        raise ValueError("RX link statistics payload too short")
    rx_mpdu_acked = _u64(data, 0)
    rx_mpdu_failed = _u64(data, 8)
    rx_pbs_passed = _u64(data, 16)
    rx_pbs_failed = _u64(data, 24)
    sum_turbo_ber_passed = _u64(data, 32)
    sum_turbo_ber_failed = _u64(data, 40)
    interval_count = data[48]
    intervals: list[dict[str, Any]] = []
    offset = 49
    for slot in range(interval_count):
        if offset + 33 > len(data):
            break
        pbs_passed = _u64(data, offset + 1)
        pbs_failed = _u64(data, offset + 9)
        ber_passed = _u64(data, offset + 17)
        ber_failed = _u64(data, offset + 25)
        intervals.append(
            {
                "slot": slot,
                "rx_phy_rate_mbps": data[offset],
                "rx_pbs_passed": pbs_passed,
                "rx_pbs_failed": pbs_failed,
                "rx_pbs_error_rate_percent": _error_rate_percent(
                    pbs_passed,
                    pbs_failed,
                ),
                "rx_ber_passed": ber_passed,
                "rx_ber_failed": ber_failed,
                "rx_ber_error_rate_percent": _error_rate_percent(
                    ber_passed,
                    ber_failed,
                ),
            }
        )
        offset += 33

    fec_bit_error_rate = 0.0
    if sum_turbo_ber_passed or sum_turbo_ber_failed:
        total_bit_errors = 100 * (sum_turbo_ber_passed + sum_turbo_ber_failed)
        total_bits = 8 * 520 * (rx_pbs_passed + rx_pbs_failed)
        if total_bits:
            fec_bit_error_rate = round(total_bit_errors / total_bits, 4)

    return {
        "rx_mpdu_acked": rx_mpdu_acked,
        "rx_mpdu_failed": rx_mpdu_failed,
        "rx_pbs_passed": rx_pbs_passed,
        "rx_pbs_failed": rx_pbs_failed,
        "rx_ber_passed": sum_turbo_ber_passed,
        "rx_ber_failed": sum_turbo_ber_failed,
        "rx_interval_count": interval_count,
        "rx_intervals": intervals,
        "rx_pbs_error_rate_percent": _error_rate_percent(rx_pbs_passed, rx_pbs_failed),
        "rx_mpdu_error_rate_percent": _error_rate_percent(rx_mpdu_acked, rx_mpdu_failed),
        "rx_fec_bit_error_rate_percent": fec_bit_error_rate,
    }


def parse_qca_link_stats(pkt: Any) -> Dict[str, Any]:
    """Parse a Qualcomm/Atheros VS_LNK_STATS confirmation."""

    payload = _payload(pkt)
    if len(payload) < 10:
        raise ValueError("payload too short")
    if payload[0] != 0x00 or int.from_bytes(payload[1:3], "little") != 0xA031:
        raise ValueError("not a VS_LNK_STATS confirmation")

    result: Dict[str, Any] = {
        "source": _source_mac(pkt),
        "status": payload[6],
        "direction": payload[7],
        "lid": payload[8],
        "tei": payload[9],
    }
    if result["status"] != 0:
        return result

    stats = payload[10:]
    if result["direction"] == 0:
        result["tx"] = _parse_qca_tx_link_stats(stats)
    elif result["direction"] == 1:
        result["rx"] = _parse_qca_rx_link_stats(stats)
    elif result["direction"] == 2:
        result["tx"] = _parse_qca_tx_link_stats(stats)
        result["rx"] = _parse_qca_rx_link_stats(stats[40:])
    else:
        result["raw_stats_length"] = len(stats)
    return result


def parse_qca_op_attributes(pkt: Any) -> Dict[str, Any]:
    """Parse a Qualcomm/Atheros VS_OP_ATTRIBUTES confirmation."""

    payload = _payload(pkt)
    if len(payload) < 15:
        raise ValueError("payload too short")
    if payload[0] != 0x00 or int.from_bytes(payload[1:3], "little") != 0xA069:
        raise ValueError("not a VS_OP_ATTRIBUTES confirmation")

    status = int.from_bytes(payload[6:8], "little")
    data_length = int.from_bytes(payload[13:15], "little")
    data = payload[15 : 15 + data_length] if data_length else payload[15:]
    result: Dict[str, Any] = {
        "op_status": status,
        "op_rtype": payload[12],
        "op_attribute_length": len(data),
        "source_mac": _source_mac(pkt),
    }
    if status:
        return result

    # Firmware 3.3.5 and later layout used by AR7x00/QCA devices.
    if len(data) >= 114:
        result.update(
            {
                "device_family": _fixed_ascii(data[0:16]),
                "device_type": _fixed_ascii(data[16:32]),
                "op_fw_major_version": _u32(data, 32),
                "op_fw_minor_version": _u32(data, 36),
                "op_composite_version": _u32(data, 40),
                "op_sustaining_version": _u32(data, 44),
                "op_build_number": _u32(data, 48),
                "op_build_date": _fixed_ascii(data[52:60]),
                "op_release_type": _fixed_ascii(data[60:72]),
                "op_dram_type": data[72],
                "op_reserved_flag": data[73],
                "op_line_frequency": data[74],
                "op_dram_size_mb": _u32(data, 75),
                "op_ram_block_rx_count": _u32(data, 79),
                "op_ram_block_tx_count": _u32(data, 83),
                "op_ram_block_shared_count": _u32(data, 87),
                "op_ram_block_free_rx_count": _u32(data, 91),
                "op_ram_block_free_tx_count": _u32(data, 95),
                "op_ram_block_free_shared_count": _u32(data, 99),
                "op_relative_snr_diff_db": _u32(data, 103),
                "op_dsp384_threshold": _u32(data, 107),
                "op_afe_tx_gain_db": data[111],
                "op_auth_mode": data[112],
                "op_microcontroller_diag_enabled": bool(data[113]),
            }
        )
        result["op_firmware_version"] = ".".join(
            str(result[key])
            for key in (
                "op_fw_major_version",
                "op_fw_minor_version",
                "op_composite_version",
                "op_build_number",
            )
        )
        return result

    # Older INT6x00 layout.
    if len(data) >= 80:
        result.update(
            {
                "device_family": _fixed_ascii(data[0:16]),
                "device_type": _fixed_ascii(data[16:32]),
                "op_fw_major_version": _u32(data, 32),
                "op_fw_minor_version": _u32(data, 36),
                "op_composite_version": _u32(data, 40),
                "op_sustaining_version": _u32(data, 44),
                "op_build_number": _u32(data, 48),
                "op_build_date": _fixed_ascii(data[52:60]),
                "op_release_type": _fixed_ascii(data[60:72]),
                "op_dram_type": data[72],
                "op_reserved_flag": data[73],
                "op_line_frequency": data[74],
                "op_dram_size_mb": _u32(data, 75),
                "op_auth_mode": data[79],
            }
        )
        result["op_firmware_version"] = ".".join(
            str(result[key])
            for key in (
                "op_fw_major_version",
                "op_fw_minor_version",
                "op_composite_version",
                "op_build_number",
            )
        )

    return result

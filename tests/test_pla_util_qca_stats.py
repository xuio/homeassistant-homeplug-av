"""Tests for optional Qualcomm Atheros stats payloads and parsers."""

from __future__ import annotations

import sys
from pathlib import Path

LIB_PATH = Path(__file__).parents[1] / "custom_components" / "homeplug_av" / "pla-util-py"
if str(LIB_PATH) not in sys.path:
    sys.path.insert(0, str(LIB_PATH))

from pla_util_py.messages import qca_payload
from pla_util_py.parsers import parse_qca_link_stats, parse_qca_network_info_stats
from scapy.layers.l2 import Ether  # type: ignore
from scapy.packet import Raw  # type: ignore


def _packet(payload: bytes):
    return Ether(src="58:d6:1f:00:00:01", dst="00:11:22:33:44:55") / Raw(load=payload)


def _u64(value: int) -> bytes:
    return value.to_bytes(8, "little")


def test_qca_stats_payloads_have_expected_headers() -> None:
    nw_stats = qca_payload("network_info_stats")
    link_stats = qca_payload(
        "link_stats",
        peer_mac="aa:bb:cc:dd:ee:ff",
        direction=2,
        lid=0xF8,
    )

    assert nw_stats.startswith(b"\x01\x74\xa0\x00\x00\x00\xb0\x52")
    assert link_stats.startswith(
        b"\x00\x30\xa0\x00\xb0\x52\x00\x02\xf8\xaa\xbb\xcc\xdd\xee\xff"
    )


def test_parse_qca_network_info_stats() -> None:
    network = bytearray(35)
    network[0:7] = b"\x01\x02\x03\x04\x05\x06\x07"
    network[8] = 3
    network[9] = 4
    network[12] = 2
    network[13:19] = bytes.fromhex("58d61f000001")
    network[19] = 1
    network[20] = 2
    network[21] = 4
    network[29] = 1

    station = bytearray(24)
    station[0:6] = bytes.fromhex("58d61f000002")
    station[6] = 5
    station[10:16] = bytes.fromhex("58d61f000003")
    station[16:18] = (321).to_bytes(2, "little")
    station[18] = 0x10
    station[20:22] = (123).to_bytes(2, "little")

    stats_data = bytes(network) + bytes(station)
    data_len = 5 + len(stats_data)
    payload = (
        b"\x01\x75\xa0\x00\x00\x00\xb0\x52"
        + b"\x01\x00"
        + data_len.to_bytes(2, "little")
        + b"\x00\x01\x00\x00\x01"
        + stats_data
    )

    parsed = parse_qca_network_info_stats(_packet(payload))

    assert parsed["source"] == "58:d6:1f:00:00:01"
    assert parsed["sub_version"] == 1
    assert parsed["in_avln"] == 1
    assert parsed["networks"][0]["nid"] == "01:02:03:04:05:06:07"
    assert parsed["networks"][0]["role"] == "CCO"
    assert parsed["networks"][0]["station_count"] == 1
    assert parsed["stations"][0]["to_rate"] == 321
    assert parsed["stations"][0]["from_rate"] == 123
    assert parsed["stations"][0]["tx_coupling"] == "Primary"
    assert parsed["stations"][0]["rx_coupling"] == "Alternate"


def test_parse_qca_link_stats() -> None:
    tx = (
        _u64(10)
        + _u64(1)
        + _u64(2)
        + _u64(100)
        + _u64(5)
    )
    rx = (
        _u64(20)
        + _u64(3)
        + _u64(200)
        + _u64(10)
        + _u64(8)
        + _u64(2)
        + b"\x01"
        + b"\x7b"
        + _u64(50)
        + _u64(5)
        + _u64(4)
        + _u64(1)
    )
    payload = b"\x00\x31\xa0\x00\xb0\x52" + b"\x00\x02\xf8\x07" + tx + rx

    parsed = parse_qca_link_stats(_packet(payload))

    assert parsed["status"] == 0
    assert parsed["direction"] == 2
    assert parsed["lid"] == 0xF8
    assert parsed["tei"] == 7
    assert parsed["tx"]["tx_pbs_passed"] == 100
    assert parsed["tx"]["tx_pbs_failed"] == 5
    assert parsed["rx"]["rx_pbs_passed"] == 200
    assert parsed["rx"]["rx_interval_count"] == 1
    assert parsed["rx"]["rx_intervals"][0]["rx_phy_rate_mbps"] == 123

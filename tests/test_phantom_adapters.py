"""Regression tests for phantom adapters and the AsyncSniffer timeout error.

Phantom adapters came from parsing arbitrary bytes (Ethernet padding filled
with stale buffer contents, or unrelated frames) as station MACs.  These tests
exercise the parsers with byte fixtures, independent of any network I/O.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

LIB_PATH = Path(__file__).resolve().parents[1] / "custom_components/homeplug_av/pla-util-py"
if str(LIB_PATH) not in sys.path:
    sys.path.insert(0, str(LIB_PATH))

from scapy.layers.inet import IP, TCP  # type: ignore
from scapy.layers.l2 import Ether  # type: ignore
from scapy.packet import Raw  # type: ignore

from pla_util_py import network, parsers

from custom_components.homeplug_av import _phantom_device_macs
from custom_components.homeplug_av.const import (
    DEFAULT_ADAPTER_RETENTION_SECONDS,
    DEFAULT_LINK_RETENTION_SECONDS,
    DOMAIN,
    MAX_NEW_ADAPTERS_PER_POLL,
)
from custom_components.homeplug_av.coordinator import PowerlineDataUpdateCoordinator
from custom_components.homeplug_av.helpers import is_valid_adapter_mac

HOST_MAC = "02:42:ac:11:00:02"
LOUNGE = "98:48:27:fa:9c:65"
ATTIC = "98:48:27:fb:1d:0d"

_STATS_HEADER = bytes([0x02, 0x2D, 0xA0, 0x00, 0x00, 0x00, 0x1F, 0x84, 0x02])


def _mac(mac: str) -> bytes:
    return bytes.fromhex(mac.replace(":", ""))


def _station(mac: str, to_rate: int, from_rate: int) -> bytes:
    return _mac(mac) + to_rate.to_bytes(2, "little") + from_rate.to_bytes(2, "little")


def _stats_payload(stations: list[bytes], padding: bytes = b"") -> bytes:
    payload = _STATS_HEADER + bytes([len(stations)]) + b"".join(stations) + padding
    return payload + b"\x00" * max(0, 46 - len(payload))


def _stats_frame(payload: bytes, src: str = ATTIC) -> Ether:
    return Ether(src=src, dst=HOST_MAC, type=0x8912) / Raw(load=payload)


# Stale padding observed in the field: TCP NOP,NOP,Timestamp options, a
# fragment of the adapters' own OUI and the HomePlug EtherType.
_GARBAGE_PADDING = bytes.fromhex(
    "0000010108 0abdffffff 00000000 9848 2700 88e167bf37 017b1ab3d955".replace(" ", "")
)


# ---------------------------------------------------------------------------
# Network stats parser
# ---------------------------------------------------------------------------


def test_valid_network_stats_cnf_ignores_garbage_padding() -> None:
    payload = _stats_payload([_station(LOUNGE, 450, 430)], padding=_GARBAGE_PADDING)

    assert parsers.parse_network_stats(_stats_frame(payload)) == [
        {"mac": LOUNGE, "to_rate": 450, "from_rate": 430}
    ]


def test_network_stats_with_two_stations() -> None:
    payload = _stats_payload([_station(LOUNGE, 450, 430), _station(ATTIC, 300, 310)])

    stations = parsers.parse_network_stats_payload(payload)

    assert [s["mac"] for s in stations] == [LOUNGE, ATTIC]


def test_tcp_ip_frame_is_not_parsed_as_stations() -> None:
    frame = (
        Ether(src="00:11:22:33:44:55", dst=HOST_MAC)
        / IP(src="192.168.1.2", dst="192.168.1.3")
        / TCP(options=[("NOP", None), ("NOP", None), ("Timestamp", (1, 2))])
        / Raw(load=b"\x98\x48\x27\xfa\x9c\x65" * 8)
    )

    assert parsers.parse_network_stats(frame) == []
    with pytest.raises(ValueError):
        parsers.parse_network_stats_payload(bytes(frame[Raw].load))


def test_tcp_ip_frame_is_rejected_by_reply_filter() -> None:
    match = network._reply_matcher(0x8912, HOST_MAC, ATTIC, None)
    tcp_frame = Ether(src=ATTIC, dst=HOST_MAC) / IP() / TCP() / Raw(load=b"x" * 40)
    reply = _stats_frame(_stats_payload([_station(LOUNGE, 1, 1)]))
    other_source = _stats_frame(_stats_payload([_station(LOUNGE, 1, 1)]), src=LOUNGE)

    assert match(reply) is True
    assert match(tcp_frame) is False
    assert match(other_source) is False


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        _STATS_HEADER,  # header without a station count
        _STATS_HEADER + bytes([3]) + _station(LOUNGE, 1, 1),  # declares 3, has 1
        _STATS_HEADER + bytes([255]) + b"\x00" * 36,  # count read from garbage
    ],
)
def test_truncated_network_stats_cnf_is_rejected(payload: bytes) -> None:
    with pytest.raises(ValueError):
        parsers.parse_network_stats_payload(payload)
    if payload:
        assert parsers.parse_network_stats(_stats_frame(payload)) == []


def test_wrong_mmtype_or_oui_is_rejected() -> None:
    good = _stats_payload([_station(LOUNGE, 1, 1)])
    wrong_mmtype = good[:1] + b"\x71\xa0" + good[3:]
    wrong_oui = good[:5] + b"\x00\xb0\x52" + good[8:]

    for payload in (wrong_mmtype, wrong_oui):
        with pytest.raises(ValueError):
            parsers.parse_network_stats_payload(payload)


def test_network_stats_station_list_drops_invalid_macs() -> None:
    payload = _stats_payload(
        [
            _station("00:00:00:00:00:00", 10, 10),
            _station("ff:ff:ff:ff:ff:ff", 10, 10),
            _station("01:01:08:0a:bd:ff", 10, 10),
            _station(ATTIC, 300, 310),
        ]
    )

    assert [s["mac"] for s in parsers.parse_network_stats_payload(payload)] == [ATTIC]


# ---------------------------------------------------------------------------
# Discover / discover-list parsers
# ---------------------------------------------------------------------------


def test_discover_list_rejects_truncated_and_invalid_entries() -> None:
    entry = _mac(ATTIC) + bytes([2, 1, 1, 0x20, 0x0A, 0])
    bad = _mac("01:00:5e:00:00:01") + b"\x00" * 6
    payload = bytes([0x01, 0x15, 0x00, 0x00, 0x00, 2]) + entry + bad
    pkt = Ether(src=LOUNGE, dst=HOST_MAC, type=0x88E1) / Raw(load=payload)

    result = parsers.parse_discover_list(pkt)

    assert [s["mac"] for s in result["stations"]] == [ATTIC]
    assert result["stations"][0]["cco"] is True

    truncated = Ether(type=0x88E1) / Raw(load=bytes([0x01, 0x15, 0x00, 0, 0, 9]) + entry)
    with pytest.raises(ValueError):
        parsers.parse_discover_list(truncated)


def test_parse_discover_requires_discover_cnf_and_unicast_source() -> None:
    cnf = bytes([0x02, 0x71, 0xA0, 0, 0, 0, 0x1F, 0x84, 0x01, 0x02, 0x00]) + b"hfid\x00"
    good = Ether(src=ATTIC, dst=HOST_MAC, type=0x8912) / Raw(load=cnf)
    multicast = Ether(src="01:7b:1a:b3:d9:55", dst=HOST_MAC, type=0x8912) / Raw(load=cnf)
    not_cnf = Ether(src=LOUNGE, dst=HOST_MAC, type=0x8912) / Raw(load=b"\x02\x2d\xa0" + cnf[3:])

    adapters = parsers.parse_discover([good, multicast, not_cnf])

    assert [a["mac"] for a in adapters] == [ATTIC]


# ---------------------------------------------------------------------------
# MAC validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mac",
    [
        "00:00:00:00:00:00",
        "ff:ff:ff:ff:ff:ff",
        "01:f9:ff:ff:ff:ff",
        "01:7b:1a:b3:d9:55",
        "33:33:00:00:00:01",
        HOST_MAC,
        "",
        None,
        "not-a-mac",
        "98:48:27:fa:9c",
    ],
)
def test_invalid_adapter_macs(mac) -> None:
    assert is_valid_adapter_mac(mac, HOST_MAC) is False
    assert parsers.is_valid_station_mac(mac, host_mac=HOST_MAC) is False


@pytest.mark.parametrize("mac", [LOUNGE, ATTIC, "58:D6:1F:1D:42:6D"])
def test_valid_adapter_macs(mac) -> None:
    assert is_valid_adapter_mac(mac, HOST_MAC) is True
    assert parsers.is_valid_station_mac(mac, host_mac=HOST_MAC) is True


# ---------------------------------------------------------------------------
# AsyncSniffer usage
# ---------------------------------------------------------------------------


class _StrictAsyncSniffer:
    """Mimics Scapy 2.7+, which rejects ``timeout`` in the constructor."""

    instances: list["_StrictAsyncSniffer"] = []
    reply: object | None = None

    def __init__(self, *args, **kwargs) -> None:
        if "timeout" in kwargs:
            raise ValueError(
                "'timeout' isn't supported with AsyncSniffer. Use join(timeout=1)"
            )
        self.kwargs = kwargs
        self.running = False
        self.exception = None
        self.stopped = False
        type(self).instances.append(self)

    def start(self) -> None:
        self.running = True
        self.kwargs["started_callback"]()

    def deliver(self, pkt) -> None:
        if self.kwargs["lfilter"](pkt):
            self.kwargs["prn"](pkt)
            if self.kwargs.get("count") == 1:
                self.running = False

    def stop(self, join=True):
        self.running = False
        self.stopped = True

    def join(self, *args, **kwargs) -> None:
        return None


def _patch_network(monkeypatch, reply) -> None:
    _StrictAsyncSniffer.instances = []

    def fake_sendp(frame, iface=None, verbose=False):
        if reply is not None:
            _StrictAsyncSniffer.instances[-1].deliver(reply)

    monkeypatch.setattr(network, "AsyncSniffer", _StrictAsyncSniffer)
    monkeypatch.setattr(network, "sendp", fake_sendp)
    monkeypatch.setattr(network, "get_if_hwaddr", lambda iface: HOST_MAC)


def test_send_message_does_not_pass_timeout_to_async_sniffer(monkeypatch) -> None:
    reply = _stats_frame(_stats_payload([_station(LOUNGE, 450, 430)]))
    _patch_network(monkeypatch, reply)

    pkt = network.send_message(
        b"\x02\x2c\xa0" + b"\x00" * 43, interface="enp0s18", dest_mac=ATTIC, timeout=0.2
    )

    assert pkt is reply
    assert "timeout" not in _StrictAsyncSniffer.instances[0].kwargs


def test_send_message_times_out_and_stops_sniffer(monkeypatch) -> None:
    _patch_network(monkeypatch, None)

    pkt = network.send_message(
        b"\x02\x2c\xa0" + b"\x00" * 43, interface="enp0s18", dest_mac=ATTIC, timeout=0.05
    )

    assert pkt is None
    assert _StrictAsyncSniffer.instances[0].stopped is True


def test_send_message_collect_works_with_strict_sniffer(monkeypatch) -> None:
    cnf = bytes([0x02, 0x71, 0xA0, 0, 0, 0, 0x1F, 0x84, 0x01, 0x02, 0x00]) + b"\x00" * 35
    reply = Ether(src=ATTIC, dst=HOST_MAC, type=0x8912) / Raw(load=cnf)
    _patch_network(monkeypatch, reply)

    pkts = network.send_message_collect(
        b"\x01\x70\xa0" + b"\x00" * 43, interface="enp0s18", timeout=0.05, window=0.01
    )

    assert pkts == [reply]


# ---------------------------------------------------------------------------
# Coordinator gating
# ---------------------------------------------------------------------------


class _FakeAsyncHass:
    def __init__(self, entry_data: dict) -> None:
        self.data = {DOMAIN: {"entry-id": entry_data}}
        self.config_entries = SimpleNamespace(async_update_entry=lambda *a, **k: None)

    async def async_add_executor_job(self, func, *args):
        return func(*args)

    def verify_event_loop_thread(self, action: str) -> None:
        return None


def _coordinator(entry_data: dict) -> PowerlineDataUpdateCoordinator:
    coordinator = PowerlineDataUpdateCoordinator.__new__(PowerlineDataUpdateCoordinator)
    coordinator.mesh_data = {}
    coordinator.hass = _FakeAsyncHass(entry_data)
    coordinator._entry_id = "entry-id"
    coordinator._interface = "enp0s18"
    coordinator._lock = asyncio.Lock()
    coordinator._adapter_retention_seconds = DEFAULT_ADAPTER_RETENTION_SECONDS
    coordinator._link_retention_seconds = DEFAULT_LINK_RETENTION_SECONDS
    coordinator._qca_diagnostic_interval_seconds = 0
    coordinator._last_qca_diagnostic_refresh = 0.0
    coordinator._last_poll_attempts = 0
    return coordinator


def _entry_data() -> dict:
    return {
        "adapter_metadata": {
            LOUNGE: {"mac": LOUNGE, "backend": "broadcom", "interface": "MII1"},
            ATTIC: {"mac": ATTIC, "backend": "broadcom", "interface": "PLC"},
        },
        "adapters": [],
        "online_macs": {LOUNGE, ATTIC},
        "discovery_miss_counts": {},
        "index_map": {},
        "entry": None,
        "host_mac": HOST_MAC,
    }


def test_coordinator_creates_exactly_the_real_adapters() -> None:
    entry_data = _entry_data()
    coordinator = _coordinator(entry_data)
    junk = [
        {"mac": mac, "to_rate": 100, "from_rate": 100}
        for mac in (
            "00:00:00:00:00:00",
            "ff:ff:ff:ff:ff:ff",
            "01:01:08:0a:bd:ff",
            HOST_MAC,
        )
    ]
    peers = {LOUNGE: ATTIC, ATTIC: LOUNGE}
    coordinator._stats_call = lambda mac: [
        {"mac": peers[mac], "to_rate": 400, "from_rate": 410},
        *junk,
    ]

    for _ in range(5):
        result = asyncio.run(coordinator._async_update_data())

    assert set(result) == {LOUNGE, ATTIC}
    assert set(coordinator.mesh_data) == {f"{LOUNGE}_{ATTIC}", f"{ATTIC}_{LOUNGE}"}


def test_unknown_peer_needs_consecutive_polls() -> None:
    entry_data = _entry_data()
    coordinator = _coordinator(entry_data)
    newcomer = "98:48:27:00:00:01"
    reports = iter([[newcomer], [], [newcomer], [newcomer]])
    current: list[str] = []

    def stats_call(mac):
        if mac != LOUNGE:
            return []
        return [{"mac": peer, "to_rate": 200, "from_rate": 200} for peer in current]

    coordinator._stats_call = stats_call

    seen = []
    for current in reports:
        result = asyncio.run(coordinator._async_update_data())
        seen.append(newcomer in result)

    assert seen == [False, False, False, True]


def test_flood_of_new_stations_is_capped() -> None:
    entry_data = _entry_data()
    coordinator = _coordinator(entry_data)
    flood = [f"98:48:27:00:01:{i:02x}" for i in range(MAX_NEW_ADAPTERS_PER_POLL + 1)]
    coordinator._stats_call = lambda mac: [
        {"mac": peer, "to_rate": 200, "from_rate": 200} for peer in flood
    ]

    for _ in range(3):
        result = asyncio.run(coordinator._async_update_data())

    assert set(result) == {LOUNGE, ATTIC}


def test_poll_errors_are_logged_once(caplog) -> None:
    entry_data = _entry_data()
    coordinator = _coordinator(entry_data)

    def failing(mac):
        raise ValueError("'timeout' isn't supported with AsyncSniffer. Use join(timeout=1)")

    coordinator._stats_call = failing
    with caplog.at_level("DEBUG"):
        for _ in range(3):
            asyncio.run(coordinator._async_update_data())

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "enp0s18" in warnings[0].getMessage()


# ---------------------------------------------------------------------------
# Upgrade cleanup
# ---------------------------------------------------------------------------


def test_phantom_device_cleanup_selection() -> None:
    devices = [
        (LOUNGE, True),
        (ATTIC, False),
        ("00:00:00:00:00:00", False),
        ("01:01:08:0a:bd:ff", True),
        ("00:00:00:00:98:48", False),  # unicast-looking phantom
        ("98:48:27:12:34:56", True),  # renamed by the user, currently offline
    ]

    assert _phantom_device_macs(devices, {LOUNGE, ATTIC}, HOST_MAC, full_sweep=False) == {
        "00:00:00:00:00:00",
        "01:01:08:0a:bd:ff",
    }
    assert _phantom_device_macs(devices, {LOUNGE, ATTIC}, HOST_MAC, full_sweep=True) == {
        "00:00:00:00:00:00",
        "01:01:08:0a:bd:ff",
        "00:00:00:00:98:48",
    }
    # Without a successful discovery the sweep never removes valid MACs.
    assert _phantom_device_macs(devices, set(), HOST_MAC, full_sweep=True) == {
        "00:00:00:00:00:00",
        "01:01:08:0a:bd:ff",
    }

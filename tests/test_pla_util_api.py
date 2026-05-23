"""Regression tests for the bundled pla-util-py API."""

from __future__ import annotations

import sys
from pathlib import Path

LIB_PATH = Path(__file__).resolve().parents[1] / "custom_components/homeplug_av/pla-util-py"
if str(LIB_PATH) not in sys.path:
    sys.path.insert(0, str(LIB_PATH))

from pla_util_py import PLAUtil
from pla_util_py import commands


def test_optional_member_commands_return_empty_shapes_on_no_reply(monkeypatch) -> None:
    monkeypatch.setattr(commands, "get_discover_list", lambda *args, **kwargs: None)
    monkeypatch.setattr(commands, "get_id_info", lambda *args, **kwargs: None)
    monkeypatch.setattr(commands, "get_network_info", lambda *args, **kwargs: None)
    monkeypatch.setattr(commands, "get_station_info", lambda *args, **kwargs: None)

    pla = PLAUtil(interface="eth0", pla_mac="20:23:51:fc:a3:28")

    assert pla.discover_list(timeout=0.1) == {"stations": []}
    assert pla.id_info(timeout=0.1) == {}
    assert pla.network_info(timeout=0.1) == []
    assert pla.station_info(timeout=0.1) == {}


def test_qca_network_info_returns_empty_shape_on_no_reply(monkeypatch) -> None:
    monkeypatch.setattr(commands, "qca_get_network_info", lambda *args, **kwargs: None)
    monkeypatch.setattr(commands, "qca_get_network_info_stats", lambda *args, **kwargs: None)

    pla = PLAUtil(interface="eth0", pla_mac="58:d6:1f:1d:42:6d", backend="qca")

    assert pla.qca_network_info(timeout=0.1) == {
        "source": "58:d6:1f:1d:42:6d",
        "sub_version": None,
        "networks": [],
        "stations": [],
    }
    assert pla.qca_network_info_stats(timeout=0.1) == {
        "source": "58:d6:1f:1d:42:6d",
        "sub_version": None,
        "networks": [],
        "stations": [],
        "first_tei": None,
        "num_stations_reported": 0,
        "in_avln": 0,
    }


def test_qca_link_stats_returns_empty_shape_on_no_reply(monkeypatch) -> None:
    monkeypatch.setattr(commands, "qca_get_link_stats", lambda *args, **kwargs: None)

    pla = PLAUtil(interface="eth0", pla_mac="58:d6:1f:1d:42:6d", backend="qca")

    assert pla.qca_link_stats("58:d6:1f:1d:46:86", timeout=0.1) == {
        "source": "58:d6:1f:1d:42:6d",
        "peer": "58:d6:1f:1d:46:86",
        "status": None,
        "direction": 2,
        "lid": 0xF8,
        "tei": None,
        "tx": {},
        "rx": {},
    }

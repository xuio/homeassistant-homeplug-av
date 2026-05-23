"""Lifecycle regression tests for the Homeplug AV integration."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from custom_components.homeplug_av import (
    _apply_discovery_online_update,
    _discover_with_retry,
    _merge_discovered_adapters,
    _seed_adapters_from_index_map,
    async_migrate_entry,
)
from custom_components.homeplug_av.const import (
    CONF_ADAPTER_RETENTION_SECONDS,
    CONF_LINK_RETENTION_SECONDS,
    CONF_SCAN_INTERVAL,
    DEFAULT_ADAPTER_RETENTION_SECONDS,
    DEFAULT_LINK_RETENTION_SECONDS,
    DOMAIN,
)
from custom_components.homeplug_av.coordinator import PowerlineDataUpdateCoordinator
from custom_components.homeplug_av.helpers import adapter_macs, ensure_index_map
from custom_components.homeplug_av.sensor import (
    MESH_DIAGNOSTIC_SENSOR_DEFINITIONS,
    STATIC_SENSOR_DEFINITIONS,
)


class _FakeConfigEntries:
    def __init__(self) -> None:
        self.updates: list[dict] = []

    def async_update_entry(self, entry, **kwargs) -> None:
        self.updates.append(kwargs)
        for key, value in kwargs.items():
            setattr(entry, key, value)


class _FakeHass:
    def __init__(self) -> None:
        self.config_entries = _FakeConfigEntries()


def _coordinator() -> PowerlineDataUpdateCoordinator:
    coordinator = PowerlineDataUpdateCoordinator.__new__(PowerlineDataUpdateCoordinator)
    coordinator.mesh_data = {}
    coordinator.hass = object()
    coordinator._entry_id = "entry-id"
    coordinator._adapter_retention_seconds = DEFAULT_ADAPTER_RETENTION_SECONDS
    coordinator._link_retention_seconds = DEFAULT_LINK_RETENTION_SECONDS
    coordinator._last_poll_attempts = 0
    return coordinator


class _FakeAsyncHass:
    def __init__(self, entry_data: dict) -> None:
        self.data = {DOMAIN: {"entry-id": entry_data}}
        self.config_entries = _FakeConfigEntries()

    async def async_add_executor_job(self, func):
        return func()

    def verify_event_loop_thread(self, action: str) -> None:
        return None


def test_merge_discovered_adapters_retains_metadata_and_normalizes_mac() -> None:
    data = {
        "adapter_metadata": {
            "aa:bb:cc:dd:ee:ff": {
                "mac": "aa:bb:cc:dd:ee:ff",
                "backend": "qca",
                "custom": "kept",
            }
        },
        "adapters": [],
    }

    online = _merge_discovered_adapters(
        data,
        [{"mac": "AA:BB:CC:DD:EE:FF", "hfid": "updated"}],
        123.0,
    )

    assert online == {"aa:bb:cc:dd:ee:ff"}
    assert data["adapter_metadata"]["aa:bb:cc:dd:ee:ff"]["custom"] == "kept"
    assert data["adapter_metadata"]["aa:bb:cc:dd:ee:ff"]["hfid"] == "updated"
    assert data["adapter_metadata"]["aa:bb:cc:dd:ee:ff"]["last_seen"] == 123.0
    assert adapter_macs(data) == {"aa:bb:cc:dd:ee:ff"}


def test_seed_adapters_from_index_map_bootstraps_known_adapters() -> None:
    data = {
        "adapter_metadata": {},
        "adapters": [],
        "index_map": {"AA:BB:CC:DD:EE:FF": 1},
    }

    _seed_adapters_from_index_map(data, 123.0)

    assert data["adapter_metadata"] == {
        "aa:bb:cc:dd:ee:ff": {
            "mac": "aa:bb:cc:dd:ee:ff",
            "interface": "PLC",
            "hfid": "Unknown",
            "last_seen": 123.0,
        }
    }
    assert data["adapters"] == [data["adapter_metadata"]["aa:bb:cc:dd:ee:ff"]]


def test_discover_with_retry_smooths_empty_broadcast_result() -> None:
    class FakePLA:
        def __init__(self) -> None:
            self.calls = 0

        def discover(self, *, timeout):
            self.calls += 1
            return [] if self.calls == 1 else [{"mac": "aa:bb:cc:dd:ee:ff"}]

    pla = FakePLA()

    assert _discover_with_retry(pla) == [{"mac": "aa:bb:cc:dd:ee:ff"}]
    assert pla.calls == 2


def test_discovery_online_update_requires_consecutive_misses() -> None:
    miss_counts: dict[str, int] = {}
    current = {"aa:aa:aa:aa:aa:aa"}

    assert _apply_discovery_online_update(current, set(), miss_counts) == current
    assert miss_counts == {"aa:aa:aa:aa:aa:aa": 1}

    assert _apply_discovery_online_update(current, set(), miss_counts) == current
    assert miss_counts == {"aa:aa:aa:aa:aa:aa": 2}

    assert _apply_discovery_online_update(current, set(), miss_counts) == set()
    assert miss_counts == {"aa:aa:aa:aa:aa:aa": 3}


def test_discovery_online_update_resets_miss_count_on_success() -> None:
    miss_counts = {"aa:aa:aa:aa:aa:aa": 2}

    assert _apply_discovery_online_update(
        {"aa:aa:aa:aa:aa:aa"},
        {"AA:AA:AA:AA:AA:AA"},
        miss_counts,
    ) == {"aa:aa:aa:aa:aa:aa"}
    assert miss_counts == {}


def test_ensure_index_map_assigns_stable_indexes_once() -> None:
    hass = _FakeHass()
    entry = SimpleNamespace(options={})
    data = {"index_map": {"bb:bb:bb:bb:bb:bb": 2}}

    ensure_index_map(
        hass,
        entry,
        data,
        {"aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"},
    )
    ensure_index_map(
        hass,
        entry,
        data,
        {"aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"},
    )

    assert data["index_map"] == {
        "aa:aa:aa:aa:aa:aa": 3,
        "bb:bb:bb:bb:bb:bb": 2,
    }
    assert hass.config_entries.updates == [
        {"options": {"index_map": data["index_map"]}}
    ]


def test_config_entry_migration_adds_retention_options_and_normalizes_indexes() -> None:
    hass = _FakeHass()
    entry = SimpleNamespace(
        entry_id="entry-id",
        version=1,
        data={CONF_SCAN_INTERVAL: 45},
        options={"index_map": {"AA:AA:AA:AA:AA:AA": 1}},
    )

    assert asyncio.run(async_migrate_entry(hass, entry)) is True

    assert entry.version == 2
    assert entry.options == {
        CONF_SCAN_INTERVAL: 45,
        CONF_ADAPTER_RETENTION_SECONDS: DEFAULT_ADAPTER_RETENTION_SECONDS,
        CONF_LINK_RETENTION_SECONDS: DEFAULT_LINK_RETENTION_SECONDS,
        "index_map": {"aa:aa:aa:aa:aa:aa": 1},
    }


def test_remove_registry_entities_for_expired_adapter(monkeypatch) -> None:
    import custom_components.homeplug_av.coordinator as coordinator_module

    removed: list[str] = []
    registry = SimpleNamespace(async_remove=removed.append)
    entries = [
        SimpleNamespace(unique_id="powerline_aa:aa:aa:aa:aa:aa_mac", entity_id="sensor.mac"),
        SimpleNamespace(
            unique_id="powerline_bb:bb:bb:bb:bb:bb_to_aa:aa:aa:aa:aa:aa_tx",
            entity_id="sensor.to_expired",
        ),
        SimpleNamespace(
            unique_id="powerline_bb:bb:bb:bb:bb:bb_from_aa:aa:aa:aa:aa:aa_rx",
            entity_id="sensor.from_expired",
        ),
        SimpleNamespace(unique_id="powerline_bb:bb:bb:bb:bb:bb_mac", entity_id="sensor.other"),
    ]
    monkeypatch.setattr(coordinator_module.er, "async_get", lambda hass: registry)
    monkeypatch.setattr(
        coordinator_module.er,
        "async_entries_for_config_entry",
        lambda registry, entry_id: entries,
    )

    coordinator = _coordinator()
    coordinator._remove_registry_entities_for_mac("AA:AA:AA:AA:AA:AA")

    assert removed == ["sensor.mac", "sensor.to_expired", "sensor.from_expired"]


def test_remove_registry_entities_for_expired_link(monkeypatch) -> None:
    import custom_components.homeplug_av.coordinator as coordinator_module

    removed: list[str] = []
    registry = SimpleNamespace(async_remove=removed.append)
    entries = [
        SimpleNamespace(
            unique_id="powerline_aa:aa:aa:aa:aa:aa_to_bb:bb:bb:bb:bb:bb_tx",
            entity_id="sensor.tx",
        ),
        SimpleNamespace(
            unique_id="powerline_aa:aa:aa:aa:aa:aa_from_bb:bb:bb:bb:bb:bb_rx",
            entity_id="sensor.rx",
        ),
        SimpleNamespace(
            unique_id="powerline_aa:aa:aa:aa:aa:aa_to_bb:bb:bb:bb:bb:bb_bda",
            entity_id="sensor.diagnostic",
        ),
        SimpleNamespace(
            unique_id="powerline_bb:bb:bb:bb:bb:bb_to_aa:aa:aa:aa:aa:aa_tx",
            entity_id="sensor.reverse",
        ),
    ]
    monkeypatch.setattr(coordinator_module.er, "async_get", lambda hass: registry)
    monkeypatch.setattr(
        coordinator_module.er,
        "async_entries_for_config_entry",
        lambda registry, entry_id: entries,
    )

    coordinator = _coordinator()
    coordinator._remove_registry_entities_for_link(
        "aa:aa:aa:aa:aa:aa",
        "bb:bb:bb:bb:bb:bb",
    )

    assert removed == ["sensor.tx", "sensor.rx", "sensor.diagnostic"]


def test_zero_rate_samples_are_retained_from_previous_nonzero_values() -> None:
    coordinator = _coordinator()

    assert coordinator._coerce_link_rates("aa", "bb", 0, 120) is None

    coordinator.mesh_data = {
        "aa_bb": {
            "tx_rate": 345,
            "rx_rate": 456,
        }
    }

    assert coordinator._coerce_link_rates("aa", "bb", 0, 120) == (
        345,
        120,
        True,
        False,
    )
    assert coordinator._coerce_link_rates("aa", "bb", 123, 0) == (
        123,
        456,
        False,
        True,
    )
    assert coordinator._coerce_link_rates("aa", "bb", 0, 0) == (
        345,
        456,
        True,
        True,
    )


def test_static_and_mesh_diagnostic_descriptions_are_deterministic() -> None:
    """Optional metadata fields should not create entities later in runtime."""

    static_fields = {definition[1] for definition in STATIC_SENSOR_DEFINITIONS}
    mesh_fields = {definition[1] for definition in MESH_DIAGNOSTIC_SENSOR_DEFINITIONS}

    assert "chipset" in static_fields
    assert "firmware" in static_fields
    assert "op_firmware_version" in static_fields
    assert {"bda", "tx_coupling", "rx_coupling", "role"} <= mesh_fields


def test_qca_poll_order_prefers_last_successful_source() -> None:
    coordinator = _coordinator()
    adapter_metadata = {
        "aa:aa:aa:aa:aa:aa": {"backend": "qca"},
        "bb:bb:bb:bb:bb:bb": {"backend": "qca"},
    }

    order, single_source = coordinator._poll_order(
        ["aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"],
        adapter_metadata,
        {"preferred_stats_source": "bb:bb:bb:bb:bb:bb"},
    )

    assert single_source is True
    assert order == ["bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa"]


def test_record_link_stat_derives_reciprocal_qca_rates() -> None:
    coordinator = _coordinator()
    mesh_data: dict = {}
    fresh_keys: set[str] = set()
    metadata = {
        "aa:aa:aa:aa:aa:aa": {"mac": "aa:aa:aa:aa:aa:aa", "backend": "qca"},
        "bb:bb:bb:bb:bb:bb": {"mac": "bb:bb:bb:bb:bb:bb", "backend": "qca"},
    }

    assert coordinator._record_link_stat(
        mesh_data=mesh_data,
        fresh_keys=fresh_keys,
        adapter_metadata=metadata,
        now=123.0,
        source_mac="aa:aa:aa:aa:aa:aa",
        peer_mac="bb:bb:bb:bb:bb:bb",
        tx_rate=140,
        rx_rate=137,
        stat={"bda": "11:22:33:44:55:66"},
    )
    assert coordinator._record_link_stat(
        mesh_data=mesh_data,
        fresh_keys=fresh_keys,
        adapter_metadata=metadata,
        now=123.0,
        source_mac="bb:bb:bb:bb:bb:bb",
        peer_mac="aa:aa:aa:aa:aa:aa",
        tx_rate=137,
        rx_rate=140,
        stat={},
        derived=True,
    )

    assert mesh_data["aa:aa:aa:aa:aa:aa_bb:bb:bb:bb:bb:bb"]["tx_rate"] == 140
    assert mesh_data["aa:aa:aa:aa:aa:aa_bb:bb:bb:bb:bb:bb"]["rx_rate"] == 137
    assert mesh_data["bb:bb:bb:bb:bb:bb_aa:aa:aa:aa:aa:aa"]["tx_rate"] == 137
    assert mesh_data["bb:bb:bb:bb:bb:bb_aa:aa:aa:aa:aa:aa"]["rx_rate"] == 140
    assert mesh_data["bb:bb:bb:bb:bb:bb_aa:aa:aa:aa:aa:aa"]["derived"] is True


def test_qca_update_uses_fallback_source_and_derives_reverse_link() -> None:
    entry_data = {
        "adapter_metadata": {
            "aa:aa:aa:aa:aa:aa": {"mac": "aa:aa:aa:aa:aa:aa", "backend": "qca"},
            "bb:bb:bb:bb:bb:bb": {"mac": "bb:bb:bb:bb:bb:bb", "backend": "qca"},
        },
        "adapters": [],
        "online_macs": {"aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"},
        "discovery_miss_counts": {},
        "index_map": {},
        "entry": SimpleNamespace(options={}),
    }
    coordinator = _coordinator()
    coordinator.hass = _FakeAsyncHass(entry_data)
    coordinator._lock = asyncio.Lock()

    def stats_call(mac):
        if mac == "aa:aa:aa:aa:aa:aa":
            return []
        return [
            {
                "mac": "aa:aa:aa:aa:aa:aa",
                "to_rate": 137,
                "from_rate": 140,
                "tx_coupling": "Primary",
                "rx_coupling": "Primary",
            }
        ]

    coordinator._stats_call = stats_call

    result = asyncio.run(coordinator._async_update_data())

    assert entry_data["preferred_stats_source"] == "bb:bb:bb:bb:bb:bb"
    assert coordinator._last_poll_attempts == 2
    assert result["aa:aa:aa:aa:aa:aa"]["mac"] == "aa:aa:aa:aa:aa:aa"
    assert coordinator.mesh_data["bb:bb:bb:bb:bb:bb_aa:aa:aa:aa:aa:aa"]["tx_rate"] == 137
    assert coordinator.mesh_data["aa:aa:aa:aa:aa:aa_bb:bb:bb:bb:bb:bb"]["tx_rate"] == 140
    assert coordinator.mesh_data["aa:aa:aa:aa:aa:aa_bb:bb:bb:bb:bb:bb"]["derived"] is True


def test_mixed_backend_update_still_derives_qca_reverse_link() -> None:
    entry_data = {
        "adapter_metadata": {
            "aa:aa:aa:aa:aa:aa": {"mac": "aa:aa:aa:aa:aa:aa", "backend": "qca"},
            "bb:bb:bb:bb:bb:bb": {"mac": "bb:bb:bb:bb:bb:bb", "backend": "qca"},
            "cc:cc:cc:cc:cc:cc": {"mac": "cc:cc:cc:cc:cc:cc", "backend": "homeplug"},
        },
        "adapters": [],
        "online_macs": {
            "aa:aa:aa:aa:aa:aa",
            "bb:bb:bb:bb:bb:bb",
            "cc:cc:cc:cc:cc:cc",
        },
        "discovery_miss_counts": {},
        "index_map": {},
        "entry": SimpleNamespace(options={}),
    }
    coordinator = _coordinator()
    coordinator.hass = _FakeAsyncHass(entry_data)
    coordinator._lock = asyncio.Lock()

    def stats_call(mac):
        if mac == "aa:aa:aa:aa:aa:aa":
            return [
                {
                    "mac": "bb:bb:bb:bb:bb:bb",
                    "to_rate": 140,
                    "from_rate": 137,
                }
            ]
        return []

    coordinator._stats_call = stats_call

    result = asyncio.run(coordinator._async_update_data())

    assert coordinator._last_poll_attempts == 3
    assert result["cc:cc:cc:cc:cc:cc"]["backend"] == "homeplug"
    assert coordinator.mesh_data["aa:aa:aa:aa:aa:aa_bb:bb:bb:bb:bb:bb"]["tx_rate"] == 140
    assert coordinator.mesh_data["bb:bb:bb:bb:bb:bb_aa:aa:aa:aa:aa:aa"]["tx_rate"] == 137
    assert coordinator.mesh_data["bb:bb:bb:bb:bb:bb_aa:aa:aa:aa:aa:aa"]["derived"] is True

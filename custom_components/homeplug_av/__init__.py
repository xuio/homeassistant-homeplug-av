"""Homeplug AV integration."""

from __future__ import annotations

import sys
from pathlib import Path
import asyncio
import json
import re
import time
from typing import Any

# Ensure bundled pla_util_py library is importable
_LIB_PATH = Path(__file__).parent / "pla-util-py"
if _LIB_PATH.exists() and str(_LIB_PATH) not in sys.path:
    sys.path.insert(0, str(_LIB_PATH))

import logging
from datetime import timedelta

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers import device_registry as dr

from .const import (
    ATTR_ADAPTER_MAC,
    ATTR_ENTRY_ID,
    ATTR_INCLUDE_LIVE_QCA,
    CONF_ADAPTER_RETENTION_SECONDS,
    CONF_LINK_RETENTION_SECONDS,
    CONF_SCAN_INTERVAL,
    DEFAULT_ADAPTER_RETENTION_SECONDS,
    DEFAULT_LINK_RETENTION_SECONDS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PLATFORMS,
    SERVICE_DUMP_ADAPTER_DIAGNOSTICS,
    SERVICE_REFRESH_DISCOVERY,
    SERVICE_REFRESH_STATS,
)
from .coordinator import PowerlineDataUpdateCoordinator
from .diagnostics import build_diagnostics_payload
from .helpers import adapter_macs, ensure_index_map, normalize_mac, snapshot_signal
from pla_util_py import PLAUtil

_LOGGER = logging.getLogger(__name__)

DISCOVERY_MISSES_TO_MARK_OFFLINE = 3

SERVICE_ENTRY_SCHEMA = vol.Schema({vol.Optional(ATTR_ENTRY_ID): cv.string})

SERVICE_DIAGNOSTICS_SCHEMA = SERVICE_ENTRY_SCHEMA.extend(
    {
        vol.Optional(ATTR_ADAPTER_MAC): cv.string,
        vol.Optional(ATTR_INCLUDE_LIVE_QCA, default=False): cv.boolean,
    }
)


def _registry_index_map(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, int]:
    """Return adapter indexes from existing device registry names."""

    registry = dr.async_get(hass)
    index_map: dict[str, int] = {}
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        name = device.name_by_user or device.name
        match = re.fullmatch(r"Adapter ([0-9]+)", name or "")
        if not match:
            continue
        for identifier_domain, identifier in device.identifiers:
            if identifier_domain == DOMAIN:
                index_map[normalize_mac(identifier)] = int(match.group(1))
    return index_map


def _qca_discover_list(network_info: dict, source_mac: str) -> dict:
    """Convert QCA VS_NW_INFO data into the integration's station shape."""

    stations: list[dict] = []
    sub_version = network_info.get("sub_version")
    for network in network_info.get("networks", []):
        snid = network.get("snid")
        cco_tei = network.get("cco_tei")
        cco_mac = normalize_mac(network.get("cco_mac"))
        common = {
            "nid": network.get("nid"),
            "cco_mac": cco_mac,
            "cco_tei": cco_tei,
            "station_count": network.get("station_count"),
            "qca_network_sub_version": sub_version,
        }
        source_station = {
            "mac": source_mac,
            "tei": network.get("tei"),
            "same_network": True,
            "snid": snid,
            "cco": network.get("role") == "CCO",
            "pco": False,
            "bcco": False,
            "signal_level": 0,
            "role": network.get("role"),
            **common,
        }
        stations.append(source_station)

        for station in network.get("stations", []):
            station_mac = normalize_mac(station.get("mac"))
            if not station_mac:
                continue
            stations.append(
                {
                    "mac": station_mac,
                    "tei": station.get("tei"),
                    "same_network": True,
                    "snid": snid,
                    "cco": station.get("tei") == cco_tei,
                    "pco": False,
                    "bcco": False,
                    "signal_level": 0,
                    "role": station.get("role"),
                    "bda": station.get("bda"),
                    "tx_coupling": station.get("tx_coupling"),
                    "rx_coupling": station.get("rx_coupling"),
                    "to_rate": station.get("to_rate"),
                    "from_rate": station.get("from_rate"),
                    **common,
                }
            )

    return {"stations": stations, "networks": network_info.get("networks", [])}


def _sync_adapter_list(entry_data: dict) -> None:
    """Mirror retained adapter metadata into the setup snapshot list."""

    entry_data["adapters"] = [
        adapter
        for _, adapter in sorted(entry_data.get("adapter_metadata", {}).items())
    ]


def _seed_adapters_from_index_map(entry_data: dict, seen_at: float) -> None:
    """Seed retained adapter metadata from persisted stable MAC indexes."""

    metadata = entry_data["adapter_metadata"]
    for mac in sorted(entry_data.get("index_map", {})):
        mac = normalize_mac(mac)
        if not mac:
            continue
        metadata.setdefault(
            mac,
            {
                "mac": mac,
                "interface": "PLC",
                "hfid": "Unknown",
                "last_seen": seen_at,
            },
        )
    _sync_adapter_list(entry_data)


def _merge_discovered_adapters(entry_data: dict, adapters: list[dict], seen_at: float) -> set[str]:
    """Merge discovered adapters into retained metadata and return online MACs."""

    online: set[str] = set()
    metadata = entry_data["adapter_metadata"]
    for adapter in adapters:
        mac = normalize_mac(adapter.get("mac"))
        if not mac:
            continue
        previous = metadata.get(mac, {})
        metadata[mac] = {**previous, **adapter, "mac": mac, "last_seen": seen_at}
        online.add(mac)
    _sync_adapter_list(entry_data)
    return online


def _discover_with_retry(pla: PLAUtil, *, timeout: float = 2.0, attempts: int = 2) -> list[dict]:
    """Discover adapters with short retries to smooth transient broadcast misses."""

    last_result: list[dict] = []
    for _ in range(attempts):
        try:
            result = pla.discover(timeout=timeout)  # type: ignore[arg-type]
        except TypeError:
            result = pla.discover()
        if result:
            return result
        last_result = result or []
    return last_result


def _apply_discovery_online_update(
    current_online: set[str],
    discovered_online: set[str],
    miss_counts: dict[str, int],
    *,
    miss_threshold: int = DISCOVERY_MISSES_TO_MARK_OFFLINE,
) -> set[str]:
    """Return online adapters after applying consecutive discovery miss tolerance."""

    current_online = {normalize_mac(mac) for mac in current_online if normalize_mac(mac)}
    discovered_online = {
        normalize_mac(mac) for mac in discovered_online if normalize_mac(mac)
    }

    online = set(discovered_online)
    for mac in discovered_online:
        miss_counts.pop(mac, None)

    for mac in current_online - discovered_online:
        misses = miss_counts.get(mac, 0) + 1
        miss_counts[mac] = misses
        if misses < miss_threshold:
            online.add(mac)

    for mac in list(miss_counts):
        if mac not in current_online and mac not in discovered_online:
            miss_counts.pop(mac, None)

    return online


def _entry_runtimes(
    hass: HomeAssistant,
    entry_id: str | None = None,
) -> list[tuple[ConfigEntry, dict[str, Any]]]:
    """Return runtime data for all or one Homeplug AV config entries."""

    domain_data = hass.data.get(DOMAIN, {})
    if entry_id is not None:
        data = domain_data.get(entry_id)
        if data is None:
            raise HomeAssistantError(f"No Homeplug AV config entry found for {entry_id}")
        entry = data.get("entry")
        if entry is None:
            raise HomeAssistantError(f"Homeplug AV config entry {entry_id} is not loaded")
        return [(entry, data)]

    return [
        (data["entry"], data)
        for data in domain_data.values()
        if data.get("entry") is not None
    ]


async def _async_collect_live_qca(
    hass: HomeAssistant,
    entry: ConfigEntry,
    data: dict[str, Any],
    adapter_mac: str | None,
) -> dict[str, Any]:
    """Collect optional live QCA debug statistics for diagnostics services."""

    requested_mac = normalize_mac(adapter_mac)
    metadata = data.get("adapter_metadata", {})
    source_macs = [requested_mac] if requested_mac else sorted(metadata)
    interface = entry.data["interface"]
    lock: asyncio.Lock | None = data.get("lock")
    mesh_data = data.get("coordinator").mesh_data if data.get("coordinator") else {}
    captured: dict[str, Any] = {}

    for source_mac in source_macs:
        adapter = metadata.get(source_mac, {})
        if adapter.get("backend") != "qca":
            continue

        peers = sorted(
            {
                normalize_mac(link.get("target"))
                for link in mesh_data.values()
                if normalize_mac(link.get("source")) == source_mac
            }
            | {
                normalize_mac(link.get("source"))
                for link in mesh_data.values()
                if normalize_mac(link.get("target")) == source_mac
            }
        )
        peers = [peer for peer in peers if peer and peer != source_mac]

        def _capture() -> dict[str, Any]:
            pla = PLAUtil(interface=interface, pla_mac=source_mac, backend="qca")
            result: dict[str, Any] = {"peer_macs": peers}
            try:
                result["network_info_stats"] = pla.qca_network_info_stats(timeout=2.0)
            except Exception as err:  # pragma: no cover - hardware dependent
                result["network_info_stats_error"] = str(err)

            link_stats: list[dict[str, Any]] = []
            for peer_mac in peers:
                try:
                    link_stats.append(
                        {
                            "peer_mac": peer_mac,
                            "stats": pla.qca_link_stats(peer_mac, timeout=2.0),
                        }
                    )
                except Exception as err:  # pragma: no cover - hardware dependent
                    link_stats.append({"peer_mac": peer_mac, "error": str(err)})
            result["link_stats"] = link_stats
            return result

        if lock is None:
            captured[source_mac] = await hass.async_add_executor_job(_capture)
        else:
            async with lock:
                captured[source_mac] = await hass.async_add_executor_job(_capture)

    return captured


async def async_setup(hass: HomeAssistant, config: dict) -> bool:  # pragma: no cover
    """Set up global integration services."""

    if hass.services.has_service(DOMAIN, SERVICE_REFRESH_DISCOVERY):
        return True

    async def _handle_refresh_discovery(call: ServiceCall) -> None:
        for _, data in _entry_runtimes(hass, call.data.get(ATTR_ENTRY_ID)):
            refresh = data.get("refresh_discovery")
            if refresh is not None:
                await refresh()

    async def _handle_refresh_stats(call: ServiceCall) -> None:
        for _, data in _entry_runtimes(hass, call.data.get(ATTR_ENTRY_ID)):
            coordinator = data.get("coordinator")
            if coordinator is not None:
                await coordinator.async_request_refresh()

    async def _handle_dump_adapter_diagnostics(call: ServiceCall) -> None:
        adapter_mac = normalize_mac(call.data.get(ATTR_ADAPTER_MAC))
        include_live_qca = bool(call.data.get(ATTR_INCLUDE_LIVE_QCA, False))
        entries: list[dict[str, Any]] = []

        for entry, data in _entry_runtimes(hass, call.data.get(ATTR_ENTRY_ID)):
            payload = build_diagnostics_payload(
                hass,
                entry,
                adapter_mac=adapter_mac or None,
                redact=False,
            )
            if include_live_qca:
                payload["live_qca"] = await _async_collect_live_qca(
                    hass,
                    entry,
                    data,
                    adapter_mac or None,
                )
            entries.append(payload)

        event_payload = {"entries": entries}
        hass.bus.async_fire(f"{DOMAIN}_diagnostics", event_payload)
        _LOGGER.info(
            "Homeplug AV diagnostics payload: %s",
            json.dumps(event_payload, sort_keys=True),
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_REFRESH_DISCOVERY,
        _handle_refresh_discovery,
        schema=SERVICE_ENTRY_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REFRESH_STATS,
        _handle_refresh_stats,
        schema=SERVICE_ENTRY_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DUMP_ADAPTER_DIAGNOSTICS,
        _handle_dump_adapter_diagnostics,
        schema=SERVICE_DIAGNOSTICS_SCHEMA,
    )

    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entries to the current schema."""

    if entry.version > 2:
        return False

    if entry.version < 2:
        options = dict(entry.options)
        options.setdefault(
            CONF_SCAN_INTERVAL,
            entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )
        options.setdefault(
            CONF_ADAPTER_RETENTION_SECONDS,
            DEFAULT_ADAPTER_RETENTION_SECONDS,
        )
        options.setdefault(
            CONF_LINK_RETENTION_SECONDS,
            DEFAULT_LINK_RETENTION_SECONDS,
        )
        if "index_map" in options:
            options["index_map"] = {
                normalize_mac(mac): int(index)
                for mac, index in options.get("index_map", {}).items()
                if normalize_mac(mac)
            }

        hass.config_entries.async_update_entry(entry, options=options, version=2)
        _LOGGER.debug("Migrated Homeplug AV config entry %s to version 2", entry.entry_id)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Powerline Stats from a config entry."""

    interface: str = entry.data["interface"]
    scan_interval: int = entry.options.get(
        CONF_SCAN_INTERVAL, entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    )
    adapter_retention_seconds: int = entry.options.get(
        CONF_ADAPTER_RETENTION_SECONDS, DEFAULT_ADAPTER_RETENTION_SECONDS
    )
    link_retention_seconds: int = entry.options.get(
        CONF_LINK_RETENTION_SECONDS, DEFAULT_LINK_RETENTION_SECONDS
    )

    pla = PLAUtil(interface=interface)

    # Shared lock to prevent concurrent network access
    network_lock = asyncio.Lock()

    index_map = {
        normalize_mac(mac): int(index)
        for mac, index in entry.options.get("index_map", {}).items()
        if normalize_mac(mac)
    }
    index_map.update(_registry_index_map(hass, entry))

    online_macs: set[str] = set()
    adapter_metadata: dict[str, dict] = {}
    discover_list_data: dict[str, dict] = {}
    discovery_miss_counts: dict[str, int] = {}

    entry_data = {
        "coordinator": None,
        "adapters": [],
        "online_macs": online_macs,
        "adapter_metadata": adapter_metadata,
        "lock": network_lock,
        "index_map": index_map,
        "discover_list_data": discover_list_data,
        "discovery_miss_counts": discovery_miss_counts,
        "entry": entry,
        "interface": interface,
        "pla": pla,
        "unloading": False,
    }
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entry_data
    _seed_adapters_from_index_map(entry_data, time.time())

    async with network_lock:
        try:
            adapters = await hass.async_add_executor_job(_discover_with_retry, pla)
        except Exception as err:
            _LOGGER.warning("Initial powerline discovery failed: %s", err)
            adapters = []

    online_macs.update(_merge_discovered_adapters(entry_data, adapters, time.time()))
    ensure_index_map(hass, entry, entry_data, adapter_macs(entry_data))
    _LOGGER.debug("Discovered %d powerline adapter(s)", len(adapters))

    coordinator = PowerlineDataUpdateCoordinator(
        hass,
        _LOGGER,
        entry_id=entry.entry_id,
        pla=pla,
        interface=interface,
        update_interval=timedelta(seconds=scan_interval),
        lock=network_lock,
        adapter_retention_seconds=adapter_retention_seconds,
        link_retention_seconds=link_retention_seconds,
    )
    entry_data["coordinator"] = coordinator

    async def _refresh_discovery() -> None:
        """Refresh adapter presence and discover-list snapshots."""

        if entry_data.get("unloading"):
            return

        async with network_lock:
            if entry_data.get("unloading"):
                return
            try:
                result1 = await hass.async_add_executor_job(_discover_with_retry, pla)
            except Exception as err:
                _LOGGER.debug("Powerline discovery poll failed: %s", err)
                return

        if result1 is None or entry_data.get("unloading"):
            return

        previous_online = set(online_macs)
        seen_at = time.time()
        discovered_set = _merge_discovered_adapters(entry_data, result1, seen_at)

        # Determine MACs that would be considered lost
        maybe_lost = online_macs - discovered_set

        if maybe_lost:
            # Immediate second check to confirm loss
            async with network_lock:
                if entry_data.get("unloading"):
                    return
                try:
                    result2 = await hass.async_add_executor_job(
                        lambda: _discover_with_retry(pla, attempts=1)
                    )
                except Exception as err:
                    _LOGGER.debug("Second powerline discovery poll failed: %s", err)
                    result2 = []

            if result2:
                new_set_second = _merge_discovered_adapters(entry_data, result2, seen_at)
                discovered_set.update(new_set_second)

        new_set = _apply_discovery_online_update(
            previous_online,
            discovered_set,
            discovery_miss_counts,
        )

        # Update the shared set atomically
        online_macs.clear()
        online_macs.update(new_set)
        ensure_index_map(hass, entry, entry_data, adapter_macs(entry_data) | online_macs)

        # Now poll discover-list for each online adapter to get detailed info
        new_discover_list_data: dict[str, dict] = {
            mac: value
            for mac, value in discover_list_data.items()
            if mac in online_macs
        }
        for mac in sorted(online_macs):
            async with network_lock:
                if entry_data.get("unloading"):
                    return
                try:
                    adapter = adapter_metadata.get(mac, {})
                    backend = adapter.get("backend")
                    pla_targeted = PLAUtil(interface=interface, pla_mac=mac, backend=backend)
                    
                    def _disc_list():
                        try:
                            if backend == "qca":
                                qca_info = pla_targeted.qca_network_info(timeout=2.0)
                                return _qca_discover_list(qca_info, mac)
                            return pla_targeted.discover_list(timeout=2.0)  # type: ignore[arg-type]
                        except TypeError:
                            return pla_targeted.discover_list()
                    
                    disc_list_result = await hass.async_add_executor_job(_disc_list)
                    
                    if disc_list_result and "stations" in disc_list_result:
                        new_discover_list_data[mac] = disc_list_result
                        _LOGGER.debug(f"Got discover-list data for {mac}: {disc_list_result}")
                        _LOGGER.debug(f"Got discover-list data for {mac}: {len(disc_list_result['stations'])} stations")
                    
                except Exception as e:
                    _LOGGER.debug(f"Failed to get discover-list for {mac}: {e}")

        previous_discover_list_data = dict(discover_list_data)
        discover_list_data.clear()
        discover_list_data.update(new_discover_list_data)
        if (
            previous_online != online_macs
            or previous_discover_list_data != new_discover_list_data
        ):
            async_dispatcher_send(hass, snapshot_signal(entry.entry_id))

    async def _poll_discover(now):
        """Periodically refresh adapter presence using discover."""

        if entry_data.get("unloading"):
            return
        await _refresh_discovery()

    entry_data["refresh_discovery"] = _refresh_discovery

    await coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Poll discover independently from the coordinator so adapter presence can
    # change without waiting for a Home Assistant reload. Start this only after
    # platform setup so it cannot compete with initial entity creation.
    entry.async_on_unload(
        async_track_time_interval(
            hass,
            _poll_discover,
            timedelta(seconds=scan_interval),
        )
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""

    if entry_data := hass.data.get(DOMAIN, {}).get(entry.entry_id):
        entry_data["unloading"] = True

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok

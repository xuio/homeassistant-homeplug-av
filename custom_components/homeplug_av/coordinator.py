"""Coordinator for fetching powerline statistics."""

from __future__ import annotations

import logging
import time
import asyncio
from datetime import timedelta
from typing import Dict, Any
import sys
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import DEFAULT_QCA_DIAGNOSTIC_INTERVAL_SECONDS, DOMAIN
from .helpers import adapter_macs, ensure_index_map, normalize_mac, snapshot_signal

_LOGGER = logging.getLogger(__name__)

# Ensure bundled library importable when executed standalone
_LIB_PATH = Path(__file__).parent / "pla-util-py"
if _LIB_PATH.exists() and str(_LIB_PATH) not in sys.path:
    sys.path.insert(0, str(_LIB_PATH))

from pla_util_py import PLAUtil


class PowerlineDataUpdateCoordinator(DataUpdateCoordinator[Dict[str, Any]]):
    """Class to manage fetching powerline data."""

    def __init__(
        self,
        hass: HomeAssistant,
        logger: logging.Logger,
        entry_id: str,
        pla: PLAUtil,
        interface: str,
        update_interval: timedelta,
        lock: asyncio.Lock,
        adapter_retention_seconds: int,
        link_retention_seconds: int,
        qca_diagnostic_interval_seconds: int = DEFAULT_QCA_DIAGNOSTIC_INTERVAL_SECONDS,
    ) -> None:
        """Initialize."""
        self._entry_id = entry_id
        self.pla = pla
        self._interface = interface
        self._lock = lock
        self.mesh_data: Dict[str, Dict[str, Any]] = {}
        self._adapter_retention_seconds = adapter_retention_seconds
        self._link_retention_seconds = link_retention_seconds
        self._qca_diagnostic_interval_seconds = qca_diagnostic_interval_seconds
        self._last_qca_diagnostic_refresh = 0.0
        self._last_poll_attempts = 0

        super().__init__(
            hass,
            logger,
            name="Powerline Data",
            update_interval=update_interval,
        )

    def _entry_data(self) -> dict[str, Any]:
        """Return this config entry's runtime data."""

        return self.hass.data.get(DOMAIN, {}).get(self._entry_id, {})

    def _stats_call(self, mac: str | None = None):
        """Return network stats with 2-second timeout when supported."""
        # Create a new PLAUtil instance for targeted polling
        if mac:
            backend = self._adapter_metadata().get(mac, {}).get("backend")
            pla_targeted = PLAUtil(interface=self._interface, pla_mac=mac, backend=backend)
        else:
            pla_targeted = self.pla
            
        try:
            return pla_targeted.network_stats(timeout=2.0)  # type: ignore[arg-type]
        except TypeError:
            # Older version without timeout param
            return pla_targeted.network_stats()

    def _qca_link_stats_call(self, source_mac: str, peer_mac: str):
        """Return optional QCA per-link counters with a short timeout."""

        pla_targeted = PLAUtil(
            interface=self._interface,
            pla_mac=source_mac,
            backend="qca",
        )
        try:
            return pla_targeted.qca_link_stats(peer_mac, timeout=2.0)  # type: ignore[arg-type]
        except TypeError:
            return pla_targeted.qca_link_stats(peer_mac)

    def _poll_order(
        self,
        poll_macs: list[str],
        adapter_metadata: Dict[str, Dict[str, Any]],
        entry_data: dict[str, Any],
    ) -> tuple[list[str], bool]:
        """Return adapter poll order and whether one QCA source can cover links."""

        qca_macs = [
            mac
            for mac in poll_macs
            if adapter_metadata.get(mac, {}).get("backend") == "qca"
        ]
        if len(qca_macs) < 2 or len(qca_macs) != len(poll_macs):
            return poll_macs, False

        preferred = normalize_mac(entry_data.get("preferred_stats_source"))
        ordered = list(qca_macs)
        if preferred in ordered:
            ordered.remove(preferred)
            ordered.insert(0, preferred)
        return ordered, True

    def _adapter_metadata(self) -> Dict[str, Dict[str, Any]]:
        """Return adapter metadata keyed by lower-case MAC address."""

        return self._entry_data().get("adapter_metadata", {})

    def _sync_adapter_list(self, entry_data: dict[str, Any]) -> None:
        """Mirror retained metadata into the adapter list used by platforms."""

        entry_data["adapters"] = [
            adapter
            for _, adapter in sorted(entry_data.get("adapter_metadata", {}).items())
        ]

    def _remove_registry_entities_for_mac(self, mac: str) -> None:
        """Remove entity registry entries that belong to an expired adapter."""

        registry = er.async_get(self.hass)
        mac = normalize_mac(mac)
        own_prefix = f"powerline_{mac}_"
        to_remove = []
        for entry in er.async_entries_for_config_entry(registry, self._entry_id):
            unique_id = entry.unique_id or ""
            if (
                unique_id.startswith(own_prefix)
                or f"_to_{mac}_" in unique_id
                or f"_from_{mac}_" in unique_id
            ):
                to_remove.append(entry.entity_id)
        for entity_id in to_remove:
            registry.async_remove(entity_id)

    def _remove_registry_entities_for_link(self, source_mac: str, target_mac: str) -> None:
        """Remove entity registry entries that belong to an expired mesh link."""

        registry = er.async_get(self.hass)
        unique_prefixes = (
            f"powerline_{source_mac}_to_{target_mac}_",
            f"powerline_{source_mac}_from_{target_mac}_",
        )
        unique_ids = {
            f"powerline_{source_mac}_to_{target_mac}_tx",
            f"powerline_{source_mac}_from_{target_mac}_rx",
        }
        for entry in er.async_entries_for_config_entry(registry, self._entry_id):
            unique_id = entry.unique_id or ""
            if unique_id in unique_ids or unique_id.startswith(unique_prefixes):
                registry.async_remove(entry.entity_id)

    def _coerce_link_rates(
        self,
        source_mac: str,
        peer_mac: str,
        tx_rate: Any,
        rx_rate: Any,
    ) -> tuple[int, int, bool, bool] | None:
        """Return non-zero link rates, retaining previous values for zero glitches."""

        try:
            tx_rate_int = int(tx_rate)
            rx_rate_int = int(rx_rate)
        except (TypeError, ValueError):
            return None

        previous_link = self.mesh_data.get(f"{source_mac}_{peer_mac}")
        retained_tx_rate = False
        retained_rx_rate = False
        if tx_rate_int == 0 or rx_rate_int == 0:
            if not previous_link:
                _LOGGER.debug(
                    "Ignoring initial zero-rate stats from %s to %s",
                    source_mac,
                    peer_mac,
                )
                return None
            previous_tx_rate = int(previous_link.get("tx_rate") or 0)
            previous_rx_rate = int(previous_link.get("rx_rate") or 0)
            if tx_rate_int == 0 and previous_tx_rate > 0:
                tx_rate_int = previous_tx_rate
                retained_tx_rate = True
            if rx_rate_int == 0 and previous_rx_rate > 0:
                rx_rate_int = previous_rx_rate
                retained_rx_rate = True
        if tx_rate_int == 0 or rx_rate_int == 0:
            _LOGGER.debug(
                "Ignoring zero-rate stats from %s to %s as stale",
                source_mac,
                peer_mac,
            )
            return None

        return tx_rate_int, rx_rate_int, retained_tx_rate, retained_rx_rate

    def _record_link_stat(
        self,
        *,
        mesh_data: Dict[str, Dict[str, Any]],
        fresh_keys: set[str],
        adapter_metadata: Dict[str, Dict[str, Any]],
        now: float,
        source_mac: str,
        peer_mac: str,
        tx_rate: Any,
        rx_rate: Any,
        stat: dict[str, Any],
        derived: bool = False,
    ) -> bool:
        """Record one link, retaining previous good rates through transient misses."""

        source_mac = normalize_mac(source_mac)
        peer_mac = normalize_mac(peer_mac)
        if not source_mac or not peer_mac or source_mac == peer_mac:
            return False

        coerced_rates = self._coerce_link_rates(
            source_mac,
            peer_mac,
            tx_rate,
            rx_rate,
        )
        if coerced_rates is None:
            return False
        tx_rate_int, rx_rate_int, retained_tx_rate, retained_rx_rate = coerced_rates

        adapter = adapter_metadata.setdefault(source_mac, {"mac": source_mac})
        adapter.setdefault("mac", source_mac)
        adapter["last_seen"] = now
        peer = adapter_metadata.setdefault(
            peer_mac,
            {"mac": peer_mac, "interface": "PLC", "last_seen": now},
        )
        peer.setdefault("mac", peer_mac)
        peer["last_seen"] = now

        key = f"{source_mac}_{peer_mac}"
        fresh_keys.add(key)
        mesh_data[key] = {
            "source": source_mac,
            "target": peer_mac,
            "tx_rate": tx_rate_int,
            "rx_rate": rx_rate_int,
            "bda": stat.get("bda"),
            "tx_coupling": stat.get("tx_coupling"),
            "rx_coupling": stat.get("rx_coupling"),
            "role": stat.get("role"),
            "last_seen": now,
            "available": True,
            "stale": retained_tx_rate or retained_rx_rate,
            "tx_rate_retained": retained_tx_rate,
            "rx_rate_retained": retained_rx_rate,
            "derived": derived,
        }
        return True

    @staticmethod
    def _flatten_qca_link_stats(
        stats: dict[str, Any] | None,
        now: float,
    ) -> dict[str, Any]:
        """Return selected VS_LNK_STATS fields for mesh link diagnostics."""

        if not stats:
            return {}

        flattened: dict[str, Any] = {
            "qca_link_stats_updated": now,
        }
        for field in ("status", "direction", "lid", "tei"):
            if stats.get(field) is not None:
                flattened[f"qca_link_stats_{field}"] = stats[field]

        if stats.get("status") not in (0, None):
            return flattened

        for section in ("tx", "rx"):
            section_stats = stats.get(section) or {}
            if not isinstance(section_stats, dict):
                continue
            for field, value in section_stats.items():
                if field == "rx_intervals" or value is None:
                    continue
                flattened[f"qca_{field}"] = value

        rx_stats = stats.get("rx") or {}
        if isinstance(rx_stats, dict):
            intervals = rx_stats.get("rx_intervals") or []
            if intervals and isinstance(intervals[0], dict):
                latest_interval = intervals[0]
                interval_fields = {
                    "rx_phy_rate_mbps": "qca_rx_phy_rate_mbps",
                    "rx_pbs_error_rate_percent": "qca_rx_interval_pbs_error_rate_percent",
                    "rx_ber_error_rate_percent": "qca_rx_interval_ber_error_rate_percent",
                }
                for source_field, target_field in interval_fields.items():
                    if latest_interval.get(source_field) is not None:
                        flattened[target_field] = latest_interval[source_field]

        return flattened

    def _qca_diagnostic_refresh_due(
        self,
        now: float,
        links: list[tuple[str, str]],
    ) -> bool:
        """Return whether optional QCA link diagnostics should be refreshed."""

        interval = getattr(
            self,
            "_qca_diagnostic_interval_seconds",
            DEFAULT_QCA_DIAGNOSTIC_INTERVAL_SECONDS,
        )
        if interval <= 0 or not links:
            return False

        last_refresh = getattr(self, "_last_qca_diagnostic_refresh", 0.0)
        return now - last_refresh >= interval

    async def _async_refresh_qca_link_diagnostics(
        self,
        mesh_data: Dict[str, Dict[str, Any]],
        links: list[tuple[str, str]],
        now: float,
    ) -> None:
        """Refresh optional QCA per-link diagnostics for fresh direct links."""

        if not self._qca_diagnostic_refresh_due(now, links):
            return

        self._last_qca_diagnostic_refresh = now
        seen: set[tuple[str, str]] = set()
        for source_mac, peer_mac in links:
            link = (normalize_mac(source_mac), normalize_mac(peer_mac))
            if not link[0] or not link[1] or link in seen:
                continue
            seen.add(link)

            key = f"{link[0]}_{link[1]}"
            conn = mesh_data.get(key)
            if not conn or conn.get("derived"):
                continue

            try:
                async with self._lock:
                    stats = await self.hass.async_add_executor_job(
                        lambda link=link: self._qca_link_stats_call(link[0], link[1])
                    )
            except Exception as err:  # pragma: no cover - hardware dependent
                _LOGGER.debug(
                    "Failed to collect QCA link stats from %s to %s: %s",
                    link[0],
                    link[1],
                    err,
                )
                conn["qca_link_stats_error"] = str(err)
                conn["qca_link_stats_updated"] = now
                continue

            updates = self._flatten_qca_link_stats(stats, now)
            if updates:
                conn.pop("qca_link_stats_error", None)
                conn.update(updates)

    def _prune_adapters(self, now: float, entry_data: dict[str, Any]) -> None:
        """Drop adapters that have been offline beyond the retention window."""

        metadata = entry_data.get("adapter_metadata", {})
        online_macs = entry_data.get("online_macs", set())
        expired: list[str] = []
        for mac, adapter in list(metadata.items()):
            if mac in online_macs:
                continue
            last_seen = adapter.get("last_seen")
            if last_seen is None:
                continue
            if now - float(last_seen) > self._adapter_retention_seconds:
                expired.append(mac)

        for mac in expired:
            metadata.pop(mac, None)
            entry_data.get("discover_list_data", {}).pop(mac, None)
            self._remove_registry_entities_for_mac(mac)

        if expired:
            self._sync_adapter_list(entry_data)

    async def _async_update_data(self) -> Dict[str, Any]:
        """Fetch data from the powerline network."""
        try:
            entry_data = self._entry_data()
            if entry_data.get("unloading"):
                return self.data or {}

            adapter_metadata = self._adapter_metadata()
            online_macs_ref: set[str] = entry_data.setdefault("online_macs", set())
            online_macs: set[str] = set(online_macs_ref)
            poll_macs = sorted(online_macs or adapter_macs(entry_data))

            # Now poll each adapter individually to get their view of the network
            # This gives us the full mesh of connections
            mesh_data: Dict[str, Dict[str, Any]] = {}
            
            async def poll_single_adapter(mac: str) -> tuple[str, list | None]:
                """Poll a single adapter for its network stats."""
                async with self._lock:
                    stats = await self.hass.async_add_executor_job(
                        lambda: self._stats_call(mac)
                    )
                return mac, stats

            ordered_poll_macs, qca_single_source = self._poll_order(
                poll_macs,
                adapter_metadata,
                entry_data,
            )
            results: list[tuple[str, list | None] | BaseException] = []
            if qca_single_source:
                for mac in ordered_poll_macs:
                    result = await asyncio.gather(
                        poll_single_adapter(mac),
                        return_exceptions=True,
                    )
                    results.extend(result)
                    if result and not isinstance(result[0], Exception) and result[0][1]:
                        entry_data["preferred_stats_source"] = result[0][0]
                        break
            else:
                tasks = [poll_single_adapter(mac) for mac in ordered_poll_macs]
                results = await asyncio.gather(*tasks, return_exceptions=True)

            self._last_poll_attempts = len(results)

            if entry_data.get("unloading"):
                return self.data or {}

            now = time.time()
            fresh_keys: set[str] = set()
            successful_sources: set[str] = set()
            qca_diagnostic_links: list[tuple[str, str]] = []

            # Process results from each adapter
            for result in results:
                if isinstance(result, Exception):
                    _LOGGER.warning(f"Failed to poll adapter: {result}")
                    continue
                    
                source_mac, stats_list = result
                
                if not stats_list:
                    _LOGGER.debug(f"No stats from adapter {source_mac}")
                    continue

                successful_sources.add(source_mac)
                if source_mac not in online_macs_ref:
                    online_macs_ref.add(source_mac)
                entry_data.get("discovery_miss_counts", {}).pop(source_mac, None)
                online_macs.add(source_mac)
                adapter = adapter_metadata.setdefault(source_mac, {"mac": source_mac})
                adapter["last_seen"] = now
                adapter["last_stats_seen"] = now

                # Each adapter reports stats to all its peers
                for stat in stats_list:
                    if not isinstance(stat, dict) or not stat.get("mac"):
                        continue
                        
                    peer_mac = normalize_mac(stat["mac"])
                    if not peer_mac:
                        continue
                    tx_rate = stat.get("to_rate")
                    rx_rate = stat.get("from_rate")
                    if tx_rate is None or rx_rate is None:
                        continue

                    if not self._record_link_stat(
                        mesh_data=mesh_data,
                        fresh_keys=fresh_keys,
                        adapter_metadata=adapter_metadata,
                        now=now,
                        source_mac=source_mac,
                        peer_mac=peer_mac,
                        tx_rate=tx_rate,
                        rx_rate=rx_rate,
                        stat=stat,
                    ):
                        continue

                    source_backend = adapter_metadata.get(source_mac, {}).get("backend")
                    peer_backend = adapter_metadata.get(peer_mac, {}).get("backend")
                    if source_backend == "qca" and peer_backend == "qca":
                        qca_diagnostic_links.append((source_mac, peer_mac))
                        reciprocal_stat = {
                            "tx_coupling": stat.get("rx_coupling"),
                            "rx_coupling": stat.get("tx_coupling"),
                        }
                        self._record_link_stat(
                            mesh_data=mesh_data,
                            fresh_keys=fresh_keys,
                            adapter_metadata=adapter_metadata,
                            now=now,
                            source_mac=peer_mac,
                            peer_mac=source_mac,
                            tx_rate=rx_rate,
                            rx_rate=tx_rate,
                            stat=reciprocal_stat,
                            derived=True,
                        )

            await self._async_refresh_qca_link_diagnostics(
                mesh_data,
                qca_diagnostic_links,
                now,
            )

            expired_links: list[tuple[str, str]] = []
            for key, previous in self.mesh_data.items():
                if key in fresh_keys:
                    continue
                source_mac = normalize_mac(previous.get("source"))
                target_mac = normalize_mac(previous.get("target"))
                if not source_mac or not target_mac:
                    continue
                age = now - float(previous.get("last_seen", 0))
                if age > self._link_retention_seconds:
                    expired_links.append((source_mac, target_mac))
                    continue
                retained = dict(previous)
                retained["stale"] = True
                if (
                    source_mac not in online_macs
                    or target_mac not in adapter_metadata
                    or source_mac in successful_sources
                ):
                    retained["available"] = False
                mesh_data[key] = retained

            for source_mac, target_mac in expired_links:
                self._remove_registry_entities_for_link(source_mac, target_mac)

            self._prune_adapters(now, entry_data)
            ensure_index_map(
                self.hass,
                entry_data.get("entry"),
                entry_data,
                adapter_macs(entry_data)
                | online_macs
                | {link["source"] for link in mesh_data.values()}
                | {link["target"] for link in mesh_data.values()},
            )
            self._sync_adapter_list(entry_data)

            _LOGGER.info(
                "Collected mesh data: %d retained connections from %d online adapters (%d poll attempt(s))",
                len(mesh_data),
                len(poll_macs),
                self._last_poll_attempts,
            )

            # Create minimal adapter entries for device tracking
            adapter_data: Dict[str, Any] = {}
            for mac, adapter in adapter_metadata.items():
                adapter_data[mac] = dict(adapter)

            # Store mesh data as coordinator attribute
            self.mesh_data = mesh_data
            async_dispatcher_send(self.hass, snapshot_signal(self._entry_id))

            return adapter_data
        except Exception as err:
            raise UpdateFailed(err) from err

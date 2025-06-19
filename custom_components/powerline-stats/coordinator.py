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

from .const import DOMAIN

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
        pla: PLAUtil,
        interface: str,
        update_interval: timedelta,
        lock: asyncio.Lock,
    ) -> None:
        """Initialize."""
        self.pla = pla
        self._interface = interface
        self._lock = lock
        self._known_macs: set[str] = set()
        self.mesh_data: Dict[str, Dict[str, Any]] = {}

        super().__init__(
            hass,
            logger,
            name="Powerline Data",
            update_interval=update_interval,
        )

    def _stats_call(self, mac: str | None = None):
        """Return network stats with 2-second timeout when supported."""
        # Create a new PLAUtil instance for targeted polling
        if mac:
            pla_targeted = PLAUtil(interface=self._interface, pla_mac=mac)
        else:
            pla_targeted = self.pla
            
        try:
            return pla_targeted.network_stats(timeout=2.0)  # type: ignore[arg-type]
        except TypeError:
            # Older version without timeout param
            return pla_targeted.network_stats()

    async def _async_update_data(self) -> Dict[str, Any]:
        """Fetch data from the powerline network."""
        try:
            # If we have previous data, include those MACs too
            if self.data:
                for key in self.data.keys():
                    if not key.startswith("_"):  # Skip special keys
                        self._known_macs.add(key)

            # Also get MACs from the initial discovery
            entry_data = self.hass.data.get(DOMAIN, {})
            for entry_id, data in entry_data.items():
                if "adapters" in data:
                    for adapter in data["adapters"]:
                        self._known_macs.add(adapter["mac"].lower())

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

            # Poll all adapters in parallel (but serialized by the lock)
            tasks = [poll_single_adapter(mac) for mac in self._known_macs]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            now = time.time()

            # Process results from each adapter
            for result in results:
                if isinstance(result, Exception):
                    _LOGGER.warning(f"Failed to poll adapter: {result}")
                    continue
                    
                source_mac, stats_list = result
                
                if not stats_list:
                    _LOGGER.debug(f"No stats from adapter {source_mac}")
                    continue

                # Each adapter reports stats to all its peers
                for stat in stats_list:
                    if not isinstance(stat, dict) or not stat.get("mac"):
                        continue
                        
                    peer_mac = stat["mac"].lower()
                    
                    # Create entry for this connection if needed
                    key = f"{source_mac}_{peer_mac}"
                    mesh_data[key] = {
                        "source": source_mac,
                        "target": peer_mac,
                        "tx_rate": stat.get("to_rate", 0),  # Rate TO peer
                        "rx_rate": stat.get("from_rate", 0),  # Rate FROM peer  
                        "last_seen": now,
                    }

            _LOGGER.info(f"Collected mesh data: {len(mesh_data)} connections from {len(self._known_macs)} adapters")

            # Create minimal adapter entries for device tracking
            adapter_data: Dict[str, Any] = {}
            for mac in self._known_macs:
                adapter_data[mac] = {"last_seen": now}

            # Store mesh data as coordinator attribute
            self.mesh_data = mesh_data

            return adapter_data
        except Exception as err:
            raise UpdateFailed(err) from err

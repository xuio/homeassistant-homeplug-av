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
        update_interval: timedelta,
        lock: asyncio.Lock,
    ) -> None:
        """Initialize."""
        self.pla = pla
        self._lock = lock

        super().__init__(
            hass,
            logger,
            name="Powerline Data",
            update_interval=update_interval,
        )

    def _stats_call(self):
        """Return network stats (serialized by lock)."""
        return self.pla.network_stats()

    async def _async_update_data(self) -> Dict[str, Any]:
        """Fetch data from the powerline network."""
        try:
            # Try up to 3 attempts as occasional packet loss can cause missed
            # replies even though the CLI always succeeds.  Each retry waits a
            # short delay so the request frames do not collide.

            stats_list = None
            async with self._lock:
                for attempt in range(3):
                    stats_list = await self.hass.async_add_executor_job(self._stats_call)
                    if stats_list:
                        break
                    await asyncio.sleep(0.2 * (attempt + 1))

            # _LOGGER.warning(f"stats_list: {stats_list}")

            # If the library returned None or anything other than an iterable of
            # dicts, treat it as no data instead of crashing.
            if not stats_list:
                return self.data or {}

            # Ensure we only process well-formed dicts containing a MAC key.
            valid_stats = [
                stat
                for stat in stats_list
                if isinstance(stat, dict) and stat.get("mac")
            ]

            now = time.time()

            # Build new dict starting from previous data so we keep entries that
            # temporarily disappear (they may come back in the next poll).
            new_data: Dict[str, Any] = {} if self.data is None else dict(self.data)

            for stat in valid_stats:
                mac_lc = stat["mac"].lower()
                entry = new_data.get(mac_lc, {})
                entry.update({
                    "to_rate": stat.get("to_rate"),
                    "from_rate": stat.get("from_rate"),
                    "last_seen": now,
                })
                new_data[mac_lc] = entry

            return new_data
        except Exception as err:
            raise UpdateFailed(err) from err

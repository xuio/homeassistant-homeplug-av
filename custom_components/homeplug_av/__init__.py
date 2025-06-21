"""Homeplug AV integration."""

from __future__ import annotations

import sys
from pathlib import Path
import asyncio

# Ensure bundled pla_util_py library is importable
_LIB_PATH = Path(__file__).parent / "pla-util-py"
if _LIB_PATH.exists() and str(_LIB_PATH) not in sys.path:
    sys.path.insert(0, str(_LIB_PATH))

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN, PLATFORMS, DEFAULT_SCAN_INTERVAL
from .coordinator import PowerlineDataUpdateCoordinator
from pla_util_py import PLAUtil

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:  # pragma: no cover
    """Set up the integration via config file (not supported)."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Powerline Stats from a config entry."""

    interface: str = entry.data["interface"]
    scan_interval: int = entry.options.get(
        "scan_interval", entry.data.get("scan_interval", DEFAULT_SCAN_INTERVAL)
    )

    pla = PLAUtil(interface=interface)

    # Shared lock to prevent concurrent network access
    network_lock = asyncio.Lock()

    # Discovery of adapters may block, so run in executor.
    adapters = await hass.async_add_executor_job(pla.discover)
    _LOGGER.debug("Discovered %d powerline adapter(s)", len(adapters))

    coordinator = PowerlineDataUpdateCoordinator(
        hass,
        _LOGGER,
        pla=pla,
        interface=interface,
        update_interval=timedelta(seconds=scan_interval),
        lock=network_lock,
    )

    # Set holding MAC addresses seen by the last discover poll
    online_macs: set[str] = set(a["mac"].lower() for a in adapters)

    # Dictionary to store discover-list data for each adapter
    discover_list_data: dict[str, dict] = {}

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "adapters": adapters,
        "online_macs": online_macs,
        "lock": network_lock,
        "index_map": {},
        "discover_list_data": discover_list_data,
    }

    async def _poll_discover(now):
        """Periodically refresh adapter presence using discover."""
        # First discover pass
        async with network_lock:
            def _disc():
                try:
                    return pla.discover(timeout=2.0)  # type: ignore[arg-type]
                except TypeError:
                    return pla.discover()

            result1 = await hass.async_add_executor_job(_disc)

        if result1 is None:
            return

        new_set = {a["mac"].lower() for a in result1}

        # Determine MACs that would be considered lost
        maybe_lost = online_macs - new_set

        if maybe_lost:
            # Immediate second check to confirm loss
            async with network_lock:
                def _disc():
                    try:
                        return pla.discover(timeout=2.0)  # type: ignore[arg-type]
                    except TypeError:
                        return pla.discover()

                result2 = await hass.async_add_executor_job(_disc)

            if result2:
                new_set_second = {a["mac"].lower() for a in result2}
                new_set.update(new_set_second)

        # Update the shared set atomically
        online_macs.clear()
        online_macs.update(new_set)

        # Now poll discover-list for each online adapter to get detailed info
        for mac in online_macs:
            async with network_lock:
                try:
                    # Create targeted PLAUtil instance
                    pla_targeted = PLAUtil(interface=interface, pla_mac=mac)
                    
                    def _disc_list():
                        try:
                            return pla_targeted.discover_list(timeout=2.0)  # type: ignore[arg-type]
                        except TypeError:
                            return pla_targeted.discover_list()
                    
                    disc_list_result = await hass.async_add_executor_job(_disc_list)
                    
                    if disc_list_result and "stations" in disc_list_result:
                        # Store the discover-list data for this adapter
                        discover_list_data[mac] = disc_list_result
                        _LOGGER.debug(f"Got discover-list data for {mac}: {disc_list_result}")
                        _LOGGER.debug(f"Got discover-list data for {mac}: {len(disc_list_result['stations'])} stations")
                        
                        # Fire event for ALL adapters since discover-list contains data about all
                        for adapter_mac in online_macs:
                            hass.bus.async_fire(
                                f"{DOMAIN}_discover_list_updated",
                                {"mac": adapter_mac}
                            )
                    
                except Exception as e:
                    _LOGGER.debug(f"Failed to get discover-list for {mac}: {e}")

    # Poll discover every scan_interval seconds but offset by half interval to
    # avoid clashing with stats polling schedule.
    async_track_time_interval(
        hass,
        _poll_discover,
        timedelta(seconds=scan_interval),
    )

    await coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # ------------------------------------------------------------------
    # Build stable adapter index mapping (Adapter 1, Adapter 2, …) stored in
    # config entry options so it survives Home Assistant restarts.
    # ------------------------------------------------------------------

    existing_map: dict[str, int] = hass.data[DOMAIN][entry.entry_id]["index_map"]

    # Ensure keys are lower-case MAC strings
    existing_map = {k.lower(): v for k, v in existing_map.items()}

    next_idx = max(existing_map.values(), default=0) + 1

    for adapter in adapters:
        mac_lc = adapter["mac"].lower()
        if mac_lc not in existing_map:
            existing_map[mac_lc] = next_idx
            next_idx += 1

    # Persist mapping if it changed
    if existing_map != hass.data[DOMAIN][entry.entry_id]["index_map"]:
        new_options = dict(entry.options)
        new_options["index_map"] = existing_map
        hass.config_entries.async_update_entry(entry, options=new_options)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok

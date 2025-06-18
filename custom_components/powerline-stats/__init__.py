"""Powerline Stats integration."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure bundled pla_util_py library is importable
_LIB_PATH = Path(__file__).parent / "pla-util-py"
if _LIB_PATH.exists() and str(_LIB_PATH) not in sys.path:
    sys.path.insert(0, str(_LIB_PATH))

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS, DEFAULT_SCAN_INTERVAL, DEFAULT_API_TIMEOUT
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
    timeout: float = entry.data.get("timeout", DEFAULT_API_TIMEOUT)

    pla = PLAUtil(interface=interface)

    # Discovery of adapters may block, so run in executor.
    adapters = await hass.async_add_executor_job(pla.discover)
    _LOGGER.debug("Discovered %d powerline adapter(s)", len(adapters))

    coordinator = PowerlineDataUpdateCoordinator(
        hass,
        _LOGGER,
        pla=pla,
        update_interval=timedelta(seconds=scan_interval),
        timeout=timeout,
    )

    await coordinator.async_config_entry_first_refresh()

    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}

    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "adapters": adapters,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok

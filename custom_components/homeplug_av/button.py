"""Button platform for Powerline Stats (adapter restart)."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN
from pla_util_py import PLAUtil

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up button entities for a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    adapters = data.get("adapters", [])
    lock = data.get("lock")

    entities: list[ButtonEntity] = []

    # Create adapter index mapping for consistent naming
    adapter_map = {a["mac"].lower(): a for a in adapters}
    adapter_index_map = {mac: idx + 1 for idx, mac in enumerate(sorted(adapter_map.keys()))}

    # Create restart button for each adapter
    for mac, adapter in adapter_map.items():
        adapter_name = f"Adapter {adapter_index_map[mac]}"
        entities.append(
            PowerlineRestartButton(
                mac=mac,
                adapter_name=adapter_name,
                interface=entry.data["interface"],
                lock=lock,
                hass=hass,
            )
        )

    async_add_entities(entities)


class PowerlineRestartButton(ButtonEntity):
    """Button to restart a powerline adapter."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:restart"

    def __init__(
        self,
        *,
        mac: str,
        adapter_name: str,
        interface: str,
        lock: Any,
        hass: Any,
    ) -> None:
        """Initialize the button."""
        self._mac = mac
        self._adapter_name = adapter_name
        self._interface = interface
        self._lock = lock
        self._hass = hass
        self._adapter_idx = int(adapter_name.split()[-1])
        self._attr_unique_id = f"powerline_{mac}_restart"
        self._attr_name = "Restart"

    async def async_press(self) -> None:
        """Handle the button press."""
        _LOGGER.info(f"Restarting adapter {self._adapter_name} ({self._mac})")
        
        async with self._lock:
            # Create targeted PLAUtil instance for this adapter
            pla = PLAUtil(interface=self._interface, pla_mac=self._mac)
            
            try:
                # Execute restart command
                await self._hass.async_add_executor_job(pla.restart)
                _LOGGER.info(f"Restart command sent to {self._adapter_name}")
            except Exception as e:
                _LOGGER.error(f"Failed to restart {self._adapter_name}: {e}")

    @property
    def device_info(self):
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self._mac)},
            "name": self._adapter_name,
            "model": "Powerline Adapter",
            "manufacturer": "Unknown",
        } 
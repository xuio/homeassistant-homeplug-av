"""Button platform for Homeplug AV (adapter restart)."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.core import callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN
from .helpers import (
    adapter_macs,
    adapter_metadata,
    adapter_name,
    device_info,
    snapshot_signal,
)
from pla_util_py import PLAUtil

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up button entities for a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    lock = data.get("lock")
    added_unique_ids: set[str] = set()

    @callback
    def _add_missing_entities() -> None:
        entities: list[ButtonEntity] = []
        desired_unique_ids: set[str] = set()
        for mac in sorted(adapter_macs(data)):
            unique_id = f"powerline_{mac}_restart"
            desired_unique_ids.add(unique_id)
            if unique_id in added_unique_ids:
                continue
            added_unique_ids.add(unique_id)
            entities.append(
                PowerlineRestartButton(
                    mac=mac,
                    adapter_name=adapter_name(data, mac),
                    interface=entry.data["interface"],
                    lock=lock,
                    hass=hass,
                )
            )
        added_unique_ids.intersection_update(desired_unique_ids)
        if entities:
            async_add_entities(entities)

    _add_missing_entities()
    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            snapshot_signal(entry.entry_id),
            _add_missing_entities,
        )
    )


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
        self._attr_unique_id = f"powerline_{mac}_restart"
        self._attr_name = "Restart"

    async def async_press(self) -> None:
        """Handle the button press."""
        _LOGGER.info(f"Restarting adapter {self._adapter_name} ({self._mac})")
        
        async with self._lock:
            # Create targeted PLAUtil instance for this adapter
            metadata = adapter_metadata(self._hass, self._mac)
            pla = PLAUtil(
                interface=self._interface,
                pla_mac=self._mac,
                backend=metadata.get("backend"),
            )
            
            try:
                # Execute restart command
                await self._hass.async_add_executor_job(pla.restart)
                _LOGGER.info(f"Restart command sent to {self._adapter_name}")
            except Exception as e:
                _LOGGER.error(f"Failed to restart {self._adapter_name}: {e}")

    @property
    def device_info(self):
        """Return device info."""
        metadata = adapter_metadata(self._hass, self._mac)
        return device_info(self._mac, self._adapter_name, metadata)

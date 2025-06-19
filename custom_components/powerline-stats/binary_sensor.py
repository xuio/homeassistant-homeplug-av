"""Binary sensor platform for Powerline Stats (online status)."""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up binary sensors for a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]

    entities: list[BinarySensorEntity] = []

    adapters = data.get("adapters", [])

    online_macs: set[str] = data.get("online_macs", set())

    # Build set of MAC addresses we know about (coordinator + discovered)
    macs: set[str] = set(coordinator.data.keys())
    macs.update(a["mac"].lower() for a in adapters)

    for mac in macs:
        entities.append(PowerlineOnlineSensor(coordinator, online_macs, mac=mac))

    async_add_entities(entities)


class PowerlineOnlineSensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor representing adapter online/offline state."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_icon = "mdi:lan"

    def __init__(self, coordinator, online_macs: set[str], *, mac: str) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self._attr_name = f"{mac} Online"
        self._attr_unique_id = f"{mac}-online"
        self._online_macs = online_macs
        # Determine initial state based on discover data
        self._last_state = mac.lower() in online_macs

    @property
    def is_on(self) -> bool | None:  # type: ignore[override]
        # Connected when MAC present in latest discover list
        self._last_state = self._mac in self._online_macs
        return self._last_state

    @property
    def available(self) -> bool:
        """Binary sensor is always available once first state determined."""
        return True

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._mac)},
            "name": self._mac,
            "manufacturer": "Unknown",
            "model": "Powerline Adapter",
        } 
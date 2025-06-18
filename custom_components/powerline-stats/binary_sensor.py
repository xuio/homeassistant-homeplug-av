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

    # Build set of known MAC addresses (same logic as sensor.py)
    macs = set(coordinator.data.keys())
    for adapter in data.get("adapters", []):
        macs.add(adapter["mac"].lower())

    for mac in macs:
        entities.append(PowerlineOnlineSensor(coordinator, mac=mac))

    async_add_entities(entities)


class PowerlineOnlineSensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor representing adapter online/offline state."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_icon = "mdi:lan"

    def __init__(self, coordinator, *, mac: str) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self._attr_name = f"{mac} Online"
        self._attr_unique_id = f"{mac}-online"
        # Determine initial state based on current coordinator data (first
        # refresh happened before entity creation).
        data = coordinator.data.get(mac)
        if data:
            to_rate = data.get("to_rate", 0)
            from_rate = data.get("from_rate", 0)
            self._last_state = not (to_rate == 0 and from_rate == 0)
        else:
            self._last_state = None

    @property
    def is_on(self) -> bool | None:  # type: ignore[override]
        data = self.coordinator.data.get(self._mac)

        if data:
            to_rate = data.get("to_rate", 0)
            from_rate = data.get("from_rate", 0)
            self._last_state = not (to_rate == 0 and from_rate == 0)

        # If we didn't get an update (`data` is None), keep previous state
        return self._last_state

    @property
    def available(self) -> bool:
        """Binary sensor is always available once first state determined."""
        return self._last_state is not None

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._mac)},
            "name": self._mac,
            "manufacturer": "Unknown",
            "model": "Powerline Adapter",
        } 
"""Sensor platform for Powerline Stats."""

from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorEntity

try:
    from homeassistant.const import DATA_RATE_MEGABITS_PER_SECOND as UNIT_MBIT_S
except ImportError:  # Older HA versions
    UNIT_MBIT_S = "Mbit/s"
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up sensors for a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]

    entities: list[SensorEntity] = []

    # Build the set of MAC addresses we know about:
    macs = set(coordinator.data.keys())

    # Also include any adapters returned by the discovery step that was executed
    # during setup – this may include devices that are currently offline.
    for adapter in data.get("adapters", []):
        macs.add(adapter["mac"].lower())

    for mac_lc in macs:
        entities.append(
            PowerlineRateSensor(
                coordinator,
                mac=mac_lc,
                name=f"{mac_lc} TX Rate",
                direction="tx",
            )
        )
        entities.append(
            PowerlineRateSensor(
                coordinator,
                mac=mac_lc,
                name=f"{mac_lc} RX Rate",
                direction="rx",
            )
        )

    async_add_entities(entities)


class PowerlineRateSensor(CoordinatorEntity, SensorEntity):
    """Representation of a powerline data rate sensor."""

    _attr_icon = "mdi:speedometer"
    _attr_native_unit_of_measurement = UNIT_MBIT_S

    def __init__(
        self,
        coordinator,
        *,
        mac: str,
        name: str,
        direction: str,  # "tx" or "rx"
    ) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self._direction = direction
        self._attr_name = name
        self._attr_unique_id = f"{mac}-{direction}"
        data = coordinator.data.get(mac)
        if data:
            to_rate = data.get("to_rate", 0)
            from_rate = data.get("from_rate", 0)
            self._last_available = not (to_rate == 0 and from_rate == 0)
        else:
            self._last_available = False

    @property
    def native_value(self):
        data = self.coordinator.data.get(self._mac)
        if not data:
            return None

        if self._direction == "tx":
            # Rate from adapter to local (uplink)
            return data.get("from_rate")
        return data.get("to_rate")

    @property
    def available(self) -> bool:
        data = self.coordinator.data.get(self._mac)
        if data:
            to_rate = data.get("to_rate", 0)
            from_rate = data.get("from_rate", 0)
            self._last_available = not (to_rate == 0 and from_rate == 0)

        return self._last_available

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._mac)},
            "name": self._attr_name.split(" ")[0],
            "manufacturer": "Unknown",
            "model": "Powerline Adapter",
        }

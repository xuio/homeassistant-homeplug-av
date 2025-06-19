"""Sensor platform for Powerline Stats."""

from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorEntity

try:
    from homeassistant.const import DATA_RATE_MEGABITS_PER_SECOND as UNIT_MBIT_S
except ImportError:  # Older HA versions
    UNIT_MBIT_S = "Mbit/s"
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up sensors for a config entry."""

    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    adapters = data.get("adapters", [])

    adapter_map = {a["mac"].lower(): a for a in adapters}

    entities: list[SensorEntity] = []

    # ------------------------------------------------------------------
    # Create static info sensors (interface & HFID) for ALL adapters
    # ------------------------------------------------------------------

    for mac, adapter in adapter_map.items():
        interface = adapter.get("interface", "Unknown")
        hfid = adapter.get("hfid", "Unknown")

        entities.append(
            PowerlineStaticSensor(
                coordinator,
                mac=mac,
                name=f"{mac} Interface",
                value=interface,
                unique_suffix="interface",
                icon="mdi:cable-data",
            )
        )

        entities.append(
            PowerlineStaticSensor(
                coordinator,
                mac=mac,
                name=f"{mac} HFID",
                value=hfid,
                unique_suffix="hfid",
                icon="mdi:identifier",
            )
        )

    # ------------------------------------------------------------------
    # Speed sensors only for adapters NOT connected via MII interfaces
    # ------------------------------------------------------------------

    ignore_macs_speed = {
        m
        for m, a in adapter_map.items()
        if a.get("interface", "").startswith("MII")
    }

    macs_speed: set[str] = set(coordinator.data.keys())
    macs_speed.update(adapter_map.keys())
    macs_speed.difference_update(ignore_macs_speed)

    for mac in macs_speed:
        entities.append(
            PowerlineRateSensor(
                coordinator,
                mac=mac,
                name=f"{mac} TX Rate",
                direction="tx",
            )
        )
        entities.append(
            PowerlineRateSensor(
                coordinator,
                mac=mac,
                name=f"{mac} RX Rate",
                direction="rx",
            )
        )

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# Entity classes
# ---------------------------------------------------------------------------


class PowerlineStaticSensor(CoordinatorEntity, SensorEntity):
    """Static sensor exposing adapter info (interface or HFID)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator,
        *,
        mac: str,
        name: str,
        value: str,
        unique_suffix: str,
        icon: str,
    ) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self._value = value
        self._attr_name = name
        self._attr_unique_id = f"{mac}-{unique_suffix}"
        self._attr_icon = icon

    @property
    def native_value(self):
        return self._value

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._mac)},
            "name": self._mac,
            "manufacturer": "Unknown",
            "model": "Powerline Adapter",
        }

    @property
    def available(self) -> bool:
        """Static metadata is always available."""
        return True


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
        # Always available; a disconnected adapter will simply report 0 Mbit/s.
        return True

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._mac)},
            "name": self._attr_name.split(" ")[0],
            "manufacturer": "Unknown",
            "model": "Powerline Adapter",
        }

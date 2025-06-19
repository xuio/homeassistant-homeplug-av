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

    # Create adapter index mapping for consistent naming
    adapter_index_map = {mac: idx + 1 for idx, mac in enumerate(sorted(adapter_map.keys()))}

    # Ensure coordinator has run at least once
    if not coordinator.data:
        await coordinator.async_refresh()

    # ------------------------------------------------------------------
    # Create static info sensors (interface & HFID) for ALL adapters
    # ------------------------------------------------------------------

    for mac, adapter in adapter_map.items():
        adapter_name = f"Adapter {adapter_index_map[mac]}"
        interface = adapter.get("interface", "Unknown")
        hfid = adapter.get("hfid", "Unknown")

        # MAC Address sensor
        entities.append(
            PowerlineStaticSensor(
                coordinator,
                mac=mac,
                adapter_name=adapter_name,
                sensor_name="MAC Address",
                value=mac,
                unique_id=f"powerline_adapter_{adapter_index_map[mac]}_mac",
                icon="mdi:ethernet",
            )
        )

        entities.append(
            PowerlineStaticSensor(
                coordinator,
                mac=mac,
                adapter_name=adapter_name,
                sensor_name="Interface",
                value=interface,
                unique_id=f"powerline_adapter_{adapter_index_map[mac]}_interface",
                icon="mdi:cable-data",
            )
        )

        entities.append(
            PowerlineStaticSensor(
                coordinator,
                mac=mac,
                adapter_name=adapter_name,
                sensor_name="HFID",
                value=hfid,
                unique_id=f"powerline_adapter_{adapter_index_map[mac]}_hfid",
                icon="mdi:identifier",
            )
        )

    # ------------------------------------------------------------------
    # Mesh connection sensors (individual links between adapters)
    # ------------------------------------------------------------------

    # Get mesh data from coordinator data
    mesh_data = coordinator.mesh_data

    _LOGGER.info(f"Found {len(mesh_data)} mesh connections")

    # Create sensors for each mesh connection
    for conn_key, conn_data in mesh_data.items():
        source_mac = conn_data.get("source", "")
        target_mac = conn_data.get("target", "")
        
        source_name = f"Adapter {adapter_index_map.get(source_mac, '?')}"
        target_name = f"Adapter {adapter_index_map.get(target_mac, '?')}"
        source_idx = adapter_index_map.get(source_mac, 0)
        target_idx = adapter_index_map.get(target_mac, 0)
        
        # Create TX sensor (source -> target)
        entities.append(
            PowerlineMeshSensor(
                coordinator,
                source_mac=source_mac,
                target_mac=target_mac,
                source_name=source_name,
                target_name=target_name,
                direction="tx",
                unique_id=f"powerline_adapter_{source_idx}_to_{target_idx}_tx",
            )
        )
        
        # Create RX sensor (target -> source)
        entities.append(
            PowerlineMeshSensor(
                coordinator,
                source_mac=source_mac,
                target_mac=target_mac,
                source_name=source_name,
                target_name=target_name,
                direction="rx",
                unique_id=f"powerline_adapter_{source_idx}_to_{target_idx}_rx",
            )
        )

    _LOGGER.info(f"Created {len(entities)} sensor entities total")

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
        adapter_name: str,
        sensor_name: str,
        value: str,
        unique_id: str,
        icon: str,
    ) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self._adapter_name = adapter_name
        self._sensor_name = sensor_name
        self._value = value
        self._attr_unique_id = unique_id
        self._attr_icon = icon

    @property
    def name(self):
        """Return the name, including device name if renamed."""
        device_name = self.device_info.get("name", self._adapter_name)
        return f"{self._sensor_name}"

    @property
    def native_value(self):
        return self._value

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._mac)},
            "name": self._adapter_name,
            "model": "Powerline Adapter",
            "manufacturer": "Unknown",
        }

    @property
    def available(self) -> bool:
        """Static metadata is always available."""
        return True


class PowerlineMeshSensor(CoordinatorEntity, SensorEntity):
    """Sensor showing rate between specific adapters."""

    _attr_icon = "mdi:speedometer"
    _attr_native_unit_of_measurement = UNIT_MBIT_S

    def __init__(
        self,
        coordinator,
        *,
        source_mac: str,
        target_mac: str,
        source_name: str,
        target_name: str,
        direction: str,  # "tx" or "rx"
        unique_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._source_mac = source_mac
        self._target_mac = target_mac
        self._source_name = source_name
        self._target_name = target_name
        self._direction = direction
        self._attr_unique_id = unique_id

    @property
    def name(self):
        """Return the name based on direction."""
        if self._direction == "tx":
            return f"{self._target_name} TX"
        else:
            return f"{self._target_name} RX"

    @property
    def native_value(self):
        # Get mesh data from coordinator
        mesh_data = self.coordinator.mesh_data
        
        key = f"{self._source_mac}_{self._target_mac}"
        conn = mesh_data.get(key)
        
        if not conn:
            return 0
            
        if self._direction == "tx":
            return conn.get("tx_rate", 0)
        else:
            return conn.get("rx_rate", 0)

    @property
    def available(self) -> bool:
        """Always available."""
        return True

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._source_mac)},
            "name": self._source_name,
            "model": "Powerline Adapter",
            "manufacturer": "Unknown",
        }

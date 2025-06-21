"""Sensor platform for Homeplug AV."""

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
    discover_list_data = data.get("discover_list_data", {})

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
                unique_id=f"powerline_{mac}_mac",
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
                unique_id=f"powerline_{mac}_interface",
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
                unique_id=f"powerline_{mac}_hfid",
                icon="mdi:identifier",
            )
        )

        # Add discover-list based sensors if data is available
        disc_data = discover_list_data.get(mac, {})
        stations = disc_data.get("stations", [])
        
        _LOGGER.debug(f"Discover-list data for {mac}: {disc_data}")
        
        # Find this adapter's own info in the stations list
        own_station = None
        for station in stations:
            if station.get("mac", "").lower() == mac:
                own_station = station
                _LOGGER.info(f"Found own station data for {mac}: {own_station}")
                break
        
        # Always create these sensors, they'll show "Unknown" until data is available
        # TEI
        entities.append(
            PowerlineDiscoverListSensor(
                coordinator,
                mac=mac,
                adapter_name=adapter_name,
                sensor_name="TEI",
                field_name="tei",
                unique_id=f"powerline_{mac}_tei",
                icon="mdi:numeric",
            )
        )
        
        # SNID
        entities.append(
            PowerlineDiscoverListSensor(
                coordinator,
                mac=mac,
                adapter_name=adapter_name,
                sensor_name="SNID",
                field_name="snid",
                unique_id=f"powerline_{mac}_snid",
                icon="mdi:identifier",
            )
        )
        
        # CCo
        entities.append(
            PowerlineDiscoverListBooleanSensor(
                coordinator,
                mac=mac,
                adapter_name=adapter_name,
                sensor_name="CCo",
                field_name="cco",
                unique_id=f"powerline_{mac}_cco",
                icon="mdi:router-network",
            )
        )
        
        # PCo
        entities.append(
            PowerlineDiscoverListBooleanSensor(
                coordinator,
                mac=mac,
                adapter_name=adapter_name,
                sensor_name="PCo",
                field_name="pco",
                unique_id=f"powerline_{mac}_pco",
                icon="mdi:router-wireless",
            )
        )
        
        # Backup CCo
        entities.append(
            PowerlineDiscoverListBooleanSensor(
                coordinator,
                mac=mac,
                adapter_name=adapter_name,
                sensor_name="Backup CCo",
                field_name="bcco",
                unique_id=f"powerline_{mac}_bcco",
                icon="mdi:backup-restore",
            )
        )
        
        # Signal Level
        entities.append(
            PowerlineDiscoverListSignalSensor(
                coordinator,
                mac=mac,
                adapter_name=adapter_name,
                sensor_name="Signal Level",
                field_name="signal_level",
                unique_id=f"powerline_{mac}_signal",
                icon="mdi:signal",
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
        
        # Create TX sensor (source -> target)
        entities.append(
            PowerlineMeshSensor(
                coordinator,
                source_mac=source_mac,
                target_mac=target_mac,
                source_name=source_name,
                target_name=target_name,
                direction="tx",
                unique_id=f"powerline_{source_mac}_to_{target_mac}_tx",
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
                unique_id=f"powerline_{source_mac}_from_{target_mac}_rx",
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


def _format_signal_level(level: int) -> str:
    """Format signal level (0-15) to human readable string."""
    if level == 0:
        return "Not available"
    elif level == 15:
        return "≤ -75 dB"
    elif level == 1:
        return "-10 to 0 dB"
    else:
        # Levels 2-14: Each step is 5 dB
        upper = -5 * level
        lower = -5 * (level + 1)
        return f"{lower} to {upper} dB"


class PowerlineBooleanSensor(PowerlineStaticSensor):
    """Static boolean sensor showing Yes/No."""
    
    @property
    def native_value(self):
        return "Yes" if self._value else "No"


class PowerlineDiscoverListSensor(CoordinatorEntity, SensorEntity):
    """Sensor that gets its value from discover-list data."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(
        self,
        coordinator,
        *,
        mac: str,
        adapter_name: str,
        sensor_name: str,
        field_name: str,
        unique_id: str,
        icon: str,
    ) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self._adapter_name = adapter_name
        self._sensor_name = sensor_name
        self._field_name = field_name
        self._attr_unique_id = unique_id
        self._attr_icon = icon
        self._update_listener = None

    async def async_added_to_hass(self) -> None:
        """Register event listener when entity is added."""
        await super().async_added_to_hass()
        
        # Listen for discover-list updates
        self._update_listener = self.hass.bus.async_listen(
            f"{DOMAIN}_discover_list_updated",
            self._handle_discover_list_update
        )

    async def async_will_remove_from_hass(self) -> None:
        """Remove event listener when entity is removed."""
        await super().async_will_remove_from_hass()
        if self._update_listener:
            self._update_listener()

    async def _handle_discover_list_update(self, event):
        """Handle discover-list update event."""
        if event.data.get("mac") == self._mac:
            self.async_write_ha_state()

    @property
    def name(self):
        """Return the name."""
        return f"{self._sensor_name}"

    @property
    def native_value(self):
        """Get value from discover-list data."""
        # Access discover-list data from the integration data
        entry_data = self.hass.data.get(DOMAIN, {})
        for entry_id, data in entry_data.items():
            discover_list_data = data.get("discover_list_data", {})
            # Look through ALL adapters' discover-list data to find info about THIS adapter
            for reporting_mac, disc_data in discover_list_data.items():
                stations = disc_data.get("stations", [])
                
                # Find this adapter in the reporting adapter's station list
                for station in stations:
                    if station.get("mac", "").lower() == self._mac:
                        value = station.get(self._field_name)
                        if value is not None:
                            return str(value)
        
        return "Unknown"

    @property
    def available(self) -> bool:
        """Always available."""
        return True

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._mac)},
            "name": self._adapter_name,
            "model": "Powerline Adapter",
            "manufacturer": "Unknown",
        }


class PowerlineDiscoverListBooleanSensor(PowerlineDiscoverListSensor):
    """Boolean sensor from discover-list data."""
    
    @property
    def native_value(self):
        """Get boolean value and convert to Yes/No."""
        # Access discover-list data from the integration data
        entry_data = self.hass.data.get(DOMAIN, {})
        for entry_id, data in entry_data.items():
            discover_list_data = data.get("discover_list_data", {})
            # Look through ALL adapters' discover-list data to find info about THIS adapter
            for reporting_mac, disc_data in discover_list_data.items():
                stations = disc_data.get("stations", [])
                
                # Find this adapter in the reporting adapter's station list
                for station in stations:
                    if station.get("mac", "").lower() == self._mac:
                        value = station.get(self._field_name, False)
                        return "Yes" if value else "No"
        
        return "No"


class PowerlineDiscoverListSignalSensor(PowerlineDiscoverListSensor):
    """Signal level sensor with special formatting."""
    
    @property
    def native_value(self):
        """Get signal level and format it."""
        # Access discover-list data from the integration data
        entry_data = self.hass.data.get(DOMAIN, {})
        for entry_id, data in entry_data.items():
            discover_list_data = data.get("discover_list_data", {})
            # Look through ALL adapters' discover-list data to find info about THIS adapter
            for reporting_mac, disc_data in discover_list_data.items():
                stations = disc_data.get("stations", [])
                
                # Find this adapter in the reporting adapter's station list
                for station in stations:
                    if station.get("mac", "").lower() == self._mac:
                        signal_level = station.get(self._field_name, 0)
                        return _format_signal_level(signal_level)
        
        return "Unknown"

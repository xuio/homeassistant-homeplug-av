"""Sensor platform for Homeplug AV."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity

try:
    from homeassistant.const import DATA_RATE_MEGABITS_PER_SECOND as UNIT_MBIT_S
except ImportError:  # Older HA versions
    UNIT_MBIT_S = "Mbit/s"
from homeassistant.core import callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN
from .helpers import (
    adapter_macs,
    adapter_metadata,
    adapter_name,
    device_info,
    normalize_mac,
    snapshot_signal,
)

_LOGGER = logging.getLogger(__name__)

ENABLED_STATIC_DIAGNOSTICS = {
    "av_version",
    "backend",
    "chipset",
    "chipset_vendor",
    "firmware",
    "homeplug_oui",
    "manufacturer",
}

def _format_bool(value: Any) -> str:
    return "Yes" if bool(value) else "No"


def _format_hex(width: int):
    def _formatter(value: Any) -> str:
        if value is None:
            return "Unknown"
        try:
            return f"0x{int(value):0{width}x}"
        except (TypeError, ValueError):
            return str(value)

    return _formatter


def _format_text(value: Any) -> str:
    return "Unknown" if value is None else str(value)


DISCOVER_LIST_SENSOR_DEFINITIONS = (
    ("Role", "role", "mdi:account-network"),
    ("NID", "nid", "mdi:identifier"),
    ("CCo MAC", "cco_mac", "mdi:router-network"),
    ("CCo TEI", "cco_tei", "mdi:numeric"),
    ("BDA", "bda", "mdi:bridge"),
    ("TX Coupling", "tx_coupling", "mdi:transit-connection-variant"),
    ("RX Coupling", "rx_coupling", "mdi:transit-connection-variant"),
    ("Network Stations", "station_count", "mdi:counter"),
    ("QCA Network Sub-Version", "qca_network_sub_version", "mdi:information"),
)

STATIC_SENSOR_DEFINITIONS = (
    ("Backend", "backend", "mdi:chip", _format_text, "text"),
    ("Manufacturer", "manufacturer", "mdi:factory", _format_text, "text"),
    ("Chipset Vendor", "chipset_vendor", "mdi:chip", _format_text, "text"),
    ("Chipset", "chipset", "mdi:chip", _format_text, "text"),
    ("Firmware", "firmware", "mdi:package-variant", _format_text, "text"),
    ("HomePlug OUI", "homeplug_oui", "mdi:identifier", _format_text, "text"),
    ("AV Version", "av_version", "mdi:information", _format_text, "text"),
    ("Device Class", "device_class", "mdi:identifier", _format_hex(2), "text"),
    ("Ident", "ident", "mdi:identifier", _format_hex(8), "text"),
    ("Version Status", "status", "mdi:list-status", _format_hex(2), "text"),
    ("Backup CCo Capable", "backup_cco", "mdi:backup-restore", _format_bool, "bool"),
    ("Proxy Capable", "proxy", "mdi:server-network", _format_bool, "bool"),
    ("Implementation Version", "implementation_version", "mdi:information", _format_text, "text"),
    ("Device Family", "device_family", "mdi:family-tree", _format_text, "text"),
    ("Device Type", "device_type", "mdi:expansion-card", _format_text, "text"),
    ("Attribute Status", "op_status", "mdi:list-status", _format_hex(2), "text"),
    ("Attribute Type", "op_rtype", "mdi:identifier", _format_hex(2), "text"),
    ("Attribute Length", "op_attribute_length", "mdi:ruler", _format_text, "text"),
    ("OP Firmware Version", "op_firmware_version", "mdi:package-variant", _format_text, "text"),
    ("Build Date", "op_build_date", "mdi:calendar", _format_text, "text"),
    ("Release Type", "op_release_type", "mdi:tag", _format_text, "text"),
    ("Build Number", "op_build_number", "mdi:counter", _format_text, "text"),
    ("Sustaining Version", "op_sustaining_version", "mdi:counter", _format_text, "text"),
    ("DRAM Size", "op_dram_size_mb", "mdi:memory", _format_text, "text"),
    ("DRAM Type", "op_dram_type", "mdi:memory", _format_hex(2), "text"),
    ("Line Frequency", "op_line_frequency", "mdi:sine-wave", _format_hex(2), "text"),
    ("Authorization Mode", "op_auth_mode", "mdi:shield-key", _format_hex(2), "text"),
    ("AFE TX Gain", "op_afe_tx_gain_db", "mdi:signal", _format_text, "text"),
    ("Relative SNR Difference", "op_relative_snr_diff_db", "mdi:signal-distance-variant", _format_text, "text"),
    ("DSP384 Threshold", "op_dsp384_threshold", "mdi:tune", _format_text, "text"),
    ("RAM Blocks RX", "op_ram_block_rx_count", "mdi:counter", _format_text, "text"),
    ("RAM Blocks TX", "op_ram_block_tx_count", "mdi:counter", _format_text, "text"),
    ("RAM Blocks Shared", "op_ram_block_shared_count", "mdi:counter", _format_text, "text"),
    ("Free RAM Blocks RX", "op_ram_block_free_rx_count", "mdi:counter", _format_text, "text"),
    ("Free RAM Blocks TX", "op_ram_block_free_tx_count", "mdi:counter", _format_text, "text"),
    ("Free RAM Blocks Shared", "op_ram_block_free_shared_count", "mdi:counter", _format_text, "text"),
    (
        "Microcontroller Diagnostics",
        "op_microcontroller_diag_enabled",
        "mdi:bug-check",
        _format_bool,
        "bool",
    ),
)

MESH_DIAGNOSTIC_SENSOR_DEFINITIONS = (
    ("BDA", "bda", "mdi:bridge"),
    ("TX Coupling", "tx_coupling", "mdi:transit-connection-variant"),
    ("RX Coupling", "rx_coupling", "mdi:transit-connection-variant"),
    ("Target Role", "role", "mdi:account-network"),
)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up sensors for a config entry."""

    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]

    # Ensure coordinator has run at least once
    if not coordinator.data:
        await coordinator.async_refresh()

    added_unique_ids: set[str] = set()

    def _adapter_entities(mac: str) -> list[SensorEntity]:
        mac = normalize_mac(mac)
        metadata = adapter_metadata(hass, mac)
        name = adapter_name(data, mac)
        entities: list[SensorEntity] = [
            PowerlineStaticSensor(
                coordinator,
                mac=mac,
                adapter_name=name,
                sensor_name="MAC Address",
                value=mac,
                unique_id=f"powerline_{mac}_mac",
                icon="mdi:ethernet",
                metadata=metadata,
            ),
            PowerlineStaticSensor(
                coordinator,
                mac=mac,
                adapter_name=name,
                sensor_name="Interface",
                value=str(metadata.get("interface", "Unknown")),
                field_name="interface",
                unique_id=f"powerline_{mac}_interface",
                icon="mdi:cable-data",
                metadata=metadata,
            ),
            PowerlineStaticSensor(
                coordinator,
                mac=mac,
                adapter_name=name,
                sensor_name="HFID",
                value=str(metadata.get("hfid", "Unknown")),
                field_name="hfid",
                unique_id=f"powerline_{mac}_hfid",
                icon="mdi:identifier",
                metadata=metadata,
            ),
            PowerlineDiscoverListSensor(
                coordinator,
                entry_id=entry.entry_id,
                mac=mac,
                adapter_name=name,
                sensor_name="TEI",
                field_name="tei",
                unique_id=f"powerline_{mac}_tei",
                icon="mdi:numeric",
                metadata=metadata,
            ),
            PowerlineDiscoverListSensor(
                coordinator,
                entry_id=entry.entry_id,
                mac=mac,
                adapter_name=name,
                sensor_name="SNID",
                field_name="snid",
                unique_id=f"powerline_{mac}_snid",
                icon="mdi:identifier",
                metadata=metadata,
            ),
            PowerlineDiscoverListBooleanSensor(
                coordinator,
                entry_id=entry.entry_id,
                mac=mac,
                adapter_name=name,
                sensor_name="CCo",
                field_name="cco",
                unique_id=f"powerline_{mac}_cco",
                icon="mdi:router-network",
                metadata=metadata,
            ),
            PowerlineDiscoverListBooleanSensor(
                coordinator,
                entry_id=entry.entry_id,
                mac=mac,
                adapter_name=name,
                sensor_name="PCo",
                field_name="pco",
                unique_id=f"powerline_{mac}_pco",
                icon="mdi:router-wireless",
                metadata=metadata,
                enabled_default=False,
            ),
            PowerlineDiscoverListBooleanSensor(
                coordinator,
                entry_id=entry.entry_id,
                mac=mac,
                adapter_name=name,
                sensor_name="Backup CCo",
                field_name="bcco",
                unique_id=f"powerline_{mac}_bcco",
                icon="mdi:backup-restore",
                metadata=metadata,
                enabled_default=False,
            ),
            PowerlineDiscoverListSignalSensor(
                coordinator,
                entry_id=entry.entry_id,
                mac=mac,
                adapter_name=name,
                sensor_name="Signal Level",
                field_name="signal_level",
                unique_id=f"powerline_{mac}_signal",
                icon="mdi:signal",
                metadata=metadata,
                enabled_default=False,
            ),
        ]

        for sensor_name, field_name, icon in DISCOVER_LIST_SENSOR_DEFINITIONS:
            entities.append(
                PowerlineDiscoverListSensor(
                    coordinator,
                    entry_id=entry.entry_id,
                    mac=mac,
                    adapter_name=name,
                    sensor_name=sensor_name,
                    field_name=field_name,
                    unique_id=f"powerline_{mac}_{field_name}",
                    icon=icon,
                    metadata=metadata,
                    enabled_default=False,
                )
            )

        for sensor_name, field_name, icon, formatter, sensor_type in STATIC_SENSOR_DEFINITIONS:
            value = metadata.get(field_name)
            sensor_cls = PowerlineBooleanSensor if sensor_type == "bool" else PowerlineStaticSensor
            entities.append(
                sensor_cls(
                    coordinator,
                    mac=mac,
                    adapter_name=name,
                    sensor_name=sensor_name,
                    value=value,
                    field_name=field_name,
                    unique_id=f"powerline_{mac}_{field_name}",
                    icon=icon,
                    formatter=formatter,
                    metadata=metadata,
                    enabled_default=field_name in ENABLED_STATIC_DIAGNOSTICS,
                    available_without_value=False,
                )
            )

        return entities

    def _mesh_entities(conn_data: dict[str, Any]) -> list[SensorEntity]:
        source_mac = normalize_mac(conn_data.get("source"))
        target_mac = normalize_mac(conn_data.get("target"))
        source_name = adapter_name(data, source_mac)
        target_name = adapter_name(data, target_mac)
        entities: list[SensorEntity] = [
            PowerlineMeshSensor(
                coordinator,
                source_mac=source_mac,
                target_mac=target_mac,
                source_name=source_name,
                target_name=target_name,
                direction="tx",
                unique_id=f"powerline_{source_mac}_to_{target_mac}_tx",
            ),
            PowerlineMeshSensor(
                coordinator,
                source_mac=source_mac,
                target_mac=target_mac,
                source_name=source_name,
                target_name=target_name,
                direction="rx",
                unique_id=f"powerline_{source_mac}_from_{target_mac}_rx",
            ),
        ]
        for sensor_name, field_name, icon in MESH_DIAGNOSTIC_SENSOR_DEFINITIONS:
            entities.append(
                PowerlineMeshDiagnosticSensor(
                    coordinator,
                    source_mac=source_mac,
                    target_mac=target_mac,
                    source_name=source_name,
                    target_name=target_name,
                    sensor_name=sensor_name,
                    field_name=field_name,
                    unique_id=f"powerline_{source_mac}_to_{target_mac}_{field_name}",
                    icon=icon,
                    enabled_default=False,
                )
            )
        return entities

    @callback
    def _add_missing_entities() -> None:
        entities: list[SensorEntity] = []
        for mac in sorted(adapter_macs(data)):
            entities.extend(_adapter_entities(mac))
        for conn_data in coordinator.mesh_data.values():
            entities.extend(_mesh_entities(conn_data))

        desired_unique_ids = {
            entity.unique_id for entity in entities if entity.unique_id
        }
        added_unique_ids.intersection_update(desired_unique_ids)
        new_entities = [
            entity
            for entity in entities
            if entity.unique_id and entity.unique_id not in added_unique_ids
        ]
        if not new_entities:
            return
        added_unique_ids.update(entity.unique_id for entity in new_entities if entity.unique_id)
        _LOGGER.info("Adding %d Homeplug AV sensor entities", len(new_entities))
        async_add_entities(new_entities)

    _add_missing_entities()
    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            snapshot_signal(entry.entry_id),
            _add_missing_entities,
        )
    )


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
        field_name: str | None = None,
        formatter=None,
        metadata: dict | None = None,
        enabled_default: bool = True,
        available_without_value: bool = True,
    ) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self._adapter_name = adapter_name
        self._sensor_name = sensor_name
        self._value = value
        self._attr_unique_id = unique_id
        self._attr_icon = icon
        self._field_name = field_name
        self._formatter = formatter or _format_text
        self._metadata = metadata or {}
        self._attr_entity_registry_enabled_default = enabled_default
        self._available_without_value = available_without_value

    @property
    def name(self):
        """Return the name, including device name if renamed."""
        return f"{self._sensor_name}"

    @property
    def native_value(self):
        if self._field_name is not None:
            metadata = adapter_metadata(self.hass, self._mac) or self._metadata
            value = metadata.get(self._field_name)
            return self._formatter(self._value if value is None else value)
        return self._formatter(self._value)

    @property
    def device_info(self):
        metadata = adapter_metadata(self.hass, self._mac) or self._metadata
        return device_info(self._mac, self._adapter_name, metadata)

    @property
    def available(self) -> bool:
        """Return whether this static value is present."""
        if self._field_name is None or self._available_without_value:
            return True
        metadata = adapter_metadata(self.hass, self._mac) or self._metadata
        return metadata.get(self._field_name) is not None


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
            return None
            
        if self._direction == "tx":
            return conn.get("tx_rate")
        else:
            return conn.get("rx_rate")

    @property
    def available(self) -> bool:
        """Return whether the latest retained link data is usable."""
        key = f"{self._source_mac}_{self._target_mac}"
        conn = self.coordinator.mesh_data.get(key)
        return bool(conn and conn.get("available", False))

    @property
    def device_info(self):
        metadata = adapter_metadata(self.hass, self._source_mac)
        return device_info(self._source_mac, self._source_name, metadata)


class PowerlineMeshDiagnosticSensor(CoordinatorEntity, SensorEntity):
    """Diagnostic data for a mesh link."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator,
        *,
        source_mac: str,
        target_mac: str,
        source_name: str,
        target_name: str,
        sensor_name: str,
        field_name: str,
        unique_id: str,
        icon: str,
        enabled_default: bool = True,
    ) -> None:
        super().__init__(coordinator)
        self._source_mac = source_mac
        self._target_mac = target_mac
        self._source_name = source_name
        self._target_name = target_name
        self._sensor_name = sensor_name
        self._field_name = field_name
        self._attr_unique_id = unique_id
        self._attr_icon = icon
        self._attr_entity_registry_enabled_default = enabled_default

    @property
    def name(self):
        return f"{self._target_name} {self._sensor_name}"

    @property
    def native_value(self):
        conn = self.coordinator.mesh_data.get(f"{self._source_mac}_{self._target_mac}")
        if not conn:
            return None
        value = conn.get(self._field_name)
        return None if value is None else str(value)

    @property
    def available(self) -> bool:
        conn = self.coordinator.mesh_data.get(f"{self._source_mac}_{self._target_mac}")
        return bool(conn and conn.get("available", False) and conn.get(self._field_name) is not None)

    @property
    def device_info(self):
        metadata = adapter_metadata(self.hass, self._source_mac)
        return device_info(self._source_mac, self._source_name, metadata)


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
        if self._field_name is not None:
            metadata = adapter_metadata(self.hass, self._mac) or self._metadata
            value = metadata.get(self._field_name)
            return _format_bool(self._value if value is None else value)
        return _format_bool(self._value)


class PowerlineDiscoverListSensor(CoordinatorEntity, SensorEntity):
    """Sensor that gets its value from discover-list data."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(
        self,
        coordinator,
        *,
        entry_id: str,
        mac: str,
        adapter_name: str,
        sensor_name: str,
        field_name: str,
        unique_id: str,
        icon: str,
        metadata: dict | None = None,
        enabled_default: bool = True,
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._mac = mac
        self._adapter_name = adapter_name
        self._sensor_name = sensor_name
        self._field_name = field_name
        self._attr_unique_id = unique_id
        self._attr_icon = icon
        self._update_listener = None
        self._metadata = metadata or {}
        self._attr_entity_registry_enabled_default = enabled_default

    async def async_added_to_hass(self) -> None:
        """Register event listener when entity is added."""
        await super().async_added_to_hass()

        self._update_listener = async_dispatcher_connect(
            self.hass,
            snapshot_signal(self._entry_id),
            self._handle_snapshot_update,
        )

    async def async_will_remove_from_hass(self) -> None:
        """Remove event listener when entity is removed."""
        await super().async_will_remove_from_hass()
        if self._update_listener:
            self._update_listener()

    @callback
    def _handle_snapshot_update(self) -> None:
        """Handle runtime snapshot updates."""

        self.async_write_ha_state()

    def _find_station(self, *, require_field: bool = False) -> dict[str, Any] | None:
        """Find this adapter in the current discover-list snapshot."""

        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        candidates: list[dict[str, Any]] = []
        for disc_data in data.get("discover_list_data", {}).values():
            for station in disc_data.get("stations", []):
                if normalize_mac(station.get("mac")) == self._mac:
                    candidates.append(station)
                    if not require_field:
                        return station
        if require_field:
            for station in candidates:
                if station.get(self._field_name) is not None:
                    return station
        return candidates[0] if candidates else None

    @property
    def name(self):
        """Return the name."""
        return f"{self._sensor_name}"

    @property
    def native_value(self):
        """Get value from discover-list data."""
        station = self._find_station(require_field=True)
        if station is not None:
            value = station.get(self._field_name)
            if value is not None:
                return str(value)
        
        return "Unknown"

    @property
    def available(self) -> bool:
        """Return whether discover-list data currently contains this adapter."""
        return self._find_station(require_field=True) is not None

    @property
    def device_info(self):
        metadata = adapter_metadata(self.hass, self._mac) or self._metadata
        return device_info(self._mac, self._adapter_name, metadata)


class PowerlineDiscoverListBooleanSensor(PowerlineDiscoverListSensor):
    """Boolean sensor from discover-list data."""
    
    @property
    def native_value(self):
        """Get boolean value and convert to Yes/No."""
        station = self._find_station(require_field=True)
        if station is not None:
            value = station.get(self._field_name, False)
            return "Yes" if value else "No"
        
        return "No"


class PowerlineDiscoverListSignalSensor(PowerlineDiscoverListSensor):
    """Signal level sensor with special formatting."""
    
    @property
    def native_value(self):
        """Get signal level and format it."""
        station = self._find_station(require_field=True)
        if station is not None:
            signal_level = station.get(self._field_name, 0)
            return _format_signal_level(signal_level)
        
        return "Unknown"

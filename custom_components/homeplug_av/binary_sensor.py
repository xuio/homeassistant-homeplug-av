"""Binary sensor platform for Homeplug AV (online status)."""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.core import callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN
from .helpers import (
    adapter_macs,
    adapter_metadata,
    adapter_name,
    device_info,
    snapshot_signal,
)

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    """Set up binary sensors for a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    online_macs: set[str] = data.get("online_macs", set())
    added_unique_ids: set[str] = set()
    entities_by_unique_id: dict[str, PowerlineOnlineSensor] = {}

    @callback
    def _add_missing_entities() -> None:
        entities: list[BinarySensorEntity] = []
        desired_unique_ids: set[str] = set()
        for mac in sorted(adapter_macs(data) | set(coordinator.data or {})):
            unique_id = f"powerline_{mac}_online"
            desired_unique_ids.add(unique_id)
            if unique_id in added_unique_ids:
                continue
            added_unique_ids.add(unique_id)
            entity = PowerlineOnlineSensor(
                coordinator,
                online_macs,
                mac=mac,
                adapter_name=adapter_name(data, mac),
            )
            entities_by_unique_id[unique_id] = entity
            entities.append(entity)
        added_unique_ids.intersection_update(desired_unique_ids)
        for unique_id in list(entities_by_unique_id):
            if unique_id not in desired_unique_ids:
                entities_by_unique_id.pop(unique_id, None)
        if entities:
            async_add_entities(entities)
        new_unique_ids = {new_entity.unique_id for new_entity in entities}
        for unique_id, entity in entities_by_unique_id.items():
            if unique_id not in new_unique_ids and getattr(entity, "_hass", None) is not None:
                entity.async_write_ha_state()

    _add_missing_entities()
    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            snapshot_signal(entry.entry_id),
            _add_missing_entities,
        )
    )


class PowerlineOnlineSensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor representing adapter online/offline state."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_icon = "mdi:lan"

    def __init__(self, coordinator, online_macs: set[str], *, mac: str, adapter_name: str) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self._adapter_name = adapter_name
        self._attr_unique_id = f"powerline_{mac}_online"
        self._online_macs = online_macs
        # Determine initial state based on discover data
        self._last_state = mac.lower() in online_macs

    @property
    def name(self):
        """Return the name."""
        return "Online"

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
        metadata = adapter_metadata(self.hass, self._mac)
        return device_info(self._mac, self._adapter_name, metadata)

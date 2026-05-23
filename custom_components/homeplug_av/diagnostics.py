"""Diagnostics support for Homeplug AV."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .helpers import normalize_mac

TO_REDACT = {
    "dak",
    "device_access_key",
    "key",
    "network_key",
    "nmk",
    "password",
}


def _json_safe(value: Any) -> Any:
    """Return a recursively JSON-serializable representation."""

    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, set):
        return sorted(_json_safe(item) for item in value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def build_diagnostics_payload(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    adapter_mac: str | None = None,
    redact: bool = True,
) -> dict[str, Any]:
    """Build a diagnostics payload for a config entry."""

    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    coordinator = runtime.get("coordinator")
    normalized_mac = normalize_mac(adapter_mac)

    adapter_metadata = dict(runtime.get("adapter_metadata", {}))
    adapters = list(runtime.get("adapters", []))
    discover_list_data = dict(runtime.get("discover_list_data", {}))
    mesh_data = dict(getattr(coordinator, "mesh_data", {}) or {})
    coordinator_data = dict(getattr(coordinator, "data", {}) or {})

    if normalized_mac:
        adapter_metadata = {
            key: value
            for key, value in adapter_metadata.items()
            if normalize_mac(key) == normalized_mac
        }
        adapters = [
            adapter
            for adapter in adapters
            if normalize_mac(adapter.get("mac")) == normalized_mac
        ]
        discover_list_data = {
            key: value
            for key, value in discover_list_data.items()
            if normalize_mac(key) == normalized_mac
            or any(
                normalize_mac(station.get("mac")) == normalized_mac
                for station in value.get("stations", [])
            )
        }
        mesh_data = {
            key: value
            for key, value in mesh_data.items()
            if normalize_mac(value.get("source")) == normalized_mac
            or normalize_mac(value.get("target")) == normalized_mac
        }
        coordinator_data = {
            key: value
            for key, value in coordinator_data.items()
            if normalize_mac(key) == normalized_mac
        }

    payload = {
        "entry": {
            "entry_id": entry.entry_id,
            "title": entry.title,
            "version": entry.version,
            "minor_version": getattr(entry, "minor_version", None),
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "runtime": {
            "online_macs": sorted(runtime.get("online_macs", set())),
            "adapters": adapters,
            "adapter_metadata": adapter_metadata,
            "discover_list_data": discover_list_data,
            "mesh_data": mesh_data,
            "coordinator_data": coordinator_data,
        },
    }

    safe_payload = _json_safe(payload)
    if not redact:
        return safe_payload
    return async_redact_data(safe_payload, TO_REDACT)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""

    return build_diagnostics_payload(hass, entry)

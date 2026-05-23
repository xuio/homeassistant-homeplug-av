"""Shared helpers for Homeplug AV entities."""

from __future__ import annotations

from typing import Any

from .const import DOMAIN


def snapshot_signal(entry_id: str) -> str:
    """Return the dispatcher signal for runtime snapshot changes."""

    return f"{DOMAIN}_{entry_id}_snapshot_updated"


def normalize_mac(mac: str | None) -> str:
    """Normalize a MAC address for use in identifiers."""

    return (mac or "").lower()


def adapter_metadata(hass: Any, mac: str) -> dict[str, Any]:
    """Return retained adapter metadata for a MAC address."""

    mac = normalize_mac(mac)
    if hass is None:
        return {}
    for data in hass.data.get(DOMAIN, {}).values():
        metadata = data.get("adapter_metadata", {})
        if mac in metadata:
            return metadata[mac]
        for adapter in data.get("adapters", []):
            if normalize_mac(adapter.get("mac")) == mac:
                return adapter
    return {}


def adapter_macs(data: dict[str, Any]) -> set[str]:
    """Return retained adapter MACs in the current snapshot."""

    macs = set(data.get("adapter_metadata", {}))
    macs.update(normalize_mac(adapter.get("mac")) for adapter in data.get("adapters", []))
    macs.discard("")
    return macs


def adapter_name(data: dict[str, Any], mac: str) -> str:
    """Return the stable display name for an adapter MAC."""

    mac = normalize_mac(mac)
    index = data.get("index_map", {}).get(mac)
    if index is None:
        return f"Adapter {mac[-5:]}" if mac else "Adapter"
    return f"Adapter {index}"


def device_info(mac: str, fallback_name: str, metadata: dict[str, Any]) -> dict[str, Any]:
    """Return Home Assistant device info for a powerline adapter."""

    manufacturer = metadata.get("manufacturer") or metadata.get("vendor") or "Unknown"
    model = metadata.get("chipset") or "Powerline Adapter"
    return {
        "identifiers": {(DOMAIN, normalize_mac(mac))},
        "name": fallback_name,
        "model": model,
        "manufacturer": manufacturer,
    }


def ensure_index_map(hass: Any, entry: Any, data: dict[str, Any], macs: set[str]) -> None:
    """Assign stable adapter indexes and persist them in config entry options."""

    index_map = {
        normalize_mac(mac): int(index)
        for mac, index in data.get("index_map", {}).items()
        if normalize_mac(mac)
    }
    next_index = max(index_map.values(), default=0) + 1

    changed = index_map != data.get("index_map", {})
    for mac in sorted(normalize_mac(mac) for mac in macs):
        if not mac:
            continue
        if mac not in index_map:
            index_map[mac] = next_index
            next_index += 1
            changed = True

    data["index_map"] = index_map
    if changed and entry is not None:
        options = dict(entry.options)
        options["index_map"] = dict(index_map)
        hass.config_entries.async_update_entry(entry, options=options)

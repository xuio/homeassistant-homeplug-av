"""Config flow for Powerline Stats integration."""

from __future__ import annotations

import logging
from typing import Any, List, Tuple

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback

from .const import DOMAIN

import socket

try:
    import psutil  # type: ignore
except ImportError:  # pragma: no cover - psutil is part of HA deps
    psutil = None  # type: ignore

from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectOptionDict,
    SelectSelectorMode,
)

_LOGGER = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _available_interfaces() -> List[Tuple[str, str | None]]:
    """Return a list of tuples (iface_name, ipv4 or None)."""
    result: List[Tuple[str, str | None]] = []
    if psutil is None:
        return result

    for name, addrs in psutil.net_if_addrs().items():
        ipv4 = None
        for addr in addrs:
            if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                ipv4 = addr.address
                break
        result.append((name, ipv4))
    return result


def _build_interface_selector(default: str | None = None):
    """Return a selector for interfaces."""

    options: List[SelectOptionDict] = []
    for name, ip in _available_interfaces():
        label = f"{name} ({ip})" if ip else name
        options.append(SelectOptionDict(value=name, label=label))

    # Fallback: allow manual entry if we couldn't detect interfaces
    if not options:
        return str  # plain text input

    return SelectSelector(
        SelectSelectorConfig(
            options=options,
            mode=SelectSelectorMode.DROPDOWN,
        )
    )


class PowerlineStatsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Powerline Stats."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step."""

        errors: dict[str, str] = {}

        # Build schema dynamically each call (to refresh if errors)
        interface_selector = _build_interface_selector()
        data_schema = vol.Schema(
            {
                vol.Required("interface"): interface_selector,
                vol.Optional("scan_interval", default=30): vol.All(
                    int, vol.Range(min=5)
                ),
                vol.Optional("timeout", default=1.0): vol.All(float, vol.Range(min=0.1, max=5.0)),
            }
        )

        if user_input is not None:
            await self.async_set_unique_id(user_input["interface"])
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=f"Powerline ({user_input['interface']})",
                data=user_input,
            )

        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )

    @callback
    def async_get_options_flow(self, config_entry):
        """Return the options flow handler."""
        return PowerlineStatsOptionsFlowHandler(config_entry)


class PowerlineStatsOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options for a config entry."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Manage the options for the custom component."""

        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options_schema = vol.Schema(
            {
                vol.Optional(
                    "scan_interval",
                    default=self.config_entry.data.get("scan_interval", 30),
                ): vol.All(int, vol.Range(min=5)),
                vol.Optional(
                    "timeout",
                    default=self.config_entry.data.get("timeout", 1.0),
                ): vol.All(float, vol.Range(min=0.1, max=5.0)),
            }
        )

        return self.async_show_form(step_id="init", data_schema=options_schema)

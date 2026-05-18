"""Config Flow for the Jeedom Bridge integration."""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import JeedomApiClient, JeedomAuthError, JeedomConnectionError
from .const import (
    CONF_API_KEY,
    CONF_JEEDOM_URL,
    CONF_PLUGIN_KEY_EDISIO,
    CONF_PLUGIN_KEY_ZWAVE,
    CONF_PLUGIN_KEY_VIRTUEL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# ── Step 1: core connection ───────────────────────────────────────────────────

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_JEEDOM_URL, description={"suggested_value": "http://192.168.1.50"}): str,
        vol.Required(CONF_API_KEY): str,
    }
)

# ── Step 2: plugin API keys (all optional) ────────────────────────────────────

STEP_PLUGINS_DATA_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_PLUGIN_KEY_EDISIO,  default=""): str,
        vol.Optional(CONF_PLUGIN_KEY_ZWAVE,   default=""): str,
        vol.Optional(CONF_PLUGIN_KEY_VIRTUEL, default=""): str,
    }
)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _validate_connection(hass: HomeAssistant, data: dict[str, Any]) -> str:
    """
    Try to connect to Jeedom with the global API key.
    Returns a suggested entry title.
    :raises JeedomConnectionError / JeedomAuthError on failure.
    """
    session = async_get_clientsession(hass)
    client = JeedomApiClient(
        session=session,
        base_url=data[CONF_JEEDOM_URL],
        api_key=data[CONF_API_KEY],
    )
    await client.async_test_connection()
    parsed = urlparse(data[CONF_JEEDOM_URL])
    return parsed.hostname or data[CONF_JEEDOM_URL]


# ── Config Flow ───────────────────────────────────────────────────────────────

class JeedomBridgeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the user-facing configuration flow for Jeedom Bridge."""

    VERSION = 1

    def __init__(self) -> None:
        self._core_data: dict[str, Any] = {}
        self._entry_title: str = ""

    # ── Step 1: URL + global API key ──────────────────────────────────────────

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show the initial form and validate core credentials."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                self._entry_title = await _validate_connection(self.hass, user_input)
            except JeedomConnectionError:
                errors["base"] = "cannot_connect"
                _LOGGER.warning("Cannot connect to Jeedom at %s", user_input.get(CONF_JEEDOM_URL))
            except JeedomAuthError:
                errors["base"] = "invalid_auth"
                _LOGGER.warning("Invalid Jeedom API key supplied during config flow.")
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during Jeedom config flow validation")
                errors["base"] = "unknown"
            else:
                # Prevent duplicate entries for the same Jeedom URL
                await self.async_set_unique_id(user_input[CONF_JEEDOM_URL].lower())
                self._abort_if_unique_id_configured()
                self._core_data = user_input
                # Proceed to plugin key step
                return await self.async_step_plugins()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    # ── Step 2: optional plugin API keys ─────────────────────────────────────

    async def async_step_plugins(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Collect optional plugin-specific API keys."""
        if user_input is not None:
            # Merge core + plugin data into a single config entry
            entry_data = {**self._core_data, **user_input}
            return self.async_create_entry(
                title=f"Jeedom ({self._entry_title})",
                data=entry_data,
            )

        return self.async_show_form(
            step_id="plugins",
            data_schema=STEP_PLUGINS_DATA_SCHEMA,
            errors={},
        )

    # ── Options Flow (update plugin keys after setup) ─────────────────────────

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        """Return the options flow handler."""
        return JeedomBridgeOptionsFlow(config_entry)


# ── Options Flow ──────────────────────────────────────────────────────────────

class JeedomBridgeOptionsFlow(config_entries.OptionsFlow):
    """Allow updating plugin API keys without re-creating the entry."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show the options form pre-filled with current values."""
        current = self._entry.data  # plugin keys are stored in data, not options

        if user_input is not None:
            # Merge updated plugin keys back into the config entry data
            updated_data = {**self._entry.data, **user_input}
            self.hass.config_entries.async_update_entry(
                self._entry, data=updated_data
            )
            return self.async_create_entry(title="", data={})

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_PLUGIN_KEY_EDISIO,
                    default=current.get(CONF_PLUGIN_KEY_EDISIO, ""),
                ): str,
                vol.Optional(
                    CONF_PLUGIN_KEY_ZWAVE,
                    default=current.get(CONF_PLUGIN_KEY_ZWAVE, ""),
                ): str,
                vol.Optional(
                    CONF_PLUGIN_KEY_VIRTUEL,
                    default=current.get(CONF_PLUGIN_KEY_VIRTUEL, ""),
                ): str,
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)

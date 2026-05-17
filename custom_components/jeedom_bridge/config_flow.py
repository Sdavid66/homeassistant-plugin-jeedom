"""Config Flow for the Jeedom Bridge integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import JeedomApiClient, JeedomAuthError, JeedomConnectionError
from .const import CONF_API_KEY, CONF_JEEDOM_URL, DOMAIN

_LOGGER = logging.getLogger(__name__)

# ── Validation schema ─────────────────────────────────────────────────────────

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_JEEDOM_URL, description={"suggested_value": "http://192.168.1.50"}): str,
        vol.Required(CONF_API_KEY): str,
    }
)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """
    Try to connect to Jeedom and return a title for the config entry.

    :raises JeedomConnectionError: Cannot reach Jeedom.
    :raises JeedomAuthError: API key rejected.
    """
    session = async_get_clientsession(hass)
    client = JeedomApiClient(
        session=session,
        base_url=data[CONF_JEEDOM_URL],
        api_key=data[CONF_API_KEY],
    )
    await client.async_test_connection()

    # Use the URL host part as the entry title
    from urllib.parse import urlparse
    parsed = urlparse(data[CONF_JEEDOM_URL])
    title = parsed.hostname or data[CONF_JEEDOM_URL]
    return {"title": f"Jeedom ({title})"}


# ── Config Flow class ─────────────────────────────────────────────────────────

class JeedomBridgeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the user-facing configuration flow for Jeedom Bridge."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show the initial form and validate user input."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await _validate_input(self.hass, user_input)
            except JeedomConnectionError:
                errors["base"] = "cannot_connect"
                _LOGGER.warning(
                    "Cannot connect to Jeedom at %s", user_input.get(CONF_JEEDOM_URL)
                )
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

                return self.async_create_entry(
                    title=info["title"],
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

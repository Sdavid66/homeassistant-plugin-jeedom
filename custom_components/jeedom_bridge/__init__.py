"""Jeedom Bridge – Home Assistant integration entry point."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import JeedomApiClient, JeedomAuthError, JeedomConnectionError
from .const import CONF_API_KEY, CONF_JEEDOM_URL, DATA_API, DATA_COORDINATOR, DOMAIN, PLATFORMS
from .coordinator import JeedomCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Set up Jeedom Bridge from a config entry.

    1. Build the API client.
    2. Instantiate and perform the first refresh of the coordinator.
    3. Forward setup to each platform (light, switch).
    """
    jeedom_url: str = entry.data[CONF_JEEDOM_URL]
    api_key: str = entry.data[CONF_API_KEY]

    session = async_get_clientsession(hass)
    client = JeedomApiClient(session=session, base_url=jeedom_url, api_key=api_key)

    # Quick connectivity test before setting up platforms
    try:
        await client.async_test_connection()
    except JeedomConnectionError as err:
        raise ConfigEntryNotReady(
            f"Cannot connect to Jeedom at {jeedom_url}: {err}"
        ) from err
    except JeedomAuthError as err:
        _LOGGER.error("Invalid Jeedom API key for %s: %s", jeedom_url, err)
        return False

    coordinator = JeedomCoordinator(hass, client)

    # Perform first data refresh; raises ConfigEntryNotReady on failure
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:
        raise ConfigEntryNotReady(f"Initial Jeedom data fetch failed: {err}") from err

    # Store shared objects in hass.data
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        DATA_API: client,
        DATA_COORDINATOR: coordinator,
    }

    # Forward to all declared platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.info(
        "Jeedom Bridge set up successfully for %s — %d devices loaded",
        jeedom_url,
        len(coordinator.data or {}),
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and release resources."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        _LOGGER.info("Jeedom Bridge entry %s unloaded.", entry.entry_id)
    return unload_ok

"""Jeedom Bridge – Home Assistant integration entry point."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import JeedomApiClient, JeedomAuthError, JeedomConnectionError, build_plugin_clients
from .const import (
    CONF_API_KEY,
    CONF_JEEDOM_URL,
    DATA_API,
    DATA_COORDINATOR,
    DATA_PLUGIN_CLIENTS,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import JeedomCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Set up Jeedom Bridge from a config entry.

    1. Build the global API client.
    2. Build optional plugin API clients (edisio, zwave, virtuel).
    3. Instantiate and perform the first refresh of the coordinator.
    4. Forward setup to each platform (light, switch).
    """
    jeedom_url: str = entry.data[CONF_JEEDOM_URL]
    api_key: str = entry.data[CONF_API_KEY]

    session = async_get_clientsession(hass)

    # ── Global Jeedom client ──────────────────────────────────────────────────
    client = JeedomApiClient(session=session, base_url=jeedom_url, api_key=api_key)

    try:
        await client.async_test_connection()
    except JeedomConnectionError as err:
        raise ConfigEntryNotReady(
            f"Cannot connect to Jeedom at {jeedom_url}: {err}"
        ) from err
    except JeedomAuthError as err:
        _LOGGER.error("Invalid Jeedom API key for %s: %s", jeedom_url, err)
        return False

    # ── Plugin clients (optional) ─────────────────────────────────────────────
    plugin_clients = build_plugin_clients(
        session=session,
        base_url=jeedom_url,
        entry_data=entry.data,
    )

    # Test each plugin connection (non-blocking: a failure logs a warning only)
    for plugin_id, plugin_client in plugin_clients.items():
        ok = await plugin_client.async_test_plugin_connection()
        if ok:
            _LOGGER.info("Plugin '%s' API: connection OK", plugin_id)
        else:
            _LOGGER.warning(
                "Plugin '%s' API: connection failed — commands for this plugin may not work.",
                plugin_id,
            )

    # ── Coordinator ───────────────────────────────────────────────────────────
    coordinator = JeedomCoordinator(hass, client, plugin_clients)

    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:
        raise ConfigEntryNotReady(f"Initial Jeedom data fetch failed: {err}") from err

    # ── Store shared objects in hass.data ─────────────────────────────────────
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        DATA_API: client,
        DATA_COORDINATOR: coordinator,
        DATA_PLUGIN_CLIENTS: plugin_clients,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.info(
        "Jeedom Bridge set up for %s — %d devices loaded, %d plugin(s) configured",
        jeedom_url,
        len(coordinator.data or {}),
        len(plugin_clients),
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and release resources."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        _LOGGER.info("Jeedom Bridge entry %s unloaded.", entry.entry_id)
    return unload_ok

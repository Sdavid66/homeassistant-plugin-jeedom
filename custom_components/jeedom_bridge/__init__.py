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
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
)
from .coordinator import JeedomCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Set up Jeedom Bridge from a config entry.

    Design philosophy: be as non-intrusive as possible.
    - A network failure → ConfigEntryNotReady (HA retries quietly, no crash).
    - An invalid API key → log error, return False (entry stays disabled, no crash).
    - Plugin failures → warning only, never block setup.
    - First data refresh failure → warn and continue with empty data rather than
      blocking HA startup.
    """
    jeedom_url: str = entry.data[CONF_JEEDOM_URL]
    api_key: str = entry.data[CONF_API_KEY]

    session = async_get_clientsession(hass)

    # ── Global Jeedom client ──────────────────────────────────────────────────
    client = JeedomApiClient(session=session, base_url=jeedom_url, api_key=api_key)

    try:
        await client.async_test_connection()
    except JeedomConnectionError as err:
        # Jeedom unreachable: ask HA to retry later (ConfigEntryNotReady is silent)
        raise ConfigEntryNotReady(
            f"Cannot connect to Jeedom at {jeedom_url}: {err}"
        ) from err
    except JeedomAuthError as err:
        _LOGGER.error("Invalid Jeedom API key for %s: %s", jeedom_url, err)
        return False
    except Exception as err:  # noqa: BLE001
        _LOGGER.exception("Unexpected error testing Jeedom connection: %s", err)
        raise ConfigEntryNotReady(f"Unexpected connection error: {err}") from err

    # ── Plugin clients (optional, fully non-blocking) ─────────────────────────
    plugin_clients: dict = {}
    try:
        plugin_clients = build_plugin_clients(
            session=session,
            base_url=jeedom_url,
            entry_data=entry.data,
        )
    except Exception:  # noqa: BLE001
        _LOGGER.warning("Failed to build plugin clients — continuing without plugins", exc_info=True)

    for plugin_id, plugin_client in plugin_clients.items():
        try:
            ok = await plugin_client.async_test_plugin_connection()
            if ok:
                _LOGGER.info("Plugin '%s' API: connection OK", plugin_id)
            else:
                _LOGGER.warning(
                    "Plugin '%s' API: connection failed — plugin data will be unavailable.",
                    plugin_id,
                )
        except Exception:  # noqa: BLE001
            _LOGGER.warning(
                "Plugin '%s' API: unexpected error during connection test — skipped",
                plugin_id, exc_info=True,
            )

    # ── Coordinator ───────────────────────────────────────────────────────────
    scan_interval = entry.options.get(
        CONF_SCAN_INTERVAL,
        entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    )
    coordinator = JeedomCoordinator(hass, client, plugin_clients, scan_interval)

    # Use async_refresh() instead of async_config_entry_first_refresh() so that
    # a failure on first load logs a warning but does NOT raise ConfigEntryNotReady
    # and does NOT cause HA to restart the setup loop aggressively.
    try:
        await coordinator.async_refresh()
    except Exception:  # noqa: BLE001
        # Non-fatal: the coordinator will retry on the next update_interval tick.
        _LOGGER.warning(
            "Initial Jeedom data refresh failed — will retry in %s s. "
            "Entities will appear once data is available.",
            coordinator.update_interval.total_seconds() if coordinator.update_interval else "N/A",
            exc_info=True,
        )

    # ── Store shared objects in hass.data ─────────────────────────────────────
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        DATA_API: client,
        DATA_COORDINATOR: coordinator,
        DATA_PLUGIN_CLIENTS: plugin_clients,
    }

    # Forward platform setup (light, switch) — always, even if data is empty
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.info(
        "Jeedom Bridge ready for %s — %d device(s) loaded, %d plugin(s) configured",
        jeedom_url,
        len(coordinator.data or {}),
        len(plugin_clients),
    )
    
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and release all resources gracefully."""
    try:
        unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    except Exception:  # noqa: BLE001
        _LOGGER.warning("Error while unloading Jeedom Bridge platforms", exc_info=True)
        unload_ok = False

    # Always clean up hass.data, even if platform unload partially failed
    try:
        domain_data = hass.data.get(DOMAIN, {})
        domain_data.pop(entry.entry_id, None)
        if not domain_data:
            hass.data.pop(DOMAIN, None)
    except Exception:  # noqa: BLE001
        _LOGGER.warning("Error cleaning up Jeedom Bridge hass.data", exc_info=True)

    if unload_ok:
        _LOGGER.info("Jeedom Bridge entry %s unloaded cleanly.", entry.entry_id)
    else:
        _LOGGER.warning("Jeedom Bridge entry %s unloaded with errors (check logs).", entry.entry_id)

    return unload_ok

async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)

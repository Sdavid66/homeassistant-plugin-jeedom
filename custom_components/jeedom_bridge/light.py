"""Light platform for Jeedom Bridge."""
from __future__ import annotations

import logging
import math
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import JeedomApiClient, JeedomApiError, JeedomConnectionError
from .const import DATA_API, DATA_COORDINATOR, DOMAIN
from .coordinator import JeedomCoordinator, JeedomDevice

_LOGGER = logging.getLogger(__name__)

# Jeedom slider range: 0–100  →  HA brightness range: 0–255
_JEEDOM_MAX = 100
_HA_MAX = 255


def _ha_brightness_to_jeedom(brightness: int) -> int:
    """Convert HA brightness (0-255) to Jeedom slider value (0-100)."""
    return round(brightness / _HA_MAX * _JEEDOM_MAX)


def _jeedom_brightness_to_ha(value: float | int | str) -> int:
    """Convert Jeedom slider value (0-100) to HA brightness (0-255)."""
    try:
        return math.floor(float(value) / _JEEDOM_MAX * _HA_MAX)
    except (ValueError, TypeError):
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# Platform setup
# ─────────────────────────────────────────────────────────────────────────────

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Jeedom light entities from a config entry."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: JeedomCoordinator = entry_data[DATA_COORDINATOR]
    client: JeedomApiClient = entry_data[DATA_API]

    entities = [
        JeedomLightEntity(coordinator, client, device)
        for device in coordinator.data.values()
        if device.is_light
    ]

    _LOGGER.debug("Adding %d Jeedom light entities", len(entities))
    async_add_entities(entities)


# ─────────────────────────────────────────────────────────────────────────────
# Entity class
# ─────────────────────────────────────────────────────────────────────────────

class JeedomLightEntity(CoordinatorEntity[JeedomCoordinator], LightEntity):
    """
    Représente une lumière Jeedom dans Home Assistant.

    Hérite de :class:`CoordinatorEntity` pour recevoir les mises à jour
    automatiques du coordinateur, et de :class:`LightEntity` pour exposer
    toutes les propriétés lumière standard (compatibilité Alexa).
    """

    _attr_has_entity_name = True
    _attr_name = None  # The device name IS the entity name

    def __init__(
        self,
        coordinator: JeedomCoordinator,
        client: JeedomApiClient,
        device: JeedomDevice,
    ) -> None:
        super().__init__(coordinator)
        self._client = client
        self._device = device
        self._attr_unique_id = f"jeedom_{device.eq_id}"

        # Color mode
        if device.supports_brightness:
            self._attr_color_mode = ColorMode.BRIGHTNESS
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        else:
            self._attr_color_mode = ColorMode.ONOFF
            self._attr_supported_color_modes = {ColorMode.ONOFF}

        # Device info — groups all entities of the same eqLogic under one device card
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.eq_id)},
            name=device.name,
            manufacturer="Jeedom",
            model=device.plugin_id,
        )

    # ── Coordinator data helpers ──────────────────────────────────────────────

    @property
    def _current_device(self) -> JeedomDevice | None:
        """Return the up-to-date device snapshot from coordinator data."""
        if self.coordinator.data:
            return self.coordinator.data.get(self._device.eq_id)
        return None

    # ── LightEntity properties ────────────────────────────────────────────────

    @property
    def name(self) -> str:
        """Return device name (used also as entity name since _attr_name is None)."""
        dev = self._current_device
        return dev.name if dev else self._device.name

    @property
    def is_on(self) -> bool:
        """Return True when the light is on."""
        dev = self._current_device
        if dev is None:
            return False
        try:
            return float(dev.current_state) > 0
        except (ValueError, TypeError):
            return str(dev.current_state).lower() in ("1", "on", "true", "allumé")

    @property
    def brightness(self) -> int | None:
        """Return current brightness (0-255) if the light supports dimming."""
        dev = self._current_device
        if dev is None or not dev.supports_brightness:
            return None
        return _jeedom_brightness_to_ha(dev.current_state)

    @property
    def available(self) -> bool:
        """Mirror coordinator availability."""
        return self.coordinator.last_update_success and self._current_device is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose Jeedom metadata as entity attributes (visible in HA dev tools)."""
        dev = self._current_device or self._device
        return {
            "jeedom_name": dev.name,
            "jeedom_id": dev.eq_id,
            "plugin": dev.plugin_id,
            "category": dev.category,
            "cmd_on_id": dev.cmd_on_id,
            "cmd_off_id": dev.cmd_off_id,
            "cmd_state_id": dev.cmd_state_id,
        }

    # ── Control methods ───────────────────────────────────────────────────────

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the light, optionally setting brightness."""
        dev = self._current_device or self._device

        # Handle dimming
        ha_brightness: int | None = kwargs.get(ATTR_BRIGHTNESS)
        if ha_brightness is not None and dev.cmd_slider_id is not None:
            jeedom_val = _ha_brightness_to_jeedom(ha_brightness)
            _LOGGER.debug(
                "Setting brightness of %s to %d%% (cmd_id=%s)",
                dev.name, jeedom_val, dev.cmd_slider_id,
            )
            await self._call_cmd(dev.cmd_slider_id, {"slider": jeedom_val})
            return

        # Simple ON
        if dev.cmd_on_id is None:
            raise HomeAssistantError(
                f"No 'on' command found for Jeedom device {dev.name} (id={dev.eq_id})"
            )
        _LOGGER.debug("Turning on %s (cmd_id=%s)", dev.name, dev.cmd_on_id)
        await self._call_cmd(dev.cmd_on_id)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the light."""
        dev = self._current_device or self._device
        if dev.cmd_off_id is None:
            raise HomeAssistantError(
                f"No 'off' command found for Jeedom device {dev.name} (id={dev.eq_id})"
            )
        _LOGGER.debug("Turning off %s (cmd_id=%s)", dev.name, dev.cmd_off_id)
        await self._call_cmd(dev.cmd_off_id)

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _call_cmd(
        self,
        cmd_id: str,
        options: dict[str, Any] | None = None,
    ) -> None:
        """
        Execute a Jeedom command, translating API errors to HomeAssistantError.
        Triggers an immediate coordinator refresh afterwards.
        """
        try:
            if options:
                await self._client.async_execute_cmd_with_options(cmd_id, options)
            else:
                await self._client.async_execute_cmd(cmd_id)
        except JeedomConnectionError as err:
            raise HomeAssistantError(
                f"Cannot reach Jeedom while executing cmd {cmd_id}: {err}"
            ) from err
        except JeedomApiError as err:
            raise HomeAssistantError(
                f"Jeedom API error while executing cmd {cmd_id}: {err}"
            ) from err

        # Request an immediate refresh so the UI reflects the new state quickly
        await self.coordinator.async_request_refresh()

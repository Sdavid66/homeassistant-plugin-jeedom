"""Switch platform for Jeedom Bridge."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
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


# ─────────────────────────────────────────────────────────────────────────────
# Platform setup
# ─────────────────────────────────────────────────────────────────────────────

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Jeedom switch entities from a config entry."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: JeedomCoordinator = entry_data[DATA_COORDINATOR]
    client: JeedomApiClient = entry_data[DATA_API]

    entities = [
        JeedomSwitchEntity(coordinator, client, device)
        for device in coordinator.data.values()
        if device.is_switch
    ]

    _LOGGER.debug("Adding %d Jeedom switch entities", len(entities))
    async_add_entities(entities)


# ─────────────────────────────────────────────────────────────────────────────
# Entity class
# ─────────────────────────────────────────────────────────────────────────────

class JeedomSwitchEntity(CoordinatorEntity[JeedomCoordinator], SwitchEntity):
    """
    Représente un interrupteur Jeedom (on/off sans variateur).

    Exposé à l'intégration Alexa via :attr:`unique_id` et :class:`DeviceInfo`.
    """

    _attr_has_entity_name = True
    _attr_name = None

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
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.eq_id)},
            name=device.name,
            manufacturer="Jeedom",
            model=device.plugin_id,
        )

    # ── Coordinator data helpers ──────────────────────────────────────────────

    @property
    def _current_device(self) -> JeedomDevice | None:
        if self.coordinator.data:
            return self.coordinator.data.get(self._device.eq_id)
        return None

    # ── SwitchEntity properties ───────────────────────────────────────────────

    @property
    def name(self) -> str:
        dev = self._current_device
        return dev.name if dev else self._device.name

    @property
    def is_on(self) -> bool:
        dev = self._current_device
        if dev is None:
            return False
        try:
            return float(dev.current_state) > 0
        except (ValueError, TypeError):
            return str(dev.current_state).lower() in ("1", "on", "true")

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and self._current_device is not None

    # ── Control ───────────────────────────────────────────────────────────────

    async def async_turn_on(self, **kwargs: Any) -> None:
        dev = self._current_device or self._device
        if dev.cmd_on_id is None:
            raise HomeAssistantError(
                f"No 'on' command for Jeedom switch {dev.name} (id={dev.eq_id})"
            )
        _LOGGER.debug("Turning on switch %s (cmd_id=%s)", dev.name, dev.cmd_on_id)
        await self._call_cmd(dev.cmd_on_id)

    async def async_turn_off(self, **kwargs: Any) -> None:
        dev = self._current_device or self._device
        if dev.cmd_off_id is None:
            raise HomeAssistantError(
                f"No 'off' command for Jeedom switch {dev.name} (id={dev.eq_id})"
            )
        _LOGGER.debug("Turning off switch %s (cmd_id=%s)", dev.name, dev.cmd_off_id)
        await self._call_cmd(dev.cmd_off_id)

    async def _call_cmd(self, cmd_id: str) -> None:
        try:
            await self._client.async_execute_cmd(cmd_id)
        except JeedomConnectionError as err:
            raise HomeAssistantError(
                f"Cannot reach Jeedom while executing cmd {cmd_id}: {err}"
            ) from err
        except JeedomApiError as err:
            raise HomeAssistantError(
                f"Jeedom API error while executing cmd {cmd_id}: {err}"
            ) from err
        await self.coordinator.async_request_refresh()

"""Cover platform for Jeedom Bridge (volets, stores, barrières)."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.cover import (
    CoverEntity,
    CoverEntityFeature,
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


# ─────────────────────────────────────────────────────────────────────────────
# Platform setup
# ─────────────────────────────────────────────────────────────────────────────

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Jeedom cover entities (volets/stores) from a config entry."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: JeedomCoordinator = entry_data[DATA_COORDINATOR]
    client: JeedomApiClient = entry_data[DATA_API]

    entities = [
        JeedomCoverEntity(coordinator, client, device)
        for device in (coordinator.data or {}).values()
        if device.is_cover
    ]

    _LOGGER.debug("Adding %d Jeedom cover entities", len(entities))
    async_add_entities(entities)


# ─────────────────────────────────────────────────────────────────────────────
# Entity class
# ─────────────────────────────────────────────────────────────────────────────

class JeedomCoverEntity(CoordinatorEntity[JeedomCoordinator], CoverEntity):
    """
    Représente un volet/store/barrière Jeedom dans Home Assistant.

    Supporte les commandes FLAP_UP (ouvrir/monter) et FLAP_DOWN (fermer/descendre).
    Compatible avec l'intégration Alexa (INTERIOR_BLIND, EXTERIOR_BLIND).
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
        self._attr_unique_id = f"jeedom_cover_{device.eq_id}"

        # Build supported features based on available commands
        features = CoverEntityFeature(0)
        if device.cmd_open_id is not None:
            features |= CoverEntityFeature.OPEN
        if device.cmd_close_id is not None:
            features |= CoverEntityFeature.CLOSE
        self._attr_supported_features = features

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

    # ── CoverEntity properties ────────────────────────────────────────────────

    @property
    def name(self) -> str:
        dev = self._current_device
        return dev.name if dev else self._device.name

    @property
    def is_closed(self) -> bool | None:
        """Return True if cover is closed, False if open, None if unknown."""
        dev = self._current_device
        if dev is None:
            return None
        try:
            val = float(dev.current_state)
            # Jeedom FLAP_STATE: 0 = closed, 1 = open (binary)
            # or 0–100 (position %) where 0 = closed
            return val == 0
        except (ValueError, TypeError):
            state = str(dev.current_state).lower()
            if state in ("0", "false", "closed", "fermé"):
                return True
            if state in ("1", "true", "open", "ouvert"):
                return False
            return None

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and self._current_device is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose Jeedom metadata as entity attributes."""
        dev = self._current_device or self._device
        return {
            "jeedom_name": dev.name,
            "jeedom_id": dev.eq_id,
            "plugin": dev.plugin_id,
            "category": dev.category,
            "cmd_open_id": dev.cmd_open_id,
            "cmd_close_id": dev.cmd_close_id,
            "cmd_cover_state_id": dev.cmd_cover_state_id,
        }

    # ── Control methods ───────────────────────────────────────────────────────

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open (raise) the cover."""
        dev = self._current_device or self._device
        if dev.cmd_open_id is None:
            raise HomeAssistantError(
                f"No 'open' command for Jeedom cover {dev.name} (id={dev.eq_id})"
            )
        _LOGGER.debug("Opening cover %s (cmd_id=%s)", dev.name, dev.cmd_open_id)
        await self._call_cmd(dev.cmd_open_id)

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close (lower) the cover."""
        dev = self._current_device or self._device
        if dev.cmd_close_id is None:
            raise HomeAssistantError(
                f"No 'close' command for Jeedom cover {dev.name} (id={dev.eq_id})"
            )
        _LOGGER.debug("Closing cover %s (cmd_id=%s)", dev.name, dev.cmd_close_id)
        await self._call_cmd(dev.cmd_close_id)

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _call_cmd(self, cmd_id: str) -> None:
        """Execute a Jeedom command and request a coordinator refresh."""
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

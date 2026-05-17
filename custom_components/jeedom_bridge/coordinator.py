"""DataUpdateCoordinator for the Jeedom Bridge integration."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import JeedomApiClient, JeedomApiError, JeedomConnectionError
from .const import (
    CMD_SUBTYPE_SLIDER,
    CMD_TYPE_ACTION,
    CMD_TYPE_INFO,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LIGHT_CATEGORIES,
    LIGHT_PLUGINS,
)

_LOGGER = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

class JeedomDevice:
    """Parsed representation of a Jeedom eqLogic with its commands."""

    __slots__ = (
        "eq_id",
        "name",
        "is_active",
        "plugin_id",
        "category",
        "cmd_on_id",
        "cmd_off_id",
        "cmd_state_id",
        "cmd_slider_id",
        "current_state",      # "1" / "0" or numeric string
        "supports_brightness",
    )

    def __init__(
        self,
        eq_id: str,
        name: str,
        is_active: bool,
        plugin_id: str,
        category: str,
        cmd_on_id: str | None,
        cmd_off_id: str | None,
        cmd_state_id: str | None,
        cmd_slider_id: str | None,
        current_state: str,
        supports_brightness: bool,
    ) -> None:
        self.eq_id = eq_id
        self.name = name
        self.is_active = is_active
        self.plugin_id = plugin_id.lower()
        self.category = category.lower()
        self.cmd_on_id = cmd_on_id
        self.cmd_off_id = cmd_off_id
        self.cmd_state_id = cmd_state_id
        self.cmd_slider_id = cmd_slider_id
        self.current_state = current_state
        self.supports_brightness = supports_brightness

    @property
    def is_light(self) -> bool:
        """Return True if this device should be mapped to a LightEntity."""
        return (
            self.plugin_id in LIGHT_PLUGINS
            or self.category in LIGHT_CATEGORIES
            or (self.cmd_on_id is not None and self.cmd_off_id is not None and self.cmd_slider_id is not None)
        )

    @property
    def is_switch(self) -> bool:
        """Return True if this device should be mapped to a SwitchEntity (on/off only)."""
        return (
            not self.is_light
            and self.cmd_on_id is not None
            and self.cmd_off_id is not None
        )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<JeedomDevice id={self.eq_id!r} name={self.name!r} "
            f"plugin={self.plugin_id!r} is_light={self.is_light}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Parsing helpers
# ─────────────────────────────────────────────────────────────────────────────

def _find_cmd(
    cmds: list[dict[str, Any]],
    cmd_type: str,
    *names: str,
    subtype: str | None = None,
) -> str | None:
    """
    Look for a command matching *type*, optional *subtype*, and one of the given *names*.
    Returns the command ID as a string, or None.
    """
    names_lower = {n.lower() for n in names}
    for cmd in cmds:
        if cmd.get("type", "").lower() != cmd_type:
            continue
        if subtype and cmd.get("subType", "").lower() != subtype:
            continue
        logical_id = cmd.get("logicalId", "").lower()
        cmd_name = cmd.get("name", "").lower()
        if logical_id in names_lower or cmd_name in names_lower:
            return str(cmd["id"])
    return None


def _parse_eqlogic(raw: dict[str, Any]) -> JeedomDevice | None:
    """
    Parse a raw eqLogic dict from Jeedom into a JeedomDevice.
    Returns None for inactive or unusable devices.
    """
    try:
        eq_id = str(raw["id"])
        name: str = raw.get("name", f"Device {eq_id}")
        is_active: bool = str(raw.get("isEnable", "1")) == "1"
        plugin_id: str = raw.get("eqType_name", "") or raw.get("plugin", "") or ""
        # Jeedom categories may differ across versions
        category: str = raw.get("category", "") or raw.get("tags", "") or ""

        cmds: list[dict[str, Any]] = raw.get("cmds", [])

        cmd_on_id = _find_cmd(cmds, CMD_TYPE_ACTION, "on", "allumer", "marche")
        cmd_off_id = _find_cmd(cmds, CMD_TYPE_ACTION, "off", "éteindre", "eteindre", "arrêt", "arret")
        cmd_state_id = _find_cmd(cmds, CMD_TYPE_INFO, "état", "etat", "state", "statut")
        cmd_slider_id = _find_cmd(
            cmds, CMD_TYPE_ACTION, "intensity", "intensité", "luminosité", "luminosite",
            "slider", "dim", "dimmer", "level", subtype=CMD_SUBTYPE_SLIDER
        )

        # Derive initial state from info commands
        current_state = "0"
        for cmd in cmds:
            if str(cmd.get("id")) == cmd_state_id:
                current_state = str(cmd.get("currentValue", "0"))
                break

        supports_brightness = cmd_slider_id is not None

        return JeedomDevice(
            eq_id=eq_id,
            name=name,
            is_active=is_active,
            plugin_id=plugin_id,
            category=category,
            cmd_on_id=cmd_on_id,
            cmd_off_id=cmd_off_id,
            cmd_state_id=cmd_state_id,
            cmd_slider_id=cmd_slider_id,
            current_state=current_state,
            supports_brightness=supports_brightness,
        )
    except (KeyError, TypeError, ValueError) as err:
        _LOGGER.warning("Failed to parse eqLogic %s: %s", raw.get("id", "?"), err)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Coordinator
# ─────────────────────────────────────────────────────────────────────────────

class JeedomCoordinator(DataUpdateCoordinator[dict[str, JeedomDevice]]):
    """
    Centralise le rafraîchissement des états Jeedom.

    Appelle ``eqLogic::all`` toutes les ``DEFAULT_SCAN_INTERVAL`` secondes et
    expose un dictionnaire ``{eq_id: JeedomDevice}`` à toutes les plateformes.
    """

    def __init__(self, hass: HomeAssistant, client: JeedomApiClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.api = client

    async def _async_update_data(self) -> dict[str, JeedomDevice]:
        """Fetch the full device list and return it indexed by eq_id."""
        try:
            raw_list = await self.api.async_get_all_eqlogics()
        except JeedomConnectionError as err:
            raise UpdateFailed(f"Cannot reach Jeedom: {err}") from err
        except JeedomApiError as err:
            raise UpdateFailed(f"Jeedom API error: {err}") from err

        devices: dict[str, JeedomDevice] = {}
        for raw in raw_list:
            device = _parse_eqlogic(raw)
            if device is None:
                continue
            if not device.is_active:
                _LOGGER.debug("Skipping inactive device %s (%s)", device.eq_id, device.name)
                continue
            if device.cmd_on_id is None and device.cmd_off_id is None:
                _LOGGER.debug(
                    "Skipping device %s (%s): no on/off commands found",
                    device.eq_id, device.name,
                )
                continue
            devices[device.eq_id] = device

        _LOGGER.debug("Coordinator updated: %d controllable devices found", len(devices))
        return devices

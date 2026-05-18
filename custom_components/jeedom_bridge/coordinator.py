"""DataUpdateCoordinator for the Jeedom Bridge integration."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import JeedomApiClient, JeedomApiError, JeedomConnectionError, JeedomPluginApiClient
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
        # light / switch commands
        "cmd_on_id",
        "cmd_off_id",
        "cmd_state_id",
        "cmd_slider_id",
        # cover (volets/stores) commands
        "cmd_open_id",
        "cmd_close_id",
        "cmd_cover_state_id",
        # state
        "current_state",
        "supports_brightness",
        # classification hint from generic_type
        "is_cover_device",
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
        cmd_open_id: str | None = None,
        cmd_close_id: str | None = None,
        cmd_cover_state_id: str | None = None,
        is_cover_device: bool = False,
    ) -> None:
        self.eq_id = eq_id
        self.name = name
        self.is_active = is_active
        self.plugin_id = plugin_id.lower()
        self.category = str(category).lower()
        self.cmd_on_id = cmd_on_id
        self.cmd_off_id = cmd_off_id
        self.cmd_state_id = cmd_state_id
        self.cmd_slider_id = cmd_slider_id
        self.cmd_open_id = cmd_open_id
        self.cmd_close_id = cmd_close_id
        self.cmd_cover_state_id = cmd_cover_state_id
        self.current_state = current_state
        self.supports_brightness = supports_brightness
        self.is_cover_device = is_cover_device

    @property
    def is_cover(self) -> bool:
        """True if this device is a cover (volet/store/barrier)."""
        return self.is_cover_device and (
            self.cmd_open_id is not None or self.cmd_close_id is not None
        )

    @property
    def is_light(self) -> bool:
        """Return True if this device should be mapped to a LightEntity."""
        if self.is_cover:
            return False
        return (
            self.category in LIGHT_CATEGORIES
            # openzwave/edisio lights detected via category; virtual mirrors them
            or self.plugin_id in {"openzwave", "edisio"} and self.category in LIGHT_CATEGORIES
            # has a brightness slider → definitely a dimmable light
            or self.cmd_slider_id is not None
        )

    @property
    def is_switch(self) -> bool:
        """True if on/off device that is neither a light nor a cover."""
        return (
            not self.is_light
            and not self.is_cover
            and self.cmd_on_id is not None
            and self.cmd_off_id is not None
        )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<JeedomDevice id={self.eq_id!r} name={self.name!r} "
            f"plugin={self.plugin_id!r} light={self.is_light} "
            f"switch={self.is_switch} cover={self.is_cover}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Parsing helpers
# ─────────────────────────────────────────────────────────────────────────────

def _find_cmd_by_generic(
    cmds: list[dict[str, Any]],
    *generic_types: str,
    cmd_type: str | None = None,
    subtype: str | None = None,
) -> str | None:
    """
    Find a command whose ``generic_type`` matches one of the given values.
    Optionally filter by ``type`` (action/info) and ``subType``.
    This is the PRIMARY lookup method — Jeedom stores intent in generic_type.
    """
    if not isinstance(cmds, list):
        return None
    wanted = {g.upper() for g in generic_types}
    for cmd in cmds:
        try:
            gt = str(cmd.get("generic_type") or "").upper()
            if gt not in wanted:
                continue
            if cmd_type and cmd.get("type", "").lower() != cmd_type:
                continue
            if subtype and cmd.get("subType", "").lower() != subtype:
                continue
            return str(cmd["id"])
        except Exception:  # noqa: BLE001
            continue
    return None


def _find_cmd(
    cmds: list[dict[str, Any]],
    cmd_type: str,
    *names: str,
    subtype: str | None = None,
) -> str | None:
    """
    Fallback: find a command by matching ``name`` or ``logicalId`` (case-insensitive).
    Use _find_cmd_by_generic() first when possible.
    """
    if not isinstance(cmds, list):
        return None
    names_lower = {n.lower() for n in names}
    for cmd in cmds:
        try:
            if cmd.get("type", "").lower() != cmd_type:
                continue
            if subtype and cmd.get("subType", "").lower() != subtype:
                continue
            logical_id = str(cmd.get("logicalId", "")).lower()
            cmd_name = str(cmd.get("name", "")).lower()
            if logical_id in names_lower or cmd_name in names_lower:
                return str(cmd["id"])
        except Exception:  # noqa: BLE001
            continue
    return None


def _find_cmd_fallback_first(
    cmds: list[dict[str, Any]],
    cmd_type: str,
    skip_id: str | None = None,
    exclude_generic: set[str] | None = None,
) -> str | None:
    """
    Last-resort fallback: first action command that is not already assigned.
    Skips commands whose generic_type is in *exclude_generic* (e.g. "DONT", "BATTERY").
    """
    if not isinstance(cmds, list):
        return None
    excluded = {g.upper() for g in (exclude_generic or set())}
    for cmd in cmds:
        try:
            if cmd.get("type", "").lower() != cmd_type:
                continue
            cmd_id = str(cmd["id"])
            if skip_id is not None and cmd_id == skip_id:
                continue
            gt = str(cmd.get("generic_type") or "").upper()
            if gt in excluded:
                continue
            return cmd_id
        except Exception:  # noqa: BLE001
            continue
    return None


def _parse_eqlogic(
    raw: dict[str, Any],
    forced_plugin_id: str | None = None,
) -> JeedomDevice | None:
    """
    Parse a raw eqLogic dict from Jeedom into a JeedomDevice.
    Returns None for inactive or unusable devices.

    ``forced_plugin_id`` is used when fetching from a plugin API directly so
    the device is always tagged with the correct plugin, even if the raw
    payload omits or misreports ``eqType_name``.
    """
    try:
        eq_id = str(raw["id"])
        name: str = raw.get("name", f"Device {eq_id}")
        is_active: bool = str(raw.get("isEnable", "1")) == "1"
        plugin_id: str = (
            forced_plugin_id
            or raw.get("eqType_name", "")
            or raw.get("plugin", "")
            or ""
        )
        # Jeedom categories may differ across versions.
        # In some Jeedom v3 builds, 'category' is returned as a dict
        # (e.g. {"light": "1", "heating": "0"}) rather than a plain string.
        _raw_cat = raw.get("category", "") or raw.get("tags", "") or ""
        if isinstance(_raw_cat, dict):
            # Pick the first enabled category key, or empty string
            category: str = next(
                (k for k, v in _raw_cat.items() if str(v) == "1"), ""
            )
        else:
            category: str = _raw_cat

        cmds: list[dict[str, Any]] = raw.get("cmds", [])
        if not cmds:
            cmds = []

        # ── Log what Jeedom returned so we can diagnose issues ────────────────
        if cmds:
            _LOGGER.debug(
                "eqLogic %s (%s): %d cmd(s): %s",
                eq_id, name,
                len(cmds),
                [(c.get("name"), c.get("generic_type"), c.get("type"), c.get("subType")) for c in cmds],
            )
        else:
            _LOGGER.info(
                "eqLogic %s (%s): no commands found — device will be skipped",
                eq_id, name,
            )

        # ── ON command ──────────────────────────────────────────────────────────
        # Priority 1: generic_type (most reliable — set by Jeedom plugin)
        cmd_on_id = _find_cmd_by_generic(
            cmds,
            "LIGHT_ON", "ENERGY_ON", "FLAP_UP",
            "CAMERA_UP", "BARRIER_UP",
            cmd_type=CMD_TYPE_ACTION,
        )
        # Priority 2: name or logicalId match
        if cmd_on_id is None:
            cmd_on_id = _find_cmd(
                cmds, CMD_TYPE_ACTION,
                "on", "1", "true",
                "allumer", "marche", "ouvrir", "ouverture",
                "open", "start", "enable",
            )
        # Priority 3: first available action command
        if cmd_on_id is None:
            cmd_on_id = _find_cmd_fallback_first(
                cmds, CMD_TYPE_ACTION,
                exclude_generic={"DONT", "BATTERY", "REFRESH"},
            )
            if cmd_on_id:
                _LOGGER.debug(
                    "eqLogic %s (%s): no generic_type/named ON cmd — using first action cmd %s",
                    eq_id, name, cmd_on_id,
                )

        # ── OFF command ─────────────────────────────────────────────────────────
        # Priority 1: generic_type
        cmd_off_id = _find_cmd_by_generic(
            cmds,
            "LIGHT_OFF", "ENERGY_OFF", "FLAP_DOWN",
            "CAMERA_DOWN", "BARRIER_DOWN",
            cmd_type=CMD_TYPE_ACTION,
        )
        # Priority 2: name or logicalId match
        if cmd_off_id is None:
            cmd_off_id = _find_cmd(
                cmds, CMD_TYPE_ACTION,
                "off", "0", "false",
                "éteindre", "eteindre", "arrêt", "arret", "fermer", "fermeture",
                "close", "stop", "disable",
            )
        # Priority 3: second available action command
        if cmd_off_id is None:
            cmd_off_id = _find_cmd_fallback_first(
                cmds, CMD_TYPE_ACTION,
                skip_id=cmd_on_id,
                exclude_generic={"DONT", "BATTERY", "REFRESH"},
            )
            if cmd_off_id:
                _LOGGER.debug(
                    "eqLogic %s (%s): no generic_type/named OFF cmd — using second action cmd %s",
                    eq_id, name, cmd_off_id,
                )

        # ── STATE command ────────────────────────────────────────────────────────
        # Priority 1: generic_type
        cmd_state_id = _find_cmd_by_generic(
            cmds,
            "LIGHT_STATE", "ENERGY_STATE", "FLAP_STATE",
            "SWITCH_STATE", "BARRIER_STATE",
            cmd_type=CMD_TYPE_INFO,
        )
        # Priority 2: name match
        if cmd_state_id is None:
            cmd_state_id = _find_cmd(
                cmds, CMD_TYPE_INFO,
                "état", "etat", "state", "statut", "status",
                "valeur", "value", "niveau", "level",
            )

        # ── SLIDER command ───────────────────────────────────────────────────────
        # Priority 1: generic_type
        cmd_slider_id = _find_cmd_by_generic(
            cmds,
            "LIGHT_SLIDER", "LIGHT_SET_BRIGHTNESS", "SET_LEVEL",
            cmd_type=CMD_TYPE_ACTION, subtype=CMD_SUBTYPE_SLIDER,
        )
        # Priority 2: name/subtype match
        if cmd_slider_id is None:
            cmd_slider_id = _find_cmd(
                cmds, CMD_TYPE_ACTION,
                "intensity", "intensité", "luminosité", "luminosite",
                "slider", "dim", "dimmer", "level", "niveau",
                subtype=CMD_SUBTYPE_SLIDER,
            )

        # ── COVER commands (volets/stores) ────────────────────────────────────────
        # Detected by FLAP/BARRIER generic_types. These are mutually exclusive
        # with the on/off commands above (same hardware cmd).
        cmd_open_id = _find_cmd_by_generic(
            cmds, "FLAP_UP", "BARRIER_UP", "CAMERA_UP",
            cmd_type=CMD_TYPE_ACTION,
        )
        if cmd_open_id is None:
            cmd_open_id = _find_cmd(
                cmds, CMD_TYPE_ACTION,
                "haut", "up", "ouvrir", "open", "monter",
            )

        cmd_close_id = _find_cmd_by_generic(
            cmds, "FLAP_DOWN", "BARRIER_DOWN", "CAMERA_DOWN",
            cmd_type=CMD_TYPE_ACTION,
        )
        if cmd_close_id is None:
            cmd_close_id = _find_cmd(
                cmds, CMD_TYPE_ACTION,
                "bas", "down", "fermer", "close", "descendre",
            )

        cmd_cover_state_id = _find_cmd_by_generic(
            cmds, "FLAP_STATE", "BARRIER_STATE",
            cmd_type=CMD_TYPE_INFO,
        )

        # A device is a cover if it has FLAP generic types,
        # even if on/off commands were also matched.
        is_cover_device = (
            cmd_open_id is not None or cmd_close_id is not None
        ) and _find_cmd_by_generic(cmds, "FLAP_UP", "FLAP_DOWN", "BARRIER_UP", "BARRIER_DOWN") is not None

        # Derive initial state from info commands
        current_state = "0"
        state_cmd_id = cmd_cover_state_id or cmd_state_id or cmd_slider_id
        
        for cmd in cmds:
            if str(cmd.get("id")) == state_cmd_id:
                # Jeedom can store the state in various fields depending on version/plugin
                val = cmd.get("currentValue")
                if val is None:
                    val = cmd.get("state")
                if val is None:
                    val = cmd.get("value")
                if val is None and isinstance(cmd.get("display"), dict):
                    val = cmd["display"].get("state")
                
                if val is not None and val != "":
                    current_state = str(val)
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
            cmd_open_id=cmd_open_id,
            cmd_close_id=cmd_close_id,
            cmd_cover_state_id=cmd_cover_state_id,
            is_cover_device=is_cover_device,
        )
    except Exception as err:  # noqa: BLE001  — catch-all: never crash the coordinator
        _LOGGER.warning(
            "Failed to parse eqLogic %s: %s (%s)",
            raw.get("id", "?"), err, type(err).__name__,
        )
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

    def __init__(
        self,
        hass: HomeAssistant,
        client: JeedomApiClient,
        plugin_clients: dict[str, JeedomPluginApiClient] | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.api = client
        self.plugin_clients: dict[str, JeedomPluginApiClient] = plugin_clients or {}

    async def _async_update_data(self) -> dict[str, JeedomDevice]:
        """Fetch devices from the global API and all configured plugin APIs."""
        try:
            return await self._fetch_all_devices()
        except UpdateFailed:
            raise
        except Exception as err:  # noqa: BLE001
            # Safety net: convert any unexpected exception so HA never receives
            # a raw exception from the coordinator (which could destabilise HA).
            _LOGGER.exception("Unexpected error in Jeedom coordinator")
            raise UpdateFailed(f"Unexpected coordinator error: {err}") from err

    async def _fetch_all_devices(self) -> dict[str, JeedomDevice]:
        """Internal fetch — may raise UpdateFailed only."""
        # ── 1. Global Jeedom API ──────────────────────────────────────────────
        try:
            raw_list = await self.api.async_get_all_eqlogics()
        except JeedomConnectionError as err:
            raise UpdateFailed(f"Cannot reach Jeedom: {err}") from err
        except JeedomApiError as err:
            raise UpdateFailed(f"Jeedom API error: {err}") from err

        _LOGGER.debug(
            "eqLogic::all returned %d raw eqLogic(s)", len(raw_list)
        )

        # Detect whether Jeedom includes cmds inline or we need to fetch them
        # separately (some Jeedom versions/configs omit cmds from eqLogic::all).
        raw_list = await self._enrich_cmds(raw_list, self.api)

        devices: dict[str, JeedomDevice] = {}
        for raw in raw_list:
            try:
                device = _parse_eqlogic(raw)
                if device is None:
                    continue
                usable, reason = self._is_usable(device)
                if usable:
                    devices[device.eq_id] = device
                    _LOGGER.debug(
                        "  ✓ %s (%s) — plugin=%s light=%s switch=%s",
                        device.name, device.eq_id,
                        device.plugin_id, device.is_light, device.is_switch,
                    )
                else:
                    _LOGGER.debug(
                        "  ✗ %s (%s) skipped: %s", device.name, device.eq_id, reason
                    )
            except Exception:  # noqa: BLE001
                _LOGGER.warning(
                    "Skipping malformed eqLogic entry: %s", raw.get("id", "?"),
                    exc_info=True,
                )

        # ── 2. Plugin-specific APIs (non-blocking per plugin) ─────────────────
        for plugin_id, plugin_client in self.plugin_clients.items():
            try:
                plugin_raws = await plugin_client.async_get_plugin_eqlogics()
            except Exception:  # noqa: BLE001
                _LOGGER.warning(
                    "Plugin '%s': unexpected error fetching eqLogics — skipped",
                    plugin_id, exc_info=True,
                )
                continue

            _LOGGER.debug("Plugin '%s': %d raw eqLogic(s) received", plugin_id, len(plugin_raws))
            plugin_raws = await self._enrich_cmds(plugin_raws, plugin_client)

            new_count = 0
            for raw in plugin_raws:
                try:
                    device = _parse_eqlogic(raw, forced_plugin_id=plugin_id)
                    if device is not None:
                        usable, reason = self._is_usable(device)
                        if usable:
                            devices[device.eq_id] = device
                            new_count += 1
                        else:
                            _LOGGER.debug(
                                "  Plugin '%s' ✗ %s (%s) skipped: %s",
                                plugin_id, device.name, device.eq_id, reason,
                            )
                except Exception:  # noqa: BLE001
                    _LOGGER.warning(
                        "Plugin '%s': skipping malformed entry %s",
                        plugin_id, raw.get("id", "?"), exc_info=True,
                    )
            _LOGGER.debug(
                "Plugin '%s': %d usable device(s) loaded", plugin_id, new_count
            )

        _LOGGER.debug("Coordinator updated: %d controllable device(s) found", len(devices))
        return devices

    async def _enrich_cmds(
        self,
        raw_list: list[dict[str, Any]],
        client: Any,
    ) -> list[dict[str, Any]]:
        """
        If any eqLogic in *raw_list* has an empty or missing 'cmds' field,
        fetch commands via cmd::byEqLogicId and inject them.
        Returns the enriched list.
        """
        needs_fetch = any(
            not raw.get("cmds")
            for raw in raw_list
            if isinstance(raw, dict)
        )

        if not needs_fetch:
            return raw_list  # cmds already present — nothing to do

        _LOGGER.debug(
            "cmds missing from eqLogic payload — fetching via cmd::byEqLogicId"
        )
        enriched = []
        for raw in raw_list:
            if not isinstance(raw, dict):
                enriched.append(raw)
                continue
            if not raw.get("cmds"):
                eq_id = raw.get("id")
                if eq_id and hasattr(client, "async_get_cmds_by_eqlogic"):
                    cmds = await client.async_get_cmds_by_eqlogic(eq_id)
                    raw = {**raw, "cmds": cmds}  # non-destructive copy
                    _LOGGER.debug(
                        "  eqLogic %s: fetched %d cmd(s) separately",
                        eq_id, len(cmds),
                    )
            enriched.append(raw)
        return enriched

    @staticmethod
    def _is_usable(device: JeedomDevice) -> tuple[bool, str]:
        """
        Return (True, "") if the device should be exposed as an entity,
        or (False, reason) explaining why it was skipped.
        """
        if not device.is_active:
            return False, "inactive (isEnable != 1)"
        if device.cmd_on_id is not None or device.cmd_off_id is not None:
            return True, ""  # has at least one on/off command
        return False, "no on/off commands found"

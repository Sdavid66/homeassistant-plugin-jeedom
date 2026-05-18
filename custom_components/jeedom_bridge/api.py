"""Jeedom JSON-RPC 2.0 API client (async, aiohttp)."""
from __future__ import annotations

import logging
from typing import Any

from aiohttp import ClientSession, ClientError, ClientResponseError

from .const import (
    JEEDOM_API_PATH,
    JEEDOM_API_VERSION,
    METHOD_EQLOGIC_ALL,
    METHOD_CMD_EXECUTE,
    METHOD_CMD_BY_EQLOGIC,
    CONF_PLUGIN_KEY_EDISIO,
    CONF_PLUGIN_KEY_ZWAVE,
    CONF_PLUGIN_KEY_VIRTUEL,
)

_LOGGER = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Custom exceptions
# ─────────────────────────────────────────────────────────────────────────────

class JeedomApiError(Exception):
    """Raised when the Jeedom API returns a JSON-RPC error."""


class JeedomConnectionError(Exception):
    """Raised when we cannot reach the Jeedom instance."""


class JeedomAuthError(Exception):
    """Raised when the API key is rejected by Jeedom."""


# ─────────────────────────────────────────────────────────────────────────────
# API Client — global Jeedom core
# ─────────────────────────────────────────────────────────────────────────────

class JeedomApiClient:
    """Thin async wrapper around the Jeedom JSON-RPC 2.0 API."""

    def __init__(
        self,
        session: ClientSession,
        base_url: str,
        api_key: str,
    ) -> None:
        self._session = session
        # Normalize URL: strip trailing slash
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._endpoint = f"{self._base_url}{JEEDOM_API_PATH}"

    # ── Low-level ─────────────────────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        extra_params: dict[str, Any] | None = None,
    ) -> Any:
        """
        Send a JSON-RPC 2.0 POST request and return the *result* field.

        :raises JeedomConnectionError: Network / timeout issues.
        :raises JeedomAuthError: API key rejected.
        :raises JeedomApiError: Any other JSON-RPC error.
        """
        params: dict[str, Any] = {"apikey": self._api_key}
        if extra_params:
            params.update(extra_params)

        payload = {
            "jsonrpc": JEEDOM_API_VERSION,
            "method": method,
            "params": params,
            "id": 1,
        }

        _LOGGER.debug("Jeedom → %s | params: %s", method, extra_params)

        try:
            async with self._session.post(
                self._endpoint,
                json=payload,
                timeout=10,
            ) as response:
                response.raise_for_status()
                data: dict[str, Any] = await response.json(content_type=None)
        except ClientResponseError as err:
            raise JeedomConnectionError(
                f"HTTP {err.status} from {self._endpoint}"
            ) from err
        except ClientError as err:
            raise JeedomConnectionError(
                f"Cannot connect to Jeedom at {self._endpoint}: {err}"
            ) from err

        if "error" in data:
            error = data["error"]
            code: int = error.get("code", -1)
            message: str = error.get("message", "Unknown error")
            if code in (-32000, 403):
                raise JeedomAuthError(f"Invalid API key (code {code}): {message}")
            raise JeedomApiError(f"Jeedom API error (code {code}): {message}")

        return data.get("result")

    # ── High-level helpers ────────────────────────────────────────────────────

    async def async_test_connection(self) -> bool:
        """Validate URL + API key by fetching all eqLogics."""
        await self._request(METHOD_EQLOGIC_ALL)
        return True

    async def async_get_all_eqlogics(self) -> list[dict[str, Any]]:
        """Return the full list of active eqLogic objects from Jeedom."""
        result = await self._request(METHOD_EQLOGIC_ALL)
        if not isinstance(result, list):
            _LOGGER.warning("eqLogic::all returned unexpected type: %s", type(result))
            return []
        return result

    async def async_get_cmds_by_eqlogic(self, eq_id: str | int) -> list[dict[str, Any]]:
        """
        Fetch all commands for a specific eqLogic by its ID.
        Used when eqLogic::all returns devices without their cmds array.
        """
        try:
            result = await self._request(
                METHOD_CMD_BY_EQLOGIC,
                extra_params={"eqLogicId": str(eq_id)},
            )
        except (JeedomApiError, JeedomConnectionError, JeedomAuthError) as err:
            _LOGGER.warning("Cannot fetch cmds for eqLogic %s: %s", eq_id, err)
            return []
        if not isinstance(result, list):
            return []
        return result

    async def async_execute_cmd(self, cmd_id: str | int) -> Any:
        """
        Execute a Jeedom command by its ID.
        Used for on/off/dim actions.
        """
        return await self._request(
            METHOD_CMD_EXECUTE,
            extra_params={"id": str(cmd_id)},
        )

    async def async_execute_cmd_with_options(
        self,
        cmd_id: str | int,
        options: dict[str, Any],
    ) -> Any:
        """Execute a command with extra options (e.g., slider value for brightness)."""
        return await self._request(
            METHOD_CMD_EXECUTE,
            extra_params={"id": str(cmd_id), "options": options},
        )


# ─────────────────────────────────────────────────────────────────────────────
# Plugin-specific API client
# ─────────────────────────────────────────────────────────────────────────────

class JeedomPluginApiClient(JeedomApiClient):
    """
    API client targeting a Jeedom plugin's own JSON-RPC endpoint.

    Each Jeedom plugin exposes its API at:
        /plugins/<plugin_id>/core/api/jeeApi.php
    with its own dedicated API key.
    """

    PLUGIN_API_PATH_TEMPLATE = "/plugins/{plugin_id}/core/api/jeeApi.php"

    def __init__(
        self,
        session: ClientSession,
        base_url: str,
        plugin_id: str,
        plugin_api_key: str,
    ) -> None:
        super().__init__(session=session, base_url=base_url, api_key=plugin_api_key)
        self.plugin_id = plugin_id
        self._endpoint = (
            f"{self._base_url}"
            f"{self.PLUGIN_API_PATH_TEMPLATE.format(plugin_id=plugin_id)}"
        )

    async def async_test_plugin_connection(self) -> bool:
        """
        Test connectivity to this plugin's API endpoint.
        Sends a simple eqLogic::all probe; returns True if reachable.
        Logs a warning (instead of raising) so one bad plugin does not
        block the whole integration setup.
        """
        try:
            await self._request(METHOD_EQLOGIC_ALL)
            return True
        except (JeedomAuthError, JeedomApiError, JeedomConnectionError) as err:
            _LOGGER.warning(
                "Plugin '%s' API test failed (%s): %s",
                self.plugin_id, type(err).__name__, err,
            )
            return False

    async def async_get_plugin_eqlogics(self) -> list[dict[str, Any]]:
        """Return eqLogics exposed by this specific plugin."""
        try:
            result = await self._request(METHOD_EQLOGIC_ALL)
        except (JeedomAuthError, JeedomApiError, JeedomConnectionError) as err:
            _LOGGER.warning(
                "Cannot fetch eqLogics from plugin '%s': %s", self.plugin_id, err
            )
            return []
        if not isinstance(result, list):
            _LOGGER.warning(
                "Plugin '%s' eqLogic::all returned unexpected type: %s",
                self.plugin_id, type(result),
            )
            return []
        return result


# ─────────────────────────────────────────────────────────────────────────────
# Factory — build plugin clients from config entry data
# ─────────────────────────────────────────────────────────────────────────────

# Map: config key → plugin_id used in the Jeedom URL path
_PLUGIN_KEY_MAP: dict[str, str] = {
    CONF_PLUGIN_KEY_EDISIO:  "edisio",
    CONF_PLUGIN_KEY_ZWAVE:   "zwave",
    CONF_PLUGIN_KEY_VIRTUEL: "virtuel",
}


def build_plugin_clients(
    session: ClientSession,
    base_url: str,
    entry_data: dict[str, Any],
) -> dict[str, JeedomPluginApiClient]:
    """
    Return a dict of ``{plugin_id: JeedomPluginApiClient}`` for every plugin
    whose API key was supplied in *entry_data*.  Keys with empty values are
    silently skipped.
    """
    clients: dict[str, JeedomPluginApiClient] = {}
    for conf_key, plugin_id in _PLUGIN_KEY_MAP.items():
        key = entry_data.get(conf_key, "").strip()
        if key:
            clients[plugin_id] = JeedomPluginApiClient(
                session=session,
                base_url=base_url,
                plugin_id=plugin_id,
                plugin_api_key=key,
            )
            _LOGGER.debug("Plugin client created for '%s'", plugin_id)
    return clients

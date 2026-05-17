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
# API Client
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

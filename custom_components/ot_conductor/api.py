"""Thin async client for the OwnTone JSON API."""

from __future__ import annotations

import json
from typing import Any

import aiohttp

from .const import QUEUE_TIMEOUT, REQUEST_TIMEOUT


class OwnToneError(Exception):
    """Raised when OwnTone cannot be reached or returns an error.

    kind is "http" (OwnTone answered with an error status), "timeout",
    "unreachable", or "error" (anything else). For "http", status is the HTTP
    status and detail is whatever text OwnTone sent back, if any.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        detail: str | None = None,
        kind: str = "error",
    ) -> None:
        super().__init__(message)
        self.status = status
        self.detail = detail
        self.kind = kind


def _detail(body: bytes) -> str | None:
    """A short, single-line version of an error response body."""
    text = " ".join(body.decode("utf-8", "replace").split())
    if not text:
        return None
    return text if len(text) <= 200 else text[:197] + "..."


class OwnToneClient:
    """Talks to one OwnTone server."""

    def __init__(self, session: aiohttp.ClientSession, host: str, port: int) -> None:
        self._session = session
        self._base = f"http://{host}:{port}"

    @property
    def base_url(self) -> str:
        return self._base

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        timeout: float = REQUEST_TIMEOUT,
    ) -> Any:
        url = f"{self._base}{path}"
        try:
            async with self._session.request(
                method,
                url,
                params=params,
                json=json_body,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                body = await resp.read()
                if resp.status >= 400:
                    detail = _detail(body)
                    raise OwnToneError(
                        f"OwnTone returned HTTP {resp.status} for {method} {path}"
                        + (f": {detail}" if detail else ""),
                        status=resp.status,
                        detail=detail,
                        kind="http",
                    )
        except TimeoutError as err:
            raise OwnToneError(
                f"Timed out calling {method} {path}", kind="timeout"
            ) from err
        except aiohttp.ClientError as err:
            raise OwnToneError(
                f"Cannot reach OwnTone at {self._base}: {err}", kind="unreachable"
            ) from err
        if not body:
            return None
        try:
            return json.loads(body)
        except ValueError:
            return None

    async def get_config(self) -> dict[str, Any]:
        return await self._request("GET", "/api/config") or {}

    async def get_outputs(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/api/outputs") or {}
        return data.get("outputs", [])

    async def get_player(self) -> dict[str, Any]:
        return await self._request("GET", "/api/player") or {}

    async def set_outputs(self, output_ids: list[str]) -> None:
        await self._request(
            "PUT", "/api/outputs/set", json_body={"outputs": output_ids}
        )

    async def set_volume(
        self,
        *,
        volume: int | None = None,
        step: int | None = None,
        output_id: str | None = None,
    ) -> None:
        """Set or step the volume. Without output_id this is the master volume."""
        params: dict[str, Any] = {}
        if volume is not None:
            params["volume"] = volume
        if step is not None:
            params["step"] = step
        if output_id is not None:
            params["output_id"] = output_id
        await self._request("PUT", "/api/player/volume", params=params)

    async def queue_add(
        self,
        uri: str,
        *,
        clear: bool,
        start: bool,
        position: int | None = None,
        from_position: int | None = None,
    ) -> None:
        params: dict[str, Any] = {"uris": uri}
        if clear:
            params["clear"] = "true"
        if position is not None:
            params["position"] = position
        if start:
            params["playback"] = "start"
            if from_position is not None:
                params["playback_from_position"] = from_position
        await self._request(
            "POST", "/api/queue/items/add", params=params, timeout=QUEUE_TIMEOUT
        )

    async def get_now_playing(self) -> dict[str, Any] | None:
        """The currently playing queue item, or None."""
        data = await self._request("GET", "/api/queue", params={"id": "now_playing"})
        items = (data or {}).get("items") or []
        return items[0] if items else None

    async def player_command(self, command: str) -> None:
        """play, pause, stop, next or previous."""
        await self._request("PUT", f"/api/player/{command}")

    async def set_shuffle(self, enabled: bool) -> None:
        await self._request(
            "PUT", "/api/player/shuffle", params={"state": str(enabled).lower()}
        )

    async def set_repeat(self, mode: str) -> None:
        """off, all or single."""
        await self._request("PUT", "/api/player/repeat", params={"state": mode})

    async def seek(self, position_ms: int) -> None:
        await self._request("PUT", "/api/player/seek", params={"position_ms": position_ms})

    async def clear_queue(self) -> None:
        await self._request("PUT", "/api/queue/clear")

    async def stop(self) -> None:
        await self._request("PUT", "/api/player/stop")

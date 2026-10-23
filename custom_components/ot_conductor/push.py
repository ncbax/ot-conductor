"""Listens to OwnTone's websocket so changes show up straight away."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

import aiohttp

from .api import OwnToneClient, OwnToneError
from .const import (
    PUSH_EVENTS,
    PUSH_HEARTBEAT,
    PUSH_RECHECK,
    PUSH_RECONNECT_MAX,
    PUSH_RECONNECT_MIN,
)

if TYPE_CHECKING:
    from .coordinator import OwnToneCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_check_push_port(
    session: aiohttp.ClientSession, host: str, port: int, timeout: float = 5.0
) -> None:
    """Try a websocket handshake; raise OwnToneError if it doesn't work."""
    try:
        async with asyncio.timeout(timeout):
            async with session.ws_connect(
                f"ws://{host}:{port}/", protocols=("notify",)
            ):
                pass  # connected fine; leaving the block closes it
    except (aiohttp.ClientError, OSError, TimeoutError) as err:
        raise OwnToneError(f"No websocket answered on port {port}: {err}") from err


class OwnTonePush:
    """Connects, subscribes, and asks the coordinator to refresh on each event.

    Notifications only say *what* changed, not the new values, so every event
    triggers a normal refresh over the REST API. If the websocket is disabled
    or unreachable the coordinator simply keeps polling at the faster interval.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        client: OwnToneClient,
        host: str,
        coordinator: OwnToneCoordinator,
        port: int | None = None,
    ) -> None:
        """port: the configured websocket port; 0 turns push off; None asks
        OwnTone for it (entries created before the port was configurable)."""
        self._session = session
        self._client = client
        self._host = host
        self._coordinator = coordinator
        self._port = port

    async def _detect_port(self) -> int:
        config = await self._client.get_config()
        return int(config.get("websocket_port") or 0)

    async def run(self) -> None:
        """Run until cancelled (when the config entry unloads)."""
        if self._port == 0:
            _LOGGER.info("Push updates are turned off in the setup; using polling")
            return
        delay = PUSH_RECONNECT_MIN
        warned = False
        while True:
            try:
                port = self._port if self._port is not None else await self._detect_port()
                if not port:  # only when auto-detecting
                    if not warned:
                        _LOGGER.info(
                            "OwnTone websockets are disabled; using polling instead"
                        )
                        warned = True
                    await asyncio.sleep(PUSH_RECHECK)
                    continue
                await self._listen(port)
                delay = PUSH_RECONNECT_MIN  # it worked, so start the backoff over
                warned = False
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - never let the loop die
                if not warned:
                    _LOGGER.warning(
                        "Push updates unavailable (%s). The websocket port must be "
                        "enabled in owntone.conf (websocket_port) and reachable from "
                        "Home Assistant. Using polling instead",
                        err,
                    )
                    warned = True
                else:
                    _LOGGER.debug("Push connection failed again: %s", err)
            await self._coordinator.async_set_push_state(False)
            await asyncio.sleep(delay)
            delay = min(delay * 2, PUSH_RECONNECT_MAX)

    async def _listen(self, port: int) -> None:
        url = f"ws://{self._host}:{port}/"
        async with self._session.ws_connect(
            url, protocols=("notify",), heartbeat=PUSH_HEARTBEAT
        ) as ws:
            await ws.send_json({"notify": PUSH_EVENTS})
            _LOGGER.info("Push updates connected (websocket port %s)", port)
            await self._coordinator.async_set_push_state(True)
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        events = json.loads(msg.data).get("notify", [])
                    except (ValueError, AttributeError):
                        continue
                    if events:
                        await self._coordinator.async_request_refresh()
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                ):
                    break

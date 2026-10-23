"""Keeps a cached picture of OwnTone: outputs, player and the playing item."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import OwnToneClient, OwnToneError
from .const import (
    DOMAIN,
    POLL_INTERVAL_FALLBACK,
    POLL_INTERVAL_PUSH,
    REFRESH_COOLDOWN,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class OwnToneCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """data = {
        "player": GET /api/player,
        "outputs": {output_id: output dict},
        "now_playing": queue item dict or None,
        "updated_at": when this snapshot was taken (for track position),
    }
    """

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: OwnToneClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=POLL_INTERVAL_FALLBACK),
            # Short cooldown so push notifications feel instant but bursts merge.
            request_refresh_debouncer=Debouncer(
                hass, _LOGGER, cooldown=REFRESH_COOLDOWN, immediate=True
            ),
        )
        self.client = client
        self.push_connected = False

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            outputs, player = await asyncio.gather(
                self.client.get_outputs(), self.client.get_player()
            )
        except OwnToneError as err:
            raise UpdateFailed(str(err)) from err

        now_playing = None
        if player.get("state") in ("play", "pause"):
            try:
                now_playing = await self.client.get_now_playing()
            except OwnToneError:
                now_playing = None  # missing track info shouldn't fail the update

        return {
            "player": player,
            "outputs": {str(o["id"]): o for o in outputs},
            "now_playing": now_playing,
            "updated_at": datetime.now(timezone.utc),
        }

    async def async_set_push_state(self, connected: bool) -> None:
        """Called by the push listener: slow the poll down while push works."""
        if connected == self.push_connected:
            return
        self.push_connected = connected
        self.update_interval = timedelta(
            seconds=POLL_INTERVAL_PUSH if connected else POLL_INTERVAL_FALLBACK
        )
        _LOGGER.debug("Push updates %s", "connected" if connected else "lost")
        # Refreshing now also catches anything missed while disconnected, and
        # reschedules the next poll with the new interval.
        await self.async_refresh()

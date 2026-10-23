"""Media players: the OwnTone master player, plus one per speaker (output)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    RepeatMode,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import OwnToneError
from .const import DOMAIN
from .coordinator import OwnToneCoordinator
from .device import output_device_info

if TYPE_CHECKING:
    from . import OwnToneConfigEntry
    from .controller import OwnToneController

F = MediaPlayerEntityFeature

_PLAYER_STATES = {
    "play": MediaPlayerState.PLAYING,
    "pause": MediaPlayerState.PAUSED,
}
_REPEAT_FROM_OWNTONE = {"off": RepeatMode.OFF, "all": RepeatMode.ALL, "single": RepeatMode.ONE}
_REPEAT_TO_OWNTONE = {v: k for k, v in _REPEAT_FROM_OWNTONE.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OwnToneConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    controller = entry.runtime_data
    coordinator = controller.coordinator

    async_add_entities([OwnToneMaster(controller)])

    known: set[str] = set()

    @callback
    def _add_new_outputs() -> None:
        new = [oid for oid in coordinator.data["outputs"] if oid not in known]
        if new:
            known.update(new)
            async_add_entities(OwnToneSpeaker(controller, oid) for oid in new)

    _add_new_outputs()
    # Speakers that appear later get their media player automatically.
    entry.async_on_unload(coordinator.async_add_listener(_add_new_outputs))


class _OwnToneMediaPlayer(CoordinatorEntity[OwnToneCoordinator], MediaPlayerEntity):
    """Shared behaviour: now-playing information and error handling."""

    _attr_has_entity_name = True
    _attr_device_class = MediaPlayerDeviceClass.SPEAKER
    _attr_media_content_type = MediaType.MUSIC

    def __init__(self, controller: OwnToneController) -> None:
        super().__init__(controller.coordinator)
        self._controller = controller

    # -- helpers
    @property
    def _player(self) -> dict[str, Any]:
        return self.coordinator.data["player"]

    @property
    def _item(self) -> dict[str, Any]:
        """The playing queue item (empty when nothing is playing)."""
        return self.coordinator.data.get("now_playing") or {}

    def _shows_media(self) -> bool:
        return True

    def _player_state(self) -> MediaPlayerState:
        return _PLAYER_STATES.get(self._player.get("state"), MediaPlayerState.IDLE)

    async def _run(self, coro) -> None:
        try:
            await coro
        except OwnToneError as err:
            raise HomeAssistantError(str(err)) from err

    async def _command(self, coro) -> None:
        """Run an OwnTone command, then refresh so the state updates promptly."""
        await self._run(coro)
        await self.coordinator.async_request_refresh()

    # -- now playing (shown on the master, and on selected speakers)
    @property
    def media_title(self) -> str | None:
        return self._item.get("title") if self._shows_media() else None

    @property
    def media_artist(self) -> str | None:
        return self._item.get("artist") if self._shows_media() else None

    @property
    def media_album_name(self) -> str | None:
        return self._item.get("album") if self._shows_media() else None

    @property
    def media_content_id(self) -> str | None:
        return self._item.get("uri") if self._shows_media() else None

    @property
    def media_duration(self) -> float | None:
        if not self._shows_media() or not self._item:
            return None
        ms = self._player.get("item_length_ms")
        return ms / 1000 if ms else None  # live streams report no length

    @property
    def media_position(self) -> float | None:
        if not self._shows_media() or not self._item:
            return None
        ms = self._player.get("item_progress_ms")
        return None if ms is None else ms / 1000

    @property
    def media_position_updated_at(self) -> datetime | None:
        if not self._shows_media() or not self._item:
            return None
        return self.coordinator.data["updated_at"]

    @property
    def media_image_url(self) -> str | None:
        if not self._shows_media():
            return None
        artwork = self._item.get("artwork_url")
        if not artwork:
            return None
        url = urljoin(self._controller.client.base_url + "/", artwork)
        if url.startswith(self._controller.client.base_url) and "?" not in url:
            url += "?maxwidth=600&maxheight=600"  # relative URLs can be scaled down
        return url


class OwnToneMaster(_OwnToneMediaPlayer):
    """The OwnTone player itself: playback, queue, master volume."""

    _attr_name = "Master"
    _attr_icon = "mdi:speaker-multiple"
    _attr_supported_features = (
        F.PLAY
        | F.PAUSE
        | F.STOP
        | F.NEXT_TRACK
        | F.PREVIOUS_TRACK
        | F.SEEK
        | F.VOLUME_SET
        | F.VOLUME_STEP
        | F.SHUFFLE_SET
        | F.REPEAT_SET
        | F.PLAY_MEDIA
        | F.MEDIA_ENQUEUE
        | F.CLEAR_PLAYLIST
    )

    def __init__(self, controller: OwnToneController) -> None:
        super().__init__(controller)
        entry = controller.entry
        self._attr_unique_id = f"{entry.entry_id}_master"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    @property
    def state(self) -> MediaPlayerState:
        return self._player_state()

    @property
    def volume_level(self) -> float | None:
        volume = self._player.get("volume")
        return None if volume is None else volume / 100

    @property
    def shuffle(self) -> bool:
        return bool(self._player.get("shuffle"))

    @property
    def repeat(self) -> RepeatMode:
        return _REPEAT_FROM_OWNTONE.get(self._player.get("repeat"), RepeatMode.OFF)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        outputs = self.coordinator.data["outputs"].values()
        return {
            "selected_outputs": [o["name"] for o in outputs if o.get("selected")],
            "update_mode": "push" if self.coordinator.push_connected else "polling",
        }

    # -- playback
    async def async_media_play(self) -> None:
        await self._command(self._controller.client.player_command("play"))

    async def async_media_pause(self) -> None:
        await self._command(self._controller.client.player_command("pause"))

    async def async_media_stop(self) -> None:
        await self._command(self._controller.client.player_command("stop"))

    async def async_media_next_track(self) -> None:
        await self._command(self._controller.client.player_command("next"))

    async def async_media_previous_track(self) -> None:
        await self._command(self._controller.client.player_command("previous"))

    async def async_media_seek(self, position: float) -> None:
        await self._command(self._controller.client.seek(int(position * 1000)))

    async def async_set_shuffle(self, shuffle: bool) -> None:
        await self._command(self._controller.client.set_shuffle(shuffle))

    async def async_set_repeat(self, repeat: RepeatMode) -> None:
        await self._command(
            self._controller.client.set_repeat(_REPEAT_TO_OWNTONE[repeat])
        )

    async def async_clear_playlist(self) -> None:
        await self._command(self._controller.client.clear_queue())

    async def async_play_media(self, media_type: str, media_id: str, **kwargs: Any) -> None:
        # Home Assistant media (TTS, media browser) arrives as a media-source ID
        # or a path on this Home Assistant; turn those into a full URL.
        if media_id.startswith("media-source://"):
            from homeassistant.components import media_source
            from homeassistant.components.media_player.browse_media import (
                async_process_play_media_url,
            )

            resolved = await media_source.async_resolve_media(
                self.hass, media_id, self.entity_id
            )
            media_id = async_process_play_media_url(self.hass, resolved.url)
        elif media_id.startswith("/"):
            from homeassistant.components.media_player.browse_media import (
                async_process_play_media_url,
            )

            media_id = async_process_play_media_url(self.hass, media_id)
        await self._run(self._controller.async_enqueue(media_id, kwargs.get("enqueue")))

    # -- volume (master)
    async def async_set_volume_level(self, volume: float) -> None:
        await self._run(
            self._controller.async_set_volume(["all"], volume=round(volume * 100), step=None)
        )

    async def async_volume_up(self) -> None:
        await self._run(
            self._controller.async_set_volume(
                ["all"], volume=None, step=round(self.volume_step * 100)
            )
        )

    async def async_volume_down(self) -> None:
        await self._run(
            self._controller.async_set_volume(
                ["all"], volume=None, step=-round(self.volume_step * 100)
            )
        )


class OwnToneSpeaker(_OwnToneMediaPlayer):
    """One speaker (output): on/off means selected/not selected, plus volume."""

    _attr_name = None  # named after its device, the speaker
    _attr_icon = "mdi:speaker"
    _attr_supported_features = (
        F.TURN_ON | F.TURN_OFF | F.VOLUME_SET | F.VOLUME_STEP
    )

    def __init__(self, controller: OwnToneController, output_id: str) -> None:
        super().__init__(controller)
        self._output_id = output_id
        entry = controller.entry
        output = controller.coordinator.data["outputs"][output_id]
        self._attr_unique_id = f"{entry.entry_id}_{output_id}"
        self._attr_device_info = output_device_info(entry.entry_id, output)

    @property
    def _output(self) -> dict[str, Any] | None:
        return self.coordinator.data["outputs"].get(self._output_id)

    def _selected(self) -> bool:
        output = self._output
        return bool(output and output.get("selected"))

    def _shows_media(self) -> bool:
        return self._selected()

    @property
    def available(self) -> bool:
        return super().available and self._output is not None

    @property
    def state(self) -> MediaPlayerState:
        if not self._selected():
            return MediaPlayerState.OFF
        return self._player_state()  # a selected speaker plays whatever the player plays

    @property
    def volume_level(self) -> float | None:
        output = self._output
        volume = None if output is None else output.get("volume")
        return None if volume is None else volume / 100

    async def async_turn_on(self) -> None:
        await self._run(self._controller.async_add_outputs([self._output_id]))

    async def async_turn_off(self) -> None:
        await self._run(self._controller.async_remove_outputs([self._output_id]))

    async def async_set_volume_level(self, volume: float) -> None:
        await self._run(
            self._controller.async_set_volume(
                [self._output_id], volume=round(volume * 100), step=None
            )
        )

    async def async_volume_up(self) -> None:
        await self._run(
            self._controller.async_set_volume(
                [self._output_id], volume=None, step=round(self.volume_step * 100)
            )
        )

    async def async_volume_down(self) -> None:
        await self._run(
            self._controller.async_set_volume(
                [self._output_id], volume=None, step=-round(self.volume_step * 100)
            )
        )

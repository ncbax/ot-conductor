"""OT Conductor: services to play streams, set volume and fade on OwnTone."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import OwnToneClient, OwnToneError
from .const import (
    ATTR_CLEAR,
    ATTR_DURATION,
    ATTR_FROM,
    ATTR_INTERVAL,
    ATTR_OUTPUTS,
    ATTR_RAISE_ERROR,
    ATTR_START,
    ATTR_STOP_PLAYBACK,
    ATTR_STEP,
    ATTR_STEPS,
    ATTR_TO,
    ATTR_URI,
    ATTR_VOLUME,
    ATTR_WAIT,
    CONF_PUSH_PORT,
    DOMAIN,
    SERVICE_ADD_OUTPUTS,
    SERVICE_FADE,
    SERVICE_PAUSE,
    SERVICE_PLAY,
    SERVICE_REMOVE_OUTPUTS,
    SERVICE_RESUME,
    SERVICE_SET_VOLUME,
    SERVICE_STOP,
)
from .controller import OwnToneController
from .coordinator import OwnToneCoordinator
from .push import OwnTonePush

PLATFORMS = [Platform.MEDIA_PLAYER]

type OwnToneConfigEntry = ConfigEntry[OwnToneController]

OUTPUTS = vol.All(cv.ensure_list, vol.Length(min=1), [cv.string])
VOLUME = vol.All(vol.Coerce(int), vol.Range(min=0, max=100))

SET_VOLUME_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Required(ATTR_OUTPUTS): OUTPUTS,
            vol.Exclusive(ATTR_VOLUME, "level"): VOLUME,
            vol.Exclusive(ATTR_STEP, "level"): vol.All(
                vol.Coerce(int), vol.Range(min=-100, max=100)
            ),
        }
    ),
    cv.has_at_least_one_key(ATTR_VOLUME, ATTR_STEP),
)

PLAY_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_URI): cv.string,
        vol.Optional(ATTR_OUTPUTS): OUTPUTS,
        vol.Optional(ATTR_VOLUME): VOLUME,
        vol.Optional(ATTR_CLEAR, default=True): cv.boolean,
        vol.Optional(ATTR_START, default=True): cv.boolean,
        vol.Optional(ATTR_RAISE_ERROR, default=True): cv.boolean,
    }
)

STOP_SCHEMA = vol.Schema({})
PAUSE_SCHEMA = vol.Schema({})
RESUME_SCHEMA = vol.Schema({})

OUTPUTS_SCHEMA = vol.Schema({vol.Required(ATTR_OUTPUTS): OUTPUTS})

REMOVE_OUTPUTS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_OUTPUTS): OUTPUTS,
        vol.Optional(ATTR_STOP_PLAYBACK, default=True): cv.boolean,
    }
)

FADE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_OUTPUTS): OUTPUTS,
        vol.Required(ATTR_TO): VOLUME,
        vol.Optional(ATTR_FROM): VOLUME,
        # A total duration sets the timing for you, so steps and interval carry
        # no defaults here: the controller applies 10 and 1.0 only without one.
        vol.Optional(ATTR_DURATION): cv.positive_time_period,
        vol.Optional(ATTR_STEPS): vol.All(vol.Coerce(int), vol.Range(min=1, max=200)),
        vol.Optional(ATTR_INTERVAL): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=60)
        ),
        vol.Optional(ATTR_WAIT, default=False): cv.boolean,
    }
)


def _seconds(value: Any) -> float | None:
    """A timedelta from the duration field as seconds; empty or zero means unset."""
    return value.total_seconds() if value else None


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services once; they find the config entry when called."""

    def controller() -> OwnToneController:
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.state is ConfigEntryState.LOADED:
                return entry.runtime_data
        raise HomeAssistantError("OT Conductor is not set up or not connected")

    async def guarded(coro) -> dict[str, Any]:
        try:
            return await coro
        except OwnToneError as err:
            raise HomeAssistantError(str(err)) from err

    async def handle_set_volume(call: ServiceCall) -> dict[str, Any]:
        return await guarded(
            controller().async_set_volume(
                call.data[ATTR_OUTPUTS],
                volume=call.data.get(ATTR_VOLUME),
                step=call.data.get(ATTR_STEP),
            )
        )

    async def handle_play(call: ServiceCall) -> dict[str, Any]:
        return await guarded(
            controller().async_play(
                call.data[ATTR_URI],
                outputs=call.data.get(ATTR_OUTPUTS),
                volume=call.data.get(ATTR_VOLUME),
                clear=call.data[ATTR_CLEAR],
                start=call.data[ATTR_START],
                raise_error=call.data[ATTR_RAISE_ERROR],
            )
        )

    async def handle_stop(call: ServiceCall) -> dict[str, Any]:
        return await guarded(controller().async_stop())

    async def handle_add_outputs(call: ServiceCall) -> dict[str, Any]:
        return await guarded(controller().async_add_outputs(call.data[ATTR_OUTPUTS]))

    async def handle_remove_outputs(call: ServiceCall) -> dict[str, Any]:
        return await guarded(
            controller().async_remove_outputs(
                call.data[ATTR_OUTPUTS],
                stop_playback=call.data[ATTR_STOP_PLAYBACK],
            )
        )

    async def handle_pause(call: ServiceCall) -> dict[str, Any]:
        return await guarded(controller().async_pause())

    async def handle_resume(call: ServiceCall) -> dict[str, Any]:
        return await guarded(controller().async_resume())

    async def handle_fade(call: ServiceCall) -> dict[str, Any]:
        return await guarded(
            controller().async_fade(
                call.data[ATTR_OUTPUTS],
                target=call.data[ATTR_TO],
                steps=call.data.get(ATTR_STEPS),
                interval=call.data.get(ATTR_INTERVAL),
                duration=_seconds(call.data.get(ATTR_DURATION)),
                start=call.data.get(ATTR_FROM),
                wait=call.data[ATTR_WAIT],
            )
        )

    for name, handler, schema in (
        (SERVICE_SET_VOLUME, handle_set_volume, SET_VOLUME_SCHEMA),
        (SERVICE_PLAY, handle_play, PLAY_SCHEMA),
        (SERVICE_STOP, handle_stop, STOP_SCHEMA),
        (SERVICE_PAUSE, handle_pause, PAUSE_SCHEMA),
        (SERVICE_RESUME, handle_resume, RESUME_SCHEMA),
        (SERVICE_FADE, handle_fade, FADE_SCHEMA),
        (SERVICE_ADD_OUTPUTS, handle_add_outputs, OUTPUTS_SCHEMA),
        (SERVICE_REMOVE_OUTPUTS, handle_remove_outputs, REMOVE_OUTPUTS_SCHEMA),
    ):
        hass.services.async_register(
            DOMAIN,
            name,
            handler,
            schema=schema,
            supports_response=SupportsResponse.OPTIONAL,
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: OwnToneConfigEntry) -> bool:
    client = OwnToneClient(
        async_get_clientsession(hass), entry.data[CONF_HOST], entry.data[CONF_PORT]
    )
    coordinator = OwnToneCoordinator(hass, entry, client)
    # Raises ConfigEntryNotReady (and retries) if OwnTone can't be reached.
    await coordinator.async_config_entry_first_refresh()

    # The server itself is a device; each speaker is a device linked to it.
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="OT Conductor",
        manufacturer="OwnTone",
    )

    entry.runtime_data = OwnToneController(hass, entry, client, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Push updates from OwnTone's websocket; the coordinator keeps polling as a
    # fallback (and as a slow safety net) in case the connection is unavailable.
    # Entries created before the push port was configurable don't have one
    # stored; passing None makes the listener ask OwnTone for its port instead.
    push = OwnTonePush(
        async_get_clientsession(hass),
        client,
        entry.data[CONF_HOST],
        coordinator,
        entry.data.get(CONF_PUSH_PORT),
    )
    entry.async_create_background_task(hass, push.run(), name="ot_conductor push")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: OwnToneConfigEntry) -> bool:
    entry.runtime_data.cancel_all_fades()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

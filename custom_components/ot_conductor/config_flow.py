"""Config flow for OT Conductor."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import OwnToneClient, OwnToneError
from .const import CONF_PUSH_PORT, DEFAULT_PORT, DEFAULT_PUSH_PORT, DOMAIN
from .push import async_check_push_port


class OTConductorConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ask for the OwnTone server address and check that it answers."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_PORT]
            push_port = user_input[CONF_PUSH_PORT]
            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()
            session = async_get_clientsession(self.hass)
            client = OwnToneClient(session, host, port)
            try:
                info = await client.get_config()
            except OwnToneError:
                errors["base"] = "cannot_connect"
            else:
                if push_port:  # 0 means push is off, so there is nothing to test
                    if info.get("websocket_port") == 0:
                        # OwnTone itself says its websocket is disabled.
                        errors[CONF_PUSH_PORT] = "push_disabled"
                    else:
                        try:
                            await async_check_push_port(session, host, push_port)
                        except OwnToneError:
                            errors[CONF_PUSH_PORT] = "cannot_connect_push"
                if not errors:
                    title = info.get("library_name") or f"OT Conductor ({host})"
                    return self.async_create_entry(
                        title=title,
                        data={
                            CONF_HOST: host,
                            CONF_PORT: port,
                            CONF_PUSH_PORT: push_port,
                        },
                    )

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                vol.Required(CONF_PUSH_PORT, default=DEFAULT_PUSH_PORT): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=65535)
                ),
            }
        )
        # Keep what the user typed when the form is shown again with an error.
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

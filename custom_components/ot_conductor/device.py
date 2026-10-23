"""Shared device description for a speaker (one OwnTone output)."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN


def output_device_info(entry_id: str, output: dict[str, Any]) -> DeviceInfo:
    """Every entity of a speaker uses this, so whichever loads first creates it."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry_id}_{output['id']}")},
        name=output["name"],
        model=output.get("type"),
        via_device=(DOMAIN, entry_id),
    )

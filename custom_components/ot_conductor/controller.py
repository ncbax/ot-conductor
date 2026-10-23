"""The logic behind the OT Conductor services."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from .api import OwnToneClient, OwnToneError
from .const import DOMAIN, MIN_FADE_INTERVAL, QUEUE_TIMEOUT

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .coordinator import OwnToneCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Target:
    """Something whose volume can be changed."""

    label: str
    key: str  # output id, or "master"
    output_id: str | None  # None means the master volume


MASTER = Target("master", "master", None)


async def _gather(coros: list) -> list:
    """Run coroutines concurrently; raise the first failure after all finish."""
    results = await asyncio.gather(*coros, return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result
    return results


def _timed_plan(
    start: int, target: int, duration: float, steps: int | None
) -> tuple[int, str | None]:
    """How many evenly spaced changes a timed fade makes for one output.

    By default one per volume point (the smoothest the volume can go), but
    never closer together than MIN_FADE_INTERVAL. Returns (steps, note), where
    the note explains a reduction of a steps value the caller asked for.
    """
    distance = abs(target - start)
    if distance == 0:
        return 0, None
    most = max(1, int(duration / MIN_FADE_INTERVAL))
    wanted = distance if steps is None else min(steps, distance)
    n = max(1, min(wanted, most))
    note = None
    if steps is not None and n < steps:
        note = (
            f"steps reduced from {steps} to {n} (the volume moves in whole points, "
            f"and no faster than one change every {MIN_FADE_INTERVAL:g} s)"
        )
    return n, note


def _queue_error_message(uri: str, err: OwnToneError) -> str:
    """Say which URI failed and why, in terms a person can act on."""
    if err.kind == "timeout":
        return (
            f"Timed out after {QUEUE_TIMEOUT:g} s waiting for OwnTone to add {uri} "
            "to the queue"
        )
    if err.kind == "unreachable":
        return f"Couldn't reach OwnTone to add {uri} to the queue ({err})"
    if err.kind == "http":
        text = f"OwnTone couldn't add {uri} to the queue (HTTP {err.status}"
        text += f": {err.detail})" if err.detail else ")"
        if err.status is not None and err.status >= 500:
            text += (
                ". That usually means OwnTone couldn't open the stream, so check the "
                "stream's source (for a relay, its log)"
            )
        return text
    return f"Couldn't add {uri} to the queue: {err}"


class OwnToneController:
    """Implements volume, play, stop and fade against one OwnTone server."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: OwnToneClient,
        coordinator: OwnToneCoordinator,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.client = client
        self.coordinator = coordinator
        self._fades: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------- helpers

    def _entity_key(self, value: str) -> str | None:
        """Output id (or "master") if value is one of this integration's entities."""
        value = value.strip()
        if "." not in value:
            return None
        entity = er.async_get(self.hass).async_get(value)
        if (
            entity is None
            or entity.platform != DOMAIN
            or entity.config_entry_id != self.entry.entry_id
        ):
            return None
        prefix = f"{self.entry.entry_id}_"
        if not entity.unique_id.startswith(prefix):
            return None
        # Unique IDs are "<entry>_<output id>", or "<entry>_master".
        return entity.unique_id[len(prefix):]

    def _wants_all(self, names: list[str]) -> bool:
        """True for "all" or the Master media player entity."""
        return any(
            n.strip().casefold() == "all" or self._entity_key(n) == "master"
            for n in names
        )

    async def _cached_outputs(self, *, refresh: bool = False) -> dict[str, dict]:
        """The cached output list; refresh=True fetches it again first."""
        if refresh:
            await self.coordinator.async_refresh()
        return self.coordinator.data["outputs"]

    async def _resolve(self, names: list[str]) -> list[tuple[str, str]]:
        """Turn entity IDs, output names or output IDs into [(name, id)]."""

        def attempt(outputs: dict[str, dict]):
            by_name = {str(o["name"]).casefold(): o for o in outputs.values()}
            found: dict[str, str] = {}
            unknown: list[str] = []
            for raw in names:
                name = raw.strip()
                key = self._entity_key(name) or name
                output = outputs.get(key) or by_name.get(key.casefold())
                if output is None:
                    unknown.append(name)
                else:
                    found[str(output["id"])] = str(output["name"])
            return found, unknown

        outputs = await self._cached_outputs()
        found, unknown = attempt(outputs)
        if unknown:  # maybe a speaker was added since the last poll
            outputs = await self._cached_outputs(refresh=True)
            found, unknown = attempt(outputs)
        if unknown:
            available = ", ".join(sorted(str(o["name"]) for o in outputs.values()))
            raise ServiceValidationError(
                f"Unknown output(s): {', '.join(unknown)}. "
                f"Available: {available or 'none'}"
            )
        return [(name, oid) for oid, name in found.items()]

    async def _targets(self, names: list[str]) -> list[Target]:
        if self._wants_all(names):
            return [MASTER]
        return [Target(name, oid, oid) for name, oid in await self._resolve(names)]

    def _cancel_fades(self, keys) -> None:
        for key in list(keys):
            task = self._fades.pop(key, None)
            if task is not None:
                task.cancel()

    def cancel_all_fades(self) -> None:
        self._cancel_fades(list(self._fades))

    async def _apply_volume(
        self, targets: list[Target], volume: int | None, step: int | None
    ) -> None:
        await _gather(
            [
                self.client.set_volume(volume=volume, step=step, output_id=t.output_id)
                for t in targets
            ]
        )

    # ------------------------------------------------------------- services

    async def async_set_volume(
        self, outputs: list[str], *, volume: int | None, step: int | None
    ) -> dict[str, Any]:
        targets = await self._targets(outputs)
        self._cancel_fades(t.key for t in targets)
        await self._apply_volume(targets, volume, step)
        await self.coordinator.async_request_refresh()
        return {"outputs": [t.label for t in targets]}

    async def _add_to_queue(self, uri: str, **kwargs: Any) -> None:
        """queue_add, with an error that names the URI and the reason."""
        try:
            await self.client.queue_add(uri, **kwargs)
        except OwnToneError as err:
            raise OwnToneError(
                _queue_error_message(uri, err),
                status=err.status,
                detail=err.detail,
                kind=err.kind,
            ) from err

    async def async_play(
        self,
        uri: str,
        *,
        outputs: list[str] | None,
        volume: int | None,
        clear: bool,
        start: bool,
        raise_error: bool = True,
    ) -> dict[str, Any]:
        """Optionally select outputs and set volume, then queue the URI.

        With raise_error=False a failure talking to OwnTone doesn't raise: the
        result says what failed ({"ok": False, "stage": ..., "error": ...}) so a
        script can decide what to do. Problems with the inputs, such as an
        unknown speaker, always raise.
        """
        selected: list[str] = []
        stage = "select_outputs"
        try:
            volume_targets: list[Target] = []
            if outputs:
                if self._wants_all(outputs):
                    resolved = [
                        (str(o["name"]), str(o["id"]))
                        for o in (await self._cached_outputs(refresh=True)).values()
                    ]
                    volume_targets = [MASTER]
                else:
                    resolved = await self._resolve(outputs)
                    volume_targets = [Target(n, i, i) for n, i in resolved]
                if not resolved:
                    raise ServiceValidationError("OwnTone reports no outputs to select")
                await self.client.set_outputs([oid for _, oid in resolved])
                selected = [name for name, _ in resolved]
            elif volume is not None:
                volume_targets = [MASTER]  # no outputs given: use master volume

            if volume is not None:
                stage = "set_volume"
                self._cancel_fades(t.key for t in volume_targets)
                await self._apply_volume(volume_targets, volume, None)

            stage = "queue_add"
            await self._add_to_queue(uri, clear=clear, start=start)
        except OwnToneError as err:
            await self.coordinator.async_request_refresh()  # state may have changed
            if raise_error:
                raise
            return {
                "ok": False,
                "stage": stage,
                "error": str(err),
                "kind": err.kind,
                "status": err.status,
                "detail": err.detail,
                "uri": uri,
                "outputs": selected,
                "volume": volume,
            }
        await self.coordinator.async_request_refresh()
        return {"ok": True, "outputs": selected, "volume": volume, "uri": uri}

    async def _change_selection(
        self, names: list[str], *, add: bool, stop_if_empty: bool = True
    ) -> dict[str, Any]:
        """Add outputs to, or remove them from, the current selection."""
        live = await self.client.get_outputs()  # fresh: selection changes often
        live_names = {str(o["id"]): str(o["name"]) for o in live}
        if self._wants_all(names):
            targets = dict(live_names)
        else:
            targets = {oid: name for name, oid in await self._resolve(names)}
            gone = [name for oid, name in targets.items() if oid not in live_names]
            if gone:
                raise ServiceValidationError(
                    f"Output(s) not currently available: {', '.join(gone)}"
                )

        selected = [str(o["id"]) for o in live if o.get("selected")]
        if add:
            new = selected + [oid for oid in targets if oid not in selected]
        else:
            new = [oid for oid in selected if oid not in targets]

        stopped = False
        if new != selected:  # skip the call if nothing would change
            await self.client.set_outputs(new)
            if stop_if_empty and selected and not new:
                # OwnTone keeps "playing" to no speakers; stop it instead.
                await self.client.stop()
                stopped = True
            await self.coordinator.async_refresh()  # switches should update at once
        changed = [oid for oid in new if oid not in selected] if add else [
            oid for oid in selected if oid not in new
        ]
        return {
            "selected": [live_names[oid] for oid in new],
            "added" if add else "removed": [live_names[oid] for oid in changed],
            "stopped": stopped,
        }

    async def async_add_outputs(self, outputs: list[str]) -> dict[str, Any]:
        return await self._change_selection(outputs, add=True)

    async def async_remove_outputs(
        self, outputs: list[str], *, stop_playback: bool = True
    ) -> dict[str, Any]:
        return await self._change_selection(
            outputs, add=False, stop_if_empty=stop_playback
        )

    async def async_enqueue(self, uri: str, mode: Any = None) -> dict[str, Any]:
        """play_media from a media player: replace (default), add, next or play."""
        mode = str(mode or "replace").lower()
        clear, start, position, from_position = True, True, None, None
        if mode in ("add", "next", "play"):
            clear = False
            start = mode == "play"
            now = self.coordinator.data.get("now_playing")
            if mode != "add" and now and now.get("position") is not None:
                position = int(now["position"]) + 1  # right after the current item
                from_position = position if mode == "play" else None
        await self._add_to_queue(
            uri, clear=clear, start=start, position=position, from_position=from_position
        )
        await self.coordinator.async_request_refresh()
        return {"uri": uri, "mode": mode}

    async def async_stop(self) -> dict[str, Any]:
        await self.client.stop()
        return {}

    async def async_pause(self) -> dict[str, Any]:
        """Pause playback, keeping the place in the queue."""
        await self.client.player_command("pause")
        await self.coordinator.async_request_refresh()
        return {}

    async def async_resume(self) -> dict[str, Any]:
        """Start or continue playing what is already in the queue."""
        await self.client.player_command("play")
        await self.coordinator.async_request_refresh()
        return {}

    async def async_fade(
        self,
        outputs: list[str],
        *,
        target: int,
        steps: int | None = None,
        interval: float | None = None,
        start: int | None = None,
        wait: bool = False,
        duration: float | None = None,
    ) -> dict[str, Any]:
        """Fade to `target`, in one of two ways.

        With a duration (seconds, above zero) the timing is worked out so the
        fade takes exactly that long, in evenly spaced changes. Otherwise it
        makes `steps` changes (default 10) `interval` seconds apart (default 1).
        """
        timed = duration is not None and duration > 0
        if timed and interval is not None:
            raise ServiceValidationError(
                "Use either a duration or an interval, not both: a duration "
                "works out the interval for you"
            )
        if not timed:
            steps = 10 if steps is None else steps
            interval = 1.0 if interval is None else interval

        targets = await self._targets(outputs)
        self._cancel_fades(t.key for t in targets)  # supersede running fades
        current = {} if start is not None else await self._read_volumes(targets)

        plans = []  # (target, begin volume, number of changes, task)
        notes: list[str] = []
        for t in targets:
            begin = start if start is not None else current[t.key]
            if timed:
                count, note = _timed_plan(begin, target, duration, steps)
                if note:
                    notes.append(f"{t.label}: {note}")
                coro = self._run_timed_fade(t.output_id, begin, target, duration, count)
            else:
                count = steps
                coro = self._run_fade(t.output_id, begin, target, steps, interval)
            task = self.entry.async_create_background_task(
                self.hass, coro, name=f"ot_conductor fade {t.label}"
            )
            self._fades[t.key] = task
            task.add_done_callback(self._make_done_callback(t))
            plans.append((t, begin, count, task))

        if timed:
            summary = [
                {
                    "output": t.label,
                    "from": b,
                    "to": target,
                    "steps": n,
                    "interval": round(duration / n, 3) if n else None,
                }
                for t, b, n, _ in plans
            ]
        else:
            summary = [{"output": t.label, "from": b, "to": target} for t, b, _, _ in plans]

        if not wait:
            if timed:
                result: dict[str, Any] = {
                    "started": summary,
                    "duration_seconds": round(duration, 2),
                }
            else:
                result = {
                    "started": summary,
                    "steps": steps,
                    "interval": interval,
                    "duration_seconds": round((steps - 1) * interval, 2),
                }
            if notes:
                result["notes"] = notes
            return result

        results = await asyncio.gather(*(p[3] for p in plans), return_exceptions=True)
        cancelled = []
        for (t, _, _, _), outcome in zip(plans, results, strict=True):
            if isinstance(outcome, asyncio.CancelledError):
                cancelled.append(t.label)
            elif isinstance(outcome, BaseException):
                raise outcome
        result = {"completed": summary, "cancelled": cancelled}
        if notes:
            result["notes"] = notes
        return result

    # --------------------------------------------------------------- fading

    async def _read_volumes(self, targets: list[Target]) -> dict[str, int]:
        volumes: dict[str, int] = {}
        outputs: dict[str, dict] | None = None
        for t in targets:
            if t.output_id is None:
                volumes[t.key] = int((await self.client.get_player()).get("volume", 0))
            else:
                if outputs is None:
                    outputs = {
                        str(o["id"]): o for o in await self.client.get_outputs()
                    }
                volumes[t.key] = int(outputs[t.key].get("volume", 0))
        return volumes

    async def _run_timed_fade(
        self,
        output_id: str | None,
        start: int,
        target: int,
        duration: float,
        steps: int,
    ) -> None:
        """Make `steps` evenly spaced changes that end exactly at `duration`.

        Each change is scheduled against a clock (not "wait, then send"), so slow
        responses from a speaker don't stretch the fade.
        """
        if steps <= 0:
            return
        loop = asyncio.get_running_loop()
        began = loop.time()
        for k in range(1, steps + 1):
            delay = began + duration * k / steps - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            value = round(start + (target - start) * k / steps)
            await self.client.set_volume(volume=value, output_id=output_id)
        await self.coordinator.async_request_refresh()

    async def _run_fade(
        self, output_id: str | None, start: int, target: int, steps: int, interval: float
    ) -> None:
        if start == target:
            return
        last = start
        for i in range(1, steps + 1):
            value = round(start + (target - start) * i / steps)
            if value != last:  # skip redundant calls when range < steps
                await self.client.set_volume(volume=value, output_id=output_id)
                last = value
            if i < steps:
                await asyncio.sleep(interval)
        await self.coordinator.async_request_refresh()

    def _make_done_callback(self, target: Target):
        def _done(task: asyncio.Task) -> None:
            if self._fades.get(target.key) is task:
                del self._fades[target.key]
            if not task.cancelled() and (exc := task.exception()) is not None:
                _LOGGER.warning("Fade on %s failed: %s", target.label, exc)

        return _done


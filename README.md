<p align="center">
  <img src="https://raw.githubusercontent.com/ncbax/ot-conductor/main/custom_components/ot_conductor/brand/icon@2x.png" width="128" alt="OT Conductor icon">
</p>

# OT Conductor

A Home Assistant integration for [OwnTone](https://owntone.github.io/owntone-server/), the open-source music server that plays to AirPlay and other speakers.

It gives you a **media player for the server and one for every speaker**, plus actions to play a stream URL on chosen speakers, pause, resume or stop, set volume, fade volume over a set time, and add or remove speakers, with instant updates when OwnTone's websocket is available.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ncbax&repository=ot-conductor&category=integration)

> This is an unofficial community project. It is not affiliated with or endorsed by OwnTone.

## What it's for

Built for setups like: play an internet stream (a radio station, or a [relay](https://github.com/ncbax/yt-audio-relay) of a YouTube live stream) on a few AirPlay speakers, fade it out over 30 minutes at bedtime, and get a notification when a stream fails to start.

## Written By

I'm a pretty cruddy programmer, and I really don't have the skills or time to develop something like this myself. I had Claude.ai write this code under my direction. I'm putting it here if anyone else finds it useful.

## Features

- **Master media player** with play, pause, stop, next, previous, seek, shuffle, repeat, volume, clear queue, and `play_media` (replace, add, play next, or play now). It shows the current track, artist, album, and artwork.
- **A media player per speaker.** Turning one on or off selects or deselects that output in OwnTone; each has its own volume. New speakers appear automatically.
- **Play audio** on specific speakers, optionally setting the volume first, with a way to **handle failures in your script**.
- **Fade volume** up or down, either over a **total duration** you choose (the timing is worked out for you) or in a set number of steps.
- **Add / remove speakers** without disturbing the others.
- **Push updates with polling fallback.** Changes show up straight away over OwnTone's websocket; if it isn't available the integration polls instead.

## Requirements

- An OwnTone server reachable from Home Assistant (developed against OwnTone 29.3).
- Home Assistant 2026.9 or newer (the version it was developed and tested on).
- Optional, for instant updates: OwnTone's websocket enabled. It is controlled by `websocket_port` in OwnTone's `owntone.conf` (3688 by default) and must be enabled there.

## Installation

### HACS (recommended)

1. Install [HACS](https://hacs.xyz/) if you haven't already.
2. Click the badge above, or in HACS open the **⋮ menu → Custom repositories**, add `https://github.com/ncbax/ot-conductor` and choose the type **Integration**.
3. Find **OT Conductor** in HACS, **Download** it, and **restart Home Assistant**.
4. Go to **Settings → Devices & services → Add integration** and choose **OT Conductor**.

### Manual

Copy the `custom_components/ot_conductor` folder from this repository into your Home Assistant `config/custom_components/` folder, restart, then add the integration as in step 4 above.

## Setup

The setup form asks for:

| Field | Default | Meaning |
|---|---|---|
| Host | | IP address or hostname of the OwnTone server |
| Port | 3689 | OwnTone's web/API port |
| Push updates port | 3688 | OwnTone's websocket port. Enter `0` to turn push updates off and use polling only |

The form checks that OwnTone answers, then tests the push port with a real connection. If that test fails, either enable `websocket_port` in `owntone.conf` and restart OwnTone, correct the port, or enter `0`.

While push updates work, the integration polls only every 5 minutes as a safety net (it also finds newly discovered speakers). Without push it polls every 15 seconds. The master player's `update_mode` attribute shows which one is active.

## What you get

| Entity | What it is |
|---|---|
| Master media player | OwnTone's player: playback, queue, master volume, now-playing information. Attributes: `selected_outputs`, `update_mode`. |
| One media player per speaker | **On** = the speaker is selected, **off** = it isn't. Volume per speaker. While selected, it shows what the player is playing. |

The entity IDs are generated from names, for example `media_player.ot_conductor_master` and `media_player.<speaker name>`, so check **Settings → Entities** for the real ones. Turning off the last selected speaker also stops playback.

## Actions

Every action returns a result you can capture with `response_variable`. Wherever an action asks for **outputs**, pick speakers from the list in the UI. The **Master** media player means *all* outputs. In YAML you can also give speaker names or IDs, or `all`.

| Action | What it does | Main fields |
|---|---|---|
| `ot_conductor.play` | Optionally selects speakers and sets volume, then plays a URI | `uri`, `outputs`, `volume`, `clear`, `start`, `raise_error` |
| `ot_conductor.stop` | Stops playback | |
| `ot_conductor.pause` | Pauses playback, keeping your place in the queue (`stop` ends playback instead) | |
| `ot_conductor.resume` | Continues playing what is in the queue, from where `pause` left off | |
| `ot_conductor.set_volume` | Sets or steps volume on several speakers at once | `outputs`, and `volume` (0-100) **or** `step` (-100 to 100) |
| `ot_conductor.fade` | Fades volume to a target | `outputs`, `to`, `from`, `duration` **or** `steps` and `interval`, `wait` |
| `ot_conductor.add_outputs` | Turns on speakers, leaving the others as they are | `outputs` |
| `ot_conductor.remove_outputs` | Turns off speakers, leaving the others as they are | `outputs`, `stop_playback` |

A **URI** for `play` can be a stream URL, or an OwnTone library item such as a playlist, artist, or album.

### Fading

Give a **total duration** and OT Conductor works out the timing so the volume moves evenly and arrives exactly when the time is up. By default it uses one step per volume point, never faster than one change per 0.5 seconds. Each speaker is calculated from its own starting volume, so they all finish together. Without a duration, `steps` (default 10) and `interval` (default 1 second) apply. A duration and an interval can't be used together.

```yaml
- action: ot_conductor.fade
  data:
    outputs: [media_player.living_room, media_player.kitchen]
    to: 0
    duration: "00:30:00"
    wait: true
- action: ot_conductor.pause   # or ot_conductor.stop
```

With `wait: true` the action holds for the whole fade. Without it the fade runs in the background. Starting a new fade, or setting the volume, on a speaker cancels the fade running there. Fades don't survive a Home Assistant restart. Pausing or stopping doesn't cancel a running fade.

### Handling a failed play

By default, if OwnTone can't add the stream, `play` stops the script with an error that names the URI, the HTTP status and OwnTone's message. Turn **Raise error on failure** off and the action finishes normally and sets the response variable to `ok: false` instead, so the script can decide what to do:

```yaml
sequence:
  - action: ot_conductor.play
    data:
      uri: "http://relay.local:8000/lofi.mp3"
      outputs: [media_player.living_room]
      raise_error: false
    response_variable: result
  - if: "{{ result is not defined or not result.ok }}"
    then:
      - action: notify.notify
        data:
          message: >-
            Stream failed: {{ result.error if result is defined else 'no result was returned' }}
```

The failed result includes `stage` (`select_outputs`, `set_volume` or `queue_add`), `error`, `kind` (`http`, `timeout` or `unreachable`), `status`, `detail`, and the `uri`. Problems with the inputs, such as an unknown speaker, always raise an error.

## Troubleshooting

| Problem | What to try |
|---|---|
| The setup form says it can't connect to the push port | Enable `websocket_port` in `owntone.conf` and restart OwnTone, check that the port is reachable from Home Assistant, or enter `0` to use polling only. |
| The form says OwnTone's websocket is disabled | OwnTone reports no websocket port. Enable it in `owntone.conf`, or enter `0`. |
| `update_mode` says `polling` | The websocket isn't connected. Check the Home Assistant log for "Push updates unavailable". |
| "Unknown output(s)" | The speaker name or ID isn't one OwnTone currently reports. The error lists the available names. |
| A speaker's state looks out of date | Changes made outside Home Assistant show up on the next update: instantly with push, within 15 seconds to 5 minutes otherwise. |
| `play` fails with an HTTP 500 | OwnTone couldn't open the stream. Check the stream's source; for a relay, look at its log. |

To see more detail, add this to `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.ot_conductor: debug
```

## Limitations

- One OwnTone server per Home Assistant.
- OwnTone has a single player and queue shared by all selected speakers, so speakers can't play different things.
- Fades run inside Home Assistant and stop if it restarts.
- No media browser.

## Related projects

- [yt-audio-relay](https://github.com/ncbax/yt-audio-relay): turns YouTube live streams into stable MP3 URLs that OwnTone can play.

## Contributing

Bug reports are welcome in the [issue tracker](https://github.com/ncbax/ot-conductor/issues). Please include your Home Assistant and OwnTone versions and the relevant lines from the log.

## License

MIT. See [LICENSE](LICENSE).

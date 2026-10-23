"""Constants for OT Conductor."""

DOMAIN = "ot_conductor"

DEFAULT_PORT = 3689

# OwnTone's websocket (push) port; set by websocket_port in owntone.conf.
CONF_PUSH_PORT = "push_port"
DEFAULT_PUSH_PORT = 3688

# Seconds. Adding a stream to the queue can be slow because the relay may need
# several seconds to resolve the YouTube URL, so it gets a longer timeout.
REQUEST_TIMEOUT = 10
QUEUE_TIMEOUT = 60

SERVICE_SET_VOLUME = "set_volume"
SERVICE_PLAY = "play"
SERVICE_STOP = "stop"
SERVICE_PAUSE = "pause"
SERVICE_RESUME = "resume"
SERVICE_ADD_OUTPUTS = "add_outputs"
SERVICE_REMOVE_OUTPUTS = "remove_outputs"
SERVICE_FADE = "fade"

ATTR_OUTPUTS = "outputs"
ATTR_VOLUME = "volume"
ATTR_STEP = "step"
ATTR_URI = "uri"
ATTR_CLEAR = "clear"
ATTR_START = "start"
ATTR_RAISE_ERROR = "raise_error"
ATTR_TO = "to"
ATTR_FROM = "from"
ATTR_STEPS = "steps"
ATTR_INTERVAL = "interval"
ATTR_DURATION = "duration"
ATTR_WAIT = "wait"
ATTR_STOP_PLAYBACK = "stop_playback"

# Polling intervals (seconds). While push updates are connected, polling only
# runs as a slow safety net (it also finds newly discovered speakers); when the
# push connection is down it speeds up so the entities stay reasonably fresh.
POLL_INTERVAL_PUSH = 300
POLL_INTERVAL_FALLBACK = 15

# A timed fade never changes the volume more often than this (seconds).
MIN_FADE_INTERVAL = 0.5

# A burst of notifications (a fade sends one per step) becomes one refresh.
REFRESH_COOLDOWN = 0.5

# OwnTone websocket notifications we subscribe to.
PUSH_EVENTS = ["player", "outputs", "volume", "queue", "options"]
PUSH_HEARTBEAT = 30
PUSH_RECONNECT_MIN = 5
PUSH_RECONNECT_MAX = 300
PUSH_RECHECK = 600  # when websockets are disabled, look again this often

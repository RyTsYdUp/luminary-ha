DOMAIN = "luminary_ha"

# Config entry keys — set during config flow, stored in entry.data
CONF_AREA = "area_id"        # HA area registry ID (selected in config flow)
CONF_ZONE_NAME = "zone_name" # human name derived from area (e.g. "Hallway")
CONF_ZONE_ID = "zone_id"     # slug derived from area name (e.g. "hallway")
CONF_SENSORS = "sensors"
CONF_LIGHT = "light"
CONF_SWITCH_DEVICE = "switch_device"
CONF_SENSOR_HW_TIMEOUTS = "sensor_hw_timeouts"  # dict[str, dict] — see hw_timeout.py

# Motion/occupancy sensor device classes accepted when picking zone sensors. Zigbee2MQTT
# PIR sensors report device_class "occupancy", not "motion" — both must be accepted or
# every Zigbee sensor in the house is silently unselectable.
MOTION_DEVICE_CLASSES = ["motion", "occupancy"]

# Daytime suppression modes
DAYTIME_MODE_SUN = "Sun Elevation"
DAYTIME_MODE_LUX = "Lux Sensor"
DAYTIME_MODES = [DAYTIME_MODE_SUN, DAYTIME_MODE_LUX]

# Entity defaults (used when entities are first created)
DEFAULT_LIGHT_ON_TIME = 60
DEFAULT_DIM_BRIGHTNESS = 10
DEFAULT_NORMAL_BRIGHTNESS = 100
DEFAULT_SUN_ELEVATION = 3.0
DEFAULT_LUX_THRESHOLD = 50
DEFAULT_DIM_START = "00:00:00"
DEFAULT_DIM_END = "06:00:00"
DEFAULT_DAYTIME_MODE = DAYTIME_MODE_SUN

# Z-Wave Central Scene values (string labels as sent by zwave_js_value_notification)
ZWAVE_SCENE_VALUE_SINGLE = "KeyPressed"
ZWAVE_SCENE_VALUE_DOUBLE = "KeyPressed2x"
ZWAVE_SCENE_KEY_UP = "001"
ZWAVE_SCENE_KEY_DOWN = "002"
ZWAVE_SCENE_KEY_CONFIG = "003"

# --- Hardware motion-clear-timeout auto-detection (hw_timeout.py) ---
#
# Z-Wave JS Configuration CC parameter entities have unique_ids shaped
# "{homeId}.{nodeId}-112-0-{paramNumber}" (112 = Configuration CC, endpoint 0).
# Confirmed against a real Zooz ZSE11 (pantry): param 13 unique_id was exactly
# "4246878805.48-112-0-13", original_name "Motion Detection: Timeout", and disabled
# by default (disabled_by="integration") — true for every Configuration CC entity on
# that device, not just param 13.
ZWAVE_CONFIG_CC = "112"
ZWAVE_CONFIG_PARAM_TIMEOUT_MAP: dict[tuple[str, str], int] = {
    ("Zooz", "ZSE11"): 13,
}
ZWAVE_TIMEOUT_KEYWORD_HINTS = ("timeout", "duration")  # fallback only for unmapped (manufacturer, model)

# Zigbee2MQTT (via HA's mqtt platform) entity unique_ids are shaped
# "{ieeeAddr}_{property_key}_zigbee2mqtt". Confirmed against a real Philips Hue motion
# sensor (laundry room): "0x0017880109197d51_occupancy_timeout_zigbee2mqtt", enabled by
# default (unlike Z-Wave Configuration entities).
ZIGBEE2MQTT_UNIQUE_ID_SUFFIX = "_zigbee2mqtt"
ZIGBEE2MQTT_TIMEOUT_PROPERTY_KEYS = ("occupancy_timeout",)

# --- Dead/stuck-sensor detection via last_seen staleness (hw_timeout.py) ---
#
# Confirmed against real hardware (2026-07-25 motion-capture analysis): a Zooz ZSE11
# with a failing battery held its binary_sensor "on" for 4+ days while never once
# reporting "unavailable" — Z-Wave JS has no way to distinguish a dead sleeping node
# from one between wake cycles. Its "Last Seen" diagnostic entity (enabled by default,
# unlike Configuration CC hw-timeout entities) simply stopped advancing the moment the
# node went silent — a far earlier and more reliable signal than either the sensor's
# own state or an "unavailable" watch.
DEFAULT_STALE_SENSOR_MINUTES = 60  # default notify-if-silent-longer-than threshold
STALE_CHECK_INTERVAL_SEC = 300  # how often the coordinator re-checks last_seen age

# --- Switch-triggered hold (coordinator.py _run_switch_on_sequence) ---
#
# 2026-08-02 hallway incident: a non-Central-Scene companion/add-on switch on the
# same circuit turned the light on (restoring a stale dim level) with nothing
# watching to ever turn it back off, since daytime motion automation is
# intentionally inert. Any switch-triggered on (main paddle single-tap or a
# companion switch) now always goes to normal_brightness and starts this timer,
# regardless of time of day. Only Disabled (double-tap) mode is exempt.
DEFAULT_SWITCH_ON_TIMEOUT_MINUTES = 60

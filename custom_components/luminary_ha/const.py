DOMAIN = "luminary_ha"

# Config entry keys — set during config flow, stored in entry.data
CONF_AREA = "area_id"        # HA area registry ID (selected in config flow)
CONF_ZONE_NAME = "zone_name" # human name derived from area (e.g. "Hallway")
CONF_ZONE_ID = "zone_id"     # slug derived from area name (e.g. "hallway")
CONF_SENSORS = "sensors"
CONF_LIGHT = "light"
CONF_SWITCH_DEVICE = "switch_device"

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

# Z-Wave Central Scene values
ZWAVE_SCENE_VALUE_SINGLE = 0
ZWAVE_SCENE_VALUE_DOUBLE = 3
ZWAVE_SCENE_KEY_UP = "001"
ZWAVE_SCENE_KEY_DOWN = "002"
ZWAVE_SCENE_KEY_CONFIG = "003"

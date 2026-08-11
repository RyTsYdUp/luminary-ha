from __future__ import annotations

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_AREA,
    CONF_LIGHT,
    CONF_SENSORS,
    CONF_SWITCH_DEVICE,
    CONF_ZONE_ID,
    CONF_ZONE_NAME,
    DOMAIN,
)
from .coordinator import ZoneCoordinator

# Diagnostics downloads get pasted verbatim into bug reports, and this dumps every
# attribute of the configured sensors and light — whatever the owning integration
# happens to put there. Redact the identifying/locating keys that commonly show up
# on Z-Wave/Zigbee/MQTT entities; none of them are needed to debug Luminary itself.
TO_REDACT = {
    "latitude",
    "longitude",
    "gps",
    "ip_address",
    "mac",
    "mac_address",
    "serial_number",
    "node_id",
    "home_id",
    "ieee",
    "device_id",
    "unique_id",
    "access_token",
    "entity_picture",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict:
    sensors = entry.options.get(CONF_SENSORS, [])
    configured_light = entry.options.get(CONF_LIGHT)
    # Same string-or-list normalisation as ZoneCoordinator.lights, done locally so
    # diagnostics still renders when the coordinator isn't registered (mid-setup).
    if not configured_light:
        lights = []
    elif isinstance(configured_light, str):
        lights = [configured_light]
    else:
        lights = list(configured_light)
    switch_device = entry.options.get(CONF_SWITCH_DEVICE)
    coordinator: ZoneCoordinator | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)

    def _state(entity_id: str) -> dict:
        state = hass.states.get(entity_id)
        if state is None:
            return {"state": "unavailable"}
        return {
            "state": state.state,
            "attributes": async_redact_data(dict(state.attributes), TO_REDACT),
            "last_changed": state.last_changed.isoformat(),
        }

    hw_timeouts = {}
    stale_sensors = {}
    light_on_time_floor = None
    light_on_time_sec = None
    if coordinator is not None:
        for entity_id in sensors:
            info = coordinator.sensor_hw_timeout_info(entity_id)
            source_entity_id = info.get("source_entity_id")
            hw_timeouts[entity_id] = {
                "stored_baseline_sec": info.get("timeout_sec"),
                "live_value_sec": coordinator.sensor_hw_timeout(entity_id),
                "source": info.get("source"),
                "source_entity_id": source_entity_id,
                "source_state": _state(source_entity_id) if source_entity_id else None,
            }
            last_seen_source = coordinator._last_seen_source_map.get(entity_id)
            stale_seconds = coordinator.sensor_stale_seconds(entity_id)
            stale_sensors[entity_id] = {
                "last_seen_source_entity_id": last_seen_source,
                "stale_seconds": stale_seconds,
                "threshold_seconds": coordinator.stale_sensor_threshold_sec,
                "is_stale": (
                    stale_seconds is not None and stale_seconds > coordinator.stale_sensor_threshold_sec
                ),
            }
        light_on_time_floor = coordinator.max_sensor_hw_timeout()
        light_on_time_sec = coordinator.light_on_time_sec

    return {
        "zone": {
            "name": entry.data.get(CONF_ZONE_NAME),
            "id": entry.data.get(CONF_ZONE_ID),
            "area_id": entry.data.get(CONF_AREA),
        },
        "config": {
            "sensors": sensors,
            "lights": lights,
            "switch_device": switch_device,
        },
        "live_state": {
            "sensors": {entity_id: _state(entity_id) for entity_id in sensors},
            "lights": {entity_id: _state(entity_id) for entity_id in lights},
        },
        "hw_timeouts": hw_timeouts,
        "stale_sensors": stale_sensors,
        "light_on_time_floor": light_on_time_floor,
        "light_on_time_sec": light_on_time_sec,
    }

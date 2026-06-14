from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_AREA,
    CONF_LIGHT,
    CONF_SENSORS,
    CONF_SWITCH_DEVICE,
    CONF_ZONE_ID,
    CONF_ZONE_NAME,
)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict:
    sensors = entry.options.get(CONF_SENSORS, [])
    light = entry.options.get(CONF_LIGHT)
    switch_device = entry.options.get(CONF_SWITCH_DEVICE)

    def _state(entity_id: str) -> dict:
        state = hass.states.get(entity_id)
        if state is None:
            return {"state": "unavailable"}
        return {
            "state": state.state,
            "attributes": dict(state.attributes),
            "last_changed": state.last_changed.isoformat(),
        }

    return {
        "zone": {
            "name": entry.data.get(CONF_ZONE_NAME),
            "id": entry.data.get(CONF_ZONE_ID),
            "area_id": entry.data.get(CONF_AREA),
        },
        "config": {
            "sensors": sensors,
            "light": light,
            "switch_device": switch_device,
        },
        "live_state": {
            "sensors": {entity_id: _state(entity_id) for entity_id in sensors},
            "light": _state(light) if light else None,
        },
    }

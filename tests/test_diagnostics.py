"""Tests for diagnostics.py's hw_timeouts section."""
from __future__ import annotations

from custom_components.luminary_ha import diagnostics
from custom_components.luminary_ha.const import CONF_SENSOR_HW_TIMEOUTS, DOMAIN
from custom_components.luminary_ha.coordinator import ZoneCoordinator

from tests.test_coordinator import SENSOR_1, _make_entry, _make_hass


async def test_diagnostics_includes_hw_timeouts_and_floor():
    entry = _make_entry()
    entry.options[CONF_SENSOR_HW_TIMEOUTS] = {
        SENSOR_1: {"timeout_sec": None, "source": "zwave", "source_entity_id": "number.src_1"},
    }
    hass = _make_hass()
    hass.states.put("number.src_1", "13")
    hass.states.put(f"number.{entry.data['zone_id']}_light_on_time_sec", "60")
    coord = ZoneCoordinator(hass, entry)
    hass.data = {DOMAIN: {entry.entry_id: coord}}

    result = await diagnostics.async_get_config_entry_diagnostics(hass, entry)

    assert result["hw_timeouts"][SENSOR_1]["live_value_sec"] == 13.0
    assert result["hw_timeouts"][SENSOR_1]["source"] == "zwave"
    assert result["hw_timeouts"][SENSOR_1]["source_state"]["state"] == "13"
    assert result["light_on_time_floor"] == 13.0
    assert result["light_on_time_sec"] == 60


async def test_diagnostics_handles_missing_coordinator():
    entry = _make_entry()
    hass = _make_hass()
    hass.data = {}  # coordinator not registered (e.g. mid-setup)

    result = await diagnostics.async_get_config_entry_diagnostics(hass, entry)

    assert result["hw_timeouts"] == {}
    assert result["stale_sensors"] == {}
    assert result["light_on_time_floor"] is None


async def test_diagnostics_includes_stale_sensors():
    entry = _make_entry()
    hass = _make_hass()
    coord = ZoneCoordinator(hass, entry)
    coord._last_seen_source_map = {SENSOR_1: "sensor.src_last_seen"}
    hass.states.put("sensor.src_last_seen", "2000-01-01T00:00:00+00:00")  # ancient -> stale
    hass.data = {DOMAIN: {entry.entry_id: coord}}

    result = await diagnostics.async_get_config_entry_diagnostics(hass, entry)

    entry_diag = result["stale_sensors"][SENSOR_1]
    assert entry_diag["last_seen_source_entity_id"] == "sensor.src_last_seen"
    assert entry_diag["is_stale"] is True
    assert entry_diag["stale_seconds"] > 365 * 24 * 3600

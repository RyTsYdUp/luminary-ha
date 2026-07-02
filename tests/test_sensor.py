"""Unit tests for sensor.py's per-sensor Motion Hardware Timeout entities."""
from __future__ import annotations

import asyncio

from custom_components.luminary_ha import sensor as sensor_module
from custom_components.luminary_ha.const import CONF_SENSOR_HW_TIMEOUTS, DOMAIN
from custom_components.luminary_ha.coordinator import ZoneCoordinator
from custom_components.luminary_ha.sensor import LuminaryMotionHwTimeoutSensor

from tests.test_coordinator import SENSOR_1, SENSOR_2, _make_entry, _make_hass


def test_native_value_reads_through_coordinator():
    entry = _make_entry()
    entry.options[CONF_SENSOR_HW_TIMEOUTS] = {
        SENSOR_1: {"timeout_sec": 20.0, "source": "manual", "source_entity_id": None}
    }
    coord = ZoneCoordinator(_make_hass(), entry)
    entity = LuminaryMotionHwTimeoutSensor(coord, SENSOR_1)

    assert entity.native_value == 20.0
    assert entity.monitored_sensor == SENSOR_1


def test_native_value_none_when_undetected():
    coord = ZoneCoordinator(_make_hass(), _make_entry())
    entity = LuminaryMotionHwTimeoutSensor(coord, SENSOR_1)
    assert entity.native_value is None


def test_extra_state_attributes_reflect_source_info():
    entry = _make_entry()
    entry.options[CONF_SENSOR_HW_TIMEOUTS] = {
        SENSOR_1: {"timeout_sec": None, "source": "zwave", "source_entity_id": "number.src_1"}
    }
    coord = ZoneCoordinator(_make_hass(), entry)
    entity = LuminaryMotionHwTimeoutSensor(coord, SENSOR_1)

    attrs = entity.extra_state_attributes
    assert attrs["monitored_sensor"] == SENSOR_1
    assert attrs["source"] == "zwave"
    assert attrs["source_entity_id"] == "number.src_1"


def test_unique_id_and_name_derived_from_sensor():
    coord = ZoneCoordinator(_make_hass(), _make_entry())
    coord.hass.states.put(SENSOR_1, "off", {"friendly_name": "Test Hallway Sensor"})
    entity = LuminaryMotionHwTimeoutSensor(coord, SENSOR_1)

    assert entity._attr_name == "Test Hallway Sensor Hardware Timeout"
    assert entity._attr_unique_id.startswith(coord.entry.entry_id)
    assert "hw_timeout" in entity._attr_unique_id


def test_async_setup_entry_creates_one_entity_per_sensor():
    entry = _make_entry()  # options has [SENSOR_1, SENSOR_2]
    coord = ZoneCoordinator(_make_hass(), entry)
    hass = _make_hass()
    hass.data = {DOMAIN: {entry.entry_id: coord}}

    added = []
    asyncio.run(sensor_module.async_setup_entry(hass, entry, lambda entities: added.extend(entities)))

    # 1 status sensor + 2 hw-timeout sensors (SENSOR_1, SENSOR_2)
    assert len(added) == 3
    assert coord.status_entity is not None
    assert set(coord._hw_timeout_entities.keys()) == {SENSOR_1, SENSOR_2}

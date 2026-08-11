"""Unit tests for number.py's dynamic light_on_time_sec floor."""
from __future__ import annotations

from custom_components.luminary_ha.const import CONF_SENSOR_HW_TIMEOUTS
from custom_components.luminary_ha.coordinator import ZoneCoordinator
from custom_components.luminary_ha.number import NUMBERS, LuminaryNumber

from tests.test_coordinator import SENSOR_1, SENSOR_2, _make_entry, _make_hass

LIGHT_ON_TIME_DESC = next(d for d in NUMBERS if d.key == "light_on_time_sec")
NORMAL_BRIGHTNESS_DESC = next(d for d in NUMBERS if d.key == "normal_brightness")
STALE_SENSOR_MINUTES_DESC = next(d for d in NUMBERS if d.key == "stale_sensor_minutes")


def _coord(hw_timeouts: dict | None = None) -> ZoneCoordinator:
    entry = _make_entry()
    if hw_timeouts is not None:
        entry.options[CONF_SENSOR_HW_TIMEOUTS] = hw_timeouts
    return ZoneCoordinator(_make_hass(), entry)


def test_min_value_stays_static_floor_with_no_sensor_timeouts():
    coord = _coord({})
    entity = LuminaryNumber(coord, LIGHT_ON_TIME_DESC)
    assert entity.native_min_value == 10  # static floor, max_sensor_hw_timeout() == 0


def test_min_value_raised_by_sensor_floor():
    coord = _coord({
        SENSOR_1: {"timeout_sec": 30.0, "source": "manual", "source_entity_id": None},
    })
    entity = LuminaryNumber(coord, LIGHT_ON_TIME_DESC)
    assert entity.native_min_value == 30.0


def test_min_value_never_drops_below_static_floor():
    coord = _coord({
        SENSOR_1: {"timeout_sec": 3.0, "source": "manual", "source_entity_id": None},  # below static floor of 10
    })
    entity = LuminaryNumber(coord, LIGHT_ON_TIME_DESC)
    assert entity.native_min_value == 10


def test_min_value_takes_max_across_sensors():
    coord = _coord({
        SENSOR_1: {"timeout_sec": 12.0, "source": "manual", "source_entity_id": None},
        SENSOR_2: {"timeout_sec": 45.0, "source": "manual", "source_entity_id": None},
    })
    entity = LuminaryNumber(coord, LIGHT_ON_TIME_DESC)
    assert entity.native_min_value == 45.0


def test_stale_sensor_minutes_default_and_bounds():
    entity = LuminaryNumber(_coord(), STALE_SENSOR_MINUTES_DESC)
    assert entity._attr_native_value == 60
    assert STALE_SENSOR_MINUTES_DESC.native_min_value == 10
    assert STALE_SENSOR_MINUTES_DESC.native_max_value == 1440


def test_unrelated_number_entity_ignores_sensor_floor():
    coord = _coord({
        SENSOR_1: {"timeout_sec": 999.0, "source": "manual", "source_entity_id": None},
    })
    entity = LuminaryNumber(coord, NORMAL_BRIGHTNESS_DESC)
    assert entity.native_min_value == NORMAL_BRIGHTNESS_DESC.native_min_value == 1


def test_max_value_static_when_floor_is_below_it():
    coord = _coord({
        SENSOR_1: {"timeout_sec": 30.0, "source": "manual", "source_entity_id": None},
    })
    entity = LuminaryNumber(coord, LIGHT_ON_TIME_DESC)
    assert entity.native_max_value == 600


def test_max_value_rises_with_a_floor_past_the_static_ceiling():
    """The hardware-timeout floor is read live and unbounded from the owning
    integration — a Zooz ZSE11's motion timeout parameter reaches 15300s, far
    past light_on_time_sec's static 600s ceiling. min > max breaks the number
    entity in the frontend and rejects the value _maybe_bump_light_on_time_floor
    writes, so the ceiling has to follow the floor up (2026-08-10 review)."""
    coord = _coord({
        SENSOR_1: {"timeout_sec": 900.0, "source": "manual", "source_entity_id": None},
    })
    entity = LuminaryNumber(coord, LIGHT_ON_TIME_DESC)
    assert entity.native_min_value == 900.0
    assert entity.native_max_value == 900.0
    assert entity.native_max_value >= entity.native_min_value


def test_unrelated_number_entity_keeps_static_max():
    coord = _coord({
        SENSOR_1: {"timeout_sec": 9999.0, "source": "manual", "source_entity_id": None},
    })
    entity = LuminaryNumber(coord, NORMAL_BRIGHTNESS_DESC)
    assert entity.native_max_value == NORMAL_BRIGHTNESS_DESC.native_max_value == 100


def test_async_setup_entry_registers_light_on_time_entity():
    import asyncio
    from custom_components.luminary_ha import number as number_module
    from custom_components.luminary_ha.const import DOMAIN

    entry = _make_entry()
    coord = ZoneCoordinator(_make_hass(), entry)
    hass = _make_hass()
    hass.data = {DOMAIN: {entry.entry_id: coord}}

    added = []

    def _add_entities(entities):
        added.extend(entities)

    asyncio.run(number_module.async_setup_entry(hass, entry, _add_entities))

    assert coord.light_on_time_entity is not None
    assert coord.light_on_time_entity.entity_description.key == "light_on_time_sec"
    assert len(added) == len(NUMBERS)

"""Unit tests for ZoneCoordinator's hardware-timeout read-through accessors and
live-update wiring (sensor_hw_timeout, max_sensor_hw_timeout,
_handle_hw_timeout_source_changed, _maybe_bump_light_on_time_floor)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from custom_components.luminary_ha.coordinator import ZoneCoordinator
from custom_components.luminary_ha.const import CONF_SENSOR_HW_TIMEOUTS

from tests.conftest import _Event as Event
from tests.test_coordinator import ENTRY_ID, LIGHT, SENSOR_1, SENSOR_2, ZONE_ID, _make_entry, _make_hass


def _coord_with_hw_timeouts(hw_timeouts: dict) -> ZoneCoordinator:
    entry = _make_entry()
    entry.options[CONF_SENSOR_HW_TIMEOUTS] = hw_timeouts
    coord = ZoneCoordinator(_make_hass(), entry)
    return coord


# ---------------------------------------------------------------------------
# sensor_hw_timeout / max_sensor_hw_timeout
# ---------------------------------------------------------------------------

def test_manual_fallback_value():
    coord = _coord_with_hw_timeouts({SENSOR_1: {"timeout_sec": 15.0, "source": "manual", "source_entity_id": None}})
    assert coord.sensor_hw_timeout(SENSOR_1) == 15.0


def test_live_source_read():
    coord = _coord_with_hw_timeouts(
        {SENSOR_1: {"timeout_sec": None, "source": "zwave", "source_entity_id": "number.src_1"}}
    )
    coord.hass.states.put("number.src_1", "13")
    assert coord.sensor_hw_timeout(SENSOR_1) == 13.0


def test_live_source_unavailable_returns_none():
    coord = _coord_with_hw_timeouts(
        {SENSOR_1: {"timeout_sec": None, "source": "zwave", "source_entity_id": "number.src_1"}}
    )
    coord.hass.states.put("number.src_1", "unavailable")
    assert coord.sensor_hw_timeout(SENSOR_1) is None


def test_unconfigured_sensor_returns_none():
    coord = _coord_with_hw_timeouts({})
    assert coord.sensor_hw_timeout(SENSOR_1) is None


def test_max_sensor_hw_timeout_takes_max_ignoring_none():
    coord = _coord_with_hw_timeouts({
        SENSOR_1: {"timeout_sec": 10.0, "source": "manual", "source_entity_id": None},
        SENSOR_2: {"timeout_sec": 30.0, "source": "manual", "source_entity_id": None},
    })
    assert coord.max_sensor_hw_timeout() == 30.0


def test_max_sensor_hw_timeout_defaults_to_zero():
    coord = _coord_with_hw_timeouts({})
    assert coord.max_sensor_hw_timeout() == 0.0


# ---------------------------------------------------------------------------
# _handle_hw_timeout_source_changed
# ---------------------------------------------------------------------------

def test_source_changed_pushes_to_display_and_light_entities():
    coord = _coord_with_hw_timeouts(
        {SENSOR_1: {"timeout_sec": None, "source": "zwave", "source_entity_id": "number.src_1"}}
    )
    coord.hass.states.put("number.src_1", "10")
    coord._hw_timeout_source_map = {"number.src_1": SENSOR_1}
    display_entity = MagicMock()
    light_entity = MagicMock()
    light_entity._attr_native_value = 60
    coord._hw_timeout_entities = {SENSOR_1: display_entity}
    coord.light_on_time_entity = light_entity
    # Sync test, no running event loop — swap in a MagicMock so the scheduled
    # coroutine isn't handed to asyncio.ensure_future(), matching the existing
    # test_22_conditions_met_creates_task pattern in test_coordinator.py.
    coord.hass.async_create_task = MagicMock(return_value=MagicMock())

    coord._handle_hw_timeout_source_changed(Event({"entity_id": "number.src_1"}))

    display_entity.async_write_ha_state.assert_called_once()
    light_entity.async_write_ha_state.assert_called_once()
    coord.hass.async_create_task.assert_called_once()  # _maybe_bump_light_on_time_floor scheduled


def test_source_changed_ignores_untracked_entity():
    coord = _coord_with_hw_timeouts({})
    coord._hw_timeout_source_map = {}
    coord.hass.async_create_task = MagicMock(return_value=MagicMock())
    coord._handle_hw_timeout_source_changed(Event({"entity_id": "number.unrelated"}))
    coord.hass.async_create_task.assert_not_called()


# ---------------------------------------------------------------------------
# _maybe_bump_light_on_time_floor
# ---------------------------------------------------------------------------

async def test_bump_raises_value_and_notifies():
    coord = _coord_with_hw_timeouts(
        {SENSOR_1: {"timeout_sec": 30.0, "source": "manual", "source_entity_id": None}}
    )
    coord.hass.states.put(f"number.{ZONE_ID}_light_on_time_sec", "10")
    coord.hass.states.put(SENSOR_1, "off", {"friendly_name": "Pantry Motion"})
    light_entity = MagicMock()
    light_entity._attr_native_value = 10
    coord.light_on_time_entity = light_entity

    await coord._maybe_bump_light_on_time_floor()

    assert light_entity._attr_native_value == 30.0
    light_entity.async_write_ha_state.assert_called_once()
    coord.hass.services.async_call.assert_awaited_once()
    call = coord.hass.services.async_call.await_args
    assert call.args[:2] == ("persistent_notification", "create")
    assert "Pantry Motion" in call.args[2]["message"]
    assert "30" in call.args[2]["message"]


async def test_no_bump_when_floor_not_exceeded():
    coord = _coord_with_hw_timeouts(
        {SENSOR_1: {"timeout_sec": 5.0, "source": "manual", "source_entity_id": None}}
    )
    coord.hass.states.put(f"number.{ZONE_ID}_light_on_time_sec", "60")
    light_entity = MagicMock()
    light_entity._attr_native_value = 60
    coord.light_on_time_entity = light_entity

    await coord._maybe_bump_light_on_time_floor()

    assert light_entity._attr_native_value == 60
    light_entity.async_write_ha_state.assert_not_called()
    coord.hass.services.async_call.assert_not_awaited()


async def test_no_bump_when_light_on_time_entity_unset():
    coord = _coord_with_hw_timeouts(
        {SENSOR_1: {"timeout_sec": 30.0, "source": "manual", "source_entity_id": None}}
    )
    coord.light_on_time_entity = None

    await coord._maybe_bump_light_on_time_floor()  # must not raise

    coord.hass.services.async_call.assert_not_awaited()

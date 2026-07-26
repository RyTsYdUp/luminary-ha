"""Unit tests for ZoneCoordinator's last_seen-staleness dead/stuck-sensor detection
(sensor_last_seen, sensor_stale_seconds, stale_sensor_threshold_sec,
_check_stale_sensors, _notify_sensor_stale, _clear_sensor_stale).

See const.py's DEFAULT_STALE_SENSOR_MINUTES comment for the real-hardware incident
(hallway_motion_2, 2026-07-13 to 2026-07-17) this feature is built to catch earlier."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from custom_components.luminary_ha.coordinator import ZoneCoordinator

from tests.test_coordinator import ENTRY_ID, LIGHT, SENSOR_1, SENSOR_2, ZONE_ID, _make_entry, _make_hass

LONG_DEAD_TS = "2000-01-01T00:00:00+00:00"  # far enough in the past to dwarf any threshold


def _recent_ts() -> str:
    """A timestamp guaranteed fresh relative to whenever the test actually runs."""
    return (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()


def _coord() -> ZoneCoordinator:
    return ZoneCoordinator(_make_hass(), _make_entry())


# ---------------------------------------------------------------------------
# sensor_last_seen / sensor_stale_seconds
# ---------------------------------------------------------------------------

def test_last_seen_none_when_undiscovered():
    coord = _coord()
    assert coord.sensor_last_seen(SENSOR_1) is None
    assert coord.sensor_stale_seconds(SENSOR_1) is None


def test_last_seen_reads_through_source_entity():
    coord = _coord()
    coord._last_seen_source_map = {SENSOR_1: "sensor.src_last_seen"}
    coord.hass.states.put("sensor.src_last_seen", "2026-07-17T15:54:05+00:00")
    last_seen = coord.sensor_last_seen(SENSOR_1)
    assert last_seen == datetime(2026, 7, 17, 15, 54, 5, tzinfo=timezone.utc)


def test_last_seen_unavailable_source_returns_none():
    coord = _coord()
    coord._last_seen_source_map = {SENSOR_1: "sensor.src_last_seen"}
    coord.hass.states.put("sensor.src_last_seen", "unavailable")
    assert coord.sensor_last_seen(SENSOR_1) is None
    assert coord.sensor_stale_seconds(SENSOR_1) is None


def test_stale_seconds_large_for_ancient_timestamp():
    coord = _coord()
    coord._last_seen_source_map = {SENSOR_1: "sensor.src_last_seen"}
    coord.hass.states.put("sensor.src_last_seen", LONG_DEAD_TS)
    # 2000-01-01 is far more than any sane threshold away from "now" no matter when
    # this test runs — avoids depending on mocking the wall clock.
    assert coord.sensor_stale_seconds(SENSOR_1) > 365 * 24 * 3600


# ---------------------------------------------------------------------------
# stale_sensor_threshold_sec
# ---------------------------------------------------------------------------

def test_threshold_defaults_to_60_minutes():
    coord = _coord()
    assert coord.stale_sensor_threshold_sec == 60 * 60


def test_threshold_reflects_configured_value():
    coord = _coord()
    coord.hass.states.put(f"number.{ZONE_ID}_stale_sensor_minutes", "15")
    assert coord.stale_sensor_threshold_sec == 15 * 60


# ---------------------------------------------------------------------------
# _check_stale_sensors
# ---------------------------------------------------------------------------

def test_check_stale_sensors_notifies_new_stale_sensor():
    coord = _coord()
    coord._last_seen_source_map = {SENSOR_1: "sensor.src_last_seen"}
    coord.hass.states.put("sensor.src_last_seen", LONG_DEAD_TS)
    coord.hass.async_create_task = MagicMock(return_value=MagicMock())

    coord._check_stale_sensors(now=None)

    assert SENSOR_1 in coord._stale_notified
    coord.hass.async_create_task.assert_called_once()


def test_check_stale_sensors_does_not_renotify_already_flagged():
    coord = _coord()
    coord._last_seen_source_map = {SENSOR_1: "sensor.src_last_seen"}
    coord.hass.states.put("sensor.src_last_seen", LONG_DEAD_TS)
    coord._stale_notified = {SENSOR_1}
    coord.hass.async_create_task = MagicMock(return_value=MagicMock())

    coord._check_stale_sensors(now=None)

    coord.hass.async_create_task.assert_not_called()


def test_check_stale_sensors_clears_recovered_sensor():
    coord = _coord()
    coord._last_seen_source_map = {SENSOR_1: "sensor.src_last_seen"}
    coord.hass.states.put("sensor.src_last_seen", _recent_ts())  # freshly seen, not stale
    coord._stale_notified = {SENSOR_1}
    coord.hass.async_create_task = MagicMock(return_value=MagicMock())

    coord._check_stale_sensors(now=None)

    assert SENSOR_1 not in coord._stale_notified
    coord.hass.async_create_task.assert_called_once()


def test_check_stale_sensors_no_action_when_unknown():
    """Undiscovered sensor (no last_seen source) is treated as unknown, not stale —
    fail-quiet rather than false-alarming on a sensor Luminary couldn't identify."""
    coord = _coord()
    coord.hass.async_create_task = MagicMock(return_value=MagicMock())

    coord._check_stale_sensors(now=None)

    coord.hass.async_create_task.assert_not_called()


def test_check_stale_sensors_updates_display_entity():
    coord = _coord()
    display_entity = MagicMock()
    coord.last_seen_entities = {SENSOR_1: display_entity, SENSOR_2: MagicMock()}
    coord.hass.async_create_task = MagicMock(return_value=MagicMock())

    coord._check_stale_sensors(now=None)

    display_entity.async_write_ha_state.assert_called_once()


# ---------------------------------------------------------------------------
# _notify_sensor_stale / _clear_sensor_stale
# ---------------------------------------------------------------------------

async def test_notify_sensor_stale_message_content():
    coord = _coord()
    coord.hass.states.put(SENSOR_1, "on", {"friendly_name": "Hallway Motion 2"})

    await coord._notify_sensor_stale(SENSOR_1, 6000.0)  # 100 minutes

    coord.hass.services.async_call.assert_awaited_once()
    call = coord.hass.services.async_call.await_args
    assert call.args[:2] == ("persistent_notification", "create")
    assert call.args[2]["notification_id"] == f"{ZONE_ID}_sensor_stale_binary_sensor_motion_1"
    assert "Hallway Motion 2" in call.args[2]["message"]
    assert "100" in call.args[2]["message"]


async def test_clear_sensor_stale_dismisses_matching_id():
    coord = _coord()

    await coord._clear_sensor_stale(SENSOR_1)

    coord.hass.services.async_call.assert_awaited_once_with(
        "persistent_notification", "dismiss",
        {"notification_id": f"{ZONE_ID}_sensor_stale_binary_sensor_motion_1"},
        blocking=False,
    )

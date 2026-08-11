"""
Comprehensive unit tests for ZoneCoordinator.

Scenarios covered
-----------------
is_dark_enough
  1.  Sun mode, elevation below threshold       → True  (dark enough)
  2.  Sun mode, elevation above threshold       → False (too bright)
  3.  Sun mode, sun entity missing              → True  (fail-safe)
  4.  Lux mode, lux below threshold             → True
  5.  Lux mode, lux above threshold             → False
  6.  Lux mode, lux entity ID not configured    → True  (fail-safe)
  7.  Lux mode, lux sensor unavailable          → True  (fail-safe)

in_dim_window
  8.  Simple window (no midnight crossing), inside   → True
  9.  Simple window, outside                         → False
  10. Midnight-crossing window (23:00–06:00), inside → True
  11. Midnight-crossing window, outside              → False

target_brightness
  12. Nightlight on, in dim window               → dim_brightness
  13. Nightlight on, not in dim window           → normal_brightness
  14. Nightlight off, in dim window              → normal_brightness (override)

compute_status
  15. automation_disabled=on                    → "Disabled"
  16. automation_disabled=off, blocker=on       → "Manual Override"
  17. neither, dark enough                      → "Automated"
  18. neither, not dark enough                  → "Standby"

nightlight_enabled property
  19. switch state "on"                         → True
  20. switch state "off"                        → False
  21. switch entity not found (None)            → True  (fail-safe default)

Motion trigger guard conditions (_handle_sensor_change)
  22. Sensor → "on", all conditions met         → motion task created
  23. Sensor → "on", automation_disabled=on     → no task
  24. Sensor → "on", motion_blocker=on          → no task
  25. Sensor → "not dark enough"                → no task
  26. Sensor → "off" (not an "on" event)        → no task

Motion restart
  27. New trigger while task running            → old task cancelled, new task created

Motion sequence: light control
  28. Sensors clear before timeout              → light on then off
  29. Timeout fires, sensors still on           → light off + refresh_value called
  30. Timeout fires, sensors clear              → light off, no refresh_value
  31. CancelledError propagates cleanly         → no light off
  32. automation_disabled set during wait       → no light off

Z-Wave event filtering
  33. Event for wrong device_id                 → no handler called
  34. Event for wrong command_class             → no handler called
  34b. No switch device configured              → no handler called (was: accepted every device)

Z-Wave switch handlers
  35. Single tap ↑, smart mode                 → delegates to _start_switch_on_hold
  36. Single tap ↑, dumb mode                  → light on at normal_brightness, no blocker/hold
  37. Single tap ↓, smart mode, sensors on     → blocker off, light on at auto brightness
  38. Single tap ↓, smart mode, sensors off    → blocker off, light off
  39. Single tap ↓, dumb mode                  → light off
  40. Double tap ↑                             → automation_disabled on, blocker off
  41. Double tap ↓, sensors on                 → automation_disabled off, light on
  42. Double tap ↓, sensors off               → automation_disabled off, light off
  43. Scene 3 (config button)                  → both off, light off

Dim window boundary handlers
  44. dim_start fires, nightlight on, light on, not overridden → dim brightness
  45. dim_start fires, nightlight off                           → no change
  46. dim_start fires, light off                               → no change
  47. dim_start fires, automation_disabled                     → no change
  48. dim_end fires, light on                                  → normal brightness
  49. dim_end fires, light off                                 → no change

Sensor unavailable / recovery
  50. Sensor becomes unavailable               → persistent_notification created
  51. Sensor recovers                          → that sensor's notification dismissed
  52. Notification ids are per-sensor, not per-zone (one sensor's recovery leaves
      the others' alerts standing)

Motion group state
  53. Any sensor "on"                          → _sensors_any_on = True
  54. All sensors "off"                        → _sensors_any_on = False
  55. State change updates motion_group_entity

Light-change dispatch (_handle_light_change) — 2026-08-02 auto-shutoff feature
  56. new_state None                            → no dispatch
  57. Light turning off                         → no dispatch
  58. automation_disabled                       → no dispatch
  59. Own context (self-commanded change)       → no dispatch
  59b. Own context behind a newer _light_on     → still no dispatch (context ring)
  60. External on, automation enabled           → dispatches _start_switch_on_hold
  61. External on during the day                → still dispatches (no more is_dark_enough gate)
  62. External on while motion_blocker already on → still dispatches (renews the hold)
  63. No context on event (defensive)           → still dispatches as external
  63b. on → on update (attribute-only change)   → no dispatch (not a switch press)
  63c. off → on edge                            → still dispatches (companion switch)
  63d. unavailable → on edge                    → still dispatches

Switch-triggered on: full brightness + auto-shutoff (_start_switch_on_hold / _run_switch_on_sequence)
  64. Smart mode                                → blocker on, light on at normal_brightness, sequence started
  65. Dumb mode (automation_disabled)           → no-op entirely
  66. Cancels an in-progress motion/hold task first
  67. Sensors clear well within timeout         → light off + blocker off after light_on_time_sec grace
  68. Sensors never clear                       → cap reached, light off anyway
  69. Sensor re-triggers during grace period    → loops back, stays on (event-driven, not sampled)
  70. automation_disabled during wait           → skipped, light left alone
  70b. automation_disabled during wait          → motion_blocker still released
  70c. Cap reached                              → motion_blocker released
  71. CancelledError propagates cleanly         → no light off
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import ANY, AsyncMock, MagicMock, call, patch

import pytest

from custom_components.luminary_ha.coordinator import ZoneCoordinator
from custom_components.luminary_ha.const import (
    CONF_AREA,
    CONF_LIGHT,
    CONF_SENSORS,
    CONF_SWITCH_DEVICE,
    CONF_ZONE_ID,
    CONF_ZONE_NAME,
    DAYTIME_MODE_LUX,
    DAYTIME_MODE_SUN,
    ZWAVE_SCENE_KEY_CONFIG,
    ZWAVE_SCENE_KEY_DOWN,
    ZWAVE_SCENE_KEY_UP,
    ZWAVE_SCENE_VALUE_DOUBLE,
    ZWAVE_SCENE_VALUE_SINGLE,
)

# Re-import the stub Event so we can construct test payloads
from tests.conftest import _Event as Event


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ZONE_ID = "hallway"
ENTRY_ID = "entry_test_001"
SENSOR_1 = "binary_sensor.motion_1"
SENSOR_2 = "binary_sensor.motion_2"
LIGHT = "light.hallway"
SWITCH_DEVICE = "device_abc"


def _s(state: str, attributes: dict | None = None):
    """Build a fake HA state object."""
    m = MagicMock()
    m.state = state
    m.attributes = attributes or {}
    return m


class FakeStates:
    """Dict-backed state machine for tests."""

    def __init__(self):
        self._d: dict = {}

    def put(self, entity_id: str, state: str, attributes: dict | None = None):
        self._d[entity_id] = _s(state, attributes)
        return self

    def get(self, entity_id):
        return self._d.get(entity_id)

    def is_state(self, entity_id: str, state: str) -> bool:
        s = self._d.get(entity_id)
        return s is not None and s.state == state


def _default_states() -> FakeStates:
    """Return a FakeStates pre-populated with safe defaults for all entities."""
    st = FakeStates()
    st.put(f"switch.{ZONE_ID}_automation_disabled", "off")
    st.put(f"switch.{ZONE_ID}_motion_blocker", "off")
    st.put(f"switch.{ZONE_ID}_nightlight_enabled", "on")
    st.put(f"number.{ZONE_ID}_light_on_time_sec", "60")
    st.put(f"number.{ZONE_ID}_dim_brightness", "10")
    st.put(f"number.{ZONE_ID}_normal_brightness", "100")
    st.put(f"number.{ZONE_ID}_sun_elevation_threshold", "3.0")
    st.put(f"number.{ZONE_ID}_lux_threshold", "50")
    st.put(f"number.{ZONE_ID}_switch_on_timeout_minutes", "60")
    st.put(f"select.{ZONE_ID}_daytime_mode", DAYTIME_MODE_SUN)
    st.put(f"text.{ZONE_ID}_lux_sensor_entity", "")
    st.put(f"time.{ZONE_ID}_dim_start", "00:00:00")
    st.put(f"time.{ZONE_ID}_dim_end", "06:00:00")
    st.put(LIGHT, "off")
    st.put(SENSOR_1, "off")
    st.put(SENSOR_2, "off")
    st.put("sun.sun", "above_horizon", {"elevation": 10.0})
    return st


def _make_hass(states: FakeStates | None = None) -> MagicMock:
    hass = MagicMock()
    hass.states = states or _default_states()
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock()
    hass.bus = MagicMock()
    hass.bus.async_listen = MagicMock(return_value=lambda: None)
    tasks: list[asyncio.Task] = []
    hass._tasks = tasks

    def _create_task(coro):
        t = asyncio.ensure_future(coro)
        tasks.append(t)
        return t

    hass.async_create_task = _create_task
    return hass


def _make_entry() -> MagicMock:
    entry = MagicMock()
    entry.entry_id = ENTRY_ID
    entry.data = {
        CONF_ZONE_ID: ZONE_ID,
        CONF_ZONE_NAME: "Hallway",
        CONF_AREA: "area_hallway",
    }
    entry.options = {
        CONF_SENSORS: [SENSOR_1, SENSOR_2],
        CONF_LIGHT: LIGHT,
        CONF_SWITCH_DEVICE: SWITCH_DEVICE,
    }
    return entry


@pytest.fixture
def states():
    return _default_states()


@pytest.fixture
def hass(states):
    return _make_hass(states)


@pytest.fixture
def entry():
    return _make_entry()


@pytest.fixture
def coord(hass, entry):
    c = ZoneCoordinator(hass, entry)
    c._dim_unsubs = []
    return c


def _zwave_event(device_id=SWITCH_DEVICE, command_class=91, key=ZWAVE_SCENE_KEY_UP, value=ZWAVE_SCENE_VALUE_SINGLE):
    return Event({
        "device_id": device_id,
        "command_class": command_class,
        "property_key": key,
        "value": value,
    })


# ---------------------------------------------------------------------------
# 1–7  is_dark_enough
# ---------------------------------------------------------------------------

class TestIsDarkEnough:

    def test_01_sun_mode_below_threshold(self, coord, states):
        states.put("sun.sun", "above_horizon", {"elevation": -2.0})
        assert coord.is_dark_enough() is True

    def test_02_sun_mode_above_threshold(self, coord, states):
        states.put("sun.sun", "above_horizon", {"elevation": 10.0})
        assert coord.is_dark_enough() is False

    def test_03_sun_entity_missing(self, coord):
        coord.hass.states.get = lambda eid: None  # no states at all
        assert coord.is_dark_enough() is True

    def test_04_lux_mode_below_threshold(self, coord, states):
        states.put(f"select.{ZONE_ID}_daytime_mode", DAYTIME_MODE_LUX)
        states.put(f"text.{ZONE_ID}_lux_sensor_entity", "sensor.lux")
        states.put("sensor.lux", "30")
        assert coord.is_dark_enough() is True

    def test_05_lux_mode_above_threshold(self, coord, states):
        states.put(f"select.{ZONE_ID}_daytime_mode", DAYTIME_MODE_LUX)
        states.put(f"text.{ZONE_ID}_lux_sensor_entity", "sensor.lux")
        states.put("sensor.lux", "200")
        assert coord.is_dark_enough() is False

    def test_06_lux_mode_no_entity_configured(self, coord, states):
        states.put(f"select.{ZONE_ID}_daytime_mode", DAYTIME_MODE_LUX)
        # lux_sensor_entity is empty string (default)
        assert coord.is_dark_enough() is True

    def test_07_lux_mode_sensor_unavailable(self, coord, states):
        states.put(f"select.{ZONE_ID}_daytime_mode", DAYTIME_MODE_LUX)
        states.put(f"text.{ZONE_ID}_lux_sensor_entity", "sensor.lux")
        states.put("sensor.lux", "unavailable")
        assert coord.is_dark_enough() is True


# ---------------------------------------------------------------------------
# 8–11  in_dim_window
# ---------------------------------------------------------------------------

class TestInDimWindow:
    """The clock is read via dt_util.now(), not datetime.now() — the window has to
    follow the timezone configured *in HA*, not whatever the host process's clock
    happens to be set to (2026-08-10 review)."""

    def _set_window(self, states, start: str, end: str):
        states.put(f"time.{ZONE_ID}_dim_start", start)
        states.put(f"time.{ZONE_ID}_dim_end", end)

    def test_08_simple_window_inside(self, coord, states):
        self._set_window(states, "22:00:00", "23:00:00")
        with patch("custom_components.luminary_ha.coordinator.dt_util") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 1, 22, 30)
            assert coord.in_dim_window() is True

    def test_09_simple_window_outside(self, coord, states):
        self._set_window(states, "22:00:00", "23:00:00")
        with patch("custom_components.luminary_ha.coordinator.dt_util") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 1, 10, 0)
            assert coord.in_dim_window() is False

    def test_10_midnight_crossing_inside(self, coord, states):
        self._set_window(states, "23:00:00", "06:00:00")
        with patch("custom_components.luminary_ha.coordinator.dt_util") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 1, 2, 0)
            assert coord.in_dim_window() is True

    def test_11_midnight_crossing_outside(self, coord, states):
        self._set_window(states, "23:00:00", "06:00:00")
        with patch("custom_components.luminary_ha.coordinator.dt_util") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 1, 12, 0)
            assert coord.in_dim_window() is False


# ---------------------------------------------------------------------------
# 12–14  target_brightness
# ---------------------------------------------------------------------------

class TestTargetBrightness:

    def test_12_nightlight_on_in_window(self, coord, states):
        states.put(f"switch.{ZONE_ID}_nightlight_enabled", "on")
        with patch.object(coord, "in_dim_window", return_value=True):
            assert coord.target_brightness() == 10   # dim_brightness default

    def test_13_nightlight_on_outside_window(self, coord, states):
        states.put(f"switch.{ZONE_ID}_nightlight_enabled", "on")
        with patch.object(coord, "in_dim_window", return_value=False):
            assert coord.target_brightness() == 100  # normal_brightness default

    def test_14_nightlight_off_in_window(self, coord, states):
        states.put(f"switch.{ZONE_ID}_nightlight_enabled", "off")
        with patch.object(coord, "in_dim_window", return_value=True):
            assert coord.target_brightness() == 100  # nightlight disabled → always normal


# ---------------------------------------------------------------------------
# 15–18  compute_status
# ---------------------------------------------------------------------------

class TestComputeStatus:

    def test_15_automation_disabled(self, coord, states):
        states.put(f"switch.{ZONE_ID}_automation_disabled", "on")
        status, icon = coord.compute_status()
        assert status == "Disabled"
        assert "off" in icon

    def test_16_manual_override(self, coord, states):
        states.put(f"switch.{ZONE_ID}_automation_disabled", "off")
        states.put(f"switch.{ZONE_ID}_motion_blocker", "on")
        status, _ = coord.compute_status()
        assert status == "Manual Override"

    def test_17_automated_when_dark(self, coord, states):
        states.put(f"switch.{ZONE_ID}_automation_disabled", "off")
        states.put(f"switch.{ZONE_ID}_motion_blocker", "off")
        states.put("sun.sun", "below_horizon", {"elevation": -5.0})
        status, _ = coord.compute_status()
        assert status == "Automated"

    def test_18_standby_when_light(self, coord, states):
        states.put(f"switch.{ZONE_ID}_automation_disabled", "off")
        states.put(f"switch.{ZONE_ID}_motion_blocker", "off")
        states.put("sun.sun", "above_horizon", {"elevation": 20.0})
        status, _ = coord.compute_status()
        assert status == "Standby"


# ---------------------------------------------------------------------------
# 19–21  nightlight_enabled
# ---------------------------------------------------------------------------

class TestNightlightEnabled:

    def test_19_switch_on(self, coord, states):
        states.put(f"switch.{ZONE_ID}_nightlight_enabled", "on")
        assert coord.nightlight_enabled is True

    def test_20_switch_off(self, coord, states):
        states.put(f"switch.{ZONE_ID}_nightlight_enabled", "off")
        assert coord.nightlight_enabled is False

    def test_21_entity_not_found_defaults_true(self, coord):
        # Entity doesn't exist in state machine yet → state is None → default True
        empty = FakeStates()
        coord.hass.states = empty
        assert coord.nightlight_enabled is True


# ---------------------------------------------------------------------------
# 22–26  Motion trigger guard conditions
# ---------------------------------------------------------------------------

class TestMotionGuards:

    def test_22_conditions_met_creates_task(self, coord, states):
        states.put("sun.sun", "below_horizon", {"elevation": -5.0})
        coord._motion_task = None
        # Use a plain MagicMock so no real asyncio task is created in the sync test
        coord.hass.async_create_task = MagicMock(return_value=MagicMock())
        coord._handle_sensor_change(Event({"entity_id": SENSOR_1, "new_state": _s("on"), "old_state": _s("off")}))
        coord.hass.async_create_task.assert_called_once()

    def test_23_automation_disabled_blocks(self, coord, states):
        states.put(f"switch.{ZONE_ID}_automation_disabled", "on")
        states.put("sun.sun", "below_horizon", {"elevation": -5.0})
        coord._handle_sensor_change(Event({"entity_id": SENSOR_1, "new_state": _s("on"), "old_state": _s("off")}))
        assert coord._motion_task is None

    def test_24_motion_blocker_blocks(self, coord, states):
        states.put(f"switch.{ZONE_ID}_motion_blocker", "on")
        states.put("sun.sun", "below_horizon", {"elevation": -5.0})
        coord._handle_sensor_change(Event({"entity_id": SENSOR_1, "new_state": _s("on"), "old_state": _s("off")}))
        assert coord._motion_task is None

    def test_25_not_dark_enough_blocks(self, coord, states):
        states.put("sun.sun", "above_horizon", {"elevation": 20.0})
        coord._handle_sensor_change(Event({"entity_id": SENSOR_1, "new_state": _s("on"), "old_state": _s("off")}))
        assert coord._motion_task is None

    def test_26_sensor_off_event_does_not_trigger(self, coord, states):
        states.put("sun.sun", "below_horizon", {"elevation": -5.0})
        coord._handle_sensor_change(Event({"entity_id": SENSOR_1, "new_state": _s("off"), "old_state": _s("on")}))
        assert coord._motion_task is None


# ---------------------------------------------------------------------------
# 27  Motion restart
# ---------------------------------------------------------------------------

class TestMotionRestart:

    async def test_27_new_trigger_cancels_old_task(self, coord, states):
        states.put("sun.sun", "below_horizon", {"elevation": -5.0})

        # Plant a fake "running" task
        old_future: asyncio.Future = asyncio.get_event_loop().create_future()
        old_task = asyncio.ensure_future(asyncio.sleep(9999))
        coord._motion_task = old_task

        # Trigger motion again
        with patch(
            "custom_components.luminary_ha.coordinator.async_track_state_change_event",
            return_value=lambda: None,
        ):
            coord._handle_sensor_change(Event({"entity_id": SENSOR_1, "new_state": _s("on"), "old_state": _s("off")}))

        await asyncio.sleep(0)  # let the event loop process cancellation
        assert old_task.cancelled()
        assert coord._motion_task is not old_task


# ---------------------------------------------------------------------------
# 28–32  Motion sequence coroutine
# ---------------------------------------------------------------------------

class TestMotionSequence:

    async def test_28_light_turns_on_then_off_when_sensors_clear(self, coord, states):
        """Sensors go off before timeout → light on then off, no refresh."""
        states.put("sun.sun", "below_horizon", {"elevation": -5.0})
        states.put(f"number.{ZONE_ID}_light_on_time_sec", "10")
        # Both sensors off already
        states.put(SENSOR_1, "off")
        states.put(SENSOR_2, "off")

        captured = {}

        def fake_track(hass, entities, cb):
            captured["cb"] = cb
            return lambda: None

        with patch("custom_components.luminary_ha.coordinator.async_track_state_change_event", fake_track):
            task = asyncio.ensure_future(coord._run_motion_sequence())
            await asyncio.sleep(0)          # let sequence reach wait_for
            if "cb" in captured:
                captured["cb"](Event({}))   # simulate state change with sensors off
            await task

        calls = [c.args[:2] for c in coord.hass.services.async_call.call_args_list]
        assert ("light", "turn_on") in calls
        assert ("light", "turn_off") in calls
        coord.hass.services.async_call.assert_any_call(
            "zwave_js", "refresh_value", {"entity_id": [SENSOR_1, SENSOR_2]}, blocking=False
        ) if coord._sensors_any_on else None

    async def test_29_timeout_with_stuck_sensors_calls_refresh(self, coord, states):
        """Timeout fires with sensors still on → refresh_value called."""
        states.put(SENSOR_1, "on")
        states.put(SENSOR_2, "off")
        coord._sensors_any_on = True
        coord._stuck_timeout = 0.01  # instant timeout; avoids 1800s real wait

        with patch("custom_components.luminary_ha.coordinator.async_track_state_change_event", return_value=lambda: None):
            await coord._run_motion_sequence()

        coord.hass.services.async_call.assert_any_call(
            "zwave_js", "refresh_value", {"entity_id": [SENSOR_1, SENSOR_2]}, blocking=False
        )

    async def test_30_timeout_sensors_clear_no_refresh(self, coord, states):
        """Timeout fires with sensors already off → no refresh_value."""
        coord._sensors_any_on = False
        coord._stuck_timeout = 0.01  # instant timeout

        with patch("custom_components.luminary_ha.coordinator.async_track_state_change_event", return_value=lambda: None):
            await coord._run_motion_sequence()

        refresh_calls = [c for c in coord.hass.services.async_call.call_args_list if c.args[0] == "zwave_js"]
        assert len(refresh_calls) == 0

    async def test_31_cancelled_error_propagates(self, coord, states):
        """CancelledError from restart must propagate; light must NOT be turned off."""
        states.put(f"number.{ZONE_ID}_light_on_time_sec", "60")

        async def slow_clear():
            await asyncio.sleep(999)

        cleared_mock = MagicMock()
        cleared_mock.wait = slow_clear

        with patch("custom_components.luminary_ha.coordinator.async_track_state_change_event", return_value=lambda: None):
            task = asyncio.ensure_future(coord._run_motion_sequence())
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        off_calls = [c for c in coord.hass.services.async_call.call_args_list if c.args[:2] == ("light", "turn_off")]
        assert len(off_calls) == 0

    async def test_32_automation_disabled_during_wait_skips_off(self, coord, states):
        """If automation_disabled turns on while waiting, don't turn off light."""
        coord._stuck_timeout = 0.01
        # Mark as disabled BEFORE timeout fires so the guard check catches it
        states.put(f"switch.{ZONE_ID}_automation_disabled", "on")

        with patch("custom_components.luminary_ha.coordinator.async_track_state_change_event", return_value=lambda: None):
            await coord._run_motion_sequence()

        off_calls = [c for c in coord.hass.services.async_call.call_args_list if c.args[:2] == ("light", "turn_off")]
        assert len(off_calls) == 0


# ---------------------------------------------------------------------------
# 33–34  Z-Wave event filtering
# ---------------------------------------------------------------------------

class TestZwaveFiltering:

    async def test_33_wrong_device_ignored(self, coord):
        coord._single_tap_up = AsyncMock()
        coord._handle_zwave_event(_zwave_event(device_id="wrong_device"))
        await asyncio.sleep(0)
        coord._single_tap_up.assert_not_called()

    async def test_34_wrong_command_class_ignored(self, coord):
        coord._single_tap_up = AsyncMock()
        coord._handle_zwave_event(_zwave_event(command_class=99))
        await asyncio.sleep(0)
        coord._single_tap_up.assert_not_called()

    async def test_34b_no_switch_device_configured_ignores_everything(self, coord, entry):
        """CONF_SWITCH_DEVICE is optional, and a zone without one must handle
        no taps at all.

        The guard used to read `if self.switch_device and device_id != ...`,
        which short-circuits to "accept" when nothing is configured — so such
        a zone acted on Central Scene notifications from *every* Z-Wave device
        in the house, letting any scene controller anywhere drive its lights
        (2026-08-10 review).
        """
        entry.options[CONF_SWITCH_DEVICE] = None
        coord._single_tap_up = AsyncMock()
        coord._handle_zwave_event(_zwave_event(device_id="some_other_scene_controller"))
        await asyncio.sleep(0)
        coord._single_tap_up.assert_not_called()


# ---------------------------------------------------------------------------
# 35–43  Z-Wave switch handlers
# ---------------------------------------------------------------------------

class TestZwaveSwitchHandlers:

    async def test_35_single_tap_up_smart_mode(self, coord, states):
        """Smart mode: delegates to _start_switch_on_hold (full bright, blocker
        on, auto-shutoff sequence started) rather than doing it inline —
        keeps a companion switch's on-report and a main-paddle tap on one
        code path."""
        states.put(f"switch.{ZONE_ID}_automation_disabled", "off")
        coord._start_switch_on_hold = AsyncMock()
        await coord._single_tap_up()
        coord._start_switch_on_hold.assert_called_once()

    async def test_36_single_tap_up_dumb_mode(self, coord, states):
        """Dumb mode: light on at normal_brightness, no blocker/hold at all."""
        states.put(f"switch.{ZONE_ID}_automation_disabled", "on")
        coord._start_switch_on_hold = AsyncMock()
        await coord._single_tap_up()
        coord.hass.services.async_call.assert_any_call(
            "light", "turn_on",
            {"entity_id": LIGHT, "brightness_pct": 100, "transition": 1},
            blocking=False,
            context=ANY,
        )
        coord._start_switch_on_hold.assert_not_called()
        blocker_calls = [
            c for c in coord.hass.services.async_call.call_args_list
            if "motion_blocker" in str(c)
        ]
        assert len(blocker_calls) == 0

    async def test_37_single_tap_down_smart_mode(self, coord, states):
        """Smart mode: light off, override held only for the duration of the
        off-command and cleared again afterward (regression guard — a prior
        fix left this switch permanently on, silently blocking all future
        motion until an explicit double-tap; 2026-08-02)."""
        states.put(f"switch.{ZONE_ID}_automation_disabled", "off")
        await coord._single_tap_down()
        coord.hass.services.async_call.assert_any_call(
            "light", "turn_off",
            {"entity_id": LIGHT, "transition": 1},
            blocking=False,
        )
        coord.hass.services.async_call.assert_any_call(
            "switch", "turn_on",
            {"entity_id": f"switch.{ZONE_ID}_motion_blocker"},
            blocking=True,
        )
        # Must be the last motion_blocker call — i.e. cleared again, not left on.
        blocker_calls = [
            c for c in coord.hass.services.async_call.call_args_list
            if c.args[:2] == ("switch", "turn_on") or c.args[:2] == ("switch", "turn_off")
        ]
        assert blocker_calls[-1].args[:2] == ("switch", "turn_off")
        assert blocker_calls[-1].kwargs == {"blocking": True}

    async def test_38_single_tap_down_smart_sensors_still_on(self, coord, states):
        """Smart mode + active motion: still turns off immediately (regression
        guard — previously this handed control back to the motion sequence
        instead of turning off, so a down-press during motion did nothing).
        Any in-progress motion task is cancelled outright rather than
        restarted, so the off sticks until a genuinely new motion transition
        comes in."""
        states.put(f"switch.{ZONE_ID}_automation_disabled", "off")
        coord._sensors_any_on = True
        coord._restart_motion_task = MagicMock()
        fake_task = MagicMock()
        fake_task.done.return_value = False
        coord._motion_task = fake_task
        await coord._single_tap_down()
        coord.hass.services.async_call.assert_any_call(
            "light", "turn_off", {"entity_id": LIGHT, "transition": 1}, blocking=False
        )
        coord._restart_motion_task.assert_not_called()
        fake_task.cancel.assert_called_once()

    async def test_39_single_tap_down_dumb_mode(self, coord, states):
        """Dumb mode: just turn light off."""
        states.put(f"switch.{ZONE_ID}_automation_disabled", "on")
        await coord._single_tap_down()
        coord.hass.services.async_call.assert_any_call(
            "light", "turn_off", {"entity_id": LIGHT, "transition": 1}, blocking=False
        )

    async def test_40_double_tap_up(self, coord):
        """Double tap up → enable dumb mode, clear override."""
        await coord._double_tap_up()
        coord.hass.services.async_call.assert_any_call(
            "switch", "turn_on",
            {"entity_id": f"switch.{ZONE_ID}_automation_disabled"},
            blocking=False,
        )
        coord.hass.services.async_call.assert_any_call(
            "switch", "turn_off",
            {"entity_id": f"switch.{ZONE_ID}_motion_blocker"},
            blocking=False,
        )

    async def test_41_double_tap_down_sensors_on(self, coord):
        """Double tap down: exit dumb mode, restart motion task when sensors active."""
        coord._sensors_any_on = True
        coord._restart_motion_task = MagicMock()
        await coord._double_tap_down()
        coord.hass.services.async_call.assert_any_call(
            "switch", "turn_off",
            {"entity_id": f"switch.{ZONE_ID}_automation_disabled"},
            blocking=False,
        )
        coord._restart_motion_task.assert_called_once()

    async def test_42_double_tap_down_sensors_off(self, coord):
        """Double tap down: exit dumb mode, light off if no sensors."""
        coord._sensors_any_on = False
        await coord._double_tap_down()
        off_calls = [
            c for c in coord.hass.services.async_call.call_args_list
            if c.args[:2] == ("light", "turn_off")
        ]
        assert len(off_calls) > 0

    async def test_43_scene3_reset(self, coord):
        """Scene 3: clear both flags and turn off light."""
        await coord._scene3_reset()
        coord.hass.services.async_call.assert_any_call(
            "switch", "turn_off",
            {"entity_id": f"switch.{ZONE_ID}_automation_disabled"},
            blocking=False,
        )
        coord.hass.services.async_call.assert_any_call(
            "switch", "turn_off",
            {"entity_id": f"switch.{ZONE_ID}_motion_blocker"},
            blocking=False,
        )
        coord.hass.services.async_call.assert_any_call(
            "light", "turn_off", {"entity_id": LIGHT, "transition": 1}, blocking=False
        )


# ---------------------------------------------------------------------------
# 44–49  Dim window boundary handlers
# ---------------------------------------------------------------------------

class TestDimWindowHandlers:

    async def test_44_dim_start_applies_dim_brightness(self, coord, states):
        states.put(LIGHT, "on")
        coord._handle_dim_window_start(None)
        await asyncio.sleep(0)  # let the scheduled coroutine run
        coord.hass.services.async_call.assert_called_once()
        call_kwargs = coord.hass.services.async_call.call_args
        assert call_kwargs.args[1] == "turn_on"
        assert call_kwargs.args[2]["brightness_pct"] == 10   # dim_brightness default

    def test_45_dim_start_skipped_when_nightlight_off(self, coord, states):
        states.put(LIGHT, "on")
        states.put(f"switch.{ZONE_ID}_nightlight_enabled", "off")
        coord._handle_dim_window_start(None)
        coord.hass.services.async_call.assert_not_called()

    def test_46_dim_start_skipped_when_light_off(self, coord, states):
        states.put(LIGHT, "off")
        coord._handle_dim_window_start(None)
        coord.hass.services.async_call.assert_not_called()

    def test_47_dim_start_skipped_when_automation_disabled(self, coord, states):
        states.put(LIGHT, "on")
        states.put(f"switch.{ZONE_ID}_automation_disabled", "on")
        coord._handle_dim_window_start(None)
        coord.hass.services.async_call.assert_not_called()

    async def test_48_dim_end_applies_normal_brightness(self, coord, states):
        states.put(LIGHT, "on")
        coord._handle_dim_window_end(None)
        await asyncio.sleep(0)  # let the scheduled coroutine run
        coord.hass.services.async_call.assert_called_once()
        call_kwargs = coord.hass.services.async_call.call_args
        assert call_kwargs.args[1] == "turn_on"
        assert call_kwargs.args[2]["brightness_pct"] == 100  # normal_brightness default

    def test_49_dim_end_skipped_when_light_off(self, coord, states):
        states.put(LIGHT, "off")
        # Use MagicMock so no loop needed; service call should never happen
        coord.hass.async_create_task = MagicMock()
        coord._handle_dim_window_end(None)
        coord.hass.async_create_task.assert_not_called()


# ---------------------------------------------------------------------------
# 50–52  Sensor unavailable
# ---------------------------------------------------------------------------

class TestSensorUnavailable:

    async def test_50_unavailable_creates_notification(self, coord):
        sensor_state = _s("unavailable", {"friendly_name": "Hallway Motion 1"})
        await coord._notify_sensor_unavailable(SENSOR_1, sensor_state)
        coord.hass.services.async_call.assert_called_once_with(
            "persistent_notification", "create",
            {
                "notification_id": f"{ZONE_ID}_sensor_unavailable_binary_sensor_motion_1",
                "title": "Hallway: Motion Sensor Unavailable",
                "message": (
                    "Hallway Motion 1 is unavailable. "
                    "Hallway motion detection may be impaired. "
                    "Check your Z-Wave network."
                ),
            },
            blocking=False,
        )

    async def test_51_recovery_dismisses_that_sensors_notification(self, coord, states):
        states.put(SENSOR_1, "off")
        await coord._clear_sensor_unavailable(SENSOR_1)
        coord.hass.services.async_call.assert_called_once_with(
            "persistent_notification", "dismiss",
            {"notification_id": f"{ZONE_ID}_sensor_unavailable_binary_sensor_motion_1"},
            blocking=False,
        )

    async def test_52_notification_ids_are_per_sensor(self, coord):
        """One notification per sensor, not per zone (2026-08-10 review).

        With a single shared per-zone id, the second sensor to drop out
        overwrote the first one's alert — so a multi-sensor zone could only
        ever name one of them — and the first sensor to recover dismissed the
        alert for every sensor still down.
        """
        await coord._notify_sensor_unavailable(SENSOR_1, _s("unavailable", {"friendly_name": "Motion 1"}))
        await coord._notify_sensor_unavailable(SENSOR_2, _s("unavailable", {"friendly_name": "Motion 2"}))

        ids = [c.args[2]["notification_id"] for c in coord.hass.services.async_call.call_args_list]
        assert len(set(ids)) == 2

        # Sensor 1 recovering leaves sensor 2's alert standing.
        coord.hass.services.async_call.reset_mock()
        await coord._clear_sensor_unavailable(SENSOR_1)
        dismissed = [c.args[2]["notification_id"] for c in coord.hass.services.async_call.call_args_list]
        assert dismissed == [f"{ZONE_ID}_sensor_unavailable_binary_sensor_motion_1"]


# ---------------------------------------------------------------------------
# 53–55  Motion group state
# ---------------------------------------------------------------------------

class TestMotionGroup:

    def test_53_any_sensor_on_sets_flag(self, coord, states):
        states.put(SENSOR_1, "on")
        states.put(SENSOR_2, "off")
        coord._handle_sensor_change(Event({
            "entity_id": SENSOR_1,
            "new_state": _s("on"),
            "old_state": _s("off"),
        }))
        assert coord._sensors_any_on is True

    def test_54_all_sensors_off_clears_flag(self, coord, states):
        coord._sensors_any_on = True
        coord._handle_sensor_change(Event({
            "entity_id": SENSOR_1,
            "new_state": _s("off"),
            "old_state": _s("on"),
        }))
        assert coord._sensors_any_on is False

    def test_55_state_change_updates_motion_group_entity(self, coord, states):
        mock_entity = MagicMock()
        coord.motion_group_entity = mock_entity
        coord._handle_sensor_change(Event({
            "entity_id": SENSOR_1,
            "new_state": _s("on"),
            "old_state": _s("off"),
        }))
        mock_entity.async_write_ha_state.assert_called_once()


# ---------------------------------------------------------------------------
# 56–63  Light-change dispatch (_handle_light_change)
#
# 2026-08-02: a non-Central-Scene companion/add-on switch turned the hallway
# light on at a stale ~1% nightlight level in broad daylight, and nothing
# was watching to correct or ever turn it back off — this listener used to
# stand down entirely whenever it wasn't dark enough. It now routes every
# switch-triggered on (main paddle or companion) into the same full-bright,
# auto-shutoff hold regardless of time of day; only its own previously-
# commanded changes (tracked via context id) and Disabled mode are skipped.
# The hold/brightness/timeout logic itself is covered separately below.
# ---------------------------------------------------------------------------

class TestLightChangeDispatch:

    def test_56_new_state_none_is_noop(self, coord):
        coord._start_switch_on_hold = AsyncMock()
        coord._handle_light_change(Event({"new_state": None}))
        coord._start_switch_on_hold.assert_not_called()

    def test_57_light_turning_off_is_noop(self, coord):
        coord._start_switch_on_hold = AsyncMock()
        coord._handle_light_change(Event({"new_state": _s("off")}))
        coord._start_switch_on_hold.assert_not_called()

    def test_58_automation_disabled_is_noop(self, coord, states):
        states.put(f"switch.{ZONE_ID}_automation_disabled", "on")
        coord._start_switch_on_hold = AsyncMock()
        coord._handle_light_change(Event({"new_state": _s("on", {"brightness": 255})}))
        coord._start_switch_on_hold.assert_not_called()

    async def test_59_own_context_is_noop(self, coord):
        """A state change carrying the context id from one of our own recent
        _light_on calls (motion sequence, dim-window re-assertion, our own
        switch-hold turn-on) must not be treated as an external switch
        press — otherwise every self-commanded on would re-trigger itself."""
        coord._own_light_context_ids.append("test-ctx-1")
        coord._start_switch_on_hold = AsyncMock()
        fake_context = MagicMock()
        fake_context.id = "test-ctx-1"
        coord._handle_light_change(
            Event({"new_state": _s("on", {"brightness": 255})}, context=fake_context)
        )
        await asyncio.sleep(0)
        coord._start_switch_on_hold.assert_not_called()

    async def test_59b_own_context_still_matches_behind_a_newer_light_on(self, coord):
        """Two _light_on calls can be in flight at once — a dim-window
        boundary landing on top of a motion trigger at 00:00. With a single
        context slot the older call's state event arrived after the newer one
        had overwritten it, so Luminary's own command was misread as an
        external switch press. A ring of recent ids keeps both recognised
        (2026-08-10 review)."""
        coord._start_switch_on_hold = AsyncMock()
        await coord._light_on(10)   # e.g. dim-window re-assertion
        await coord._light_on(100)  # motion trigger right behind it
        first_ctx_id = coord._own_light_context_ids[0]

        stale_context = MagicMock()
        stale_context.id = first_ctx_id
        coord._handle_light_change(
            Event({"new_state": _s("on", {"brightness": 25})}, context=stale_context)
        )
        await asyncio.sleep(0)
        coord._start_switch_on_hold.assert_not_called()

    async def test_60_external_on_dispatches_hold(self, coord, states):
        states.put("sun.sun", "below_horizon", {"elevation": -5.0})
        coord._start_switch_on_hold = AsyncMock()
        coord._handle_light_change(Event({"new_state": _s("on", {"brightness": 255})}))
        await asyncio.sleep(0)
        coord._start_switch_on_hold.assert_called_once()

    async def test_61_external_on_during_daytime_still_dispatches(self, coord, states):
        """No more is_dark_enough() gate — a switch always means full
        brightness, day or night (this is the exact 2026-08-02 gap: the
        light turning on at 1% at 7:51 AM was ignored entirely before)."""
        states.put("sun.sun", "above_horizon", {"elevation": 27.7})
        coord._start_switch_on_hold = AsyncMock()
        coord._handle_light_change(Event({"new_state": _s("on", {"brightness": 3})}))
        await asyncio.sleep(0)
        coord._start_switch_on_hold.assert_called_once()

    async def test_62_external_on_while_blocker_already_on_still_dispatches(self, coord, states):
        """A fresh external on-report while motion_blocker is already on
        (an existing hold in progress) renews the hold rather than being
        ignored — renewed switch activity should reset the auto-shutoff
        clock, not be silently absorbed."""
        states.put(f"switch.{ZONE_ID}_motion_blocker", "on")
        coord._start_switch_on_hold = AsyncMock()
        coord._handle_light_change(Event({"new_state": _s("on", {"brightness": 255})}))
        await asyncio.sleep(0)
        coord._start_switch_on_hold.assert_called_once()

    async def test_63_no_context_on_event_still_dispatches(self, coord):
        """Defensive: an event with no context at all (context=None) must
        still be treated as external, not accidentally swallowed."""
        coord._start_switch_on_hold = AsyncMock()
        coord._handle_light_change(Event({"new_state": _s("on", {"brightness": 255})}, context=None))
        await asyncio.sleep(0)
        coord._start_switch_on_hold.assert_called_once()

    async def test_63b_on_to_on_update_is_noop(self, coord):
        """Only a real off->on edge counts as a switch press.

        async_track_state_change_event also fires for attribute-only updates,
        so without an old_state check any re-write while the light is already
        on reads as a switch press. HA only stamps the originating context on
        entity writes for ~5s after the service call, so a Z-Wave dimmer's
        delayed confirming report falls outside the context check above and
        would snap a 10% nightlight activation straight to 100% — defeating
        nightlight dimming outright (2026-08-10 review).
        """
        coord._start_switch_on_hold = AsyncMock()
        coord._handle_light_change(Event({
            "old_state": _s("on", {"brightness": 25}),
            "new_state": _s("on", {"brightness": 26}),
        }))
        await asyncio.sleep(0)
        coord._start_switch_on_hold.assert_not_called()

    async def test_63c_off_to_on_edge_still_dispatches(self, coord):
        """The companion-switch case the old_state guard must not break: a
        genuine off->on from a switch is still an off->on edge."""
        coord._start_switch_on_hold = AsyncMock()
        coord._handle_light_change(Event({
            "old_state": _s("off"),
            "new_state": _s("on", {"brightness": 255}),
        }))
        await asyncio.sleep(0)
        coord._start_switch_on_hold.assert_called_once()

    async def test_63d_unavailable_to_on_still_dispatches(self, coord):
        """A light coming back from unavailable already on (power restored to
        the circuit) is also not an on->on update — still external."""
        coord._start_switch_on_hold = AsyncMock()
        coord._handle_light_change(Event({
            "old_state": _s("unavailable"),
            "new_state": _s("on", {"brightness": 255}),
        }))
        await asyncio.sleep(0)
        coord._start_switch_on_hold.assert_called_once()


# ---------------------------------------------------------------------------
# 64–71  Switch-triggered on: full brightness + auto-shutoff
# (_start_switch_on_hold / _run_switch_on_sequence)
# ---------------------------------------------------------------------------

class TestSwitchOnHold:

    async def test_64_smart_mode_starts_hold(self, coord, states):
        states.put(f"switch.{ZONE_ID}_automation_disabled", "off")
        # Avoid actually running the long-lived sequence in this test —
        # covered separately below.
        coord._run_switch_on_sequence = AsyncMock()
        await coord._start_switch_on_hold()
        coord.hass.services.async_call.assert_any_call(
            "switch", "turn_on",
            {"entity_id": f"switch.{ZONE_ID}_motion_blocker"},
            blocking=True,
        )
        coord.hass.services.async_call.assert_any_call(
            "light", "turn_on",
            {"entity_id": LIGHT, "brightness_pct": 100, "transition": 1},
            blocking=False,
            context=ANY,
        )
        assert coord._motion_task is not None

    async def test_65_dumb_mode_is_full_noop(self, coord, states):
        states.put(f"switch.{ZONE_ID}_automation_disabled", "on")
        await coord._start_switch_on_hold()
        coord.hass.services.async_call.assert_not_called()
        assert coord._motion_task is None

    async def test_66_cancels_in_progress_task_first(self, coord, states):
        states.put(f"switch.{ZONE_ID}_automation_disabled", "off")
        coord._run_switch_on_sequence = AsyncMock()
        fake_task = MagicMock()
        fake_task.done.return_value = False
        coord._motion_task = fake_task
        await coord._start_switch_on_hold()
        fake_task.cancel.assert_called_once()

    async def test_67_sensors_clear_within_timeout_turns_off_after_grace(self, coord, states):
        """Sensors already clear → cleared immediately, light off after the
        light_on_time_sec grace period, blocker released."""
        states.put(f"number.{ZONE_ID}_light_on_time_sec", "0")
        states.put(f"number.{ZONE_ID}_switch_on_timeout_minutes", "60")
        states.put(SENSOR_1, "off")
        states.put(SENSOR_2, "off")

        with patch("custom_components.luminary_ha.coordinator.async_track_state_change_event", return_value=lambda: None):
            await coord._run_switch_on_sequence()

        coord.hass.services.async_call.assert_any_call(
            "light", "turn_off", {"entity_id": LIGHT, "transition": 1}, blocking=False
        )
        coord.hass.services.async_call.assert_any_call(
            "switch", "turn_off",
            {"entity_id": f"switch.{ZONE_ID}_motion_blocker"},
            blocking=True,
        )

    async def test_68_never_clears_hits_cap_and_turns_off_anyway(self, coord, states):
        """Sensors stay "on" the whole time (or a sensor is stuck) → the
        overall switch_on_timeout_sec cap fires and the light is turned off
        regardless — the actual point of an auto-shutoff safety net."""
        states.put(f"number.{ZONE_ID}_switch_on_timeout_minutes", str(0.02 / 60))  # ~1.2ms cap
        states.put(SENSOR_1, "on")

        with patch("custom_components.luminary_ha.coordinator.async_track_state_change_event", return_value=lambda: None):
            await coord._run_switch_on_sequence()

        coord.hass.services.async_call.assert_any_call(
            "light", "turn_off", {"entity_id": LIGHT, "transition": 1}, blocking=False
        )

    async def test_69_retrigger_during_grace_period_loops_and_stays_on(self, coord, states):
        """A new on-event fires again during the grace window → loop back
        and wait for clear again instead of turning off out from under
        someone still there.

        Event-driven now, not a single instantaneous end-of-sleep sample
        (2026-08-10 fix) — a sensor with a short onboard hardware
        clear-timeout can report a brief off-blip mid-occupancy, and the
        old sample-once check could land right on top of one of those
        blips despite continuous real presence.
        """
        states.put(f"number.{ZONE_ID}_light_on_time_sec", "60")
        states.put(f"number.{ZONE_ID}_switch_on_timeout_minutes", "60")
        states.put(SENSOR_1, "off")
        states.put(SENSOR_2, "off")

        captured = []

        def fake_track(hass, entities, cb):
            captured.append(cb)
            return lambda: None

        with patch("custom_components.luminary_ha.coordinator.async_track_state_change_event", fake_track):
            task = asyncio.ensure_future(coord._run_switch_on_sequence())
            await asyncio.sleep(0)  # let it reach the grace-period wait (1st pass)

            # Retrigger during the grace window
            states.put(SENSOR_1, "on")
            captured[-1](Event({"entity_id": SENSOR_1, "new_state": _s("on")}))
            await asyncio.sleep(0)  # loop back to the top, re-subscribe

            # Let the sensors genuinely clear for the 2nd pass, and shrink
            # the grace window so it finishes without a real 60s wait
            states.put(f"number.{ZONE_ID}_light_on_time_sec", "0")
            states.put(SENSOR_1, "off")
            captured[-1](Event({"entity_id": SENSOR_1, "new_state": _s("off")}))

            await task

        coord.hass.services.async_call.assert_any_call(
            "light", "turn_off", {"entity_id": LIGHT, "transition": 1}, blocking=False
        )

    async def test_70_automation_disabled_during_wait_skips_off(self, coord, states):
        # Sensors are already clear (defaults), so cleared.wait() resolves
        # instantly regardless of switch_on_timeout_sec — light_on_time_sec
        # is what actually needs to stay small here, or this real-sleeps for
        # the full default 60s grace period.
        states.put(f"number.{ZONE_ID}_light_on_time_sec", "0")
        states.put(f"switch.{ZONE_ID}_automation_disabled", "on")

        with patch("custom_components.luminary_ha.coordinator.async_track_state_change_event", return_value=lambda: None):
            await coord._run_switch_on_sequence()

        off_calls = [c for c in coord.hass.services.async_call.call_args_list if c.args[:2] == ("light", "turn_off")]
        assert len(off_calls) == 0

    async def test_70b_automation_disabled_during_wait_still_releases_blocker(self, coord, states):
        """Disabled mode skips the light-off but must not skip the blocker
        release (2026-08-10 review).

        _start_switch_on_hold is the only thing that raises motion_blocker and
        this sequence is the only thing that lowers it, so returning early
        here latched the zone in "Manual Override" permanently — the same
        failure class as the 2026-08-02 single-tap-down latch. Reachable by
        flipping automation_disabled from the HA UI mid-hold; double-tap-up
        clears the blocker itself and so never exposed it.
        """
        states.put(f"number.{ZONE_ID}_light_on_time_sec", "0")
        states.put(f"switch.{ZONE_ID}_automation_disabled", "on")

        with patch("custom_components.luminary_ha.coordinator.async_track_state_change_event", return_value=lambda: None):
            await coord._run_switch_on_sequence()

        coord.hass.services.async_call.assert_any_call(
            "switch", "turn_off",
            {"entity_id": f"switch.{ZONE_ID}_motion_blocker"},
            blocking=True,
        )

    async def test_70c_cap_reached_releases_blocker(self, coord, states):
        """The other non-cancellation exit — the overall auto-shutoff cap —
        must release the blocker too."""
        states.put(f"number.{ZONE_ID}_switch_on_timeout_minutes", str(0.02 / 60))  # ~1.2ms cap
        states.put(SENSOR_1, "on")

        with patch("custom_components.luminary_ha.coordinator.async_track_state_change_event", return_value=lambda: None):
            await coord._run_switch_on_sequence()

        coord.hass.services.async_call.assert_any_call(
            "switch", "turn_off",
            {"entity_id": f"switch.{ZONE_ID}_motion_blocker"},
            blocking=True,
        )

    async def test_71_cancelled_error_propagates(self, coord, states):
        """Sensors already clear (defaults), so the sequence moves straight
        to the light_on_time_sec grace sleep (default 60s) — cancelling
        there must propagate cleanly, same pattern as the motion sequence's
        equivalent guard."""
        with patch("custom_components.luminary_ha.coordinator.async_track_state_change_event", return_value=lambda: None):
            task = asyncio.ensure_future(coord._run_switch_on_sequence())
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        off_calls = [c for c in coord.hass.services.async_call.call_args_list if c.args[:2] == ("light", "turn_off")]
        assert len(off_calls) == 0

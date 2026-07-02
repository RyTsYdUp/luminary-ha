"""Tests for config_flow.py's confirm-timeout loop and pure-logic helpers."""
from __future__ import annotations

from unittest.mock import patch

from custom_components.luminary_ha import config_flow
from custom_components.luminary_ha.const import (
    CONF_LIGHT,
    CONF_SENSOR_HW_TIMEOUTS,
    CONF_SENSORS,
    CONF_SWITCH_DEVICE,
    CONF_ZONE_NAME,
)
from custom_components.luminary_ha.hw_timeout import DetectionResult

from tests.conftest import FakeArea, FakeAreaRegistry
from tests.test_coordinator import LIGHT, SENSOR_1, SENSOR_2, _make_hass

AREA_ID = "area_pantry"


# ---------------------------------------------------------------------------
# Pure-logic helpers
# ---------------------------------------------------------------------------

def test_build_timeout_schema_with_default():
    schema = config_flow.build_timeout_schema(15.0)
    (key,) = schema.schema.keys()
    assert key.default() == 15.0


def test_build_timeout_schema_without_default():
    import voluptuous as vol
    schema = config_flow.build_timeout_schema(None)
    (key,) = schema.schema.keys()
    assert key.default is vol.UNDEFINED  # required, no prefill — user must type a value


def test_compute_sensors_to_confirm_new_sensor_included():
    result = config_flow.compute_sensors_to_confirm([SENSOR_1], {})
    assert result == [SENSOR_1]


def test_compute_sensors_to_confirm_tracked_sensor_excluded():
    existing = {SENSOR_1: {"timeout_sec": 10.0, "source": "zwave", "source_entity_id": "number.src"}}
    result = config_flow.compute_sensors_to_confirm([SENSOR_1], existing)
    assert result == []


def test_compute_sensors_to_confirm_manual_sensor_reincluded():
    existing = {SENSOR_1: {"timeout_sec": 10.0, "source": "manual", "source_entity_id": None}}
    result = config_flow.compute_sensors_to_confirm([SENSOR_1], existing)
    assert result == [SENSOR_1]  # manual/failed entries have no live source — re-offer


def test_prune_removed_sensors():
    existing = {
        SENSOR_1: {"timeout_sec": 10.0, "source": "manual", "source_entity_id": None},
        SENSOR_2: {"timeout_sec": 20.0, "source": "manual", "source_entity_id": None},
    }
    result = config_flow.prune_removed_sensors([SENSOR_1], existing)
    assert result == {SENSOR_1: existing[SENSOR_1]}


# ---------------------------------------------------------------------------
# LuminaryConfigFlow — full flow simulation
# ---------------------------------------------------------------------------

def _area_registry():
    reg = FakeAreaRegistry()
    reg.add(FakeArea(AREA_ID, "Pantry"))
    return reg


async def test_user_step_shows_form_with_no_input():
    flow = config_flow.LuminaryConfigFlow()
    flow.hass = _make_hass()
    result = await flow.async_step_user(None)
    assert result["type"] == "form"
    assert result["step_id"] == "user"


async def test_user_step_area_not_found_error():
    flow = config_flow.LuminaryConfigFlow()
    flow.hass = _make_hass()
    with patch("custom_components.luminary_ha.config_flow.ar.async_get", return_value=FakeAreaRegistry()):
        result = await flow.async_step_user({
            "area_id": AREA_ID, CONF_SENSORS: [SENSOR_1], CONF_LIGHT: LIGHT,
        })
    assert result["errors"]["area_id"] == "area_not_found"


async def test_user_step_no_sensors_error():
    flow = config_flow.LuminaryConfigFlow()
    flow.hass = _make_hass()
    with patch("custom_components.luminary_ha.config_flow.ar.async_get", return_value=_area_registry()):
        result = await flow.async_step_user({
            "area_id": AREA_ID, CONF_SENSORS: [], CONF_LIGHT: LIGHT,
        })
    assert result["errors"][CONF_SENSORS] == "no_sensors"


async def test_user_step_valid_input_enters_confirm_timeout():
    flow = config_flow.LuminaryConfigFlow()
    flow.hass = _make_hass()
    detection = DetectionResult.success(SENSOR_1, 13.0, "zwave", "number.src_1")

    with patch("custom_components.luminary_ha.config_flow.ar.async_get", return_value=_area_registry()), \
         patch("custom_components.luminary_ha.hw_timeout.async_detect_hw_timeout", return_value=detection):
        result = await flow.async_step_user({
            "area_id": AREA_ID, CONF_SENSORS: [SENSOR_1], CONF_LIGHT: LIGHT,
        })

    assert result["type"] == "form"
    assert result["step_id"] == "confirm_timeout"
    assert result["errors"] == {}
    (key,) = result["data_schema"].schema.keys()
    assert key.default() == 13.0


async def test_full_flow_two_sensors_creates_entry_with_hw_timeouts():
    flow = config_flow.LuminaryConfigFlow()
    flow.hass = _make_hass()
    detections = {
        SENSOR_1: DetectionResult.success(SENSOR_1, 10.0, "zwave", "number.src_1"),
        SENSOR_2: DetectionResult.success(SENSOR_2, 30.0, "zwave", "number.src_2"),
    }

    async def fake_detect(hass, sensor_entity_id):
        return detections[sensor_entity_id]

    with patch("custom_components.luminary_ha.config_flow.ar.async_get", return_value=_area_registry()), \
         patch("custom_components.luminary_ha.hw_timeout.async_detect_hw_timeout", side_effect=fake_detect):
        result = await flow.async_step_user({
            "area_id": AREA_ID, CONF_SENSORS: [SENSOR_1, SENSOR_2], CONF_LIGHT: LIGHT,
        })
        assert result["step_id"] == "confirm_timeout"
        # accept the auto-detected default for sensor 1
        result = await flow.async_step_confirm_timeout({"hw_timeout_sec": 10.0})
        assert result["type"] == "form"  # second sensor still pending
        # accept the auto-detected default for sensor 2
        result = await flow.async_step_confirm_timeout({"hw_timeout_sec": 30.0})

    assert result["type"] == "create_entry"
    assert result["title"] == "Pantry"
    hw_timeouts = result["options"][CONF_SENSOR_HW_TIMEOUTS]
    assert hw_timeouts[SENSOR_1] == {"timeout_sec": 10.0, "source": "zwave", "source_entity_id": "number.src_1"}
    assert hw_timeouts[SENSOR_2] == {"timeout_sec": 30.0, "source": "zwave", "source_entity_id": "number.src_2"}


async def test_detection_failure_shows_error_but_accepts_manual_entry():
    flow = config_flow.LuminaryConfigFlow()
    flow.hass = _make_hass()
    detection = DetectionResult.failure(SENSOR_1, "no_matching_parameter")

    with patch("custom_components.luminary_ha.config_flow.ar.async_get", return_value=_area_registry()), \
         patch("custom_components.luminary_ha.hw_timeout.async_detect_hw_timeout", return_value=detection):
        result = await flow.async_step_user({
            "area_id": AREA_ID, CONF_SENSORS: [SENSOR_1], CONF_LIGHT: LIGHT,
        })
        assert result["errors"]["base"] == "detection_failed"

        result = await flow.async_step_confirm_timeout({"hw_timeout_sec": 22.0})

    assert result["type"] == "create_entry"
    hw_timeouts = result["options"][CONF_SENSOR_HW_TIMEOUTS]
    assert hw_timeouts[SENSOR_1] == {"timeout_sec": 22.0, "source": "manual", "source_entity_id": None}


async def test_editing_auto_filled_value_downgrades_to_manual():
    """If the user overrides the auto-detected default, it's recorded as manual."""
    flow = config_flow.LuminaryConfigFlow()
    flow.hass = _make_hass()
    detection = DetectionResult.success(SENSOR_1, 10.0, "zwave", "number.src_1")

    with patch("custom_components.luminary_ha.config_flow.ar.async_get", return_value=_area_registry()), \
         patch("custom_components.luminary_ha.hw_timeout.async_detect_hw_timeout", return_value=detection):
        await flow.async_step_user({"area_id": AREA_ID, CONF_SENSORS: [SENSOR_1], CONF_LIGHT: LIGHT})
        result = await flow.async_step_confirm_timeout({"hw_timeout_sec": 99.0})  # user typed something else

    hw_timeouts = result["options"][CONF_SENSOR_HW_TIMEOUTS]
    assert hw_timeouts[SENSOR_1] == {"timeout_sec": 99.0, "source": "manual", "source_entity_id": None}


# ---------------------------------------------------------------------------
# LuminaryOptionsFlow
# ---------------------------------------------------------------------------

class _FakeConfigEntry:
    def __init__(self, options):
        self.options = options


async def test_options_flow_skips_already_tracked_sensors():
    existing_options = {
        CONF_SENSORS: [SENSOR_1],
        CONF_LIGHT: LIGHT,
        CONF_SWITCH_DEVICE: None,
        CONF_SENSOR_HW_TIMEOUTS: {
            SENSOR_1: {"timeout_sec": 10.0, "source": "zwave", "source_entity_id": "number.src_1"},
        },
    }
    entry = _FakeConfigEntry(existing_options)
    flow = config_flow.LuminaryOptionsFlow(entry)
    flow.hass = _make_hass()

    # SENSOR_1 already live-tracked -> should go straight to create_entry, no form
    result = await flow.async_step_init({
        CONF_SENSORS: [SENSOR_1], CONF_LIGHT: LIGHT, CONF_SWITCH_DEVICE: None,
    })

    assert result["type"] == "create_entry"
    assert result["data"][CONF_SENSOR_HW_TIMEOUTS] == existing_options[CONF_SENSOR_HW_TIMEOUTS]


async def test_options_flow_prompts_only_for_new_sensor():
    existing_options = {
        CONF_SENSORS: [SENSOR_1],
        CONF_LIGHT: LIGHT,
        CONF_SWITCH_DEVICE: None,
        CONF_SENSOR_HW_TIMEOUTS: {
            SENSOR_1: {"timeout_sec": 10.0, "source": "zwave", "source_entity_id": "number.src_1"},
        },
    }
    entry = _FakeConfigEntry(existing_options)
    flow = config_flow.LuminaryOptionsFlow(entry)
    flow.hass = _make_hass()
    detection = DetectionResult.success(SENSOR_2, 25.0, "zigbee2mqtt", "number.src_2")

    with patch("custom_components.luminary_ha.hw_timeout.async_detect_hw_timeout", return_value=detection):
        result = await flow.async_step_init({
            CONF_SENSORS: [SENSOR_1, SENSOR_2], CONF_LIGHT: LIGHT, CONF_SWITCH_DEVICE: None,
        })
        assert result["step_id"] == "confirm_timeout"  # only prompted for SENSOR_2
        result = await flow.async_step_confirm_timeout({"hw_timeout_sec": 25.0})

    hw_timeouts = result["data"][CONF_SENSOR_HW_TIMEOUTS]
    assert hw_timeouts[SENSOR_1] == existing_options[CONF_SENSOR_HW_TIMEOUTS][SENSOR_1]  # preserved
    assert hw_timeouts[SENSOR_2] == {"timeout_sec": 25.0, "source": "zigbee2mqtt", "source_entity_id": "number.src_2"}


async def test_options_flow_prunes_removed_sensor():
    existing_options = {
        CONF_SENSORS: [SENSOR_1, SENSOR_2],
        CONF_LIGHT: LIGHT,
        CONF_SWITCH_DEVICE: None,
        CONF_SENSOR_HW_TIMEOUTS: {
            SENSOR_1: {"timeout_sec": 10.0, "source": "zwave", "source_entity_id": "number.src_1"},
            SENSOR_2: {"timeout_sec": 20.0, "source": "zwave", "source_entity_id": "number.src_2"},
        },
    }
    entry = _FakeConfigEntry(existing_options)
    flow = config_flow.LuminaryOptionsFlow(entry)
    flow.hass = _make_hass()

    # SENSOR_2 removed from the zone
    result = await flow.async_step_init({
        CONF_SENSORS: [SENSOR_1], CONF_LIGHT: LIGHT, CONF_SWITCH_DEVICE: None,
    })

    assert result["type"] == "create_entry"
    hw_timeouts = result["data"][CONF_SENSOR_HW_TIMEOUTS]
    assert set(hw_timeouts.keys()) == {SENSOR_1}


async def test_options_flow_no_sensors_error():
    entry = _FakeConfigEntry({CONF_SENSORS: [], CONF_LIGHT: LIGHT, CONF_SWITCH_DEVICE: None})
    flow = config_flow.LuminaryOptionsFlow(entry)
    flow.hass = _make_hass()

    result = await flow.async_step_init({CONF_SENSORS: [], CONF_LIGHT: LIGHT, CONF_SWITCH_DEVICE: None})

    assert result["errors"][CONF_SENSORS] == "no_sensors"

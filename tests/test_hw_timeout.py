"""Unit tests for hw_timeout.py detection logic."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from custom_components.luminary_ha import hw_timeout
from tests.conftest import FakeDeviceEntry, FakeRegistryEntry, make_fake_registries

SENSOR = "binary_sensor.pantry_motion_motion_detection"


def _s(state: str):
    m = MagicMock()
    m.state = state
    return m


def _make_hass(states: dict[str, str] | None = None) -> MagicMock:
    hass = MagicMock()
    values = states or {}
    hass.states.get = lambda eid: _s(values[eid]) if eid in values else None
    return hass


def _patched(ent_reg, dev_reg):
    return (
        patch.object(hw_timeout.er, "async_get", return_value=ent_reg),
        patch.object(hw_timeout.dr, "async_get", return_value=dev_reg),
    )


async def test_zwave_param_map_hit():
    ent_reg, dev_reg = make_fake_registries()
    ent_reg.add(FakeRegistryEntry(SENSOR, unique_id="4246878805.48-113-0-Home Security-Motion sensor status.8",
                                   platform="zwave_js", device_id="dev1"))
    ent_reg.add(FakeRegistryEntry("number.pantry_motion_motion_detection_timeout",
                                   unique_id="4246878805.48-112-0-13", platform="zwave_js", device_id="dev1",
                                   original_name="Motion Detection: Timeout", disabled_by="integration"))
    dev_reg.add(FakeDeviceEntry("dev1", manufacturer="Zooz", model="ZSE11"))
    hass = _make_hass({"number.pantry_motion_motion_detection_timeout": "10"})

    p1, p2 = _patched(ent_reg, dev_reg)
    with p1, p2:
        result = await hw_timeout.async_detect_hw_timeout(hass, SENSOR)

    assert result.ok is True
    assert result.value == 10.0
    assert result.source == "zwave"
    assert result.source_entity_id == "number.pantry_motion_motion_detection_timeout"


async def test_zwave_value_unavailable_when_disabled():
    """Real-world case: the Configuration CC entity is disabled by default (Step 0 finding)."""
    ent_reg, dev_reg = make_fake_registries()
    ent_reg.add(FakeRegistryEntry(SENSOR, unique_id="4246878805.48-113-0-...", platform="zwave_js", device_id="dev1"))
    ent_reg.add(FakeRegistryEntry("number.pantry_motion_motion_detection_timeout",
                                   unique_id="4246878805.48-112-0-13", platform="zwave_js", device_id="dev1",
                                   original_name="Motion Detection: Timeout", disabled_by="integration"))
    dev_reg.add(FakeDeviceEntry("dev1", manufacturer="Zooz", model="ZSE11"))
    hass = _make_hass({})  # disabled entity has no state at all

    p1, p2 = _patched(ent_reg, dev_reg)
    with p1, p2:
        result = await hw_timeout.async_detect_hw_timeout(hass, SENSOR)

    assert result.ok is False
    assert result.reason == "value_unavailable"
    assert result.source == "zwave"
    assert result.source_entity_id == "number.pantry_motion_motion_detection_timeout"


async def test_zwave_keyword_fallback_for_unmapped_model():
    ent_reg, dev_reg = make_fake_registries()
    ent_reg.add(FakeRegistryEntry(SENSOR, unique_id="123.9-113-0-x", platform="zwave_js", device_id="dev1"))
    ent_reg.add(FakeRegistryEntry("number.some_other_timeout", unique_id="123.9-112-0-7", platform="zwave_js",
                                   device_id="dev1", original_name="Re-trigger Duration"))
    dev_reg.add(FakeDeviceEntry("dev1", manufacturer="AcmeCo", model="XYZ"))  # not in ZWAVE_CONFIG_PARAM_TIMEOUT_MAP
    hass = _make_hass({"number.some_other_timeout": "30"})

    p1, p2 = _patched(ent_reg, dev_reg)
    with p1, p2:
        result = await hw_timeout.async_detect_hw_timeout(hass, SENSOR)

    assert result.ok is True
    assert result.value == 30.0
    assert result.source_entity_id == "number.some_other_timeout"


async def test_zwave_no_matching_parameter():
    ent_reg, dev_reg = make_fake_registries()
    ent_reg.add(FakeRegistryEntry(SENSOR, unique_id="123.9-113-0-x", platform="zwave_js", device_id="dev1"))
    ent_reg.add(FakeRegistryEntry("number.unrelated", unique_id="123.9-112-0-99", platform="zwave_js",
                                   device_id="dev1", original_name="LED Brightness"))
    dev_reg.add(FakeDeviceEntry("dev1", manufacturer="AcmeCo", model="XYZ"))
    hass = _make_hass({"number.unrelated": "5"})

    p1, p2 = _patched(ent_reg, dev_reg)
    with p1, p2:
        result = await hw_timeout.async_detect_hw_timeout(hass, SENSOR)

    assert result.ok is False
    assert result.reason == "no_matching_parameter"


async def test_zigbee2mqtt_keyword_hit():
    ent_reg, dev_reg = make_fake_registries()
    z2m_sensor = "binary_sensor.laundry_room_motion_occupancy"
    ent_reg.add(FakeRegistryEntry(z2m_sensor, unique_id="0x0017880109197d51_occupancy_zigbee2mqtt",
                                   platform="mqtt", device_id="dev2"))
    ent_reg.add(FakeRegistryEntry("number.laundry_room_motion_occupancy_timeout",
                                   unique_id="0x0017880109197d51_occupancy_timeout_zigbee2mqtt",
                                   platform="mqtt", device_id="dev2", original_name="Occupancy timeout"))
    dev_reg.add(FakeDeviceEntry("dev2", manufacturer="Philips", model="Hue motion sensor"))
    hass = _make_hass({"number.laundry_room_motion_occupancy_timeout": "0"})

    p1, p2 = _patched(ent_reg, dev_reg)
    with p1, p2:
        result = await hw_timeout.async_detect_hw_timeout(hass, z2m_sensor)

    assert result.ok is True
    assert result.value == 0.0
    assert result.source == "zigbee2mqtt"
    assert result.source_entity_id == "number.laundry_room_motion_occupancy_timeout"


async def test_zigbee2mqtt_no_hit():
    ent_reg, dev_reg = make_fake_registries()
    z2m_sensor = "binary_sensor.some_sensor_occupancy"
    ent_reg.add(FakeRegistryEntry(z2m_sensor, unique_id="0xabc_occupancy_zigbee2mqtt", platform="mqtt",
                                   device_id="dev3"))
    ent_reg.add(FakeRegistryEntry("sensor.some_sensor_battery", unique_id="0xabc_battery_zigbee2mqtt",
                                   platform="mqtt", device_id="dev3"))
    dev_reg.add(FakeDeviceEntry("dev3", manufacturer="Generic", model="Sensor"))
    hass = _make_hass({"sensor.some_sensor_battery": "80"})

    p1, p2 = _patched(ent_reg, dev_reg)
    with p1, p2:
        result = await hw_timeout.async_detect_hw_timeout(hass, z2m_sensor)

    assert result.ok is False
    assert result.reason == "no_matching_parameter"


async def test_missing_device_id():
    ent_reg, dev_reg = make_fake_registries()
    ent_reg.add(FakeRegistryEntry(SENSOR, unique_id="x", platform="zwave_js", device_id=None))

    p1, p2 = _patched(ent_reg, dev_reg)
    with p1, p2:
        result = await hw_timeout.async_detect_hw_timeout(_make_hass(), SENSOR)

    assert result.ok is False
    assert result.reason == "no_device"


async def test_missing_registry_entry():
    ent_reg, dev_reg = make_fake_registries()  # SENSOR never added

    p1, p2 = _patched(ent_reg, dev_reg)
    with p1, p2:
        result = await hw_timeout.async_detect_hw_timeout(_make_hass(), SENSOR)

    assert result.ok is False
    assert result.reason == "no_registry_entry"


async def test_unsupported_platform():
    ent_reg, dev_reg = make_fake_registries()
    ent_reg.add(FakeRegistryEntry(SENSOR, unique_id="x", platform="template", device_id="dev4"))
    dev_reg.add(FakeDeviceEntry("dev4"))

    p1, p2 = _patched(ent_reg, dev_reg)
    with p1, p2:
        result = await hw_timeout.async_detect_hw_timeout(_make_hass(), SENSOR)

    assert result.ok is False
    assert result.reason == "unsupported_platform"


async def test_async_detect_all_aggregates():
    ent_reg, dev_reg = make_fake_registries()
    sensor_a, sensor_b = "binary_sensor.a", "binary_sensor.b"
    ent_reg.add(FakeRegistryEntry(sensor_a, unique_id="x", platform="template", device_id="devA"))
    ent_reg.add(FakeRegistryEntry(sensor_b, unique_id="y", platform="template", device_id="devB"))
    dev_reg.add(FakeDeviceEntry("devA"))
    dev_reg.add(FakeDeviceEntry("devB"))

    p1, p2 = _patched(ent_reg, dev_reg)
    with p1, p2:
        results = await hw_timeout.async_detect_all(_make_hass(), [sensor_a, sensor_b])

    assert set(results.keys()) == {sensor_a, sensor_b}
    assert all(r.reason == "unsupported_platform" for r in results.values())


# ---------------------------------------------------------------------------
# async_detect_last_seen
# ---------------------------------------------------------------------------

async def test_last_seen_zwave_hit():
    ent_reg, dev_reg = make_fake_registries()
    ent_reg.add(FakeRegistryEntry(SENSOR, unique_id="4246878805.48-113-0-x", platform="zwave_js", device_id="dev1"))
    ent_reg.add(FakeRegistryEntry("sensor.pantry_motion_last_seen", unique_id="4246878805.48-x-last-seen",
                                   platform="zwave_js", device_id="dev1"))
    dev_reg.add(FakeDeviceEntry("dev1", manufacturer="Zooz", model="ZSE11"))
    hass = _make_hass({"sensor.pantry_motion_last_seen": "2026-07-13T12:28:08+00:00"})

    p1, p2 = _patched(ent_reg, dev_reg)
    with p1, p2:
        result = await hw_timeout.async_detect_last_seen(hass, SENSOR)

    assert result.ok is True
    assert result.source == "zwave"
    assert result.source_entity_id == "sensor.pantry_motion_last_seen"


async def test_last_seen_zigbee2mqtt_hit():
    ent_reg, dev_reg = make_fake_registries()
    z2m_sensor = "binary_sensor.laundry_room_motion_occupancy"
    ent_reg.add(FakeRegistryEntry(z2m_sensor, unique_id="0xabc_occupancy_zigbee2mqtt", platform="mqtt",
                                   device_id="dev2"))
    ent_reg.add(FakeRegistryEntry("sensor.laundry_room_motion_last_seen", unique_id="0xabc_last_seen_zigbee2mqtt",
                                   platform="mqtt", device_id="dev2"))
    dev_reg.add(FakeDeviceEntry("dev2", manufacturer="Philips", model="Hue motion sensor"))
    hass = _make_hass({"sensor.laundry_room_motion_last_seen": "2026-07-04T09:00:00+00:00"})

    p1, p2 = _patched(ent_reg, dev_reg)
    with p1, p2:
        result = await hw_timeout.async_detect_last_seen(hass, z2m_sensor)

    assert result.ok is True
    assert result.source == "zigbee2mqtt"
    assert result.source_entity_id == "sensor.laundry_room_motion_last_seen"


async def test_last_seen_value_unavailable():
    ent_reg, dev_reg = make_fake_registries()
    ent_reg.add(FakeRegistryEntry(SENSOR, unique_id="4246878805.48-113-0-x", platform="zwave_js", device_id="dev1"))
    ent_reg.add(FakeRegistryEntry("sensor.pantry_motion_last_seen", unique_id="4246878805.48-x-last-seen",
                                   platform="zwave_js", device_id="dev1"))
    dev_reg.add(FakeDeviceEntry("dev1", manufacturer="Zooz", model="ZSE11"))
    hass = _make_hass({})  # no state at all

    p1, p2 = _patched(ent_reg, dev_reg)
    with p1, p2:
        result = await hw_timeout.async_detect_last_seen(hass, SENSOR)

    assert result.ok is False
    assert result.reason == "value_unavailable"
    assert result.source_entity_id == "sensor.pantry_motion_last_seen"


async def test_last_seen_no_matching_sibling():
    ent_reg, dev_reg = make_fake_registries()
    ent_reg.add(FakeRegistryEntry(SENSOR, unique_id="123.9-113-0-x", platform="zwave_js", device_id="dev1"))
    ent_reg.add(FakeRegistryEntry("sensor.pantry_motion_battery", unique_id="123.9-battery", platform="zwave_js",
                                   device_id="dev1"))
    dev_reg.add(FakeDeviceEntry("dev1", manufacturer="Zooz", model="ZSE11"))
    hass = _make_hass({"sensor.pantry_motion_battery": "80"})

    p1, p2 = _patched(ent_reg, dev_reg)
    with p1, p2:
        result = await hw_timeout.async_detect_last_seen(hass, SENSOR)

    assert result.ok is False
    assert result.reason == "no_matching_parameter"


async def test_last_seen_unsupported_platform():
    ent_reg, dev_reg = make_fake_registries()
    ent_reg.add(FakeRegistryEntry(SENSOR, unique_id="x", platform="template", device_id="dev4"))
    dev_reg.add(FakeDeviceEntry("dev4"))

    p1, p2 = _patched(ent_reg, dev_reg)
    with p1, p2:
        result = await hw_timeout.async_detect_last_seen(_make_hass(), SENSOR)

    assert result.ok is False
    assert result.reason == "unsupported_platform"


async def test_last_seen_missing_registry_entry():
    ent_reg, dev_reg = make_fake_registries()  # SENSOR never added

    p1, p2 = _patched(ent_reg, dev_reg)
    with p1, p2:
        result = await hw_timeout.async_detect_last_seen(_make_hass(), SENSOR)

    assert result.ok is False
    assert result.reason == "no_registry_entry"


async def test_last_seen_missing_device_id():
    ent_reg, dev_reg = make_fake_registries()
    ent_reg.add(FakeRegistryEntry(SENSOR, unique_id="x", platform="zwave_js", device_id=None))

    p1, p2 = _patched(ent_reg, dev_reg)
    with p1, p2:
        result = await hw_timeout.async_detect_last_seen(_make_hass(), SENSOR)

    assert result.ok is False
    assert result.reason == "no_device"

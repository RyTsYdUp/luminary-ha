"""Unit tests for button.py's Refresh Hardware Timeouts button."""
from __future__ import annotations

from unittest.mock import patch

from custom_components.luminary_ha.button import LuminaryRefreshHwTimeoutsButton
from custom_components.luminary_ha.const import CONF_SENSOR_HW_TIMEOUTS
from custom_components.luminary_ha.coordinator import ZoneCoordinator

from tests.conftest import FakeDeviceEntry, FakeRegistryEntry, make_fake_registries
from tests.test_coordinator import SENSOR_1, SENSOR_2, _make_entry, _make_hass


def _coord(hw_timeouts: dict) -> ZoneCoordinator:
    entry = _make_entry()
    entry.options[CONF_SENSOR_HW_TIMEOUTS] = hw_timeouts
    return ZoneCoordinator(_make_hass(), entry)


async def test_press_refreshes_zwave_source():
    coord = _coord({
        SENSOR_1: {"timeout_sec": None, "source": "zwave", "source_entity_id": "number.src_1"},
    })
    button = LuminaryRefreshHwTimeoutsButton(coord)

    await button.async_press()

    coord.hass.services.async_call.assert_awaited_once_with(
        "zwave_js", "refresh_value", {"entity_id": "number.src_1"}, blocking=False
    )


async def test_press_skips_sensors_without_source_entity():
    coord = _coord({
        SENSOR_1: {"timeout_sec": 15.0, "source": "manual", "source_entity_id": None},
    })
    button = LuminaryRefreshHwTimeoutsButton(coord)

    await button.async_press()

    coord.hass.services.async_call.assert_not_awaited()


async def test_press_refreshes_zigbee2mqtt_source():
    coord = _coord({
        SENSOR_2: {"timeout_sec": None, "source": "zigbee2mqtt", "source_entity_id": "number.src_2"},
    })
    button = LuminaryRefreshHwTimeoutsButton(coord)

    ent_reg, dev_reg = make_fake_registries()
    ent_reg.add(FakeRegistryEntry("number.src_2", unique_id="0xabc_occupancy_timeout_zigbee2mqtt",
                                   platform="mqtt", device_id="dev1"))
    dev_reg.add(FakeDeviceEntry("dev1"))
    dev_reg.devices["dev1"].name = "Laundry Room Motion"

    with patch("custom_components.luminary_ha.button.er.async_get", return_value=ent_reg), \
         patch("custom_components.luminary_ha.button.dr.async_get", return_value=dev_reg):
        await button.async_press()

    coord.hass.services.async_call.assert_awaited_once_with(
        "mqtt", "publish",
        {"topic": "zigbee2mqtt/Laundry Room Motion/get", "payload": '{"occupancy_timeout": ""}'},
        blocking=False,
    )

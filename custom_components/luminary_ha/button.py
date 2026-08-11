from __future__ import annotations

import json
import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ZoneCoordinator
from .hw_timeout import _zigbee2mqtt_property_key

_LOGGER = logging.getLogger(__name__)

# MQTT wildcards and the level separator — a device friendly_name containing any
# of these can't be safely interpolated into a publish topic.
MQTT_TOPIC_UNSAFE_CHARS = ("+", "#", "/")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ZoneCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([LuminaryRefreshHwTimeoutsButton(coordinator)])


class LuminaryRefreshHwTimeoutsButton(ButtonEntity):
    """Nudge Z-Wave JS / Zigbee2MQTT to re-read each sensor's hardware timeout.

    Convenience shortcut only — staleness in the owning integration's own cache is
    that integration's problem to solve, not Luminary's (see coordinator.py). This
    just triggers that integration's own refresh mechanism without leaving Luminary's
    UI, for the narrow case a parameter changed out-of-band (device re-paired, a
    second controller on the network, etc.) and the displayed value hasn't caught up.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "refresh_hw_timeouts"
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator: ZoneCoordinator) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{coordinator.entry.entry_id}_refresh_hw_timeouts"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name=coordinator.zone_name,
            manufacturer="Luminary HA",
            model="Motion Zone",
            suggested_area=coordinator.area_id,
        )

    async def async_press(self) -> None:
        hass = self._coordinator.hass
        ent_reg = er.async_get(hass)
        dev_reg = dr.async_get(hass)

        for sensor_entity_id in self._coordinator.sensors:
            info = self._coordinator.sensor_hw_timeout_info(sensor_entity_id)
            source_entity_id = info.get("source_entity_id")
            if not source_entity_id:
                continue

            if info.get("source") == "zwave":
                await hass.services.async_call(
                    "zwave_js", "refresh_value",
                    {"entity_id": source_entity_id},
                    blocking=False,
                )
            elif info.get("source") == "zigbee2mqtt":
                await self._refresh_zigbee2mqtt(hass, ent_reg, dev_reg, source_entity_id)

    async def _refresh_zigbee2mqtt(self, hass, ent_reg, dev_reg, source_entity_id: str) -> None:
        """Publish a Z2M "get" request for the timeout property's underlying device.

        Z2M's MQTT topics key on the device's configured friendly_name, which HA's
        device registry `name` field mirrors — unverified beyond that inference; the
        Step 0 spike confirmed unique_id/platform naming but not this get-topic path,
        since that requires an actual MQTT publish rather than a read-only lookup.

        Both halves used to be built by f-string interpolation. A friendly_name
        containing an MQTT wildcard or a topic separator published somewhere other
        than intended, and a quote in the property key produced malformed JSON —
        so both are validated/encoded properly now (2026-08-10 review).
        """
        entry = ent_reg.async_get(source_entity_id)
        if entry is None or not entry.unique_id or not entry.device_id:
            return
        property_key = _zigbee2mqtt_property_key(entry.unique_id)
        device = dev_reg.async_get(entry.device_id)
        if not property_key or device is None or not device.name:
            return
        if any(ch in device.name for ch in MQTT_TOPIC_UNSAFE_CHARS):
            _LOGGER.warning(
                "Skipping Zigbee2MQTT refresh for %s: device name %r is not safe "
                "to interpolate into an MQTT topic",
                source_entity_id, device.name,
            )
            return
        await hass.services.async_call(
            "mqtt", "publish",
            {
                "topic": f"zigbee2mqtt/{device.name}/get",
                "payload": json.dumps({property_key: ""}),
            },
            blocking=False,
        )

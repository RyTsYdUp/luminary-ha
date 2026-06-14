from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ZoneCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ZoneCoordinator = hass.data[DOMAIN][entry.entry_id]
    entity = LuminaryMotionGroup(coordinator)
    coordinator.motion_group_entity = entity
    async_add_entities([entity])


class LuminaryMotionGroup(BinarySensorEntity):
    """Virtual OR group: True when any configured motion sensor is active."""

    _attr_has_entity_name = True
    _attr_translation_key = "motion_group"
    _attr_device_class = BinarySensorDeviceClass.MOTION
    _attr_icon = "mdi:motion-sensor"

    def __init__(self, coordinator: ZoneCoordinator) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{coordinator.entry.entry_id}_motion_group"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name=coordinator.zone_name,
            manufacturer="Luminary HA",
            model="Motion Zone",
            suggested_area=coordinator.area_id,
        )

    @property
    def is_on(self) -> bool:
        return self._coordinator._sensors_any_on

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
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
    entity = LuminaryStatusSensor(coordinator)
    coordinator.status_entity = entity
    async_add_entities([entity])


class LuminaryStatusSensor(SensorEntity):
    """Reports the current operating mode of the zone automation."""

    _attr_has_entity_name = True
    _attr_translation_key = "automation_status"

    def __init__(self, coordinator: ZoneCoordinator) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{coordinator.entry.entry_id}_automation_status"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name=coordinator.zone_name,
            manufacturer="Luminary HA",
            model="Motion Zone",
            suggested_area=coordinator.area_id,
        )

    @property
    def native_value(self) -> str:
        status, _ = self._coordinator.compute_status()
        return status

    @property
    def icon(self) -> str:
        _, icon = self._coordinator.compute_status()
        return icon

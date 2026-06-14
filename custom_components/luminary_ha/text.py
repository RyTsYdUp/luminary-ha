from __future__ import annotations

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback, Event
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DAYTIME_MODE_LUX, DOMAIN
from .coordinator import ZoneCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ZoneCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([LuminaryLuxSensorEntity(coordinator)])


class LuminaryLuxSensorEntity(TextEntity, RestoreEntity):
    """Stores the entity_id of the lux sensor used in Lux Sensor mode."""

    _attr_has_entity_name = True
    _attr_translation_key = "lux_sensor_entity"
    _attr_icon = "mdi:eye-circle-outline"
    _attr_mode = TextMode.TEXT
    _attr_native_max = 100
    _attr_native_min = 0
    _attr_native_value = ""
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: ZoneCoordinator) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{coordinator.entry.entry_id}_lux_sensor_entity"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name=coordinator.zone_name,
            manufacturer="Luminary HA",
            model="Motion Zone",
            suggested_area=coordinator.area_id,
        )

    @property
    def available(self) -> bool:
        return (
            self._coordinator.daytime_detection_enabled
            and self._coordinator.daytime_mode == DAYTIME_MODE_LUX
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            if last.state not in ("unknown", "unavailable"):
                self._attr_native_value = last.state
        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                [
                    self._coordinator.eid("switch", "daytime_detection_enabled"),
                    self._coordinator.eid("select", "daytime_mode"),
                ],
                self._on_dependency_changed,
            )
        )

    @callback
    def _on_dependency_changed(self, event: Event) -> None:
        self.async_write_ha_state()

    async def async_set_value(self, value: str) -> None:
        self._attr_native_value = value
        self.async_write_ha_state()
        if self._coordinator.status_entity is not None:
            self._coordinator.status_entity.async_write_ha_state()

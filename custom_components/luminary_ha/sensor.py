from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import slugify

from .const import DOMAIN
from .coordinator import ZoneCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ZoneCoordinator = hass.data[DOMAIN][entry.entry_id]

    status_entity = LuminaryStatusSensor(coordinator)
    coordinator.status_entity = status_entity

    hw_timeout_entities = [LuminaryMotionHwTimeoutSensor(coordinator, sid) for sid in coordinator.sensors]
    coordinator._hw_timeout_entities = {
        entity.monitored_sensor: entity for entity in hw_timeout_entities
    }

    async_add_entities([status_entity, *hw_timeout_entities])


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


class LuminaryMotionHwTimeoutSensor(SensorEntity):
    """Read-only display of one configured motion sensor's onboard hardware clear-timeout.

    Value is never cached here — see coordinator.sensor_hw_timeout() — Z-Wave JS / Z2M
    own this value, Luminary just reads and displays it.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:timer-alert-outline"

    def __init__(self, coordinator: ZoneCoordinator, sensor_entity_id: str) -> None:
        self._coordinator = coordinator
        self.monitored_sensor = sensor_entity_id
        state = coordinator.hass.states.get(sensor_entity_id)
        friendly = state.attributes.get("friendly_name") if state else sensor_entity_id
        self._attr_name = f"{friendly} Hardware Timeout"
        self._attr_unique_id = f"{coordinator.entry.entry_id}_hw_timeout_{slugify(sensor_entity_id)}"
        self._attr_native_unit_of_measurement = "s"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name=coordinator.zone_name,
            manufacturer="Luminary HA",
            model="Motion Zone",
            suggested_area=coordinator.area_id,
        )

    @property
    def native_value(self) -> float | None:
        return self._coordinator.sensor_hw_timeout(self.monitored_sensor)

    @property
    def extra_state_attributes(self) -> dict:
        info = self._coordinator.sensor_hw_timeout_info(self.monitored_sensor)
        return {
            "monitored_sensor": self.monitored_sensor,
            "source": info.get("source"),
            "source_entity_id": info.get("source_entity_id"),
        }

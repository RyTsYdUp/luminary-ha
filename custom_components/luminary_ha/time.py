from __future__ import annotations

import datetime
from dataclasses import dataclass

from homeassistant.components.time import TimeEntity, TimeEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback, Event
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DEFAULT_DIM_END, DEFAULT_DIM_START, DOMAIN
from .coordinator import ZoneCoordinator


@dataclass(frozen=True, kw_only=True)
class LuminaryTimeDescription(TimeEntityDescription):
    default: str = "00:00:00"
    requires_nightlight: bool = False


TIMES: tuple[LuminaryTimeDescription, ...] = (
    LuminaryTimeDescription(
        key="dim_start",
        translation_key="dim_start",
        icon="mdi:weather-night",
        default=DEFAULT_DIM_START,
        requires_nightlight=True,
    ),
    LuminaryTimeDescription(
        key="dim_end",
        translation_key="dim_end",
        icon="mdi:weather-sunny",
        default=DEFAULT_DIM_END,
        requires_nightlight=True,
    ),
)


def _parse_time(value: str) -> datetime.time:
    parts = value.split(":")
    return datetime.time(int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ZoneCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [LuminaryTime(coordinator, desc) for desc in TIMES]
    async_add_entities(entities)


class LuminaryTime(TimeEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: ZoneCoordinator, description: LuminaryTimeDescription
    ) -> None:
        self._coordinator = coordinator
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{description.key}"
        self._attr_native_value = _parse_time(description.default)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name=coordinator.zone_name,
            manufacturer="Luminary HA",
            model="Motion Zone",
            suggested_area=coordinator.area_id,
        )

    @property
    def available(self) -> bool:
        if self.entity_description.requires_nightlight:
            return self._coordinator.nightlight_enabled
        return True

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            if last.state not in ("unknown", "unavailable"):
                try:
                    self._attr_native_value = _parse_time(last.state)
                except (ValueError, IndexError):
                    pass
        # Flush state to HA state machine before rescheduling dim triggers
        self.async_write_ha_state()
        self._coordinator.reschedule_dim_triggers()

        if self.entity_description.requires_nightlight:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    [self._coordinator.eid("switch", "nightlight_enabled")],
                    self._on_dependency_changed,
                )
            )

    @callback
    def _on_dependency_changed(self, event: Event) -> None:
        self.async_write_ha_state()

    async def async_set_value(self, value: datetime.time) -> None:
        self._attr_native_value = value
        self.async_write_ha_state()
        self._coordinator.reschedule_dim_triggers()

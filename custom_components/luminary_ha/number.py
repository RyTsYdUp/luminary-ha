from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant, callback, Event
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    DAYTIME_MODE_LUX,
    DAYTIME_MODE_SUN,
    DEFAULT_DIM_BRIGHTNESS,
    DEFAULT_LIGHT_ON_TIME,
    DEFAULT_LUX_THRESHOLD,
    DEFAULT_NORMAL_BRIGHTNESS,
    DEFAULT_SUN_ELEVATION,
    DOMAIN,
)
from .coordinator import ZoneCoordinator


@dataclass(frozen=True, kw_only=True)
class LuminaryNumberDescription(NumberEntityDescription):
    default: float = 0.0
    requires_nightlight: bool = False
    requires_daytime_mode: str | None = None


NUMBERS: tuple[LuminaryNumberDescription, ...] = (
    LuminaryNumberDescription(
        key="light_on_time_sec",
        translation_key="light_on_time_sec",
        icon="mdi:timer-outline",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        native_min_value=10,
        native_max_value=600,
        native_step=5,
        default=DEFAULT_LIGHT_ON_TIME,
    ),
    LuminaryNumberDescription(
        key="sun_elevation_threshold",
        translation_key="sun_elevation_threshold",
        icon="mdi:weather-sunset",
        native_unit_of_measurement="°",
        native_min_value=-18,
        native_max_value=15,
        native_step=0.5,
        default=DEFAULT_SUN_ELEVATION,
        requires_daytime_mode=DAYTIME_MODE_SUN,
    ),
    LuminaryNumberDescription(
        key="lux_threshold",
        translation_key="lux_threshold",
        icon="mdi:brightness-5",
        native_unit_of_measurement="lx",
        native_min_value=0,
        native_max_value=1000,
        native_step=5,
        default=DEFAULT_LUX_THRESHOLD,
        requires_daytime_mode=DAYTIME_MODE_LUX,
    ),
    LuminaryNumberDescription(
        key="dim_brightness",
        translation_key="dim_brightness",
        icon="mdi:brightness-3",
        native_unit_of_measurement="%",
        native_min_value=1,
        native_max_value=100,
        native_step=1,
        default=DEFAULT_DIM_BRIGHTNESS,
        requires_nightlight=True,
    ),
    LuminaryNumberDescription(
        key="normal_brightness",
        translation_key="normal_brightness",
        icon="mdi:brightness-7",
        native_unit_of_measurement="%",
        native_min_value=1,
        native_max_value=100,
        native_step=1,
        default=DEFAULT_NORMAL_BRIGHTNESS,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ZoneCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(LuminaryNumber(coordinator, desc) for desc in NUMBERS)


class LuminaryNumber(NumberEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: ZoneCoordinator, description: LuminaryNumberDescription
    ) -> None:
        self._coordinator = coordinator
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{description.key}"
        self._attr_native_value = description.default
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name=coordinator.zone_name,
            manufacturer="Luminary HA",
            model="Motion Zone",
            suggested_area=coordinator.area_id,
        )

    @property
    def available(self) -> bool:
        if self.entity_description.requires_nightlight and not self._coordinator.nightlight_enabled:
            return False
        if self.entity_description.requires_daytime_mode is not None:
            if not self._coordinator.daytime_detection_enabled:
                return False
            if self._coordinator.daytime_mode != self.entity_description.requires_daytime_mode:
                return False
        return True

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            try:
                self._attr_native_value = float(last.state)
            except (ValueError, TypeError):
                pass

        watch: list[str] = []
        if self.entity_description.requires_nightlight:
            watch.append(self._coordinator.eid("switch", "nightlight_enabled"))
        if self.entity_description.requires_daytime_mode is not None:
            watch.append(self._coordinator.eid("switch", "daytime_detection_enabled"))
            watch.append(self._coordinator.eid("select", "daytime_mode"))
        if watch:
            self.async_on_remove(
                async_track_state_change_event(self.hass, watch, self._on_dependency_changed)
            )

    @callback
    def _on_dependency_changed(self, event: Event) -> None:
        self.async_write_ha_state()

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.async_write_ha_state()
        if self._coordinator.status_entity is not None:
            self._coordinator.status_entity.async_write_ha_state()

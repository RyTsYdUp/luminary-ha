from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .coordinator import ZoneCoordinator


@dataclass(frozen=True, kw_only=True)
class LuminarySwitchDescription(SwitchEntityDescription):
    default_on: bool = False
    restore: bool = True


SWITCHES: tuple[LuminarySwitchDescription, ...] = (
    LuminarySwitchDescription(
        key="daytime_detection_enabled",
        translation_key="daytime_detection_enabled",
        icon="mdi:brightness-auto",
        default_on=True,
    ),
    LuminarySwitchDescription(
        key="nightlight_enabled",
        translation_key="nightlight_enabled",
        icon="mdi:weather-night",
        default_on=True,
    ),
    # Deliberately not restored across restarts. motion_blocker is transient
    # state owned by a running sequence: _start_switch_on_hold raises it and
    # _run_switch_on_sequence lowers it again. async_unload cancels that task,
    # so restoring "on" after a restart or integration reload resurrects the
    # flag with nothing alive to ever clear it — the zone sits in permanent
    # "Manual Override" and ignores all motion (2026-08-10 review; same latch
    # class as the 2026-08-02 single-tap-down bug). Starting off fails open to
    # working automation, which is the safe direction for a lighting zone.
    LuminarySwitchDescription(
        key="motion_blocker",
        translation_key="motion_blocker",
        icon="mdi:hand-back-right",
        default_on=False,
        restore=False,
    ),
    LuminarySwitchDescription(
        key="automation_disabled",
        translation_key="automation_disabled",
        icon="mdi:robot-off",
        default_on=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ZoneCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(LuminarySwitch(coordinator, desc) for desc in SWITCHES)


class LuminarySwitch(SwitchEntity, RestoreEntity):
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: ZoneCoordinator, description: LuminarySwitchDescription
    ) -> None:
        self._coordinator = coordinator
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{description.key}"
        self._attr_is_on = description.default_on
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name=coordinator.zone_name,
            manufacturer="Luminary HA",
            model="Motion Zone",
            suggested_area=coordinator.area_id,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if not self.entity_description.restore:
            return
        if (last := await self.async_get_last_state()) is not None:
            self._attr_is_on = last.state == "on"

    async def async_turn_on(self, **kwargs) -> None:
        self._attr_is_on = True
        self.async_write_ha_state()
        if self._coordinator.status_entity is not None:
            self._coordinator.status_entity.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self._attr_is_on = False
        self.async_write_ha_state()
        if self._coordinator.status_entity is not None:
            self._coordinator.status_entity.async_write_ha_state()

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import ZoneCoordinator

PLATFORMS = ["binary_sensor", "button", "number", "select", "sensor", "switch", "text", "time"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    coordinator = ZoneCoordinator(hass, entry)
    await coordinator.async_setup()
    hass.data[DOMAIN][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.async_register_entity_listeners()
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        # .pop with a default: a setup that failed part-way never registered a
        # coordinator, and a bare pop would raise KeyError and mask the real error.
        coordinator: ZoneCoordinator | None = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if coordinator is not None:
            await coordinator.async_unload()
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)

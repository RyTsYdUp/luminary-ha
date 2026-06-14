from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import area_registry as ar
from homeassistant.util import slugify
from homeassistant.helpers.selector import (
    AreaSelector,
    AreaSelectorConfig,
    DeviceSelector,
    DeviceSelectorConfig,
    EntitySelector,
    EntitySelectorConfig,
)

from .const import (
    CONF_AREA,
    CONF_LIGHT,
    CONF_SENSORS,
    CONF_SWITCH_DEVICE,
    CONF_ZONE_ID,
    CONF_ZONE_NAME,
    DOMAIN,
)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_AREA): AreaSelector(AreaSelectorConfig()),
        vol.Required(CONF_SENSORS): EntitySelector(
            EntitySelectorConfig(
                domain="binary_sensor",
                device_class="motion",
                multiple=True,
            )
        ),
        vol.Required(CONF_LIGHT): EntitySelector(
            EntitySelectorConfig(domain="light")
        ),
        vol.Optional(CONF_SWITCH_DEVICE): DeviceSelector(DeviceSelectorConfig()),
    }
)

STEP_OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SENSORS): EntitySelector(
            EntitySelectorConfig(
                domain="binary_sensor",
                device_class="motion",
                multiple=True,
            )
        ),
        vol.Required(CONF_LIGHT): EntitySelector(
            EntitySelectorConfig(domain="light")
        ),
        vol.Optional(CONF_SWITCH_DEVICE): DeviceSelector(DeviceSelectorConfig()),
    }
)


class LuminaryConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> config_entries.FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            area_reg = ar.async_get(self.hass)
            area = area_reg.async_get_area(user_input[CONF_AREA])

            if area is None:
                errors[CONF_AREA] = "area_not_found"
            elif not user_input.get(CONF_SENSORS):
                errors[CONF_SENSORS] = "no_sensors"
            else:
                zone_id = slugify(area.name)
                await self.async_set_unique_id(zone_id)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=area.name,
                    data={
                        CONF_AREA: user_input[CONF_AREA],
                        CONF_ZONE_NAME: area.name,
                        CONF_ZONE_ID: zone_id,
                    },
                    options={
                        CONF_SENSORS: user_input[CONF_SENSORS],
                        CONF_LIGHT: user_input[CONF_LIGHT],
                        CONF_SWITCH_DEVICE: user_input.get(CONF_SWITCH_DEVICE),
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> LuminaryOptionsFlow:
        return LuminaryOptionsFlow(config_entry)


class LuminaryOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> config_entries.FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            if not user_input.get(CONF_SENSORS):
                errors[CONF_SENSORS] = "no_sensors"
            else:
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_SENSORS: user_input[CONF_SENSORS],
                        CONF_LIGHT: user_input[CONF_LIGHT],
                        CONF_SWITCH_DEVICE: user_input.get(CONF_SWITCH_DEVICE),
                    },
                )

        current = self._config_entry.options
        suggested = {
            CONF_SENSORS: current.get(CONF_SENSORS, []),
            CONF_LIGHT: current.get(CONF_LIGHT, ""),
        }
        if current.get(CONF_SWITCH_DEVICE):
            suggested[CONF_SWITCH_DEVICE] = current[CONF_SWITCH_DEVICE]

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(STEP_OPTIONS_SCHEMA, suggested),
            errors=errors,
        )

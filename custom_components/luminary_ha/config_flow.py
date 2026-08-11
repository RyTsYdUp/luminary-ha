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
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from . import hw_timeout
from .const import (
    CONF_AREA,
    CONF_LIGHT,
    CONF_SENSOR_HW_TIMEOUTS,
    CONF_SENSORS,
    CONF_SWITCH_DEVICE,
    CONF_ZONE_ID,
    CONF_ZONE_NAME,
    DOMAIN,
    MOTION_DEVICE_CLASSES,
)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_AREA): AreaSelector(AreaSelectorConfig()),
        vol.Required(CONF_SENSORS): EntitySelector(
            EntitySelectorConfig(
                domain="binary_sensor",
                device_class=MOTION_DEVICE_CLASSES,
                multiple=True,
            )
        ),
        vol.Required(CONF_LIGHT): EntitySelector(
            EntitySelectorConfig(domain="light", multiple=True)
        ),
        vol.Optional(CONF_SWITCH_DEVICE): DeviceSelector(DeviceSelectorConfig()),
    }
)

STEP_OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SENSORS): EntitySelector(
            EntitySelectorConfig(
                domain="binary_sensor",
                device_class=MOTION_DEVICE_CLASSES,
                multiple=True,
            )
        ),
        vol.Required(CONF_LIGHT): EntitySelector(
            EntitySelectorConfig(domain="light", multiple=True)
        ),
        vol.Optional(CONF_SWITCH_DEVICE): DeviceSelector(DeviceSelectorConfig()),
    }
)


def build_timeout_schema(default_value: float | None) -> vol.Schema:
    """Build the per-sensor hardware-timeout confirmation form schema.

    No default is set when detection failed and there's no prior manual value —
    the field is required, but empty, so the user must explicitly enter something.
    """
    key = (
        vol.Required("hw_timeout_sec", default=default_value)
        if default_value is not None
        else vol.Required("hw_timeout_sec")
    )
    return vol.Schema({
        key: NumberSelector(
            NumberSelectorConfig(min=1, max=3600, step=1, mode=NumberSelectorMode.BOX, unit_of_measurement="s")
        ),
    })


def compute_sensors_to_confirm(sensor_entity_ids: list[str], existing_hw_timeouts: dict) -> list[str]:
    """Sensors needing a confirm-timeout pass: new to the zone, or previously
    manual/failed (no source_entity_id — nothing live tracking them yet)."""
    return [
        sid for sid in sensor_entity_ids
        if not existing_hw_timeouts.get(sid, {}).get("source_entity_id")
    ]


def prune_removed_sensors(sensor_entity_ids: list[str], existing_hw_timeouts: dict) -> dict:
    """Drop CONF_SENSOR_HW_TIMEOUTS entries for sensors no longer in the zone."""
    return {sid: info for sid, info in existing_hw_timeouts.items() if sid in sensor_entity_ids}


class _HwTimeoutConfirmStep:
    """Shared confirm-timeout step, mixed into both LuminaryConfigFlow and
    LuminaryOptionsFlow.

    Requires the including flow to set self._sensors_to_confirm (list, non-empty),
    self._confirmed (dict, accumulator), self._existing_hw_timeouts (dict, for
    re-visits/prefill) before first entering this step, and to implement
    _finish_confirm_timeout() to assemble and create the final entry.
    """

    async def async_step_confirm_timeout(self, user_input: dict | None = None):
        sensor_entity_id = self._sensors_to_confirm[0]

        if user_input is not None:
            detection = getattr(self, "_pending_detection", None)
            unchanged = (
                detection is not None
                and detection.ok
                and detection.value == user_input["hw_timeout_sec"]
            )
            self._confirmed[sensor_entity_id] = {
                "timeout_sec": user_input["hw_timeout_sec"],
                "source": detection.source if unchanged else "manual",
                "source_entity_id": detection.source_entity_id if unchanged else None,
            }
            self._sensors_to_confirm = self._sensors_to_confirm[1:]
            if self._sensors_to_confirm:
                return await self.async_step_confirm_timeout()
            return self._finish_confirm_timeout()

        detection = await hw_timeout.async_detect_hw_timeout(self.hass, sensor_entity_id)
        self._pending_detection = detection
        previous = self._existing_hw_timeouts.get(sensor_entity_id, {})
        default_value = detection.value if detection.ok else previous.get("timeout_sec")

        sensor_state = self.hass.states.get(sensor_entity_id)
        sensor_name = sensor_state.attributes.get("friendly_name") if sensor_state else sensor_entity_id

        errors: dict[str, str] = {}
        if detection.ok:
            status_text = f"Auto-detected from {detection.source_entity_id}: {detection.value}s. Confirm or edit."
        else:
            errors["base"] = "detection_failed"
            status_text = (
                f"Auto-detection failed ({detection.reason}). Enter the value from the "
                "sensor's manual/app, or enable its Configuration entity in Home Assistant "
                "and re-open this flow."
            )

        return self.async_show_form(
            step_id="confirm_timeout",
            data_schema=build_timeout_schema(default_value),
            errors=errors,
            description_placeholders={
                "sensor_name": str(sensor_name),
                "status": status_text,
                "remaining": str(len(self._sensors_to_confirm) - 1),
            },
        )


class LuminaryConfigFlow(_HwTimeoutConfirmStep, config_entries.ConfigFlow, domain=DOMAIN):
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
            elif not user_input.get(CONF_LIGHT):
                errors[CONF_LIGHT] = "no_lights"
            else:
                zone_id = slugify(area.name)
                await self.async_set_unique_id(zone_id)
                self._abort_if_unique_id_configured()

                self._zone_data = {
                    CONF_AREA: user_input[CONF_AREA],
                    CONF_ZONE_NAME: area.name,
                    CONF_ZONE_ID: zone_id,
                }
                self._zone_options = {
                    CONF_SENSORS: user_input[CONF_SENSORS],
                    CONF_LIGHT: user_input[CONF_LIGHT],
                    CONF_SWITCH_DEVICE: user_input.get(CONF_SWITCH_DEVICE),
                }
                self._sensors_to_confirm = list(user_input[CONF_SENSORS])
                self._confirmed = {}
                self._existing_hw_timeouts = {}
                return await self.async_step_confirm_timeout()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )

    def _finish_confirm_timeout(self) -> config_entries.FlowResult:
        options = {**self._zone_options, CONF_SENSOR_HW_TIMEOUTS: self._confirmed}
        return self.async_create_entry(
            title=self._zone_data[CONF_ZONE_NAME],
            data=self._zone_data,
            options=options,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> LuminaryOptionsFlow:
        return LuminaryOptionsFlow(config_entry)


class LuminaryOptionsFlow(_HwTimeoutConfirmStep, config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> config_entries.FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            if not user_input.get(CONF_SENSORS):
                errors[CONF_SENSORS] = "no_sensors"
            elif not user_input.get(CONF_LIGHT):
                errors[CONF_LIGHT] = "no_lights"
            else:
                self._zone_options = {
                    CONF_SENSORS: user_input[CONF_SENSORS],
                    CONF_LIGHT: user_input[CONF_LIGHT],
                    CONF_SWITCH_DEVICE: user_input.get(CONF_SWITCH_DEVICE),
                }
                existing = self._config_entry.options.get(CONF_SENSOR_HW_TIMEOUTS, {})
                self._existing_hw_timeouts = prune_removed_sensors(user_input[CONF_SENSORS], existing)
                self._confirmed = dict(self._existing_hw_timeouts)
                self._sensors_to_confirm = compute_sensors_to_confirm(
                    user_input[CONF_SENSORS], self._existing_hw_timeouts
                )
                if self._sensors_to_confirm:
                    return await self.async_step_confirm_timeout()
                return self._finish_confirm_timeout()

        current = self._config_entry.options
        # A zone configured before multi-light support stored a bare entity_id
        # string; the selector is multiple now and needs a list to pre-fill from.
        current_lights = current.get(CONF_LIGHT) or []
        if isinstance(current_lights, str):
            current_lights = [current_lights]
        suggested = {
            CONF_SENSORS: current.get(CONF_SENSORS, []),
            CONF_LIGHT: list(current_lights),
        }
        if current.get(CONF_SWITCH_DEVICE):
            suggested[CONF_SWITCH_DEVICE] = current[CONF_SWITCH_DEVICE]

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(STEP_OPTIONS_SCHEMA, suggested),
            errors=errors,
        )

    def _finish_confirm_timeout(self) -> config_entries.FlowResult:
        options = {**self._zone_options, CONF_SENSOR_HW_TIMEOUTS: self._confirmed}
        return self.async_create_entry(title="", data=options)

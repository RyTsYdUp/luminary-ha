from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant, callback, Event
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
)

from .const import (
    CONF_SENSORS,
    CONF_LIGHT,
    CONF_SWITCH_DEVICE,
    CONF_ZONE_ID,
    CONF_ZONE_NAME,
    CONF_AREA,
    DAYTIME_MODE_SUN,
    ZWAVE_SCENE_VALUE_SINGLE,
    ZWAVE_SCENE_VALUE_DOUBLE,
    ZWAVE_SCENE_KEY_UP,
    ZWAVE_SCENE_KEY_DOWN,
    ZWAVE_SCENE_KEY_CONFIG,
)

_LOGGER = logging.getLogger(__name__)


class ZoneCoordinator:
    """Coordinates all automation logic for a single Luminary zone."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._unsub_listeners: list = []
        self._dim_unsubs: list = []
        self._motion_task: asyncio.Task | None = None
        self._sensors_any_on: bool = False
        # Entity references set by platforms after entity creation
        self.motion_group_entity = None
        self.status_entity = None

    # ------------------------------------------------------------------
    # Config accessors
    # ------------------------------------------------------------------

    @property
    def zone_id(self) -> str:
        return self.entry.data[CONF_ZONE_ID]

    @property
    def zone_name(self) -> str:
        return self.entry.data[CONF_ZONE_NAME]

    @property
    def area_id(self) -> str | None:
        return self.entry.data.get(CONF_AREA)

    @property
    def sensors(self) -> list[str]:
        return self.entry.options.get(CONF_SENSORS, [])

    @property
    def light(self) -> str | None:
        return self.entry.options.get(CONF_LIGHT)

    @property
    def switch_device(self) -> str | None:
        return self.entry.options.get(CONF_SWITCH_DEVICE)

    # ------------------------------------------------------------------
    # Entity ID helpers
    # ------------------------------------------------------------------

    def eid(self, domain: str, suffix: str) -> str:
        return f"{domain}.{self.zone_id}_{suffix}"

    def _state(self, entity_id: str) -> str | None:
        state = self.hass.states.get(entity_id)
        return state.state if state else None

    def _float(self, entity_id: str, default: float = 0.0) -> float:
        s = self._state(entity_id)
        if s in (None, "unavailable", "unknown"):
            return default
        try:
            return float(s)
        except (ValueError, TypeError):
            return default

    # ------------------------------------------------------------------
    # Zone state properties (read from entity states)
    # ------------------------------------------------------------------

    @property
    def automation_disabled(self) -> bool:
        return self._state(self.eid("switch", "automation_disabled")) == "on"

    @property
    def motion_blocker(self) -> bool:
        return self._state(self.eid("switch", "motion_blocker")) == "on"

    @property
    def nightlight_enabled(self) -> bool:
        state = self._state(self.eid("switch", "nightlight_enabled"))
        return state != "off"  # default True when entity not yet registered

    @property
    def daytime_detection_enabled(self) -> bool:
        state = self._state(self.eid("switch", "daytime_detection_enabled"))
        return state != "off"  # default True when entity not yet registered

    @property
    def light_on_time_sec(self) -> int:
        return int(self._float(self.eid("number", "light_on_time_sec"), 60))

    @property
    def dim_brightness(self) -> int:
        return int(self._float(self.eid("number", "dim_brightness"), 10))

    @property
    def normal_brightness(self) -> int:
        return int(self._float(self.eid("number", "normal_brightness"), 100))

    @property
    def sun_elevation_threshold(self) -> float:
        return self._float(self.eid("number", "sun_elevation_threshold"), 3.0)

    @property
    def lux_threshold(self) -> float:
        return self._float(self.eid("number", "lux_threshold"), 50.0)

    @property
    def daytime_mode(self) -> str:
        return self._state(self.eid("select", "daytime_mode")) or DAYTIME_MODE_SUN

    @property
    def lux_sensor_entity(self) -> str:
        return self._state(self.eid("text", "lux_sensor_entity")) or ""

    # ------------------------------------------------------------------
    # Logic helpers
    # ------------------------------------------------------------------

    def is_dark_enough(self) -> bool:
        """Return True when the light should trigger (detection bypassed or dark condition met)."""
        if not self.daytime_detection_enabled:
            return True
        if self.daytime_mode == DAYTIME_MODE_SUN:
            sun = self.hass.states.get("sun.sun")
            if sun is None:
                return True
            elevation = float(sun.attributes.get("elevation", 0))
            return elevation < self.sun_elevation_threshold

        lux_id = self.lux_sensor_entity
        if not lux_id:
            return True
        lux_state = self.hass.states.get(lux_id)
        if lux_state is None or lux_state.state in ("unknown", "unavailable"):
            return True
        try:
            return float(lux_state.state) < self.lux_threshold
        except (ValueError, TypeError):
            return True

    def in_dim_window(self) -> bool:
        """Return True if current local time falls inside the nightlight window."""
        now = datetime.now().strftime("%H:%M")

        start_state = self.hass.states.get(self.eid("time", "dim_start"))
        end_state = self.hass.states.get(self.eid("time", "dim_end"))

        start = (
            start_state.state[:5]
            if start_state and start_state.state not in ("unknown", "unavailable")
            else "00:00"
        )
        end = (
            end_state.state[:5]
            if end_state and end_state.state not in ("unknown", "unavailable")
            else "06:00"
        )

        if start <= end:
            return start <= now < end
        # Midnight-crossing window
        return now >= start or now < end

    def target_brightness(self) -> int:
        if self.nightlight_enabled and self.in_dim_window():
            return self.dim_brightness
        return self.normal_brightness

    def compute_status(self) -> tuple[str, str]:
        """Return (state_string, icon) for the automation status sensor."""
        if self.automation_disabled:
            return "Disabled", "mdi:robot-off"
        if self.motion_blocker:
            return "Manual Override", "mdi:hand-back-right"
        if self.is_dark_enough():
            return "Automated", "mdi:motion-sensor"
        return "Standby", "mdi:motion-sensor-off"

    # ------------------------------------------------------------------
    # Light / switch service calls
    # ------------------------------------------------------------------

    async def _light_on(self, brightness_pct: int | None = None) -> None:
        if not self.light:
            return
        pct = brightness_pct if brightness_pct is not None else self.target_brightness()
        await self.hass.services.async_call(
            "light", "turn_on",
            {"entity_id": self.light, "brightness_pct": pct, "transition": 1},
            blocking=False,
        )

    async def _light_off(self) -> None:
        if not self.light:
            return
        await self.hass.services.async_call(
            "light", "turn_off",
            {"entity_id": self.light, "transition": 1},
            blocking=False,
        )

    async def _set_switch(self, service: str, entity_id: str) -> None:
        await self.hass.services.async_call(
            "switch", service, {"entity_id": entity_id}, blocking=False
        )

    # ------------------------------------------------------------------
    # Motion sequence (automations 1 + 10)
    # ------------------------------------------------------------------

    @callback
    def _handle_sensor_change(self, event: Event) -> None:
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        entity_id = event.data.get("entity_id")

        # Keep motion group derived state current
        self._sensors_any_on = any(
            self.hass.states.is_state(s, "on") for s in self.sensors
        )
        if self.motion_group_entity is not None:
            self.motion_group_entity.async_write_ha_state()
        if self.status_entity is not None:
            self.status_entity.async_write_ha_state()

        if new_state is None:
            return

        # Sensor unavailable notifications (automations 11 + 12)
        if new_state.state == "unavailable":
            self.hass.async_create_task(
                self._notify_sensor_unavailable(entity_id, new_state)
            )
        elif old_state is not None and old_state.state == "unavailable":
            self.hass.async_create_task(self._maybe_clear_unavailable())

        # Motion trigger
        if new_state.state != "on":
            return
        if self.automation_disabled or self.motion_blocker:
            return
        if not self.is_dark_enough():
            return
        self._restart_motion_task()

    def _restart_motion_task(self) -> None:
        """Cancel any in-progress motion sequence and start fresh (mode: restart)."""
        if self._motion_task and not self._motion_task.done():
            self._motion_task.cancel()
        self._motion_task = self.hass.async_create_task(self._run_motion_sequence())

    async def _run_motion_sequence(self) -> None:
        try:
            await self._light_on()

            # Wait for all sensors to clear, or until the safety timeout fires
            cleared = asyncio.Event()

            @callback
            def _on_change(_event: Event) -> None:
                if not any(self.hass.states.is_state(s, "on") for s in self.sensors):
                    cleared.set()

            unsub = async_track_state_change_event(
                self.hass, self.sensors, _on_change
            )
            try:
                await asyncio.wait_for(cleared.wait(), timeout=float(self.light_on_time_sec))
            except asyncio.TimeoutError:
                pass
            finally:
                unsub()

            # Re-check overrides before turning off (may have changed during wait)
            if self.automation_disabled or self.motion_blocker:
                return

            await self._light_off()

            # Stuck sensor recovery (automation 10)
            if self._sensors_any_on:
                await self._refresh_sensors()

        except asyncio.CancelledError:
            raise  # Propagate so the task is properly cancelled

    async def _refresh_sensors(self) -> None:
        """Poll all sensors via Z-Wave JS to recover from a stuck 'on' state."""
        try:
            await self.hass.services.async_call(
                "zwave_js", "refresh_value",
                {"entity_id": self.sensors},
                blocking=False,
            )
        except Exception as err:
            _LOGGER.debug("refresh_value skipped (Z-Wave JS unavailable?): %s", err)

    # ------------------------------------------------------------------
    # Z-Wave switch handler (automations 2–6)
    # ------------------------------------------------------------------

    @callback
    def _handle_zwave_event(self, event: Event) -> None:
        data = event.data
        if data.get("device_id") != self.switch_device:
            return
        if data.get("command_class") != 91:
            return

        key = str(data.get("property_key", ""))
        value = data.get("value")

        if key == ZWAVE_SCENE_KEY_UP and value == ZWAVE_SCENE_VALUE_SINGLE:
            self.hass.async_create_task(self._single_tap_up())
        elif key == ZWAVE_SCENE_KEY_DOWN and value == ZWAVE_SCENE_VALUE_SINGLE:
            self.hass.async_create_task(self._single_tap_down())
        elif key == ZWAVE_SCENE_KEY_UP and value == ZWAVE_SCENE_VALUE_DOUBLE:
            self.hass.async_create_task(self._double_tap_up())
        elif key == ZWAVE_SCENE_KEY_DOWN and value == ZWAVE_SCENE_VALUE_DOUBLE:
            self.hass.async_create_task(self._double_tap_down())
        elif key == ZWAVE_SCENE_KEY_CONFIG and value == ZWAVE_SCENE_VALUE_SINGLE:
            self.hass.async_create_task(self._scene3_reset())

    async def _single_tap_up(self) -> None:
        """Full bright + manual override (smart) or plain on (dumb)."""
        await self._light_on(100)
        if not self.automation_disabled:
            await self._set_switch("turn_on", self.eid("switch", "motion_blocker"))

    async def _single_tap_down(self) -> None:
        """Return to auto (smart) or plain off (dumb)."""
        if self.automation_disabled:
            await self._light_off()
            return
        await self._set_switch("turn_off", self.eid("switch", "motion_blocker"))
        if self._sensors_any_on:
            await self._light_on()
        else:
            await self._light_off()

    async def _double_tap_up(self) -> None:
        """Enable dumb mode; preserve light state."""
        await self._set_switch("turn_on", self.eid("switch", "automation_disabled"))
        await self._set_switch("turn_off", self.eid("switch", "motion_blocker"))

    async def _double_tap_down(self) -> None:
        """Disable dumb mode; resume automation."""
        await self._set_switch("turn_off", self.eid("switch", "automation_disabled"))
        if self._sensors_any_on:
            await self._light_on()
        else:
            await self._light_off()

    async def _scene3_reset(self) -> None:
        """Panic reset: clear all overrides and turn light off."""
        await self._set_switch("turn_off", self.eid("switch", "automation_disabled"))
        await self._set_switch("turn_off", self.eid("switch", "motion_blocker"))
        await self._light_off()

    # ------------------------------------------------------------------
    # Dim window (automations 7, 8, 9)
    # ------------------------------------------------------------------

    @callback
    def _handle_dim_window_start(self, now: datetime) -> None:
        if not self.nightlight_enabled:
            return
        light_state = self.hass.states.get(self.light)
        if light_state and light_state.state == "on":
            if not self.automation_disabled and not self.motion_blocker:
                self.hass.async_create_task(self._light_on(self.dim_brightness))

    @callback
    def _handle_dim_window_end(self, now: datetime) -> None:
        light_state = self.hass.states.get(self.light)
        if light_state and light_state.state == "on":
            if not self.automation_disabled and not self.motion_blocker:
                self.hass.async_create_task(self._light_on(self.normal_brightness))

    @callback
    def _handle_dim_time_entity_changed(self, event: Event) -> None:
        """Re-schedule dim triggers and re-apply brightness when window times change."""
        self.reschedule_dim_triggers()
        light_state = self.hass.states.get(self.light)
        if light_state and light_state.state == "on":
            if not self.automation_disabled and not self.motion_blocker:
                self.hass.async_create_task(self._light_on())

    def reschedule_dim_triggers(self) -> None:
        """Cancel existing dim time listeners and re-register with current values."""
        for unsub in self._dim_unsubs:
            unsub()
        self._dim_unsubs.clear()

        for suffix, handler in (
            ("dim_start", self._handle_dim_window_start),
            ("dim_end", self._handle_dim_window_end),
        ):
            state = self.hass.states.get(self.eid("time", suffix))
            if state is None or state.state in ("unknown", "unavailable"):
                continue
            try:
                parts = state.state.split(":")
                h, m = int(parts[0]), int(parts[1])
                self._dim_unsubs.append(
                    async_track_time_change(self.hass, handler, hour=h, minute=m, second=0)
                )
            except (ValueError, IndexError, AttributeError):
                pass

    # ------------------------------------------------------------------
    # Sensor unavailable notifications (automations 11 + 12)
    # ------------------------------------------------------------------

    async def _notify_sensor_unavailable(self, entity_id: str, state) -> None:
        name = state.attributes.get("friendly_name") or entity_id
        await self.hass.services.async_call(
            "persistent_notification", "create",
            {
                "notification_id": f"{self.zone_id}_sensor_unavailable",
                "title": f"{self.zone_name}: Motion Sensor Unavailable",
                "message": (
                    f"{name} is unavailable. "
                    f"{self.zone_name} motion detection may be impaired. "
                    "Check your Z-Wave network."
                ),
            },
            blocking=False,
        )

    async def _maybe_clear_unavailable(self) -> None:
        all_ok = all(
            (s := self.hass.states.get(sid)) is not None and s.state != "unavailable"
            for sid in self.sensors
        )
        if all_ok:
            await self.hass.services.async_call(
                "persistent_notification", "dismiss",
                {"notification_id": f"{self.zone_id}_sensor_unavailable"},
                blocking=False,
            )

    # ------------------------------------------------------------------
    # Status sensor invalidation
    # ------------------------------------------------------------------

    @callback
    def _handle_status_trigger(self, event: Event) -> None:
        if self.status_entity is not None:
            self.status_entity.async_write_ha_state()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_setup(self) -> None:
        """Register all event listeners."""
        # Sensor state changes (motion + unavailable)
        if self.sensors:
            self._unsub_listeners.append(
                async_track_state_change_event(
                    self.hass, self.sensors, self._handle_sensor_change
                )
            )
        # Entities that affect the status sensor display
        self._unsub_listeners.append(
            async_track_state_change_event(
                self.hass,
                [
                    self.eid("switch", "automation_disabled"),
                    self.eid("switch", "motion_blocker"),
                    self.eid("switch", "nightlight_enabled"),
                    self.eid("switch", "daytime_detection_enabled"),
                    self.eid("select", "daytime_mode"),
                    "sun.sun",
                ],
                self._handle_status_trigger,
            )
        )
        # Dim time entity changes
        self._unsub_listeners.append(
            async_track_state_change_event(
                self.hass,
                [self.eid("time", "dim_start"), self.eid("time", "dim_end")],
                self._handle_dim_time_entity_changed,
            )
        )
        # Z-Wave switch events
        if self.switch_device:
            self._unsub_listeners.append(
                self.hass.bus.async_listen(
                    "zwave_js_value_notification", self._handle_zwave_event
                )
            )
        # Initial group state
        self._sensors_any_on = any(
            self.hass.states.is_state(s, "on") for s in self.sensors
        )

    async def async_unload(self) -> None:
        """Remove all listeners and cancel running tasks."""
        for unsub in self._unsub_listeners:
            unsub()
        self._unsub_listeners.clear()
        for unsub in self._dim_unsubs:
            unsub()
        self._dim_unsubs.clear()
        if self._motion_task and not self._motion_task.done():
            self._motion_task.cancel()

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant, callback, Context, Event
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.util import slugify

from . import hw_timeout
from .const import (
    CONF_SENSORS,
    CONF_SENSOR_HW_TIMEOUTS,
    CONF_LIGHT,
    CONF_SWITCH_DEVICE,
    CONF_ZONE_ID,
    CONF_ZONE_NAME,
    CONF_AREA,
    DAYTIME_MODE_SUN,
    DEFAULT_STALE_SENSOR_MINUTES,
    DEFAULT_SWITCH_ON_TIMEOUT_MINUTES,
    DOMAIN,
    STALE_CHECK_INTERVAL_SEC,
    ZWAVE_SCENE_VALUE_SINGLE,
    ZWAVE_SCENE_VALUE_DOUBLE,
    ZWAVE_SCENE_KEY_UP,
    ZWAVE_SCENE_KEY_DOWN,
    ZWAVE_SCENE_KEY_CONFIG,
)

_LOGGER = logging.getLogger(__name__)


class ZoneCoordinator:
    """Coordinates all automation logic for a single Luminary zone."""

    _stuck_timeout: int = 1800  # seconds; override in tests

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._unsub_listeners: list = []
        self._dim_unsubs: list = []
        self._motion_task: asyncio.Task | None = None
        self._sensors_any_on: bool = False
        # Context id of the last light.turn_on call Luminary itself issued, so
        # _handle_light_change can tell its own commanded changes (motion
        # sequence, dim-window re-assertion, switch-hold) apart from a
        # genuinely external one (switch/companion switch, another
        # integration) without relying on brightness happening to match.
        self._own_light_context_id: str | None = None
        # Entity references set by platforms after entity creation
        self.motion_group_entity = None
        self.status_entity = None
        self.light_on_time_entity = None
        # Hardware-timeout tracking: sensor_entity_id -> its display entity, and
        # source_entity_id (the Configuration CC / Z2M number entity) -> sensor_entity_id.
        # No cached values live here — sensor_hw_timeout() always reads through to the
        # source entity's current state; Z-Wave JS / Z2M own that value, not Luminary.
        self._hw_timeout_entities: dict = {}
        self._hw_timeout_source_map: dict[str, str] = {}
        # Dead/stuck-sensor detection via last_seen staleness (see const.py comment
        # and hw_timeout.async_detect_last_seen). Discovered once at startup;
        # sensor_last_seen() always reads through to the source entity's live state.
        self._last_seen_source_map: dict[str, str] = {}
        self._stale_notified: set[str] = set()
        self.last_seen_entities: dict = {}

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
        """Return the entity_id for a coordinator-owned entity.

        Looks up the entity registry by unique_id so this works regardless of
        how HA generated the entity_id (device renames, area prefixes, etc.).
        Falls back to the constructed ID for tests or pre-registration calls.
        """
        try:
            ent_reg = er.async_get(self.hass)
            entity_id = ent_reg.async_get_entity_id(
                domain, DOMAIN, f"{self.entry.entry_id}_{suffix}"
            )
            if isinstance(entity_id, str):
                return entity_id
        except Exception:
            pass
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
    # Hardware motion-clear-timeout (read-through; Z-Wave JS / Z2M own the value)
    # ------------------------------------------------------------------

    def sensor_hw_timeout_info(self, sensor_entity_id: str) -> dict:
        return self.entry.options.get(CONF_SENSOR_HW_TIMEOUTS, {}).get(sensor_entity_id, {})

    def sensor_hw_timeout(self, sensor_entity_id: str) -> float | None:
        """Return sensor_entity_id's onboard hardware clear-timeout, or None if unknown.

        Manually-entered values (no source_entity_id) come straight from entry.options.
        Auto-detected values are always read live from the source entity's current
        state — never cached — since the owning integration (zwave_js/mqtt) is the
        source of truth, not Luminary.
        """
        info = self.sensor_hw_timeout_info(sensor_entity_id)
        if not info:
            return None
        source_entity_id = info.get("source_entity_id")
        if source_entity_id is None:
            timeout_sec = info.get("timeout_sec")
            return float(timeout_sec) if timeout_sec is not None else None
        return self._float_or_none(source_entity_id)

    def _float_or_none(self, entity_id: str) -> float | None:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return None

    def max_sensor_hw_timeout(self) -> float:
        """Return the largest known hardware timeout across this zone's sensors, or 0."""
        values = [self.sensor_hw_timeout(sid) for sid in self.sensors]
        return max((v for v in values if v is not None), default=0.0)

    # ------------------------------------------------------------------
    # Dead/stuck-sensor detection (read-through; see const.py comment)
    # ------------------------------------------------------------------

    def sensor_last_seen(self, sensor_entity_id: str) -> datetime | None:
        """Live-read sensor_entity_id's last-communicated timestamp, or None if
        undiscovered/unavailable. Never cached — same read-through philosophy as
        sensor_hw_timeout(); the owning integration owns this value, not Luminary."""
        source_entity_id = self._last_seen_source_map.get(sensor_entity_id)
        if source_entity_id is None:
            return None
        state = self.hass.states.get(source_entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        try:
            return datetime.fromisoformat(state.state)
        except (ValueError, TypeError):
            return None

    def sensor_stale_seconds(self, sensor_entity_id: str) -> float | None:
        """Seconds since sensor_entity_id last communicated, or None if unknown."""
        last_seen = self.sensor_last_seen(sensor_entity_id)
        if last_seen is None:
            return None
        return (datetime.now(timezone.utc) - last_seen).total_seconds()

    @property
    def stale_sensor_threshold_sec(self) -> float:
        return self._float(self.eid("number", "stale_sensor_minutes"), DEFAULT_STALE_SENSOR_MINUTES) * 60

    @property
    def switch_on_timeout_sec(self) -> float:
        return self._float(
            self.eid("number", "switch_on_timeout_minutes"), DEFAULT_SWITCH_ON_TIMEOUT_MINUTES
        ) * 60

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
        context = Context()
        self._own_light_context_id = context.id
        await self.hass.services.async_call(
            "light", "turn_on",
            {"entity_id": self.light, "brightness_pct": pct, "transition": 1},
            blocking=False,
            context=context,
        )

    async def _light_off(self) -> None:
        if not self.light:
            return
        await self.hass.services.async_call(
            "light", "turn_off",
            {"entity_id": self.light, "transition": 1},
            blocking=False,
        )

    async def _set_switch(self, service: str, entity_id: str, blocking: bool = False) -> None:
        await self.hass.services.async_call(
            "switch", service, {"entity_id": entity_id}, blocking=blocking
        )

    # ------------------------------------------------------------------
    # Light state enforcement (window brightness applies no matter how the
    # light was turned on — motion, physical paddle tap, or another
    # integration entirely)
    # ------------------------------------------------------------------

    @callback
    def _handle_light_change(self, event: Event) -> None:
        """Route any switch-triggered on into the full-bright auto-shutoff hold.

        Z-Wave dimmers restore their own last-remembered level on a plain
        physical tap, independent of anything Luminary commanded — that
        level can be stale (e.g. left over from the last nightlight-window
        use). Also covers a 3-way companion switch on the same circuit: it
        can turn the light on without going through this device's own
        Central Scene events at all (2026-08-02 hallway incident — a
        companion switch restored a stale ~1% nightlight level in broad
        daylight, and nothing was watching to correct or ever turn it back
        off, since this listener used to stand down whenever it wasn't dark
        enough).

        Nightlight dim brightness is reserved for motion-triggered
        activations only — any switch, main or companion, always means full
        brightness, any time of day. Self-commanded changes (identified by
        context id, set by _light_on) are ignored so this doesn't fight the
        motion sequence's own dim-brightness calls or re-trigger itself.
        """
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state != "on":
            return
        context = getattr(event, "context", None)
        if context is not None and context.id == self._own_light_context_id:
            return
        if self.automation_disabled:
            return
        self.hass.async_create_task(self._start_switch_on_hold())

    # ------------------------------------------------------------------
    # Switch-triggered on: full brightness + timed auto-shutoff
    # ------------------------------------------------------------------

    async def _start_switch_on_hold(self) -> None:
        """Turn the light on at full/normal brightness and hold it via a
        timed auto-shutoff sequence.

        Entered by any switch-triggered on: the main paddle's single-tap-up,
        or an unsolicited on-report from a non-Central-Scene companion
        switch (routed here by _handle_light_change). A no-op in Disabled
        (double-tap) mode — that mode intentionally has no nightlight, no
        motion, and no auto-shutoff at all.
        """
        if self.automation_disabled:
            return
        await self._set_switch("turn_on", self.eid("switch", "motion_blocker"), blocking=True)
        if self._motion_task and not self._motion_task.done():
            self._motion_task.cancel()
        await self._light_on(self.normal_brightness)
        self._motion_task = self.hass.async_create_task(self._run_switch_on_sequence())

    async def _run_switch_on_sequence(self) -> None:
        """Auto-shutoff for a switch-triggered on.

        Unlike the motion sequence — which is torn down and restarted fresh
        by every new motion trigger via _restart_motion_task — this sequence
        keeps running for as long as motion_blocker holds it exclusive of
        the ordinary motion listener (see _handle_sensor_change's guard), so
        it has to watch for re-triggers itself: each clear + light_on_time_sec
        grace period that ends with a sensor back on loops back to waiting
        again, rather than turning off out from under someone still there.
        switch_on_timeout_sec is an overall cap so it still turns off
        eventually even if the space is never genuinely clear (or a sensor
        is stuck) — that's the actual point of an *auto-shutoff*.

        The grace period watches for any new on-event over its *entire*
        window rather than sleeping blind and sampling state once at the
        end — sensors with a short onboard hardware clear-timeout (e.g. 10s)
        report brief off-blips during continuous real occupancy, so a single
        instantaneous sample can land mid-blip and wrongly conclude the room
        is empty (2026-08-10 kitchen incident: cabinet lights kept getting
        cut mid-cooking well under the 60-minute cap). Any retrigger during
        the window now resets it, the same guarantee _restart_motion_task
        already gives the ordinary motion path.
        """
        deadline = asyncio.get_running_loop().time() + self.switch_on_timeout_sec
        try:
            while True:
                cleared = asyncio.Event()
                retriggered = asyncio.Event()

                @callback
                def _on_change(event: Event) -> None:
                    new_state = event.data.get("new_state")
                    if new_state is not None and new_state.state == "on":
                        retriggered.set()
                    if not any(self.hass.states.is_state(s, "on") for s in self.sensors):
                        cleared.set()

                unsub = async_track_state_change_event(self.hass, self.sensors, _on_change)
                try:
                    if not any(self.hass.states.is_state(s, "on") for s in self.sensors):
                        cleared.set()
                    remaining = max(deadline - asyncio.get_running_loop().time(), 0)
                    try:
                        await asyncio.wait_for(cleared.wait(), timeout=remaining)
                    except asyncio.TimeoutError:
                        break  # cap reached; shut off regardless of activity

                    retriggered.clear()
                    remaining = max(deadline - asyncio.get_running_loop().time(), 0)
                    grace = min(float(self.light_on_time_sec), remaining)
                    try:
                        await asyncio.wait_for(retriggered.wait(), timeout=grace)
                    except asyncio.TimeoutError:
                        break  # no retrigger for the full grace window; genuinely clear
                finally:
                    unsub()

                if asyncio.get_running_loop().time() >= deadline:
                    break
                # else: re-triggered during the grace period; loop back and wait for clear again

            if self.automation_disabled:
                return
            await self._light_off()
            await self._set_switch("turn_off", self.eid("switch", "motion_blocker"), blocking=True)
        except asyncio.CancelledError:
            raise  # Propagate so the task is properly cancelled

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

            # Wait for all sensors to clear. 30-minute cap handles stuck sensors.
            cleared = asyncio.Event()

            @callback
            def _on_change(_event: Event) -> None:
                if not any(self.hass.states.is_state(s, "on") for s in self.sensors):
                    cleared.set()

            unsub = async_track_state_change_event(
                self.hass, self.sensors, _on_change
            )
            # Race guard: sensors may have cleared between the _sensors_any_on
            # check in the caller and listener registration here.  The callback
            # won't fire for a transition that already happened, so seed the
            # event manually if sensors are already off.
            if not any(self.hass.states.is_state(s, "on") for s in self.sensors):
                cleared.set()
            stuck = False
            try:
                await asyncio.wait_for(cleared.wait(), timeout=self._stuck_timeout)
            except asyncio.TimeoutError:
                stuck = True
            finally:
                unsub()

            # Post-motion delay: keep the light on for light_on_time_sec after
            # all motion clears (skipped when sensors are stuck).
            if not stuck:
                await asyncio.sleep(float(self.light_on_time_sec))

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
        if self.switch_device and data.get("device_id") != self.switch_device:
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
        """Full bright with a timed auto-shutoff hold (smart) or plain on (dumb).

        Delegates to _start_switch_on_hold — the same entry point a
        non-Central-Scene companion switch reaches via _handle_light_change —
        so a main-paddle tap and a companion-switch press behave identically:
        full brightness, never dim, auto-shutoff unless Disabled mode.
        """
        if self.automation_disabled:
            await self._light_on(self.normal_brightness)
            return
        await self._start_switch_on_hold()

    async def _single_tap_down(self) -> None:
        """Full off now; hand control back to automation (smart) or plain off (dumb).

        Always turns the light off immediately. Previously this released
        the override and, if motion was still active, handed control back
        to the motion sequence instead of actually turning off — so a
        down-press during active motion looked like it did nothing
        (2026-08-01). The fix for that briefly engaged the override flag,
        but never cleared it again, so it latched "Manual Override"
        permanently and silently blocked all future motion until an
        explicit double-tap (caught 2026-08-02 on the pantry zone).

        The override flag is only held for the duration of the off-command
        itself, to block a sensor re-trigger racing with it (e.g. flapping
        right at the sensor's own hardware clear-timeout) from turning the
        light back on out from under this call. It's cleared again right
        after, so this is a momentary suppression, not a persistent
        override — unlike _single_tap_up. Any lingering motion task from
        before the tap is cancelled rather than restarted, so the off
        sticks until a genuinely new motion transition comes in; double-tap
        down remains the explicit "resume automation" gesture.
        """
        if self.automation_disabled:
            await self._light_off()
            return
        await self._set_switch("turn_on", self.eid("switch", "motion_blocker"), blocking=True)
        if self._motion_task and not self._motion_task.done():
            self._motion_task.cancel()
        await self._light_off()
        await self._set_switch("turn_off", self.eid("switch", "motion_blocker"), blocking=True)

    async def _double_tap_up(self) -> None:
        """Enable dumb mode; preserve light state."""
        await self._set_switch("turn_on", self.eid("switch", "automation_disabled"))
        await self._set_switch("turn_off", self.eid("switch", "motion_blocker"))

    async def _double_tap_down(self) -> None:
        """Disable dumb mode; resume automation."""
        await self._set_switch("turn_off", self.eid("switch", "automation_disabled"))
        await self._set_switch("turn_off", self.eid("switch", "motion_blocker"))
        if self._sensors_any_on:
            self._restart_motion_task()
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
    # Dead/stuck-sensor detection (last_seen staleness — see const.py comment)
    # ------------------------------------------------------------------

    @callback
    def _check_stale_sensors(self, now: datetime) -> None:
        """Periodic poll — staleness is an absence of updates, so it can't be caught
        by a state-change listener; something has to actively check the clock."""
        for sensor_entity_id in self.sensors:
            display_entity = self.last_seen_entities.get(sensor_entity_id)
            if display_entity is not None:
                display_entity.async_write_ha_state()

            stale_seconds = self.sensor_stale_seconds(sensor_entity_id)
            is_stale = stale_seconds is not None and stale_seconds > self.stale_sensor_threshold_sec
            was_notified = sensor_entity_id in self._stale_notified

            if is_stale and not was_notified:
                self._stale_notified.add(sensor_entity_id)
                self.hass.async_create_task(self._notify_sensor_stale(sensor_entity_id, stale_seconds))
            elif not is_stale and was_notified:
                self._stale_notified.discard(sensor_entity_id)
                self.hass.async_create_task(self._clear_sensor_stale(sensor_entity_id))

    async def _notify_sensor_stale(self, sensor_entity_id: str, stale_seconds: float) -> None:
        state = self.hass.states.get(sensor_entity_id)
        name = state.attributes.get("friendly_name") if state else sensor_entity_id
        minutes = int(stale_seconds // 60)
        await self.hass.services.async_call(
            "persistent_notification", "create",
            {
                "notification_id": f"{self.zone_id}_sensor_stale_{slugify(sensor_entity_id)}",
                "title": f"{self.zone_name}: Motion Sensor May Be Dead",
                "message": (
                    f"{name} hasn't reported anything in {minutes} minutes — far longer "
                    "than normal, even for a sleeping battery sensor. Motion detection "
                    "here may be degraded even though the sensor's own state doesn't "
                    "show unavailable. Check its battery."
                ),
            },
            blocking=False,
        )

    async def _clear_sensor_stale(self, sensor_entity_id: str) -> None:
        await self.hass.services.async_call(
            "persistent_notification", "dismiss",
            {"notification_id": f"{self.zone_id}_sensor_stale_{slugify(sensor_entity_id)}"},
            blocking=False,
        )

    # ------------------------------------------------------------------
    # Hardware timeout live updates
    # ------------------------------------------------------------------

    @callback
    def _handle_hw_timeout_source_changed(self, event: Event) -> None:
        """A tracked Configuration CC / Z2M timeout entity changed state.

        Luminary doesn't own this value (see sensor_hw_timeout()) — this just
        re-renders the affected entities and re-checks the light_on_time_sec floor.
        """
        source_entity_id = event.data.get("entity_id")
        sensor_entity_id = self._hw_timeout_source_map.get(source_entity_id)
        if sensor_entity_id is None:
            return
        display_entity = self._hw_timeout_entities.get(sensor_entity_id)
        if display_entity is not None:
            display_entity.async_write_ha_state()
        if self.light_on_time_entity is not None:
            self.light_on_time_entity.async_write_ha_state()
        self.hass.async_create_task(self._maybe_bump_light_on_time_floor())

    async def _maybe_bump_light_on_time_floor(self) -> None:
        """If the hardware-timeout floor now exceeds light_on_time_sec, raise it and notify.

        Runs both on live source-entity changes and once at startup (an existing zone's
        light_on_time_sec may already sit below the floor after this feature is first
        confirmed, or after the user lowers it manually).
        """
        if self.light_on_time_entity is None:
            return
        floor = self.max_sensor_hw_timeout()
        current = self.light_on_time_sec
        if floor <= current:
            return
        driving_sensor = next(
            (sid for sid in self.sensors if self.sensor_hw_timeout(sid) == floor), None
        )
        self.light_on_time_entity._attr_native_value = floor
        self.light_on_time_entity.async_write_ha_state()
        sensor_state = self.hass.states.get(driving_sensor) if driving_sensor else None
        sensor_name = (
            sensor_state.attributes.get("friendly_name")
            if sensor_state
            else driving_sensor or "A motion sensor"
        )
        await self.hass.services.async_call(
            "persistent_notification", "create",
            {
                "notification_id": f"{self.zone_id}_light_on_time_floor",
                "title": f"{self.zone_name}: post-motion delay raised to {int(floor)}s",
                "message": (
                    f"{sensor_name} has a hardware clear timeout of {int(floor)}s, longer than "
                    f"the previously configured {int(current)}s post-motion delay. Raised "
                    "automatically to prevent light flicker."
                ),
            },
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
        """Register listeners for external entities (sensors, Z-Wave).

        Own-entity listeners require the entity registry to be populated first;
        call async_register_entity_listeners() after platforms are set up.
        """
        if self.sensors:
            self._unsub_listeners.append(
                async_track_state_change_event(
                    self.hass, self.sensors, self._handle_sensor_change
                )
            )
        self._unsub_listeners.append(
            self.hass.bus.async_listen(
                "zwave_js_value_notification", self._handle_zwave_event
            )
        )
        if self.light:
            self._unsub_listeners.append(
                async_track_state_change_event(
                    self.hass, [self.light], self._handle_light_change
                )
            )
        self._sensors_any_on = any(
            self.hass.states.is_state(s, "on") for s in self.sensors
        )

        # Hardware-timeout live tracking: watch each confirmed sensor's source entity
        # (Configuration CC / Z2M number entity) so the floor stays current without
        # Luminary caching the value itself.
        hw_timeouts = self.entry.options.get(CONF_SENSOR_HW_TIMEOUTS, {})
        self._hw_timeout_source_map = {
            info["source_entity_id"]: sensor_entity_id
            for sensor_entity_id, info in hw_timeouts.items()
            if info.get("source_entity_id")
        }
        if self._hw_timeout_source_map:
            self._unsub_listeners.append(
                async_track_state_change_event(
                    self.hass,
                    list(self._hw_timeout_source_map),
                    self._handle_hw_timeout_source_changed,
                )
            )

        # Dead/stuck-sensor detection: discover each sensor's own "last communicated"
        # diagnostic entity, then periodically check for staleness (see const.py
        # comment). Unlike the hw-timeout Configuration CC entities, this one is
        # enabled by default, so no confirm-step / config-flow involvement is needed.
        for sensor_entity_id in self.sensors:
            result = await hw_timeout.async_detect_last_seen(self.hass, sensor_entity_id)
            if result.ok and result.source_entity_id:
                self._last_seen_source_map[sensor_entity_id] = result.source_entity_id
        self._unsub_listeners.append(
            async_track_time_interval(
                self.hass, self._check_stale_sensors, timedelta(seconds=STALE_CHECK_INTERVAL_SEC)
            )
        )

    @callback
    def async_register_entity_listeners(self) -> None:
        """Register state-change listeners for own entities.

        Must be called after async_forward_entry_setups() so the entity
        registry is populated and eid() can resolve the correct entity_ids.
        """
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
        self._unsub_listeners.append(
            async_track_state_change_event(
                self.hass,
                [self.eid("time", "dim_start"), self.eid("time", "dim_end")],
                self._handle_dim_time_entity_changed,
            )
        )
        # Startup floor check: an existing zone's light_on_time_sec may already sit
        # below the hardware-timeout floor (first confirm-timeout run after upgrade,
        # or the user lowered it manually since the last check).
        self.hass.async_create_task(self._maybe_bump_light_on_time_floor())

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

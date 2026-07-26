"""Detect a motion sensor's onboard hardware clear-timeout.

Reads directly from the entity/device registries of whichever integration owns the
physical sensor (Z-Wave JS or Zigbee2MQTT/mqtt) — Luminary never caches this value
itself, since the owning integration is the source of truth for its own device
parameters. See coordinator.py's sensor_hw_timeout()/max_sensor_hw_timeout() for the
live-read side of this.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import (
    ZIGBEE2MQTT_TIMEOUT_PROPERTY_KEYS,
    ZIGBEE2MQTT_UNIQUE_ID_SUFFIX,
    ZWAVE_CONFIG_CC,
    ZWAVE_CONFIG_PARAM_TIMEOUT_MAP,
    ZWAVE_TIMEOUT_KEYWORD_HINTS,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

DetectionReason = Literal[
    "ok",
    "no_registry_entry",
    "no_device",
    "unsupported_platform",
    "no_matching_parameter",
    "value_unavailable",
]

DetectionSource = Literal["zwave", "zigbee2mqtt"]


@dataclass(frozen=True)
class DetectionResult:
    entity_id: str
    ok: bool
    value: float | None = None
    source: DetectionSource | None = None
    source_entity_id: str | None = None
    reason: DetectionReason | None = None

    @classmethod
    def failure(
        cls,
        entity_id: str,
        reason: DetectionReason,
        *,
        source: DetectionSource | None = None,
        source_entity_id: str | None = None,
    ) -> "DetectionResult":
        return cls(
            entity_id=entity_id,
            ok=False,
            reason=reason,
            source=source,
            source_entity_id=source_entity_id,
        )

    @classmethod
    def success(
        cls, entity_id: str, value: float, source: DetectionSource, source_entity_id: str
    ) -> "DetectionResult":
        return cls(
            entity_id=entity_id,
            ok=True,
            value=value,
            source=source,
            source_entity_id=source_entity_id,
            reason="ok",
        )


def _read_float_state(hass: "HomeAssistant", entity_id: str) -> float | None:
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unknown", "unavailable"):
        return None
    try:
        return float(state.state)
    except (ValueError, TypeError):
        return None


def _zwave_config_param(unique_id: str) -> int | None:
    """Parse "{homeId}.{nodeId}-112-0-{paramNumber}" -> paramNumber, or None if not a match."""
    parts = unique_id.split("-")
    if len(parts) < 4 or parts[1] != ZWAVE_CONFIG_CC:
        return None
    try:
        return int(parts[3])
    except ValueError:
        return None


def _find_zwave_timeout_entity(
    siblings: list, manufacturer: str | None, model: str | None
) -> object | None:
    config_siblings = [e for e in siblings if e.unique_id and _zwave_config_param(e.unique_id) is not None]

    param_number = ZWAVE_CONFIG_PARAM_TIMEOUT_MAP.get((manufacturer or "", model or ""))
    if param_number is not None:
        for entry in config_siblings:
            if _zwave_config_param(entry.unique_id) == param_number:
                return entry

    for entry in config_siblings:
        name = (entry.original_name or entry.translation_key or "").lower()
        if any(hint in name for hint in ZWAVE_TIMEOUT_KEYWORD_HINTS):
            return entry
    return None


def _zigbee2mqtt_property_key(unique_id: str) -> str | None:
    """Parse "{ieeeAddr}_{property_key}_zigbee2mqtt" -> property_key, or None if not a match."""
    if not unique_id.endswith(ZIGBEE2MQTT_UNIQUE_ID_SUFFIX):
        return None
    stripped = unique_id[: -len(ZIGBEE2MQTT_UNIQUE_ID_SUFFIX)]
    if "_" not in stripped:
        return None
    _, _, property_key = stripped.partition("_")
    return property_key or None


def _find_zigbee2mqtt_timeout_entity(siblings: list) -> object | None:
    z2m_siblings = [
        (entry, _zigbee2mqtt_property_key(entry.unique_id))
        for entry in siblings
        if entry.unique_id
    ]
    z2m_siblings = [(entry, key) for entry, key in z2m_siblings if key]

    for entry, key in z2m_siblings:
        if key in ZIGBEE2MQTT_TIMEOUT_PROPERTY_KEYS:
            return entry
    for entry, key in z2m_siblings:
        if "timeout" in key:
            return entry
    return None


async def async_detect_hw_timeout(hass: "HomeAssistant", sensor_entity_id: str) -> DetectionResult:
    """Detect sensor_entity_id's onboard hardware clear-timeout, if any is discoverable."""
    ent_reg = er.async_get(hass)
    entry = ent_reg.async_get(sensor_entity_id)
    if entry is None:
        return DetectionResult.failure(sensor_entity_id, "no_registry_entry")

    device_id = entry.device_id
    if device_id is None:
        return DetectionResult.failure(sensor_entity_id, "no_device")

    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get(device_id)
    siblings = er.async_entries_for_device(ent_reg, device_id, include_disabled_entities=True)

    if entry.platform == "zwave_js":
        manufacturer = device.manufacturer if device else None
        model = device.model if device else None
        match = _find_zwave_timeout_entity(siblings, manufacturer, model)
        if match is None:
            return DetectionResult.failure(sensor_entity_id, "no_matching_parameter")
        value = _read_float_state(hass, match.entity_id)
        if value is None:
            return DetectionResult.failure(
                sensor_entity_id, "value_unavailable", source="zwave", source_entity_id=match.entity_id
            )
        return DetectionResult.success(sensor_entity_id, value, "zwave", match.entity_id)

    if entry.platform == "mqtt":
        match = _find_zigbee2mqtt_timeout_entity(siblings)
        if match is None:
            return DetectionResult.failure(sensor_entity_id, "no_matching_parameter")
        value = _read_float_state(hass, match.entity_id)
        if value is None:
            return DetectionResult.failure(
                sensor_entity_id, "value_unavailable", source="zigbee2mqtt", source_entity_id=match.entity_id
            )
        return DetectionResult.success(sensor_entity_id, value, "zigbee2mqtt", match.entity_id)

    return DetectionResult.failure(sensor_entity_id, "unsupported_platform")


async def async_detect_all(
    hass: "HomeAssistant", sensor_entity_ids: list[str]
) -> dict[str, DetectionResult]:
    return {sid: await async_detect_hw_timeout(hass, sid) for sid in sensor_entity_ids}


# ---------------------------------------------------------------------------
# "Last seen" discovery — for dead/stuck-sensor detection via communication
# staleness rather than the sensor's own reported state (see const.py comment).
# ---------------------------------------------------------------------------


def _find_last_seen_entity(siblings: list) -> object | None:
    """Match a sensor's own "last communicated" diagnostic sibling.

    Confirmed naming convention against real hardware: Z-Wave JS's "Last Seen" entity
    is `sensor.<node>_last_seen`. No Zigbee2MQTT equivalent has been confirmed against
    real hardware in this house yet — the suffix check is generic enough to catch one
    if the naming convention matches, but treat that path as unverified.
    """
    for entry in siblings:
        if entry.entity_id.startswith("sensor.") and entry.entity_id.endswith("_last_seen"):
            return entry
    return None


def _read_timestamp_state(hass: "HomeAssistant", entity_id: str) -> datetime | None:
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unknown", "unavailable"):
        return None
    try:
        return datetime.fromisoformat(state.state)
    except (ValueError, TypeError):
        return None


async def async_detect_last_seen(hass: "HomeAssistant", sensor_entity_id: str) -> DetectionResult:
    """Detect sensor_entity_id's "last communicated" diagnostic sibling, if any.

    Unlike hw-timeout Configuration CC entities, Z-Wave JS's Last Seen sensor is
    enabled by default — no user confirmation step needed before Luminary can read a
    live value, unlike sensor_hw_timeout()'s confirm_timeout config-flow step.
    """
    ent_reg = er.async_get(hass)
    entry = ent_reg.async_get(sensor_entity_id)
    if entry is None:
        return DetectionResult.failure(sensor_entity_id, "no_registry_entry")

    device_id = entry.device_id
    if device_id is None:
        return DetectionResult.failure(sensor_entity_id, "no_device")

    if entry.platform not in ("zwave_js", "mqtt"):
        return DetectionResult.failure(sensor_entity_id, "unsupported_platform")

    siblings = er.async_entries_for_device(ent_reg, device_id, include_disabled_entities=True)
    match = _find_last_seen_entity(siblings)
    if match is None:
        return DetectionResult.failure(sensor_entity_id, "no_matching_parameter")

    source: DetectionSource = "zwave" if entry.platform == "zwave_js" else "zigbee2mqtt"
    ts = _read_timestamp_state(hass, match.entity_id)
    if ts is None:
        return DetectionResult.failure(
            sensor_entity_id, "value_unavailable", source=source, source_entity_id=match.entity_id
        )
    return DetectionResult.success(sensor_entity_id, ts.timestamp(), source, match.entity_id)

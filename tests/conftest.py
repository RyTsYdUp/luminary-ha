"""
Stub out homeassistant imports so the coordinator can be imported
without a full HA installation.
"""
import sys
import os
from unittest.mock import MagicMock

# Put the project root on sys.path so custom_components is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# --- Lightweight HA stubs ---

def _callback(func):
    """Passthrough for the @callback decorator."""
    return func


class _Event:
    """Minimal Event for constructing test payloads."""
    def __init__(self, data=None, context=None):
        self.data = data or {}
        self.context = context


class _Context:
    """Minimal Context: real HA gives every service call/event a unique id
    so listeners can tell their own commanded changes apart from external
    ones. A plain MagicMock() wouldn't do here — by default every call to a
    Mock returns the *same* cached return_value, so every "new" Context
    would compare equal to every other one and defeat that distinction."""
    _counter = 0

    def __init__(self):
        _Context._counter += 1
        self.id = f"test-ctx-{_Context._counter}"


# homeassistant.core
_core = MagicMock()
_core.callback = _callback
_core.Event = _Event
_core.Context = _Context
_core.HomeAssistant = object

# homeassistant.helpers.event  –  functions are mocked per-test via patch()
_helpers_event = MagicMock()
_helpers_event.async_track_state_change_event = MagicMock(return_value=lambda: None)
_helpers_event.async_track_time_change = MagicMock(return_value=lambda: None)


# --- entity_registry / device_registry fakes ---
#
# Real HA shape: `entity_registry.async_get(hass) -> EntityRegistry`,
# `EntityRegistry.async_get(entity_id) -> RegistryEntry | None`,
# `EntityRegistry.async_get_entity_id(domain, platform, unique_id) -> str | None`,
# module-level `entity_registry.async_entries_for_device(registry, device_id,
# include_disabled_entities=False) -> list[RegistryEntry]`. Mirrored here with plain
# objects (not MagicMock) so hw_timeout.py tests can seed real, inspectable fixtures.
# `async_get_entity_id` returns None by default (matching real "not found" behavior) —
# this preserves coordinator.eid()'s existing fallback-to-constructed-id path, which
# earlier tests already implicitly depend on.

class FakeRegistryEntry:
    def __init__(self, entity_id, unique_id=None, platform=None, device_id=None,
                 original_name=None, translation_key=None, disabled_by=None):
        self.entity_id = entity_id
        self.unique_id = unique_id
        self.platform = platform
        self.device_id = device_id
        self.original_name = original_name
        self.translation_key = translation_key
        self.disabled_by = disabled_by


class FakeDeviceEntry:
    def __init__(self, id, manufacturer=None, model=None):
        self.id = id
        self.manufacturer = manufacturer
        self.model = model


class FakeEntityRegistry:
    def __init__(self):
        self.entities: dict[str, FakeRegistryEntry] = {}

    def add(self, entry: FakeRegistryEntry) -> FakeRegistryEntry:
        self.entities[entry.entity_id] = entry
        return entry

    def async_get(self, entity_id):
        return self.entities.get(entity_id)

    def async_get_entity_id(self, domain, platform, unique_id):
        for entry in self.entities.values():
            if entry.unique_id == unique_id:
                return entry.entity_id
        return None


class FakeDeviceRegistry:
    def __init__(self):
        self.devices: dict[str, FakeDeviceEntry] = {}

    def add(self, entry: FakeDeviceEntry) -> FakeDeviceEntry:
        self.devices[entry.id] = entry
        return entry

    def async_get(self, device_id):
        return self.devices.get(device_id)


def make_fake_registries():
    """Return a fresh (entity_registry, device_registry) pair for a single test."""
    return FakeEntityRegistry(), FakeDeviceRegistry()


def _er_async_entries_for_device(registry, device_id, include_disabled_entities=False):
    entries = [e for e in registry.entities.values() if e.device_id == device_id]
    if not include_disabled_entities:
        entries = [e for e in entries if e.disabled_by is None]
    return entries


_helpers_entity_registry = MagicMock()
_helpers_entity_registry.RegistryEntry = FakeRegistryEntry
_helpers_entity_registry.async_entries_for_device = _er_async_entries_for_device
# .async_get(hass) is patched per-test (via unittest.mock.patch) to return a
# test-specific FakeEntityRegistry instance — same pattern already used for
# homeassistant.helpers.event functions.

_helpers_device_registry = MagicMock()
_helpers_device_registry.DeviceEntry = FakeDeviceEntry

# --- Minimal real (non-MagicMock) entity/platform base classes ---
#
# These are used as actual Python base classes (`class LuminaryNumber(NumberEntity,
# RestoreEntity):`) and as a frozen-dataclass base (`class LuminaryNumberDescription
# (NumberEntityDescription):`) — a bare MagicMock can't serve either role, so real
# lightweight stand-ins are needed here, not just attribute mocks.

from dataclasses import dataclass as _dataclass


class EntityCategory:
    CONFIG = "config"
    DIAGNOSTIC = "diagnostic"


class DeviceInfo(dict):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class _FakeEntityBase:
    """Stand-in for homeassistant.helpers.entity.Entity."""
    hass = None

    def async_write_ha_state(self) -> None:
        pass

    def async_on_remove(self, func) -> None:
        pass


class RestoreEntity(_FakeEntityBase):
    async def async_added_to_hass(self) -> None:
        pass

    async def async_get_last_state(self):
        return None


@_dataclass(frozen=True, kw_only=True)
class EntityDescription:
    key: str
    translation_key: str | None = None
    icon: str | None = None
    entity_category: str | None = None
    device_class: str | None = None


@_dataclass(frozen=True, kw_only=True)
class NumberEntityDescription(EntityDescription):
    native_unit_of_measurement: str | None = None
    native_min_value: float | None = None
    native_max_value: float | None = None
    native_step: float | None = None
    mode: str | None = None


class NumberEntity(_FakeEntityBase):
    pass


class NumberMode:
    BOX = "box"
    SLIDER = "slider"
    AUTO = "auto"


@_dataclass(frozen=True, kw_only=True)
class SensorEntityDescription(EntityDescription):
    native_unit_of_measurement: str | None = None
    state_class: str | None = None


class SensorEntity(_FakeEntityBase):
    pass


@_dataclass(frozen=True, kw_only=True)
class ButtonEntityDescription(EntityDescription):
    pass


class ButtonEntity(_FakeEntityBase):
    pass


_helpers_entity = MagicMock()
_helpers_entity.DeviceInfo = DeviceInfo
_helpers_entity.EntityCategory = EntityCategory
_helpers_entity.Entity = _FakeEntityBase
_helpers_entity.EntityDescription = EntityDescription

_helpers_entity_platform = MagicMock()  # AddEntitiesCallback etc. — type-hint only, never evaluated
_helpers_restore_state = MagicMock()
_helpers_restore_state.RestoreEntity = RestoreEntity

_components_number = MagicMock()
_components_number.NumberEntity = NumberEntity
_components_number.NumberEntityDescription = NumberEntityDescription
_components_number.NumberMode = NumberMode

_components_sensor = MagicMock()
_components_sensor.SensorEntity = SensorEntity
_components_sensor.SensorEntityDescription = SensorEntityDescription

_components_button = MagicMock()
_components_button.ButtonEntity = ButtonEntity
_components_button.ButtonEntityDescription = ButtonEntityDescription

_homeassistant = sys.modules.setdefault("homeassistant", MagicMock())
sys.modules["homeassistant.core"] = _core
sys.modules["homeassistant.helpers.entity"] = _helpers_entity
sys.modules["homeassistant.helpers.entity_platform"] = _helpers_entity_platform
sys.modules["homeassistant.helpers.restore_state"] = _helpers_restore_state
sys.modules["homeassistant.components.number"] = _components_number
sys.modules["homeassistant.components.sensor"] = _components_sensor
sys.modules["homeassistant.components.button"] = _components_button

# `homeassistant.helpers` itself is a bare MagicMock, and `from homeassistant.helpers
# import entity_registry as er`-style imports resolve via Python's fromlist "hasattr"
# fast path — since a MagicMock answers hasattr() true for any name, that path never
# consults sys.modules and would silently hand back a fresh, unrelated auto-vivified
# mock instead of the real fakes registered below. Explicitly wiring the submodule
# attributes onto the parent mock (in addition to registering them in sys.modules, for
# any `import homeassistant.helpers.x` direct-dotted-path form) makes both import
# styles resolve to the same objects.
_helpers = MagicMock()
_helpers.event = _helpers_event
_helpers.entity_registry = _helpers_entity_registry
_helpers.device_registry = _helpers_device_registry
_helpers.entity = _helpers_entity
_helpers.entity_platform = _helpers_entity_platform
_helpers.restore_state = _helpers_restore_state
sys.modules["homeassistant.helpers"] = _helpers
sys.modules["homeassistant.helpers.event"] = _helpers_event
sys.modules["homeassistant.helpers.entity_registry"] = _helpers_entity_registry
sys.modules["homeassistant.helpers.device_registry"] = _helpers_device_registry

_const = MagicMock()  # UnitOfTime.SECONDS etc. — opaque values, never compared meaningfully in tests
sys.modules["homeassistant.const"] = _const


def _slugify(text: str) -> str:
    import re
    return re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_")


_util = MagicMock()
_util.slugify = _slugify
sys.modules["homeassistant.util"] = _util


# --- config_entries / area_registry fakes (for config_flow.py) ---
#
# Real HA's ConfigFlow/OptionsFlow are proper classes with async_show_form,
# async_create_entry, async_set_unique_id, etc. — a bare MagicMock can't be
# subclassed (`class Foo(config_entries.ConfigFlow, domain=...)` needs a real type),
# so minimal working stand-ins are needed, not just attribute mocks.

class FlowResult(dict):
    pass


class _FakeFlowBase:
    hass = None

    async def async_set_unique_id(self, unique_id):
        self._unique_id = unique_id

    def _abort_if_unique_id_configured(self):
        pass

    def async_show_form(self, *, step_id, data_schema=None, errors=None, description_placeholders=None):
        return FlowResult(
            type="form",
            step_id=step_id,
            data_schema=data_schema,
            errors=errors or {},
            description_placeholders=description_placeholders or {},
        )

    def async_create_entry(self, *, title, data, options=None):
        result = FlowResult(type="create_entry", title=title, data=data)
        if options is not None:
            result["options"] = options
        return result

    def add_suggested_values_to_schema(self, schema, suggested):
        return schema  # simplified: real HA merges suggested values into field defaults


class ConfigFlow(_FakeFlowBase):
    def __init_subclass__(cls, *, domain=None, **kwargs):
        super().__init_subclass__(**kwargs)
        cls._domain = domain


class OptionsFlow(_FakeFlowBase):
    pass


class ConfigEntry:
    pass


_config_entries = MagicMock()
_config_entries.ConfigFlow = ConfigFlow
_config_entries.OptionsFlow = OptionsFlow
_config_entries.ConfigEntry = ConfigEntry
_config_entries.FlowResult = FlowResult
sys.modules["homeassistant.config_entries"] = _config_entries


class FakeArea:
    def __init__(self, area_id, name):
        self.id = area_id
        self.name = name


class FakeAreaRegistry:
    def __init__(self):
        self.areas: dict[str, FakeArea] = {}

    def add(self, area: FakeArea) -> FakeArea:
        self.areas[area.id] = area
        return area

    def async_get_area(self, area_id):
        return self.areas.get(area_id)


_helpers_area_registry = MagicMock()
_helpers_area_registry.FakeAreaRegistry = FakeAreaRegistry
# .async_get(hass) patched per-test, same pattern as entity_registry/device_registry
sys.modules["homeassistant.helpers.area_registry"] = _helpers_area_registry

_helpers_selector = MagicMock()  # AreaSelector/EntitySelector/NumberSelector/etc. — constructed, not exercised
_helpers.selector = _helpers_selector
_helpers.area_registry = _helpers_area_registry
sys.modules["homeassistant.helpers.selector"] = _helpers_selector

_components = MagicMock()
_components.number = _components_number
_components.sensor = _components_sensor
_components.button = _components_button
sys.modules["homeassistant.components"] = _components

# Top-level `homeassistant` package attributes — needed for `from homeassistant import
# config_entries`-style imports (fromlist relative to the top-level package itself),
# which hit the same hasattr-fast-path issue described above.
_homeassistant.core = _core
_homeassistant.config_entries = _config_entries
_homeassistant.const = _const
_homeassistant.util = _util
_homeassistant.helpers = _helpers
_homeassistant.components = _components

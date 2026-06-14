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
    def __init__(self, data=None):
        self.data = data or {}


# homeassistant.core
_core = MagicMock()
_core.callback = _callback
_core.Event = _Event
_core.HomeAssistant = object

# homeassistant.helpers.event  –  functions are mocked per-test via patch()
_helpers_event = MagicMock()
_helpers_event.async_track_state_change_event = MagicMock(return_value=lambda: None)
_helpers_event.async_track_time_change = MagicMock(return_value=lambda: None)

sys.modules.setdefault("homeassistant", MagicMock())
sys.modules["homeassistant.core"] = _core
sys.modules["homeassistant.config_entries"] = MagicMock()
sys.modules["homeassistant.helpers"] = MagicMock()
sys.modules["homeassistant.helpers.event"] = _helpers_event
sys.modules["homeassistant.const"] = MagicMock()

"""Unit tests for switch.py's restore behaviour.

The interesting case here is motion_blocker, which deliberately does *not*
restore across a restart — see the 2026-08-10 review note on SWITCHES.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.luminary_ha.coordinator import ZoneCoordinator
from custom_components.luminary_ha.switch import SWITCHES, LuminarySwitch

from tests.test_coordinator import _make_entry, _make_hass

BLOCKER_DESC = next(d for d in SWITCHES if d.key == "motion_blocker")
NIGHTLIGHT_DESC = next(d for d in SWITCHES if d.key == "nightlight_enabled")
DISABLED_DESC = next(d for d in SWITCHES if d.key == "automation_disabled")


def _entity(description, last_state: str | None):
    coord = ZoneCoordinator(_make_hass(), _make_entry())
    entity = LuminarySwitch(coord, description)

    async def _last():
        if last_state is None:
            return None
        restored = MagicMock()
        restored.state = last_state
        return restored

    entity.async_get_last_state = _last
    return entity


async def test_motion_blocker_does_not_restore_on_state():
    """A restart or integration reload mid-hold must not resurrect the flag.

    _start_switch_on_hold raises motion_blocker and only _run_switch_on_sequence
    lowers it again; async_unload cancels that task. Restoring "on" therefore
    brings the flag back with nothing alive to ever clear it, and the zone sits
    in permanent "Manual Override" ignoring all motion (2026-08-10 review; same
    latch class as the 2026-08-02 single-tap-down bug).
    """
    entity = _entity(BLOCKER_DESC, "on")
    await entity.async_added_to_hass()
    assert entity._attr_is_on is False


async def test_motion_blocker_description_opts_out_of_restore():
    assert BLOCKER_DESC.restore is False


@pytest.mark.parametrize(
    ("description", "last_state", "expected"),
    [
        (NIGHTLIGHT_DESC, "off", False),
        (NIGHTLIGHT_DESC, "on", True),
        (DISABLED_DESC, "on", True),
        (DISABLED_DESC, "off", False),
    ],
)
async def test_user_preference_switches_still_restore(description, last_state, expected):
    """The opt-out is narrow: everything the user actually configures — the
    nightlight toggle, daytime detection, dumb mode — still survives a restart."""
    entity = _entity(description, last_state)
    await entity.async_added_to_hass()
    assert entity._attr_is_on is expected


async def test_restoring_switch_falls_back_to_default_with_no_prior_state():
    entity = _entity(NIGHTLIGHT_DESC, None)
    await entity.async_added_to_hass()
    assert entity._attr_is_on is NIGHTLIGHT_DESC.default_on is True

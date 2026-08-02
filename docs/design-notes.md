# Design Notes

Full design documentation for this project lives in Obsidian:

**`02 - Areas/Homelab/Hallway Lights Automation - HA Package.md`**

This file summarizes key decisions for contributors.

---

## Why a package and not a blueprint

Blueprints configure automation inputs at creation time only — there's no way to expose them as live-adjustable Lovelace sliders. This package uses `input_number`, `input_datetime`, and `input_select` helpers for all configurable values, so every parameter is adjustable from the dashboard without editing YAML.

The tradeoff is that the package isn't one-click installable from the HA Blueprints UI. Users do a search-and-replace on placeholder strings instead. The setup script (`scripts/setup.sh`) automates this.

## Sensor timeout strategy

Each physical sensor has a built-in re-trigger timeout configured at the device level (Z-Wave parameter 13). This package does **not** stack an additional software wait on top of that — lights go off as soon as all sensors report clear. This avoids double-timeout for pass-through areas like hallways.

The `ZONE_motion_timeout_min` helper is a *safety cap only* — it fires if sensors fail to report clear (Z-Wave packet loss, sensor fault, etc.). Normal operation never reaches it.

## Timer reset is free via `mode: restart`

The main motion automation uses `mode: restart`. Any new `to: on` trigger from any sensor restarts the automation, which also resets the safety timeout clock. A genuinely stuck sensor (one that never changes state) will not produce new triggers and the timeout will eventually fire.

## Midnight-crossing dim window

The dim window start/end comparison uses a template that handles spans crossing midnight:

```
if start < end:   in window when start ≤ now < end   (e.g. 01:00–06:00)
if start > end:   in window when now ≥ start OR now < end   (e.g. 23:00–06:00)
```

This logic appears in both `script.ZONE_set_brightness` and the status sensor template.

## Lux fail-safe

If the configured lux sensor entity is unavailable or returns `unknown`, the condition treats it as "dark" (automation runs). This prevents a sensor outage from leaving a room in permanent darkness.

## Switch scene 3 as panic reset

Scene 3 (config/middle button on most Zooz switches) clears both override flags and turns the light off. This is a recovery path if the room ends up in an unexpected state that's hard to debug at the switch.

## Status sensor states

| State | Meaning |
|---|---|
| `Automated` | Active — will respond to motion |
| `Standby` | Enabled but suppressed by sun/lux condition |
| `Manual Override` | Blocker flag on — auto-off disabled |
| `Disabled` | Dumb switch mode — all automation off |

`Standby` is important to distinguish from `Automated` — it tells the user the automation is working correctly and just isn't triggering because it's daytime/bright.

## Three-scene switch support

The package is designed for Z-Wave switches with 3 central scenes:
- Scene 001 (up paddle): single tap = manual on, double tap = disable automation
- Scene 002 (down paddle): single tap = manual off, double tap = re-enable automation
- Scene 003 (config/middle): single tap = panic reset

On 2-scene switches, scene 003 automation can be removed or repurposed.

## Single tap down now always turns the light off

Originally single tap down cleared the manual-override flag and, if any sensor was still reporting motion, called straight into the motion sequence instead of turning the light off — the idea being that "down" while still present in the room meant "hand control back to auto," not "off." In practice this meant a down-press in a room with any lingering motion (a hallway is rarely motion-free for more than a few seconds) looked like it did nothing: the light stayed on, immediately re-lit by the motion path this same press had just triggered.

Confirmed against a real switch: pressed down once, motion sensors were still active, light stayed on with no visible response. Fixed by making single tap down mirror single tap up — set the manual-override flag first (blocking, so the motion listener sees it before reacting), then unconditionally turn the light off, regardless of `_sensors_any_on`. "Return to auto" as a single-tap gesture is gone; double tap down already covers that (clears both override flags and resumes normal motion handling), so it remains the one explicit way back to automatic mode.

**Follow-up (found in production within a day):** the fix above set the override flag but never cleared it again, so *every* single tap-down permanently latched the zone into "Manual Override" — motion kept triggering the sensors but the light never turned back on, silently, until someone happened to double-tap. Hit on the pantry zone (shared integration code — one deploy reaches every zone at once). Corrected so the flag is held only for the duration of the off-command itself (still closes the same-instant sensor-flap race this was meant to guard against), then explicitly cleared right after. Any in-progress motion task is cancelled directly rather than handed to `_restart_motion_task()`, so the off still sticks through lingering motion — the sensor-change listener only reacts to a fresh off→on transition, not sustained "on" state, so no restart is needed to prevent an immediate re-light. Single tap down is a momentary suppression now, not a persistent override, unlike single tap up.

## Window brightness enforcement (not just Luminary's own actions)

Early versions only applied `target_brightness()` (dim-in-window / normal-outside) through paths Luminary itself triggered — motion, its own tap handlers, the dim-window boundary crossings. A physical switch tap that doesn't fire a recognized Central Scene event, or any other integration changing the light, was invisible to that logic. Confirmed live: a Zooz dimmer restoring its own last-remembered level (a stale nightlight-window brightness) on a plain physical tap left the light at 1% in the middle of the afternoon.

Fixed by listening on the light entity itself for any off→on report and correcting brightness to match the current window regardless of cause, with a 1% tolerance to avoid fighting rounding and a no-op when `automation_disabled`/`motion_blocker` are active so it doesn't undo an intentional manual override.

**Follow-up (found via a real 3-way circuit):** this listener didn't check `is_dark_enough()`. A companion switch wired for 3-way operation on the same circuit can toggle the light on without going through this device's own Central Scene events at all — so a broad-daylight on from the companion switch was getting pushed straight to full brightness, even though the sun-elevation gate correctly blocks the *motion*-triggered path in the same conditions. Root-caused by reconstructing the sequence from the recorder DB and the Z-Wave JS log: no Central Scene event, no manual-override toggle, but a `light.turn_on` service call with the exact `brightness_pct`/`transition` signature this listener produces — timed right after a live sun-elevation check confirmed daytime (~28°, well past the 3° threshold). Added the same `is_dark_enough()` gate here that the motion path already had, so a daytime on from anything outside Luminary is left as-is instead of being forced bright.

## Dead-sensor detection via last_seen, not sensor state or "unavailable"

A sensor's own binary on/off state can't tell you the sensor is dead — a node that stops communicating simply holds its last reported value. Real-hardware analysis (three weeks of house-wide motion capture) found a sensor held "on" for over four days after its battery died, and Z-Wave JS never marked it "unavailable" during that whole window — so watching for `unavailable` (the existing sensor-unavailable alert) is not a sufficient failure signal on its own.

The reliable signal turned out to be the sensor's own **last communicated** diagnostic entity (Z-Wave JS's "Last Seen"; a Zigbee2MQTT equivalent is supported by the same naming-convention lookup but unconfirmed against real hardware). Because staleness is an absence of updates, it can't be caught by a state-change listener — something has to poll the clock, so this runs on a 5-minute interval rather than event-driven like the rest of the coordinator.

One caveat found in production: this "Last Seen" entity is sometimes disabled by default by the owning integration, same as the Configuration CC entities behind hardware-timeout detection — Luminary deliberately doesn't auto-enable it (would need to reload the underlying integration just to activate one entity), so detection silently no-ops for a sensor until the user enables it once.

## Multi-room usage

Each room instance requires a unique `ZONE` prefix. All entity IDs, automation IDs, and script IDs are namespaced under this prefix, so multiple instances coexist without conflict. Use `scripts/setup.sh` to generate room-specific files.

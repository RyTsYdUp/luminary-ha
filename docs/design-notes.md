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
- Scene 002 (down paddle): single tap = return to auto, double tap = re-enable automation
- Scene 003 (config/middle): single tap = panic reset

On 2-scene switches, scene 003 automation can be removed or repurposed.

## Multi-room usage

Each room instance requires a unique `ZONE` prefix. All entity IDs, automation IDs, and script IDs are namespaced under this prefix, so multiple instances coexist without conflict. Use `scripts/setup.sh` to generate room-specific files.

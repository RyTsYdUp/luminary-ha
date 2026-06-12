# luminary-ha

A configurable motion-activated lighting package for Home Assistant, designed for multi-sensor zones. Features a live dashboard control panel, day/night brightness profiles, sun-elevation or lux-based daytime suppression, stuck-sensor protection, and full Z-Wave switch integration including single-tap, double-tap, and scene-3 actions.

## Features

- **Multi-sensor support** — watches up to 3 motion sensors; all must clear before lights turn off
- **Stuck-sensor protection** — configurable safety timeout turns off lights if sensors never report clear
- **Day/night brightness profiles** — separate brightness levels for a configurable nightlight window
- **Daytime suppression** — choose between sun elevation or a lux sensor to prevent triggering during daylight
- **Live configuration panel** — all settings adjustable via Lovelace sliders and pickers; no YAML edits needed after setup
- **Three switch modes** via Z-Wave central scene:
  - Single tap up: manual full-bright override
  - Single tap down: return to automatic mode
  - Double tap up: disable all automation (dumb switch mode)
  - Double tap down: re-enable automation
  - Scene 3 press: panic reset — clears all overrides

## Requirements

- Home Assistant 2024.1+
- Z-Wave JS integration
- A Z-Wave switch with central scene support (3 scenes recommended)
- 1–3 motion sensors (binary sensors)
- A dimmable light entity

## Installation

### 1. Copy files

```
packages/
  ZONE/
    package.yaml
```

Add to `configuration.yaml`:

```yaml
homeassistant:
  packages:
    ZONE: !include packages/ZONE/package.yaml
```

### 2. Replace placeholders

Open `package.yaml` and replace all placeholder strings:

| Placeholder | Replace with | Example |
|---|---|---|
| `ZONE` | Entity prefix (lowercase, underscores) | `hallway` |
| `ROOM_NAME` | Friendly display name | `Hallway` |
| `SWITCH_DEVICE` | Z-Wave switch device_id | `34d2d48bfa169a03ee8969bc8f3be804` |
| `LIGHT_ENTITY` | Light entity_id | `light.hallway_lights` |
| `SENSOR_1` | First motion sensor entity_id | `binary_sensor.hallway_motion_1_motion_detection` |
| `SENSOR_2` | Second motion sensor entity_id | `binary_sensor.hallway_motion_2_motion_detection` |
| `SENSOR_3` | Third motion sensor entity_id | `binary_sensor.hallway_motion_3_motion_detection` |

A helper script is provided — see [Setup Script](#setup-script) below.

### 3. Find your switch device_id

In Home Assistant, go to **Settings → Devices & Services → Z-Wave JS**, find your switch, and copy the device ID from the URL bar (`/config/devices/device/XXXXXXXX`).

### 4. Reload packages

```
Developer Tools → YAML → All YAML configuration → Check Configuration → Restart
```

Or for a faster reload: **Developer Tools → YAML → Reload: Automations** and **Reload: Scripts** and **Reload: Input helpers**.

### 5. Add the dashboard card

Open your dashboard in edit mode, add a new card, choose **Manual**, and paste the contents of `lovelace_card.yaml`. Replace `ZONE` and `ROOM_NAME` with your values.

### 6. Configure

Open the dashboard card and set:
- **Daytime Suppression Mode**: Sun Elevation (recommended) or Lux Sensor
- **Sun Elevation Threshold**: Start with `3`. Increase to activate earlier before sunset, decrease to activate only after full dark.
- **Nightlight Window**: Start/end times for your dim brightness profile (e.g. 00:00–06:00)
- **Nightlight Brightness**: Low value for bathroom trips (5–15% recommended)
- **Normal Brightness**: Your preferred daytime/evening brightness
- **Stuck Sensor Timeout**: How many minutes before the safety shutdown fires (2–5 min recommended)

---

## Setup Script

A bash script is provided to generate a configured `package.yaml` interactively:

```bash
cd scripts
bash setup.sh
```

The script prompts for all placeholder values and outputs a ready-to-use `package.yaml`.

---

## Multiple Rooms

Install a separate copy of the package for each room, with a unique `ZONE` prefix per room.

```
packages/
  hallway/
    package.yaml
  bedroom/
    package.yaml
  staircase/
    package.yaml
```

Each room gets its own set of helpers, automations, and dashboard card.

---

## Switch Behavior Reference

| Action | When automation enabled | When dumb mode active |
|---|---|---|
| Single tap up | Full bright + manual override on | Turn light on |
| Single tap down | Return to auto (dim/normal by time) | Turn light off |
| Double tap up | Enable dumb mode, lights off | — (already in dumb mode) |
| Double tap down | — (already enabled) | Re-enable automation |
| Scene 3 press | Reset all overrides, lights off | Reset all overrides |

---

## Status Indicator States

| State | Meaning |
|---|---|
| **Automated** | Motion will trigger lights (dark enough per suppression setting) |
| **Standby** | Automation enabled but won't trigger (too bright — sun/lux condition not met) |
| **Manual Override** | Single-tap-up mode; lights on at full bright, auto-off disabled |
| **Disabled** | Dumb switch mode; automation fully off, switch operates lights directly |

---

## Sensor Timeout Note

This package assumes each motion sensor has its own built-in re-trigger timeout configured at the device level (Z-Wave parameter 13 on Zooz ZSE11, for example). The package does **not** add an additional wait on top of this — lights turn off as soon as all sensors report clear. The **Stuck Sensor Timeout** only fires if sensors fail to report clear within that window.

---

## Dim Window and Midnight Crossing

The dim window correctly handles windows that span midnight (e.g., 23:00–06:00). The template uses a midnight-safe comparison:

```
if start < end:   active when start ≤ now < end
if start > end:   active when now ≥ start OR now < end
```

---

## License

MIT

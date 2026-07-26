# luminary-ha — Logic Flow Diagrams

These diagrams document the automation logic in `custom_components/luminary_ha/coordinator.py`. Useful for contributors, troubleshooters, and anyone adapting the integration for a new sensor platform.

> Historical note: the project started as a YAML-only `package.yaml` (see `docs/design-notes.md`), and these diagrams described that version through 2026-06-14. The project is now a full Python custom integration — `mode: restart` automations became a cancellable `asyncio.Task`, `input_*` helpers became native HA entities, and three features (hardware-timeout auto-detection, window brightness enforcement, dead-sensor detection) were added afterward that had no YAML-era equivalent. Diagrams below reflect the current Python integration.

---

## 1. System Architecture

High-level view of how components interact. `ZoneCoordinator` is the hub — one instance per configured zone.

```mermaid
flowchart LR
    subgraph IN[" Inputs "]
        direction TB
        MS["Motion sensors"]
        SW["Z-Wave switch\ncentral scene events"]
        SUN["Sun elevation\nor lux sensor"]
        LT_EXT["Light\n(any off→on report)"]
        HWT["Sensor's own hardware\nclear-timeout entity"]
        LS["Sensor's own\nLast Seen entity"]
    end

    subgraph ST[" Live Config Entities "]
        direction TB
        BL["motion_blocker\nManual override"]
        DIS["automation_disabled\nDumb switch"]
        CFG["Sliders / selects / times\nbrightness · dim window ·\nlight_on_time · stale threshold"]
    end

    subgraph CO[" ZoneCoordinator "]
        direction TB
        A1["Main motion sequence"]
        A2["Switch tap handlers"]
        A3["Dim window boundaries"]
        A4["Light-change listener"]
        A5["Hardware-timeout floor"]
        A6["Stale-sensor poll (5 min)"]
    end

    subgraph OUT[" Outputs "]
        direction TB
        LT["Light"]
        SE["Automation Status sensor"]
        DG["Diagnostic entities"]
        NT["persistent_notification"]
    end

    MS --> A1
    SUN --> A1
    SW --> A2
    LT_EXT --> A4
    HWT --> A5
    LS --> A6
    CFG --> A1
    CFG --> A4
    CFG --> A5
    CFG --> A6
    BL --> A1
    DIS --> A1
    A2 --> BL
    A2 --> DIS
    A1 --> LT
    A2 --> LT
    A3 --> LT
    A4 --> LT
    A5 --> DG
    A5 --> NT
    A6 --> DG
    A6 --> NT
    BL --> SE
    DIS --> SE
    CFG --> SE
```

---

## 2. Main Motion Automation

The core loop. A fresh trigger from any sensor cancels and replaces any in-progress sequence — the `asyncio.Task` equivalent of the old YAML `mode: restart`, so a new trigger resets the wait clock for free.

```mermaid
flowchart TD
    TR(["Any motion sensor goes ON\nrestarts this flow — a fresh trigger\ncancels & replaces any sequence in progress"])

    TR --> C1{"automation_disabled = on?"}
    C1 -- Yes --> STOP1(["Stop"])
    C1 -- No --> C2{"motion_blocker = on?"}
    C2 -- Yes --> STOP1
    C2 -- No --> DM{"Daytime suppression"}

    DM -- "Sun Elevation" --> CE{"Sun elevation\nbelow threshold?"}
    DM -- "Lux Sensor" --> CL{"Lux below threshold\nor sensor unavailable?"}
    DM -- "Disabled" --> DW

    CE -- "No — too bright" --> STOP2(["Stop — daytime suppression"])
    CE -- "Yes — dark enough" --> DW
    CL -- "No — too bright" --> STOP2
    CL -- "Yes or unavailable" --> DW

    DW{"In dim window?"}
    DW -- Yes --> D1["Turn on at\ndim_brightness %"]
    DW -- No --> D2["Turn on at\nnormal_brightness %"]

    D1 --> WT["Wait for ALL sensors OFF\n— 30 min hardcoded safety cap —"]
    D2 --> WT

    WT --> ST{"Sensors cleared\nbefore the cap?"}
    ST -- Yes --> DELAY["Sleep light_on_time_sec\n(floor = slowest sensor's own\nhardware clear-timeout — see §6)"]
    ST -- "No — stuck" --> RC

    DELAY --> RC{"automation_disabled or\nmotion_blocker became on\nduring the wait?"}
    RC -- Yes --> HOLD(["Stop — keep light on"])
    RC -- No --> OFF["Turn off light"]

    OFF --> STK{"A sensor still\nreports ON?"}
    STK -- Yes --> REFRESH["zwave_js.refresh_value\n— nudge a fresh state report"]
    STK -- No --> DONE(["Done"])
    REFRESH --> DONE
```

**Note the two separate timers:** the 30-minute wait cap is a hardcoded stuck-sensor safety net (`_stuck_timeout`), unrelated to the user-configurable `light_on_time_sec` post-clear delay. Conflating them was the original YAML design's approach; splitting them is what let `light_on_time_sec` get a hardware-derived floor without touching stuck-sensor recovery.

---

## 3. Switch Actions

Single-tap actions branch on dumb mode so the switch keeps working as a plain on/off switch even when automation is fully disabled.

```mermaid
flowchart TD
    EV(["Z-Wave central scene event\nCommand Class 91"])

    EV --> S1["Scene 001 — up paddle"]
    EV --> S2["Scene 002 — down paddle"]
    EV --> S3["Scene 003 — config button"]

    S1 --> V1{"KeyPressed\nor KeyPressed2x?"}
    V1 -- "KeyPressed — single tap" --> V1A{"automation\ndisabled?"}
    V1A -- "No — smart mode" --> V1B["Set motion_blocker ON\n(blocking, before the light call —\ncloses a race with the light-change listener, §5)"]
    V1B --> V1E["Light 100%"]
    V1A -- "Yes — dumb mode" --> V1E
    V1 -- "KeyPressed2x — double tap" --> V1D["automation_disabled ON\nmotion_blocker OFF\nenter dumb mode\n— light state preserved, not forced off"]

    S2 --> V2{"KeyPressed\nor KeyPressed2x?"}
    V2 -- "KeyPressed — single tap" --> V2A{"automation\ndisabled?"}
    V2A -- "No — smart mode" --> V2B["motion_blocker OFF"]
    V2B --> V2C{"Any sensor ON?"}
    V2C -- Yes --> V2D["Restart motion sequence\n(time-appropriate brightness)"]
    V2C -- No --> V2E["Light OFF"]
    V2A -- "Yes — dumb mode" --> V2F["Light OFF\nplain off"]
    V2 -- "KeyPressed2x — double tap" --> V2G["automation_disabled OFF\nmotion_blocker OFF\nexit dumb mode"]
    V2G --> V2H{"Any sensor ON?"}
    V2H -- Yes --> V2I["Restart motion sequence\n(time-appropriate brightness)"]
    V2H -- No --> V2J["Light OFF"]

    S3 --> V3["automation_disabled OFF\nmotion_blocker OFF\nLight OFF\npanic reset"]
```

---

## 4. Status Sensor State Machine

`sensor.<zone>_automation_status` reflects which of four states the zone is in.
Priority: **Disabled > Manual Override > Automated / Standby**.

```mermaid
stateDiagram-v2
    [*] --> Automated

    Automated: Automated
    Automated: Motion triggers lights at\ntime-appropriate brightness

    Standby: Standby
    Standby: Enabled but suppressed\nby sun or lux condition

    Override: Manual Override
    Override: Light held at full brightness\nMotion will not auto-off

    Disabled: Disabled
    Disabled: No automation active\nSwitch acts as dumb light

    Automated --> Standby: Daytime condition met\nsun up or lux too high
    Standby --> Automated: Daytime condition cleared

    Automated --> Override: Single tap UP
    Standby --> Override: Single tap UP

    Override --> Automated: Single tap DOWN\ndark enough
    Override --> Standby: Single tap DOWN\ntoo bright

    Automated --> Disabled: Double tap UP
    Standby --> Disabled: Double tap UP
    Override --> Disabled: Double tap UP

    Disabled --> Automated: Double tap DOWN or Scene 3\ndark enough
    Disabled --> Standby: Double tap DOWN or Scene 3\ntoo bright
```

---

## 5. Window Brightness Enforcement

Added 2026-07-21 after a real incident: a physical paddle tap with no Central Scene report restored a Z-Wave dimmer's stale remembered brightness (a leftover nightlight-window level) at 4pm — completely invisible to the automation, since brightness was previously only ever *applied* through Luminary's own code paths. This listener watches the light entity directly, independent of what caused the change.

```mermaid
flowchart TD
    LC(["Light reports OFF → ON\nany cause: physical tap, another\nintegration, or Luminary itself"])

    LC --> C1{"new_state is on?"}
    C1 -- No --> STOP(["Ignore — not a turn-on"])
    C1 -- Yes --> C2{"automation_disabled or\nmotion_blocker active?"}
    C2 -- Yes --> STOP2(["Ignore — don't fight\nan intentional override"])
    C2 -- No --> T["target = dim_brightness if in window\nelse normal_brightness — see §8"]

    T --> M{"Reported brightness\nwithin 1% of target?"}
    M -- Yes --> NOOP(["No-op — already correct,\nprevents a self-trigger loop"])
    M -- No --> COR["Corrective light.turn_on\nat target brightness"]
```

---

## 6. Hardware-Timeout Auto-Detection & Dynamic Floor

Each motion sensor has its own onboard "how long to wait after last trigger before reporting clear" timer. `light_on_time_sec` must exceed the slowest configured sensor's timeout or lights flicker. Luminary auto-detects each sensor's value and enforces it as a live floor — never a cached snapshot, since the owning integration (Z-Wave JS / Zigbee2MQTT) is the source of truth for its own device parameters.

```mermaid
flowchart TD
    SETUP(["Zone setup / options flow"])
    SETUP --> DET["Auto-detect each sensor's onboard\nclear-timeout — Z-Wave Configuration CC\nparameter, or Z2M occupancy_timeout"]
    DET --> OK{"Detected\nsuccessfully?"}
    OK -- Yes --> CONFIRM["User confirms\nor overrides the value"]
    OK -- "No — often because the\nConfiguration CC entity is\ndisabled by default" --> MANUAL["User enters value manually,\nor enables the entity and retries"]
    CONFIRM --> STORE["Stored: source_entity_id\nread live, never cached"]
    MANUAL --> STORE

    STORE --> LIVE["Coordinator watches\nsource_entity_id for changes"]
    LIVE --> FLOOR{"max(all sensors' hw-timeout)\n> current light_on_time_sec?"}
    FLOOR -- No --> IDLE(["No action"])
    FLOOR -- Yes --> BUMP["Raise light_on_time_sec\nto the new floor"]
    BUMP --> NOTIFY["persistent_notification:\nwhich sensor, old vs new value"]
```

---

## 7. Dead-Sensor Detection (last_seen staleness)

Added 2026-07-25 after analyzing three weeks of house-wide motion-capture data: a sensor with a dying battery held its binary_sensor "on" for **4 days 3 hours**, and Z-Wave JS never once marked it "unavailable" during that window — so an on/off state or an "unavailable" watch can't detect this failure mode. The sensor's own **Last Seen** diagnostic entity, which simply stops advancing the moment the node goes silent, can.

```mermaid
flowchart TD
    SETUP(["Zone startup"])
    SETUP --> DISC["Discover each sensor's own\nLast Seen diagnostic entity"]
    DISC --> FOUND{"Found\nand enabled?"}
    FOUND -- No --> SKIP(["Detection unavailable for this sensor\n— silent no-op, same failure mode\nas an undetected hardware timeout"])
    FOUND -- Yes --> POLL

    POLL(["Every 5 minutes:\ncheck each sensor"]) --> AGE{"Time since last_seen\n> Stale Sensor Alert Threshold?"}
    AGE -- No --> CLEAR{"Was previously\nflagged stale?"}
    CLEAR -- Yes --> DISMISS["Dismiss notification"]
    CLEAR -- No --> NOOP(["No-op"])
    AGE -- Yes --> ALREADY{"Already\nnotified?"}
    ALREADY -- Yes --> NOOP2(["No-op — don't re-notify"])
    ALREADY -- No --> NOTIFY["persistent_notification:\nsensor may be dead"]
```

**Why polling, not a listener:** staleness is an *absence* of updates, which a state-change listener structurally cannot observe — something has to actively check the clock. This is the one part of the coordinator that isn't purely event-driven.

**Known gap, confirmed in production:** the Last Seen entity is sometimes disabled by default by the owning integration, same as the Configuration CC entities in §6 — Luminary deliberately doesn't auto-enable it (would require reloading the underlying integration just to activate one entity), so detection silently no-ops until the user enables it once.

---

## 8. Dim Window: Midnight-Crossing Logic

The nightlight window may span midnight (e.g. start `23:00`, end `06:00`). A naive `start ≤ now < end` comparison breaks when start > end because the times wrap around. `target_brightness()` (used by the main motion sequence, the dim-window boundary handlers, and the brightness-enforcement listener in §5) handles this with a two-branch check:

```mermaid
flowchart TD
    A(["Is now inside the dim window?"])
    A --> B{"start ≤ end?\nsame-day window"}
    B -- "Yes\ne.g. 01:00 to 05:00" --> C["True if: start ≤ t < end"]
    B -- "No — spans midnight\ne.g. 23:00 to 06:00" --> D["True if: t >= start OR t < end"]
    C --> E(["Return true or false"])
    D --> E
```

**Example:**
- Start `23:00`, End `06:00`, current time `02:30`:
  - `start > end` → midnight-crossing branch
  - `02:30 >= 23:00`? No. `02:30 < 06:00`? **Yes** → dim window is active ✓
- Same config, current time `14:00`:
  - `14:00 >= 23:00`? No. `14:00 < 06:00`? No → not in dim window ✓

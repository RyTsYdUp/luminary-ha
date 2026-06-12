# luminary-ha — Logic Flow Diagrams

These diagrams document the automation logic in `package.yaml`. Useful for contributors, troubleshooters, and anyone adapting the package for a new room.

---

## 1. System Architecture

High-level view of how components interact.

```mermaid
flowchart LR
    subgraph IN[" Inputs "]
        direction TB
        MS["Motion sensors\nSENSOR_1 / 2 / 3"]
        SW["Z-Wave switch\nscene events"]
        SUN["Sun elevation\nor lux sensor"]
        TI["System time"]
    end

    subgraph ST[" State — Helpers "]
        direction TB
        BL["motion_blocker\nManual override"]
        DIS["automation_disabled\nDumb switch"]
        CFG["Config sliders\ndim window · brightness\ntimeout · mode"]
    end

    subgraph AU[" Automations "]
        direction TB
        A1["Main motion\nmode: restart"]
        A2["Switch actions\n5 events"]
        A3["Dim window\nboundaries"]
    end

    subgraph OUT[" Outputs "]
        direction TB
        LT["Light"]
        SE["Status sensor"]
    end

    MS --> A1
    SUN --> A1
    TI --> A3
    SW --> A2
    CFG --> A1
    BL --> A1
    DIS --> A1
    A2 --> BL
    A2 --> DIS
    A1 --> LT
    A2 --> LT
    A3 --> LT
    BL --> SE
    DIS --> SE
    CFG --> SE
```

---

## 2. Main Motion Automation

The core loop. Runs in `mode: restart` — any new motion trigger while the automation is running restarts from the top, which also resets the stuck-sensor timeout clock automatically.

```mermaid
flowchart TD
    TR(["Any motion sensor goes ON\nmode: restart — new trigger resets this flow"])

    TR --> C1{"automation_disabled = on?"}
    C1 -- Yes --> STOP1(["Stop"])
    C1 -- No --> C2{"motion_blocker = on?"}
    C2 -- Yes --> STOP1
    C2 -- No --> DM{"Daytime suppression mode"}

    DM -- "Sun Elevation" --> CE{"Sun elevation\nbelow threshold?"}
    DM -- "Lux Sensor" --> CL{"Lux below threshold\nor sensor unavailable?"}

    CE -- "No — too bright" --> STOP2(["Stop — daytime suppression"])
    CE -- "Yes — dark enough" --> DW
    CL -- "No — too bright" --> STOP2
    CL -- "Yes or unavailable" --> DW

    DW{"In dim window?"}
    DW -- Yes --> D1["Turn on at\ndim_brightness %"]
    DW -- No --> D2["Turn on at\nnormal_brightness %"]

    D1 --> WT["Wait:\nall 3 sensors OFF\n— or —\nmotion_timeout expires\nstuck-sensor safety cap"]
    D2 --> WT

    WT --> RC{"automation_disabled or\nmotion_blocker became on\nduring the wait?"}
    RC -- Yes --> HOLD(["Stop — keep light on"])
    RC -- No --> OFF(["Turn off light"])
```

---

## 3. Switch Actions

Single-tap actions branch on dumb mode so the switch keeps working as a plain on/off switch even when automation is fully disabled.

```mermaid
flowchart TD
    EV(["Z-Wave central scene event"])

    EV --> S1["Scene 001 — up paddle"]
    EV --> S2["Scene 002 — down paddle"]
    EV --> S3["Scene 003 — config button"]

    S1 --> V1{"value?"}
    V1 -- "0  single tap" --> V1A{"automation\ndisabled?"}
    V1A -- "No — smart mode" --> V1B["Light 100%\nSet motion_blocker ON"]
    V1A -- "Yes — dumb mode" --> V1C["Light 100%\nplain on"]
    V1 -- "3  double tap" --> V1D["automation_disabled ON\nmotion_blocker OFF\nLight OFF\nenter dumb mode"]

    S2 --> V2{"value?"}
    V2 -- "0  single tap" --> V2A{"automation\ndisabled?"}
    V2A -- "No — smart mode" --> V2B["motion_blocker OFF"]
    V2B --> V2C{"Any sensor ON?"}
    V2C -- Yes --> V2D["Set time-appropriate brightness"]
    V2C -- No --> V2E["Light OFF"]
    V2A -- "Yes — dumb mode" --> V2F["Light OFF\nplain off"]
    V2 -- "3  double tap" --> V2G["automation_disabled OFF\nexit dumb mode"]
    V2G --> V2H{"Any sensor ON?"}
    V2H -- Yes --> V2I["Set time-appropriate brightness"]
    V2H -- No --> V2J["Light OFF"]

    S3 --> V3["automation_disabled OFF\nmotion_blocker OFF\nLight OFF\npanic reset"]
```

---

## 4. Status Sensor State Machine

`sensor.ZONE_automation_status` reflects which of four states the zone is in.
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

## 5. Dim Window: Midnight-Crossing Logic

The nightlight window may span midnight (e.g. start `23:00`, end `06:00`). A naive `start ≤ now < end` comparison breaks when start > end because the times wrap around. luminary-ha handles this with a two-branch check used in both the `set_brightness` script and the `dim_window_start/end` automations:

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

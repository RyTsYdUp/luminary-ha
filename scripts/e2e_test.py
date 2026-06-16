#!/usr/bin/env python3
"""Automated E2E tests for luminary-ha on the test HA instance."""
import json, time, urllib.request, urllib.error, os, sys

BASE = "http://supervisor/core/api"
TOKEN = os.environ["SUPERVISOR_TOKEN"]
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def api(method, path, data=None):
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(
        f"{BASE}/{path}", data=body, headers=HEADERS, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": e.code, "msg": e.read().decode()}


def call(domain, service, **kwargs):
    return api("POST", f"services/{domain}/{service}", kwargs)


def state(entity_id):
    r = api("GET", f"states/{entity_id}")
    return r.get("state", "?")


def wait_for_state(entity_id, expected, timeout, poll=2):
    """Poll until entity reaches expected state or timeout. Returns (ok, elapsed)."""
    start = time.time()
    while True:
        s = state(entity_id)
        elapsed = time.time() - start
        if s == expected:
            return True, elapsed
        if elapsed >= timeout:
            print(f"  [poll] {entity_id}={s!r} at {elapsed:.0f}s (want {expected!r}, gave up)")
            return False, elapsed
        time.sleep(poll)



def check(label, entity_id, expected):
    s = state(entity_id)
    ok = s == expected
    print(f"  {'OK' if ok else 'FAIL'} {label}: {entity_id} = {s!r} (want {expected!r})")
    return ok


LIGHT    = "light.test_hallway_light"
DISABLED = "switch.hallway_hallway_light_automation_automation_disabled"
BLOCKER  = "switch.hallway_hallway_light_automation_manual_override"
DAY_DET  = "switch.hallway_hallway_light_automation_daytime_detection"
LOT      = "number.hallway_hallway_light_automation_light_on_time"
M1 = "input_boolean.test_motion_1"
M2 = "input_boolean.test_motion_2"


def reset():
    call("input_boolean", "turn_off", entity_id=M1)
    call("input_boolean", "turn_off", entity_id=M2)
    call("switch", "turn_off", entity_id=DISABLED)
    call("switch", "turn_off", entity_id=BLOCKER)
    call("switch", "turn_off", entity_id=DAY_DET)
    call("light",  "turn_off", entity_id=LIGHT)
    time.sleep(1)


results = []

# Set short post-motion delay for testing (entity min may clamp it)
call("number", "set_value", entity_id=LOT, value=3)
time.sleep(0.5)
LOT_SEC = float(state(LOT))
WAIT = LOT_SEC + 3  # buffer
print(f"\nlight_on_time_sec = {LOT_SEC}s (will wait {WAIT}s after motion clears)\n")


# ── T1: Basic motion trigger ──────────────────────────────────────────────────
print("=== T1: Basic motion -> light on, clear + delay -> light off ===")
reset()
call("input_boolean", "turn_on", entity_id=M1)
time.sleep(1)
r1a = check("light on after motion", LIGHT, "on")
call("input_boolean", "turn_off", entity_id=M1)
r1b, _ = wait_for_state(LIGHT, "off", timeout=WAIT)
print(f"  {'OK' if r1b else 'FAIL'} light off after delay: {state(LIGHT)!r} (want 'off')")
results.append(("T1 basic motion", r1a and r1b))


# ── T2: Two sensors ───────────────────────────────────────────────────────────
print("\n=== T2: Two sensors — stays on while any active ===")
reset()
call("input_boolean", "turn_on", entity_id=M1)
call("input_boolean", "turn_on", entity_id=M2)
time.sleep(0.5)
call("input_boolean", "turn_off", entity_id=M1)
time.sleep(1)
r2a = check("light still on (M2 active)", LIGHT, "on")
call("input_boolean", "turn_off", entity_id=M2)
r2b, t2 = wait_for_state(LIGHT, "off", timeout=WAIT)
print(f"  {'OK' if r2b else 'FAIL'} light off after both clear + delay: {state(LIGHT)!r} at {t2:.0f}s")
results.append(("T2 two sensors", r2a and r2b))


# ── T3: Single tap UP ────────────────────────────────────────────────────────
print("\n=== T3: Single tap UP -> override on, light on ===")
reset()
call("input_button", "press", entity_id="input_button.test_switch_single_up")
time.sleep(1)
r3a = check("light on", LIGHT, "on")
r3b = check("motion_blocker on", BLOCKER, "on")
results.append(("T3 single tap up", r3a and r3b))


# ── T4: Single tap DOWN with no motion ───────────────────────────────────────
print("\n=== T4: Single tap DOWN (no motion) -> override off, light off ===")
# blocker is on, light is on, no motion — carries from T3
call("input_button", "press", entity_id="input_button.test_switch_single_down")
time.sleep(1)
r4a = check("motion_blocker off", BLOCKER, "off")
r4b = check("light off (no motion)", LIGHT, "off")
results.append(("T4 single tap down no motion", r4a and r4b))


# ── T5: Single tap DOWN with active motion ───────────────────────────────────
print("\n=== T5: Single tap DOWN (motion active) -> override off, light stays on ===")
reset()
call("input_boolean", "turn_on", entity_id=M1)
time.sleep(0.5)
call("input_button", "press", entity_id="input_button.test_switch_single_up")
time.sleep(0.5)
call("input_button", "press", entity_id="input_button.test_switch_single_down")
time.sleep(1)
r5a = check("motion_blocker off", BLOCKER, "off")
r5b = check("light still on (motion active)", LIGHT, "on")
results.append(("T5 single tap down motion active", r5a and r5b))


# ── T6: Double tap UP (dumb mode) ────────────────────────────────────────────
print("\n=== T6: Double tap UP -> dumb mode, motion ignored ===")
reset()
call("input_button", "press", entity_id="input_button.test_switch_double_up")
time.sleep(0.5)
r6a = check("automation_disabled on", DISABLED, "on")
r6b = check("motion_blocker off", BLOCKER, "off")
call("input_boolean", "turn_on", entity_id=M1)
time.sleep(1)
r6c = check("light stays off (dumb mode)", LIGHT, "off")
results.append(("T6 double tap up", r6a and r6b and r6c))


# ── T7: Double tap DOWN (resume, motion active) ───────────────────────────────
print("\n=== T7: Double tap DOWN (motion active) -> resume, light on ===")
# dumb mode on, M1 on, light off — carries from T6
call("input_button", "press", entity_id="input_button.test_switch_double_down")
time.sleep(1)
r7a = check("automation_disabled off", DISABLED, "off")
r7b = check("light on (motion resumed)", LIGHT, "on")
call("input_boolean", "turn_off", entity_id=M1)
r7c, t7 = wait_for_state(LIGHT, "off", timeout=WAIT)
print(f"  {'OK' if r7c else 'FAIL'} light off after delay: {state(LIGHT)!r} at {t7:.0f}s")
results.append(("T7 double tap down", r7a and r7b and r7c))


# ── T8: Scene 3 panic reset ───────────────────────────────────────────────────
print("\n=== T8: Scene 3 -> clear all overrides, light off ===")
reset()
call("switch", "turn_on", entity_id=DISABLED)
call("switch", "turn_on", entity_id=BLOCKER)
call("light",  "turn_on", entity_id=LIGHT)
time.sleep(0.5)
call("input_button", "press", entity_id="input_button.test_switch_scene3")
time.sleep(1)
r8a = check("automation_disabled off", DISABLED, "off")
r8b = check("motion_blocker off", BLOCKER, "off")
r8c = check("light off", LIGHT, "off")
results.append(("T8 scene 3 reset", r8a and r8b and r8c))


# ── T9: Daytime suppression ───────────────────────────────────────────────────
print("\n=== T9: Daytime detection on -> motion suppressed ===")
reset()
call("switch", "turn_on", entity_id=DAY_DET)
time.sleep(0.5)
call("input_boolean", "turn_on", entity_id=M1)
time.sleep(1)
r9a = check("light stays off (sun above threshold)", LIGHT, "off")
call("switch", "turn_off", entity_id=DAY_DET)
call("input_boolean", "turn_off", entity_id=M1)
results.append(("T9 daytime suppression", r9a))


# ── T10: Motion restart ───────────────────────────────────────────────────────
print("\n=== T10: Motion restart cancels in-progress delay ===")
reset()
call("input_boolean", "turn_on", entity_id=M1)
time.sleep(0.5)
call("input_boolean", "turn_off", entity_id=M1)  # starts 3s countdown
time.sleep(1)                                      # 1s into countdown
call("input_boolean", "turn_on", entity_id=M1)    # restart
time.sleep(0.5)
r10a = check("light still on after restart", LIGHT, "on")
call("input_boolean", "turn_off", entity_id=M1)
r10b, t10 = wait_for_state(LIGHT, "off", timeout=WAIT)
print(f"  {'OK' if r10b else 'FAIL'} light off after final delay: {state(LIGHT)!r} at {t10:.0f}s")
results.append(("T10 motion restart", r10a and r10b))


# ── Restore ───────────────────────────────────────────────────────────────────
call("number", "set_value", entity_id=LOT, value=60)
reset()

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 50)
print("RESULTS")
print("=" * 50)
passed = sum(1 for _, ok in results if ok)
for name, ok in results:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)

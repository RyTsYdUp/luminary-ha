"""Inject a CONF_SENSOR_HW_TIMEOUTS entry into the test HA's Hallway zone config entry,
for exercising the T15/T16 floor-enforcement + live-update scenarios in e2e_test.py.

Bypasses the config/options flow (which needs a browser) by editing storage directly,
same technique as reset_test_zone.py. Points binary_sensor.test_hallway_motion_1's
hardware timeout at input_number.test_hw_timeout_source (see config/test_entities.yaml)
so E2E tests can drive the "live source change" scenario with a plain number entity.

Run on the test HA instance itself (scp this file over, then `python3` it), then
`ha core restart` to load the change — options edited via storage aren't picked up
until the config entry reloads.
"""
import json

PATH = "/config/.storage/core.config_entries"
with open(PATH) as f:
    d = json.load(f)

entry = next(e for e in d["data"]["entries"] if e["domain"] == "luminary_ha" and e["title"] == "Hallway")
entry["options"]["sensor_hw_timeouts"] = {
    "binary_sensor.test_hallway_motion_1": {
        "timeout_sec": None,
        "source": "zwave",
        "source_entity_id": "input_number.test_hw_timeout_source",
    },
}

with open(PATH, "w") as f:
    json.dump(d, f, indent=2)

print("Injected sensor_hw_timeouts:", entry["options"]["sensor_hw_timeouts"])

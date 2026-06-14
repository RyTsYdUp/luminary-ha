import json

with open("/config/.storage/core.config_entries") as f:
    d = json.load(f)

before = len(d["data"]["entries"])
d["data"]["entries"] = [
    e for e in d["data"]["entries"]
    if not (e["domain"] == "luminary_ha" and e["title"] == "Hallway")
]
print("Config entries:", before, "->", len(d["data"]["entries"]))

with open("/config/.storage/core.config_entries", "w") as f:
    json.dump(d, f, indent=2)

with open("/config/.storage/core.entity_registry") as f:
    er = json.load(f)

before = len(er["data"]["entities"])
er["data"]["entities"] = [
    e for e in er["data"]["entities"]
    if e.get("platform") != "luminary_ha"
]
print("Entity registry:", before, "->", len(er["data"]["entities"]))

with open("/config/.storage/core.entity_registry", "w") as f:
    json.dump(er, f, indent=2)

print("Done")

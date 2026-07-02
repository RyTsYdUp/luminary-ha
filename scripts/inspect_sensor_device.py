#!/usr/bin/env python3
"""Dump a motion sensor's owning device and all sibling entities via the HA WebSocket API.

Throwaway diagnostic spike for luminary-ha's hardware-timeout auto-detection feature.
Used to empirically confirm, against real hardware, how a sensor's onboard
clear-timeout parameter is actually exposed:
  - Z-Wave JS: which sibling entity is the Configuration CC param, its real
    unique_id shape, and whether it's disabled by default.
  - Zigbee2MQTT: the real platform value and expose/entity naming for the
    device's occupancy/motion timeout parameter.

Usage:
    python scripts/inspect_sensor_device.py binary_sensor.pantry_motion_motion_detection
    python scripts/inspect_sensor_device.py binary_sensor.<some_zigbee2mqtt_motion_sensor>
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

try:
    import websockets
except ImportError:
    print("Missing dependency: pip install websockets")
    sys.exit(1)

CREDENTIALS_PATH = Path.home() / ".claude" / "homelab_credentials.json"


def load_token() -> str:
    with open(CREDENTIALS_PATH, encoding="utf-8") as f:
        creds = json.load(f)
    return creds["home_assistant"]["token"]


async def ws_command(ws, msg_id: int, payload: dict) -> dict:
    await ws.send(json.dumps({"id": msg_id, **payload}))
    while True:
        raw = await ws.recv()
        msg = json.loads(raw)
        if msg.get("id") == msg_id:
            if not msg.get("success", True):
                raise RuntimeError(f"Command failed: {msg}")
            return msg.get("result")


async def inspect(host: str, port: int, entity_id: str) -> None:
    url = f"ws://{host}:{port}/api/websocket"
    token = load_token()
    print(f"Connecting to {url} ...")

    async with websockets.connect(url, max_size=None) as ws:
        msg = json.loads(await ws.recv())
        assert msg["type"] == "auth_required", f"Unexpected first message: {msg}"

        await ws.send(json.dumps({"type": "auth", "access_token": token}))
        msg = json.loads(await ws.recv())
        if msg["type"] != "auth_ok":
            print(f"Auth failed: {msg}")
            return
        print("Authenticated.\n")

        next_id = 1

        def nid() -> int:
            nonlocal next_id
            next_id += 1
            return next_id - 1

        entry = await ws_command(ws, nid(), {"type": "config/entity_registry/get", "entity_id": entity_id})
        print(f"=== Entity registry entry: {entity_id} ===")
        print(json.dumps(entry, indent=2))

        device_id = entry.get("device_id")
        if not device_id:
            print("\nNo device_id on this entity (helper/template entity?) — nothing more to inspect.")
            return

        device_list = await ws_command(ws, nid(), {"type": "config/device_registry/list"})
        device = next((d for d in device_list if d.get("id") == device_id), None)
        print(f"\n=== Owning device ({device_id}) ===")
        if device:
            print(json.dumps({
                "name": device.get("name"),
                "manufacturer": device.get("manufacturer"),
                "model": device.get("model"),
                "identifiers": device.get("identifiers"),
            }, indent=2))
        else:
            print("Device not found in device registry list.")

        entity_list = await ws_command(ws, nid(), {"type": "config/entity_registry/list"})
        siblings = [e for e in entity_list if e.get("device_id") == device_id]

        states_result = await ws_command(ws, nid(), {"type": "get_states"})
        state_by_id = {s["entity_id"]: s for s in states_result}

        print(f"\n=== {len(siblings)} sibling entities on this device ===")
        for sib in sorted(siblings, key=lambda e: e.get("entity_id", "")):
            sib_eid = sib.get("entity_id")
            live = state_by_id.get(sib_eid)
            print(f"\n  entity_id:       {sib_eid}")
            print(f"  unique_id:       {sib.get('unique_id')}")
            print(f"  platform:        {sib.get('platform')}")
            print(f"  disabled_by:     {sib.get('disabled_by')}")
            print(f"  translation_key: {sib.get('translation_key')}")
            print(f"  original_name:   {sib.get('original_name')}")
            if live:
                print(f"  live state:      {live.get('state')!r}  attrs={json.dumps(live.get('attributes'))}")
            else:
                print("  live state:      (no current state — likely disabled)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect a motion sensor's device and sibling entities")
    parser.add_argument("entity_id", help="e.g. binary_sensor.pantry_motion_motion_detection")
    parser.add_argument("--host", default="192.168.20.2")
    parser.add_argument("--port", type=int, default=8123)
    args = parser.parse_args()

    try:
        asyncio.run(inspect(args.host, args.port, args.entity_id))
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()

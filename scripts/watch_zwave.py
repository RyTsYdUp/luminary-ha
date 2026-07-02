#!/usr/bin/env python3
"""Stream zwave_js_value_notification events from Home Assistant via WebSocket.

Usage:
    python scripts/watch_zwave.py [--host 192.168.20.2] [--port 8123]

Press Ctrl+C to stop.
"""
import asyncio
import json
import sys
import argparse
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

# Z-Wave Central Scene key labels (from const.py)
KEY_LABELS = {"001": "UP", "002": "DOWN", "003": "CONFIG/SCENE3"}
# Value labels confirmed from production Zooz ZSE11 (2026-06-22)
VALUE_LABELS = {
    "KeyPressed":   "single-tap",
    "KeyPressed2x": "double-tap",
    "KeyPressed3x": "triple-tap",
    "KeyHeldDown":  "held",
    "KeyReleased":  "released",
}


async def listen(host: str, port: int) -> None:
    url = f"ws://{host}:{port}/api/websocket"
    token = load_token()
    print(f"Connecting to {url} ...")

    async with websockets.connect(url) as ws:
        msg = json.loads(await ws.recv())
        assert msg["type"] == "auth_required", f"Unexpected first message: {msg}"

        await ws.send(json.dumps({"type": "auth", "access_token": token}))
        msg = json.loads(await ws.recv())
        if msg["type"] != "auth_ok":
            print(f"Auth failed: {msg}")
            return
        print("Authenticated.\n")

        await ws.send(json.dumps({
            "id": 1,
            "type": "subscribe_events",
            "event_type": "zwave_js_value_notification",
        }))
        msg = json.loads(await ws.recv())
        if not msg.get("success"):
            print(f"Subscribe failed: {msg}")
            return

        print("Listening for zwave_js_value_notification events.")
        print("Trigger your Z-Wave switch now. Press Ctrl+C to stop.\n")

        while True:
            raw = await ws.recv()
            msg = json.loads(raw)
            if msg.get("type") != "event":
                continue

            d = msg["event"]["data"]
            cc = d.get("command_class")
            key = str(d.get("property_key", ""))
            value = d.get("value")
            device = d.get("device_id", "?")

            key_label = KEY_LABELS.get(key, key)
            value_label = VALUE_LABELS.get(value, f"value={value}")

            if cc == 91:
                print(f"[Central Scene CC91]  device={device}  key={key} ({key_label})  {value_label}")
            else:
                print(f"[CC{cc}]  {json.dumps(d)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch Z-Wave events from Home Assistant")
    parser.add_argument("--host", default="192.168.20.2")
    parser.add_argument("--port", type=int, default=8123)
    args = parser.parse_args()

    try:
        asyncio.run(listen(args.host, args.port))
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Capture motion/occupancy sensor events house-wide, across both Z-Wave and
Zigbee2MQTT, for offline analysis of how differently-modeled sensors actually fire
events over time.

Subscribes to plain `state_changed` events rather than a protocol-specific event type
(like `zwave_js_value_notification`) — both Z-Wave and Zigbee2MQTT normalize into
ordinary entity states in Home Assistant, so one subscription covers both. Zigbee2MQTT
PIR sensors report `device_class: "occupancy"`, not `"motion"` (confirmed against real
hardware) — both device classes are captured.

Usage:
    python scripts/capture_motion_events.py [--host 192.168.20.2] [--port 8123]
        [--output motion_capture.jsonl] [--duration 86400]

Press Ctrl+C to stop (or let --duration elapse). Output is JSON Lines, flushed after
every event so a crash or Ctrl+C doesn't lose data from a long unattended run.

Reconnects automatically (exponential backoff, capped at 5 minutes) on any network-level
disconnect — a WiFi blip, an HA restart for something unrelated, etc. — so a multi-day
run survives transient outages instead of silently dying on the first one. Auth/subscribe
failures are treated as non-retryable (a bad token won't fix itself) and stop the script.
"""
import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import websockets
except ImportError:
    print("Missing dependency: pip install websockets")
    sys.exit(1)

CREDENTIALS_PATH = Path.home() / ".claude" / "homelab_credentials.json"
MOTION_DEVICE_CLASSES = ("motion", "occupancy")


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


def _is_motion_entity(entity_id: str, new_state: dict | None) -> bool:
    if not entity_id.startswith("binary_sensor."):
        return False
    if new_state is None:
        return False
    return new_state.get("attributes", {}).get("device_class") in MOTION_DEVICE_CLASSES


class AuthOrSubscribeError(Exception):
    """Non-retryable — a bad token or subscribe rejection won't fix itself on retry."""


async def _capture_session(url: str, token: str, out, platform_cache: dict, deadline: float | None) -> bool:
    """Run one WS connection's worth of capture. Returns True if `deadline` was
    reached (caller should stop), False if the loop should never fall through here
    (only returns on deadline; any disconnect raises instead, for the caller to retry)."""
    async with websockets.connect(url, max_size=None) as ws:
        msg = json.loads(await ws.recv())
        if msg["type"] != "auth_required":
            raise AuthOrSubscribeError(f"Unexpected first message: {msg}")

        await ws.send(json.dumps({"type": "auth", "access_token": token}))
        msg = json.loads(await ws.recv())
        if msg["type"] != "auth_ok":
            raise AuthOrSubscribeError(f"Auth failed: {msg}")
        print("Authenticated.")

        await ws.send(json.dumps({
            "id": 1,
            "type": "subscribe_events",
            "event_type": "state_changed",
        }))
        msg = json.loads(await ws.recv())
        if not msg.get("success"):
            raise AuthOrSubscribeError(f"Subscribe failed: {msg}")

        print("Listening for motion/occupancy state changes.\n")

        next_id = 100

        def nid() -> int:
            nonlocal next_id
            next_id += 1
            return next_id - 1

        while True:
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return True
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    # Deadline reached while idle-waiting for the next event — not a
                    # dropped connection, so don't let the caller's retry logic treat
                    # it as one.
                    return True
            else:
                raw = await ws.recv()

            msg = json.loads(raw)
            if msg.get("type") != "event":
                continue

            d = msg["event"]["data"]
            entity_id = d.get("entity_id", "")
            new_state = d.get("new_state")
            old_state = d.get("old_state")

            if not _is_motion_entity(entity_id, new_state):
                continue

            if entity_id not in platform_cache:
                try:
                    entry = await ws_command(ws, nid(), {
                        "type": "config/entity_registry/get", "entity_id": entity_id,
                    })
                    platform_cache[entity_id] = entry.get("platform", "?")
                except Exception:
                    platform_cache[entity_id] = "?"
            platform = platform_cache[entity_id]

            record = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "entity_id": entity_id,
                "platform": platform,
                "device_id": None,
                "old_state": old_state.get("state") if old_state else None,
                "new_state": new_state.get("state") if new_state else None,
                "attributes": new_state.get("attributes") if new_state else {},
            }
            out.write(json.dumps(record) + "\n")

            old_label = record["old_state"]
            new_label = record["new_state"]
            print(f"[{record['ts']}] {entity_id} ({platform}): {old_label} -> {new_label}")


async def capture(host: str, port: int, output_path: Path, duration: float | None) -> None:
    url = f"ws://{host}:{port}/api/websocket"
    token = load_token()
    deadline = time.monotonic() + duration if duration is not None else None
    platform_cache: dict[str, str] = {}
    backoff = 5.0

    with open(output_path, "a", encoding="utf-8", buffering=1) as out:
        print(f"Writing to {output_path}")
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                print("Duration elapsed. Stopping.")
                return
            try:
                print(f"Connecting to {url} ...")
                reached_deadline = await _capture_session(url, token, out, platform_cache, deadline)
                if reached_deadline:
                    print("Duration elapsed. Stopping.")
                    return
            except AuthOrSubscribeError as e:
                print(f"Non-retryable error, stopping: {e}")
                return
            except (OSError, websockets.exceptions.WebSocketException, asyncio.TimeoutError) as e:
                if deadline is not None and time.monotonic() >= deadline:
                    print("Duration elapsed during a disconnect. Stopping.")
                    return
                sleep_for = min(backoff, max(0.0, deadline - time.monotonic())) if deadline is not None else backoff
                print(f"Connection lost ({e!r}); reconnecting in {sleep_for:.0f}s...")
                await asyncio.sleep(sleep_for)
                backoff = min(backoff * 2, 300.0)  # exponential backoff, capped at 5 min
                continue
            else:
                backoff = 5.0  # a session that ran without erroring resets the backoff


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture motion/occupancy events house-wide")
    parser.add_argument("--host", default="192.168.20.2")
    parser.add_argument("--port", type=int, default=8123)
    parser.add_argument(
        "--output",
        default=None,
        help="JSONL output path (default: scripts/motion_capture_<timestamp>.jsonl)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Auto-stop after this many seconds (default: run until Ctrl+C)",
    )
    args = parser.parse_args()

    output_path = Path(args.output) if args.output else (
        Path(__file__).parent / f"motion_capture_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    )

    try:
        asyncio.run(capture(args.host, args.port, output_path, args.duration))
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()

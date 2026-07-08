#!/usr/bin/env python3
"""Poll battery/last-seen/node-status health for every motion & occupancy
sensor's owning device, house-wide, as a companion to capture_motion_events.py.

capture_motion_events.py only logs binary_sensor on/off transitions — it can show
that a sensor was stuck "on" for an abnormal duration, but not *why* (dead battery
vs. RF/mesh loss vs. genuinely-clear-just-not-reported). This script fills that gap
by periodically snapshotting each motion node's `battery_level`, `last_seen`,
`node_status`, and (Z-Wave sleepy devices) `wake_up_interval` sibling entities, so a
stuck-open event can be correlated against whether the node was still actively
communicating, what its battery was doing, and whether a silence gap is within its
own expected sleep-cycle length or actually anomalous.

Discovery is one-shot at startup: enumerate every binary_sensor with
device_class in (motion, occupancy), resolve each to its owning device_id via the
entity registry, then scan all entities on that device for battery/last_seen/
node_status/wake_up_interval siblings (matched by live-state device_class where
possible, falling back to entity_id suffix — Zigbee2MQTT devices don't always
expose device_class on these secondary entities the way Z-Wave JS does).
`wake_up_interval` entities are Z-Wave JS `number.*` entities exposing the node's
Wake Up CC interval (in seconds) — like the Configuration CC hw-timeout entities,
these are frequently disabled by default, so `state` may come back `None` until
someone enables the entity once via Settings → Entities. Many Z-Wave motion sensors
(e.g. Zooz ZSE11) don't implement Wake Up CC at all — for those, `diagnostics`
entities (rssi, round_trip_time, commands_dropped_rx/tx, timed_out_responses,
successful_commands_rx/tx — Z-Wave JS's own link-quality statistics, also disabled
by default) are the closest available substitute for judging whether a silence gap
reflects a real RF/mesh problem.

Unlike the event-driven motion capture, this logs a snapshot on every poll interval
(not just on change) — the whole point is to have a continuous timeline so a
*stopped* last_seen or node_status shows up as a gap, not just a changed value.

Usage:
    python scripts/capture_node_health.py [--host 192.168.20.2] [--port 8123]
        [--output node_health.jsonl] [--duration 356800] [--interval 300]
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


_DIAGNOSTIC_SUFFIXES = (
    "_rssi",
    "_signal_strength",
    "_round_trip_time",
    "_commands_dropped_rx",
    "_commands_dropped_tx",
    "_timed_out_responses",
    "_successful_commands_rx",
    "_successful_commands_tx",
)


def _classify_sibling(entity_id: str, state: dict | None) -> str | None:
    """Return 'battery', 'last_seen', 'node_status', 'wake_up_interval',
    'diagnostics', or None."""
    if not entity_id.startswith(("sensor.", "binary_sensor.", "number.")):
        return None
    attrs = (state or {}).get("attributes", {})
    device_class = attrs.get("device_class")
    suffix = entity_id.rsplit("_", 1)[-1] if "_" in entity_id else ""
    if device_class == "battery" or entity_id.endswith(("_battery_level", "_battery")):
        return "battery"
    if device_class == "timestamp" and "last_seen" in entity_id:
        return "last_seen"
    if entity_id.endswith("_last_seen"):
        return "last_seen"
    if "node_status" in entity_id:
        return "node_status"
    if "wake_up_interval" in entity_id:
        return "wake_up_interval"
    if entity_id.endswith(_DIAGNOSTIC_SUFFIXES):
        return "diagnostics"
    return None


async def discover(ws) -> dict[str, str]:
    """Returns {entity_id: kind} for every battery/last_seen/node_status sibling
    of every motion/occupancy sensor's owning device."""
    next_id = 1

    def nid() -> int:
        nonlocal next_id
        next_id += 1
        return next_id - 1

    entity_list = await ws_command(ws, nid(), {"type": "config/entity_registry/list"})
    states_result = await ws_command(ws, nid(), {"type": "get_states"})
    state_by_id = {s["entity_id"]: s for s in states_result}

    motion_device_ids: set[str] = set()
    for entity_id, state in state_by_id.items():
        if not entity_id.startswith("binary_sensor."):
            continue
        if state.get("attributes", {}).get("device_class") not in MOTION_DEVICE_CLASSES:
            continue
        entry = next((e for e in entity_list if e.get("entity_id") == entity_id), None)
        if entry and entry.get("device_id"):
            motion_device_ids.add(entry["device_id"])

    tracked: dict[str, str] = {}
    for entry in entity_list:
        device_id = entry.get("device_id")
        if device_id not in motion_device_ids:
            continue
        entity_id = entry.get("entity_id")
        kind = _classify_sibling(entity_id, state_by_id.get(entity_id))
        if kind:
            tracked[entity_id] = kind

    return tracked


async def _poll_loop(url: str, token: str, out, tracked: dict[str, str], interval: float,
                      deadline: float | None) -> bool:
    """Runs polls on one WS connection. Returns True once `deadline` is reached."""
    async with websockets.connect(url, max_size=None) as ws:
        msg = json.loads(await ws.recv())
        if msg["type"] != "auth_required":
            raise RuntimeError(f"Unexpected first message: {msg}")

        await ws.send(json.dumps({"type": "auth", "access_token": token}))
        msg = json.loads(await ws.recv())
        if msg["type"] != "auth_ok":
            raise RuntimeError(f"Auth failed: {msg}")
        print("Authenticated.")

        if not tracked:
            tracked.update(await discover(ws))
            print(f"Discovered {len(tracked)} battery/last_seen/node_status entities "
                  f"across motion & occupancy sensor devices.")
            for entity_id, kind in sorted(tracked.items()):
                print(f"  [{kind}] {entity_id}")

        next_id = 1000

        def nid() -> int:
            nonlocal next_id
            next_id += 1
            return next_id - 1

        while True:
            if deadline is not None and time.monotonic() >= deadline:
                return True

            result = await ws_command(ws, nid(), {"type": "get_states"})
            state_by_id = {s["entity_id"]: s for s in result}
            ts = datetime.now(timezone.utc).isoformat()
            for entity_id, kind in tracked.items():
                state = state_by_id.get(entity_id)
                record = {
                    "ts": ts,
                    "entity_id": entity_id,
                    "kind": kind,
                    "state": state.get("state") if state else None,
                    "attributes": state.get("attributes") if state else {},
                }
                out.write(json.dumps(record) + "\n")
            out.flush()
            print(f"[{ts}] polled {len(tracked)} entities")

            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return True
                await asyncio.sleep(min(interval, remaining))
            else:
                await asyncio.sleep(interval)


async def capture(host: str, port: int, output_path: Path, duration: float | None,
                   interval: float) -> None:
    url = f"ws://{host}:{port}/api/websocket"
    token = load_token()
    deadline = time.monotonic() + duration if duration is not None else None
    tracked: dict[str, str] = {}
    backoff = 5.0

    with open(output_path, "a", encoding="utf-8") as out:
        print(f"Writing to {output_path}")
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                print("Duration elapsed. Stopping.")
                return
            try:
                print(f"Connecting to {url} ...")
                reached_deadline = await _poll_loop(url, token, out, tracked, interval, deadline)
                if reached_deadline:
                    print("Duration elapsed. Stopping.")
                    return
            except (OSError, websockets.exceptions.WebSocketException, asyncio.TimeoutError) as e:
                if deadline is not None and time.monotonic() >= deadline:
                    print("Duration elapsed during a disconnect. Stopping.")
                    return
                sleep_for = min(backoff, max(0.0, deadline - time.monotonic())) if deadline is not None else backoff
                print(f"Connection lost ({e!r}); reconnecting in {sleep_for:.0f}s...")
                await asyncio.sleep(sleep_for)
                backoff = min(backoff * 2, 300.0)
                continue
            else:
                backoff = 5.0


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll node health for motion/occupancy sensor devices")
    parser.add_argument("--host", default="192.168.20.2")
    parser.add_argument("--port", type=int, default=8123)
    parser.add_argument(
        "--output",
        default=None,
        help="JSONL output path (default: scripts/node_health_<timestamp>.jsonl)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Auto-stop after this many seconds (default: run until Ctrl+C)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=300.0,
        help="Seconds between polls (default: 300)",
    )
    args = parser.parse_args()

    output_path = Path(args.output) if args.output else (
        Path(__file__).parent / f"node_health_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    )

    try:
        asyncio.run(capture(args.host, args.port, output_path, args.duration, args.interval))
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()

"""Record raw PumpPortal websocket frames for parser verification."""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import websockets
from dotenv import load_dotenv


async def record_frames(api_key: str | None, duration_minutes: int, output_path: Path) -> None:
    """Record websocket frames for specified duration.

    Args:
        api_key: PumpPortal API key (optional)
        duration_minutes: How long to record in minutes
        output_path: Where to write JSONL output
    """
    # Build URL with optional API key
    if api_key:
        ws_url = f"wss://pumpportal.fun/api/data?api-key={api_key}"
    else:
        ws_url = "wss://pumpportal.fun/api/data"

    frame_count = 0

    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Connecting to wss://pumpportal.fun/api/data")
    print(f"API key: {'present' if api_key else 'NOT SET (testing keyless)'}")
    print(f"Recording for {duration_minutes} minutes")
    print(f"Output: {output_path}")
    print("-" * 60)

    try:
        async with websockets.connect(ws_url) as ws:
            print("Connected!")

            # Subscribe to both free methods
            subscribe_new_token = json.dumps({"method": "subscribeNewToken"})
            await ws.send(subscribe_new_token)
            print("Sent: subscribeNewToken")

            subscribe_migration = json.dumps({"method": "subscribeMigration"})
            await ws.send(subscribe_migration)
            print("Sent: subscribeMigration")
            print("-" * 60)

            # Record frames
            start_time = asyncio.get_event_loop().time()
            end_time = start_time + (duration_minutes * 60)

            with output_path.open("w") as f:
                while asyncio.get_event_loop().time() < end_time:
                    try:
                        # Wait for message with timeout
                        message = await asyncio.wait_for(ws.recv(), timeout=1.0)

                        # Record with receive timestamp
                        record = {
                            "received_ts": datetime.now(timezone.utc).isoformat(),
                            "frame": message
                        }
                        f.write(json.dumps(record) + "\n")
                        f.flush()

                        frame_count += 1

                        # Print progress every 10 frames
                        if frame_count % 10 == 0:
                            elapsed = asyncio.get_event_loop().time() - start_time
                            remaining = (end_time - asyncio.get_event_loop().time()) / 60
                            print(
                                f"Frames: {frame_count:6d} | "
                                f"Elapsed: {elapsed/60:5.1f}m | "
                                f"Remaining: {remaining:5.1f}m"
                            )

                    except asyncio.TimeoutError:
                        # No message in last second, continue waiting
                        pass

            print("-" * 60)
            print(f"Recording complete: {frame_count} frames")

    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)


def main():
    """Main entrypoint for recording mode."""
    parser = argparse.ArgumentParser(description="Record raw PumpPortal websocket frames")
    parser.add_argument(
        "--minutes",
        type=int,
        required=True,
        help="Duration to record in minutes"
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output JSONL file path"
    )
    args = parser.parse_args()

    # Load API key from environment (optional)
    load_dotenv()
    api_key = os.getenv("PUMPPORTAL_API_KEY")

    if not api_key:
        print("WARNING: PUMPPORTAL_API_KEY not found in environment")
        print("Attempting keyless connection (testing if API key is required)...")
        print("")

    # Run recording
    asyncio.run(record_frames(api_key, args.minutes, args.out))


if __name__ == "__main__":
    main()

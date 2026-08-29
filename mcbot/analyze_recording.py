"""Analyze recorded PumpPortal websocket frames."""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


def analyze_recording(jsonl_path: Path) -> None:
    """Analyze recorded frames and report statistics.

    Args:
        jsonl_path: Path to recorded JSONL file
    """
    if not jsonl_path.exists():
        print(f"ERROR: {jsonl_path} not found")
        return

    frames = []
    message_shapes = defaultdict(list)
    migration_count = 0
    token_creation_count = 0

    # Read all frames
    with jsonl_path.open() as f:
        for line_num, line in enumerate(f, 1):
            try:
                record = json.loads(line)
                frame_str = record.get("frame", "")

                # Parse the actual websocket frame
                try:
                    frame_data = json.loads(frame_str)
                except (json.JSONDecodeError, TypeError):
                    # Frame is not JSON - record as string
                    frame_data = {"__raw__": frame_str}

                frames.append({
                    "received_ts": record.get("received_ts"),
                    "data": frame_data
                })

                # Categorize by top-level keys
                if isinstance(frame_data, dict):
                    keys_tuple = tuple(sorted(frame_data.keys()))
                    message_shapes[keys_tuple].append(frame_data)

                    # Detect message type heuristically
                    # (We don't know the actual fields yet - this is discovery!)
                    if any(k for k in frame_data.keys() if "migrat" in k.lower()):
                        migration_count += 1
                    if any(k for k in frame_data.keys() if "token" in k.lower() or "mint" in k.lower()):
                        token_creation_count += 1

            except json.JSONDecodeError as e:
                print(f"WARNING: Line {line_num} is not valid JSON: {e}")

    # Report statistics
    print("=" * 70)
    print("PUMPPORTAL RECORDING ANALYSIS")
    print("=" * 70)
    print(f"Total frames received: {len(frames)}")
    print(f"Distinct message shapes: {len(message_shapes)}")
    print()

    print("MESSAGE SHAPES (by sorted top-level keys):")
    print("-" * 70)
    for idx, (keys_tuple, examples) in enumerate(sorted(message_shapes.items()), 1):
        print(f"\nShape #{idx}: {len(examples)} frames")
        print(f"Keys: {list(keys_tuple)}")
        print(f"Example frame:")
        print(json.dumps(examples[0], indent=2))

    print()
    print("=" * 70)
    print(f"Frames with 'migration' in keys: {migration_count}")
    print(f"Frames with 'token'/'mint' in keys: {token_creation_count}")
    print("=" * 70)


def main():
    """Main entrypoint."""
    parser = argparse.ArgumentParser(description="Analyze recorded websocket frames")
    parser.add_argument(
        "jsonl_path",
        type=Path,
        help="Path to recorded JSONL file"
    )
    args = parser.parse_args()

    analyze_recording(args.jsonl_path)


if __name__ == "__main__":
    main()

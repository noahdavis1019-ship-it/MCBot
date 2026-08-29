# Setup Instructions

## Prerequisites

1. Python 3.11 or higher
2. PumpPortal API key (free)

## Installation

```bash
# Clone repository
cd MCBot

# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create virtual environment and install dependencies
uv venv
uv pip install -e ".[dev]"
```

## Configuration

### Get PumpPortal API Key

1. Visit https://pumpportal.fun/
2. Create a free API key
3. Copy the key

### Configure Environment

```bash
# Copy example environment file
cp .env.example .env

# Edit .env and add your API key
# PUMPPORTAL_API_KEY=your-key-here
```

## Recording Websocket Data (REQUIRED FIRST STEP)

Before running the collector, you MUST record real websocket frames to verify the parser:

```bash
source .venv/bin/activate

# Record for 30 minutes
python -m mcbot.record --minutes 30 --out tests/fixtures/pumpportal_raw.jsonl

# Analyze the recording
python -m mcbot.analyze_recording tests/fixtures/pumpportal_raw.jsonl
```

This will:
- Connect to PumpPortal websocket
- Subscribe to `subscribeNewToken` and `subscribeMigration`
- Record every frame verbatim to JSONL
- Report message shapes and field names

**DO NOT proceed to running the collector until you have recorded and analyzed real data.**

## Running the Collector

```bash
source .venv/bin/activate
python -m mcbot.collect
```

## Running Tests

```bash
source .venv/bin/activate
pytest
```

## Troubleshooting

### "PUMPPORTAL_API_KEY not found"

You need to create a `.env` file with your API key. See Configuration section above.

### "Connection refused" or websocket errors

- Check that your API key is valid
- PumpPortal may be down - check https://pumpportal.fun/
- You may have exceeded rate limits (unlikely with free tier subscriptions)

### Multiple websocket connections error

The code enforces a single connection. If you see this error:
- Make sure you're not running multiple instances
- PumpPortal bans multiple connections from the same API key

# Setup Instructions

## Prerequisites

1. Python 3.11 or higher
2. PumpPortal API key (optional - keyless connection works for migration events)

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

## Configuration (Optional)

### Get PumpPortal API Key

**Note:** The collector works without an API key for migration events. You may optionally add a key for access to additional PumpSwap data.

1. Visit https://pumpportal.fun/
2. Create a free API key
3. Copy the key

### Configure Environment

```bash
# Copy example environment file (if using API key)
cp .env.example .env

# Edit .env and add your API key
# PUMPPORTAL_API_KEY=your-key-here
```

If you skip this step, the collector will connect without a key and work normally for migration events.

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

## Running as macOS Background Service (Optional)

For 24/7 operation with automatic restart on crash or reboot, install the collector as a launchd agent.

### Prerequisites

1. Ensure logs directory exists:
```bash
mkdir -p ~/.mcbot/logs
```

2. Edit `com.mcbot.collect.plist` and replace `YOUR_USERNAME` with your actual username in all paths:
   - `/Users/YOUR_USERNAME/MCBot/.venv/bin/python3`
   - `/Users/YOUR_USERNAME/MCBot`
   - `/Users/YOUR_USERNAME/.mcbot/logs/stdout.log`
   - `/Users/YOUR_USERNAME/.mcbot/logs/stderr.log`

### Install

```bash
# Copy plist to LaunchAgents directory
cp com.mcbot.collect.plist ~/Library/LaunchAgents/

# Load the agent (starts immediately and on login)
launchctl load ~/Library/LaunchAgents/com.mcbot.collect.plist
```

### Verify

```bash
# Check if running
launchctl list | grep mcbot

# View logs
tail -f ~/.mcbot/logs/stdout.log
tail -f ~/.mcbot/logs/stderr.log
```

### Uninstall

```bash
# Stop and unload the agent
launchctl unload ~/Library/LaunchAgents/com.mcbot.collect.plist

# Remove plist file
rm ~/Library/LaunchAgents/com.mcbot.collect.plist
```

## Coverage Reporting

Generate hourly uptime and observation coverage reports:

```bash
source .venv/bin/activate

# Last 24 hours (default)
python -m mcbot.report

# Last 48 hours
python -m mcbot.report --hours 48

# Custom time range
python -m mcbot.report --start "2026-08-28T00:00:00+00:00" --end "2026-08-29T00:00:00+00:00"

# Custom database path
python -m mcbot.report --db /path/to/custom.db
```

### Coverage Report Format

The report shows per-hour statistics:
- **HB Exp/Rcv**: Heartbeats expected (12/hour) vs received
- **Uptime**: Percentage of expected heartbeats received
- **Migs**: Migration events captured
- **OK/Missed/Error**: Observation counts by status

Example output:
```
==================================================
HOURLY COVERAGE REPORT
==================================================
Hour (UTC)           HB Exp   HB Rcv   Uptime   Migs   OK       Missed   Error
--------------------------------------------------
2026-08-29 10:00:00  12       12       100.0%   2      84       0        0
2026-08-29 11:00:00  12       11        91.7%   1      77       3        1
==================================================
Summary:
  Hours covered:       2
  Avg uptime:          95.8%
  Total migrations:    3
  Total observations:  165
    OK:                161
    MISSED_LATE:       3
    HTTP_ERROR:        1
```

**Note:** Gaps in uptime are expected when running on a laptop. This is a measurement tool, not an error condition.

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

### LaunchAgent not starting

- Check paths in `com.mcbot.collect.plist` are correct
- Verify logs directory exists: `~/.mcbot/logs`
- Check stderr log: `tail ~/.mcbot/logs/stderr.log`
- Verify Python path: `which python3` in your venv

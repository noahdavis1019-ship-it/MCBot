# MCBot

Solana memecoin quant research infrastructure. This is a research project focused on understanding pump.fun token migration dynamics and execution costs. No trading code exists or will be written in the near term.

## Current Experiments

- **EXP-001**: Census collector for pump.fun migrations with forward returns and execution cost probes

## Quick Start

```bash
# Install dependencies
uv venv
uv pip install -e ".[dev]"

# Run the collector
python -m mcbot.collect

# Run tests
pytest
```

## Project Structure

```
mcbot/
  collect.py       # Main entrypoint
  db.py           # SQLite schema and operations
  collector.py    # PumpPortal websocket client
  scheduler.py    # Observation scheduler
  probe.py        # Jupiter quote probe
  ratelimit.py    # Token bucket rate limiter
data/             # SQLite database storage
tests/            # Unit and integration tests
docs/             # Experiment documentation
```

## Documentation

- [SYSTEM_STATE.md](docs/SYSTEM_STATE.md) - Current system state and known gaps
- [EXPERIMENT_REGISTRY.md](docs/EXPERIMENT_REGISTRY.md) - Active and completed experiments
- [DATA_DICTIONARY.md](docs/DATA_DICTIONARY.md) - Database schema documentation
- [DECISIONS.md](docs/DECISIONS.md) - Design decisions and rationale

## Constraints

- Free tier APIs only (Jupiter 1 rps, DexScreener ~60 rpm, PumpPortal 1 connection)
- No wallet, no private keys, no transaction execution
- Single process, standard library + httpx + websockets + sqlite3
- Append-only database, no mutations after insert

## License

Research project - see LICENSE

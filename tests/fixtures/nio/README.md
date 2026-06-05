# Matrix nio HTTP fixtures (plan 0016)

Two tiers of JSON cassettes, replayed in tests via `aioresponses` (no live network in CI).

## `synthetic/` (committed)

Minimal, protocol-correct responses with fixed room IDs and timestamps. CI runs
deterministic behavioral tests against these (`tests/matrix/test_client.py`).

## `recorded/` (gitignored, local only)

Captured from a live homeserver with `scripts/record_nio_fixtures.py` and
`.env.local`. Tokens are sanitized on write. Used by `@pytest.mark.recorded`
smoke tests (`tests/matrix/test_recorded_fixtures.py`); they skip when absent.

```bash
cp .env.local.example .env.local   # fill in matrix.org test credentials
uv run python scripts/record_nio_fixtures.py --full-sync
uv run python scripts/record_nio_fixtures.py
uv run pytest -m recorded -n 0
```

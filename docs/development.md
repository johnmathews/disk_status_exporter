# Development

## Prerequisites

- Python 3.13+ (for local development)
- [uv](https://docs.astral.sh/uv/) for dependency management

## Setup

```sh
# Clone the repo
git clone https://github.com/johnmathews/disk_status_exporter.git
cd disk_status_exporter

# Create venv and install all dependencies
uv sync --all-groups
```

## Running Locally

```sh
uv run uvicorn main:app --host 0.0.0.0 --port 9635
```

Note: on a development machine without physical HDDs or `smartctl`, the `/metrics` endpoint will return header lines and zero-count device totals, which is expected.

## Running Tests

```sh
uv run -m pytest
```

This runs the test suite with coverage enabled (configured in `pyproject.toml`). Coverage reports are generated in:

- Terminal: summary with missing lines
- HTML: `htmlcov/` directory
- XML: `coverage.xml`

The project requires a minimum of 85% branch coverage (`fail_under = 85`).

## Test Structure

Tests are in `tests/test_exporter_api.py` and use `monkeypatch` to mock system-level functions (`list_block_devices`, `is_rotational`, `smartctl_power_state`, etc.) since physical disk access is not available in test environments.

Key test areas:

- Health endpoint (`/healthz`)
- Metrics endpoint with mocked device data
- Device filtering (non-rotational, virtual device exclusion)
- Power state parsing and multi-probe tie-breaking
- Cooldown behavior after timeouts
- Error handling (smartctl errors, timeouts)

## Mutation Testing

```sh
uv run mutmut run
uv run mutmut results
```

## Project Configuration

All project metadata, dependencies, and tool configuration is in `pyproject.toml`:

- `[project]` — name, version, description
- `[dependency-groups]` — dev dependencies (pytest, coverage, httpx, etc.)
- `[tool.pytest.ini_options]` — pytest configuration with coverage defaults
- `[tool.coverage.*]` — coverage settings (branch coverage, thresholds, output)

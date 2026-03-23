# Architecture

## Overview

disk-status-exporter is a single-file Python application (`main.py`) that runs as an async FastAPI web server. It exposes Prometheus-compatible metrics describing the power state of physical hard drives.

## Components

### Web Server (FastAPI + Uvicorn)

The application uses FastAPI with Uvicorn as the ASGI server. Two HTTP endpoints are exposed on port 9635:

- `GET /metrics` — Prometheus scrape endpoint (returns `text/plain`)
- `GET /healthz` — health check (returns JSON `{"status": "ok"}`)

### Device Discovery

On each scrape, the exporter enumerates block devices from `/sys/block`, filtering out:

- Loop, RAM, floppy, optical, device-mapper, mdraid, and zvol devices (by kernel name prefix)
- Non-rotational devices (SSDs) via `/sys/block/<kname>/queue/rotational`
- Virtual devices (QEMU/virtio) via sysfs vendor/model strings and `/dev/disk/by-id` prefixes

### Power State Probing

Each HDD is probed using `smartctl -d sat,12 -n standby -i <dev>`:

- `-d sat,12` forces SATA passthrough, avoiding autodetection probes that can wake sleeping drives
- `-n standby` tells smartctl not to wake a drive that is in standby
- The exporter parses stdout text for state keywords (STANDBY, SLEEP, IDLE_A, etc.) rather than relying on exit codes

Probes run concurrently via `asyncio.to_thread()`, bounded by a semaphore (`MAX_CONCURRENCY`, default 8).

### Multi-Probe Tie-Breaking

When `PROBE_ATTEMPTS > 1`, the exporter runs multiple probes per device and returns the highest-activity state observed. States are ranked by `ACTIVITY_RANK` (ascending activity): error < unknown < sleep < standby < idle_a < idle_b < idle_c < idle < active_or_idle < active.

### Timeout Cooldown

If smartctl times out on a device (often meaning the drive is spinning up), that device enters a cooldown period (`COOLDOWN_SECONDS`, default 300s). During cooldown, the device reports `unknown` and is not probed, preventing repeated wake attempts.

### ZFS Pool Mapping

If `zpool` is available on the system, the exporter parses `zpool status -L -P` to map devices to their pool names (e.g., `tank`). If zpool is absent, the `pool` label defaults to `"none"`.

### Device Identification

Devices are labeled with stable `/dev/disk/by-id/` symlinks, preferring prefixes in this order: `ata-`, `scsi-`, `wwn-`, `nvme-`, `usb-`, `virtio-`. Falls back to `/dev/<kname>` if no by-id link exists.

## Data Flow

```
Prometheus scrape (GET /metrics)
  -> Enumerate /sys/block devices
  -> Filter: rotational HDDs only, exclude virtual
  -> Concurrent smartctl probes (semaphore-bounded)
  -> Parse stdout for power state keywords
  -> Build Prometheus text format response
  -> Return text/plain
```

## Dependencies

### Runtime (in container)

- Python 3.11 (via `python:3.11-slim` base image)
- FastAPI + Uvicorn (installed via pip in Dockerfile)
- `smartmontools` (provides `smartctl`, installed via apt)

### Development

- `uv` for dependency management
- `pytest` + `pytest-asyncio` for testing
- `coverage` + `pytest-cov` for coverage reporting
- `hypothesis` for property-based testing
- `mutmut` for mutation testing
- `httpx` for async test client
- `fastapi` + `uvicorn` for local development

## File Layout

```
disk-status-exporter/
  main.py                  # entire application
  dockerfile               # container build
  pyproject.toml           # project metadata, dev deps, pytest/coverage config
  requirements-dev.txt     # (legacy, dev deps managed via uv)
  uv.lock                  # locked dependency versions
  readme.md                # project README with metrics reference
  tests/
    test_exporter_api.py   # API and unit tests
  .github/
    workflows/
      docker-publish.yml   # CI: test, build, push to GHCR
  docs/                    # project documentation
  journal/                 # development journal
```

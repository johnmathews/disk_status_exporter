# 2026-03-23: Add /docs/ and /journal/ directories

## What was done

Created the project documentation structure:

- `docs/architecture.md` — system architecture, components, data flow, dependencies, and file layout
- `docs/deployment.md` — container image details, running instructions, CI/CD pipeline, environment variables, Prometheus configuration, and logging format
- `docs/development.md` — local development setup, running tests, coverage requirements, test structure, and mutation testing

Created the development journal directory with this initial entry.

## Decisions

- Documentation was split into three focused files (architecture, deployment, development) rather than duplicating what's already in `readme.md`. The README covers metrics reference and quick-start; the docs directory covers deeper topics.
- The journal uses YYMMDD format per project convention.

## Current project state

- Single-file Python application (`main.py`) with FastAPI
- Test suite in `tests/test_exporter_api.py` with 85%+ branch coverage requirement
- CI/CD via GitHub Actions: test, build, push to `ghcr.io/johnmathews/disk_status_exporter`
- Deployed as a privileged Docker container on TrueNAS/Proxmox hosts

# Deployment

## Container Image

The image is published to GitHub Container Registry at:

```
ghcr.io/johnmathews/disk_status_exporter
```

### Tags

- `:latest` — most recent push to `main`
- `:v1.2.3` — tagged releases
- `:rN` — CI run number (e.g., `r124`)
- `:sha-<7chars>` — short commit SHA

### Running

```sh
docker run -d \
  --name=disk-status-exporter \
  --privileged \
  -v /dev:/dev:ro \
  -v /run/udev:/run/udev:ro \
  -p 9635:9635 \
  ghcr.io/johnmathews/disk_status_exporter:latest
```

**Required privileges:**

- `--privileged` is needed so `smartctl` can issue SATA/SCSI commands to physical disks
- `/dev:/dev:ro` gives access to block device nodes
- `/run/udev:/run/udev:ro` provides udev device metadata (needed for `/dev/disk/by-id` resolution)

### Platform Options

- **TrueNAS**: deploy as a custom App
- **Proxmox**: run as a Docker container on the host or inside a privileged LXC

## Creating Tagged Releases

```sh
git tag -a v1.2.3 -m "release v1.2.3"
git push origin v1.2.3
```

GitHub Actions builds and pushes `:latest`, `:v1.2.3`, and `:sha-<commit>`.

## CI/CD Pipeline

The GitHub Actions workflow (`.github/workflows/docker-publish.yml`) runs on:

- Push to `main`
- Push of `v*` tags
- Manual `workflow_dispatch`

Pipeline steps:

1. Checkout source
2. Set up `uv` with Python 3.11
3. Sync all dependency groups
4. Run `pytest` with coverage (HTML + XML reports)
5. Upload coverage artifacts
6. Compute version metadata (tag or run number)
7. Log in to GHCR
8. Build and push Docker image with computed tags

Tests must pass (including the `fail_under = 85` coverage threshold) before the image is built.

## Environment Variables

| Variable            | Default | Description                                                    |
| ------------------- | ------- | -------------------------------------------------------------- |
| `PROBE_ATTEMPTS`    | 1       | Number of smartctl probes per scrape                           |
| `PROBE_INTERVAL_MS` | 1000    | Milliseconds between probe attempts (when PROBE_ATTEMPTS > 1) |
| `MAX_CONCURRENCY`   | 8       | Maximum concurrent device probes                               |
| `COOLDOWN_SECONDS`  | 300     | Seconds to skip a device after a timeout                       |
| `LOG_LEVEL`         | INFO    | Logging verbosity (DEBUG, INFO, WARNING, ERROR)                |

## Prometheus Configuration

Add the exporter as a scrape target:

```yaml
scrape_configs:
  - job_name: "disk-status"
    static_configs:
      - targets: ["<host>:9635"]
    scrape_interval: 60s
```

A longer scrape interval (60s+) is recommended to minimize the chance of waking sleeping drives.

## Logging

Logs are written to stdout in logfmt format:

```
ts=2025-01-23T19:30:00+0000 level=INFO scan complete: enumerated=6 scanned_hdds=2 ...
```

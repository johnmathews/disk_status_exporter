
## Context and Motivation

This service exports the power state of all physical hard drives on a TrueNAS system or a Proxmox host.
It is designed for Prometheus scraping and Grafana visualization.
The goal is to monitor when disks spin up or enter standby, helping to verify power-saving behavior and detect abnormal activity.

The exporter focuses on reliability and minimal footprint:

- Runs without requiring ZFS libraries inside the container.
- Uses persistent /dev/disk/by-id identifiers for stable labeling.
- Ignores virtual and SSD devices to avoid false metrics.

## Outputs

The container exposes Prometheus-formatted metrics on port 9635.

Example metrics:

```text
# HELP disk_power_state Current disk power state as a numeric code
# TYPE disk_power_state gauge
disk_power_state{device_id="/dev/disk/by-id/wwn-0x5000c500f7425581",type="hdd",pool="tank"} 0
disk_info{device_id="/dev/disk/by-id/wwn-0x5000c500f7425581",type="hdd",pool="tank"} 1
```

State codes:

- `0` = standby
- `1` = idle
- `2` = active or idle
- `-1` = unknown
- `-2` = error

A /healthz endpoint returns simple JSON status.

## Configuration

Environment variables control probe behavior:

| Variable | Default | Description |
|----------|---------|-------------|
| `PROBE_ATTEMPTS` | 1 | Number of smartctl probes per scrape. Single probe is recommended to minimize wake risk. |
| `PROBE_INTERVAL_MS` | 1000 | Milliseconds between probe attempts (when `PROBE_ATTEMPTS` > 1). |
| `MAX_CONCURRENCY` | 8 | Maximum concurrent device probes. |
| `COOLDOWN_SECONDS` | 300 | Seconds to skip a device after a timeout. Prevents repeated wake attempts on unresponsive drives. |
| `LOG_LEVEL` | INFO | Logging verbosity (DEBUG, INFO, WARNING, ERROR). |

### HDD Wake Prevention

The exporter uses several techniques to avoid waking sleeping HDDs:

- **SATA passthrough** (`-d sat,12`): Tells smartctl the device type explicitly, skipping autodetection probes that can wake drives.
- **Single probe by default**: `PROBE_ATTEMPTS=1` minimizes wake opportunities per scrape.
- **Timeout cooldown**: Devices that timeout (often indicating spin-up) are skipped for `COOLDOWN_SECONDS` to avoid repeated wake attempts.

## How to Deploy

Build or pull the image from GitHub Container Registry:

```sh
docker run -d \
  --name=disk-status-exporter \
  --privileged \
  -v /dev:/dev:ro \
  -v /run/udev:/run/udev:ro \
  -p 9635:9635 \
  ghcr.io/johnmathews/disk_status_exporter:latest
```

TrueNAS users can deploy it as a custom App.
Proxmox users can run it as a lightweight Docker container on the host or inside a privileged LXC.

Logs are written in logfmt format and printed to stdout.

## How to Tag

Version tags are created through Git.
Each push to the main branch builds and publishes an image with an incremental run tag (for example r124) and latest.
Tagged releases create versioned images.

To create and push a tagged release:

```sh
git tag -a v1.2.3 -m "release v1.2.3"
git push origin v1.2.3
```

GitHub Actions will automatically build and push:

- `:latest`
- `:v1.2.3`
- `:sha-<shortcommit>`

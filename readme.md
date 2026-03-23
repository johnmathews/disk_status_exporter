## Context and Motivation

This service exports the power state of all physical hard drives on a TrueNAS system or a Proxmox host. It is designed
for Prometheus scraping and Grafana visualization. The goal is to monitor when disks spin up or enter standby, helping to
verify power-saving behavior and detect abnormal activity.

The exporter focuses on reliability and minimal footprint:

- Runs without requiring ZFS libraries inside the container.
- Uses persistent /dev/disk/by-id identifiers for stable labeling.
- Ignores virtual and SSD devices to avoid false metrics.

## Metrics

The container exposes Prometheus-formatted metrics on `GET /metrics` (port 9635). A `GET /healthz` endpoint returns
`{"status": "ok"}`.

Only physical rotational (HDD) devices are reported. SSDs, virtual devices (QEMU, virtio), loop devices, device-mapper,
mdraid, and zvols are excluded.

### Metric Reference

#### `disk_power_state` (gauge)

Numeric code representing the current power state of a disk.

Labels: `device_id`, `device`, `type`, `pool`.

| Value | State           | Meaning                                                       |
| ----- | --------------- | ------------------------------------------------------------- |
| `-2`  | `error`         | smartctl returned an error                                    |
| `-1`  | `unknown`       | state could not be determined, or device is in cooldown       |
| `0`   | `standby`       | drive is spun down (platters stopped)                         |
| `1`   | `idle`          | generic idle (not further classified by drive firmware)       |
| `2`   | `active_or_idle` | drive is active or idle (smartctl cannot distinguish)         |
| `3`   | `idle_a`        | ACS idle_a (shallow idle, fast recovery)                      |
| `4`   | `idle_b`        | ACS idle_b (heads unloaded)                                   |
| `5`   | `idle_c`        | ACS idle_c (heads unloaded, lower power)                      |
| `6`   | `active`        | drive is actively performing I/O                              |
| `7`   | `sleep`         | deepest power-saving mode (requires reset to wake)            |

Higher values mean more activity / higher power draw, except for `sleep` (7) which is the deepest low-power state.
The `ACTIVITY_RANK` used internally for multi-probe tie-breaking orders states by ascending activity:
error < unknown < sleep < standby < idle_a < idle_b < idle_c < idle < active_or_idle < active.

#### `disk_power_state_string` (gauge)

Always `1`. Carries the human-readable power state name as the `state` label. Useful for Grafana value mappings and
`label_values()` queries.

Labels: `device_id`, `device`, `type`, `pool`, `state`.

#### `disk_info` (gauge)

Always `1`. Static metadata about the disk. Join on `device_id` to attach labels to other metrics.

Labels: `device_id`, `device`, `type`, `pool`.

#### `disk_exporter_scan_seconds` (gauge)

Wall-clock duration of the most recent scrape in seconds. No labels.

#### `disk_exporter_devices_total` (gauge)

Count of devices seen during the most recent scrape.

Label: `kind` (one of `enumerated`, `scanned_hdds`, `skipped_non_rotational`, `skipped_virtual`).

### Label Descriptions

| Label       | Description                                                                                    |
| ----------- | ---------------------------------------------------------------------------------------------- |
| `device_id` | Stable `/dev/disk/by-id/...` symlink (prefers `ata-`, `wwn-`, `scsi-` prefixes). Falls back to `/dev/<kname>` if no by-id link exists. |
| `device`    | Kernel device path, e.g. `/dev/sda`.                                                           |
| `type`      | Always `hdd` (SSDs are filtered out before reporting).                                         |
| `pool`      | ZFS pool name (e.g. `tank`) or `none` if the device is not part of a zpool.                    |
| `state`     | Human-readable power state string (only on `disk_power_state_string`).                         |
| `kind`      | Counter category (only on `disk_exporter_devices_total`).                                      |

### Example Scrape Output

```text
# HELP disk_power_state Current disk power state as a numeric code (0=standby, 7=sleep, 1=idle, 2=active_or_idle, -1=unknown, -2=error, 3=idle_a, 4=idle_b, 5=idle_c, 6=active).
# TYPE disk_power_state gauge
# HELP disk_info Static labels describing the disk (type/pool). Always 1.
# TYPE disk_info gauge
# HELP disk_power_state_string Always 1; carries the current power state as the 'state' label for display.
# TYPE disk_power_state_string gauge
# HELP disk_exporter_scan_seconds Duration of the last scan in seconds.
# TYPE disk_exporter_scan_seconds gauge
# HELP disk_exporter_devices_total Devices seen / scanned / skipped.
# TYPE disk_exporter_devices_total gauge
disk_info{device_id="/dev/disk/by-id/wwn-0x5000c500f7425581",device="/dev/sda",type="hdd",pool="tank"} 1
disk_power_state{device_id="/dev/disk/by-id/wwn-0x5000c500f7425581",device="/dev/sda",type="hdd",pool="tank"} 0
disk_power_state_string{device_id="/dev/disk/by-id/wwn-0x5000c500f7425581",device="/dev/sda",type="hdd",pool="tank",state="standby"} 1
disk_info{device_id="/dev/disk/by-id/wwn-0x5000c500f7425582",device="/dev/sdb",type="hdd",pool="tank"} 1
disk_power_state{device_id="/dev/disk/by-id/wwn-0x5000c500f7425582",device="/dev/sdb",type="hdd",pool="tank"} 6
disk_power_state_string{device_id="/dev/disk/by-id/wwn-0x5000c500f7425582",device="/dev/sdb",type="hdd",pool="tank",state="active"} 1
disk_exporter_scan_seconds 0.234567
disk_exporter_devices_total{kind="enumerated"} 6
disk_exporter_devices_total{kind="scanned_hdds"} 2
disk_exporter_devices_total{kind="skipped_non_rotational"} 3
disk_exporter_devices_total{kind="skipped_virtual"} 1
```

### Example PromQL Queries

```promql
# Current power state of each disk (human-readable)
disk_power_state_string == 1

# Disks currently in standby
disk_power_state == 0

# Disks that are active or spinning
disk_power_state >= 2

# Number of disks in standby per pool
count by (pool) (disk_power_state == 0)

# State changes over time (useful for detecting unexpected wake-ups)
changes(disk_power_state[1h])

# Join disk_info labels onto power state
disk_power_state * on(device_id) group_left(pool, type) disk_info
```

## Configuration

Environment variables control probe behavior:

| Variable            | Default | Description                                                                                       |
| ------------------- | ------- | ------------------------------------------------------------------------------------------------- |
| `PROBE_ATTEMPTS`    | 1       | Number of smartctl probes per scrape. Single probe is recommended to minimize wake risk.          |
| `PROBE_INTERVAL_MS` | 1000    | Milliseconds between probe attempts (when `PROBE_ATTEMPTS` > 1).                                  |
| `MAX_CONCURRENCY`   | 8       | Maximum concurrent device probes.                                                                 |
| `COOLDOWN_SECONDS`  | 300     | Seconds to skip a device after a timeout. Prevents repeated wake attempts on unresponsive drives. |
| `LOG_LEVEL`         | INFO    | Logging verbosity (DEBUG, INFO, WARNING, ERROR).                                                  |

### HDD Wake Prevention

The exporter uses several techniques to avoid waking sleeping HDDs:

- **SATA passthrough** (`-d sat,12`): Tells smartctl the device type explicitly, skipping autodetection probes that can
  wake drives.
- **Single probe by default**: `PROBE_ATTEMPTS=1` minimizes wake opportunities per scrape.
- **Timeout cooldown**: Devices that timeout (often indicating spin-up) are skipped for `COOLDOWN_SECONDS` to avoid
  repeated wake attempts.

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

TrueNAS users can deploy it as a custom App. Proxmox users can run it as a lightweight Docker container on the host or
inside a privileged LXC.

Logs are written in logfmt format and printed to stdout.

## How to Tag

Version tags are created through Git. Each push to the main branch builds and publishes an image with an incremental run
tag (for example r124) and latest. Tagged releases create versioned images.

To create and push a tagged release:

```sh
git tag -a v1.2.3 -m "release v1.2.3"
git push origin v1.2.3
```

GitHub Actions will automatically build and push:

- `:latest`
- `:v1.2.3`
- `:sha-<shortcommit>`

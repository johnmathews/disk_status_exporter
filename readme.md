
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

This repo builds a container that runs in trueNAS as a custom app and on the
proxmox host itself as a docker container.

It collects the status of HDDs and exposes metrics on port 9635.

This Github repo will trigger a Github action that builds a container and pushes
it to the `ghcr`.

The script does not yet collect the name of the datapool that each disk is
assigned to because loading zfs into the container makes it much heavier.

Bugs:
- the trueNAS boot disk (an NVMe drive) is monitored, and its status is always error.

Design Considerations:

- the service should handle HDDs, SSDs and M.2 drives correctly.
- the service should use persistent IDs and not `sd<letter>` names because these change on reboot.
- the service should create prometheus metrics that adhere to prometheus best practice and standards.


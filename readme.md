This repo builds a container that runs in trueNAS as a custom app.

It collects the status of the disks that trueNAS manages and exposes metrics at port 9635.

This github repo will trigger a github action that builds a container and pushes it to the ghcr.

The script does not yet collect the name of the datapool that each disk is assigned to. 

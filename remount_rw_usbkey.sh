#!/usr/bin/env bash
# remount_rw_usbkey.sh  –  Temporarily remount the first USB drive read-write.
# Useful for editing files directly on the drive from the Pi.
MOUNT="${1:-/mnt/usbdrive0}"
if mountpoint -q "${MOUNT}"; then
    mount -o remount,rw "${MOUNT}"
    echo "Remounted ${MOUNT} as read-write."
    echo "Remember to run:  mount -o remount,ro ${MOUNT}  when done."
else
    echo "Error: ${MOUNT} is not mounted."
    exit 1
fi

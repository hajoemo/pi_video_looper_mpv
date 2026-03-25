#!/usr/bin/env bash
# enable.sh  –  Enable the looper to start on boot and start it now.
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: Please run as root: sudo ./enable.sh"
    exit 1
fi
echo "Enabling mpv_video_looper..."
supervisorctl start mpv_video_looper
echo "mpv_video_looper enabled and started."

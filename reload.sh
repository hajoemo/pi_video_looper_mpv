#!/usr/bin/env bash
# reload.sh  –  Restart the looper and pick up any config changes.
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: Please run as root: sudo ./reload.sh"
    exit 1
fi
echo "Reloading mpv_video_looper..."
supervisorctl restart mpv_video_looper
echo "Done."

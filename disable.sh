#!/usr/bin/env bash
# disable.sh  –  Stop the looper and prevent it from starting on boot.
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: Please run as root: sudo ./disable.sh"
    exit 1
fi
CONF="/etc/supervisor/conf.d/mpv_video_looper.conf"
echo "Disabling mpv_video_looper..."
supervisorctl stop mpv_video_looper
# Flip autostart to false so it won't start after next reboot
if [ -f "${CONF}" ]; then
    sed -i 's/^autostart=true/autostart=false/' "${CONF}"
    supervisorctl reread
    supervisorctl update
    echo "autostart disabled in ${CONF}"
fi
echo "mpv_video_looper stopped and disabled. Run sudo ./enable.sh to re-enable."

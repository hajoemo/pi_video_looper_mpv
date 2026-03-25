#!/usr/bin/env bash
# install.sh  –  Install mpv_video_looper on Raspberry Pi OS (Bookworm / Bullseye)
# Run as root: sudo ./install.sh

set -euo pipefail

# Must be run as root
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: Please run as root: sudo ./install.sh"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="/usr/local/lib/mpv_video_looper"
SERVICE_NAME="mpv_video_looper"

# --- Detect the real user (the one who called sudo) ---
REAL_USER="${SUDO_USER:-pi}"
REAL_HOME="$(getent passwd "${REAL_USER}" | cut -d: -f6)"
if [ -z "${REAL_HOME}" ]; then
    echo "ERROR: Could not determine home directory for user '${REAL_USER}'."
    exit 1
fi
echo "=== mpv_video_looper installer ==="
echo "  Installing for user: ${REAL_USER} (home: ${REAL_HOME})"

# --- Detect boot config path (Bookworm uses /boot/firmware/, older uses /boot/) ---
if [ -d "/boot/firmware" ]; then
    BOOT_DIR="/boot/firmware"
else
    BOOT_DIR="/boot"
fi
BOOT_CONFIG="${BOOT_DIR}/video_looper.ini"
echo "  Boot config path:    ${BOOT_CONFIG}"
echo ""

# --- 1. System packages ---
echo "[1/6] Installing system packages..."
apt-get update -qq

# exfat: Bookworm ships exfatprogs; Bullseye/Buster ship exfat-fuse
EXFAT_PKG="exfatprogs"
if ! apt-cache show exfatprogs &>/dev/null; then
    EXFAT_PKG="exfat-fuse"
fi

apt-get install -y --no-install-recommends \
    mpv \
    python3 \
    python3-pip \
    python3-pygame \
    supervisor \
    ntfs-3g \
    "${EXFAT_PKG}"

# Optional GPIO support
if python3 -c "import RPi.GPIO" 2>/dev/null; then
    echo "  RPi.GPIO already present."
else
    apt-get install -y --no-install-recommends python3-rpi.gpio 2>/dev/null || \
        pip3 install RPi.GPIO --break-system-packages 2>/dev/null || \
        echo "  WARNING: RPi.GPIO not installed – GPIO control will be unavailable."
fi

# python-mpv binding (optional – looper falls back to subprocess)
pip3 install python-mpv --break-system-packages 2>/dev/null || \
    echo "  Note: python-mpv binding not installed; subprocess fallback will be used (fully functional)."

# --- 2. Copy application files ---
echo "[2/6] Copying application files to ${APP_DIR}..."
mkdir -p "${APP_DIR}"
cp -r "${SCRIPT_DIR}/mpv_video_looper/"* "${APP_DIR}/"
# Also install the reference config so the fallback path in load_config() works
mkdir -p "${APP_DIR}/assets"
cp "${SCRIPT_DIR}/assets/video_looper.ini" "${APP_DIR}/assets/video_looper.ini"

# --- 3. Install boot config ---
echo "[3/6] Installing config to ${BOOT_CONFIG}..."
if [ ! -f "${BOOT_CONFIG}" ]; then
    cp "${SCRIPT_DIR}/assets/video_looper.ini" "${BOOT_CONFIG}"
    echo "  Config installed at ${BOOT_CONFIG}"
else
    echo "  Existing config found at ${BOOT_CONFIG} – not overwriting."
    echo "  Reference config available at ${SCRIPT_DIR}/assets/video_looper.ini"
fi

# --- 4. Create video directory ---
echo "[4/6] Creating video directory..."
mkdir -p "${REAL_HOME}/video"
chown "${REAL_USER}:${REAL_USER}" "${REAL_HOME}/video"

# --- 5. Set up supervisor service ---
echo "[5/6] Configuring supervisor service..."
cat > "/etc/supervisor/conf.d/${SERVICE_NAME}.conf" << SUPERVISOR_CONF
[program:mpv_video_looper]
command=python3 ${APP_DIR}/video_looper.py ${BOOT_CONFIG}
directory=${APP_DIR}
autostart=true
autorestart=true
user=${REAL_USER}
environment=DISPLAY=":0",SDL_VIDEODRIVER="fbcon",SDL_FBDEV="/dev/fb0",HOME="${REAL_HOME}"
stdout_logfile=/var/log/supervisor/mpv_video_looper-stdout.log
stdout_logfile_maxbytes=1MB
stderr_logfile=/var/log/supervisor/mpv_video_looper-stderr.log
stderr_logfile_maxbytes=1MB
SUPERVISOR_CONF

# Add user to video group for framebuffer access
usermod -aG video "${REAL_USER}" 2>/dev/null || true

# --- 6. Reload supervisor ---
echo "[6/6] Reloading supervisor..."
supervisorctl reread
supervisorctl update

echo ""
echo "=== Installation complete! ==="
echo ""
echo "The looper will start automatically on next boot."
echo "To start it now:     sudo supervisorctl start ${SERVICE_NAME}"
echo "To stop it:          sudo supervisorctl stop  ${SERVICE_NAME}"
echo "To reload config:    sudo ./reload.sh"
echo "To disable:          sudo ./disable.sh"
echo ""
echo "Edit settings:       sudo nano ${BOOT_CONFIG}"
echo "View logs:           sudo tail -f /var/log/supervisor/mpv_video_looper-stdout.log"
echo ""

#!/bin/bash
# OSWorld FastAPI Server Entrypoint
# Starts Xvfb, GNOME desktop (same as OSWorld VMs), and the FastAPI server

set -e

# Configuration
DISPLAY_NUM="${DISPLAY_NUM:-99}"
SCREEN_WIDTH="${SCREEN_WIDTH:-1920}"
SCREEN_HEIGHT="${SCREEN_HEIGHT:-1080}"
SCREEN_DEPTH="${SCREEN_DEPTH:-24}"

export DISPLAY=":${DISPLAY_NUM}"
export HOME="/home/user"
export USER="user"
export XDG_RUNTIME_DIR="/tmp/runtime-user"
export XDG_SESSION_TYPE="x11"
export XDG_SESSION_CLASS="user"
export XDG_CURRENT_DESKTOP="GNOME"
export GNOME_SHELL_SESSION_MODE="ubuntu"

# Create runtime directories
mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"

echo "============================================"
echo "OSWorld FastAPI Server (GNOME Desktop)"
echo "============================================"
echo "Display: $DISPLAY"
echo "Screen: ${SCREEN_WIDTH}x${SCREEN_HEIGHT}x${SCREEN_DEPTH}"
echo "Desktop: GNOME (matching OSWorld VMs)"
echo ""

# Kill any existing processes
pkill -f "Xvfb :${DISPLAY_NUM}" 2>/dev/null || true
pkill -f gnome 2>/dev/null || true
pkill -f mutter 2>/dev/null || true

# Clean up stale lock files
rm -f /tmp/.X${DISPLAY_NUM}-lock 2>/dev/null || true
rm -f /tmp/.X11-unix/X${DISPLAY_NUM} 2>/dev/null || true

# Start Xvfb (virtual X server) with GLX support
echo "Starting Xvfb virtual display..."
Xvfb ":${DISPLAY_NUM}" -screen 0 "${SCREEN_WIDTH}x${SCREEN_HEIGHT}x${SCREEN_DEPTH}" +extension GLX +render -noreset &
XVFB_PID=$!

# Wait for Xvfb to start
sleep 2

# Check if Xvfb is running
if ! kill -0 $XVFB_PID 2>/dev/null; then
    echo "ERROR: Xvfb failed to start"
    exit 1
fi
echo "Xvfb started successfully (PID: $XVFB_PID)"

# Start D-Bus session daemon
echo "Starting D-Bus session..."
export DBUS_SESSION_BUS_ADDRESS=$(dbus-daemon --session --fork --print-address)
echo "D-Bus address: $DBUS_SESSION_BUS_ADDRESS"

# Start AT-SPI registry (accessibility - required for a11y tree)
echo "Starting AT-SPI registry..."
if [ -f /usr/libexec/at-spi2-registryd ]; then
    /usr/libexec/at-spi2-registryd &
elif [ -f /usr/lib/at-spi2-core/at-spi2-registryd ]; then
    /usr/lib/at-spi2-core/at-spi2-registryd &
fi
sleep 1

# Start GNOME-compatible window manager
# We use mutter (GNOME's window manager) for visual compatibility
echo "Starting window manager..."
DESKTOP_PID=""

# Try mutter first (X11 mode only, not Wayland)
if command -v mutter &> /dev/null; then
    echo "Attempting to start Mutter..."
    mutter --replace --sm-disable 2>/dev/null &
    DESKTOP_PID=$!
    sleep 2
    
    if kill -0 $DESKTOP_PID 2>/dev/null; then
        echo "Mutter started successfully (PID: $DESKTOP_PID)"
    else
        echo "Mutter failed, trying metacity..."
        DESKTOP_PID=""
    fi
fi

# Fallback to metacity if mutter fails
if [ -z "$DESKTOP_PID" ] || ! kill -0 $DESKTOP_PID 2>/dev/null; then
    if command -v metacity &> /dev/null; then
        echo "Starting Metacity window manager..."
        metacity --replace --sm-disable &
        DESKTOP_PID=$!
        sleep 1
        echo "Metacity started (PID: $DESKTOP_PID)"
    fi
fi

# Start GNOME settings daemon for proper theming
if command -v /usr/libexec/gsd-xsettings &> /dev/null; then
    echo "Starting GNOME settings daemon..."
    /usr/libexec/gsd-xsettings &
fi

# Set desktop background
echo "Setting desktop background..."
if command -v feh &> /dev/null; then
    if command -v convert &> /dev/null; then
        convert -size ${SCREEN_WIDTH}x${SCREEN_HEIGHT} gradient:'#2c3e50-#4a6fa5' /tmp/background.png 2>/dev/null || true
        feh --bg-fill /tmp/background.png 2>/dev/null || true
    else
        feh --bg-fill /usr/share/backgrounds/*.png 2>/dev/null || true
    fi
elif command -v xsetroot &> /dev/null; then
    xsetroot -solid "#2c3e50"
fi

# Start desktop icons manager
# Try PCManFM first (more reliable in containers), then nemo
DESKTOP_MANAGER_STARTED=false

if command -v pcmanfm &> /dev/null; then
    echo "Starting PCManFM desktop..."
    pcmanfm --desktop --profile default &
    sleep 1
    DESKTOP_MANAGER_STARTED=true
fi

if [ "$DESKTOP_MANAGER_STARTED" = false ] && command -v nemo &> /dev/null; then
    echo "Starting Nemo desktop..."
    gsettings set org.nemo.desktop show-desktop-icons true 2>/dev/null || true
    nemo-desktop &
    sleep 1
fi

# Start Nautilus file manager daemon (GNOME's file manager, used by xdg-open)
if command -v nautilus &> /dev/null; then
    echo "Starting Nautilus daemon..."
    nautilus --daemon &
fi

# Start tint2 panel/taskbar
if command -v tint2 &> /dev/null; then
    echo "Starting tint2 panel..."
    tint2 &
    sleep 1
fi

# Enable accessibility
gsettings set org.gnome.desktop.interface toolkit-accessibility true 2>/dev/null || true

# Set GNOME theme to match OSWorld VMs
gsettings set org.gnome.desktop.interface gtk-theme 'Adwaita' 2>/dev/null || true
gsettings set org.gnome.desktop.interface icon-theme 'Adwaita' 2>/dev/null || true
gsettings set org.gnome.desktop.wm.preferences theme 'Adwaita' 2>/dev/null || true

# Set up Chrome remote debugging port forwarding (1337 -> 9222)
if command -v socat &> /dev/null; then
    echo "Setting up Chrome port forwarding (1337 -> 9222)..."
    socat TCP-LISTEN:1337,fork,reuseaddr TCP:localhost:9222 &
fi

# Give services time to initialize
sleep 2

echo ""
echo "Desktop environment ready!"
echo "============================================"
echo "Desktop: GNOME (same as OSWorld VMs)"
echo "Theme: Adwaita"
echo "Resolution: ${SCREEN_WIDTH}x${SCREEN_HEIGHT}"
echo ""
echo "Installed applications:"
echo "  - Chromium (with remote debugging on port 9222)"
echo "  - LibreOffice (Writer, Calc, Impress)"
echo "  - GIMP"
echo "  - VLC (HTTP interface on port 8080)"
echo "  - Nautilus File Manager"
echo "  - GNOME Terminal"
echo ""
echo "Starting FastAPI server on port 5000..."
echo "============================================"

# Handle shutdown gracefully
cleanup() {
    echo ""
    echo "Shutting down..."
    pkill -f socat 2>/dev/null || true
    pkill -f gnome 2>/dev/null || true
    pkill -f mutter 2>/dev/null || true
    pkill -f metacity 2>/dev/null || true
    pkill -f nautilus 2>/dev/null || true
    [ -n "$DESKTOP_PID" ] && kill $DESKTOP_PID 2>/dev/null || true
    kill $XVFB_PID 2>/dev/null || true
    exit 0
}

trap cleanup SIGTERM SIGINT

# Start the FastAPI server
exec python3 -m uvicorn main:app --host 0.0.0.0 --port 5000

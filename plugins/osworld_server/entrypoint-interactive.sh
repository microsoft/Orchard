#!/bin/bash
# OSWorld FastAPI Server Entrypoint - Interactive Mode
# Starts Xvfb, VNC server, noVNC, GNOME desktop, and the FastAPI server
# Allows direct GUI interaction via VNC or web browser

set -e

# Configuration
DISPLAY_NUM="${DISPLAY_NUM:-99}"
SCREEN_WIDTH="${SCREEN_WIDTH:-1920}"
SCREEN_HEIGHT="${SCREEN_HEIGHT:-1080}"
SCREEN_DEPTH="${SCREEN_DEPTH:-24}"
VNC_PORT="${VNC_PORT:-5900}"
NOVNC_PORT="${NOVNC_PORT:-6080}"
VNC_PASSWORD="${VNC_PASSWORD:-osworld}"

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
echo "OSWorld FastAPI Server - Interactive Mode"
echo "============================================"
echo "Display: $DISPLAY"
echo "Screen: ${SCREEN_WIDTH}x${SCREEN_HEIGHT}x${SCREEN_DEPTH}"
echo "VNC Port: $VNC_PORT (password: $VNC_PASSWORD)"
echo "noVNC Port: $NOVNC_PORT (web browser)"
echo ""

# Kill any existing processes
pkill -f "Xvfb :${DISPLAY_NUM}" 2>/dev/null || true
pkill -f x11vnc 2>/dev/null || true
pkill -f websockify 2>/dev/null || true
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

# Start VNC server
echo "Starting VNC server on port $VNC_PORT..."
x11vnc -display ":${DISPLAY_NUM}" \
    -rfbport $VNC_PORT \
    -shared \
    -forever \
    -nopw \
    -xkb \
    -noxrecord \
    -noxfixes \
    -noxdamage \
    -cursor arrow \
    -bg \
    -o /tmp/x11vnc.log
sleep 1

# Verify VNC is running
if pgrep -x x11vnc > /dev/null; then
    echo "VNC server started successfully"
else
    echo "WARNING: VNC server may have failed to start"
    cat /tmp/x11vnc.log 2>/dev/null || true
fi

# Start noVNC (web-based VNC client)
echo "Starting noVNC web server on port $NOVNC_PORT..."

# Find noVNC installation path
NOVNC_PATH=""
if [ -d "/usr/share/novnc" ]; then
    NOVNC_PATH="/usr/share/novnc"
elif [ -d "/usr/share/webapps/novnc" ]; then
    NOVNC_PATH="/usr/share/webapps/novnc"
fi

if [ -n "$NOVNC_PATH" ]; then
    # Start websockify to bridge WebSocket to VNC
    websockify --web=$NOVNC_PATH $NOVNC_PORT localhost:$VNC_PORT &
    NOVNC_PID=$!
    sleep 1
    
    if kill -0 $NOVNC_PID 2>/dev/null; then
        echo "noVNC started successfully (PID: $NOVNC_PID)"
    else
        echo "WARNING: noVNC may have failed to start"
    fi
else
    echo "WARNING: noVNC not found, web interface disabled"
fi

# Start D-Bus session daemon
echo "Starting D-Bus session..."
export DBUS_SESSION_BUS_ADDRESS=$(dbus-daemon --session --fork --print-address)
echo "D-Bus address: $DBUS_SESSION_BUS_ADDRESS"

# Start AT-SPI registry (accessibility)
echo "Starting AT-SPI registry..."
if [ -f /usr/libexec/at-spi2-registryd ]; then
    /usr/libexec/at-spi2-registryd &
elif [ -f /usr/lib/at-spi2-core/at-spi2-registryd ]; then
    /usr/lib/at-spi2-core/at-spi2-registryd &
fi
sleep 1

# Start GNOME-compatible window manager
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

# Fallback to metacity
if [ -z "$DESKTOP_PID" ] || ! kill -0 $DESKTOP_PID 2>/dev/null; then
    if command -v metacity &> /dev/null; then
        echo "Starting Metacity window manager..."
        metacity --replace --sm-disable &
        DESKTOP_PID=$!
        sleep 1
        echo "Metacity started (PID: $DESKTOP_PID)"
    fi
fi

# Start GNOME settings daemon
if command -v /usr/libexec/gsd-xsettings &> /dev/null; then
    echo "Starting GNOME settings daemon..."
    /usr/libexec/gsd-xsettings &
fi

# Set desktop background
echo "Setting desktop background..."
if command -v feh &> /dev/null; then
    # Create a simple gradient background image
    if command -v convert &> /dev/null; then
        convert -size ${SCREEN_WIDTH}x${SCREEN_HEIGHT} gradient:'#2c3e50-#4a6fa5' /tmp/background.png 2>/dev/null || true
        feh --bg-fill /tmp/background.png 2>/dev/null || true
    else
        feh --bg-fill /usr/share/backgrounds/*.png 2>/dev/null || \
        feh --bg-fill /usr/share/backgrounds/*.jpg 2>/dev/null || true
    fi
elif command -v xsetroot &> /dev/null; then
    xsetroot -solid "#2c3e50"
fi

# Start desktop icons manager
# Try PCManFM first (more reliable in containers), then nemo, then nautilus
DESKTOP_MANAGER_STARTED=false

if command -v pcmanfm &> /dev/null; then
    echo "Starting PCManFM desktop..."
    pcmanfm --desktop --profile default &
    sleep 1
    DESKTOP_MANAGER_STARTED=true
    echo "PCManFM desktop started"
fi

if [ "$DESKTOP_MANAGER_STARTED" = false ] && command -v nemo &> /dev/null; then
    echo "Starting Nemo desktop..."
    gsettings set org.nemo.desktop show-desktop-icons true 2>/dev/null || true
    nemo-desktop &
    sleep 1
    DESKTOP_MANAGER_STARTED=true
    echo "Nemo desktop started"
fi

# Start Nautilus daemon for file management (used by xdg-open)
if command -v nautilus &> /dev/null; then
    nautilus --daemon &
fi

# Start tint2 panel/taskbar for a more complete desktop experience
if command -v tint2 &> /dev/null; then
    echo "Starting tint2 panel..."
    tint2 &
    sleep 1
fi

# Enable accessibility
gsettings set org.gnome.desktop.interface toolkit-accessibility true 2>/dev/null || true

# Set GNOME theme
gsettings set org.gnome.desktop.interface gtk-theme 'Adwaita' 2>/dev/null || true
gsettings set org.gnome.desktop.interface icon-theme 'Adwaita' 2>/dev/null || true
gsettings set org.gnome.desktop.wm.preferences theme 'Adwaita' 2>/dev/null || true

# Set up Chrome remote debugging port forwarding
if command -v socat &> /dev/null; then
    echo "Setting up Chrome port forwarding (1337 -> 9222)..."
    socat TCP-LISTEN:1337,fork,reuseaddr TCP:localhost:9222 &
fi

# Give services time to initialize
sleep 2

echo ""
echo "============================================"
echo "Interactive Desktop Ready!"
echo "============================================"
echo ""
echo "Remote Desktop Access:"
echo "  VNC:   vnc://localhost:$VNC_PORT"
echo "  Web:   http://localhost:$NOVNC_PORT/vnc.html"
echo ""
echo "On macOS, connect with:"
echo "  open vnc://localhost:$VNC_PORT"
echo ""
echo "On Linux, connect with:"
echo "  vncviewer localhost:$VNC_PORT"
echo ""
echo "Or open in browser:"
echo "  http://localhost:$NOVNC_PORT/vnc.html"
echo ""
echo "API Ports:"
echo "  FastAPI:  http://localhost:5000"
echo "  Chrome:   http://localhost:9222"
echo "  VLC:      http://localhost:8080"
echo ""
echo "Starting FastAPI server..."
echo "============================================"

# Handle shutdown gracefully
cleanup() {
    echo ""
    echo "Shutting down..."
    pkill -f websockify 2>/dev/null || true
    pkill -f x11vnc 2>/dev/null || true
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


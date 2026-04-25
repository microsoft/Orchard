#!/bin/bash
# OSWorld FastAPI Server Startup Script
# 
# This script provides different ways to start the server:
# - With X11 display (for running on a machine with a display)
# - Headless with Xvfb (for servers without displays)
# - Direct Python execution (for development)

set -e

MODE="${1:-direct}"
PORT="${2:-5000}"

echo "============================================"
echo "OSWorld FastAPI Server"
echo "============================================"
echo "Mode: $MODE"
echo "Port: $PORT"
echo ""

case "$MODE" in
    "direct")
        echo "Starting server directly..."
        python3 -m uvicorn main:app --host 0.0.0.0 --port "$PORT" --reload
        ;;
    
    "headless")
        echo "Starting server in headless mode with Xvfb..."
        
        # Check if Xvfb is available
        if ! command -v Xvfb &> /dev/null; then
            echo "Error: Xvfb is not installed. Install it with: sudo apt-get install xvfb"
            exit 1
        fi
        
        # Kill any existing Xvfb on display :99
        pkill -f "Xvfb :99" 2>/dev/null || true
        
        # Start Xvfb
        Xvfb :99 -screen 0 1920x1080x24 &
        XVFB_PID=$!
        export DISPLAY=:99
        
        echo "Xvfb started on display :99 (PID: $XVFB_PID)"
        
        # Start D-Bus if available
        if command -v dbus-daemon &> /dev/null; then
            eval $(dbus-launch --sh-syntax)
            echo "D-Bus started"
        fi
        
        # Start AT-SPI registry if available
        if command -v /usr/libexec/at-spi2-registryd &> /dev/null; then
            /usr/libexec/at-spi2-registryd &
            echo "AT-SPI registry started"
        fi
        
        # Wait for display to be ready
        sleep 2
        
        echo "Starting FastAPI server..."
        python3 -m uvicorn main:app --host 0.0.0.0 --port "$PORT"
        
        # Cleanup on exit
        kill $XVFB_PID 2>/dev/null || true
        ;;
    
    "docker-ubuntu")
        echo "Building and starting Ubuntu Docker container..."
        docker-compose up --build osworld-ubuntu
        ;;
    
    "docker-headless")
        echo "Building and starting headless Ubuntu Docker container..."
        docker-compose up --build osworld-ubuntu-headless
        ;;
    
    "docker-windows")
        echo "Building and starting Windows Docker container..."
        echo "Note: This requires Docker Desktop in Windows containers mode"
        docker-compose up --build osworld-windows
        ;;
    
    *)
        echo "Usage: $0 [mode] [port]"
        echo ""
        echo "Modes:"
        echo "  direct         - Run directly with Python (default)"
        echo "  headless       - Run with Xvfb virtual display"
        echo "  docker-ubuntu  - Run in Ubuntu Docker container"
        echo "  docker-headless - Run in headless Ubuntu Docker container"
        echo "  docker-windows - Run in Windows Docker container"
        echo ""
        echo "Examples:"
        echo "  $0                    # Run directly on port 5000"
        echo "  $0 direct 8000        # Run directly on port 8000"
        echo "  $0 headless           # Run headless on port 5000"
        echo "  $0 docker-ubuntu      # Run in Ubuntu container"
        exit 1
        ;;
esac


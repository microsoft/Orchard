# OSWorld FastAPI Server

A containerized FastAPI server for OS operations emulation with a full GNOME desktop environment. Supports screenshots, command execution, mouse/keyboard actions, accessibility tree, and more.

## Features

- **Full GNOME Desktop**: Complete desktop environment matching OSWorld VMs
- **Screenshot Capture**: Take screenshots with cursor overlay
- **Command Execution**: Run shell commands, Python scripts, and Bash scripts
- **Mouse/Keyboard Actions**: Full PyAutoGUI support for automation
- **Accessibility Tree**: Get the accessibility tree for UI analysis (AT-SPI)
- **File Operations**: Upload, download, and manage files
- **Window Management**: Activate, close, and manage windows
- **Pre-installed Applications**: Chromium, LibreOffice, GIMP, VLC, gedit, and more

## Quick Start

### Headless Mode (API Only - Recommended for Automation)

```bash
# Build and run with Docker Compose
docker-compose up osworld

# Or build manually
docker build -f Dockerfile.ubuntu -t osworld-server .
docker run -p 5000:5000 -p 9222:9222 -p 8080:8080 --cap-add SYS_ADMIN osworld-server
```

### Interactive Mode (with VNC Remote Desktop)

View and interact with the Ubuntu desktop directly via VNC or web browser:

```bash
# Build and run interactive mode
docker-compose up osworld-interactive
```

**Access the desktop:**
- **VNC Client**: `vnc://localhost:5900` (no password)
- **Web Browser**: http://localhost:6080/vnc.html (click "Connect")

On macOS, you can use the built-in Screen Sharing:
```bash
open vnc://localhost:5900
```

### Test the Environment

```bash
# Basic connectivity test
./run_test.sh

# Full application test (tests all installed apps)
./run_test_full.sh
```

## Pre-installed Applications

The Docker container includes all applications needed for OSWorld compatibility:

| Application | Description | Remote Control |
|-------------|-------------|----------------|
| **Chromium** | Web browser | Port 9222 (Chrome DevTools) |
| **LibreOffice** | Office suite (Writer, Calc, Impress) | - |
| **GIMP** | Image editor | - |
| **VLC** | Media player | Port 8080 (HTTP interface) |
| **Nautilus** | File manager | - |
| **GNOME Terminal** | Terminal emulator | - |
| **gedit** | Text editor | - |

## Ports

| Port | Description | Mode |
|------|-------------|------|
| `5000` | FastAPI server | All |
| `5900` | VNC server (remote desktop) | Interactive only |
| `6080` | noVNC web interface | Interactive only |
| `9222` | Chromium Remote Debugging | All |
| `8080` | VLC HTTP Interface | All |

## Remote Desktop Access (Interactive Mode)

When using `osworld-interactive`, you can view and control the Ubuntu desktop:

### VNC Client (Recommended)

Connect using any VNC client (RealVNC, TigerVNC, Remmina):

```
vnc://localhost:5900
```

**macOS** (built-in Screen Sharing):
```bash
open vnc://localhost:5900
```

**Linux**:
```bash
vncviewer localhost:5900
# or
remmina -c vnc://localhost:5900
```

**Windows**:
- Use RealVNC Viewer, TightVNC, or TigerVNC
- Connect to: `localhost:5900`

### Web Browser (noVNC)

No VNC client needed - works in any browser:

```
http://localhost:6080/vnc.html
```

Click "Connect" to view the desktop.

## API Endpoints

### Health & Info

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Health check and server info |
| `/platform` | GET | Get the operating system |
| `/screen_size` | POST | Get screen dimensions |
| `/cursor_position` | GET | Get current cursor position |

### Screenshot & Display

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/screenshot` | GET | Capture screenshot with cursor |
| `/accessibility` | GET | Get accessibility tree (XML) |
| `/terminal` | GET | Get terminal output (Linux) |

### Command Execution

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/execute` | POST | Execute shell command |
| `/execute/action` | POST | Execute PyAutoGUI action |
| `/execute/python` | POST | Run Python script |
| `/execute/bash` | POST | Run Bash script |
| `/setup/launch` | POST | Launch an application |

### File Operations

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/file` | POST | Download a file |
| `/setup/upload` | POST | Upload a file |
| `/setup/download_file` | POST | Download from URL |
| `/list_directory` | POST | List directory contents |

### Window Management

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/setup/open_file` | POST | Open file with default app |
| `/setup/activate_window` | POST | Bring window to front |
| `/setup/close_window` | POST | Close a window |
| `/setup/change_wallpaper` | POST | Change desktop wallpaper |

### Recording

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/start_recording` | POST | Start screen recording |
| `/end_recording` | POST | Stop and get recording |

## Usage Examples

### Using the Python Client

```python
from client import OSWorldClient

# Connect to the server
client = OSWorldClient("http://localhost:5000")

# Take a screenshot
screenshot = client.screenshot("screenshot.png")

# Click at a position
client.click(x=100, y=200)

# Type text
client.type_text("Hello World")

# Press a key
client.press("enter")

# Hotkey combination
client.hotkey("ctrl", "c")

# Execute a command
result = client.execute(["ls", "-la"])
print(result["output"])

# Run Python code
result = client.run_python("print(2 + 2)")
print(result["output"])  # "4"

# Get accessibility tree
a11y_tree = client.get_accessibility_tree()
```

### Using curl

```bash
# Take screenshot
curl http://localhost:5000/screenshot -o screenshot.png

# Execute command
curl -X POST http://localhost:5000/execute \
  -H "Content-Type: application/json" \
  -d '{"command": ["echo", "Hello"], "shell": false}'

# Click at position
curl -X POST http://localhost:5000/execute/action \
  -H "Content-Type: application/json" \
  -d '{"action_type": "CLICK", "parameters": {"x": 100, "y": 200}}'

# Type text
curl -X POST http://localhost:5000/execute/action \
  -H "Content-Type: application/json" \
  -d '{"action_type": "TYPING", "parameters": {"text": "Hello World"}}'

# Launch an application
curl -X POST http://localhost:5000/setup/launch \
  -H "Content-Type: application/json" \
  -d '{"command": ["nautilus", "/home/user"], "shell": false}'

# Run Python code
curl -X POST http://localhost:5000/execute/python \
  -H "Content-Type: application/json" \
  -d '{"code": "print(2 + 2)"}'
```

## Action Types

The `/execute/action` endpoint supports these action types:

| Action Type | Parameters | Description |
|------------|------------|-------------|
| `MOVE_TO` | `x`, `y` | Move cursor to position |
| `CLICK` | `x`, `y`, `button`, `num_clicks` | Click at position |
| `DOUBLE_CLICK` | `x`, `y` | Double-click |
| `RIGHT_CLICK` | `x`, `y` | Right-click |
| `MOUSE_DOWN` | `button` | Press mouse button |
| `MOUSE_UP` | `button` | Release mouse button |
| `DRAG_TO` | `x`, `y` | Drag to position |
| `SCROLL` | `dx`, `dy` | Scroll mouse wheel |
| `TYPING` | `text` | Type text |
| `PRESS` | `key` | Press and release key |
| `KEY_DOWN` | `key` | Press key |
| `KEY_UP` | `key` | Release key |
| `HOTKEY` | `keys` (list) | Press key combination |

## Docker Configuration

### Available Services

| Service | Dockerfile | Description |
|---------|------------|-------------|
| `osworld` | `Dockerfile.ubuntu` | Headless mode (API only) |
| `osworld-interactive` | `Dockerfile.ubuntu-interactive` | With VNC remote desktop |
| `osworld-x11` | `Dockerfile.ubuntu` | Use host X11 display |
| `osworld-windows` | `Dockerfile.windows` | Windows (requires Windows containers) |

### Example: Interactive Mode

```yaml
services:
  osworld-interactive:
    build:
      context: .
      dockerfile: Dockerfile.ubuntu-interactive
    ports:
      - "5000:5000"   # FastAPI server
      - "5900:5900"   # VNC remote desktop
      - "6080:6080"   # noVNC web interface
      - "9222:9222"   # Chromium remote debugging
      - "8080:8080"   # VLC HTTP interface
    cap_add:
      - SYS_ADMIN
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DISPLAY_NUM` | `99` | X11 display number |
| `SCREEN_WIDTH` | `1920` | Screen width |
| `SCREEN_HEIGHT` | `1080` | Screen height |

## User Account

The container creates a user account for desktop operations:

- **Username**: `user`
- **Password**: `password`
- **Home**: `/home/user`

Standard directories are created: `Desktop`, `Documents`, `Downloads`, `Pictures`, `Videos`, `Music`.

## Troubleshooting

### Docker Build Issues

If you encounter GPG signature errors during build:

```bash
# Clear Docker build cache
docker builder prune -f

# Rebuild without cache
docker-compose build --no-cache osworld
```

### Accessibility Tree Empty

Ensure the container is running with proper permissions:

```bash
docker run --privileged --cap-add SYS_ADMIN ...
```

### View Container Logs

```bash
docker-compose logs -f osworld
```

### Access Container Shell

```bash
# Exec into running container
docker exec -it osworld_server-osworld-1 bash

# Or start with shell for debugging
docker-compose run --rm osworld bash
```

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Docker Container                      │
│  ┌─────────────────────────────────────────────────┐   │
│  │             GNOME Desktop (Mutter WM)            │   │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐           │   │
│  │  │Chromium │ │LibreOfc │ │  GIMP   │  ...      │   │
│  │  └─────────┘ └─────────┘ └─────────┘           │   │
│  └─────────────────────────────────────────────────┘   │
│                          │                               │
│  ┌─────────────────────────────────────────────────┐   │
│  │               FastAPI Server (:5000)             │   │
│  │  ┌─────────────┐  ┌─────────────┐              │   │
│  │  │ Screenshot  │  │  Execute    │              │   │
│  │  │  Endpoint   │  │  Endpoint   │              │   │
│  │  └──────┬──────┘  └──────┬──────┘              │   │
│  │         │                 │                      │   │
│  │         ▼                 ▼                      │   │
│  │  ┌─────────────────────────────────────────┐   │   │
│  │  │         PyAutoGUI / AT-SPI / wmctrl      │   │   │
│  │  └─────────────────────────────────────────┘   │   │
│  └─────────────────────────────────────────────────┘   │
│                          │                               │
│  ┌─────────────────────────────────────────────────┐   │
│  │              Xvfb Virtual Display (:99)          │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
              ┌─────────────────────────┐
              │      Client (HTTP)       │
              │  - Python Client         │
              │  - curl                  │
              │  - Any HTTP client       │
              └─────────────────────────┘
```

## Development

### API Documentation

When the server is running, visit:
- Swagger UI: http://localhost:5000/docs
- ReDoc: http://localhost:5000/redoc

### Test Scripts

- `run_test.sh` - Basic connectivity and endpoint test
- `run_test_full.sh` - Full application compatibility test

## License

See the main OSWorld project for license information.

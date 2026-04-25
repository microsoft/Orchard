"""
OSWorld FastAPI Client - Example client for interacting with the OSWorld server.

This client demonstrates how to:
- Take screenshots
- Execute commands
- Perform mouse/keyboard actions
- Get accessibility tree
- Run Python/Bash scripts
- Manage files

Usage:
    from client import OSWorldClient
    
    client = OSWorldClient("http://localhost:5000")
    screenshot = client.screenshot()
    client.click(x=100, y=200)
    client.type_text("Hello World")
"""

import base64
import json
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import requests


class OSWorldClient:
    """Client for interacting with the OSWorld FastAPI server."""

    def __init__(self, base_url: str = "http://localhost:5000", timeout: int = 30):
        """
        Initialize the OSWorld client.
        
        Args:
            base_url: The base URL of the OSWorld server (e.g., "http://localhost:5000")
            timeout: Default timeout for requests in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()

    def _request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        """Make a request to the server."""
        url = f"{self.base_url}{endpoint}"
        kwargs.setdefault("timeout", self.timeout)
        response = self._session.request(method, url, **kwargs)
        return response

    def _get(self, endpoint: str, **kwargs) -> requests.Response:
        return self._request("GET", endpoint, **kwargs)

    def _post(self, endpoint: str, **kwargs) -> requests.Response:
        return self._request("POST", endpoint, **kwargs)

    # ========================================================================
    # Health & Info
    # ========================================================================

    def health_check(self) -> Dict[str, Any]:
        """Check if the server is running."""
        response = self._get("/")
        response.raise_for_status()
        return response.json()

    def get_platform(self) -> str:
        """Get the server's operating system platform."""
        response = self._get("/platform")
        response.raise_for_status()
        return response.json()["platform"]

    def get_screen_size(self) -> Dict[str, int]:
        """Get the screen size (width and height)."""
        response = self._post("/screen_size")
        response.raise_for_status()
        return response.json()

    def get_cursor_position(self) -> Dict[str, int]:
        """Get the current cursor position."""
        response = self._get("/cursor_position")
        response.raise_for_status()
        return response.json()

    # ========================================================================
    # Screenshot & Display
    # ========================================================================

    def screenshot(self, save_path: Optional[str] = None) -> bytes:
        """
        Take a screenshot.
        
        Args:
            save_path: Optional path to save the screenshot
            
        Returns:
            Screenshot as bytes (PNG format)
        """
        response = self._get("/screenshot")
        response.raise_for_status()
        
        screenshot_bytes = response.content
        
        if save_path:
            Path(save_path).write_bytes(screenshot_bytes)
        
        return screenshot_bytes

    # ========================================================================
    # Accessibility
    # ========================================================================

    def get_accessibility_tree(self) -> str:
        """Get the accessibility tree of the desktop."""
        response = self._get("/accessibility")
        response.raise_for_status()
        return response.json()["AT"]

    def get_terminal_output(self) -> Optional[str]:
        """Get the terminal output (Linux only)."""
        response = self._get("/terminal")
        response.raise_for_status()
        return response.json().get("output")

    # ========================================================================
    # Command Execution
    # ========================================================================

    def execute(self, command: Union[str, List[str]], shell: bool = False) -> Dict[str, Any]:
        """
        Execute a shell command.
        
        Args:
            command: Command to execute (string or list of arguments)
            shell: Whether to run in shell mode
            
        Returns:
            Dict with status, output, error, and returncode
        """
        response = self._post("/execute", json={"command": command, "shell": shell})
        response.raise_for_status()
        return response.json()

    def run_python(self, code: str) -> Dict[str, Any]:
        """
        Execute Python code on the server.
        
        Args:
            code: Python code to execute
            
        Returns:
            Dict with status, output, error, and return_code
        """
        response = self._post("/execute/python", json={"code": code})
        response.raise_for_status()
        return response.json()

    def run_bash(self, script: str, timeout: int = 100, 
                 working_dir: Optional[str] = None) -> Dict[str, Any]:
        """
        Execute a Bash script on the server.
        
        Args:
            script: Bash script content
            timeout: Execution timeout in seconds
            working_dir: Working directory for script execution
            
        Returns:
            Dict with status, output, error, and returncode
        """
        payload = {"script": script, "timeout": timeout}
        if working_dir:
            payload["working_dir"] = working_dir
            
        response = self._post("/execute/bash", json=payload)
        response.raise_for_status()
        return response.json()

    # ========================================================================
    # Mouse Actions
    # ========================================================================

    def _action(self, action_type: str, **parameters) -> Dict[str, Any]:
        """Execute a PyAutoGUI action."""
        response = self._post("/execute/action", json={
            "action_type": action_type,
            "parameters": parameters
        })
        response.raise_for_status()
        return response.json()

    def move_to(self, x: float, y: float) -> Dict[str, Any]:
        """Move the cursor to the specified position."""
        return self._action("MOVE_TO", x=x, y=y)

    def click(self, x: Optional[float] = None, y: Optional[float] = None,
              button: str = "left", clicks: int = 1) -> Dict[str, Any]:
        """
        Click at the specified position.
        
        Args:
            x: X coordinate (optional, uses current position if not specified)
            y: Y coordinate (optional, uses current position if not specified)
            button: Mouse button ("left", "right", "middle")
            clicks: Number of clicks
        """
        params = {"button": button, "num_clicks": clicks}
        if x is not None:
            params["x"] = x
        if y is not None:
            params["y"] = y
        return self._action("CLICK", **params)

    def double_click(self, x: Optional[float] = None, y: Optional[float] = None) -> Dict[str, Any]:
        """Double-click at the specified position."""
        params = {}
        if x is not None:
            params["x"] = x
        if y is not None:
            params["y"] = y
        return self._action("DOUBLE_CLICK", **params)

    def right_click(self, x: Optional[float] = None, y: Optional[float] = None) -> Dict[str, Any]:
        """Right-click at the specified position."""
        params = {}
        if x is not None:
            params["x"] = x
        if y is not None:
            params["y"] = y
        return self._action("RIGHT_CLICK", **params)

    def drag_to(self, x: float, y: float) -> Dict[str, Any]:
        """Drag the cursor to the specified position."""
        return self._action("DRAG_TO", x=x, y=y)

    def scroll(self, dx: int = 0, dy: int = 0) -> Dict[str, Any]:
        """
        Scroll the mouse wheel.
        
        Args:
            dx: Horizontal scroll amount
            dy: Vertical scroll amount
        """
        return self._action("SCROLL", dx=dx, dy=dy)

    def mouse_down(self, button: str = "left") -> Dict[str, Any]:
        """Press down a mouse button."""
        return self._action("MOUSE_DOWN", button=button)

    def mouse_up(self, button: str = "left") -> Dict[str, Any]:
        """Release a mouse button."""
        return self._action("MOUSE_UP", button=button)

    # ========================================================================
    # Keyboard Actions
    # ========================================================================

    def type_text(self, text: str) -> Dict[str, Any]:
        """
        Type text using the keyboard.
        
        Note: This uses typewrite which types character by character.
        """
        return self._action("TYPING", text=text)

    def press(self, key: str) -> Dict[str, Any]:
        """Press and release a key."""
        return self._action("PRESS", key=key)

    def key_down(self, key: str) -> Dict[str, Any]:
        """Press down a key."""
        return self._action("KEY_DOWN", key=key)

    def key_up(self, key: str) -> Dict[str, Any]:
        """Release a key."""
        return self._action("KEY_UP", key=key)

    def hotkey(self, *keys: str) -> Dict[str, Any]:
        """
        Press a key combination.
        
        Example:
            client.hotkey("ctrl", "c")  # Copy
            client.hotkey("alt", "tab")  # Switch window
        """
        return self._action("HOTKEY", keys=list(keys))

    # ========================================================================
    # File Operations
    # ========================================================================

    def get_file(self, file_path: str, save_path: Optional[str] = None) -> bytes:
        """
        Download a file from the server.
        
        Args:
            file_path: Path to the file on the server
            save_path: Optional local path to save the file
            
        Returns:
            File content as bytes
        """
        response = self._post("/file", data={"file_path": file_path})
        response.raise_for_status()
        
        content = response.content
        
        if save_path:
            Path(save_path).write_bytes(content)
        
        return content

    def upload_file(self, local_path: str, remote_path: str) -> Dict[str, Any]:
        """
        Upload a file to the server.
        
        Args:
            local_path: Path to the local file
            remote_path: Path where to save the file on the server
            
        Returns:
            Dict with status and message
        """
        with open(local_path, "rb") as f:
            files = {"file_data": f}
            data = {"file_path": remote_path}
            response = self._post("/setup/upload", data=data, files=files)
        
        response.raise_for_status()
        return response.json()

    def download_url(self, url: str, save_path: str) -> Dict[str, Any]:
        """
        Download a file from a URL to the server.
        
        Args:
            url: URL to download from
            save_path: Path where to save the file on the server
            
        Returns:
            Dict with status and message
        """
        response = self._post("/setup/download_file", json={"url": url, "path": save_path})
        response.raise_for_status()
        return response.json()

    def list_directory(self, path: str) -> Dict[str, Any]:
        """
        List the contents of a directory on the server.
        
        Args:
            path: Directory path to list
            
        Returns:
            Dict with directory tree structure
        """
        response = self._post("/list_directory", json={"path": path})
        response.raise_for_status()
        return response.json()

    # ========================================================================
    # Application & Window Management
    # ========================================================================

    def launch_app(self, command: Union[str, List[str]], shell: bool = False) -> Dict[str, Any]:
        """
        Launch an application.
        
        Args:
            command: Command to launch the application
            shell: Whether to run in shell mode
            
        Returns:
            Dict with status and message
        """
        response = self._post("/setup/launch", json={"command": command, "shell": shell})
        response.raise_for_status()
        return response.json()

    def open_file(self, path: str) -> Dict[str, Any]:
        """
        Open a file with its default application.
        
        Args:
            path: Path to the file to open
            
        Returns:
            Dict with status and message
        """
        response = self._post("/setup/open_file", json={"path": path})
        response.raise_for_status()
        return response.json()

    def activate_window(self, window_name: str, strict: bool = False, 
                       by_class: bool = False) -> Dict[str, Any]:
        """
        Activate (bring to front) a window.
        
        Args:
            window_name: Name of the window to activate
            strict: Use strict matching
            by_class: Match by class name instead of title
            
        Returns:
            Dict with status and message
        """
        response = self._post("/setup/activate_window", json={
            "window_name": window_name,
            "strict": strict,
            "by_class": by_class
        })
        response.raise_for_status()
        return response.json()

    def close_window(self, window_name: str, strict: bool = False,
                    by_class: bool = False) -> Dict[str, Any]:
        """
        Close a window.
        
        Args:
            window_name: Name of the window to close
            strict: Use strict matching
            by_class: Match by class name instead of title
            
        Returns:
            Dict with status and message
        """
        response = self._post("/setup/close_window", json={
            "window_name": window_name,
            "strict": strict,
            "by_class": by_class
        })
        response.raise_for_status()
        return response.json()

    # ========================================================================
    # Desktop Customization
    # ========================================================================

    def change_wallpaper(self, path: str) -> Dict[str, Any]:
        """
        Change the desktop wallpaper.
        
        Args:
            path: Path to the wallpaper image
            
        Returns:
            Dict with status and message
        """
        response = self._post("/setup/change_wallpaper", json={"path": path})
        response.raise_for_status()
        return response.json()

    def get_wallpaper(self, save_path: Optional[str] = None) -> bytes:
        """
        Get the current wallpaper.
        
        Args:
            save_path: Optional path to save the wallpaper
            
        Returns:
            Wallpaper as bytes
        """
        response = self._post("/wallpaper")
        response.raise_for_status()
        
        content = response.content
        
        if save_path:
            Path(save_path).write_bytes(content)
        
        return content

    def get_desktop_path(self) -> str:
        """Get the path to the desktop folder."""
        response = self._post("/desktop_path")
        response.raise_for_status()
        return response.json()["desktop_path"]

    # ========================================================================
    # Recording
    # ========================================================================

    def start_recording(self) -> Dict[str, Any]:
        """Start screen recording (Linux only)."""
        response = self._post("/start_recording")
        response.raise_for_status()
        return response.json()

    def end_recording(self, save_path: Optional[str] = None) -> bytes:
        """
        End screen recording and get the video file.
        
        Args:
            save_path: Optional path to save the video
            
        Returns:
            Video file as bytes
        """
        response = self._post("/end_recording")
        response.raise_for_status()
        
        content = response.content
        
        if save_path:
            Path(save_path).write_bytes(content)
        
        return content


# ============================================================================
# Example Usage
# ============================================================================

def example_usage():
    """Demonstrate how to use the OSWorld client."""
    
    # Create client
    client = OSWorldClient("http://localhost:5000")
    
    # Check server health
    print("1. Checking server health...")
    status = client.health_check()
    print(f"   Server status: {status}")
    
    # Get platform
    print("\n2. Getting platform...")
    platform = client.get_platform()
    print(f"   Platform: {platform}")
    
    # Get screen size
    print("\n3. Getting screen size...")
    size = client.get_screen_size()
    print(f"   Screen size: {size['width']}x{size['height']}")
    
    # Take screenshot
    print("\n4. Taking screenshot...")
    screenshot = client.screenshot("screenshot.png")
    print(f"   Screenshot saved ({len(screenshot)} bytes)")
    
    # Execute a command
    print("\n5. Executing command...")
    result = client.execute(["echo", "Hello from OSWorld!"])
    print(f"   Output: {result['output'].strip()}")
    
    # Move mouse
    print("\n6. Moving mouse to center of screen...")
    client.move_to(size['width'] // 2, size['height'] // 2)
    print("   Mouse moved!")
    
    # Get cursor position
    print("\n7. Getting cursor position...")
    pos = client.get_cursor_position()
    print(f"   Cursor at: ({pos['x']}, {pos['y']})")
    
    # Run Python code
    print("\n8. Running Python code...")
    result = client.run_python("print('Hello from Python!')\nprint(2 + 2)")
    print(f"   Output: {result['output'].strip()}")
    
    print("\n✅ All examples completed successfully!")


if __name__ == "__main__":
    example_usage()


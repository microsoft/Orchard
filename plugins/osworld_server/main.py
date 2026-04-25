"""
OSWorld FastAPI Server - A containerized server for OS operations emulation.

This server provides REST API endpoints for:
- Screenshot capture
- Command execution (shell commands, Python scripts, Bash scripts)
- Accessibility tree retrieval
- File operations
- Mouse/keyboard actions via PyAutoGUI
- Window management
- Screen recording

Designed to run inside a Docker container for direct execution of Windows/Ubuntu actions.
"""

import ctypes
import json
import os
import platform
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
import traceback
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
import concurrent.futures

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
import uvicorn

# Platform detection
platform_name: str = platform.system()

# Flags for available features
HAS_DISPLAY = False
HAS_PYAUTOGUI = False
HAS_ACCESSIBILITY = False
HAS_XCURSOR = False

# Lazy import holders
pyautogui = None
lxml = None
Image = None
ImageGrab = None
Xcursor = None
display = None
X = None
pyatspi = None
Accessible = Any
BaseWrapper = Any
Desktop = None
_Element = Any

def _init_display_imports():
    """Initialize display-related imports. Called lazily when display is needed."""
    global HAS_DISPLAY, HAS_PYAUTOGUI, HAS_ACCESSIBILITY, HAS_XCURSOR
    global pyautogui, lxml, Image, ImageGrab, Xcursor, display, X
    global pyatspi, Accessible, Desktop, BaseWrapper, _Element
    
    if HAS_DISPLAY:
        return True
    
    try:
        # Test if display is available
        if platform_name == "Linux":
            import Xlib
            from Xlib import display as xdisplay, X as xX
            # Try to connect to display
            d = xdisplay.Display()
            d.close()
            display = xdisplay
            X = xX
        
        # Import pyautogui
        import pyautogui as pag
        pyautogui = pag
        pyautogui.PAUSE = 0
        pyautogui.FAILSAFE = False
        HAS_PYAUTOGUI = True
        
        # Import PIL
        from PIL import Image as PILImage
        Image = PILImage
        
        if platform_name == "Windows":
            from PIL import ImageGrab as PILImageGrab
            ImageGrab = PILImageGrab
        
        # Import lxml
        import lxml.etree as lxmletree
        lxml = type('lxml', (), {'etree': lxmletree})()
        from lxml.etree import _Element as LxmlElement
        _Element = LxmlElement
        
        HAS_DISPLAY = True
        print("Display imports initialized successfully")
        
        # Try to import accessibility (Linux only)
        if platform_name == "Linux":
            try:
                import pyatspi as pa
                pyatspi = pa
                from pyatspi import Accessible as Acc
                Accessible = Acc
                HAS_ACCESSIBILITY = True
            except Exception as e:
                print(f"Accessibility imports failed: {e}")
        
        # Try to import pyxcursor (Linux only)
        if platform_name == "Linux":
            try:
                from pyxcursor import Xcursor as XC
                Xcursor = XC
                HAS_XCURSOR = True
            except Exception as e:
                print(f"pyxcursor import failed: {e}")
        
        # Windows-specific imports
        if platform_name == "Windows":
            try:
                from pywinauto import Desktop as WinDesktop
                from pywinauto.base_wrapper import BaseWrapper as WinBaseWrapper
                Desktop = WinDesktop
                BaseWrapper = WinBaseWrapper
            except Exception as e:
                print(f"pywinauto imports failed: {e}")
        
        return True
        
    except Exception as e:
        print(f"Display initialization failed: {e}")
        HAS_DISPLAY = False
        return False


def _require_display():
    """Ensure display is available, raise HTTPException if not."""
    if not _init_display_imports():
        raise HTTPException(
            status_code=503, 
            detail="Display not available. Make sure X server is running or use headless mode with Xvfb."
        )

# Initialize FastAPI app
app = FastAPI(
    title="OSWorld Server",
    description="FastAPI server for OS operations emulation - supports Windows, Ubuntu, and macOS",
    version="1.0.0",
)

# Try to initialize display on startup (non-blocking)
@app.on_event("startup")
async def startup_event():
    """Try to initialize display imports on startup."""
    _init_display_imports()

# Global settings
TIMEOUT = 1800  # seconds
SCREENSHOT_DIR = "/tmp/screenshots"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

# Recording state
recording_process = None
recording_path = "/tmp/recording.mp4"

# Trajectory recording
trajectory_events: List[Dict] = []


# ============================================================================
# Pydantic Models for Request/Response
# ============================================================================

class ExecuteCommandRequest(BaseModel):
    command: Union[str, List[str]] = Field(..., description="Command to execute (string or list)")
    shell: bool = Field(default=False, description="Whether to run command in shell mode")


class ExecuteWithVerificationRequest(BaseModel):
    command: Union[str, List[str]] = Field(..., description="Command to execute")
    shell: bool = Field(default=False)
    verification: Dict[str, Any] = Field(default={}, description="Verification criteria")
    max_wait_time: int = Field(default=10, description="Max wait time in seconds")
    check_interval: float = Field(default=1, description="Check interval in seconds")


class LaunchAppRequest(BaseModel):
    command: Union[str, List[str]] = Field(..., description="Command to launch application")
    shell: bool = Field(default=False)


class RunPythonRequest(BaseModel):
    code: str = Field(..., description="Python code to execute")


class RunBashScriptRequest(BaseModel):
    script: str = Field(..., description="Bash script content")
    timeout: int = Field(default=100, description="Execution timeout in seconds")
    working_dir: Optional[str] = Field(default=None, description="Working directory")


class DownloadFileRequest(BaseModel):
    url: str = Field(..., description="URL to download from")
    path: str = Field(..., description="Local path to save the file")


class OpenFileRequest(BaseModel):
    path: str = Field(..., description="Path to file or application")


class ActivateWindowRequest(BaseModel):
    window_name: str = Field(..., description="Name of window to activate")
    strict: bool = Field(default=False, description="Use strict matching")
    by_class: bool = Field(default=False, description="Match by class name")


class CloseWindowRequest(BaseModel):
    window_name: str = Field(..., description="Name of window to close")
    strict: bool = Field(default=False)
    by_class: bool = Field(default=False)


class ChangeWallpaperRequest(BaseModel):
    path: str = Field(..., description="Path to wallpaper image")


class ListDirectoryRequest(BaseModel):
    path: str = Field(..., description="Directory path to list")


class ActionRequest(BaseModel):
    """Generic action request for PyAutoGUI actions"""
    action_type: str = Field(..., description="Type of action (CLICK, MOVE_TO, TYPING, etc.)")
    parameters: Dict[str, Any] = Field(default={}, description="Action parameters")


# ============================================================================
# Helper Functions
# ============================================================================

def _append_event(event_type: str, data: Dict[str, Any], ts: float = None):
    """Append an event to the trajectory log."""
    trajectory_events.append({
        "type": event_type,
        "data": data,
        "timestamp": ts or time.time()
    })


def _get_machine_architecture() -> str:
    """Get the machine architecture."""
    architecture = platform.machine().lower()
    if architecture in ['amd32', 'amd64', 'x86', 'x86_64', 'x86-64', 'x64', 'i386', 'i686']:
        return 'amd'
    elif architecture in ['arm64', 'aarch64', 'aarch32']:
        return 'arm'
    return 'unknown'


# ============================================================================
# Accessibility Tree Namespace Maps
# ============================================================================

_accessibility_ns_map = {
    "ubuntu": {
        "st": "https://accessibility.ubuntu.example.org/ns/state",
        "attr": "https://accessibility.ubuntu.example.org/ns/attributes",
        "cp": "https://accessibility.ubuntu.example.org/ns/component",
        "doc": "https://accessibility.ubuntu.example.org/ns/document",
        "docattr": "https://accessibility.ubuntu.example.org/ns/document/attributes",
        "txt": "https://accessibility.ubuntu.example.org/ns/text",
        "val": "https://accessibility.ubuntu.example.org/ns/value",
        "act": "https://accessibility.ubuntu.example.org/ns/action",
    },
    "windows": {
        "st": "https://accessibility.windows.example.org/ns/state",
        "attr": "https://accessibility.windows.example.org/ns/attributes",
        "cp": "https://accessibility.windows.example.org/ns/component",
        "doc": "https://accessibility.windows.example.org/ns/document",
        "docattr": "https://accessibility.windows.example.org/ns/document/attributes",
        "txt": "https://accessibility.windows.example.org/ns/text",
        "val": "https://accessibility.windows.example.org/ns/value",
        "act": "https://accessibility.windows.example.org/ns/action",
        "class": "https://accessibility.windows.example.org/ns/class",
        "cnt": "https://accessibility.windows.example.org/ns/count",
        "cols": "https://accessibility.windows.example.org/ns/columns",
        "id": "https://accessibility.windows.example.org/ns/id",
    },
    "macos": {
        "st": "https://accessibility.macos.example.org/ns/state",
        "attr": "https://accessibility.macos.example.org/ns/attributes",
        "cp": "https://accessibility.macos.example.org/ns/component",
        "doc": "https://accessibility.macos.example.org/ns/document",
        "txt": "https://accessibility.macos.example.org/ns/text",
        "val": "https://accessibility.macos.example.org/ns/value",
        "act": "https://accessibility.macos.example.org/ns/action",
        "role": "https://accessibility.macos.example.org/ns/role",
    }
}

_accessibility_ns_map_ubuntu = _accessibility_ns_map['ubuntu']
_accessibility_ns_map_windows = _accessibility_ns_map['windows']
_accessibility_ns_map_macos = _accessibility_ns_map['macos']

# A11y tree constants
MAX_DEPTH = 50
MAX_WIDTH = 1024
MAX_CALLS = 5000
libreoffice_version_tuple: Optional[Tuple[int, ...]] = None


# ============================================================================
# Linux-specific Accessibility Tree Functions
# ============================================================================

if platform_name == "Linux":
    def _get_libreoffice_version() -> Tuple[int, ...]:
        """Get the LibreOffice version as a tuple of integers."""
        result = subprocess.run("libreoffice --version", shell=True, text=True, stdout=subprocess.PIPE)
        version_str = result.stdout.split()[1]
        return tuple(map(int, version_str.split(".")))

    def _has_active_terminal(desktop: Accessible) -> bool:
        """Check whether the terminal window is open and active."""
        for app in desktop:
            if app.getRoleName() == "application" and app.name == "gnome-terminal-server":
                for frame in app:
                    if frame.getRoleName() == "frame" and frame.getState().contains(pyatspi.STATE_ACTIVE):
                        return True
        return False

    def _create_atspi_node(node: Accessible, depth: int = 0, flag: Optional[str] = None) -> _Element:
        """Create an XML node from an AT-SPI accessible object."""
        node_name = node.name
        attribute_dict: Dict[str, Any] = {"name": node_name}

        # States
        states: List[StateType] = node.getState().get_states()
        for st in states:
            state_name: str = StateType._enum_lookup[st]
            state_name = state_name.split("_", maxsplit=1)[1].lower()
            if len(state_name) == 0:
                continue
            attribute_dict[f"{{{_accessibility_ns_map_ubuntu['st']}}}{state_name}"] = "true"

        # Attributes
        attributes: Dict[str, str] = node.get_attributes()
        for attribute_name, attribute_value in attributes.items():
            if len(attribute_name) == 0:
                continue
            attribute_dict[f"{{{_accessibility_ns_map_ubuntu['attr']}}}{attribute_name}"] = attribute_value

        # Component
        if (attribute_dict.get(f"{{{_accessibility_ns_map_ubuntu['st']}}}visible", "false") == "true" and
            attribute_dict.get(f"{{{_accessibility_ns_map_ubuntu['st']}}}showing", "false") == "true"):
            try:
                component: Component = node.queryComponent()
                bbox: Sequence[int] = component.getExtents(pyatspi.XY_SCREEN)
                attribute_dict[f"{{{_accessibility_ns_map_ubuntu['cp']}}}screencoord"] = str(tuple(bbox[0:2]))
                attribute_dict[f"{{{_accessibility_ns_map_ubuntu['cp']}}}size"] = str(tuple(bbox[2:]))
            except NotImplementedError:
                pass

        text = ""
        # Text
        try:
            text_obj: ATText = node.queryText()
            text = text_obj.getText(0, text_obj.characterCount)
            text = text.replace("\ufffc", "").replace("\ufffd", "")
        except NotImplementedError:
            pass

        # Image, Selection, Value, Action
        try:
            node.queryImage()
            attribute_dict["image"] = "true"
        except NotImplementedError:
            pass

        try:
            node.querySelection()
            attribute_dict["selection"] = "true"
        except NotImplementedError:
            pass

        try:
            value: ATValue = node.queryValue()
            value_key = f"{{{_accessibility_ns_map_ubuntu['val']}}}"
            for attr_name, attr_func in [
                ("value", lambda: value.currentValue),
                ("min", lambda: value.minimumValue),
                ("max", lambda: value.maximumValue),
                ("step", lambda: value.minimumIncrement)
            ]:
                try:
                    attribute_dict[f"{value_key}{attr_name}"] = str(attr_func())
                except:
                    pass
        except NotImplementedError:
            pass

        try:
            action: ATAction = node.queryAction()
            for i in range(action.nActions):
                action_name = action.getName(i).replace(" ", "-")
                attribute_dict[f"{{{_accessibility_ns_map_ubuntu['act']}}}{action_name}_desc"] = action.getDescription(i)
                attribute_dict[f"{{{_accessibility_ns_map_ubuntu['act']}}}{action_name}_kb"] = action.getKeyBinding(i)
        except NotImplementedError:
            pass

        raw_role_name = node.getRoleName().strip()
        node_role_name = (raw_role_name or "unknown").replace(" ", "-")

        if not flag:
            if raw_role_name == "document spreadsheet":
                flag = "calc"
            if raw_role_name == "application" and node.name == "Thunderbird":
                flag = "thunderbird"

        xml_node = lxml.etree.Element(
            node_role_name,
            attrib=attribute_dict,
            nsmap=_accessibility_ns_map_ubuntu
        )

        if len(text) > 0:
            xml_node.text = text

        if depth == MAX_DEPTH:
            return xml_node

        if flag == "calc" and node_role_name == "table":
            global libreoffice_version_tuple
            MAXIMUN_COLUMN = 1024 if libreoffice_version_tuple < (7, 4) else 16384
            MAX_ROW = 104_8576

            index_base = 0
            first_showing = False
            column_base = None
            for r in range(MAX_ROW):
                for clm in range(column_base or 0, MAXIMUN_COLUMN):
                    child_node: Accessible = node[index_base + clm]
                    showing = child_node.getState().contains(STATE_SHOWING)
                    if showing:
                        child_node: _Element = _create_atspi_node(child_node, depth + 1, flag)
                        if not first_showing:
                            column_base = clm
                            first_showing = True
                        xml_node.append(child_node)
                    elif first_showing and column_base is not None or clm >= 500:
                        break
                if first_showing and clm == column_base or not first_showing and r >= 500:
                    break
                index_base += MAXIMUN_COLUMN
            return xml_node
        else:
            try:
                for i, ch in enumerate(node):
                    if i == MAX_WIDTH:
                        break
                    xml_node.append(_create_atspi_node(ch, depth + 1, flag))
            except:
                pass
            return xml_node


# ============================================================================
# Windows-specific Accessibility Tree Functions
# ============================================================================

if platform_name == "Windows":
    def _create_pywinauto_node(node, nodes, depth: int = 0, flag: Optional[str] = None) -> _Element:
        """Create an XML node from a pywinauto wrapper."""
        nodes = nodes or set()
        if node in nodes:
            return None
        nodes.add(node)

        attribute_dict: Dict[str, Any] = {"name": node.element_info.name}

        base_properties = {}
        try:
            base_properties.update(node.get_properties())
        except:
            try:
                _element_class = node.__class__

                class TempElement(node.__class__):
                    writable_props = pywinauto.base_wrapper.BaseWrapper.writable_props

                node.__class__ = TempElement
                properties = node.get_properties()
                node.__class__ = _element_class
                base_properties.update(properties)
            except:
                pass

        # States
        for attr_name, attr_func in [
            ("enabled", lambda: node.is_enabled()),
            ("visible", lambda: node.is_visible()),
            ("minimized", lambda: node.is_minimized()),
            ("maximized", lambda: node.is_maximized()),
            ("focused", lambda: node.is_focused()),
            ("selected", lambda: node.is_selected()),
            ("expanded", lambda: node.is_expanded()),
            ("editable", lambda: node.is_editable()),
        ]:
            try:
                attribute_dict[f"{{{_accessibility_ns_map_windows['st']}}}{attr_name}"] = str(attr_func()).lower()
            except:
                pass

        # Component
        try:
            rectangle = node.rectangle()
            attribute_dict[f"{{{_accessibility_ns_map_windows['cp']}}}screencoord"] = f"({rectangle.left}, {rectangle.top})"
            attribute_dict[f"{{{_accessibility_ns_map_windows['cp']}}}size"] = f"({rectangle.width()}, {rectangle.height()})"
        except:
            pass

        # Text
        text = node.window_text()
        if text == attribute_dict["name"]:
            text = ""

        # Selection
        if hasattr(node, "select"):
            attribute_dict["selection"] = "true"

        attribute_dict[f"{{{_accessibility_ns_map_windows['class']}}}class"] = str(type(node))

        node_role_name = node.class_name().lower().replace(" ", "-")
        node_role_name = "".join(
            map(lambda ch: ch if ch.isidentifier() or ch in {"-"} or ch.isalnum() else "-", node_role_name))

        if node_role_name.strip() == "":
            node_role_name = "unknown"
        if not node_role_name[0].isalpha():
            node_role_name = "tag" + node_role_name

        xml_node = lxml.etree.Element(
            node_role_name,
            attrib=attribute_dict,
            nsmap=_accessibility_ns_map_windows
        )

        if text and len(text) > 0 and text != attribute_dict["name"]:
            xml_node.text = text

        if depth == MAX_DEPTH:
            return xml_node

        children = node.children()
        if children:
            with concurrent.futures.ThreadPoolExecutor() as executor:
                futures = [executor.submit(_create_pywinauto_node, ch, nodes, depth + 1, flag)
                          for ch in children[:MAX_WIDTH]]
            try:
                for future in concurrent.futures.as_completed(futures):
                    result = future.result()
                    if result is not None:
                        xml_node.append(result)
            except:
                pass
        return xml_node


# ============================================================================
# API Endpoints
# ============================================================================

@app.get("/")
async def root():
    """Health check endpoint."""
    return {
        "status": "running",
        "platform": platform_name,
        "version": "1.0.0",
        "endpoints": [
            "/screenshot", "/accessibility", "/execute", "/execute/action",
            "/execute/python", "/execute/bash", "/terminal", "/file",
            "/platform", "/screen_size", "/cursor_position"
        ]
    }


@app.get("/platform")
async def get_platform():
    """Get the current platform."""
    return {"platform": platform_name}


@app.get("/screenshot")
async def capture_screenshot():
    """Capture a screenshot with cursor."""
    _require_display()
    file_path = os.path.join(SCREENSHOT_DIR, "screenshot.png")
    
    try:
        if platform_name == "Windows":
            ratio = ctypes.windll.shcore.GetScaleFactorForDevice(0) / 100
            img = ImageGrab.grab(bbox=None, include_layered_windows=True)
            
            try:
                def get_cursor():
                    hcursor = win32gui.GetCursorInfo()[1]
                    hdc = win32ui.CreateDCFromHandle(win32gui.GetDC(0))
                    hbmp = win32ui.CreateBitmap()
                    hbmp.CreateCompatibleBitmap(hdc, 36, 36)
                    hdc = hdc.CreateCompatibleDC()
                    hdc.SelectObject(hbmp)
                    hdc.DrawIcon((0, 0), hcursor)

                    bmpinfo = hbmp.GetInfo()
                    bmpstr = hbmp.GetBitmapBits(True)
                    cursor = Image.frombuffer('RGB', (bmpinfo['bmWidth'], bmpinfo['bmHeight']),
                                             bmpstr, 'raw', 'BGRX', 0, 1).convert("RGBA")

                    win32gui.DestroyIcon(hcursor)
                    win32gui.DeleteObject(hbmp.GetHandle())
                    hdc.DeleteDC()

                    pixdata = cursor.load()
                    width, height = cursor.size
                    for y in range(height):
                        for x in range(width):
                            if pixdata[x, y] == (0, 0, 0, 255):
                                pixdata[x, y] = (0, 0, 0, 0)

                    hotspot = win32gui.GetIconInfo(hcursor)[1:3]
                    return (cursor, hotspot)

                cursor, (hotspotx, hotspoty) = get_cursor()
                pos_win = win32gui.GetCursorPos()
                pos = (round(pos_win[0] * ratio - hotspotx), round(pos_win[1] * ratio - hotspoty))
                img.paste(cursor, pos, cursor)
            except Exception:
                pass

            img.save(file_path)
            
        elif platform_name == "Linux":
            screenshot = pyautogui.screenshot()
            
            if HAS_XCURSOR:
                try:
                    cursor_obj = Xcursor()
                    imgarray = cursor_obj.getCursorImageArrayFast()
                    cursor_img = Image.fromarray(imgarray)
                    cursor_x, cursor_y = pyautogui.position()
                    screenshot.paste(cursor_img, (cursor_x, cursor_y), cursor_img)
                except Exception:
                    pass
            
            screenshot.save(file_path)
            
        elif platform_name == "Darwin":
            subprocess.run(["screencapture", "-C", file_path])
        else:
            raise HTTPException(status_code=500, detail=f"Unsupported platform: {platform_name}")

        return FileResponse(file_path, media_type="image/png")
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to capture screenshot: {str(e)}")


@app.get("/accessibility")
async def get_accessibility_tree():
    """Get the accessibility tree of the desktop."""
    _require_display()
    try:
        if platform_name == "Linux":
            global libreoffice_version_tuple
            libreoffice_version_tuple = _get_libreoffice_version()

            desktop: Accessible = pyatspi.Registry.getDesktop(0)
            xml_node = lxml.etree.Element("desktop-frame", nsmap=_accessibility_ns_map_ubuntu)
            
            with concurrent.futures.ThreadPoolExecutor() as executor:
                futures = [executor.submit(_create_atspi_node, app_node, 1) for app_node in desktop]
                for future in concurrent.futures.as_completed(futures):
                    xml_tree = future.result()
                    xml_node.append(xml_tree)
            
            return {"AT": lxml.etree.tostring(xml_node, encoding="unicode")}

        elif platform_name == "Windows":
            desktop: Desktop = Desktop(backend="uia")
            xml_node = lxml.etree.Element("desktop", nsmap=_accessibility_ns_map_windows)
            
            with concurrent.futures.ThreadPoolExecutor() as executor:
                futures = [executor.submit(_create_pywinauto_node, wnd, {}, 1) for wnd in desktop.windows()]
                for future in concurrent.futures.as_completed(futures):
                    xml_tree = future.result()
                    if xml_tree is not None:
                        xml_node.append(xml_tree)
            
            return {"AT": lxml.etree.tostring(xml_node, encoding="unicode")}

        elif platform_name == "Darwin":
            # macOS accessibility tree implementation
            return {"AT": "<desktop></desktop>", "note": "macOS accessibility tree not fully implemented"}
        else:
            raise HTTPException(status_code=500, detail=f"Unsupported platform: {platform_name}")
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get accessibility tree: {str(e)}")


@app.get("/terminal")
async def get_terminal_output():
    """Get the terminal output (Linux only for now)."""
    _require_display()
    try:
        if platform_name == "Linux":
            desktop: Accessible = pyatspi.Registry.getDesktop(0)
            if _has_active_terminal(desktop):
                desktop_xml = _create_atspi_node(desktop)
                xpath = '//application[@name="gnome-terminal-server"]/frame[@st:active="true"]//terminal[@st:focused="true"]'
                terminals = desktop_xml.xpath(xpath, namespaces=_accessibility_ns_map_ubuntu)
                output = terminals[0].text.rstrip() if len(terminals) == 1 else None
                return {"output": output, "status": "success"}
            return {"output": None, "status": "no_active_terminal"}
        else:
            raise HTTPException(status_code=501, detail=f"Terminal output not implemented for {platform_name}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/execute")
@app.post("/setup/execute")
async def execute_command(request: ExecuteCommandRequest):
    """Execute a shell command."""
    command = request.command
    shell = request.shell

    if isinstance(command, str) and not shell:
        command = shlex.split(command)

    # Expand user directory
    if isinstance(command, list):
        for i, arg in enumerate(command):
            if arg.startswith("~/"):
                command[i] = os.path.expanduser(arg)

    try:
        flags = subprocess.CREATE_NO_WINDOW if platform_name == "Windows" else 0
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=shell,
            text=True,
            timeout=120,
            creationflags=flags if platform_name == "Windows" else 0,
        )
        return {
            "status": "success",
            "output": result.stdout,
            "error": result.stderr,
            "returncode": result.returncode
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/execute/action")
async def execute_action(request: ActionRequest):
    """Execute a PyAutoGUI action."""
    _require_display()
    action_type = request.action_type
    parameters = request.parameters

    try:
        if action_type == "MOVE_TO":
            x = parameters.get("x")
            y = parameters.get("y")
            if x is not None and y is not None:
                pyautogui.moveTo(x, y)
            else:
                pyautogui.moveTo()

        elif action_type == "CLICK":
            button = parameters.get("button", "left")
            x = parameters.get("x")
            y = parameters.get("y")
            clicks = parameters.get("num_clicks", 1)
            if x is not None and y is not None:
                pyautogui.click(x=x, y=y, button=button, clicks=clicks)
            else:
                pyautogui.click(button=button, clicks=clicks)

        elif action_type == "DOUBLE_CLICK":
            x = parameters.get("x")
            y = parameters.get("y")
            if x is not None and y is not None:
                pyautogui.doubleClick(x=x, y=y)
            else:
                pyautogui.doubleClick()

        elif action_type == "RIGHT_CLICK":
            x = parameters.get("x")
            y = parameters.get("y")
            if x is not None and y is not None:
                pyautogui.rightClick(x=x, y=y)
            else:
                pyautogui.rightClick()

        elif action_type == "MOUSE_DOWN":
            button = parameters.get("button", "left")
            pyautogui.mouseDown(button=button)

        elif action_type == "MOUSE_UP":
            button = parameters.get("button", "left")
            pyautogui.mouseUp(button=button)

        elif action_type == "DRAG_TO":
            x = parameters.get("x")
            y = parameters.get("y")
            if x is not None and y is not None:
                pyautogui.dragTo(x, y, duration=1.0, button='left', mouseDownUp=True)

        elif action_type == "SCROLL":
            dx = parameters.get("dx", 0)
            dy = parameters.get("dy", 0)
            if dx:
                pyautogui.hscroll(dx)
            if dy:
                pyautogui.vscroll(dy)

        elif action_type == "TYPING":
            text = parameters.get("text", "")
            pyautogui.typewrite(text)

        elif action_type == "PRESS":
            key = parameters.get("key")
            if key:
                pyautogui.press(key)

        elif action_type == "KEY_DOWN":
            key = parameters.get("key")
            if key:
                pyautogui.keyDown(key)

        elif action_type == "KEY_UP":
            key = parameters.get("key")
            if key:
                pyautogui.keyUp(key)

        elif action_type == "HOTKEY":
            keys = parameters.get("keys", [])
            if keys:
                pyautogui.hotkey(*keys)

        elif action_type in ["WAIT", "FAIL", "DONE"]:
            pass

        else:
            raise HTTPException(status_code=400, detail=f"Unknown action type: {action_type}")

        _append_event("Action", {"action_type": action_type, "parameters": parameters})
        return {"status": "success", "action_type": action_type}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/execute/python")
@app.post("/run_python")
async def run_python(request: RunPythonRequest):
    """Execute a Python script."""
    code = request.code
    
    temp_filename = f"/tmp/python_exec_{uuid.uuid4().hex}.py"
    
    try:
        with open(temp_filename, 'w') as f:
            f.write(code)
        
        result = subprocess.run(
            ['/usr/bin/python3', temp_filename],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30
        )
        
        try:
            os.remove(temp_filename)
        except:
            pass
        
        output = result.stdout
        error_output = result.stderr
        combined_message = output + ('\n' + error_output if error_output else '')
        
        return {
            "status": "success" if result.returncode == 0 else "error",
            "message": combined_message,
            "output": output,
            "error": error_output,
            "return_code": result.returncode
        }
        
    except subprocess.TimeoutExpired:
        try:
            os.remove(temp_filename)
        except:
            pass
        raise HTTPException(status_code=500, detail="Execution timeout")
    except Exception as e:
        try:
            os.remove(temp_filename)
        except:
            pass
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/execute/bash")
@app.post("/run_bash_script")
async def run_bash_script(request: RunBashScriptRequest):
    """Execute a Bash script."""
    script = request.script
    timeout = request.timeout
    working_dir = request.working_dir
    
    if working_dir:
        working_dir = os.path.expanduser(working_dir)
        if not os.path.exists(working_dir):
            raise HTTPException(status_code=400, detail=f"Working directory does not exist: {working_dir}")
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as tmp_file:
        if "#!/bin/bash" not in script:
            script = "#!/bin/bash\n\n" + script
        tmp_file.write(script)
        tmp_file_path = tmp_file.name
    
    try:
        os.chmod(tmp_file_path, 0o755)
        
        bash_cmd = ['bash', tmp_file_path] if platform_name == "Windows" else ['/bin/bash', tmp_file_path]
        
        result = subprocess.run(
            bash_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            cwd=working_dir,
            shell=False
        )
        
        _append_event("BashScript", {
            "script": script,
            "output": result.stdout,
            "returncode": result.returncode
        })
        
        return {
            "status": "success" if result.returncode == 0 else "error",
            "output": result.stdout,
            "error": "",
            "returncode": result.returncode
        }
        
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail=f"Script execution timed out after {timeout} seconds")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            os.unlink(tmp_file_path)
        except:
            pass


@app.post("/setup/launch")
async def launch_app(request: LaunchAppRequest):
    """Launch an application."""
    command = request.command
    shell = request.shell

    if isinstance(command, str) and not shell:
        command = shlex.split(command)

    if isinstance(command, list):
        for i, arg in enumerate(command):
            if arg.startswith("~/"):
                command[i] = os.path.expanduser(arg)

    try:
        # Handle google-chrome -> chromium-browser mapping
        # In Docker containers, chromium-browser is used instead of google-chrome
        if isinstance(command, list) and 'google-chrome' in command:
            index = command.index('google-chrome')
            # Check if chromium-browser exists (Docker/Ubuntu), otherwise try chromium (Snap)
            if shutil.which('chromium-browser'):
                command[index] = 'chromium-browser'
            elif shutil.which('chromium'):
                command[index] = 'chromium'
        elif isinstance(command, str) and 'google-chrome' in command:
            if shutil.which('chromium-browser'):
                command = command.replace('google-chrome', 'chromium-browser')
            elif shutil.which('chromium'):
                command = command.replace('google-chrome', 'chromium')
        
        subprocess.Popen(command, shell=shell)
        return {"status": "success", "message": f"{command if shell else ' '.join(command)} launched successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/file")
async def get_file(file_path: str = Form(...)):
    """Get a file from the server."""
    file_path = os.path.expandvars(os.path.expanduser(file_path))
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    
    return FileResponse(file_path, filename=os.path.basename(file_path))


@app.post("/setup/upload")
async def upload_file(file_path: str = Form(...), file_data: UploadFile = File(...)):
    """Upload a file to the server."""
    file_path = os.path.expandvars(os.path.expanduser(file_path))
    
    try:
        target_dir = os.path.dirname(file_path)
        if target_dir:
            os.makedirs(target_dir, exist_ok=True)
        
        with open(file_path, 'wb') as f:
            content = await file_data.read()
            f.write(content)
        
        uploaded_size = os.path.getsize(file_path)
        return {"status": "success", "message": f"File Uploaded: {uploaded_size} bytes"}
        
    except Exception as e:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except:
                pass
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/setup/download_file")
async def download_file(request: DownloadFileRequest):
    """Download a file from a URL."""
    import requests
    
    url = request.url
    path = Path(os.path.expandvars(os.path.expanduser(request.path)))
    path.parent.mkdir(parents=True, exist_ok=True)

    max_retries = 3
    error = None
    
    for i in range(max_retries):
        try:
            response = requests.get(url, stream=True, timeout=300)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            downloaded_size = 0
            
            with open(path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded_size += len(chunk)
            
            actual_size = os.path.getsize(path)
            if total_size > 0 and actual_size != total_size:
                raise Exception(f"Download incomplete. Expected {total_size} bytes, got {actual_size} bytes")
            
            return {"status": "success", "message": f"File downloaded successfully: {actual_size} bytes"}

        except Exception as e:
            error = e
            if path.exists():
                try:
                    path.unlink()
                except:
                    pass

    raise HTTPException(status_code=500, detail=f"Failed to download: {error}")


@app.post("/setup/open_file")
async def open_file(request: OpenFileRequest):
    """Open a file or application."""
    path = request.path
    path_obj = Path(os.path.expandvars(os.path.expanduser(path)))
    is_file_path = path_obj.exists()
    
    if not is_file_path and not shutil.which(path):
        raise HTTPException(status_code=404, detail=f"Application/file not found: {path}")

    try:
        if is_file_path:
            if platform_name == "Windows":
                os.startfile(path_obj)
            else:
                open_cmd = "open" if platform_name == "Darwin" else "xdg-open"
                subprocess.Popen([open_cmd, str(path_obj)])
        else:
            subprocess.Popen([path])

        return {"status": "success", "message": "File/application opened successfully"}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/setup/activate_window")
async def activate_window(request: ActivateWindowRequest):
    """Activate a window."""
    window_name = request.window_name
    strict = request.strict
    by_class = request.by_class

    try:
        if platform_name == "Windows" or platform_name == "Darwin":
            import pygetwindow as gw
            if by_class:
                raise HTTPException(status_code=501, detail="Get window by class name not supported")
            
            windows = gw.getWindowsWithTitle(window_name)
            if not windows:
                raise HTTPException(status_code=404, detail=f"Window '{window_name}' not found")
            
            window = None
            if strict:
                for wnd in windows:
                    if wnd.title == window_name:
                        window = wnd
                        break
                if window is None:
                    raise HTTPException(status_code=404, detail=f"Window '{window_name}' not found (strict mode)")
            else:
                window = windows[0]
            
            if platform_name == "Darwin":
                window.unminimize()
            window.activate()
            
        elif platform_name == "Linux":
            subprocess.run([
                "wmctrl",
                f"-{'x' if by_class else ''}{'F' if strict else ''}a",
                window_name
            ])
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported OS: {platform_name}")

        return {"status": "success", "message": "Window activated successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/setup/close_window")
async def close_window(request: CloseWindowRequest):
    """Close a window."""
    window_name = request.window_name
    strict = request.strict
    by_class = request.by_class

    try:
        if platform_name == "Windows":
            import pygetwindow as gw
            if by_class:
                raise HTTPException(status_code=501, detail="Get window by class name not supported on Windows")
            
            windows = gw.getWindowsWithTitle(window_name)
            if not windows:
                raise HTTPException(status_code=404, detail=f"Window '{window_name}' not found")
            
            window = windows[0] if not strict else next((w for w in windows if w.title == window_name), None)
            if window is None:
                raise HTTPException(status_code=404, detail=f"Window '{window_name}' not found (strict mode)")
            
            window.close()
            
        elif platform_name == "Linux":
            subprocess.run([
                "wmctrl",
                f"-{'x' if by_class else ''}{'F' if strict else ''}c",
                window_name
            ])
        elif platform_name == "Darwin":
            raise HTTPException(status_code=501, detail="Not supported on macOS")
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported OS: {platform_name}")

        return {"status": "success", "message": "Window closed successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/setup/change_wallpaper")
async def change_wallpaper(request: ChangeWallpaperRequest):
    """Change the desktop wallpaper."""
    path = Path(os.path.expandvars(os.path.expanduser(request.path)))

    if not path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    try:
        if platform_name == "Windows":
            ctypes.windll.user32.SystemParametersInfoW(20, 0, str(path), 3)
        elif platform_name == "Linux":
            subprocess.run(["gsettings", "set", "org.gnome.desktop.background", "picture-uri", f"file://{path}"])
        elif platform_name == "Darwin":
            subprocess.run(
                ["osascript", "-e", f'tell application "Finder" to set desktop picture to POSIX file "{path}"'])
        return {"status": "success", "message": "Wallpaper changed successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/screen_size")
async def get_screen_size():
    """Get the screen size."""
    _require_display()
    try:
        if platform_name == "Linux":
            d = display.Display()
            screen_width = d.screen().width_in_pixels
            screen_height = d.screen().height_in_pixels
        elif platform_name == "Windows":
            user32 = ctypes.windll.user32
            screen_width = user32.GetSystemMetrics(0)
            screen_height = user32.GetSystemMetrics(1)
        elif platform_name == "Darwin":
            screen_width, screen_height = pyautogui.size()
        else:
            screen_width, screen_height = 1920, 1080
            
        return {"width": screen_width, "height": screen_height}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/cursor_position")
async def get_cursor_position():
    """Get the current cursor position."""
    _require_display()
    try:
        pos = pyautogui.position()
        return {"x": pos.x, "y": pos.y}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/desktop_path")
async def get_desktop_path():
    """Get the desktop path."""
    home_directory = str(Path.home())
    desktop_path = os.path.join(home_directory, "Desktop")

    if os.path.exists(desktop_path):
        return {"desktop_path": desktop_path}
    raise HTTPException(status_code=404, detail="Desktop path not found")


@app.post("/list_directory")
async def get_directory_tree(request: ListDirectoryRequest):
    """List the contents of a directory recursively."""
    def _list_dir_contents(directory):
        tree = {'type': 'directory', 'name': os.path.basename(directory), 'children': []}
        try:
            for entry in os.listdir(directory):
                full_path = os.path.join(directory, entry)
                if os.path.isdir(full_path):
                    tree['children'].append(_list_dir_contents(full_path))
                else:
                    tree['children'].append({'type': 'file', 'name': entry})
        except OSError as e:
            tree = {'error': str(e)}
        return tree

    start_path = request.path
    if not os.path.isdir(start_path):
        raise HTTPException(status_code=400, detail="The provided path is not a directory")

    directory_tree = _list_dir_contents(start_path)
    return {"directory_tree": directory_tree}


@app.post("/start_recording")
async def start_recording():
    """Start screen recording."""
    global recording_process
    
    if recording_process and recording_process.poll() is None:
        raise HTTPException(status_code=400, detail="Recording is already in progress")

    if os.path.exists(recording_path):
        try:
            os.remove(recording_path)
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"Failed to remove old recording file: {e}")

    if platform_name != "Linux":
        raise HTTPException(status_code=501, detail="Recording only supported on Linux")

    d = display.Display()
    screen_width = d.screen().width_in_pixels
    screen_height = d.screen().height_in_pixels

    start_command = f"ffmpeg -y -f x11grab -draw_mouse 1 -s {screen_width}x{screen_height} -i :0.0 -c:v libx264 -r 30 {recording_path}"

    recording_process = subprocess.Popen(
        shlex.split(start_command),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True
    )

    try:
        recording_process.wait(timeout=2)
        error_output = recording_process.stderr.read()
        raise HTTPException(status_code=500, detail=f"Failed to start recording: {error_output}")
    except subprocess.TimeoutExpired:
        return {"status": "success", "message": "Started recording successfully"}


@app.post("/end_recording")
async def end_recording():
    """End screen recording and return the video file."""
    global recording_process

    if not recording_process or recording_process.poll() is not None:
        recording_process = None
        raise HTTPException(status_code=400, detail="No recording in progress")

    try:
        recording_process.send_signal(signal.SIGINT)
        _, error_output = recording_process.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        recording_process.kill()
        recording_process.communicate()
        recording_process = None
        raise HTTPException(status_code=500, detail="Recording process was unresponsive")

    recording_process = None

    if os.path.exists(recording_path) and os.path.getsize(recording_path) > 0:
        return FileResponse(recording_path, filename="recording.mp4", media_type="video/mp4")
    
    raise HTTPException(status_code=500, detail="Recording failed - output file is missing or empty")


@app.post("/wallpaper")
async def get_wallpaper():
    """Get the current wallpaper."""
    try:
        if platform_name == "Windows":
            SPI_GETDESKWALLPAPER = 0x73
            MAX_PATH = 260
            buffer = ctypes.create_unicode_buffer(MAX_PATH)
            ctypes.windll.user32.SystemParametersInfoW(SPI_GETDESKWALLPAPER, MAX_PATH, buffer, 0)
            wallpaper_path = buffer.value
        elif platform_name == "Darwin":
            script = 'tell application "System Events" to tell every desktop to get picture'
            process = subprocess.Popen(['osascript', '-e', script], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            output, _ = process.communicate()
            wallpaper_path = output.strip().decode('utf-8')
        elif platform_name == "Linux":
            output = subprocess.check_output(
                ["gsettings", "get", "org.gnome.desktop.background", "picture-uri"],
                stderr=subprocess.PIPE
            )
            wallpaper_path = output.decode('utf-8').strip().replace('file://', '').replace("'", "")
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported OS: {platform_name}")

        if wallpaper_path and os.path.exists(wallpaper_path):
            return FileResponse(wallpaper_path, media_type="image/png")
        raise HTTPException(status_code=404, detail="Wallpaper file not found")
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Main Entry Point
# ============================================================================

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000, log_level="info")


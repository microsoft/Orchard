#!/usr/bin/env python3
"""
OSWorld Server Test Suite

This script tests all endpoints of the OSWorld FastAPI server,
aligned with OSWorld's action space.

Usage:
    python test_server.py [--url http://localhost:5000]
"""

import argparse
import json
import os
import sys
import time
from io import BytesIO
from typing import Any, Dict, List, Optional

import requests


class OSWorldServerTester:
    """Test suite for OSWorld FastAPI server."""
    
    def __init__(self, base_url: str = "http://localhost:5000"):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.passed = 0
        self.failed = 0
        self.skipped = 0
    
    def _log(self, status: str, message: str):
        """Log a test result."""
        colors = {
            "PASS": "\033[92m",  # Green
            "FAIL": "\033[91m",  # Red
            "SKIP": "\033[93m",  # Yellow
            "INFO": "\033[94m",  # Blue
            "RESET": "\033[0m"
        }
        print(f"{colors.get(status, '')}{status}: {message}{colors['RESET']}")
    
    def _test(self, name: str, condition: bool, details: str = ""):
        """Record a test result."""
        if condition:
            self.passed += 1
            self._log("PASS", f"{name}")
        else:
            self.failed += 1
            self._log("FAIL", f"{name} - {details}")
        return condition
    
    def _get(self, endpoint: str, **kwargs) -> requests.Response:
        """Make a GET request."""
        return self.session.get(f"{self.base_url}{endpoint}", **kwargs)
    
    def _post(self, endpoint: str, **kwargs) -> requests.Response:
        """Make a POST request."""
        return self.session.post(f"{self.base_url}{endpoint}", **kwargs)
    
    # ========================================================================
    # Health & Info Tests
    # ========================================================================
    
    def test_health_check(self):
        """Test the health check endpoint."""
        self._log("INFO", "Testing health check...")
        try:
            response = self._get("/", timeout=10)
            self._test("Health check returns 200", response.status_code == 200, 
                      f"Got {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                self._test("Health check has status", "status" in data, 
                          f"Response: {data}")
            return True
        except requests.exceptions.ConnectionError as e:
            self._log("FAIL", f"Cannot connect to server at {self.base_url}: {e}")
            return False
        except Exception as e:
            self._log("FAIL", f"Health check failed: {e}")
            return False
    
    def test_platform(self):
        """Test the platform endpoint."""
        self._log("INFO", "Testing platform endpoint...")
        try:
            response = self._get("/platform", timeout=10)
            self._test("Platform returns 200", response.status_code == 200,
                      f"Got {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                self._test("Platform has platform field", "platform" in data,
                          f"Response: {data}")
                print(f"    Platform: {data.get('platform', 'unknown')}")
        except Exception as e:
            self._log("FAIL", f"Platform test failed: {e}")
    
    # ========================================================================
    # Screenshot Test
    # ========================================================================
    
    def test_screenshot(self):
        """Test the screenshot endpoint."""
        self._log("INFO", "Testing screenshot endpoint...")
        try:
            response = self._get("/screenshot", timeout=30)
            
            # Check status code
            if response.status_code != 200:
                self._test("Screenshot returns 200", False,
                          f"Got {response.status_code}: {response.text[:200]}")
                return
            
            # Check content type
            content_type = response.headers.get("content-type", "")
            is_image = "image" in content_type
            self._test("Screenshot returns image content-type", is_image,
                      f"Got content-type: {content_type}")
            
            # Check content is valid PNG
            content = response.content
            is_png = len(content) > 8 and content[:8] == b'\x89PNG\r\n\x1a\n'
            self._test("Screenshot is valid PNG", is_png,
                      f"First 20 bytes: {content[:20]}")
            
            if is_png:
                # Save for inspection
                with open("test_screenshot.png", "wb") as f:
                    f.write(content)
                print(f"    Saved screenshot to test_screenshot.png ({len(content)} bytes)")
                
                # Try to open with PIL
                try:
                    from PIL import Image
                    img = Image.open(BytesIO(content))
                    self._test("Screenshot opens with PIL", True)
                    print(f"    Image size: {img.size}, mode: {img.mode}")
                except Exception as e:
                    self._test("Screenshot opens with PIL", False, str(e))
            
        except Exception as e:
            self._log("FAIL", f"Screenshot test failed: {e}")
    
    # ========================================================================
    # Command Execution Tests
    # ========================================================================
    
    def test_execute_command(self):
        """Test command execution endpoint."""
        self._log("INFO", "Testing execute endpoint...")
        try:
            # Test simple command
            response = self._post("/execute", 
                                 json={"command": ["echo", "hello"], "shell": False},
                                 timeout=30)
            self._test("Execute returns 200", response.status_code == 200,
                      f"Got {response.status_code}: {response.text[:200]}")
            
            if response.status_code == 200:
                data = response.json()
                self._test("Execute has output", "output" in data,
                          f"Response: {data}")
                if "output" in data:
                    print(f"    Output: {data['output'].strip()}")
            
            # Test shell command
            response = self._post("/execute",
                                 json={"command": "echo 'shell test'", "shell": True},
                                 timeout=30)
            self._test("Execute shell returns 200", response.status_code == 200,
                      f"Got {response.status_code}")
            
        except Exception as e:
            self._log("FAIL", f"Execute test failed: {e}")
    
    def test_run_python(self):
        """Test Python script execution."""
        self._log("INFO", "Testing run_python endpoint...")
        try:
            code = "print('Hello from Python')\nprint(2 + 2)"
            response = self._post("/run_python", json={"code": code}, timeout=30)
            self._test("Run Python returns 200", response.status_code == 200,
                      f"Got {response.status_code}: {response.text[:200]}")
            
            if response.status_code == 200:
                data = response.json()
                self._test("Run Python has output", "output" in data,
                          f"Response: {data}")
                if "output" in data:
                    print(f"    Output: {data['output'].strip()}")
                    
        except Exception as e:
            self._log("FAIL", f"Run Python test failed: {e}")
    
    def test_run_bash(self):
        """Test Bash script execution."""
        self._log("INFO", "Testing run_bash_script endpoint...")
        try:
            script = "echo 'Hello from Bash'\npwd\nls -la /tmp | head -5"
            response = self._post("/run_bash_script", 
                                 json={"script": script, "timeout": 30},
                                 timeout=60)
            self._test("Run Bash returns 200", response.status_code == 200,
                      f"Got {response.status_code}: {response.text[:200]}")
            
            if response.status_code == 200:
                data = response.json()
                self._test("Run Bash has output", "output" in data,
                          f"Response: {data}")
                if "output" in data:
                    lines = data['output'].strip().split('\n')[:5]
                    print(f"    Output (first 5 lines):")
                    for line in lines:
                        print(f"      {line}")
                    
        except Exception as e:
            self._log("FAIL", f"Run Bash test failed: {e}")
    
    # ========================================================================
    # Action Tests (aligned with OSWorld action space)
    # ========================================================================
    
    def test_action(self, action_type: str, parameters: Dict[str, Any], 
                   description: str = "") -> bool:
        """Test a single action."""
        try:
            response = self._post("/execute/action",
                                 json={"action_type": action_type, "parameters": parameters},
                                 timeout=30)
            success = response.status_code == 200
            detail = description or f"{action_type}({parameters})"
            self._test(f"Action {detail}", success,
                      f"Got {response.status_code}: {response.text[:100]}")
            return success
        except Exception as e:
            self._log("FAIL", f"Action {action_type} failed: {e}")
            return False
    
    def test_all_actions(self):
        """Test all OSWorld action types."""
        self._log("INFO", "Testing OSWorld actions...")
        
        # Get screen size first
        screen_width, screen_height = 1920, 1080
        try:
            response = self._post("/screen_size", timeout=10)
            if response.status_code == 200:
                data = response.json()
                screen_width = data.get("width", 1920)
                screen_height = data.get("height", 1080)
                print(f"    Screen size: {screen_width}x{screen_height}")
        except:
            pass
        
        center_x = screen_width // 2
        center_y = screen_height // 2
        
        # Test MOVE_TO
        self.test_action("MOVE_TO", {"x": center_x, "y": center_y}, 
                        f"MOVE_TO({center_x}, {center_y})")
        
        # Test CLICK
        self.test_action("CLICK", {}, "CLICK (current position)")
        self.test_action("CLICK", {"x": center_x, "y": center_y}, 
                        f"CLICK({center_x}, {center_y})")
        self.test_action("CLICK", {"button": "left", "num_clicks": 1}, 
                        "CLICK(left, 1)")
        
        # Test DOUBLE_CLICK
        self.test_action("DOUBLE_CLICK", {"x": center_x, "y": center_y},
                        f"DOUBLE_CLICK({center_x}, {center_y})")
        
        # Test RIGHT_CLICK
        self.test_action("RIGHT_CLICK", {"x": center_x, "y": center_y},
                        f"RIGHT_CLICK({center_x}, {center_y})")
        time.sleep(0.5)
        # Press Escape to close any context menu
        self.test_action("PRESS", {"key": "escape"}, "PRESS(escape)")
        
        # Test MOUSE_DOWN / MOUSE_UP
        self.test_action("MOUSE_DOWN", {"button": "left"}, "MOUSE_DOWN(left)")
        self.test_action("MOUSE_UP", {"button": "left"}, "MOUSE_UP(left)")
        
        # Test SCROLL
        self.test_action("SCROLL", {"dx": 0, "dy": 3}, "SCROLL(0, 3)")
        self.test_action("SCROLL", {"dx": 0, "dy": -3}, "SCROLL(0, -3)")
        
        # Test DRAG_TO (small movement)
        self.test_action("MOVE_TO", {"x": center_x - 50, "y": center_y}, 
                        f"MOVE_TO({center_x - 50}, {center_y})")
        self.test_action("DRAG_TO", {"x": center_x + 50, "y": center_y},
                        f"DRAG_TO({center_x + 50}, {center_y})")
        
        # Test TYPING
        self.test_action("TYPING", {"text": "test"}, "TYPING('test')")
        
        # Test PRESS
        self.test_action("PRESS", {"key": "backspace"}, "PRESS(backspace)")
        self.test_action("PRESS", {"key": "enter"}, "PRESS(enter)")
        
        # Test KEY_DOWN / KEY_UP
        self.test_action("KEY_DOWN", {"key": "shift"}, "KEY_DOWN(shift)")
        self.test_action("KEY_UP", {"key": "shift"}, "KEY_UP(shift)")
        
        # Test HOTKEY
        self.test_action("HOTKEY", {"keys": ["ctrl", "a"]}, "HOTKEY(ctrl+a)")
        
        # Test WAIT, FAIL, DONE (special actions that don't do anything)
        self.test_action("WAIT", {}, "WAIT")
        self.test_action("DONE", {}, "DONE")
    
    # ========================================================================
    # Screen & Cursor Tests
    # ========================================================================
    
    def test_screen_size(self):
        """Test screen size endpoint."""
        self._log("INFO", "Testing screen_size endpoint...")
        try:
            response = self._post("/screen_size", timeout=10)
            self._test("Screen size returns 200", response.status_code == 200,
                      f"Got {response.status_code}: {response.text[:200]}")
            
            if response.status_code == 200:
                data = response.json()
                self._test("Screen size has width", "width" in data)
                self._test("Screen size has height", "height" in data)
                print(f"    Screen: {data.get('width')}x{data.get('height')}")
                
        except Exception as e:
            self._log("FAIL", f"Screen size test failed: {e}")
    
    def test_cursor_position(self):
        """Test cursor position endpoint."""
        self._log("INFO", "Testing cursor_position endpoint...")
        try:
            response = self._get("/cursor_position", timeout=10)
            self._test("Cursor position returns 200", response.status_code == 200,
                      f"Got {response.status_code}: {response.text[:200]}")
            
            if response.status_code == 200:
                data = response.json()
                self._test("Cursor position has x", "x" in data)
                self._test("Cursor position has y", "y" in data)
                print(f"    Cursor: ({data.get('x')}, {data.get('y')})")
                
        except Exception as e:
            self._log("FAIL", f"Cursor position test failed: {e}")
    
    # ========================================================================
    # File Operations Tests
    # ========================================================================
    
    def test_file_operations(self):
        """Test file operations."""
        self._log("INFO", "Testing file operations...")
        
        # Test list_directory
        try:
            response = self._post("/list_directory", json={"path": "/tmp"}, timeout=10)
            self._test("List directory returns 200", response.status_code == 200,
                      f"Got {response.status_code}")
        except Exception as e:
            self._log("FAIL", f"List directory failed: {e}")
        
        # Test desktop_path
        try:
            response = self._post("/desktop_path", timeout=10)
            if response.status_code == 200:
                data = response.json()
                self._test("Desktop path returns path", "desktop_path" in data)
                print(f"    Desktop: {data.get('desktop_path')}")
            else:
                self._log("SKIP", f"Desktop path not found (status {response.status_code})")
                self.skipped += 1
        except Exception as e:
            self._log("FAIL", f"Desktop path failed: {e}")
    
    # ========================================================================
    # Summary
    # ========================================================================
    
    def run_all_tests(self):
        """Run all tests."""
        print("=" * 60)
        print("OSWorld Server Test Suite")
        print(f"Target: {self.base_url}")
        print("=" * 60)
        print()
        
        # First check if server is reachable
        if not self.test_health_check():
            print()
            print("=" * 60)
            print("ABORTED: Server not reachable")
            print("=" * 60)
            return False
        
        print()
        
        # Run all tests
        self.test_platform()
        print()
        
        self.test_screenshot()
        print()
        
        self.test_execute_command()
        print()
        
        self.test_run_python()
        print()
        
        self.test_run_bash()
        print()
        
        self.test_screen_size()
        print()
        
        self.test_cursor_position()
        print()
        
        self.test_all_actions()
        print()
        
        self.test_file_operations()
        print()
        
        # Print summary
        print("=" * 60)
        print("Test Summary")
        print("=" * 60)
        total = self.passed + self.failed + self.skipped
        print(f"  Passed:  {self.passed}/{total}")
        print(f"  Failed:  {self.failed}/{total}")
        print(f"  Skipped: {self.skipped}/{total}")
        print()
        
        if self.failed == 0:
            print("\033[92m✓ All tests passed!\033[0m")
        else:
            print(f"\033[91m✗ {self.failed} test(s) failed\033[0m")
        
        return self.failed == 0


def main():
    parser = argparse.ArgumentParser(description="Test OSWorld FastAPI server")
    parser.add_argument("--url", default="http://localhost:5000",
                       help="Server URL (default: http://localhost:5000)")
    args = parser.parse_args()
    
    tester = OSWorldServerTester(args.url)
    success = tester.run_all_tests()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()


#!/bin/bash

# Full OSWorld Compatibility Test
# Tests all installed applications one at a time, closing between tests

BASE_URL="http://localhost:5000"
OUTPUT_DIR="./test_output"
mkdir -p "$OUTPUT_DIR"

echo "=========================================="
echo "OSWorld Full Compatibility Test"
echo "=========================================="
echo ""

# Helper function for screenshots
take_screenshot() {
    local name=$1
    curl -s "$BASE_URL/screenshot" -o "$OUTPUT_DIR/${name}.png"
    echo "  Screenshot saved: ${name}.png ($(ls -lh "$OUTPUT_DIR/${name}.png" | awk '{print $5}'))"
}

# Helper function to close all windows
close_all_windows() {
    echo "  Closing all windows..."
    curl -s -X POST "$BASE_URL/execute" \
      -H "Content-Type: application/json" \
      -d '{"command": "wmctrl -l | grep -v \"Desktop\" | awk \"{print \\$1}\" | xargs -I{} wmctrl -ic {}", "shell": true}' > /dev/null 2>&1
    sleep 1
}

# Helper function to close specific window by name pattern
close_window() {
    local pattern=$1
    echo "  Closing window matching: $pattern"
    curl -s -X POST "$BASE_URL/execute" \
      -H "Content-Type: application/json" \
      -d "{\"command\": \"wmctrl -c '$pattern'\", \"shell\": true}" > /dev/null 2>&1
    sleep 0.5
}

# 1. Server check
echo "[1/14] Checking server..."
curl -s "$BASE_URL/" | python3 -m json.tool 2>/dev/null | head -5
echo ""

# 2. Screen info
echo "[2/14] Screen information..."
curl -s -X POST "$BASE_URL/screen_size" | python3 -m json.tool 2>/dev/null
echo ""

# 3. Close any existing windows and take clean desktop screenshot
echo "[3/14] Taking clean desktop screenshot..."
close_all_windows
sleep 2
take_screenshot "01_desktop_clean"
echo ""

# 4. Test File Manager (Nautilus)
echo "[4/14] Testing Nautilus File Manager..."
curl -s -X POST "$BASE_URL/setup/launch" \
  -H "Content-Type: application/json" \
  -d '{"command": ["nautilus", "/home/user"], "shell": false}' | python3 -m json.tool 2>/dev/null
sleep 3
take_screenshot "02_file_manager"
close_window "Files"
close_window "user"
echo ""

# 5. Test Terminal (GNOME Terminal)
echo "[5/14] Testing GNOME Terminal..."
curl -s -X POST "$BASE_URL/setup/launch" \
  -H "Content-Type: application/json" \
  -d '{"command": ["gnome-terminal"], "shell": false}' | python3 -m json.tool 2>/dev/null || \
curl -s -X POST "$BASE_URL/setup/launch" \
  -H "Content-Type: application/json" \
  -d '{"command": ["xterm", "-geometry", "100x30"], "shell": false}' | python3 -m json.tool 2>/dev/null
sleep 2
take_screenshot "03_terminal"
close_window "Terminal"
close_window "xterm"
echo ""

# 6. Test Chromium browser
echo "[6/14] Testing Chromium browser..."
curl -s -X POST "$BASE_URL/setup/launch" \
  -H "Content-Type: application/json" \
  -d '{"command": ["chromium-browser", "--no-first-run", "--no-default-browser-check", "--disable-sync"], "shell": false}' | python3 -m json.tool 2>/dev/null
sleep 4
take_screenshot "04_chromium"
close_window "Chromium"
echo ""

# 7. Test LibreOffice Writer
echo "[7/14] Testing LibreOffice Writer..."
curl -s -X POST "$BASE_URL/setup/launch" \
  -H "Content-Type: application/json" \
  -d '{"command": ["libreoffice", "--writer"], "shell": false}' | python3 -m json.tool 2>/dev/null
sleep 4
take_screenshot "05_writer"
close_window "Writer"
close_window "LibreOffice"
echo ""

# 8. Test LibreOffice Calc
echo "[8/14] Testing LibreOffice Calc..."
curl -s -X POST "$BASE_URL/setup/launch" \
  -H "Content-Type: application/json" \
  -d '{"command": ["libreoffice", "--calc"], "shell": false}' | python3 -m json.tool 2>/dev/null
sleep 4
take_screenshot "06_calc"
close_window "Calc"
close_window "LibreOffice"
echo ""

# 9. Test LibreOffice Impress
echo "[9/14] Testing LibreOffice Impress..."
curl -s -X POST "$BASE_URL/setup/launch" \
  -H "Content-Type: application/json" \
  -d '{"command": ["libreoffice", "--impress"], "shell": false}' | python3 -m json.tool 2>/dev/null
sleep 4
take_screenshot "07_impress"
close_window "Impress"
close_window "LibreOffice"
echo ""

# 10. Test GIMP
echo "[10/14] Testing GIMP..."
curl -s -X POST "$BASE_URL/setup/launch" \
  -H "Content-Type: application/json" \
  -d '{"command": ["gimp"], "shell": false}' | python3 -m json.tool 2>/dev/null
sleep 5
take_screenshot "08_gimp"
close_window "GIMP"
close_window "GNU Image"
echo ""

# 11. Test gedit (Text Editor)
echo "[11/14] Testing gedit..."
curl -s -X POST "$BASE_URL/setup/launch" \
  -H "Content-Type: application/json" \
  -d '{"command": ["gedit"], "shell": false}' | python3 -m json.tool 2>/dev/null
sleep 2
take_screenshot "09_gedit"
close_window "gedit"
close_window "Text Editor"
echo ""

# 12. Type text in a fresh terminal
echo "[12/14] Testing typing in terminal..."
curl -s -X POST "$BASE_URL/setup/launch" \
  -H "Content-Type: application/json" \
  -d '{"command": ["xterm", "-geometry", "100x30"], "shell": false}' | python3 -m json.tool 2>/dev/null
sleep 2
curl -s -X POST "$BASE_URL/execute/action" \
  -H "Content-Type: application/json" \
  -d '{"action_type": "TYPING", "parameters": {"text": "echo Hello OSWorld! This is a compatibility test."}}' | python3 -m json.tool 2>/dev/null
sleep 1
take_screenshot "10_typing"
close_window "xterm"
echo ""

# 13. Get accessibility tree
echo "[13/14] Testing accessibility tree..."
# Open a simple app first
curl -s -X POST "$BASE_URL/setup/launch" \
  -H "Content-Type: application/json" \
  -d '{"command": ["nautilus", "/home/user"], "shell": false}' | python3 -m json.tool 2>/dev/null
sleep 2
A11Y=$(curl -s "$BASE_URL/accessibility")
A11Y_LEN=$(echo "$A11Y" | python3 -c "import sys, json; d=json.load(sys.stdin); print(len(d.get('AT','')))" 2>/dev/null || echo "0")
echo "  Accessibility tree length: $A11Y_LEN characters"
close_window "Files"
echo ""

# 14. Final state - show window list
echo "[14/14] Final state..."
close_all_windows
sleep 1
take_screenshot "11_final_clean"
echo ""

# Summary
echo "=========================================="
echo "Test Complete!"
echo "=========================================="
echo ""
echo "Screenshots saved in: $OUTPUT_DIR/"
ls -lh "$OUTPUT_DIR"/*.png 2>/dev/null
echo ""
echo "Application Test Results:"
echo "  [1] Desktop - clean screenshot"
echo "  [2] Nautilus File Manager"
echo "  [3] Terminal (gnome-terminal or xterm)"
echo "  [4] Chromium Browser"
echo "  [5] LibreOffice Writer"
echo "  [6] LibreOffice Calc"
echo "  [7] LibreOffice Impress"
echo "  [8] GIMP"
echo "  [9] gedit"
echo "  [10] Typing test"
echo "  [11] Final clean state"
echo ""
echo "Ports:"
echo "  5000 - FastAPI Server"
echo "  9222 - Chrome Remote Debugging"
echo "  8080 - VLC HTTP Interface"
echo ""
echo "To view screenshots: open $OUTPUT_DIR/*.png"

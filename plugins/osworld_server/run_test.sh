# 1. Check if server is running
curl -v http://localhost:5000/

# 2. Check platform
curl http://localhost:5000/platform

# 3. Test screenshot - save response with headers to see what's returned
curl -v http://localhost:5000/screenshot -o test.png 2>&1 | head -50

# 4. Check what type of file was saved
file test.png

# 5. If it's not a PNG, check the content (might be JSON error)
head -c 500 test.png

# 6. Or view as text if it's an error response
cat test.png

# 7. Test screen size
curl -X POST http://localhost:5000/screen_size

# 8. Test a simple execute command
curl -X POST http://localhost:5000/execute \
  -H "Content-Type: application/json" \
  -d '{"command": ["echo", "hello"], "shell": false}'

# 9. Test cursor position
curl http://localhost:5000/cursor_position
#!/usr/bin/env python3
"""Test file upload and download operations."""

import asyncio
import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from orchard.client import AsyncSandboxClient


async def main():
    """Test file operations."""
    print("=== Testing File Operations ===\n")
    
    async with AsyncSandboxClient("http://localhost:8000") as client:
        print("Creating sandbox...")
        async with await client.create_sandbox("python:3.11-slim") as sandbox:
            print(f"Created sandbox: {sandbox.sandbox_id}\n")
            
            # Test 1: Upload content
            print("1. Uploading content...")
            test_content = b"print('Hello from uploaded file!')\nprint(1 + 2)"
            result = await sandbox.upload_content(test_content, "/workspace/test.py")
            print(f"   Uploaded: {result}\n")
            
            # Test 2: List files
            print("2. Listing files in /workspace...")
            files = await sandbox.list_files("/workspace")
            for f in files:
                print(f"   {f['type']:10} {f['size']:>10} {f['name']}")
            print()
            
            # Test 3: Execute the uploaded file
            print("3. Executing uploaded file...")
            result = await sandbox.exec("python /workspace/test.py")
            print(f"   Output: {result.stdout.strip()}")
            print(f"   Exit code: {result.exit_code}\n")
            
            # Test 4: Download content
            print("4. Downloading file content...")
            content = await sandbox.download_content("/workspace/test.py")
            print(f"   Downloaded {len(content)} bytes")
            print(f"   Content: {content.decode()}\n")
            
            # Test 5: Create file via exec and download
            print("5. Creating file via exec and downloading...")
            await sandbox.exec("echo 'Created by exec' > /workspace/output.txt")
            content = await sandbox.download_content("/workspace/output.txt")
            print(f"   Content: {content.decode().strip()}\n")
            
        print("Sandbox deleted.")
    
    print("\n=== All tests passed! ===")


if __name__ == "__main__":
    asyncio.run(main())

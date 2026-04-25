"""Test file operations with sandbox."""
import asyncio
from orchard import AsyncSandboxClient


async def main():
    async with AsyncSandboxClient() as client:
        async with await client.create_sandbox("python:3.11-slim") as sandbox:
            # Create a test file first
            await sandbox.exec("echo 'hello world' > /workspace/output.txt")
            
            # Upload file
            await sandbox.upload_content(b"print('hello')", "/workspace/test.py")
            
            # Run the uploaded script
            result = await sandbox.exec("python /workspace/test.py")
            print(f"Script output: {result.stdout}")
            
            # Download file
            content = await sandbox.download_content("/workspace/output.txt")
            print(f"Downloaded content: {content}")
            
            # List files
            files = await sandbox.list_files("/workspace")
            print(f"Files in /workspace: {files}")


if __name__ == "__main__":
    asyncio.run(main())

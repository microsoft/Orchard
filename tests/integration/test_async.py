import asyncio
from orchard import AsyncSandboxClient
import random

async def main():
    async with AsyncSandboxClient() as client:
        # Create sandbox with automatic cleanup
        async with await client.create_sandbox("python:3.11-slim") as sandbox:
            result = await sandbox.exec("echo 'Hello from async sandbox!'")
            print(f"Output: {result.stdout}")
            print(f"Exit Code: {result.exit_code}")
            print(f"Succeeded: {result.succeeded}")
            for _ in range(20):
                result = await sandbox.exec([
                    "python", "-c",
                    'print("Async Sandbox Python execution")'
                ])
                print(f"Python Output: {result.stdout.strip()}")
                print(f"Python Error Output: {result.stderr.strip()}")
                print(f"Python Exit Code: {result.exit_code}")
                await asyncio.sleep(random.uniform(1, 10))
        # Sandbox auto-deleted here
    # Session auto-closed here

asyncio.run(main())
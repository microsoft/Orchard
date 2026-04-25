from aks_modal import SandboxClient
import random
import time

# Connect to orchestrator (uses SANDBOX_BASE_URL and SANDBOX_API_KEY env vars)
client = SandboxClient()

try:
    # Create sandbox with custom resources
    with client.create_sandbox(
        image="python:3.11-slim",
        cpu="4",
        memory="16Gi"
    ) as sandbox:
        
        # Execute commands
        result = sandbox.exec("echo Hello!")
        print(result.stdout)
        
        # Run Python code - retries are now automatic in _request
        for idx in range(20):
            print(f"\nRunning Python code iteration {idx+1}...")
            
            try:
                result = sandbox.exec([
                    "python", "-c",
                    'print("Sandbox Python execution")'
                ])
                
                print(f"  Exit code: {result.exit_code}")
                print(f"  Stdout: {result.stdout.strip()}")
                
                if result.stderr:
                    print(f"  Stderr: {result.stderr}")
                
                if result.succeeded:
                    print(f"  ✅ Success")
                else:
                    print(f"  ⚠️ Execution failed (exit code: {result.exit_code})")
                    
            except Exception as e:
                print(f"  ❌ Exception: {type(e).__name__}: {e}")
                raise
            
            time.sleep(random.uniform(2, 10))
        
        print("\n✅ All iterations completed successfully")

except Exception as e:
    print(f"\n❌ Error: {type(e).__name__}: {e}")
finally:
    # Ensure client is closed
    try:
        client.close()
    except:
        pass
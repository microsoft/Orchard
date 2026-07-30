"""Main entry point for the orchestrator service."""

import uvicorn

from orchard_env.orchestrator.api import app
from orchard_env.orchestrator.settings import settings


def main():
    """Run the application."""
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_config=None,  # We use our own logging setup
        backlog=2048,  # Increase TCP accept queue for high concurrency
        # Keep idle HTTP/1.1 connections alive long enough that client-side
        # connection pools (requests.Session / aiohttp) don't race a
        # server-initiated FIN and produce spurious ConnectionError on the
        # next POST.  120s comfortably exceeds typical client think-time
        # and is well below the LB idle timeout (100 min).
        timeout_keep_alive=120,
    )


if __name__ == "__main__":
    main()

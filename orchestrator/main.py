"""Main entry point for the orchestrator service."""

import uvicorn

from orchestrator.api import app
from orchestrator.settings import settings


def main():
    """Run the application."""
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_config=None,  # We use our own logging setup
        backlog=2048,  # Increase TCP accept queue for high concurrency
    )


if __name__ == "__main__":
    main()

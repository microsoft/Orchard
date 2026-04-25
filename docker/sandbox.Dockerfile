# Sandbox container image with built-in agent.
#
# The agent is a lightweight FastAPI server that accepts exec / file
# operations directly from the orchestrator (over Pod IP), completely
# bypassing the K8s API Server for those hot paths.
#
# Build:
#   docker build -f Dockerfile.sandbox -t <acr>/sandbox-python:3.11 .
#
FROM python:3.11-slim

# Install agent dependencies (tiny footprint)
COPY agent/requirements.txt /opt/agent/requirements.txt
RUN pip install --no-cache-dir -r /opt/agent/requirements.txt

# Copy agent code
COPY agent/ /opt/agent/

# Create workspace directory
RUN mkdir -p /workspace

WORKDIR /workspace

ENV AGENT_PORT=8080
ENV WORKING_DIR=/workspace

EXPOSE 8080

# Start the agent in the background, then keep the container alive.
# The agent listens on 0.0.0.0:8080 for orchestrator commands.
CMD ["sh", "-c", "python /opt/agent/server.py & sleep infinity"]

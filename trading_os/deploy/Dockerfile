# syntax=docker/dockerfile:1

# Trading OS & Journal — production image for 24/7 headless deployment (v1.8).
#
# Build from the REPOSITORY ROOT so the whole package is in the build context:
#     docker build -f trading_os/deploy/Dockerfile -t trading-os .
#
# The journal database, screenshots and backups all live on a volume mounted at /data,
# so the container itself stays disposable.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TRADING_OS_DATA_DIR=/data \
    TRADING_OS_PORT=8765

WORKDIR /app

# Install dependencies first so the layer is cached until requirements change.
COPY trading_os/requirements.txt /app/trading_os/requirements.txt
RUN pip install --require-hashes=false -r /app/trading_os/requirements.txt

# Then the application package.
COPY trading_os/ /app/trading_os/

# Run as an unprivileged user that owns the data volume.
RUN useradd --system --create-home --uid 10001 tradingos \
    && mkdir -p /data \
    && chown -R tradingos:tradingos /app /data
USER tradingos

VOLUME ["/data"]
EXPOSE 8765

# Liveness probe: the orchestrator restarts the container if the app or its database stops answering.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=4).status==200 else 1)"]

# Bind 0.0.0.0 so the mapped port is reachable; the app still only answers to localhost, IP addresses and the host
# names listed in TRADING_OS_ALLOWED_HOSTS, and refuses every other Host header (no DNS rebinding).
CMD ["python", "trading_os/run.py", "--host", "0.0.0.0", "--port", "8765", "--no-browser", "--data-dir", "/data"]

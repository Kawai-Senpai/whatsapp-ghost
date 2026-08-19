# WhatsApp Ghost - a contract-compatible local WhatsApp Cloud API sandbox.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first: this layer is cached until pyproject.toml changes.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

# Runtime assets the package needs but does not install.
COPY examples/ ./examples/
COPY contracts/ ./contracts/

# The database and uploaded media live here; mount a volume to keep them.
ENV WABA_DATA_DIR=/data
RUN mkdir -p /data

# Run unprivileged, and let the app own its data directory.
RUN useradd --create-home --uid 10001 ghost && chown -R ghost:ghost /data /app
USER ghost

EXPOSE 8787

# Listens on all interfaces so the container is reachable from the host.
CMD ["python", "-m", "uvicorn", "whatsapp_ghost.api:app", "--host", "0.0.0.0", "--port", "8787"]

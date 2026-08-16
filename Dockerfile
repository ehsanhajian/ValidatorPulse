FROM python:3.12-slim-bookworm

WORKDIR /app

RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin vp

COPY pyproject.toml ./
COPY validator_pulse ./validator_pulse
COPY templates ./templates
COPY static ./static

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir . \
    && chown -R vp:vp /app

USER vp

ENV HOST=0.0.0.0 \
    PORT=3000 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UVICORN_RELOAD=false

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import socket; socket.create_connection(('127.0.0.1', 3000), 2).close()"

CMD ["python", "-m", "validator_pulse"]

FROM node:22-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH
RUN python -m venv /opt/venv \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin canvas
WORKDIR /app
COPY pyproject.toml alembic.ini ./
COPY backend/ backend/
COPY migrations/ migrations/
RUN pip install --no-cache-dir .
COPY --from=frontend /build/frontend/dist frontend/dist
COPY docker/entrypoint.sh /usr/local/bin/canvas-helper-entrypoint
RUN chmod 0555 /usr/local/bin/canvas-helper-entrypoint \
    && mkdir -p /data \
    && chown canvas:canvas /data
USER canvas
EXPOSE 8000
VOLUME ["/data"]
ENTRYPOINT ["canvas-helper-entrypoint"]
CMD ["uvicorn", "canvas_helper.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]

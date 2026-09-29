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
COPY pyproject.toml alembic.ini requirements.lock ./
COPY backend/ backend/
COPY migrations/ migrations/
# Install the pinned dependency set first, then the package itself without
# resolving again, so an image rebuilt months later gets the same versions.
# The 'ai' extra is not optional for the server image: without it every
# /api/ai/chat request fails with "Install the optional 'ai' dependency".
RUN pip install --no-cache-dir -r requirements.lock \
    && pip install --no-cache-dir --no-deps ".[ai]"
COPY --from=frontend /build/frontend/dist frontend/dist
# Point the app at the built frontend explicitly, so serving it does not
# depend on the working directory the container happens to start in.
ENV CANVAS_HELPER_FRONTEND_DIR=/app/frontend/dist
COPY docker/entrypoint.sh /usr/local/bin/canvas-helper-entrypoint
RUN chmod 0555 /usr/local/bin/canvas-helper-entrypoint \
    && mkdir -p /data \
    && chown canvas:canvas /data
USER canvas
EXPOSE 8000
VOLUME ["/data"]
ENTRYPOINT ["canvas-helper-entrypoint"]
CMD ["uvicorn", "canvas_helper.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]

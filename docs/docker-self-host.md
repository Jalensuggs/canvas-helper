# Docker self-hosting

Copy `.env.example` to a protected deployment environment file and replace
every `change-me` value. Generate the encryption key with:

```bash
python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

Set `CANVAS_HELPER_PUBLIC_URL` to the final HTTPS origin, then start:

```bash
docker compose up -d --build
docker compose ps
```

The API is bound to loopback by default. Put Caddy, nginx, or another trusted
TLS proxy in front; `docker/Caddyfile.example` is a starting point. The API
container applies migrations before serving, and the worker waits for its
healthcheck. Enable the currently optional, application-independent MinIO
service with `--profile object-storage`.

Do not expose PostgreSQL, MinIO, or port 8000 directly to the internet. Pin
container image digests for controlled production rollouts.

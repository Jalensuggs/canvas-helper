# Threat model

## Assets and boundaries

Protected assets are Canvas tokens, AI keys, course material, notes, sessions,
and encrypted server credentials. Boundaries are the local Tauri webview and
loopback sidecar, Canvas/AI HTTPS APIs, and the self-hosted reverse proxy/API,
worker, PostgreSQL, SMTP, and optional object-storage services.

## Primary threats and controls

- A hostile website targets localhost: loopback host/origin validation and the
  required mutation header reject ambient browser requests.
- Path traversal or malicious Canvas content: resolved-path containment,
  symlink checks, HTML sanitization, and no SPA fallback for file-like/API URLs.
- Token disclosure: desktop Keychain storage, encrypted server credentials,
  redacted logging, and no credential read API.
- Cross-tenant access: authenticated user scoping in server queries plus CSRF
  cookies/headers and rotating server sessions.
- Supply-chain or accidental secret commits: lockfiles, dependency audits,
  secret scanning, and runtime-data CI checks.
- Sidecar duplication/orphaning: Tauri single instance, backend file lock,
  parent-owned child process, atomic port discovery, and exit cleanup.

## Residual risks

A compromised user account or host can read that user's displayed data.
Administrators with database, volume, or encryption-key access are trusted.
Optional AI providers receive selected prompt context. TLS and reliable backups
are operator responsibilities. MinIO is supplied as an optional deployment
building block but is not currently used by application storage.

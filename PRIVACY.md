# Privacy

Canvas Helper is local-first. The desktop app stores its SQLite database and
downloaded materials in the operating system app-data directory. Canvas and AI
credentials are stored in the operating-system Keychain and are never returned
by the API.

The app contacts the configured Canvas host and, only when enabled, the chosen
AI provider. AI prompts may include the sources displayed for that request;
review provider terms before enabling AI. The project includes no analytics,
advertising SDK, or telemetry.

In server mode, operators control the PostgreSQL database, material volume,
SMTP service, logs, backups, and retention. Users should contact that operator
for export or deletion. Removing the desktop app does not necessarily remove
its app-data directory or Keychain entries; see `docs/backup-upgrade.md`.

# Backup, restore, and upgrade

## Desktop

Quit Canvas Helper before copying its app-data directory. Back up the SQLite
database and `materials/`; credentials remain in the operating-system
Keychain and should be re-entered after a machine migration. Restore into the
same app-data location before launch.

## Docker

Back up PostgreSQL with `pg_dump` and archive the `app_data` volume. If the
optional MinIO profile is used independently, back up its buckets too. Keep
the credential encryption key in a separate secret manager: database backups
cannot decrypt credentials without it.

## Upgrade

Take a verified backup, pull/build the desired version, and run
`docker compose up -d --build`. The API applies forward migrations before it
becomes healthy. Check logs and `/health`. Rollback may require restoring the
pre-upgrade database; Alembic downgrades are not guaranteed. Never rotate the
credential encryption key without a migration procedure.

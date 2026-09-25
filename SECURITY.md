# Security Policy

Report suspected vulnerabilities privately to the project maintainer. Do not
open a public issue containing credentials, private course content, or an
exploitation recipe. Include affected versions, impact, reproduction steps,
and a safe contact method. Maintainers should acknowledge a report within
seven days and coordinate disclosure after a fix is available.

Only the current release is supported. Rotate Canvas/API tokens and the server
credential encryption key if exposure is suspected. Desktop users should also
remove the `canvas-helper:*` Keychain entry and reconnect.

Self-hosters are responsible for TLS termination, SMTP security, host
patching, backups, and restricting the API port to the reverse proxy.

# Local development

Prerequisites: Python 3.12+, Node 22+, and optionally stable Rust plus the
platform Tauri 2 prerequisites.

```bash
make install
make dev
```

Run `make backend` and `make frontend` in separate terminals. Open
`http://127.0.0.1:5173`. Configuration is read only from
`CANVAS_HELPER_*` environment variables; `.env` is not auto-loaded.

Useful checks:

```bash
make test
make migrate
make desktop-check
```

Desktop development requires PyInstaller and Rust. `make desktop-dev` builds a
target-triple-named sidecar before launching Tauri. The app-data database is
separate from the repository development database.

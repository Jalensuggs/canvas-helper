# Contributing

Use Python 3.12+, Node 22+, and stable Rust. Create a branch, keep changes
focused, and never commit Canvas tokens, `.env` files, databases, downloaded
course material, or generated signing keys.

```bash
make install
make check
```

Schema changes require an Alembic revision and successful upgrades against
both SQLite and PostgreSQL. Security-sensitive changes should include
regression tests and update `docs/threat-model.md`. UI changes must retain
keyboard access and readable focus states.

By contributing, you agree that your work is licensed under the MIT License.

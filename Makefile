.PHONY: install install-desktop dev backend frontend migrate test lint build check \
	lock desktop-sidecar desktop-dev desktop-build desktop-check docker-build docker-config

# pyproject requires >=3.12; picking the interpreter explicitly turns a
# confusing pip resolution error into an obvious missing-interpreter one.
PYTHON ?= python3.12

install:
	$(PYTHON) -m venv .venv
	.venv/bin/python -m pip install -e ".[ai,dev,test]"
	cd frontend && npm ci

lock:
	rm -rf .lockenv
	$(PYTHON) -m venv .lockenv
	.lockenv/bin/python -m pip install --quiet --upgrade pip
	.lockenv/bin/python -m pip install --quiet ".[ai]"
	printf '%s\n' \
		'# Pinned runtime dependencies for the server image.' \
		'#' \
		'# Regenerate after changing pyproject.toml:' \
		'#     make lock' \
		'#' \
		'# Resolved on Python 3.12 / linux for the "ai" extra. Without a lock, a rebuilt' \
		'# image silently picks up new majors of SQLAlchemy, FastAPI and the provider' \
		'# SDKs; the Postgres driver default in particular moved between SQLAlchemy' \
		'# 2.0 and 2.1.' > requirements.lock
	.lockenv/bin/python -m pip freeze --exclude-editable | grep -v '^canvas-helper' | sort >> requirements.lock
	rm -rf .lockenv

install-desktop: install
	.venv/bin/python -m pip install -e ".[desktop]"

dev:
	@echo "Run 'make backend' and 'make frontend' in separate terminals."

backend:
	.venv/bin/uvicorn canvas_helper.main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload

frontend:
	cd frontend && npm run dev

migrate:
	.venv/bin/alembic upgrade head

test:
	.venv/bin/pytest -q
	cd frontend && npm test

# Correctness linting only. `ruff format` would rewrite most of the tree, so
# adopting it deserves its own commit rather than riding along with CI.
lint:
	.venv/bin/ruff check backend tests scripts migrations

build:
	cd frontend && npm run build

check: lint test build docker-config

desktop-sidecar: build
	.venv/bin/python scripts/build_sidecar.py

# The tauri CLI finds the project by looking for tauri.conf.json in a subfolder
# of the working directory, so it runs from the repository root, not frontend/.
# It still runs beforeDevCommand/beforeBuildCommand in the frontend directory.
TAURI ?= ./frontend/node_modules/.bin/tauri

desktop-dev: desktop-sidecar
	$(TAURI) dev

desktop-build: desktop-sidecar
	$(TAURI) build

# cargo check needs the sidecar first: tauri.conf.json declares it as an
# externalBin, and a missing one fails the build script.
desktop-check: desktop-sidecar
	cd src-tauri && cargo check

docker-build:
	docker build -t canvas-helper:local .

docker-config:
	POSTGRES_PASSWORD=check CANVAS_HELPER_PUBLIC_URL=https://canvas.example.test \
	CANVAS_HELPER_CREDENTIAL_ENCRYPTION_KEY=MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA= \
	CANVAS_HELPER_EMAIL_FROM=check@example.test CANVAS_HELPER_SMTP_HOST=smtp.example.test \
	CANVAS_HELPER_SMTP_USERNAME=check CANVAS_HELPER_SMTP_PASSWORD=check \
	docker compose config --quiet

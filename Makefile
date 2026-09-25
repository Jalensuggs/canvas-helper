.PHONY: install install-desktop dev backend frontend migrate test build check \
	desktop-sidecar desktop-dev desktop-build desktop-check docker-build docker-config

install:
	python3 -m venv .venv
	.venv/bin/python -m pip install -e ".[ai,test]"
	cd frontend && npm ci

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

build:
	cd frontend && npm run build

check: test build docker-config

desktop-sidecar: build
	.venv/bin/python scripts/build_sidecar.py

desktop-dev: desktop-sidecar
	cd frontend && npx tauri dev --config ../src-tauri/tauri.conf.json

desktop-build: desktop-sidecar
	cd frontend && npx tauri build --config ../src-tauri/tauri.conf.json

desktop-check:
	cd src-tauri && cargo check

docker-build:
	docker build -t canvas-helper:local .

docker-config:
	POSTGRES_PASSWORD=check CANVAS_HELPER_PUBLIC_URL=https://canvas.example.test \
	CANVAS_HELPER_CREDENTIAL_ENCRYPTION_KEY=MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA= \
	CANVAS_HELPER_EMAIL_FROM=check@example.test CANVAS_HELPER_SMTP_HOST=smtp.example.test \
	CANVAS_HELPER_SMTP_USERNAME=check CANVAS_HELPER_SMTP_PASSWORD=check \
	docker compose config --quiet

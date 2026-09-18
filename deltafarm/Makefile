.PHONY: setup dev demo backend frontend test build clean

# Einmalige Einrichtung: virtuelle Umgebung, Python- und Node-Pakete.
setup:
	uv venv
	uv pip install -e ".[dev]"
	cd web && npm install --no-fund --no-audit

# Backend und Frontend zusammen starten.
dev:
	./run.sh

backend:
	.venv/bin/python -m app.main

frontend:
	cd web && npm run dev -- --host 127.0.0.1

test:
	.venv/bin/python -m pytest -q

build:
	cd web && npm run build

clean:
	rm -rf .venv web/node_modules web/dist .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

demo:
	.venv/bin/python -m app.demo

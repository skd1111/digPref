# =====================================================================
# Enterprise Local AI IDE Agent — Makefile
# Single entrypoint for the most common dev workflows.
# =====================================================================

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

# ---------- Variables ----------
PYTHON        ?= python3
UV            ?= uv
PNPM          ?= pnpm
CARGO         ?= cargo
DOCKER        ?= docker
COMPOSE_FILE  ?= infra/docker/docker-compose.dev.yml

# ---------- Help ----------
.PHONY: help
help: ## Show this help.
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ---------- Bootstrap ----------
.PHONY: bootstrap
bootstrap: ## Check & install toolchain (uv / pnpm / rust / tauri).
	@echo ">> Checking toolchain..."
	@command -v $(UV)    >/dev/null 2>&1 || (echo "Install uv: https://docs.astral.sh/uv/" && exit 1)
	@command -v $(PNPM)  >/dev/null 2>&1 || (echo "Install pnpm: npm i -g pnpm"           && exit 1)
	@command -v $(CARGO) >/dev/null 2>&1 || (echo "Install rust: https://rustup.rs/"     && exit 1)
	@$(CARGO) tauri --version >/dev/null 2>&1 || (echo "Install tauri-cli: cargo install tauri-cli --version '^2.0'" && exit 1)
	@echo ">> Toolchain OK."

# ---------- Install ----------
.PHONY: install
install: install-py install-js install-rust ## Install all workspace deps.

.PHONY: install-py
install-py: ## Install Python deps via uv workspace.
	$(UV) sync --all-packages

.PHONY: install-js
install-js: ## Install JS deps via pnpm workspace.
	cd apps/desktop && $(PNPM) install

.PHONY: install-rust
install-rust: ## Install Rust deps for Tauri.
	cd apps/desktop/src-tauri && $(CARGO) fetch

# ---------- Dev ----------
.PHONY: dev
dev: ## Start Agent + MCP servers (Docker).
	$(DOCKER) compose -f $(COMPOSE_FILE) up --build

.PHONY: dev-agent
dev-agent: ## Run Python Agent locally (no Docker).
	cd services/agent && $(UV) run uvicorn agent.main:app --reload --host 127.0.0.1 --port 8765

.PHONY: dev-desktop
dev-desktop: ## Run Tauri desktop in dev mode.
	cd apps/desktop && $(PNPM) tauri dev

# ---------- Test ----------
.PHONY: test
test: test-py test-js test-rust ## Run all tests.

.PHONY: test-py
test-py:
	$(UV) run pytest -q

.PHONY: test-js
test-js:
	cd apps/desktop && $(PNPM) test

.PHONY: test-rust
test-rust:
	cd apps/desktop/src-tauri && $(CARGO) test

# ---------- Lint / Format ----------
.PHONY: lint
lint: lint-py lint-js lint-rust ## Lint everything.

.PHONY: lint-py
lint-py:
	$(UV) run ruff check .
	$(UV) run ruff format --check .

.PHONY: lint-js
lint-js:
	cd apps/desktop && $(PNPM) lint

.PHONY: lint-rust
lint-rust:
	cd apps/desktop/src-tauri && $(CARGO) clippy --all-targets -- -D warnings

.PHONY: fmt
fmt: ## Auto-format everything.
	$(UV) run ruff format .
	cd apps/desktop && $(PNPM) format
	cd apps/desktop/src-tauri && $(CARGO) fmt

# ---------- Build ----------
.PHONY: build
build: build-agent build-desktop ## Build all artifacts.

.PHONY: build-agent
build-agent:
	cd services/agent && $(UV) build

.PHONY: build-desktop
build-desktop:
	cd apps/desktop && $(PNPM) tauri build

# ---------- Clean ----------
.PHONY: clean
clean: ## Remove build artifacts & caches.
	rm -rf .venv **/__pycache__ **/*.pyc .pytest_cache .mypy_cache .ruff_cache
	rm -rf apps/desktop/node_modules apps/desktop/dist
	rm -rf apps/desktop/src-tauri/target
	rm -rf services/**/dist services/**/build

.PHONY: nuke
nuke: clean ## Also remove lockfiles & node_modules (full reset).
	rm -f uv.lock apps/desktop/pnpm-lock.yaml
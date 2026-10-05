# Find Yourself cross-platform Makefile (Batch J / §8)
# Supports Linux / macOS / Windows development workflows.

.PHONY: help install test test-backend test-frontend lint lint-fix typecheck sync-version build build-docker up down deploy-web

PYTHON ?= python
UV ?= uv
NPM ?= npm

help:
	@echo "Available targets:"
	@echo "  make install       Install all backend and frontend dependencies"
	@echo "  make test          Run backend and frontend tests"
	@echo "  make test-backend  Run backend pytest suite"
	@echo "  make test-frontend Run frontend vitest suite"
	@echo "  make lint          Run ruff code checks and formatting inspection"
	@echo "  make lint-fix      Run ruff check --fix"
	@echo "  make typecheck     Run frontend typescript typecheck"
	@echo "  make sync-version  Sync version across pyproject, package.json, tauri"
	@echo "  make build         Sync version and build web frontend"
	@echo "  make build-docker  Build production Docker image"
	@echo "  make up            Start production compose stack"
	@echo "  make down          Stop production compose stack"
	@echo "  make deploy-web    Deploy web frontend to Cloudflare Pages"

install:
	$(UV) sync --all-extras
	$(NPM) --prefix web install

test: test-backend test-frontend

test-backend:
	$(UV) run pytest tests/unit

test-frontend:
	$(NPM) --prefix web run test

lint:
	$(UV) run ruff check .

lint-fix:
	$(UV) run ruff check --fix .

typecheck:
	$(NPM) --prefix web run typecheck

sync-version:
	$(PYTHON) scripts/sync_version.py

build: sync-version
	$(NPM) --prefix web run build

build-docker:
	docker build -t find-yourself:latest .

up:
	docker compose -f infra/compose.prod.yml up -d

down:
	docker compose -f infra/compose.prod.yml down

deploy-web:
	$(PYTHON) scripts/sync_version.py
	$(NPM) --prefix web run build

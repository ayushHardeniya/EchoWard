.PHONY: help install dev test lint check clean

.DEFAULT_GOAL := help

help:
	@echo "EchoWard"
	@echo "  make install  Install dependencies"
	@echo "  make dev      Show development commands"
	@echo "  make test     Run backend tests"
	@echo "  make lint     Run lint checks"
	@echo "  make check    Run full project checks"
	@echo "  make clean    Remove generated files"

install:
	python3 -m venv backend/.venv
	backend/.venv/bin/pip install -r backend/requirements.txt
	cd frontend && npm install

dev:
	@echo "Backend:  cd backend && .venv/bin/uvicorn app.main:app --reload"
	@echo "Frontend: cd frontend && npm run dev"

test:
	cd backend && .venv/bin/pytest

lint:
	cd backend && .venv/bin/ruff check .
	cd frontend && npm run lint

check:
	cd backend && .venv/bin/pytest
	cd backend && .venv/bin/ruff check .
	cd frontend && npm run lint
	cd frontend && npx tsc --noEmit
	cd frontend && npm run build

clean:
	rm -rf frontend/.next
	rm -rf frontend/node_modules/.cache
	rm -rf backend/.pytest_cache
	rm -rf backend/.ruff_cache
	rm -rf backend/app/__pycache__
	rm -rf backend/tests/__pycache__
	
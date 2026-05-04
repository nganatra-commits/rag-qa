.PHONY: install dev fmt lint typecheck test ingest run docker

install:
	python -m pip install -e ".[dev]"

dev:
	uvicorn ragqa.main:app --reload --host 127.0.0.1 --port 8000

fmt:
	ruff format src tests scripts

lint:
	ruff check src tests scripts

typecheck:
	mypy src

test:
	pytest -q

ingest:
	python scripts/ingest_pdfs.py

run:
	uvicorn ragqa.main:app --host 0.0.0.0 --port 8000

docker:
	docker build -t ragqa-backend:dev .

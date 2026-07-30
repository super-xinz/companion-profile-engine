.PHONY: install migrate rules test run demo

install:
	python3 -m venv .venv
	.venv/bin/pip install -e '.[dev]'

migrate:
	.venv/bin/alembic upgrade head

rules:
	.venv/bin/profile-rules --source ./rules

test:
	.venv/bin/pytest

run:
	.venv/bin/profile-engine

demo:
	.venv/bin/python scripts/demo.py

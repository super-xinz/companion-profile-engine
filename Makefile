.PHONY: install migrate rules test run demo

CONDA_ENV ?= $(CURDIR)/.conda-env
CONDA_RUN = conda run --no-capture-output -p $(CONDA_ENV)

install:
	conda env update -p $(CONDA_ENV) -f environment.yml --prune
	$(CONDA_RUN) pip install -e '.[dev]'

migrate:
	$(CONDA_RUN) alembic upgrade head

rules:
	$(CONDA_RUN) profile-rules --source ./rules

test:
	PROFILE_SEMANTIC_EXTRACTOR=deterministic $(CONDA_RUN) pytest

run:
	$(CONDA_RUN) profile-engine

demo:
	$(CONDA_RUN) python scripts/demo.py

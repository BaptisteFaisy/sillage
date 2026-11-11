# Sillage — developer commands. CI runs the same targets (5 min budget).

PYTHON ?= python3
PYTEST ?= $(PYTHON) -m pytest
RUFF   ?= $(PYTHON) -m ruff
SRC    := src tests data infra scripts

.PHONY: help install lint format limits test smoke demo bench serve mcp synth assets all clean

help:
	@echo "make install  - dev dependencies"
	@echo "make lint     - ruff check + format --check"
	@echo "make format   - ruff format (writes)"
	@echo "make limits   - < 500 lines per file, < 50 per function"
	@echo "make test     - offline unit + acceptance tests"
	@echo "make smoke    - the whole loop, offline, one command"
	@echo "make demo     - seeded demo: capture, distil, validate, recall"
	@echo "make bench    - replay bench, with and without memory"
	@echo "make serve    - portal + Guichet API on http://127.0.0.1:8080"
	@echo "make mcp      - MCP server on stdio (any MCP client can connect)"
	@echo "make synth    - write the synthetic history under data/generated"
	@echo "make all      - lint + limits + test + smoke (what CI runs)"

install:
	$(PYTHON) -m pip install --user -e ".[dev,api]"

lint:
	$(RUFF) check $(SRC)
	$(RUFF) format --check $(SRC)

format:
	$(RUFF) format $(SRC)
	$(RUFF) check --fix $(SRC)

limits:
	$(PYTHON) scripts/check_limits.py

test:
	PYTHONPATH=src $(PYTEST) -m "not slow"

smoke:
	PYTHONPATH=src $(PYTHON) -m sillage.cli smoke

demo:
	PYTHONPATH=src $(PYTHON) -m sillage.cli demo

bench:
	PYTHONPATH=src $(PYTHON) -m sillage.cli bench

serve:
	PYTHONPATH=src $(PYTHON) -m sillage.cli serve

mcp:
	PYTHONPATH=src $(PYTHON) -m sillage.cli mcp

synth:
	PYTHONPATH=src $(PYTHON) -m sillage.cli synth

all: lint limits test smoke

clean:
	rm -rf .pytest_cache .ruff_cache build dist *.egg-info data/generated

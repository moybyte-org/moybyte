VENV ?= .venv
SYSTEM_PYTHON ?= python3
PYTHON ?= $(VENV)/bin/python
KIDCODE ?= $(VENV)/bin/kidcode

.PHONY: setup test run-example run-headless compile-blocks doctor check-portable

setup:
	$(SYSTEM_PYTHON) -m venv --system-site-packages $(VENV)
	$(PYTHON) -m pip install --no-build-isolation -e '.[dev]'

test:
	$(PYTHON) -m pytest

doctor:
	$(KIDCODE) doctor

run-example:
	$(KIDCODE) run examples/tiny_runner.kcproj

run-headless:
	$(KIDCODE) run examples/tiny_runner.kcproj --headless --frames 60

compile-blocks:
	$(KIDCODE) compile examples/blocks_demo.kcproj

check-portable:
	$(KIDCODE) check-portable examples/tiny_runner.kcproj examples/blocks_demo.kcproj examples/music_player_stub.kcproj

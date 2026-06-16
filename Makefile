VENV ?= .venv
SYSTEM_PYTHON ?= python3
PYTHON ?= $(VENV)/bin/python
KIDCODE ?= $(VENV)/bin/kidcode

.PHONY: setup test run-example run-headless compile-blocks doctor check-portable pack-example export-lilygo-example device-doctor firmware-build-lilygo

setup:
	$(SYSTEM_PYTHON) -m venv --system-site-packages $(VENV)
	$(PYTHON) -m pip install --no-build-isolation -e '.[dev]'

test:
	$(PYTHON) -m pytest

doctor:
	$(KIDCODE) doctor

device-doctor:
	$(KIDCODE) device-doctor --board lilygo_t_deck_plus

run-example:
	$(KIDCODE) run examples/tiny_runner.kcproj

run-headless:
	$(KIDCODE) run examples/tiny_runner.kcproj --headless --frames 60

compile-blocks:
	$(KIDCODE) compile examples/blocks_demo.kcproj

check-portable:
	$(KIDCODE) check-portable examples/tiny_runner.kcproj examples/blocks_demo.kcproj examples/music_player_stub.kcproj examples/radio_pong_stub.kcproj

pack-example:
	$(KIDCODE) pack examples/tiny_runner.kcproj --out /tmp/tiny_runner.kc8

export-lilygo-example:
	$(KIDCODE) export-device examples/tiny_runner.kcproj --board lilygo_t_deck_plus --out /tmp/kidcode_lilygo_t_deck_plus

firmware-build-lilygo:
	pio run -d firmware/lilygo_t_deck_plus

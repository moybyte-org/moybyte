VENV ?= .venv
SYSTEM_PYTHON ?= python3
PYTHON ?= $(VENV)/bin/python
MONITOR_SECONDS ?= 12
LOG ?= /tmp/moybyte_lilygo_serial.log
IDF_PYTHON ?= $(HOME)/.espressif/python_env/idf5.5_py3.10_env/bin/python
MPY_FW_DIR ?= firmware/lilygo_t_deck_plus_mainline
MPY_BUILD_DIR ?= $(MPY_FW_DIR)/.build/micropython/ports/esp32/build-MOYBYTE_TDECK
MPY_APP_BIN ?= dist/tdeck_mainline/moybyte_tdeck_app.bin
MPY_FULL_BIN ?= dist/tdeck_mainline/moybyte_tdeck.bin
# OTA (#53): the bootable app partition is ota_0. With the dual-OTA table it sits at
# 0x20000 (otadata shifted it up from the legacy 0x10000). The app-only cable flash
# below writes here AND clears otadata (MPY_OTADATA_OFFSET) so the bootloader boots the
# slot we just wrote -- not a stale ota_1 left by a prior SD/OTA update. Override for a
# non-OTA single-factory build: make ... MPY_APP_OFFSET=0x10000 MPY_OTADATA_OFFSET=
MPY_APP_OFFSET ?= 0x20000
# OTA boot selector (otadata) region, erased by the cable flash so the bootloader falls
# back to ota_0 (no factory partition). Empty -> skip the erase (non-OTA factory build).
MPY_OTADATA_OFFSET ?= 0x1d000
MPY_OTADATA_SIZE ?= 0x2000

MPY_FLASH_MODE ?= dio

# OTA online update (#53 Phase 3): host a manifest + .bin for Settings -> UPDATE ONLINE.
OTA_PORT ?= 8000
# Two-channel publish (#53): stage artifacts under OTA_ROOT/<channel>/ and serve that
# dir (the systemd host, tools/moybyte-ota.service) so the device pulls stable or beta.
OTA_ROOT ?= $(HOME)/.moybyte-ota

.PHONY: check-venv firmware-build-guition-s3 firmware-build-lilygo-micropython firmware-build-p4 firmware-build-tdeck-mainline firmware-flash-lilygo-micropython firmware-flash-lilygo-micropython-full firmware-flash-lilygo-micropython-full-erase firmware-flash-lilygo-micropython-no-reset firmware-flash-guition-s3 firmware-flash-p4 firmware-flash-tdeck-mainline firmware-monitor-guition-s3 firmware-monitor-lilygo-micropython firmware-monitor-p4 firmware-monitor-tdeck-mainline firmware-run-lilygo-micropython ota-host ota-keygen ota-manifest ota-publish-stable ota-publish-unstable ota-serve ota-serve-install p4-web-push p4-web-stale release setup site site-firmware site-gifs site-hero sync-issues test vendor-libmoy vendor-p8-import

# A PLAIN venv on purpose. Two flags used to live here and both hid bugs on every
# machine but the maintainer's:
#   --system-site-packages   leaked the host's pygame/wheel in, so missing deps only
#                            showed up on a clean machine.
#   --no-build-isolation     forbade pip from fetching a build backend, so the stock
#                            venv setuptools (59.6 on 3.10, absent on 3.12+) failed
#                            with "invalid command 'bdist_wheel'".
# `sim` is installed too: tools/simulate_desktop.py (the first thing a new dev runs)
# needs pygame. Lua needs no extra: the host builds the boards' own vendored
# Lua 5.4 + libmoy binding on demand (runtime/lua_binding.py -- the lupa extra
# was deleted 2026-08-14), so a C compiler is the requirement, same as host audio.
setup:
	$(SYSTEM_PYTHON) -m venv $(VENV)
# A venv seeded by an older distro python can carry setuptools < 64, which has
# no PEP 660 editable hook -- and there is no setup.py to fall back to since the
# .moyproj SDK went. Upgrade first, then install.
	$(PYTHON) -m pip install -q --upgrade pip setuptools
	$(PYTHON) -m pip install -e '.[dev,sim]'
# THE HOST NEEDS A C COMPILER. Not for a nicety -- for the console. The host
# draws on the same libmoy raster both boards run, ctypes-loaded from a .so this
# builds (runtime/gfx_binding.py); the pure-Python raster that used to stand
# behind it was deleted with runtime/canvas.py. So say it HERE, where the person
# who can act on it is still watching, instead of letting them find out at the
# first draw via `AttributeError: 'NoneType' object has no attribute 'hg_fill'`.
# Non-fatal on purpose: the venv and the deps are installed and useful either
# way, and firmware work needs the ESP-IDF toolchain rather than this one.
	@$(PYTHON) -m runtime.native_build || { \
	  echo ""; \
	  echo "  ^^ make setup FINISHED, but the host console will not run until"; \
	  echo "     a C compiler is installed. Re-run 'make setup' after that."; \
	  echo ""; }
# Host audio binding (#97 stage 0): compile vendored libmoy into the cached
# .so the sim's AudioEngine loads. Never fails setup -- with no C compiler it
# prints a note and the host runs silent (which, since the check above, is the
# smaller half of what a missing compiler costs).
	$(PYTHON) -m runtime.audio_binding

# Without this, every venv-backed target below dies with a bare
# "/bin/sh: .venv/bin/python: No such file or directory".
check-venv:
	@test -x $(PYTHON) || { echo "no venv at $(VENV)/ -- run: make setup"; exit 1; }

VENV_TARGETS := test \
                site-gifs site-hero sync-issues release ota-keygen \
                ota-manifest ota-serve ota-publish-unstable \
                ota-publish-stable ota-host ota-serve-install firmware-flash-p4 \
                firmware-monitor-p4 firmware-flash-guition-s3 firmware-monitor-guition-s3
$(VENV_TARGETS): check-venv

# Flashing/monitoring needs a board on a serial port, and the T-Deck images need the
# ESP-IDF 5.5 toolchain. Both are legitimately environment-dependent -- say which
# piece is missing instead of failing with a bare `test` or "No such file".
REQUIRE_PORT = @test -n "$(PORT)" || { echo "PORT is not set -- e.g. make $@ PORT=/dev/ttyACM0 (try: make device-port)"; exit 1; }
REQUIRE_IDF = @test -x $(IDF_PYTHON) || { echo "no ESP-IDF python at $(IDF_PYTHON) -- install the ESP-IDF 5.5 toolchain (see $(MPY_FW_DIR)/README.md) or pass IDF_PYTHON=..."; exit 1; }
# esptool/pyserial are the `device` extra: `make setup` does not install them
# because flashing needs hardware. The P4 targets run them from the project venv.
REQUIRE_ESPTOOL = @$(PYTHON) -c "import esptool" >/dev/null 2>&1 || { echo "esptool is not installed -- run: $(PYTHON) -m pip install -e '.[device]'"; exit 1; }
REQUIRE_PYSERIAL = @$(PYTHON) -c "import serial" >/dev/null 2>&1 || { echo "pyserial is not installed -- run: $(PYTHON) -m pip install -e '.[device]'"; exit 1; }

# The suite is ~2000 host tests with no shared mutable state, so it parallelizes
# cleanly: 3m25s -> ~42s on 12 cores, same pass/fail set. `-n auto` scales to the
# machine (CI included). Serial fallback: make test JOBS=0
#
# The two env vars are not belt-and-braces; they fix different things.
#
# `env -u PYTHONPATH` is the ROOT cause. `make setup` builds a hermetic venv (no
# --system-site-packages), but a venv only isolates site-packages -- it cannot
# undo PYTHONPATH, which Python injects into sys.path REGARDLESS, and ahead of
# the venv's own packages. A sourced ROS setup.bash exports /opt/ros/..., so on
# this machine sys.path[1:3] was ROS and `import launch_testing` resolved outside
# the venv. That is a general shadowing hazard (any ROS package outranks the same
# name in .venv), not just a pytest one; the tests are simply where it surfaced.
#
# PYTEST_DISABLE_PLUGIN_AUTOLOAD is then a SPEED lever, not a correctness one:
# pytest otherwise imports every plugin it can find via entry points, in each of
# the N workers. Measured with PYTHONPATH already cleared: 75s autoloading vs 42s
# without. We use no pytest plugin except xdist, which -p loads explicitly.
#
# (pyproject's addopts additionally blocks the two ROS plugins by name. That is
# for a BARE `pytest tests/foo.py`, which gets neither of these env vars.)
JOBS ?= auto
PYTEST_ENV = env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
PYTEST_FLAGS = $(if $(filter-out 0,$(JOBS)),-p xdist -n $(JOBS),)

test:
	$(PYTEST_ENV) $(PYTHON) -m pytest $(PYTEST_FLAGS)

# ---------------------------------------------------------------------------
# The desktop MicroPython that the COMPILED-VS-COMPILED checks run against.
#
# tests/test_gfx_binding.py::test_matches_the_native_moy_gfx drives the same
# ops through the host's ctypes binding and through the REAL native moy_gfx,
# and diffs the framebuffers. That is the only lane in `make test` where two
# independently COMPILED kernels are compared -- everything else either
# compares the host to itself or compares it to a transcription, and a
# transcription can be right while the C is wrong (CLAUDE.md records the
# provisional_tline day). The same binary carries `moycore` and `moy_audio`,
# so tests/test_moycore_loop.py, tests/test_semantic_traces.py and
# test_audio_parity's native pass are the other consumers.
#
# It used to be a HAND-BUILT artifact under firmware/.../.build (prose
# instructions in native/moycore/README.md), gitignored and built by nobody --
# so the check passed on the one machine that had it and SILENTLY SKIPPED
# everywhere else, which is the exact failure mode a parity test exists to
# prevent. Hence a real target, and a CI step that runs it.
#
# ~15s from cold (2s clone, 4s submodules, 2s mpy-cross, 5s compile on 12
# cores) and under a second warm, which is why there is no cache to go stale --
# a cache MISS that silently skipped the check is the failure being fixed here,
# so the cheapest honest answer is to always build.
#
# The two overrides trim the standard variant's system dependencies down to a
# compiler, because a build that needs more than that on some machine turns
# back into a build that does not happen. MICROPY_PY_SSL=0 drops the only
# submodule it would otherwise want (mbedtls); MICROPY_PY_FFI=0 drops libffi.
# Nothing on either side of a raster/VM parity check speaks TLS or ctypes.
UNIX_MP_TAG ?= v1.28.0
UNIX_MP_DIR ?= .build/unix_micropython
UNIX_MP_SRC := $(UNIX_MP_DIR)/micropython
UNIX_MP_USERMODS := $(UNIX_MP_DIR)/usermods
UNIX_MP := $(UNIX_MP_SRC)/ports/unix/build-moybyte/micropython
UNIX_MP_NATIVE := native
# Every native module that ships a Makefile fragment. moy_alloc/moy_sd have
# none (ESP-IDF only) and are skipped by the port's own discovery anyway.
# moy_web is here so the BAKED web console is exercised as code on a real
# MicroPython -- its memoryview is handed straight at flash-mapped rodata and
# must stay read-only, which is the kind of thing that otherwise fails first on
# glass. Its blob table is generated (see the recipe below).
UNIX_MP_MODULES ?= moy_gfx moy_lua moycore moy_audio moy_web
UNIX_MP_JOBS ?= $(shell nproc 2>/dev/null || echo 4)

.PHONY: unix-micropython
unix-micropython:
	@mkdir -p $(UNIX_MP_DIR) $(UNIX_MP_USERMODS)
# moy_web's blob table is build OUTPUT (gitignored): it .incbin's the web
# runner's bundle, so on a tree that has never built one it has to exist as an
# empty table or this build stops on a missing source file. REQUIRE=0 and quiet
# on purpose: the generator is strict under CI, where this binary is built and
# where no wasm bundle exists -- and "this image has no browser console" is not
# news about a test binary that is not an image.
	@MOYBYTE_REQUIRE_WEB_BUNDLE=0 $(PYTHON) tools/gen_web_blob.py --quiet >/dev/null
	@test -d $(UNIX_MP_SRC)/.git || git clone --depth 1 --branch $(UNIX_MP_TAG) \
	    --quiet https://github.com/micropython/micropython $(UNIX_MP_SRC)
# Outside the clone guard on purpose (the web runner's build.sh learned the
# same lesson): a checkout can exist WITHOUT its submodules if a previous run
# died between the two, and a guarded init could never repair that.
	@cd $(UNIX_MP_SRC) && git submodule update --init --depth 1 --quiet \
	    lib/micropython-lib lib/berkeley-db-1.xx
# Symlinks, not copies: the modules under test must be the working tree's, or
# this proves the parity of a stale snapshot.
	@for m in $(UNIX_MP_MODULES); do \
	    ln -sfn $(abspath $(UNIX_MP_NATIVE))/$$m $(UNIX_MP_USERMODS)/$$m; done
# The frozen bytecode carries a SUPPLEMENTARY qstr enum: every name the frozen
# modules use that the compiled pool does not already have. A usermod that adds
# or drops one of those names moves it across that boundary, and upstream's own
# rule regenerates frozen_content.c from a pool that this same pass is still
# rebuilding -- so the file goes stale, `redeclaration of enumerator MP_QSTR_x`
# (or the reverse once it is gone), and re-running make does NOT converge. It
# cost a confused half hour when moy_web landed with a `names()` verb.
# Regenerating is ~5s and only happens when a usermod source actually changed.
	@f=$(UNIX_MP_SRC)/ports/unix/build-moybyte/frozen_content.c; \
	  if [ -f "$$f" ] && [ -n "$$(find -L $(UNIX_MP_USERMODS)/ -name '*.[ch]' \
	      -newer "$$f" -print -quit 2>/dev/null)" ]; then rm -f "$$f"; fi
	@$(MAKE) --no-print-directory -C $(UNIX_MP_SRC)/mpy-cross -j$(UNIX_MP_JOBS)
	@$(MAKE) --no-print-directory -C $(UNIX_MP_SRC)/ports/unix \
	    VARIANT=standard MICROPY_PY_SSL=0 MICROPY_PY_FFI=0 BUILD=build-moybyte \
	    USER_C_MODULES=$(abspath $(UNIX_MP_USERMODS)) -j$(UNIX_MP_JOBS)
	@echo "desktop MicroPython with the native usermods: $(UNIX_MP)"

# Build the project site into _site/ (the GitHub Pages source). Embeds the web
# runner's dist/ as the playable player, so build that first for a live page:
#   firmware/web_runner/build.sh && make site
# It also embeds whatever `make site-firmware` last pulled down, which is what
# the page's board flasher writes; without it the flash section says so.
site:
	$(PYTHON) site/build.py

# Pull both boards' current firmware into dist/ci-firmware for the site's
# flasher: the rolling `firmware-latest` release, falling back per board to a
# live run artifact. Needs the `gh` CLI, authenticated.
site-firmware:
	$(SYSTEM_PYTHON) tools/fetch_ci_firmware.py

# Regenerate the teaser-site demo GIFs from the real console (headless).
site-gifs:
	$(PYTHON) tools/make_site_gifs.py

# Re-record the site's hero shot (site/hero.gif, committed -- the Pages job has
# no Pillow). moy_night is deliberate: the site's colours ARE that wallpaper's.
site-hero:
	$(PYTHON) tools/make_site_gifs.py --windowed --scene code \
		--wallpaper moy_night --out $(CURDIR)/site
	mv $(CURDIR)/site/code.gif $(CURDIR)/site/hero.gif

# Mirror GitHub issues into docs/issues/ (open/ + closed/ + INDEX.md) so issue
# numbers referenced in commits/docs/chat resolve locally. Needs the `gh` CLI, authed.
sync-issues:
	$(PYTHON) tools/sync_issues.py

# Re-vendor libmoy -- moy-spec's C console -- from a checkout beside this one
# (or SPEC=/path, or $MOYBYTE_MOY_SPEC). Copies the pinned file set, re-stamps
# native/libmoy_vendor.json, and tests/test_libmoy_vendor.py holds it to that:
# a vendored file edited HERE is a red test rather than a change that survives
# until the next re-vendor silently reverts it.
#   make vendor-libmoy
#   make vendor-libmoy SPEC=/path/to/moy-spec
vendor-libmoy:
	$(PYTHON) tools/vendor_libmoy.py $(if $(SPEC),--spec $(SPEC))

# Re-vendor moy-spec's PICO-8 asset converter (tools/p8_import.py), the same way
# and for the same reason: SPEC.md 8.1 is what says what a converted note MEANS,
# so the converter belongs upstream and travels HERE. It was a hand-copy once;
# upstream corrected PICO-8's pitch offset, the copy never heard, and every cart
# imported here played two octaves flat for ten days with a green suite.
# tests/test_p8_import_vendor.py is what makes that loud now.
#   make vendor-p8-import
#   make vendor-p8-import SPEC=/path/to/moy-spec
vendor-p8-import:
	$(PYTHON) tools/vendor_p8_import.py $(if $(SPEC),--spec $(SPEC))

# The T-Deck build (mainline MicroPython -- the only T-Deck build since the
# fork's deletion, 2026-08-17). The `lilygo-micropython` names below are the
# fork-era spelling, kept as aliases because the site, CI echoes and muscle
# memory all use them.
firmware-build-tdeck-mainline:
	bash firmware/lilygo_t_deck_plus_mainline/build.sh

firmware-build-lilygo-micropython: firmware-build-tdeck-mainline	## alias (fork-era name)

# OTA (#53 Phase 3): emit dist/latest.json from the built image (auto size + sha256 +
# version read from moy_ota.FIRMWARE_VERSION). Point it at your host with OTA_BASE_URL;
# with none it uses http://<LAN-IP>:$(OTA_PORT) for a local test against `make ota-serve`.
#   make ota-manifest                         # local test
#   make ota-manifest OTA_BASE_URL=https://you.example.com/moybyte
ota-manifest:
	$(PYTHON) tools/gen_ota_manifest.py $(if $(OTA_BASE_URL),--base-url $(OTA_BASE_URL)) --port $(OTA_PORT)

# Serve the built T-Deck dist (the .bin + latest.json) over plain HTTP for the
# device to pull from. (The build writes to a repo-root dist/ -- the board-local
# dist/ this once served died with the fork.)
ota-serve:
	cd dist/tdeck_mainline && $(PYTHON) -m http.server $(OTA_PORT)

# The two OTA channels are the two BRANCHES, and CI publishes both (a push to master
# rolls the `firmware-latest` release, a push to dev rolls `firmware-beta`; moy_ota's
# DEFAULT_CHANNEL_URLS point at them, so a device needs no ota.json and no host of its
# owner's). The targets below stay the LOCAL path: publishing from an uncommitted tree,
# or serving a channel on a LAN with no internet. An /sd/update/ota.json still overrides
# the baked defaults, which is how a device is pointed at OTA_ROOT instead of GitHub.
#
# Publish the CURRENT working tree (uncommitted OK) as a BETA build the device can pull
# over WiFi: build with the unstable channel stamp, then copy the image + a matching
# manifest into OTA_ROOT/unstable/. No commit, no PC needed by the tester -- on the
# device: Settings -> CHANNEL = BETA -> UPDATE ONLINE. The beta version is the build
# epoch, so every publish reads as newer than the last.
ota-publish-unstable:
	MOYBYTE_OTA_CHANNEL=unstable $(MAKE) firmware-build-lilygo-micropython
	$(PYTHON) tools/gen_ota_manifest.py --root $(OTA_ROOT) $(if $(OTA_BASE_URL),--base-url $(OTA_BASE_URL)) --port $(OTA_PORT)

# Publish a STABLE build (normally from master) into OTA_ROOT/stable/.
ota-publish-stable:
	MOYBYTE_OTA_CHANNEL=stable $(MAKE) firmware-build-lilygo-micropython
	$(PYTHON) tools/gen_ota_manifest.py --root $(OTA_ROOT) $(if $(OTA_BASE_URL),--base-url $(OTA_BASE_URL)) --port $(OTA_PORT)

# Serve OTA_ROOT (both channels) over HTTP for the device. Foreground; for a persistent
# host use the systemd --user service below.
ota-host:
	mkdir -p $(OTA_ROOT)
	$(PYTHON) -m http.server $(OTA_PORT) --directory $(OTA_ROOT)

# Install + start the OTA host as a systemd --user service so it stays up while the PC
# is on (linger keeps it across logout). Run this yourself once; re-run after changing
# OTA_ROOT/OTA_PORT. (loginctl enable-linger may prompt for sudo.)
ota-serve-install:
	mkdir -p $(OTA_ROOT) $(HOME)/.config/systemd/user
	sed -e 's#@PYTHON@#$(abspath $(PYTHON))#g' -e 's#@ROOT@#$(OTA_ROOT)#g' -e 's#@PORT@#$(OTA_PORT)#g' \
	    tools/moybyte-ota.service > $(HOME)/.config/systemd/user/moybyte-ota.service
	systemctl --user daemon-reload
	systemctl --user enable --now moybyte-ota.service
	loginctl enable-linger $(USER) || true
	@echo "OTA host: serving $(OTA_ROOT) on :$(OTA_PORT) (systemd --user moybyte-ota)"

# Generate the OTA signing key, ONCE. Prints the `gh secret set` line that gives it
# to CI and the OTA_PUBLIC_KEYS constant to bake into the firmware. The private key
# never belongs in the repo; back it up, because a lost key means every deployed
# board needs a USB reflash before it will trust a replacement.
ota-keygen:
	$(PYTHON) -m pip install -q -e '.[release]'
	$(PYTHON) tools/ota_sign.py keygen $(if $(OUT),--out $(OUT))

# Cut a release: merge dev into master and bump moy_ota.FIRMWARE_VERSION (tools/
# release.py explains the whole sequence). Work lands on `dev`, which CI publishes as
# beta; `master` is what users get, so the merge is the release and the bump rides it.
# It stops BEFORE pushing -- pushing master is the moment a device is offered the build.
#   make release
#   make release NOTES="what changed for a device owner"
#   make release PUSH=1
release:
	$(PYTHON) tools/release.py $(if $(NAME),--name "$(NAME)") $(if $(NOTES),--notes "$(NOTES)") $(if $(PUSH),--push)

firmware-flash-lilygo-micropython:
	$(REQUIRE_PORT)
	$(REQUIRE_IDF)
	@[ -z "$(MPY_OTADATA_OFFSET)" ] || $(IDF_PYTHON) tools/esptool_no_modem.py --chip esp32s3 -p $(PORT) -b 460800 --before default_reset --after no_reset erase_region $(MPY_OTADATA_OFFSET) $(MPY_OTADATA_SIZE)
	$(IDF_PYTHON) tools/esptool_no_modem.py --chip esp32s3 -p $(PORT) -b 460800 --before default_reset --after hard_reset write_flash --flash_mode dio --flash_size 16MB --flash_freq 80m 0x0 $(MPY_BUILD_DIR)/bootloader/bootloader.bin 0x8000 $(MPY_BUILD_DIR)/partition_table/partition-table.bin $(MPY_APP_OFFSET) $(MPY_APP_BIN)

firmware-flash-lilygo-micropython-no-reset:
	$(REQUIRE_PORT)
	$(REQUIRE_IDF)
	$(IDF_PYTHON) tools/esptool_no_modem.py --chip esp32s3 -p $(PORT) -b 460800 --before no_reset --after no_reset write_flash --flash_mode dio --flash_size 16MB --flash_freq 80m 0x0 $(MPY_BUILD_DIR)/bootloader/bootloader.bin 0x8000 $(MPY_BUILD_DIR)/partition_table/partition-table.bin $(MPY_APP_OFFSET) $(MPY_APP_BIN)

# The canonical cable flash: facts (chip/offset/baud/otadata/reset strategy)
# come from the board's board.toml [flash] section via tools/board_flash.py
# (#202 Phase A) -- the Makefile no longer restates any of them.
firmware-flash-tdeck-mainline:
	$(REQUIRE_PORT)
	$(REQUIRE_ESPTOOL)
	$(PYTHON) tools/board_flash.py flash firmware/lilygo_t_deck_plus_mainline --port $(PORT)

firmware-flash-lilygo-micropython-full: firmware-flash-tdeck-mainline	## alias (fork-era name)

firmware-flash-lilygo-micropython-full-erase:
	$(REQUIRE_PORT)
	$(REQUIRE_IDF)
	$(IDF_PYTHON) tools/esptool_no_modem.py --chip esp32s3 -p $(PORT) -b 460800 --before no_reset --after hard_reset write_flash --flash_mode $(MPY_FLASH_MODE) --flash_size 16MB --flash_freq 80m --erase-all 0x0 $(MPY_FULL_BIN)

firmware-run-lilygo-micropython:
	$(REQUIRE_PORT)
	$(REQUIRE_IDF)
	$(IDF_PYTHON) tools/esptool_no_modem.py --chip esp32s3 -p $(PORT) --before no_reset --after hard_reset --no-stub run

firmware-monitor-tdeck-mainline:
	$(REQUIRE_PORT)
	$(REQUIRE_PYSERIAL)
	$(PYTHON) tools/board_flash.py monitor firmware/lilygo_t_deck_plus_mainline --port $(PORT)

firmware-monitor-lilygo-micropython: firmware-monitor-tdeck-mainline	## alias (fork-era name)

# ESP32-P4 (Waveshare 7B, #58): mainline-MicroPython build via the board dir's
# build.sh -> dist/p4/moybyte_p4.bin, flashed at 0x2000 (the P4's app offset).
# Serial = CH343 (no native-takeover starvation, REPL stays alive), so plain
# esptool auto-reset works; esptool comes from the project venv. The image
# path and every other flash fact live in the board's board.toml [flash].

firmware-build-p4:
	firmware/esp32_p4_wifi6_touch_lcd_7b/build.sh

# The cable flash writes the app into ota_0 (0x10000, inside the 0x2000 merged
# image) but the BOOTLOADER picks its slot from otadata -- so on a board that has
# taken an OTA and is running ota_1, flashing would appear to do nothing: the new
# image lands in the slot otadata is not pointing at. Clearing otadata makes the
# bootloader fall back to ota_0, which is what was just written. The T-Deck has
# always done this (#53); the P4 needed it the moment it could OTA at all.
# The flash facts (chip/offset/baud/otadata region) live in the board's
# board.toml [flash] section, read by tools/board_flash.py (#202 Phase A) --
# including the otadata-first erase whose rationale the paragraph above
# records. The 0xd000-vs-0x1d000 per-board difference lives THERE now.
firmware-flash-p4:
	$(REQUIRE_PORT)
	$(REQUIRE_ESPTOOL)
	$(PYTHON) tools/board_flash.py flash firmware/esp32_p4_wifi6_touch_lcd_7b --port $(PORT)
	@$(MAKE) --no-print-directory p4-web-push PORT=$(PORT) WEB_PUSH_OPTIONAL=1

# The web console the BOARD serves (`/moy/web`, reached over WiFi) RIDES THE
# FIRMWARE IMAGE now (moy_web / tools/gen_web_blob.py): baking it was ruled out
# while the bundle was ~1.13MB against ~1.04MB of headroom, and pre-gzipping it
# (572,693 B) made it fit both slots. So a flashed board always serves a console
# current with its own firmware, and this push is the OVERRIDE -- storage wins
# over the image, which is what keeps the sub-minute dev loop alive without a
# reflash. (When baking was decided the T-Deck could not be pushed to at all --
# that board's serial RX was dead under the desktop until #201 fixed it on
# 2026-08-16 -- so the image was the only way it could ever be current. It is
# still the guarantee; this push is still the override.) The flash target still
# pushes (optional -- a board with no WiFi must not fail a cable flash), and
# `p4-web-stale` is the check you can run on its
# own. p4_push_web.py compares byte-for-byte per file, so a re-push is
# idempotent and cheap; running it is the verification.
p4-web-push:
	$(REQUIRE_PORT)
	@if [ ! -f firmware/web_runner/dist/micropython.wasm ]; then \
	  echo "no firmware/web_runner/dist -- build it with firmware/web_runner/build.sh"; \
	  [ -n "$(WEB_PUSH_OPTIONAL)" ] || exit 1; \
	else \
	  $(MAKE) --no-print-directory p4-web-stale; \
	  $(PYTHON) tools/p4_push_web.py --port $(PORT) \
	    || { echo "web-console push FAILED (board on WiFi? bundle unchanged on the board)"; \
	         [ -n "$(WEB_PUSH_OPTIONAL)" ] || exit 1; }; \
	fi

# Is the built bundle older than the console sources it was built from? This is
# the staleness the push cannot detect -- p4_push_web only compares dist against
# the BOARD, so a dist that is itself behind `runtime/` pushes a stale bundle and
# reports "all files match".
p4-web-stale:
	@newer=$$(find runtime firmware/web_runner -name '*.py' -newer firmware/web_runner/dist/micropython.wasm 2>/dev/null | head -5); \
	if [ -n "$$newer" ]; then \
	  echo "WARNING: firmware/web_runner/dist is OLDER than console sources, e.g."; \
	  echo "$$newer" | sed 's/^/    /'; \
	  echo "  rebuild it (firmware/web_runner/build.sh) or you will push a stale console."; \
	else \
	  echo "web bundle is newer than every runtime/ and web_runner/ source."; \
	fi

firmware-monitor-p4:
	$(REQUIRE_PORT)
	$(REQUIRE_PYSERIAL)
	$(PYTHON) tools/board_flash.py monitor firmware/esp32_p4_wifi6_touch_lcd_7b --port $(PORT)

# Guition JC3248W535 (#202): the third board, provisioned through the port
# kit -- build via the board dir's build.sh -> dist/guition_s3/, and the
# flash/monitor facts live in its board.toml [flash]/[monitor].

firmware-build-guition-s3:
	firmware/guition_jc3248w535/build.sh

firmware-flash-guition-s3:
	$(REQUIRE_PORT)
	$(REQUIRE_ESPTOOL)
	$(PYTHON) tools/board_flash.py flash firmware/guition_jc3248w535 --port $(PORT)

firmware-monitor-guition-s3:
	$(REQUIRE_PORT)
	$(REQUIRE_PYSERIAL)
	$(PYTHON) tools/board_flash.py monitor firmware/guition_jc3248w535 --port $(PORT)

# T-Deck recovery note: there is NO BOOT BUTTON on a T-Deck. The trackball
# CLICK is GPIO0: hold the trackball in while powering the board on, then
# release, to reach the ROM loader when an image wedges the USB device.
# (The build/flash/monitor targets live above, under their canonical
# firmware-*-tdeck-mainline names; the image + otadata offsets are the MPY_*
# variables at the top of this file.)


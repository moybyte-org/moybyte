"""`board.toml` -- the board declaration, and the reader that has to be trusted.

#161 Phase 3 moved each board's staged module list out of its `build.sh` and
into a declarative file, as a DENYLIST with the reason beside every denial.
`tests/test_staging_closure.py` consumes that file to decide what a fresh build
freezes; this one checks the file and its reader are worth consuming.

THE READER. `tools/board_config.py` parses TOML itself instead of importing
one. That is a real decision with a real cost, so it is tested rather than
asserted: `requires-python` is >=3.10, `tomllib` landed in 3.11, `tomli` is not
a declared dependency of this project, and `build.sh` may be running on nothing
but the system `python3` (BUILD_PYTHON falls back to it when there is no venv).
A third-party import in the build path would mean a board that cannot be built
without `make setup`. So the parser is stdlib, and the test below runs the REAL
implementation beside it on the actual board files -- an independent parser
being the only thing that can tell "my subset is right" from "my subset agrees
with itself". pytest depends on `tomli` below 3.11, so this check runs in CI
rather than being skipped where it matters.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tools import board_config

ROOT = Path(__file__).resolve().parent.parent
TDECK = ROOT / "firmware" / "lilygo_t_deck_plus_mainline"
TDECK_MAINLINE = ROOT / "firmware" / "lilygo_t_deck_plus_mainline"
P4 = ROOT / "firmware" / "esp32_p4_wifi6_touch_lcd_7b"
GUITION = ROOT / "firmware" / "guition_jc3248w535"
# The HEADLESS fourth target (#41), a build target since 2026-08-29. It is in
# BOARDS because every check in this file is about the DECLARATION and its
# reader -- the parser agreeing with a real TOML implementation, build.sh
# obeying board.toml instead of restating it, the flash facts being shaped like
# flash facts -- and none of that cares whether a board has a screen. The two
# tests that do care about a console say so where they narrow.
ZERO = ROOT / "firmware" / "seeed_xiao_esp32s3_zero"
BOARDS = {"tdeck": TDECK, "tdeck-mainline": TDECK_MAINLINE, "p4": P4,
          "guition-s3": GUITION, "zero": ZERO}

try:                                    # 3.11+
    import tomllib as _real_toml
except ImportError:                     # pytest's own dep below 3.11
    try:
        import tomli as _real_toml
    except ImportError:
        _real_toml = None


@pytest.mark.parametrize("board", sorted(BOARDS))
def test_the_stdlib_parser_agrees_with_a_real_toml_implementation(board):
    if _real_toml is None:
        pytest.skip("no tomllib/tomli available to check against")
    text = (BOARDS[board] / "board.toml").read_text(encoding="utf-8")
    assert board_config.loads(text) == _real_toml.loads(text)


def test_the_parser_handles_the_shapes_the_board_files_use():
    """A direct exercise of the subset, so a regression names itself instead of
    surfacing as "the board stages the wrong modules"."""
    cfg = board_config.loads('''
# a comment
[a]
s = "plain"
esc = "a\\"b\\nc"
n = 3
yes = true
arr = ["x", "y"]
multi = [
  "p",     # trailing comma + comment inside an array
  "q",
]
long = """
first
second"""

[a.b]
deep = "yes"

[[a.items]]
k = "1"

[[a.items]]
k = "2"
''')
    assert cfg["a"]["s"] == "plain"
    assert cfg["a"]["esc"] == 'a"b\nc'
    assert cfg["a"]["n"] == 3 and cfg["a"]["yes"] is True
    assert cfg["a"]["arr"] == ["x", "y"] and cfg["a"]["multi"] == ["p", "q"]
    assert cfg["a"]["long"] == "first\nsecond"       # one leading NL trimmed
    assert cfg["a"]["b"]["deep"] == "yes"
    assert [i["k"] for i in cfg["a"]["items"]] == ["1", "2"]


@pytest.mark.parametrize("board", sorted(BOARDS))
def test_build_sh_reads_the_declaration_instead_of_restating_it(board):
    """The declaration is only true if the build obeys it.

    Both halves matter: build.sh must CALL the stager, and it must no longer
    carry a hand-written list of shared modules beside it -- a build that does
    both would drift, silently, in the direction of whichever one a person
    remembered to edit.
    """
    sh = (BOARDS[board] / "build.sh").read_text(encoding="utf-8")
    assert "tools/board_config.py" in sh and "stage" in sh, (
        "%s/build.sh no longer stages via the board declaration" % board)
    stale = [line for line in sh.splitlines()
             if "${REPO_ROOT}/runtime/" in line and line.strip().startswith("cp ")]
    assert not stale, (
        "%s/build.sh hand-copies runtime modules again: %s" % (board, stale))


@pytest.mark.parametrize("board", sorted(BOARDS))
def test_stage_produces_the_declared_set_and_prunes_strays(tmp_path, board):
    """The stager, end to end, against a throwaway `modules/`.

    The prune half is the one worth exercising: the frozen manifest freezes the
    whole modules/ DIRECTORY rather than the list build.sh copied, and that
    directory is gitignored and never cleaned -- so before this landed, a module
    that stopped being staged kept being frozen forever on every tree that had
    built before. It was not theoretical: `canvas.py`, `palette.py` and (on the
    P4) `moy_lua_glue.py` were all still sitting in `modules/`, and therefore
    still in the image, well after the last build that produced them.
    """
    src = BOARDS[board]
    work = tmp_path / src.name
    work.mkdir()
    shutil.copy(src / "board.toml", work / "board.toml")
    dest = work / "modules"
    dest.mkdir()
    # The `keep` names are the board's OWN, so this reads the declaration
    # rather than assuming one -- a hard-coded name here would assert "the
    # prune ate a generated file" about a file some board never produces.
    # (Every board generates a `carts_data.py` since 2026-08-30, but they are
    # not the same file: the console boards' is the plain roster and the
    # Zero's is the packed one -- tests/test_seed_pack.py owns that half.)
    keep = board_config.load(src).get("modules", {}).get("keep", [])
    assert keep, "%s declares no generated files to keep" % board
    generated = keep[0]
    (dest / "palette.py").write_text("# a stray from an older build\n")
    (dest / generated).write_text("# generated by the build\n")
    (dest / "moy_runtime.py").write_text("# board-authored, TRACKED\n")
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run(["git", "add", "modules/moy_runtime.py"], cwd=work, check=True)

    wanted, removed = board_config.stage(work, ROOT, quiet=True)

    assert "palette.py" in removed, "the stale stray survived the prune"
    assert (dest / generated).exists(), "prune ate a generated file"
    assert (dest / "moy_runtime.py").exists(), (
        "prune ate a TRACKED board-authored module -- the one outcome that "
        "must be impossible, since modules/ holds the board's own sources too")
    if board != "zero":
        # The console tier: every board that HAS a console stages it, and stages
        # the font under the name device_canvas imports. The Zero stages neither
        # -- it has no console at all -- which its own row below pins instead.
        assert (dest / "console.py").exists() and (dest / "moy_font.py").exists()
    assert not (dest / "font.py").exists(), "font.py must stage RENAMED only"
    if board.startswith("tdeck") or board.startswith("guition") or board == "zero":
        assert not (dest / "wm_windowed.py").exists()
    on_disk = {p.name for p in dest.glob("*.py")} - {generated, "moy_runtime.py"}
    assert on_disk == set(wanted)


def test_the_p4_stages_the_windowed_tier_and_the_s3_does_not():
    """The one real difference between the two boards' shared module sets.

    Stated here as well as in `test_staging_closure.py` because this is the
    claim `docs/surface_model_v1.md` L6 rests on: `wm.py` and `console.py` are
    frozen verbatim on the S3, so the windowed WM and its `surface` leaf must
    not be reachable there at all.
    """
    tdeck = set(board_config.staged_modules(TDECK, ROOT))
    p4 = set(board_config.staged_modules(P4, ROOT))
    guition = set(board_config.staged_modules(GUITION, ROOT))
    assert {"wm_windowed.py", "surface.py"} <= p4
    assert not {"wm_windowed.py", "surface.py"} & tdeck
    assert not {"wm_windowed.py", "surface.py"} & guition


def test_the_board_identity_matches_the_ota_stamp():
    """#161's actual thesis, in miniature: the same fact in two files.

    An OTA payload is an app-partition image, so the board is inside the signed
    manifest and a board handed the other one writes a valid image that cannot
    boot. `[board].ota` is that identity; it must agree with what the firmware
    actually stamps -- the P4's build.sh writes it into `_ota_build.py`, and the
    T-Deck's comes from `moy_ota.BOARD`'s default.
    """
    # The stamp is ONE implementation in the shared build lib (2026-08-17):
    # moybyte_ota_identity writes BOARD="${board_id}" into _ota_build.py, and
    # each build.sh passes its [board].ota as that argument -- so the pin is
    # the call site, plus the lib actually stamping what it is handed.
    lib = (ROOT / "tools" / "esp32_build_lib.sh").read_text(encoding="utf-8")
    assert 'BOARD = "${board_id}"' in lib

    p4_sh = (P4 / "build.sh").read_text(encoding="utf-8")
    p4_id = board_config.load(P4)["board"]["ota"]
    assert "moybyte_ota_identity %s " % p4_id in p4_sh

    tdeck_sh = (TDECK / "build.sh").read_text(encoding="utf-8")
    tdeck_id = board_config.load(TDECK)["board"]["ota"]
    assert "moybyte_ota_identity %s " % tdeck_id in tdeck_sh
    assert p4_id != tdeck_id

    guition_sh = (GUITION / "build.sh").read_text(encoding="utf-8")
    guition_id = board_config.load(GUITION)["board"]["ota"]
    assert "moybyte_ota_identity %s " % guition_id in guition_sh
    assert guition_id not in (p4_id, tdeck_id)

    # ...and moy_ota.py's own BOARD default (what runs when the generated
    # _ota_build.py is missing) agrees with the T-Deck's id -- read from the
    # TRACKED device/ source, not a gitignored staged copy.
    ota = (ROOT / "device" / "moy_ota.py").read_text(encoding="utf-8")
    assert 'BOARD = "%s"' % tdeck_id in ota


# -- the [native] declaration (#161: the C-module list is data too) -----------


@pytest.mark.parametrize("board", sorted(BOARDS))
def test_every_native_denial_names_a_module_and_says_why(board):
    """The C twin of the shared denylist's contract: a denial with no reason is
    an allowlist wearing a costume, and a denial naming a module that does not
    exist is a decision about nothing (or a rename nobody followed)."""
    for name, entry in board_config.native_denials(BOARDS[board]).items():
        assert entry.get("why", "").strip(), (
            "%s denies native module %r without a why" % (board, name))
        assert (ROOT / "native" / name / "micropython.cmake").exists(), (
            "%s denies native module %r which does not exist under native/"
            % (board, name))


@pytest.mark.parametrize("board", sorted(BOARDS))
def test_build_sh_stages_native_via_the_declaration(board):
    """No hand-written native list in build.sh -- the same both-halves check
    as the Python side: the script must reach the stager (via the shared build
    lib's moybyte_stage_native, which runs `board_config.py stage-native`) and
    must not carry `cp` lines over the shared native/ tree beside it."""
    sh = (BOARDS[board] / "build.sh").read_text(encoding="utf-8")
    assert "moybyte_stage_native" in sh or "stage-native" in sh, (
        "%s/build.sh no longer stages native modules via board.toml" % board)
    lib = (ROOT / "tools" / "esp32_build_lib.sh").read_text(encoding="utf-8")
    assert "stage-native" in lib
    stale = [line for line in sh.splitlines()
             if "${REPO_ROOT}/native/" in line
             and line.strip().startswith("cp ")]
    assert not stale, (
        "%s/build.sh hand-copies shared native modules again: %s"
        % (board, stale))


@pytest.mark.parametrize("board", sorted(BOARDS))
def test_stage_native_produces_the_declared_tree(tmp_path, board):
    """stage-native writes exactly the declared modules plus one generated
    cmake whose include list names each of them -- and a re-run DEMOLISHES a
    stray, because nothing in .staged/ is authored."""
    src = BOARDS[board]
    work = tmp_path / "board"
    work.mkdir()
    shutil.copyfile(src / "board.toml", work / "board.toml")
    mods = board_config.stage_native(work, ROOT, quiet=True)
    assert mods == board_config.native_modules(src, ROOT)
    staged = work / "native" / ".staged"
    dirs = sorted(p.name for p in staged.iterdir() if p.is_dir())
    assert dirs == mods
    cmake = (staged / "micropython.cmake").read_text(encoding="utf-8")
    for m in mods:
        assert "/%s/micropython.cmake" % m in cmake
    # A stray from an older declaration must not survive the next stage.
    (staged / "moy_stray").mkdir()
    board_config.stage_native(work, ROOT, quiet=True)
    assert not (staged / "moy_stray").exists()


def test_the_p4_denies_exactly_its_missing_hardware():
    """The P4's denials are the hand-cp list build.sh used to encode silently:
    no SD in play, ES8311 audio still open (#82), and no banded flush at all --
    its MIPI-DSI panel scans a PSRAM framebuffer continuously, so moy_flush's
    feeder + bounce slots would be dead code and dead SRAM. The T-Deck denies
    nothing. If this changes, it should be because a board's hardware story
    changed -- update board.toml first, this pin second."""
    assert sorted(board_config.native_denials(P4)) == [
        "moy_audio", "moy_flush", "moy_sd"]
    assert board_config.native_denials(TDECK) == {}
    # The Guition's two denials are bring-up staging decisions (SD is stage 4,
    # audio stage 5 -- docs/board_ports_2026-08.md); each names its stage in
    # board.toml. Update there first, this pin second.
    assert sorted(board_config.native_denials(GUITION)) == ["moy_audio", "moy_sd"]


# -- the [flash]/[monitor] declaration (#202 Phase A) -------------------------


@pytest.mark.parametrize("board", sorted(BOARDS))
def test_flash_facts_are_declared_and_shaped(board):
    """Every board declares its cable-flash facts; tools/board_flash.py reads
    them and the Makefile targets are two lines. The shapes matter: offsets are
    hex strings an esptool command takes verbatim, the image path is
    repo-relative, and the otadata pair exists because skipping that erase on
    an OTA'd board makes a flash look like it did nothing."""
    cfg = board_config.load(BOARDS[board])
    fl = cfg.get("flash")
    assert fl, "%s has no [flash] section" % board
    for key in ("image", "offset", "baud", "otadata_offset", "otadata_size"):
        assert key in fl, "%s [flash] lacks %s" % (board, key)
    int(str(fl["offset"]), 16)
    int(str(fl["otadata_offset"]), 16)
    int(str(fl["otadata_size"]), 16)
    assert not str(fl["image"]).startswith("/")
    assert cfg.get("monitor", {}).get("baud"), "%s has no [monitor] baud" % board


def test_the_way_out_of_the_loader_is_declared_where_it_is_not_hard_reset():
    """`after` is optional and means "hard_reset", which is what the three
    console boards want. The Zero must DECLARE watchdog_reset, because
    hard_reset does nothing on its TinyUSB CDC (its README's hardware facts) --
    and a board left sitting in the loader after a flash reads exactly like an
    image that did not take.

    This is pinned because the fact was already written in that board's own
    toml prose while board_flash.py hardcoded the opposite, which is the shape
    of bug this repo keeps turning declarations into data to avoid."""
    zero = board_config.load(ZERO)["flash"]
    assert zero.get("after") == "watchdog_reset"
    for name in ("tdeck", "p4", "guition-s3"):
        fl = board_config.load(BOARDS[name])["flash"]
        assert fl.get("after", "hard_reset") == "hard_reset", name


def test_board_flash_takes_the_after_from_the_declaration():
    """The reader half. A hardcoded --after is what this key replaced, so the
    literal must not come back: the only `hard_reset` left in the writer is the
    DEFAULT in the lookup."""
    src = (ROOT / "tools" / "board_flash.py").read_text()
    assert 'fl.get("after", "hard_reset")' in src
    assert '"--after", "hard_reset"' not in src


def test_the_two_boards_otadata_offsets_differ_as_their_tables_do():
    """The per-board fact this section exists for: the T-Deck's otadata sits at
    0x1d000 and the P4's at 0xd000 -- one transposed digit apart, and erasing
    the wrong one on the other board has already happened once by hand
    (2026-08-17, the day this became data)."""
    t = board_config.load(TDECK)["flash"]["otadata_offset"]
    p = board_config.load(P4)["flash"]["otadata_offset"]
    assert (t, p) == ("0x1d000", "0xd000")


def test_makefile_flashes_via_the_declaration():
    """Both halves, as ever: the canonical flash/monitor targets must CALL
    tools/board_flash.py, and must no longer restate any flash fact (a chip, a
    bare offset, a baud) beside the declaration."""
    mk = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert mk.count("tools/board_flash.py flash") >= 2
    assert mk.count("tools/board_flash.py monitor") >= 2
    # The canonical targets carry no inline esptool write_flash of their own --
    # the legacy lilygo variants (parts flash / full-erase / no-reset) keep
    # theirs and are the deliberate exceptions.
    for target in ("firmware-flash-tdeck-mainline:", "firmware-flash-p4:"):
        body = mk.split(target, 1)[1].split("\n\n", 1)[0]
        assert "write_flash" not in body, "%s restates flash facts" % target
        assert "board_flash.py" in body


# -- [serial]: the cart-push transport, as data (tools/push_cart.py) ----------

# Every board with real silicon behind it -- the [serial] and sdkconfig checks
# below are about hardware facts and their store, which the headless Zero has
# exactly as much of as the others (it is the module STAGING checks that care
# whether a board has a console, and they use BOARDS above).
_DEVICE_BOARDS = {"tdeck": TDECK, "p4": P4, "guition-s3": GUITION,
                  "zero": ZERO}


@pytest.mark.parametrize("board", sorted(_DEVICE_BOARDS))
def test_every_device_board_declares_its_serial_transport(board):
    """`tools/push_cart.py` refuses a board with no [serial] block rather than
    guessing, because both wrong guesses are silent: opening a USB-Serial/JTAG
    part with the lines LOW chip-resets it mid-write, and an over-long line on
    the P4's unflow-controlled UART is dropped as noise with no error."""
    ser = board_config.load(str(_DEVICE_BOARDS[board])).get("serial")
    assert ser, "%s/board.toml has no [serial] section" % board
    for key in ("dtr", "rts", "attach_only", "chunk"):
        assert key in ser, "%s [serial] is missing %r" % (board, key)
    assert isinstance(ser["chunk"], int) and ser["chunk"] > 0


def test_the_soc_usb_boards_are_attach_only_and_the_external_uart_is_not():
    """The line-state rule is INVERTED between the two part families, and each
    half was learned on glass. The S3 boards' USB-Serial/JTAG is ON the SoC, so
    a reset re-enumerates the device under an open handle (rst:0x15, 2026-08-17)
    -- attach, never pulse. The P4's CH343 is external, so an explicit reset is
    safe and dtr/rts LOW at open never glitches its auto-reset circuit.

    The Zero is the THIRD combination and not a mistake: it is an S3 whose image
    keeps TinyUSB CDC rather than the #201 USB-Serial/JTAG promotion (its
    mpconfigboard.h argues that), so DTR must be asserted at open -- the CDC
    rule, and the memory's oldest Zero fact -- while a reset is perfectly safe
    because nothing on that board holds state a reset loses. Lines-asserted is
    therefore NOT the tell for attach_only; the part behind them is.
    """
    for name in ("tdeck", "guition-s3"):
        ser = board_config.load(str(_DEVICE_BOARDS[name]))["serial"]
        assert ser["dtr"] is True and ser["rts"] is True, name
        assert ser["attach_only"] is True, name
    p4 = board_config.load(str(P4))["serial"]
    assert p4["dtr"] is False and p4["rts"] is False
    assert p4["attach_only"] is False
    zero = board_config.load(str(ZERO))["serial"]
    assert zero["dtr"] is True and zero["rts"] is True
    assert zero["attach_only"] is False
    assert zero["usb"] == "303a:4001", (
        "the Zero's USB id is TinyUSB CDC's; 303a:1001 would mean its image "
        "switched to USB-Serial/JTAG, which changes the DTR rule with it")


def test_the_p4_chunk_stays_under_its_uart_ring():
    """Measured 2026-08-19: a 44KB cart at the harness default of 768 failed
    five times with a DIFFERENT bad hash each attempt and went clean at 256.
    That UART's stdin ring is ~256 bytes with no flow control, so this is a
    hardware bound, not a tuning preference -- raising it re-breaks the push."""
    assert board_config.load(str(P4))["serial"]["chunk"] <= 256


def test_push_cart_holds_no_per_board_branch():
    """The board differences are DATA (#202 Phase A). The tool may name the
    board dirs in its BOARDS map; it may not branch on which board it is."""
    src = (ROOT / "tools" / "push_cart.py").read_text()
    for marker in ('== "p4"', "== 'p4'", '== "tdeck"', "== 'tdeck'",
                   '== "guition"', "== 'guition'"):
        assert marker not in src, "push_cart.py branches on the board: %s" % marker


def test_the_on_glass_suites_read_the_declaration_instead_of_retyping_it():
    """The last hand-written copies of these facts (#206). Both suites drive a
    real board, so they are hardware-gated and CI never runs them -- which is
    exactly why what is typed inside them rots unseen. Each hand-wrote
    `dtr=True, rts=True` under a comment restating the measurement its
    board.toml already carries, and neither read `attach_only` at all: the fact
    that stops a reset stranding the handle was data with no consumer."""
    for suite, name in (("test_tdeck_on_glass.py", "lilygo_t_deck_plus_mainline"),
                        ("test_guition_on_glass.py", "guition_jc3248w535")):
        src = (ROOT / "tests" / suite).read_text()
        assert 'board_dir=ROOT / "firmware" / "%s"' % name in src, (
            "%s no longer points P4Board at %s/board.toml" % (suite, name))
        for typed in ("dtr=", "rts=", "chunk="):
            assert typed not in src, (
                "%s retypes a [serial] fact (%s) the board file states" % (
                    suite, typed))


def test_attach_only_is_a_fact_with_teeth():
    """Declaring it is not enough -- it has to REFUSE. `P4Board.reset()` pulses
    RTS, which on a SoC-USB board re-enumerates the device under the open handle
    and every read returns nothing forever, reading exactly like a dead board."""
    sys.path.insert(0, str(ROOT / "tools"))
    from p4_autotest import declared_serial
    for name in ("tdeck", "guition-s3"):
        assert declared_serial(_DEVICE_BOARDS[name])["attach_only"] is True
    assert declared_serial(P4)["attach_only"] is False
    src = (ROOT / "tools" / "p4_autotest.py").read_text()
    body = src.split("def reset(", 1)[1].split("\n    def ", 1)[0]
    assert "if self.attach_only:" in body and "raise" in body, (
        "P4Board.reset() no longer refuses a board that declares attach_only")


# -- the sdkconfig fragment, read as data --------------------------------------
#
# `boards/<BOARD>/sdkconfig.board` is the store of a board's decided IDF
# settings and STAYS the store (docs/board_ports_2026-08.md declines moving it
# into TOML); build.sh's stale-sdkconfig guard DERIVES its option list from it.
# These checks pin that derivation, not the facts -- a hand-typed subset is how
# a setting comes to silently no-op on a warm build dir.


@pytest.mark.parametrize("board", sorted(_DEVICE_BOARDS))
def test_build_sh_carries_no_hand_written_sdkconfig_list(board):
    """The both-halves check, same shape as the native/staging ones: the script
    must reach the shared guard, and must not name CONFIG_ options beside it."""
    sh = (_DEVICE_BOARDS[board] / "build.sh").read_text(encoding="utf-8")
    assert "moybyte_sdkconfig_guard" in sh, (
        "%s/build.sh no longer applies its sdkconfig fragment via the "
        "shared guard" % board)
    assert "moybyte_partition_and_sdkconfig_guard" not in sh
    stale = [ln.strip() for ln in sh.splitlines() if "'CONFIG_" in ln]
    assert not stale, (
        "%s/build.sh spells out sdkconfig options again -- they belong in "
        "boards/*/sdkconfig.board, which the guard reads: %s" % (board, stale))


@pytest.mark.parametrize("board", sorted(_DEVICE_BOARDS))
def test_the_guard_checks_what_the_fragment_decided(board):
    """Every positive assignment in the fragment reaches the guard's list, and
    the required list is exactly the non-disabling half."""
    d = _DEVICE_BOARDS[board]
    settings = board_config.sdkconfig_settings(d)
    required = board_config.sdkconfig_required(d)
    assert settings, "%s: no settings parsed out of sdkconfig.board" % board
    assert ([s.assignment for s in required]
            == [s.assignment for s in settings if not s.disables])
    assert all(s.assignment.startswith(s.option + "=") for s in required)
    # A generated sdkconfig never carries a bare `CONFIG_X=` line -- and it
    # renders `=n` as `# CONFIG_X is not set` -- so a disable in the required
    # list would demand a line no build can have. The `=n` case is not
    # hypothetical: CONFIG_BT_HCI_LOG_DEBUG_EN=n rode the required list for a
    # day and failed every CI p4 build while local builds only warned.
    assert not any(a.endswith("=") or a.endswith("=n")
                   for a in (s.assignment for s in required))


def test_an_equals_n_disable_never_reaches_the_required_list():
    """The exact 2026-08-25 CI failure, pinned: the P4 fragment spells one
    disable `CONFIG_BT_HCI_LOG_DEBUG_EN=n` (the idiomatic Kconfig form), the
    generated config renders it `# ... is not set`, and a guard that greps for
    the literal `=n` line reports it inert forever."""
    d = _DEVICE_BOARDS["p4"]
    settings = {s.option: s for s in board_config.sdkconfig_settings(d)}
    s = settings.get("CONFIG_BT_HCI_LOG_DEBUG_EN")
    if s is None:
        pytest.skip("the P4 fragment no longer carries the option")
    assert s.disables, "=n is a disable spelling"
    assert s.assignment not in [
        q.assignment for q in board_config.sdkconfig_required(d)]


@pytest.mark.parametrize("board", sorted(_DEVICE_BOARDS))
def test_every_decided_setting_carries_its_prose(board):
    """The `why` beside a value is what the build prints when ESP-IDF refuses
    the setting, so a value with no prose is a value nobody can review. Same
    contract as a board.toml denial with no reason."""
    for s in board_config.sdkconfig_settings(_DEVICE_BOARDS[board]):
        assert s.why.strip(), (
            "%s sets %s at sdkconfig.board:%d with no comment block above it"
            % (board, s.option, s.line))


def test_last_assignment_wins():
    """Both S3 fragments set FLASHFREQ_80M=y as the board fact up top and turn
    it off again in the 120MHz MSPI block. A guard reading the FIRST would
    demand a line the build must not have, and would delete the generated
    sdkconfig on every single build."""
    for name in ("tdeck", "guition-s3"):
        settings = {s.option: s for s in
                    board_config.sdkconfig_settings(_DEVICE_BOARDS[name])}
        assert settings["CONFIG_ESPTOOLPY_FLASHFREQ_80M"].disables, name
        assert settings["CONFIG_ESPTOOLPY_FLASHFREQ_120M"].value == "y", name
        assert "CONFIG_ESPTOOLPY_FLASHFREQ_80M=y" not in [
            s.assignment for s in board_config.sdkconfig_required(
                _DEVICE_BOARDS[name])], name


@pytest.mark.parametrize("board", sorted(_DEVICE_BOARDS))
def test_the_partition_table_is_named_once(board):
    """The CSV filename lives in the one setting that has to state it, and the
    build reads it back out (the guard exports BOARD_PARTITION_CSV)."""
    d = _DEVICE_BOARDS[board]
    csv = board_config.sdkconfig_get(d, "CONFIG_PARTITION_TABLE_CUSTOM_FILENAME")
    assert (board_config.sdkconfig_path(d).parent / csv).exists(), (
        "%s names a partition table that is not beside its board def: %s"
        % (board, csv))
    sh = (d / "build.sh").read_text(encoding="utf-8")
    assert csv not in sh, (
        "%s/build.sh restates the partition table filename %r, which "
        "sdkconfig.board already has to name" % (board, csv))
    assert "${BOARD_PARTITION_CSV}" in sh

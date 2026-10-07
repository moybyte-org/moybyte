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

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tools import board_config

ROOT = Path(__file__).resolve().parent.parent
TDECK = ROOT / "firmware" / "lilygo_t_deck_plus_mainline"
P4 = ROOT / "firmware" / "esp32_p4_wifi6_touch_lcd_7b"
GUITION = ROOT / "firmware" / "guition_jc3248w535"
# The HEADLESS fourth target (#41), a build target since 2026-08-29. It is in
# BOARDS because every check in this file is about the DECLARATION and its
# reader -- the parser agreeing with a real TOML implementation, build.sh
# obeying board.toml instead of restating it, the flash facts being shaped like
# flash facts -- and none of that cares whether a board has a screen. The two
# tests that do care about a console say so where they narrow.
ZERO = ROOT / "firmware" / "seeed_xiao_esp32s3_zero"
GUITION_P4 = ROOT / "firmware" / "guition_jc8012p4a1c"
BOARDS = {"tdeck": TDECK, "p4": P4, "guition-s3": GUITION, "zero": ZERO,
          "guition-p4": GUITION_P4}

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
    if board in ("tdeck", "guition-s3", "zero"):
        assert not (dest / "wm_windowed.py").exists()      # no desktop to window
    else:
        assert (dest / "wm_windowed.py").exists()          # the two P4 desks
    on_disk = {p.name for p in dest.glob("*.py")} - {generated, "moy_runtime.py"}
    assert on_disk == set(wanted)


def test_the_p4_stages_the_windowed_tier_and_the_s3_does_not():
    """The one real difference between the two boards' shared module sets.

    Stated here as well as in `test_staging_closure.py` because this is the
    claim `docs/surface_model_v1.md` L6 rests on: `wm.py` and `console.py` are
    frozen verbatim on the S3, so the windowed WM must not be reachable there
    at all. (The surface table it signals is the kernel's, native/moy_glass,
    since §15.)
    """
    tdeck = set(board_config.staged_modules(TDECK, ROOT))
    p4 = set(board_config.staged_modules(P4, ROOT))
    guition = set(board_config.staged_modules(GUITION, ROOT))
    assert "wm_windowed.py" in p4
    assert "wm_windowed.py" not in tdeck | guition
    assert "surface.py" not in tdeck | guition | p4


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


def test_the_panel_diagonal_is_declared_once_and_reaches_the_console():
    """The #203 tap-target floor's one input, and the same-fact-in-two-files
    shape again: `[panel].diagonal_in` is the authority, `moy_runtime.py`'s
    PANEL_DIAGONAL_IN is what the boot path hands the Workstation, and a floor
    derived from a stale copy would size every tap target on the board wrong.

    Declaring a diagonal is the OPT-IN, so the silences are asserted too --
    each is a decision recorded in `chrome.chrome_scale_floor`'s docstring, and
    a board that grows a `[panel]` block should be a red test read by a human
    rather than a chrome scale that doubled on somebody's glass."""
    from runtime.chrome import chrome_scale_floor

    declared = {b: board_config.load(d).get("panel", {}).get("diagonal_in")
                for b, d in BOARDS.items()}
    assert {b for b, v in declared.items() if v} == {
        "guition-s3", "guition-p4", "p4"}, (
        "the set of boards opting in to the chrome tap-target floor changed: "
        "%s. Read chrome_scale_floor's docstring before updating this."
        % sorted(b for b, v in declared.items() if v))

    # ...and each number does the job it was declared for: the landscape glass
    # the board composites onto floors the chrome a scale above its font.
    for name, board_dir, glass in (("guition-s3", GUITION, (480, 320)),
                                   ("guition-p4", GUITION_P4, (1280, 800)),
                                   ("p4", P4, (1024, 600))):
        diag = declared[name]
        runtime_py = (board_dir / "modules" / "moy_runtime.py").read_text(
            encoding="utf-8")
        assert "PANEL_DIAGONAL_IN = %s" % diag in runtime_py, name
        assert "panel_diagonal_in=PANEL_DIAGONAL_IN" in runtime_py, name
        assert chrome_scale_floor(glass[0], glass[1], diag) == 2, name

    # The T-Deck's silence is the one that was ARGUED (2026-09-06): the owner
    # asked for the floor there, it was built and rendered, and it was declined
    # because at 320px wide cs 2 leaves the bar's lent zone too narrow for the
    # Editor's tab ladder. Its glass floors at 2 like everything else, so the
    # arithmetic cannot be what stops a later re-declaration -- this can.
    assert declared["tdeck"] is None
    assert chrome_scale_floor(320, 240, 2.8) == 2
    tdeck_toml = (TDECK / "board.toml").read_text(encoding="utf-8")
    assert "NO [panel] BLOCK, AND THAT IS A DECISION" in tdeck_toml


# -- the [board] header, and where a declared key has a reader ----------------
#
# Every board.toml in the tree, the web runner's included: these two are about
# the DECLARATION itself, and the runner declares the same shapes.
ALL_DECLS = sorted((ROOT / "firmware").glob("*/board.toml"))

TIERS = ("handheld", "desktop", "headless", "browser")


def test_every_declaration_names_its_board_and_its_tier():
    """`[board]` is the header every other section is read under. `tier` is
    the word the module sets are argued from -- every `wm_windowed.py` denial
    in the tree points at `tier_why` -- so an unspelled or unexplained tier
    leaves those denials pointing at nothing."""
    assert len(ALL_DECLS) >= 6, "declaration discovery found %d" % len(ALL_DECLS)
    for toml in ALL_DECLS:
        cfg = board_config.load(toml.parent)["board"]
        assert cfg["name"] == toml.parent.name, (
            "%s: [board] name is the directory's, and the build prints it" % toml)
        assert cfg["tier"] in TIERS, (
            "%s: tier %r is not one of %s" % (toml, cfg.get("tier"), TIERS))
        assert len(cfg.get("tier_why", "").split()) >= 10, (
            "%s: tier_why must ARGUE the tier -- the denials cite it" % toml)


def test_strategy_is_declared_only_where_something_reads_it():
    """`strategy` is read in ONE place -- `board_config.shared_strategy`, over
    `[modules.shared]`. It sat under `[native.shared]`, `[native.p4]` and
    `[modules.device]` too, where an `allowlist` would have been obeyed by
    nothing and the build would have compiled everything anyway: a declaration
    that reads as a decision and is inert."""
    for toml in ALL_DECLS:
        cfg = board_config.load(toml.parent)
        for sect, body in sorted(_tables(cfg)):
            if "strategy" not in body:
                continue
            assert sect == "modules.shared", (
                "%s: [%s] declares strategy = %r, which no reader consumes"
                % (toml, sect, body["strategy"]))
            assert body["strategy"] in ("denylist", "allowlist"), toml
        assert (board_config.shared_strategy(toml.parent)
                == cfg["modules"]["shared"]["strategy"])


def _tables(cfg, prefix=""):
    """(dotted name, table) for every table in a parsed board.toml."""
    for key, val in cfg.items():
        if isinstance(val, dict):
            name = prefix + key
            yield name, val
            for sub in _tables(val, name + "."):
                yield sub


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
def test_every_native_take_names_a_module_and_says_why(board):
    """A take is a written yes, held to a denial's standard: a module that
    exists, and a reason."""
    for name, entry in board_config.native_takes(BOARDS[board]).items():
        assert entry.get("why", "").strip(), (
            "%s takes native module %r without a why" % (board, name))
        assert (ROOT / "native" / name / "micropython.cmake").exists(), (
            "%s takes native module %r which does not exist under native/"
            % (board, name))
        assert name not in board_config.native_denials(BOARDS[board]), name


# The WebAssembly tier ships on every console board or on none
# (docs/wasm_tier_plan_2026-09.md), so its engine is the one shared module
# whose default "yes" is not enough: every board file decides it in writing.
CONSOLE_BOARDS = ("tdeck", "p4", "guition-s3", "guition-p4")


@pytest.mark.parametrize("board", sorted(BOARDS))
def test_every_board_decides_the_wasm_engine(board):
    takes = board_config.native_takes(BOARDS[board])
    denies = board_config.native_denials(BOARDS[board])
    assert "moy_wasm" in takes or "moy_wasm" in denies, (
        "%s/board.toml neither takes nor denies moy_wasm" % board)
    staged = "moy_wasm" in board_config.native_modules(BOARDS[board], ROOT)
    assert staged == (board in CONSOLE_BOARDS), (
        "%s: the wasm tier is on every console board or on none" % board)


@pytest.mark.parametrize("board", sorted(BOARDS))
def test_every_board_decides_the_kernel_entry(board):
    """Taking native/moy_kernel hands app_main to the kernel
    (docs/kernel_spine_2026-10.md §8): every console takes it, and the Zero
    keeps the port's entry, each in writing."""
    takes = board_config.native_takes(BOARDS[board])
    denies = board_config.native_denials(BOARDS[board])
    assert "moy_kernel" in takes or "moy_kernel" in denies, (
        "%s/board.toml neither takes nor denies moy_kernel" % board)
    staged = "moy_kernel" in board_config.native_modules(BOARDS[board], ROOT)
    assert staged == (board in CONSOLE_BOARDS), (
        "%s: the kernel is the entry on every console board" % board)


@pytest.mark.parametrize("board", sorted(BOARDS))
def test_every_console_takes_the_native_spine_and_the_zero_declares_no_twin(board):
    """The spine is native on every console and its Python twin is not frozen
    there (docs/kernel_spine_2026-10.md); the Zero has no console to route and
    declares no twin. The build exports the declaration as the hook."""
    want = {"MOY_SPINE_IMPL": "c"} if board in CONSOLE_BOARDS else {}
    assert board_config.impls(BOARDS[board]) == want


def test_a_native_twin_the_board_names_is_py_or_c(tmp_path):
    (tmp_path / "board.toml").write_text(
        '[native]\n[native.impl]\nspine = "rust"\nwhy = "no"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="py or c"):
        board_config.impls(tmp_path)


def test_a_console_build_drops_the_python_spine_twin_and_the_environment_wins(tmp_path):
    """The lib exports the board's twin before anything reads the hook, and a
    build that sets MOY_SPINE_IMPL itself keeps its own."""
    def frozen(**env):
        script = (
            "set -euo pipefail\nsource tools/esp32_build_lib.sh\n"
            "BUILD_PYTHON='%s' REPO_ROOT='%s' SCRIPT_DIR='%s'\n"
            "moybyte_board_impls\n"
            "echo \"hook=${MOY_SPINE_IMPL:-}\"\n"
            "'%s' tools/board_config.py list '%s' | grep -c '^moy_spine.py$' || true\n"
            % (sys.executable, ROOT, TDECK, sys.executable, TDECK))
        e = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), **env}
        out = subprocess.run(["bash", "-c", script], cwd=str(ROOT), env=e,
                             capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        return out.stdout.split()
    assert frozen() == ["hook=c", "0"]                  # no Python twin frozen
    assert frozen(MOY_SPINE_IMPL="py") == ["hook=py", "1"]


def test_a_module_both_taken_and_denied_is_refused(tmp_path):
    (tmp_path / "board.toml").write_text(
        '[native]\n[native.shared]\nsource = "native"\n'
        '[[native.shared.deny]]\nmodule = "moy_wasm"\nwhy = "no"\n'
        '[[native.shared.take]]\nmodule = "moy_wasm"\nwhy = "yes"\n',
        encoding="utf-8")
    with pytest.raises(ValueError):
        board_config.native_modules(tmp_path, ROOT)


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


def test_a_generated_file_is_not_staged_with_its_old_timestamp(
        tmp_path, monkeypatch):
    """A removed web bundle shipped as the old blob. The desktop build leaves
    `moy_web_blob.gen.c` in `native/moy_web/` (here the empty table, written
    long ago); the stager copied it with its mtime, the generator found its text
    already right and left it, and ninja -- seeing a source older than the
    object a previous board build made from the full blob -- kept that object.
    The staged tree carries no generated file, so the generator writes a new
    one and it is newer than any object built before it."""
    import os
    import time
    from tools import gen_web_blob

    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("MOYBYTE_REQUIRE_WEB_BUNDLE", "0")
    root = tmp_path / "root"
    shutil.copytree(ROOT / "native" / "moy_web", root / "native" / "moy_web")
    empty = tmp_path / "no_dist"
    empty.mkdir()
    source = root / "native" / "moy_web" / "moy_web_blob.gen.c"
    assert gen_web_blob.main(["--dist", str(empty), "--out", str(source),
                              "--quiet"]) == 0
    os.utime(source, (1, 1))

    work = tmp_path / "board"
    work.mkdir()
    (work / "board.toml").write_text('[native.shared]\nsource = "native"\n',
                                     encoding="utf-8")
    assert board_config.stage_native(work, root, quiet=True) == ["moy_web"]
    staged = work / "native" / ".staged" / "moy_web"
    assert (staged / "modmoy_web.c").exists()
    assert not (staged / "moy_web_blob.gen.c").exists(), \
        "a generated file was staged with the mtime it was last written at"

    object_built_at = time.time() - 100
    out = staged / "moy_web_blob.gen.c"
    assert gen_web_blob.main(["--dist", str(empty), "--out", str(out),
                              "--quiet"]) == 0
    assert out.stat().st_mtime > object_built_at, \
        "the generated file is older than the object built before it"


def test_the_p4_denies_exactly_its_missing_hardware():
    """The P4's denials: ES8311 audio still open (#82), and no banded flush at all --
    its MIPI-DSI panel scans a PSRAM framebuffer continuously, so moy_flush's
    feeder + bounce slots would be dead code and dead SRAM. Its card takes
    moy_sd (SDMMC slot 0 under the store's card volume). The T-Deck denies
    nothing. If this changes, it should be because a board's hardware story
    changed -- update board.toml first, this pin second."""
    assert sorted(board_config.native_denials(P4)) == ["moy_audio", "moy_flush"]
    assert board_config.native_denials(TDECK) == {}
    # The Guition's denial is a bring-up staging decision (audio is stage 5 --
    # docs/board_ports_2026-08.md), named with its stage in board.toml; its
    # card opens through moy_sd (the store's card volume). Update there first,
    # this pin second.
    assert sorted(board_config.native_denials(GUITION)) == ["moy_audio"]


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
    for name in ("tdeck", "p4", "guition-s3", "guition-p4"):
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


def test_every_target_the_makefile_suggests_exists():
    """A hint that names a missing target is worse than no hint.

    `PORT is not set -- ... (try: make device-port)` shipped pointing at a
    target nobody had written, so the one message a person sees at exactly the
    moment they do not know the answer sent them to `No rule to make target`.
    Any `make <x>` this file offers has to be a real target.
    """
    mk = (ROOT / "Makefile").read_text(encoding="utf-8")
    # Targets are the `name:` at the start of a line (skip the `.PHONY:` etc).
    defined = set(re.findall(r"^([a-zA-Z][\w.-]*):", mk, re.M))
    assert "device-port" in defined, "the PORT hint's target is missing"
    # Only what the Makefile SAYS to a person: the body of an `echo` and the
    # `## ` help text. Scanning all prose instead catches things like "what
    # make does", which is a sentence, not a suggestion.
    advice = " ".join(re.findall(r'echo "([^"]*)"', mk)
                      + re.findall(r"##\s*(.*)$", mk, re.M))
    suggested = set(re.findall(r"\bmake ([a-z][\w-]*)", advice))
    assert suggested, "no `make <target>` advice found -- the scan missed it"
    for name in suggested:
        assert name in defined, (
            "the Makefile tells people to run `make %s`, which is not a target"
            % name)


# -- [serial]: the cart-push transport, as data (tools/push_cart.py) ----------

# Every board with real silicon behind it -- the [serial] and sdkconfig checks
# below are about hardware facts and their store, which the headless Zero has
# exactly as much of as the others (it is the module STAGING checks that care
# whether a board has a console, and they use BOARDS above).
_DEVICE_BOARDS = {"tdeck": TDECK, "p4": P4, "guition-s3": GUITION,
                  "zero": ZERO, "guition-p4": GUITION_P4}


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

    The Zero is the THIRD combination and not a mistake. It took the
    USB-Serial/JTAG promotion on 2026-08-30 (its mpconfigboard.h carries the
    evidence: on TinyUSB CDC there was no software path into the ROM loader at
    all), so it opens lines-HIGH like the console S3s -- but a reset is
    perfectly safe on it, because nothing there holds state a reset loses, so it
    is NOT attach_only. Lines-asserted is therefore not the tell for
    attach_only; the part behind them is.

    Worth keeping about the switch: the two line VALUES did not move, and their
    reason inverted. On CDC, DTR had to be asserted or the REPL was silent. On
    USB-Serial/JTAG, opening with both LOW is a chip reset and both HIGH
    attaches cleanly. Same `true`, opposite fact -- which is the argument for
    these being data with a comment rather than a habit.
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
    assert zero["usb"] == "303a:1001", (
        "the Zero shows the S3's USB-Serial/JTAG id since 2026-08-30; 303a:4001 "
        "would mean its image went back to TinyUSB CDC, which takes the "
        "software route into the ROM loader with it")
    # ...and it now SHARES that id with the two console boards, which is the one
    # cost of the switch: `find_port` can no longer tell them apart by id alone,
    # and this board has no dev channel to answer an identity probe.
    assert zero["usb"] == board_config.load(
        str(_DEVICE_BOARDS["guition-s3"]))["serial"]["usb"]


def test_the_p4s_chunk_and_window_fit_the_ring_its_build_gives_it():
    """The Waveshare P4's serial is a UART with no flow control, so a byte
    that arrives with the stdin ring full is dropped with no error: a `py`
    line and a `recv` window each have to fit the ring whole. The ring is the
    one tools/patch_stdin_ring.py gives this board's build -- on the stock 260
    bytes a chunk of 768 corrupted a push five times running (2026-08-19). A
    chunk's %r escaping can nearly double it, behind a ~40-byte prefix, and a
    ring holds one byte less than its size."""
    from tools import patch_stdin_ring
    build = (P4 / "build.sh").read_text(encoding="utf-8")
    assert re.search(r"^moybyte_patch_stdin_ring$", build, re.M), \
        "the P4 build no longer takes the stdin ring its [serial] is sized for"
    ser = board_config.load(str(P4))["serial"]
    holds = patch_stdin_ring.RING_BYTES - 1
    assert 2 * ser["chunk"] + 64 <= holds, (ser["chunk"], holds)
    assert ser["window"] <= holds, (ser["window"], holds)


def test_a_grown_stdin_ring_holds_a_window_and_never_meets_a_uart_isr():
    """MOY_SERIAL_RING_BYTES moves a board's stdin ring into PSRAM so the
    window the host sends on an ack lands while the store writes. It has to
    hold a whole window, fit the ring's 16-bit count, and stay off a console
    whose RX ISR runs with the cache off: the Waveshare P4's UART ISR is IRAM
    and fills the ring during a flash write, which is why its ring is in TCM."""
    found = {}
    for name, d in _DEVICE_BOARDS.items():
        for h in (d / "boards").glob("*/mpconfigboard.h"):
            m = re.search(r"^#define MOY_SERIAL_RING_BYTES\s+\((\d+)\)",
                          h.read_text(encoding="utf-8"), re.M)
            if m:
                found[name] = int(m.group(1))
    assert set(found) == {"tdeck", "guition-s3", "guition-p4"}, found
    for name, ring in found.items():
        ser = board_config.load(str(_DEVICE_BOARDS[name]))["serial"]
        assert 2 * ser["window"] <= ring <= 65535, (name, ring, ser["window"])
        build = (_DEVICE_BOARDS[name] / "build.sh").read_text(encoding="utf-8")
        assert not re.search(r"^moybyte_patch_stdin_ring$", build, re.M), name


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
                        ("test_guition_on_glass.py", "guition_jc3248w535"),
                        ("test_guition_p4_on_glass.py", "guition_jc8012p4a1c")):
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
    # Asserted, never skipped: the option going away is the one event that
    # would leave this pin passing while covering nothing.
    s = settings["CONFIG_BT_HCI_LOG_DEBUG_EN"]
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


def test_every_console_board_imports_its_frozen_modules_first():
    """Each import walks `sys.path` in order, and MicroPython's default puts the
    flash root ('') ahead of '.frozen': every frozen module paid a failed stat
    of the flash per name first. The console boards' boot.py reorders it, and
    this runs each one against the default path. The headless Zero keeps the
    default."""
    import types
    consoles = [t.parent for t in ALL_DECLS
                if board_config.load(t.parent)["board"]["tier"]
                in ("handheld", "desktop")]
    assert len(consoles) >= 4, consoles
    for d in consoles:
        fake_sys = types.SimpleNamespace(path=["", ".frozen", "/lib"])
        mods = {"sys": fake_sys, "gc": types.SimpleNamespace(collect=lambda: 0)}
        real_import = __import__

        def _import(name, *a, **kw):
            return mods[name] if name in mods else real_import(name, *a, **kw)

        src = (d / "modules" / "boot.py").read_text(encoding="utf-8")
        exec(compile(src, str(d / "boot.py"), "exec"),
             {"__builtins__": dict(__builtins__ if isinstance(__builtins__, dict)
                                   else vars(__builtins__),
                                   __import__=_import, print=lambda *a: None)})
        assert fake_sys.path == [".frozen", "", "/lib"], (d.name, fake_sys.path)


# Sprint 3's kernel modules (docs/kernel_survival_2026-10.md section 2 item 6):
# every board decides each in writing, and every console takes all three.
@pytest.mark.parametrize("board", sorted(BOARDS))
@pytest.mark.parametrize("module", ("moy_glass", "moy_input", "moy_net"))
def test_every_board_decides_the_survival_modules(board, module):
    takes = board_config.native_takes(BOARDS[board])
    denies = board_config.native_denials(BOARDS[board])
    assert module in takes or module in denies, (
        "%s/board.toml neither takes nor denies %s" % (board, module))
    if board in CONSOLE_BOARDS:
        assert module in takes, "%s does not take %s" % (board, module)

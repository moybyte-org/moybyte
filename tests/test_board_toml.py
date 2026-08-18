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
from pathlib import Path

import pytest

from tools import board_config

ROOT = Path(__file__).resolve().parent.parent
TDECK = ROOT / "firmware" / "lilygo_t_deck_plus_mainline"
TDECK_MAINLINE = ROOT / "firmware" / "lilygo_t_deck_plus_mainline"
P4 = ROOT / "firmware" / "esp32_p4_wifi6_touch_lcd_7b"
GUITION = ROOT / "firmware" / "guition_jc3248w535"
BOARDS = {"tdeck": TDECK, "tdeck-mainline": TDECK_MAINLINE, "p4": P4,
          "guition-s3": GUITION}

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
    (dest / "palette.py").write_text("# a stray from an older build\n")
    (dest / "carts_data.py").write_text("CARTS = {}\n")   # declared in `keep`
    (dest / "moy_runtime.py").write_text("# board-authored, TRACKED\n")
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run(["git", "add", "modules/moy_runtime.py"], cwd=work, check=True)

    wanted, removed = board_config.stage(work, ROOT, quiet=True)

    assert "palette.py" in removed, "the stale stray survived the prune"
    assert (dest / "carts_data.py").exists(), "prune ate a generated file"
    assert (dest / "moy_runtime.py").exists(), (
        "prune ate a TRACKED board-authored module -- the one outcome that "
        "must be impossible, since modules/ holds the board's own sources too")
    assert (dest / "console.py").exists() and (dest / "moy_font.py").exists()
    assert not (dest / "font.py").exists(), "font.py must stage RENAMED only"
    if board.startswith("tdeck") or board.startswith("guition"):
        assert not (dest / "wm_windowed.py").exists()
    on_disk = {p.name for p in dest.glob("*.py")} - {"carts_data.py",
                                                     "moy_runtime.py"}
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
    """The two P4 denials are the hand-cp list build.sh used to encode
    silently: no SD in play, ES8311 audio still open (#82). The T-Deck denies
    nothing. If this changes, it should be because a board's hardware story
    changed -- update board.toml first, this pin second."""
    assert sorted(board_config.native_denials(P4)) == ["moy_audio", "moy_sd"]
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

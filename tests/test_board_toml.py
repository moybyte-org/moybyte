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
TDECK = ROOT / "firmware" / "lilygo_t_deck_plus_micropython"
P4 = ROOT / "firmware" / "esp32_p4_wifi6_touch_lcd_7b"
BOARDS = {"tdeck": TDECK, "p4": P4}

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
    if board == "tdeck":
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
    assert {"wm_windowed.py", "surface.py"} <= p4
    assert not {"wm_windowed.py", "surface.py"} & tdeck


def test_the_board_identity_matches_the_ota_stamp():
    """#161's actual thesis, in miniature: the same fact in two files.

    An OTA payload is an app-partition image, so the board is inside the signed
    manifest and a board handed the other one writes a valid image that cannot
    boot. `[board].ota` is that identity; it must agree with what the firmware
    actually stamps -- the P4's build.sh writes it into `_ota_build.py`, and the
    T-Deck's comes from `moy_ota.BOARD`'s default.
    """
    p4_sh = (P4 / "build.sh").read_text(encoding="utf-8")
    p4_id = board_config.load(P4)["board"]["ota"]
    assert 'BOARD = "%s"' % p4_id in p4_sh

    ota = (TDECK / "modules" / "moy_ota.py").read_text(encoding="utf-8")
    tdeck_id = board_config.load(TDECK)["board"]["ota"]
    assert 'BOARD = "%s"' % tdeck_id in ota
    assert p4_id != tdeck_id

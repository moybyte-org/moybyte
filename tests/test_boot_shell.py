"""The boot shell's mode ladder, EXECUTED, plus the four boards over it.

`device/boot_shell.py` is ~20 lines that used to live once per board, and the
copies had already started to drift: the Waveshare P4 kept a flag per mode --
the shape the T-Deck's own docstring argues against -- and was the only one
without the `except Exception` guard, so a `run_desktop` that raised there took
the REPL down with it instead of printing what broke.

That is what a shared body has to keep true, so it is asserted by RUNNING it
(the ladder's branches, the two guards, the unknown-mode report) rather than by
grepping for its text. The board half is a source pin, because there is no board
here: each console board declares its name, its modes and its smoke module, and
nothing else.
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "device" / "boot_shell.py"

BOARDS = {
    "lilygo_t_deck_plus_mainline": "tdeck_smoke",
    "esp32_p4_wifi6_touch_lcd_7b": "moybyte_shell",
    "guition_jc3248w535": "guition_smoke",
    "guition_jc8012p4a1c": "guition_p4_smoke",
}


def _load():
    spec = importlib.util.spec_from_file_location("boot_shell_under_test", SOURCE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


boot_shell = _load()


def _shell_src(board):
    return (ROOT / "firmware" / board / "modules"
            / "moybyte_shell.py").read_text(encoding="utf-8")


@pytest.fixture
def desk(monkeypatch):
    """A fake `moy_runtime`, since `main` imports the console by name."""
    mod = types.ModuleType("moy_runtime")
    mod.ran = []
    mod.raises = None

    def run_desktop():
        mod.ran.append(1)
        if mod.raises is not None:
            raise mod.raises
    mod.run_desktop = run_desktop
    monkeypatch.setitem(sys.modules, "moy_runtime", mod)
    return mod


@pytest.fixture
def smoke(monkeypatch):
    """A fake smoke module with one self-terminating mode on it."""
    mod = types.ModuleType("fake_smoke")
    mod.ran = []
    mod.raises = None

    def panel():
        mod.ran.append("panel")
        if mod.raises is not None:
            raise mod.raises
    mod.panel = panel
    monkeypatch.setitem(sys.modules, "fake_smoke", mod)
    return mod


def test_desktop_takes_the_loop_over(desk, smoke, capsys):
    boot_shell.main("T-Deck", "desktop", ("panel", "desktop"), "fake_smoke")
    assert desk.ran == [1] and smoke.ran == []
    assert "Moybyte T-Deck shell starting -- mode=desktop" in capsys.readouterr().out


def test_a_ctrl_c_out_of_the_desktop_drops_to_the_repl(desk, capsys):
    desk.raises = KeyboardInterrupt()
    boot_shell.main("P4", "desktop", ("desktop",), "fake_smoke")
    assert "interrupted -> REPL" in capsys.readouterr().out


def test_a_desktop_that_raises_says_what_broke_and_keeps_the_repl(desk, capsys):
    """The guard the Waveshare P4's private copy did not have. Without it a
    board that fails to boot the console loses the one surface that could say
    why."""
    desk.raises = RuntimeError("no panel")
    boot_shell.main("P4", "desktop", ("desktop",), "fake_smoke")
    assert "Moybyte desktop FAILED: no panel" in capsys.readouterr().out


def test_an_unknown_mode_reports_the_ladder_and_runs_nothing(desk, smoke, capsys):
    """ONE STRING, NOT SIX BOOLEANS -- and a typo in the one string has to be an
    answer, not a board that silently boots something else."""
    boot_shell.main("P4", "touchh", ("panel", "touch", "desktop"), "fake_smoke")
    out = capsys.readouterr().out
    assert "unknown MODE 'touchh'" in out and "panel, touch, desktop" in out
    assert desk.ran == [] and smoke.ran == []


def test_a_smoke_mode_runs_the_verb_of_that_name(smoke, desk):
    boot_shell.main("P4", "panel", ("panel", "desktop"), "fake_smoke")
    assert smoke.ran == ["panel"] and desk.ran == []


def test_a_failed_smoke_is_a_result_and_not_a_traceback(smoke, capsys):
    """A bring-up program must never be the thing that spends a REPL the owner
    might still have had -- the follow-up question is typed INTO it."""
    smoke.raises = ValueError("no ack")
    boot_shell.main("Guition S3", "panel", ("panel",), "fake_smoke")
    assert "Moybyte panel smoke FAILED: ValueError: no ack" in capsys.readouterr().out


def test_a_missing_smoke_module_is_reported_rather_than_fatal(capsys):
    boot_shell.main("P4", "panel", ("panel",), "no_such_smoke_module")
    assert "Moybyte panel smoke FAILED:" in capsys.readouterr().out


@pytest.mark.parametrize("board", sorted(BOARDS))
def test_every_board_declares_three_things_and_takes_the_ladder(board):
    src = _shell_src(board)
    assert "import boot_shell" in src
    assert "boot_shell.main(BOARD, MODE, MODES, SMOKE)" in src
    for name in ("BOARD = ", "SMOKE = ", "MODE = ", "MODES = ("):
        assert name in src, "%s does not declare %s" % (board, name.strip(" ="))
    # The shapes the shared body replaced. A board that re-grows either is back
    # to the state this module was written out of.
    assert "RUN_" not in src, "%s went back to a flag per mode" % board
    assert "run_desktop()" not in src, "%s re-grew a private ladder" % board


@pytest.mark.parametrize("board", sorted(BOARDS))
def test_every_mode_a_board_offers_exists_in_its_smoke_module(board):
    """MODES is what `main` checks a typo against, so a rung that names no verb
    is a mode that reports "known" and then dies on a getattr."""
    src = _shell_src(board)
    modes = src.split("MODES = (")[1].split(")")[0]
    modes = [m.strip().strip('",') for m in modes.split(",") if m.strip()]
    assert "desktop" in modes
    smoke_name = BOARDS[board]
    assert 'SMOKE = "%s"' % smoke_name in src
    smoke_src = (ROOT / "firmware" / board / "modules"
                 / (smoke_name + ".py")).read_text(encoding="utf-8")
    for mode in modes:
        if mode == "desktop":
            continue
        assert "def %s(" % mode in smoke_src, (
            "%s offers MODE %r and %s.py defines no such verb"
            % (board, mode, smoke_name))

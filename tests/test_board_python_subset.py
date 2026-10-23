"""The Python the boards run is MicroPython's subset, and some of what it
leaves out fails only when the line runs, on glass: a slice with a step raises
NotImplementedError there ("only slices with step=1"), so a desk Editor whose
Blocks palette sliced its categories with `[side::2]` failed every frame of
the Code workspace on the P4s while every host test passed. This reads every
module a board stages for what the host cannot catch by running it."""

import ast
from pathlib import Path

from tools import board_config
from tools.p4_autotest import board_dirs

ROOT = Path(__file__).resolve().parent.parent


def _stepped_slices(path):
    tree = ast.parse(path.read_text(), str(path))
    return [node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Slice) and node.step is not None]


def test_board_modules_slice_without_a_step():
    staged = set()
    for board_dir in board_dirs().values():
        staged.update(board_config.staged_modules(board_dir, ROOT).values())
    assert len(staged) > 50, "the boards stage too few modules to say"
    found = ["%s:%d" % (p.relative_to(ROOT), line)
             for p in sorted(staged) for line in _stepped_slices(p)]
    assert not found, "a slice with a step raises on a board: " + ", ".join(found)

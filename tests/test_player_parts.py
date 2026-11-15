"""The Player and its two parts (docs/refactor_2026-11.md, phase 12).

`Player` (runtime/run/player.py) runs a cart; the app-cart face
(`player.face`, runtime/run/player_face.py) and the pacing half with the
front hand-off (`player.pace`, runtime/run/player_pace.py) are objects it
holds. Two rules keep them apart:

  1. THE PLAYER DEFINES NO METHOD OF EITHER PART. A name a part defines is
     not a method, a property or a class attribute of the Player too, so a
     verb lives in one place and every caller names the part.

  2. THE FRAME PATH MAKES NO CALL THROUGH EITHER PART. An attribute hop costs
     on a board. Every Player method a frame reaches -- `tick`,
     `frame_plan`, `handle_input`, `handle_pointer` and what they call on
     the Player, outside an exception handler -- names neither part; the
     part methods a frame runs are bound onto the Player once, at its
     construction.
"""

import ast
from pathlib import Path

from runtime.player import Player
from runtime.player_face import AppFace
from runtime.player_pace import Pace

ROOT = Path(__file__).resolve().parents[1]
PARTS = {"face": AppFace, "pace": Pace}
ENTRIES = ("tick", "frame_plan", "handle_input", "handle_pointer")


def _names(cls):
    return {n for k in cls.__mro__ if k is not object for n in vars(k)
            if not n.startswith("__")}


def test_the_player_defines_no_method_of_its_parts():
    mine = _names(Player)
    for attr, cls in PARTS.items():
        both = sorted(mine & _names(cls))
        assert not both, "Player and its %s both define %s" % (attr, both)


def _player_methods():
    tree = ast.parse((ROOT / "runtime/run/player.py").read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Player")
    return {f.name: f for f in cls.body if isinstance(f, ast.FunctionDef)}


def _walk_outside_handlers(node):
    """Every node under `node` but those inside an `except` clause: a frame
    that raised is the crash path, which the stop card takes from there."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.ExceptHandler):
            continue
        yield child
        yield from _walk_outside_handlers(child)


def _self_attr(node):
    return (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id == "self")


def _frame_path(methods):
    seen, todo = set(), list(ENTRIES)
    while todo:
        name = todo.pop()
        if name in seen or name not in methods:
            continue
        seen.add(name)
        for node in _walk_outside_handlers(methods[name]):
            if isinstance(node, ast.Call) and _self_attr(node.func):
                todo.append(node.func.attr)
    return seen


def test_the_frame_path_makes_no_call_through_either_part():
    methods = _player_methods()
    path = _frame_path(methods)
    assert {"tick", "frame_plan", "_run_ticks", "_tick_tail"} <= path, path
    bad = []
    for name in sorted(path):
        for node in _walk_outside_handlers(methods[name]):
            if _self_attr(node) and node.attr in PARTS:
                bad.append("Player.%s:%d self.%s" % (name, node.lineno, node.attr))
    assert not bad, "the frame path calls through a part:\n" + "\n".join(bad)


def test_the_part_methods_a_frame_runs_are_bound_once(tmp_path):
    """What the frame path calls of a part is the part's own method, bound
    on the Player when it is built."""
    from ws_helpers import build_ws

    pl = build_ws(tmp_path).run.player
    called = set()
    for name in _frame_path(_player_methods()):
        for node in _walk_outside_handlers(_player_methods()[name]):
            if isinstance(node, ast.Call) and _self_attr(node.func):
                called.add(node.func.attr)
    bound = {n for n in called if n in vars(pl) and callable(vars(pl)[n])}
    assert bound, "the frame path binds no part method"
    for n in sorted(bound):
        f = vars(pl)[n]
        assert f.__self__ in (pl.face, pl.pace), n

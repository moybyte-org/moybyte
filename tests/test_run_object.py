"""A run's state is ONE object (`procs.Run`, decision 6 of #246).

Everything the shell holds of a Python run -- its Player, Project, audio
session, canvas and the binding over it, its compiled frame, its view and its
clock -- lives on its `Run`; `ws.run` is the run the shell serves and a Proc
holds the rest. Nothing of a run lives on the Workstation, so a new field or a
new app cannot leak one run's state into another's by landing there. These
pins hold that line: the field set is read off `Run` itself, so a field added
to it is guarded the day it lands.
"""
import ast
import os
import re

from runtime import procs
from tools.board_config import runtime_file

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIELDS = sorted(procs.Run().__dict__)
# What the Workstation's run fields were called before they moved: a name
# here coming back on the Workstation is the old shape returning.
OLD = ("_run_canvas", "_run_canvas_stock", "_run_canvas_shared", "_fat_cart",
       "_world_made")
WS = r"(?<![\w.\]])(?:self\.su\.ws|self\._ws|self\.ws|su\.ws|ws)"


def _console_classes():
    """(path, ClassDef) for the Workstation and every mixin it is made of."""
    path = str(runtime_file("console.py"))
    tree = ast.parse(open(path).read())
    ws_cls = next(n for n in tree.body
                  if isinstance(n, ast.ClassDef) and n.name == "Workstation")
    names = {b.id for b in ws_cls.bases if isinstance(b, ast.Name)} | {"Workstation"}
    out = []
    shell = os.path.dirname(path)
    for fn in sorted(os.listdir(shell)):
        if not (fn.startswith("console") and fn.endswith(".py")):
            continue
        p = os.path.join(shell, fn)
        for n in ast.parse(open(p).read()).body:
            if isinstance(n, ast.ClassDef) and n.name in names:
                out.append((p, n))
                names.discard(n.name)
    assert not names, "Workstation bases not found: %s" % sorted(names)
    return out


def test_the_run_has_its_fields():
    assert set(FIELDS) >= {"player", "project", "audio", "canvas", "cart_frame",
                           "bound", "stock", "promoted", "app_full_canvas",
                           "app_window_canvas", "fat_cart", "canvas_tag",
                           "view", "cart_start_ms"}


def test_no_workstation_code_reaches_a_run_field_on_itself():
    """`self.player` (or any Run field, or an old name of one) inside the
    Workstation or a mixin of it: the run's state back on the Workstation.
    Its code goes through `self.run`."""
    bad = []
    names = set(FIELDS) | set(OLD)
    for path, cls in _console_classes():
        for node in ast.walk(cls):
            if (isinstance(node, ast.Attribute) and node.attr in names
                    and isinstance(node.value, ast.Name) and node.value.id == "self"):
                bad.append("%s:%d self.%s" % (os.path.relpath(path, ROOT),
                                              node.lineno, node.attr))
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                  and node.func.id in ("getattr", "setattr", "hasattr")
                  and len(node.args) >= 2
                  and isinstance(node.args[0], ast.Name) and node.args[0].id == "self"
                  and isinstance(node.args[1], ast.Constant)
                  and node.args[1].value in names):
                bad.append("%s:%d %s(self, %r)" % (os.path.relpath(path, ROOT),
                                                   node.lineno, node.func.id,
                                                   node.args[1].value))
    assert not bad, "the run's state on the Workstation:\n" + "\n".join(bad)


def test_no_code_reaches_a_run_field_on_the_workstation():
    """`ws.player`, `self.ws.canvas`, `ws.input.cart_start_ms`...: every tier
    reads the run through `ws.run` or the run it was handed. Strings count
    too: the board tools send their code to the console."""
    names = "|".join(re.escape(f) for f in sorted(set(FIELDS) | set(OLD) | {"game_view"},
                                                  key=len, reverse=True))
    pat = re.compile(WS + r"(?:\.input)?\.(" + names + r")\b")
    bad = []
    for top in ("runtime", "device", "tools", "firmware", "tests", "experiments"):
        for dirpath, dirs, files in os.walk(os.path.join(ROOT, top)):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"
                       and d != "modules"]
            for fn in files:
                if not fn.endswith(".py") or fn == "test_run_object.py":
                    continue
                p = os.path.join(dirpath, fn)
                for i, line in enumerate(open(p, encoding="utf-8", errors="replace")):
                    for m in pat.finditer(line):
                        if m.group(1) == "game_view" and ".input." not in m.group(0):
                            continue        # Workstation.game_view: the source rect
                        bad.append("%s:%d %s" % (os.path.relpath(p, ROOT), i + 1,
                                                 m.group(0)))
    assert not bad, "a run's field read off the Workstation:\n" + "\n".join(bad[:40])


def test_a_built_workstation_holds_no_run_field(tmp_path):
    """The running console, not just its source: none of the run's fields
    is an attribute of the Workstation, and the Player knows its run."""
    from ws_helpers import build_desktop_ws
    ws = build_desktop_ws(tmp_path)
    for name in set(FIELDS) | set(OLD):
        assert name not in ws.__dict__, name
        assert not hasattr(type(ws), name), name
    assert isinstance(ws.run, procs.Run)
    assert ws.run.player.run is ws.run
    assert not hasattr(procs, "World") and not hasattr(procs, "_swap")


def test_switching_runs_moves_one_reference(tmp_path):
    """Promoting a Proc puts its Run in front and the front's in the Proc
    (a handheld's: no windows to trade): the two Run objects keep every
    field they had."""
    from ws_helpers import build_ws
    ws = build_ws(tmp_path)
    p = procs.Proc("run:9", procs.Run())
    p.run.player = type(ws.run.player)(ws, ws.run.player.NAMES, p.run)
    p.run.project = type(ws.run.project)(ws)
    front = ws.run
    canvas, player = front.canvas, front.player
    ws.procs.list.append(p)
    ws.procs.by_key[p.key] = p
    ws.procs.promote(p)
    assert ws.run is not front and p.run is front
    assert ws.run.player.run is ws.run
    assert front.canvas is canvas and front.player is player
    assert ws.procs.spare is front          # an idle front: kept for the next


def _open(ws, title):
    from ws_helpers import open_cart
    open_cart(ws, title)
    assert ws.run.player.cart_error is None, ws.run.player.cart_error


def test_two_consoles_in_one_process_keep_their_own_runs(tmp_path):
    """Two Workstations built in a row, the first left running: each run is
    its own -- its Player, namespace, start stamp and clock -- and stepping
    the first between the second's frames moves nothing of the second's. A
    Run that outlived its console, or a stamp read off the wrong run, shows
    here as the second cart's clock standing still or its namespace gone."""
    from ws_helpers import build_ws
    a = build_ws(tmp_path / "a")
    _open(a, "Brick Siege Lua")
    for _ in range(3):
        a.frame(1 / 30)
    b = build_ws(tmp_path / "b")
    _open(b, "Brick Siege Lua")
    ra, rb = a.run, b.run
    assert ra is not rb and ra.player is not rb.player
    assert ra.player.run is ra and rb.player.run is rb
    assert rb.cart_start_ms is not None and ra.cart_start_ms is not None
    lua_a, lua_b = ra.player._lua, rb.player._lua
    assert lua_a is not lua_b
    seen = []
    for i in range(12):
        b.frame(1 / 30)
        a.frame(1 / 30)
        seen.append(float(lua_b.get_global("t")))
    assert a.run is ra and b.run is rb
    assert rb.player.ns is not None and rb.player.cart_error is None
    assert ra.player.ns is not None and ra.player.cart_error is None
    assert seen[-1] > seen[0], seen
    a.exit()
    for _ in range(3):
        b.frame(1 / 30)
    assert b.run is rb and rb.player.ns is not None
    assert float(lua_b.get_global("t")) > seen[-1]

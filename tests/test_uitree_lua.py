"""The UI tree API's Lua layer (runtime/run/uitree.lua) on the host console.

- An app written in it opens, paints, takes input and closes beside today's.
- Old against port: Moy Mesh's hand-written thread and Radio pages and their
  ports draw the same pixels through one input script, on the handheld and
  in a desk window (tests/uitree_lua_parity.py).
- The checker (tools/uitree_lua_check.py): one failing case per error code a
  Lua app can raise, each reported at its file and line.
"""
import json
import os
import shutil
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path[:0] = [ROOT, HERE, os.path.join(ROOT, "tools")]

import uitree_lua_parity as parity  # noqa: E402
import uitree_lua_check as checker  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "uitree_lua")


def _cart(root, name, main, extra=None):
    d = os.path.join(root, name + ".moy")
    os.makedirs(d)
    files = dict(extra or {})
    files["main.lua"] = main
    man = {"format": "moy-1", "title": name, "runtime": "lua", "main": "main.lua",
           "sources": [f for f in files if f not in ("fire.lua",)],
           "extensions": ["moybyte.ui", "moybyte.uitree"], "moybyte": {"type": "app", "permissions": []}}
    man["sources"].sort(key=lambda f: f == "main.lua")
    for f, text in files.items():
        with open(os.path.join(d, f), "w") as fh:
            fh.write(text)
    with open(os.path.join(d, "manifest.json"), "w") as fh:
        json.dump(man, fh)
    return d


def _console(carts, size="320"):
    from runtime import host_app
    from ws_helpers import open_cart, quiesce
    ws = host_app.build_workstation(carts, **parity.SIZES[size])
    ws.look.set_theme("moy", persist=False, variant="dark")
    drv = host_app.ConsoleDriver(ws)

    def frame():
        quiesce(ws)
        ws.mark_dirty()
        drv.frame(parity.DT)
    return ws, drv, frame, open_cart


def test_an_app_opens_paints_takes_input_and_closes(tmp_path):
    from runtime import host_app
    carts = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(carts)
    shutil.copytree(os.path.join(FIX, "counter.moy"), os.path.join(carts, "counter.moy"))
    ws, drv, frame, open_cart = _console(carts)
    open_cart(ws, "Tree Counter")
    for _ in range(4):
        frame()
    player = ws.run.player
    assert player is not None and player.cart_error is None
    before = bytes(drv.rgb888())
    for _ in range(3):
        drv.press("a")
        frame()
        frame()
    assert player.cart_error is None
    after = bytes(drv.rgb888())
    assert before != after, "three presses of the focused Add one changed nothing on glass"
    ws.spine.exit_to_caller()
    assert player.lua is None, "the app's Lua state outlived its run"


@pytest.mark.parametrize("size", sorted(parity.SIZES))
@pytest.mark.parametrize("pair", sorted(parity.PAIRS))
def test_old_and_port_draw_the_same_pixels(pair, size):
    wh, old, port, errors, _logs = parity.compare(pair, size)
    assert errors == []
    assert list(old) == list(port)
    moved = {name: parity.differing(wh, old[name], port[name]) for name in old}
    moved = {name: d for name, d in moved.items() if d[1]}
    assert moved == {}, "shots differ (box, pixels): %r; `tests/uitree_lua_parity.py OUT %s` keeps them" % (moved, pair)


def test_a_focus_on_no_node_stops_the_run_at_its_line(tmp_path):
    from runtime import host_app
    carts = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(carts)
    _cart(carts, "Focus", 'ui.app("Focus", { icon = "calc" })\n'
                          'ui.hook("opened", function() ui.focus("nope") end)\n'
                          'ui.page("p", function() ui.label("x") end)\n')
    ws, drv, frame, open_cart = _console(carts)
    open_cart(ws, "Focus")
    for _ in range(4):
        frame()
    err = ws.run.player.cart_error
    assert err and "error[N08] main.lua:2:" in err, err


def test_an_anchor_on_no_node_stops_the_run_at_the_popover(tmp_path):
    from runtime import host_app
    carts = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(carts)
    _cart(carts, "Anchor", 'ui.app("Anchor", { icon = "calc" })\n'
                           'local s = ui.state { open = true }\n'
                           'ui.page("p", function()\n'
                           '  ui.label("x")\n'
                           '  ui.popover("more", { shown = "open", anchor = "nope" }, function() ui.label("y") end)\n'
                           'end)\n')
    ws, drv, frame, open_cart = _console(carts)
    open_cart(ws, "Anchor")
    for _ in range(4):
        frame()
    err = ws.run.player.cart_error
    assert err and "error[N06] main.lua:5:" in err, err


def test_a_cancelled_timer_runs_nothing(tmp_path):
    from runtime import host_app
    from runtime.host import moy_log
    carts = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(carts)
    _cart(carts, "Timers", 'ui.app("Timers", { icon = "calc" })\n'
                           'local s = ui.state { log = "" }\n'
                           'ui.action("tick", "Tick", function() s.log = s.log .. "t" end)\n'
                           'ui.action("boom", "Boom", function() s.log = s.log .. "b" end)\n'
                           'ui.action("late", "Late", function() s.log = s.log .. "l" end)\n'
                           'ui.hook("opened", function()\n'
                           '  ui.after(0, "tick")\n'
                           '  ui.after(0, "boom")\n'
                           '  ui.after(60000, "late")\n'
                           '  ui.cancel("boom")\n'
                           'end)\n'
                           'ui.page("p", function() ui.label("{log}") end)\n')
    ws, drv, frame, open_cart = _console(carts)
    seq0 = moy_log.next()
    open_cart(ws, "Timers")
    for _ in range(12):
        frame()
    assert ws.run.player.cart_error is None
    lines = [r[4] for r in moy_log.read(seq0, 4000) if "[trace]" in str(r[4])]
    ran = [l.split()[3] for l in lines if " action " in l]
    assert "tick" in ran and "boom" not in ran and "late" not in ran, lines
    assert any("cancelled" in l for l in lines), lines


# -- the checker: one failing case per code ------------------------------------------

APP = 'ui.app("T", { icon = "calc" })\n'
PAGE = '\nui.page("p", function() ui.label("x") end)\n'
GO = 'ui.action("go", "Go", function() end)\n'
REC = 'local s = ui.state { r = ui.record { x = 0 } }\n'

# code: (main.lua, the line the error names, a fire.lua run after the build or None)
CASES = {
    "A01": (APP + GO + 'ui.page("p", function() ui.button(function() end) end)\n', 3, None),
    "A02": (APP + 'local s = ui.state { m = ui.model { key = "id", id = "" }, cur = ui.opt(ui.row_of("m")) }\n'
            'ui.action("del", "Delete", function(a) end, { row = "cur", takes = "arg", off = "hide" })' + PAGE, 3, None),
    "A03": (APP + 'ui.action("go", "Go", function(a, b) end)' + PAGE, 2, None),
    "A08": (APP + 'ui.action("go", "", function() end)' + PAGE, 2, None),
    "A09": (APP + 'ui.action(3, "Go", function() end)' + PAGE, 2, None),
    "A10": (APP + 'ui.after(100, "go")\n' + GO + PAGE, 2, None),
    "B01": (APP + 'local s = ui.state { n = 0 }\nui.page("p", function()\n  if s.n > 0 then ui.label("x") end\nend)\n', 4, None),
    "B02": (APP + 'local s = ui.state { n = 0 }\nui.page("p", function()\n  s.n = 1\n  ui.label("x")\nend)\n', 4, None),
    "B03": (APP + 'ui.page("p", function()\n  ui.action("go", "Go", function() end)\nend)\n', 3, None),
    "C01": (APP + 'local s = ui.state { n = 0 }\nui.page("p", function() ui.label("x", { shown = "{n}" }) end)\n', 3, None),
    "C02": (APP + 'local s = ui.state { a = false, b = false, c = false }\n'
            'ui.page("p", function() ui.label("x", { shown = "a && b || c" }) end)\n', 3, None),
    "C03": (APP + 'local s = ui.state { a = false, b = false, c = false }\n'
            'ui.page("p", function() ui.label("x", { shown = "a && (b || c)" }) end)\n', 3, None),
    "D01": (APP + 'ui.app("T2", { icon = "calc" })' + PAGE, 2, None),
    "D02": ('ui.app("T", { icon = "calc", size = 3 })' + PAGE, 1, None),
    "D04": (PAGE, "load", None),
    "E01": (APP + 'local s = ui.state { e = ui.enum("a", "b") }' + PAGE, 2, None),
    "E02": (APP + 'local s = ui.state { e = ui.enum("a", "b", { init = "c" }) }' + PAGE, 2, None),
    "E03": (APP + 'local s = ui.state { e = ui.enum("a", 3, { init = "a" }) }' + PAGE, 2, None),
    "G01": (APP + 'local s = ui.state { n = 0 }\nui.derive("x", "bool", "y")\nui.derive("y", "bool", "x")' + PAGE, 3, None),
    "H01": (APP + 'ui.hook("tick", function() end)\nui.page("p", function() ui.button("tick") end)\n', 3, None),
    "H03": (APP + 'ui.hook("tick", function() end, { hint = "Tick" })' + PAGE, 2, None),
    "I02": (APP + 'ui.action("go", "Go", function() end, { icon = "{n}" })' + PAGE, 2, None),
    "K01": (APP + 'ui.action("go", "Go", function() end, { keys = { "role:home" } })' + PAGE, 2, None),
    "K02": (APP + 'ui.action("go", "Go", function() end, { keys = { "s" } })\n'
            'ui.action("stop", "Stop", function() end, { keys = { "s" } })' + PAGE, 3, None),
    "K03": (APP + 'ui.action("go", "Go", function() end, { keys = { "alt+x" } })' + PAGE, 2, None),
    "N01": (APP + 'ui.page("p", function() ui.label("x", { nope = 1 }) end)\n', 2, None),
    "N02": (APP + GO + 'ui.page("p", function()\n  ui.button("go")\n  ui.button("go")\nend)\n', 5, None),
    "N03": (APP + 'local s = ui.state { m = ui.model { key = "id", id = "" } }\nui.page("p", function()\n'
            '  ui.rows("m", function()\n    ui.rows("m", function() ui.label("x") end)\n  end)\nend)\n', 5, None),
    "N04": (APP + 'ui.at(0, 0, 10, 10)' + PAGE, 2, None),
    "O01": (APP + 'local s = ui.state { a = false }\nui.action("go", "Go", function() end, { when = "a" })' + PAGE, 3, None),
    "O02": (APP + 'ui.action("go", "Go", function() end, { off = "grey" })' + PAGE, 2, None),
    "O04": (APP + 'local s = ui.state { a = false }\nui.page("p", function() ui.label("x", { when = "a" }) end)\n', 3, None),
    "P01": (APP + 'ui.action("open", "Open", nil, { push = "nope" })' + PAGE, 2, None),
    "P02": (APP + 'ui.action("open", "Open", nil, { push = "p", takes = "row" })' + PAGE, 2, None),
    "P03": (APP + 'ui.action("open", "Open", function() end, { push = "p" })' + PAGE, 2, None),
    "P04": (APP + 'local s = ui.state { n = 0 }' + PAGE + 'ui.page("q", function() ui.label("y") end, { arg = "n" })\n',
            4, None),
    "S01": (APP + 'local s = ui.state { n = 0 }\nlocal x = s.nope' + PAGE, 3, None),
    "S02": (APP + REC + 'ui.action("go", "Go", function()\n  s.r = { x = "a" }\nend)' + PAGE, 4, 'ui._fire("go")\n'),
    "S03": (APP + REC + 'ui.action("go", "Go", function()\n  s.r.x = 1\nend)' + PAGE, 4, 'ui._fire("go")\n'),
    "S04": (APP + REC + 'ui.action("go", "Go", function()\n  local v = s.r.nope\nend)' + PAGE, 4, 'ui._fire("go")\n'),
    "S05": (APP + REC + 'ui.action("go", "Go", function()\n  s.r = 5\nend)' + PAGE, 4, 'ui._fire("go")\n'),
    "S06": (APP + 'local s = ui.state { n = 0 }\ns.n = 1' + PAGE, 3, None),
    "P05": (APP + 'ui.action("go", "Go", function()\n  ui.pop()\nend)' + PAGE, 3, 'ui._fire("go")\n'),
    "S07": (APP + 'local s = ui.state { m = ui.model { name = "" } }' + PAGE, 2, None),
    "S09": (APP + 'local s = ui.state { n = 0 }\nui.derive("big", "bool", "n>3")\n'
            'ui.action("go", "Go", function()\n  s.big = true\nend)' + PAGE, 5, 'ui._fire("go")\n'),
    "S10": (APP + 'local s = ui.state { unread = 0 }\nui.action("go", "Go", function()\n  s.unread = 0\nend)\n'
            'ui.page("p", function() ui.scroll({ new = "unread" }, function() ui.label("x") end) end)\n', 4,
            'ui._fire("go")\n'),
    "S11": (APP + 'local s = ui.state { r = ui.row_of(3) }' + PAGE, 2, None),
    "S13": (APP + 'local s = ui.state { x = print }' + PAGE, 2, None),
    "T01": (APP + 'local s = ui.state { n = 0 }\nui.page("p", function() ui.label("{n|nope}") end)\n', 3, None),
    "T02": (APP + 'ui.page("p", function() ui.label("{@nope}") end)\n', 2, None),
    "T03": (APP + 'local s = ui.state { n = 0 }\nui.page("p", function() ui.label("{n.x}") end)\n', 3, None),
    "T04": (APP + 'local s = ui.state { e = ui.enum("a", "b", { init = "a" }) }\nui.labels("L", { a = "A" })\n'
            'ui.page("p", function() ui.label("{e|map L}") end)\n', 4, None),
    "T05": (APP + 'ui.page("p", function() ui.label("{.x}") end)\n', 2, None),
    "U02": (APP + 'local s = ui.state { go = 0 }\n' + GO + PAGE, 3, None),
    "W01": (APP + 'local s = ui.state { t = "" }\nui.action("go", "Go", function(row) end, { takes = "row" })\n'
            'ui.page("p", function() ui.field("t", { on = "go" }) end)\n', 4, None),
    # what only the source read sees
    "Q01": (APP + 'ui.action("go", "Go", function()\n  undefined_thing()\nend)' + PAGE, 3, None),
    "S13 source": (APP + 'local s = ui.state { a = nil, n = 0 }' + PAGE, 2, None),
    "P01 source": (APP + 'ui.action("go", "Go", function()\n  ui.push("nope")\nend)' + PAGE, 3, None),
    "A01 source": (APP + 'ui.action("go", "Go", function()\n  ui.post("nope")\nend)' + PAGE, 3, None),
    "A02 source": (APP + 'ui.hook("h", function() end)\nui.action("go", "Go", function()\n  ui.post("h", 3)\nend)'
                   + PAGE, 4, None),
    "S09 source": (APP + 'local s = ui.state { n = 0 }\nui.derive("big", "bool", "n>3")\n'
                   'ui.action("go", "Go", function()\n  s.big = true\nend)' + PAGE, 5, None),
    "S03 source": (APP + REC + 'ui.action("go", "Go", function()\n  s.r.x = 1\nend)' + PAGE, 4, None),
    "S04 source": (APP + REC + 'ui.action("go", "Go", function()\n  local v = s.r.nope\nend)' + PAGE, 4, None),
}

# The one code a Lua app cannot reach: X01 is the layer's own assertion that a
# condition was checked at build.
NOT_RAISABLE = {"X01"}
# what only a frame raises, cased on the console above
RUN_CASES = {"N08", "N06"}


def test_every_code_the_layer_raises_has_a_case():
    import re
    with open(checker.LAYER) as fh:
        raised = set(re.findall(r'fail\("([A-Z][0-9]{2})"', fh.read()))
    with open(checker.__file__) as fh:
        raised |= set(re.findall(r'_err\("([A-Z][0-9]{2})"', fh.read()))
    cased = {c.split()[0] for c in CASES}
    assert raised - NOT_RAISABLE - RUN_CASES - cased == set()
    assert cased - raised == set()


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_checker_reports_each_code_at_its_line(case, tmp_path):
    main, line, fire = CASES[case]
    code = case.split()[0]
    extra = {"fire.lua": fire} if fire else None
    d = _cart(str(tmp_path), "case", main, extra)
    errors = [e for e in checker.check(d, after=["fire.lua"] if fire else ()) if e.startswith("error[")]
    where = "load" if line == "load" else "main.lua:%d" % line
    want = "error[%s] %s:" % (code, where)
    assert any(e.startswith(want) for e in errors), "%s not in %r" % (want, errors)
    for e in errors:
        assert "; fix: " in e and "(expected " in e, e


@pytest.mark.parametrize("cart", ["counter", "mesh_thread", "mesh_radio"])
def test_the_fixtures_check_clean(cart, tmp_path):
    d = os.path.join(str(tmp_path), cart + ".moy")
    shutil.copytree(parity.fixture(cart), d)
    for f in ("mesh.lua", "data.lua"):
        src = os.path.join(parity.MESH, f) if f == "mesh.lua" else os.path.join(parity.fixture("mesh_thread"), f)
        if not os.path.exists(os.path.join(d, f)):
            shutil.copy(src, d)
    assert checker.check(d) == []

"""The UI tree API's Lua layer (runtime/run/uitree.lua) on the host console.

- An app written in it opens, paints, takes input and closes beside today's.
- Old against port: Moy Mesh's hand-written thread and Radio pages and their
  ports draw the same pixels through one input script, on the handheld and
  in a desk window (tests/uitree_lua_parity.py), on the reference back end.
- Every other test runs on the engine in use (the C engine on the host).
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
    """The reference back end (runtime/run/uitree_replay.lua) against the
    hand-written pages it replays into today's verbs: the C engine lays out
    by its own rules, and the ports hold a record and bubbles it does not
    draw yet (E00)."""
    import uitree_app
    uitree_app.use_engine("reference")
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


def test_a_foot_that_shares_its_width_and_a_slider_icon_draw(tmp_path):
    """foot look="share" spreads its row's buttons over its width; a
    slider's icon= stands before its label: each moves the pixels."""
    from runtime import host_app
    carts = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(carts)
    body = ('ui.app("%s", { icon = "calc" })\n'
            'local s = ui.state { v = 3 }\n'
            'ui.action("a", "Add", function() end)\nui.action("b", "Back", function() end)\n'
            'ui.page("p", function()\n'
            '  ui.scroll("body", function() ui.slider("v", { label = "Level", lo = 0, hi = 9%s }) end)\n'
            '  ui.foot({%s}, function() ui.row(function() ui.button("a") ui.button("b") end) end)\n'
            'end)\n')
    shots = {}
    for name, icon, look in (("Plain", "", ""), ("Shared", "", ' look = "share" '), ("Iconed", ', icon = "calc"', "")):
        _cart(carts, name, body % (name, icon, look))
    ws, drv, frame, open_cart = _console(carts)
    for name in ("Plain", "Shared", "Iconed"):
        open_cart(ws, name)
        for _ in range(4):
            frame()
        assert ws.run.player.cart_error is None, ws.run.player.cart_error
        shots[name] = bytes(drv.rgb888())
        ws.spine.exit_to_caller()
    assert shots["Shared"] != shots["Plain"] and shots["Iconed"] != shots["Plain"]


def test_a_split_a_grid_and_a_panel_draw_on_the_c_engine(tmp_path):
    """The placing kinds only the C engine draws: a split's parts, a grid's
    cells and a panel each move the pixels, and the reference back end
    refuses them by name (E00)."""
    import uitree_app
    from runtime import host_app
    uitree_app.use_engine("c")
    carts = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(carts)
    body = ('ui.app("%s", { icon = "calc" })\n'
            'ui.action("a", "Add", function() end)\nui.action("b", "Back", function() end)\n'
            'ui.page("p", function()\n%s\nend)\n')
    pages = {
        "Plain": '  ui.label("one")\n  ui.label("two")',
        "Split": '  ui.split({ dir = "row", sizes = { 1, 1 } }, function() ui.label("one") ui.label("two") end)',
        "Grid": '  ui.grid({ cols = 2, rows = 1 }, function() ui.button("a") ui.button("b") end)',
        "Panel": '  ui.panel("side", function() ui.label("one") end)\n  ui.label("two")',
    }
    for name, page in pages.items():
        _cart(carts, name, body % (name, page))
    ws, drv, frame, open_cart = _console(carts)
    shots = {}
    for name in pages:
        open_cart(ws, name)
        for _ in range(4):
            frame()
        assert ws.run.player.cart_error is None, (name, ws.run.player.cart_error)
        shots[name] = bytes(drv.rgb888())
        ws.spine.exit_to_caller()
    assert shots["Split"] != shots["Plain"] and shots["Grid"] != shots["Plain"]
    uitree_app.use_engine("reference")
    open_cart(ws, "Grid")
    for _ in range(4):
        frame()
    err = ws.run.player.cart_error
    assert err and "error[E00] main.lua:5:" in err and "ui.grid" in err, err


def test_the_props_only_the_c_engine_draws_lower_and_draw(tmp_path):
    """A button's marked, tone and w, a group's and a row's close, a row's
    gap, pad and mid, a slider's look and a rows' changed: each
    page opens on the C engine and draws, and the reference refuses one by
    name (E00)."""
    import uitree_app
    from runtime import host_app
    uitree_app.use_engine("c")
    carts = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(carts)
    head = ('ui.app("%s", { icon = "calc" })\n'
            'local s = ui.state { on = true, v = 3, q = "", items = ui.model { key = "id", id = "" } }\n'
            'ui.action("a", "Add", function() end)\nui.action("x", "Close", function() end)\n'
            'ui.action("pick", "Pick", function(row) end, { takes = "row" })\n'
            'ui.hook("opened", function() s.items:set({ { id = "one" }, { id = "two" } }) end)\n'
            'ui.page("p", function()\n%s\nend)\n')
    pages = {
        "Marked": '  ui.button("a", { marked = "on", tone = "danger", w = 60 })',
        "Closer": '  ui.group("G", { close = "x" }, function() ui.label("in") end)',
        "Spaced": '  ui.foot(function() ui.row({ close = "x", gap = 4, pad = 2, mid = true }, function() ui.button("a") end) end)',
        "Steps": '  ui.slider("v", { label = "Level", lo = 0, hi = 9, look = "steps" })',
        "Picked": '  ui.rows("items", { changed = "pick" }, function() ui.label("{.id}") end)',
    }
    for name, page in pages.items():
        _cart(carts, name, head % (name, page))
    ws, drv, frame, open_cart = _console(carts)
    for name in pages:
        open_cart(ws, name)
        for _ in range(4):
            frame()
        assert ws.run.player.cart_error is None, (name, ws.run.player.cart_error)
        ws.spine.exit_to_caller()
    uitree_app.use_engine("reference")
    open_cart(ws, "Steps")
    for _ in range(4):
        frame()
    err = ws.run.player.cart_error
    assert err and "error[E00] main.lua:8:" in err and "ui.slider's look" in err, err


def test_tabs_cards_and_a_rows_group_draw_and_a_filter_is_refused_on_the_c_engine(tmp_path):
    """Tabs over an enum, cards with their card, and a rows' group open on
    the C engine and draw; a rows' filter, which the engine does not draw
    (nor for a Python app), stops the run at its line with E00."""
    import uitree_app
    from runtime import host_app
    uitree_app.use_engine("c")
    carts = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(carts)
    head = ('ui.app("%s", { icon = "calc" })\n'
            'local s = ui.state { tab = ui.enum("a", "b", { init = "a" }), q = "",\n'
            '  items = ui.model { key = "id", id = "", kind = "" } }\n'
            'ui.labels("TABS", { a = "A", b = "B" })\n'
            'ui.action("pick", "Pick", function(row) end, { takes = "row" })\n'
            'ui.hook("opened", function() s.items:set({ { id = "one", kind = "x" }, { id = "two", kind = "y" } }) end)\n'
            'ui.page("p", function()\n%s\nend)\n')
    pages = {
        "Tabbed": '  ui.tabs("tab", { labels = "TABS", offered = { b = "q" } })',
        "Shelf": '  ui.cards("items", { w = 80, h = 60 }, function() ui.card("pick", { label = "{.id}", sub = "{.kind}" }) end)',
        "Grouped": '  ui.rows("items", { group = "{.kind}" }, function() ui.label("{.id}") end)',
        "Found": '  ui.rows("items", { filter = "q" }, function() ui.label("{.id}") end)',
    }
    for name, page in pages.items():
        _cart(carts, name, head % (name, page))
    ws, drv, frame, open_cart = _console(carts)
    for name in ("Tabbed", "Shelf", "Grouped"):
        open_cart(ws, name)
        for _ in range(4):
            frame()
        assert ws.run.player.cart_error is None, (name, ws.run.player.cart_error)
        ws.spine.exit_to_caller()
    open_cart(ws, "Found")
    for _ in range(4):
        frame()
    err = ws.run.player.cart_error
    assert err and "error[E00] main.lua:8:" in err and "filter" in err, err


def test_a_canvas_draws_with_the_carts_verbs_inside_the_frame(tmp_path):
    """A canvas's draw runs inside the C engine's frame at the canvas's rect
    and draws with the cart's own verbs; a draw that writes state stops the
    run at its line (S06), as a mistake in a draw does. The reference back
    end refuses a canvas by name (E00)."""
    import uitree_app
    from runtime import host_app
    uitree_app.use_engine("c")
    carts = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(carts)
    head = ('ui.app("%s", { icon = "calc" })\n'
            'local s = ui.state { n = 0 }\n'
            'ui.page("p", function()\n%s\nend)\n')
    pages = {
        "Bare": '  ui.label("x")',
        "Pad": '  ui.label("x")\n  ui.canvas("pad", { h = 40, draw = function(x, y, w, h) rect(x, y, w, h, 8) end })',
        "Writes": '  ui.canvas("pad", { h = 40, draw = function(x, y, w, h)\n    s.n = 1\n  end })',
    }
    for name, page in pages.items():
        _cart(carts, name, head % (name, page))
    ws, drv, frame, open_cart = _console(carts)
    shots = {}
    for name in ("Bare", "Pad"):
        open_cart(ws, name)
        for _ in range(4):
            frame()
        assert ws.run.player.cart_error is None, (name, ws.run.player.cart_error)
        shots[name] = bytes(drv.rgb888())
        ws.spine.exit_to_caller()
    assert shots["Pad"] != shots["Bare"]
    open_cart(ws, "Writes")
    for _ in range(4):
        frame()
    err = ws.run.player.cart_error
    assert err and "error[S06] main.lua:5:" in err, err
    ws.spine.exit_to_caller()
    uitree_app.use_engine("reference")
    open_cart(ws, "Pad")
    for _ in range(4):
        frame()
    err = ws.run.player.cart_error
    assert err and "error[E00] main.lua:5:" in err and "ui.canvas" in err, err


def test_a_canvas_pointer_hands_down_drag_and_up_in_its_own_coordinates(tmp_path):
    """A canvas with pointer= on the C engine: a press on it hands its action
    a read-only event (kind, x, y, source) at the canvas's own coordinates,
    the drag that follows and the release, each traced as Python's are."""
    import uitree_app
    from runtime import host_app
    from runtime.host import moy_log
    uitree_app.use_engine("c")
    carts = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(carts)
    _cart(carts, "Pointer", 'ui.app("Pointer", { icon = "calc" })\n'
          'local s = ui.state { got = "" }\n'
          'ui.action("feel", "Feel", function(ev) s.got = s.got .. ev.kind .. ":" .. ev.source .. " " end,\n'
          '  { takes = "pointer" })\n'
          'ui.page("p", function()\n'
          '  ui.canvas("pad", { h = 120, pointer = "feel", draw = function(x, y, w, h) rect(x, y, w, h, 8) end })\n'
          'end)\n')
    ws, drv, frame, open_cart = _console(carts)
    seq = moy_log.next()
    open_cart(ws, "Pointer")
    for _ in range(4):
        frame()
    assert ws.run.player.cart_error is None, ws.run.player.cart_error
    cx, cy = ws.sys_canvas.w // 2, ws.sys_canvas.h // 2
    drv.touch(cx, cy)
    frame()
    drv.touch_drag(cx + 5, cy + 3)
    frame()
    drv.touch_up()
    frame()
    frame()
    assert ws.run.player.cart_error is None, ws.run.player.cart_error
    lines = [r[4].split("[trace] ", 1)[1] for r in moy_log.read(seq, 4000) if "[trace] " in r[4]]
    acts = [" ".join(l.split()[1:]) for l in lines if l.split()[1:2] == ["action"]]
    assert acts == ["action feel from pointer down on canvas 'pad'", "action feel from pointer drag on canvas 'pad'",
                    "action feel from pointer up on canvas 'pad'"], lines
    assert any("down:touch drag:touch up:touch" in l for l in lines), lines


def test_a_letter_key_and_the_confirm_role_fire_on_the_c_engine(tmp_path):
    """An action's letter key fires it, traced `key 's'`, and the confirm
    role's key fires its action where nothing on the page is focused, as
    uitree_c.py's keys() fires them for a Python app."""
    import uitree_app
    from runtime import host_app
    from runtime.host import moy_log
    uitree_app.use_engine("c")
    carts = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(carts)
    _cart(carts, "Keys", 'ui.app("Keys", { icon = "calc" })\n'
          'local s = ui.state { n = 0 }\n'
          'ui.action("save", "Save", function() s.n = s.n + 1 end, { keys = { "s" } })\n'
          'ui.action("go", "Go", function() s.n = s.n + 10 end, { keys = { "role:confirm" } })\n'
          'ui.page("p", function() ui.label("{n}") end)\n')
    ws, drv, frame, open_cart = _console(carts)
    seq = moy_log.next()
    open_cart(ws, "Keys")
    for _ in range(4):
        frame()
    drv.type_char(ord("s"))
    frame()
    frame()
    drv.press("a")
    frame()
    frame()
    assert ws.run.player.cart_error is None, ws.run.player.cart_error
    lines = [r[4].split("[trace] ", 1)[1] for r in moy_log.read(seq, 4000) if "[trace] " in r[4]]
    acts = [" ".join(l.split()[1:]) for l in lines if l.split()[1:2] == ["action"]]
    assert acts == ["action save from key 's'", "action go from role confirm"], lines


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
    "C01 single": (APP + 'local s = ui.state { a = false, b = false }\n'
                   'ui.page("p", function() ui.label("x", { shown = "a & b" }) end)\n', 3, None),
    "C03": (APP + 'local s = ui.state { a = false, b = false, c = false }\n'
            'ui.page("p", function() ui.label("x", { shown = "a && (b || c)" }) end)\n', 3, None),
    "D01": (APP + 'ui.app("T2", { icon = "calc" })' + PAGE, 2, None),
    "D02": ('ui.app("T", { icon = "calc", size = 3 })' + PAGE, 1, None),
    "D04": (PAGE, "load", None),
    "E01": (APP + 'local s = ui.state { e = ui.enum("a", "b") }' + PAGE, 2, None),
    "E02": (APP + 'local s = ui.state { e = ui.enum("a", "b", { init = "c" }) }' + PAGE, 2, None),
    "E03": (APP + 'local s = ui.state { e = ui.enum("a", 3, { init = "a" }) }' + PAGE, 2, None),
    "G01": (APP + 'local s = ui.state { n = 0 }\nui.derive("x", { cond = "y" })\nui.derive("y", { cond = "x" })' + PAGE, 3, None),
    "H01": (APP + 'ui.hook("tick", function() end)\nui.page("p", function() ui.button("tick") end)\n', 3, None),
    "H03": (APP + 'ui.hook("tick", function() end, { hint = "Tick" })' + PAGE, 2, None),
    "I02": (APP + 'ui.action("go", "Go", function() end, { icon = "{n}" })' + PAGE, 2, None),
    "K01": (APP + 'ui.action("go", "Go", function() end, { keys = { "role:home" } })' + PAGE, 2, None),
    "K02": (APP + 'ui.action("go", "Go", function() end, { keys = { "s" } })\n'
            'ui.action("stop", "Stop", function() end, { keys = { "s" } })' + PAGE, 3, None),
    "K03": (APP + 'ui.action("go", "Go", function() end, { keys = { "alt+x" } })' + PAGE, 2, None),
    "N01": (APP + 'ui.page("p", function() ui.label("x", { nope = 1 }) end)\n', 2, None),
    "N01 scroll": (APP + 'ui.page("p", function() ui.scroll(function() ui.label("x") end) end)\n', 2, None),
    "N01 derive": (APP + 'local s = ui.state { n = 0 }\nui.derive("big", "bool", "n>3")' + PAGE, 3, None),
    "N01 text derive": (APP + 'local s = ui.state { n = 0 }\nui.derive("left", { text = "{n} left" })\n'
                        'ui.derive("big", { cond = "n>3", text = "x" })' + PAGE, 4, None),
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
    "S09": (APP + 'local s = ui.state { n = 0 }\nui.derive("big", { cond = "n>3" })\n'
            'ui.action("go", "Go", function()\n  s.big = true\nend)' + PAGE, 5, 'ui._fire("go")\n'),
    "S10": (APP + 'local s = ui.state { unread = 0 }\nui.action("go", "Go", function()\n  s.unread = 0\nend)\n'
            'ui.page("p", function() ui.scroll("list", { new = "unread" }, function() ui.label("x") end) end)\n', 4,
            'ui._fire("go")\n'),
    "S11": (APP + 'local s = ui.state { r = ui.row_of(3) }' + PAGE, 2, None),
    "S13": (APP + 'local s = ui.state { x = print }' + PAGE, 2, None),
    "T01": (APP + 'local s = ui.state { n = 0 }\nui.page("p", function() ui.label("{n|nope}") end)\n', 3, None),
    "T02": (APP + 'ui.page("p", function() ui.label("{@nope}") end)\n', 2, None),
    "T03": (APP + 'local s = ui.state { n = 0 }\nui.page("p", function() ui.label("{n.x}") end)\n', 3, None),
    "T03 signal": (APP + 'local s = ui.state { t = "" }\nui.page("p", function() ui.info("Signal", { signal = "{t}" }) end)\n', 3, None),
    "N01 signal": (APP + 'ui.page("p", function() ui.info("Signal", { signal = "3 bars" }) end)\n', 2, None),
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
    "S09 source": (APP + 'local s = ui.state { n = 0 }\nui.derive("big", { cond = "n>3" })\n'
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

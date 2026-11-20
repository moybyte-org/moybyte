"""The cross-tier rules every runtime keeps alike.

  * a native cart (`"moybyte": {"native": [chips]}`, main `main.<chip>.elf`)
    is compiled exactly as a wasm cart is: the store never writes text over
    its module, its code is its `src/`, and a crash keeps the stop card;
  * the Blocks tab is a Python cart's only (blocks compile to Python), and no
    blocks save writes into a Lua or compiled cart;
  * a Lua cart that asks for "multiplayer" has the `net` a Python cart has.
"""

import json
import os

import pytest

from runtime import host_app
from ws_helpers import editor_row, open_cart

MOY = host_app.moy_carts


def _native(root, title="Native Bits", chips=("host",)):
    d = os.path.join(root, "native_bits.moy")
    os.makedirs(os.path.join(d, "src"))
    with open(os.path.join(d, "manifest.json"), "w") as f:
        json.dump({"title": title, "moybyte": {"type": "game", "native": list(chips)}}, f)
    for chip in chips:
        with open(os.path.join(d, "main.%s.elf" % chip), "wb") as f:
            f.write(b"\x7fELF not text")
    with open(os.path.join(d, "src", "game.c"), "w") as f:
        f.write("int frame;\n")
    return d


def _lua(root, title="Lua Bits"):
    d = os.path.join(root, "lua_bits.moy")
    os.makedirs(d)
    with open(os.path.join(d, "manifest.json"), "w") as f:
        json.dump({"title": title, "runtime": "lua", "main": "main.lua"}, f)
    with open(os.path.join(d, "main.lua"), "w") as f:
        f.write("")
    return d


def _store(tmp_path, *makers):
    root = str(tmp_path / "carts")
    MOY.ensure_dirs(root)
    for m in makers:
        m(root)
    return root


def _cart(ws, title):
    return next(c for c in ws.carts.all if c["title"] == title)


# -- a native cart is compiled ---------------------------------------------------


def test_the_store_treats_a_native_cart_as_compiled(tmp_path):
    path = _native(_store(tmp_path))
    cart = MOY.load(path)
    assert cart["main"] == "main.host.elf"
    assert MOY.is_compiled(cart) and not MOY.is_python(cart)
    assert MOY.cart_sources(cart) == ["src/game.c"]
    assert MOY.source_text(cart) == "int frame;\n"
    # C is no Python, and the gate does not pretend it is
    assert MOY.runtime_compile_check(cart, "int frame = 2;\n") == (True, "")


def test_no_text_write_reaches_a_native_module(tmp_path):
    path = _native(_store(tmp_path))
    cart = MOY.load(path)
    elf = os.path.join(path, "main.host.elf")
    before = open(elf, "rb").read()
    # its src/ is rebuilt into the module on a computer: an edit here is one
    # Play never runs, so the store writes none
    assert MOY.save_code(cart, "int frame = 1;\n")[0] == MOY.SAVE_BAD_SYNTAX
    assert open(os.path.join(path, "src", "game.c")).read() == "int frame;\n"
    assert MOY.save_code(cart, "x", name="main.host.elf")[0] == MOY.SAVE_BAD_SYNTAX
    assert open(elf, "rb").read() == before
    assert MOY.save_blocks(cart, {"objects": []})[0] == MOY.SAVE_BAD_SYNTAX
    assert not os.path.exists(os.path.join(path, "main.py"))
    assert open(elf, "rb").read() == before


def test_a_copy_of_a_native_cart_carries_its_module_bytes(tmp_path):
    root = _store(tmp_path)
    cart = MOY.load(_native(root))
    dup = MOY.duplicate(cart, root)
    assert dup is not None and dup["main"] == "main.host.elf"
    assert open(os.path.join(dup["path"], "main.host.elf"), "rb").read() == b"\x7fELF not text"
    assert MOY.cart_sources(dup) == ["src/game.c"]


def test_the_editor_opens_a_native_cart_on_its_src(tmp_path):
    ws = host_app.build_workstation(_store(tmp_path, _native))
    ws.open_in_editor(_cart(ws, "Native Bits"))
    assert ws.text.code_sources() == ["src/game.c"]
    ws.text.set_menu_view("code")
    assert ws.editor_app.tab == "code"
    assert ws.text.code_file_name() == "src/game.c"
    assert "int frame" in ws.editor.text()


def test_a_native_crash_keeps_the_stop_card(tmp_path):
    ws = host_app.build_workstation(_store(tmp_path, _native))
    ws.open_in_editor(_cart(ws, "Native Bits"))
    ws.cart_error = "trap"
    assert ws.crash_to_code() is False


# -- blocks are Python's ---------------------------------------------------------


def test_only_a_python_cart_has_a_blocks_tab(tmp_path):
    ws = host_app.build_workstation(_store(tmp_path, _lua, _native))
    for title in ("Lua Bits", "Native Bits"):
        ws.open_in_editor(_cart(ws, title))
        assert not ws.text.takes_blocks()
        assert "blocks" not in editor_row(ws)
        ws.text.set_menu_view("blocks")
        assert ws.editor_app.tab == "cards", title
    py = next(c for c in ws.carts.all if MOY.is_python(c) and c.get("path"))
    ws.open_in_editor(py)
    assert ws.text.takes_blocks()
    assert "blocks" in editor_row(ws)


def test_a_blocks_save_never_writes_python_into_a_lua_cart(tmp_path):
    ws = host_app.build_workstation(_store(tmp_path, _lua))
    cart = _cart(ws, "Lua Bits")
    assert MOY.save_blocks(cart, {"objects": []})[0] == MOY.SAVE_BAD_SYNTAX
    ws.open_in_editor(cart)
    ws.block_ui.build()
    assert ws.block_ui.save_blocks() is False
    path = cart["path"]
    assert not os.path.exists(os.path.join(path, "main.py"))
    assert not os.path.exists(os.path.join(path, "blocks.json"))
    assert ws.run.project.cart["src"] == ""


# -- a Lua cart plays with a friend ----------------------------------------------

NET_LUA = """
on_net(function(m)
  net.send({got = m.hello, list = {1, 2.5, "a\\"b\\n"}, ok = true})
end)
function _update() end
function _draw() cls(0) end
"""


def test_a_lua_cart_sends_and_hears_net_messages(tmp_path):
    from runtime import players as players_mod
    root = _store(tmp_path)
    cart = MOY.create("Lua Net", root, src=NET_LUA, type="game",
                      runtime="lua", main="main.lua")
    man_path = os.path.join(cart["path"], "manifest.json")
    with open(man_path) as f:
        man = json.load(f)
    man["moybyte"]["permissions"] = ["multiplayer"]
    with open(man_path, "w") as f:
        json.dump(man, f)
    ws = host_app.build_workstation(root)
    if "lua" not in ws.runtimes:
        pytest.skip("no host Lua tier")
    drv = host_app.ConsoleDriver(ws)
    open_cart(ws, "Lua Net")
    assert ws.run.player.cart_error is None, ws.run.player.cart_error
    peer = players_mod.LoopbackNet()
    peer.link(ws.net)
    heard = []
    peer.on_message(heard.append)
    peer.send({"hello": 7})
    drv.frame(1 / 30)
    assert ws.run.player.cart_error is None, ws.run.player.cart_error
    peer.pump()
    assert heard == [{"got": 7, "list": [1, 2.5, 'a"b\n'], "ok": True}]


# -- the Editor offers a tab only where the run reads what it writes ---------------


def _wasm(root, title="Wasm Bits"):
    d = os.path.join(root, "wasm_bits.moy")
    os.makedirs(os.path.join(d, "src"))
    with open(os.path.join(d, "manifest.json"), "w") as f:
        json.dump({"title": title, "runtime": "wasm", "main": "main.wasm",
                   "moybyte": {"type": "game"}}, f)
    with open(os.path.join(d, "main.wasm"), "wb") as f:
        f.write(b"\0asm\1\0\0\0")
    with open(os.path.join(d, "src", "main.c"), "w") as f:
        f.write("int frame;\n")
    return d


ASSET_TABS = ("paint", "map", "scene", "music")


def test_each_tier_is_offered_the_tabs_its_run_reads(tmp_path):
    ws = host_app.build_workstation(_store(tmp_path, _lua, _native, _wasm))
    want = {"Lua Bits": ASSET_TABS, "Wasm Bits": ("paint", "map", "music"),
            "Native Bits": ()}
    for title, tabs in want.items():
        ws.open_in_editor(_cart(ws, title))
        app = ws.editor_app
        row = editor_row(ws)
        for tab in ASSET_TABS:
            assert (tab in row) == (tab in tabs), (title, tab)
            ws.text.set_menu_view(tab)
            assert app.tab == (tab if tab in tabs else "cards"), (title, tab)
        assert app._offers("mine") == (title != "Native Bits")


def test_a_native_carts_config_tab_has_no_make_it_mine_cards_and_says_why(tmp_path):
    ws = host_app.build_workstation(_store(tmp_path, _native, _wasm))
    from runtime import ui_draw
    cart = _cart(ws, "Native Bits")
    cart["edit"] = [{"key": "speed", "type": "int", "min": 1, "max": 9, "default": 3}]
    ws.open_in_editor(cart)
    ws.text.set_menu_view("cards")
    from runtime import editor_settings
    page = editor_settings.SettingsPage(ws, None, (None, None))
    assert page._fields() == []
    assert ws.editor_app.missing_note() == ui_draw.s("editor.no_assets")
    ws.open_in_editor(_cart(ws, "Wasm Bits"))
    assert ws.editor_app.missing_note() == ui_draw.s("editor.no_scenes")


@pytest.mark.parametrize("title", ["Native Bits", "Wasm Bits"])
def test_a_compiled_carts_code_tab_reads_and_never_writes(tmp_path, title):
    ws = host_app.build_workstation(_store(tmp_path, _native, _wasm))
    cart = _cart(ws, title)
    ws.open_in_editor(cart)
    ws.text.set_menu_view("code")
    app = ws.editor_app
    assert app.tab == "code" and app.code_read_only()
    ed = ws.editor
    assert ed.read_only and ed.text() == "int frame;\n"
    path = os.path.join(cart["path"], "src", ws.text.code_file_name()[4:])
    for k in (ord("x"), 13, 8, 127, 9):
        assert ed.key(k) is False
    ed.select_all()
    assert ed.cut() is False and ed.paste() is False and ed.delete_selection() is False
    assert ed.copy() and ed.text() == "int frame;\n"
    ed.insert_text("more")
    assert ed.text() == "int frame;\n" and not ed.dirty
    assert ws.saves.save_code() is True               # nothing to write
    with open(path) as f:
        assert f.read() == "int frame;\n"
    # a Lua cart's code is still its own
    ws2 = host_app.build_workstation(_store(tmp_path / "lua", _lua))
    ws2.open_in_editor(_cart(ws2, "Lua Bits"))
    ws2.text.set_menu_view("code")
    assert not ws2.editor.read_only and ws2.editor.key(ord("x"))


def test_the_read_only_code_tab_draws_its_banner_over_the_code(tmp_path):
    ws = host_app.build_workstation(_store(tmp_path, _native))
    ws.open_in_editor(_cart(ws, "Native Bits"))
    ws.text.set_menu_view("code")
    drv = host_app.ConsoleDriver(ws)
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    cl = ws.code_layer
    assert cl.s.read_only and cl.s.ro_words
    rect, _top, _cell, lh, _x0 = cl.view()
    top = cl.page.content[1]
    assert rect[1] > top                              # the code starts under the banner
    cv = ws.sys_canvas
    band = {bytes(cv._buf[2 * (y * cv.w + x):2 * (y * cv.w + x) + 2])
            for y in range(top, rect[1]) for x in range(rect[0], rect[0] + rect[2])}
    assert len(band) > 1                              # ...which has its words
    assert ws.editor.ROWS == rect[3] // lh


def test_the_desk_offers_the_workspaces_and_panes_the_cart_has(tmp_path):
    ws = host_app.build_workstation(_store(tmp_path, _native, _wasm, _lua),
                                    sys_size=(1024, 600), panel_diagonal_in=7.0,
                                    font_scale=1, windowed=True)
    ws.home.open_desk()
    drv = host_app.ConsoleDriver(ws)
    want = {"Native Bits": ["settings", "code"],
            "Wasm Bits": ["settings", "code", "art", "sound"],
            "Lua Bits": ["settings", "code", "art", "world", "sound"]}
    for title, keys in want.items():
        ws.open_in_editor(_cart(ws, title))
        for _ in range(2):
            drv.frame(1 / 30)
        d = ws.editor_desk
        assert d.keys() == keys, title
        shown = {c for pane, _r, _p in d.panes() for c in pane}
        for k in keys:
            d.switch(k)
            drv.frame(1 / 30)
            shown |= {c for pane, _r, _p in d.panes() for c in pane}
        lacks = set(d.lacks())
        assert not shown & lacks, (title, shown & lacks)
        d.step(1)
        assert d.key in keys
    assert "scene" in shown and lacks == {"blocks"}

"""Intents and MIME types (runtime/system/intents.py, docs/os_2026-11/
between_apps.md section 1): the one MIME table, the `handles` manifest field,
`moybyte.open_with` and `moybyte.share`, the "open with" menu the console
asks with, the last choice reused and changed from it, and the grant the
receiver holds for its run. Driven through the real input feeds: keys, a
pad and touch."""

import json
import os

import pytest

import moybyte
from runtime import host_app, intents as I, ui_draw
from ws_helpers import build_ws, boot_on_fixture

_DT = 1 / 30

JOT = """
got = []
def moy_open(h):
    got.append(h)
def _draw():
    cls(1)
"""


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    boot_on_fixture(monkeypatch)
    I.INTENTS.reset()
    yield
    I.INTENTS.reset()
    ui_draw.input_kind("keys")


def _jot(carts_dir, handles, title="Jot"):
    """A user app cart whose manifest says it opens `handles`."""
    host_app.moy_carts.ensure_dirs(carts_dir)
    cart = host_app.moy_carts.create(title, carts_dir, src=JOT, type="app")
    path = os.path.join(cart["path"], "manifest.json")
    with open(path) as f:
        man = json.load(f)
    man["moybyte"]["handles"] = list(handles)
    with open(path, "w") as f:
        json.dump(man, f)


def _ws(tmp_path, handles=None):
    if handles is not None:
        _jot(str(tmp_path / "carts"), handles)
    ws = build_ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    _frames(drv)
    ws.carts_store.save_file("docs", "todo", "# milk\n", ws.carts_root)
    return ws, drv


def _frames(drv, n=3):
    for _ in range(n):
        drv.frame(_DT)


def _press(drv, *names):
    for n in names:
        drv.press(n)
        _frames(drv, 1)
    _frames(drv, 2)


def _key(ws, title):
    return ws.app_cart_id(next(c for c in ws.carts.all if c.get("title") == title))


def _menu(ws, drv):
    _frames(drv)
    c = ws.confirm
    assert c.open and c.title == "Open with"
    return c


def _jot_ns(ws):
    assert ws.cart is not None and ws.cart.get("title") == "Jot"
    return ws.player.ns


# -- the table ------------------------------------------------------------------


def test_the_table_types_each_item():
    assert I.mime_of("Documents/todo.md") == "text/markdown"
    assert I.mime_of("Documents/todo.txt") == "text/plain"
    assert I.mime_of("Documents/data.json") == "application/json"
    assert I.mime_of("Documents/hi.py") == "text/x-python"
    assert I.mime_of("Documents/hi.lua") == "text/x-lua"
    assert I.mime_of("Pictures/cat.moyimg") == "image/x-moyimg"
    assert I.mime_of("Music/tune.moysong") == "audio/x-moysong"
    assert I.mime_of("https://moybyte.org/a") == "x-scheme-handler/https"
    assert I.mime_of("Documents/Stories/tale") == I.OCTET
    assert I.matches("image/*", "image/x-moyimg") and I.matches("text/plain", "text/plain")
    assert not I.matches("image/*", "text/plain") and not I.matches("text", "text/plain")


def test_an_item_is_its_path_in_the_shared_folders():
    """A user file is named as USB and the Files app show it, and reads back
    as the layer's (kind, name): a note's `.md` dropped, a vault file's whole
    name kept, a subfolder's kind its own."""
    for item, kn in (("Documents/todo.md", ("docs", "todo")),
                     ("Documents/list.txt", ("docs", "list.txt")),
                     ("Pictures/cat.moyimg", ("drawings", "cat")),
                     ("Pictures/Sprites/hero.moygfx", ("sprites", "hero")),
                     ("Music/tune.moysong", ("music", "tune")),
                     ("Recordings/take_1", ("recordings", "take_1")),
                     ("Documents/Stories/tale", ("stories", "tale"))):
        assert I.split(item) == kn, item
        assert I.item_of(*kn) == item
    for bad in ("nowhere/x", "docs/todo", "Documents/todo", "Pictures/cat.md",
                "Pictures/Sprites", "Documents/", "/x.md", "https://a/b.md", 3):
        assert I.split(bad) is None, bad
    assert I.item_of("selfies", "x") is None


def test_the_manifest_field_reaches_the_shelf(tmp_path):
    ws, _drv = _ws(tmp_path, ["text/markdown"])
    by = {c.get("title"): c for c in ws.carts.all}
    assert by["Jot"]["handles"] == ["text/markdown"]
    assert "text/markdown" in by["Notes"]["handles"]
    assert by["Paint"]["handles"] == ["image/x-moyimg"]
    assert by["Star Catcher"]["handles"] == []


# -- one match ------------------------------------------------------------------


def test_one_match_opens_without_asking(tmp_path):
    ws, drv = _ws(tmp_path)
    assert moybyte.open_with("Documents/todo.md")
    _frames(drv)
    assert not ws.confirm.open
    assert ws.cart.get("title") == "Notes"
    assert ws.system.get(I.KEY, None) is None, "no choice was made"


def test_a_drawing_opens_paint(tmp_path):
    ws, drv = _ws(tmp_path)
    assert moybyte.open_with("Pictures/cat.moyimg")
    _frames(drv)
    assert not ws.confirm.open and ws.wm.top_kind() == "artwork"


def test_what_no_app_opens_goes_nowhere(tmp_path):
    ws, drv = _ws(tmp_path)
    assert not moybyte.open_with("https://moybyte.org")
    assert not moybyte.open_with("stories/tale")
    assert not moybyte.open_with("nowhere")
    assert not moybyte.share("x", "text/x-unknown")
    _frames(drv)
    assert not ws.confirm.open


# -- several: the menu, the last choice, changing it ------------------------------


def test_several_ask_and_the_choice_is_reused(tmp_path):
    ws, drv = _ws(tmp_path, ["text/markdown"])
    assert moybyte.open_with("Documents/todo.md")
    c = _menu(ws, drv)
    assert c.rows == ["Jot", "Notes"] and c.icons == ["app", "notes"]
    assert c.line == "todo.md"
    assert c.focused == "Jot", "the first row takes the focus"
    _press(drv, "down", "a")
    assert not c.open and ws.cart.get("title") == "Notes"
    assert ws.system.get(I.KEY, None) == {"text/markdown": _key(ws, "Notes")}
    ws.go_home()
    _frames(drv)
    assert moybyte.open_with("Documents/todo.md")
    _frames(drv)
    assert not ws.confirm.open, "the last choice is reused, unasked"
    assert ws.cart.get("title") == "Notes"


def test_the_menu_changes_the_choice(tmp_path):
    ws, drv = _ws(tmp_path, ["text/markdown"])
    ws.system.set(I.KEY, {"text/markdown": _key(ws, "Notes")})
    assert moybyte.open_with("Documents/todo.md", True)
    c = _menu(ws, drv)
    assert c.rows == ["Notes", "Jot"], "the last choice first"
    assert c.focused == "Notes"
    _press(drv, "down", "a")
    assert ws.system.get(I.KEY, None) == {"text/markdown": _key(ws, "Jot")}
    got = _jot_ns(ws)["got"]
    assert len(got) == 1
    ws.go_home()
    _frames(drv)
    assert moybyte.open_with("Documents/todo.md")
    _frames(drv)
    assert not ws.confirm.open and len(_jot_ns(ws)["got"]) == 1, "a fresh run, unasked"


def test_cancel_opens_nothing_and_keeps_the_choice(tmp_path):
    ws, drv = _ws(tmp_path, ["text/markdown"])
    for how in ("b", "home", "esc"):
        assert moybyte.open_with("Documents/todo.md")
        c = _menu(ws, drv)
        if how == "esc":
            drv.escape()
            _frames(drv, 1)
        else:
            _press(drv, how)
        assert not c.open and ws.cart is None, how
        assert ws.system.get(I.KEY, None) is None and I.INTENTS.asking is None


def test_the_pad_alone_picks(tmp_path):
    ws, drv = _ws(tmp_path, ["text/markdown"])
    ui_draw.input_kind("pad")
    assert moybyte.open_with("Documents/todo.md")
    _menu(ws, drv)
    _press(drv, "a")
    assert len(_jot_ns(ws)["got"]) == 1


def test_touch_alone_picks(tmp_path):
    ws, drv = _ws(tmp_path, ["text/markdown"])
    ui_draw.input_kind("touch")
    assert moybyte.open_with("Documents/todo.md")
    c = _menu(ws, drv)
    x, y, w, h = c._host.box("Notes")
    drv.click(x + w // 2, y + h // 2)
    _frames(drv, 2)
    assert not c.open and ws.cart.get("title") == "Notes"
    assert ws.system.get(I.KEY, None) == {"text/markdown": _key(ws, "Notes")}


# -- the grant ------------------------------------------------------------------


def test_the_receiver_holds_one_file_read_write_for_its_run(tmp_path):
    ws, drv = _ws(tmp_path, ["text/markdown"])
    ws.system.set(I.KEY, {"text/markdown": _key(ws, "Jot")})
    assert moybyte.open_with("Documents/todo.md")
    _frames(drv)
    h = _jot_ns(ws)["got"][0]
    assert (h.name, h.mime, h.mode, h.url) == ("todo.md", "text/markdown", "rw", None)
    assert h.read() == "# milk\n"
    assert h.write("# eggs\n")
    assert ws.carts_store.load_file("docs", "todo", ws.carts_root) == "# eggs\n"
    ws.exit()
    _frames(drv)
    assert h.read() is None and not h.write("x"), "the grant ended with the run"


def test_share_hands_its_data_read_only(tmp_path):
    ws, drv = _ws(tmp_path, ["text/*"])
    ws.system.set(I.KEY, {"text/x-greeting": _key(ws, "Jot")})
    assert moybyte.share("hello", "text/x-greeting")
    _frames(drv)
    h = _jot_ns(ws)["got"][0]
    assert (h.name, h.mime, h.mode) == (None, "text/x-greeting", "r")
    assert h.read() == "hello" and not h.write("bye")


def test_share_into_notes_keeps_a_new_note(tmp_path):
    ws, drv = _ws(tmp_path)
    assert moybyte.share("a shared line\n", "text/plain")
    _frames(drv)
    assert ws.cart.get("title") == "Notes"
    assert ws.carts_store.load_file("docs", "shared.txt", ws.carts_root) == "a shared line\n"


def test_a_url_goes_to_the_app_that_takes_its_scheme(tmp_path):
    ws, drv = _ws(tmp_path, ["x-scheme-handler/https"])
    assert moybyte.open_with("https://moybyte.org/a")
    _frames(drv)
    h = _jot_ns(ws)["got"][0]
    assert (h.url, h.mode, h.read()) == ("https://moybyte.org/a", "r", None)


# -- a Lua service's face -----------------------------------------------------------


def test_a_lua_service_opens_and_shares_through_the_same_verbs(tmp_path):
    from runtime import scheduler as S
    ws, drv = _ws(tmp_path, ["text/*"])
    ws.system.set(I.KEY, {"text/markdown": _key(ws, "Jot"), "text/plain": _key(ws, "Jot")})
    src = ('ok = {}\n'
           'moybyte.on("achieve", function() moybyte.open_with("Documents/todo.md") end)\n'
           'moybyte.on("award", function() moybyte.share("hi", "text/plain") end)\n')
    try:
        pid = S.SCHED.start({"id": "t.lua", "title": "t.lua", "type": "service",
                             "runtime": "lua", "src": src, "permissions": ["front"],
                             "required": [], "path": "/carts/t.lua.moy"})
        assert pid is not None
        S.SCHED.send(pid, "achieve", ())
        S.SCHED.mail()
        _frames(drv)
        h = _jot_ns(ws)["got"][-1]
        assert (h.mime, h.read()) == ("text/markdown", "# milk\n")
        S.SCHED.send(pid, "award", ())
        S.SCHED.mail()
        _frames(drv)
        h = _jot_ns(ws)["got"][-1]
        assert (h.mime, h.mode, h.read()) == ("text/plain", "r", "hi")
    finally:
        S.SCHED.reset()


def test_a_second_item_while_the_menu_is_up_replaces_the_question(tmp_path):
    ws, drv = _ws(tmp_path, ["text/markdown", "text/plain"])
    ws.carts_store.save_file("docs", "list.txt", "eggs\n", ws.carts_root)
    assert moybyte.open_with("Documents/todo.md")
    _menu(ws, drv)
    assert moybyte.open_with("Documents/list.txt")
    c = _menu(ws, drv)
    assert c.line == "list.txt" and I.INTENTS.asking is not None
    _press(drv, "a")
    h = _jot_ns(ws)["got"][0]
    assert h.read() == "eggs\n"
    assert ws.system.get(I.KEY, None) == {"text/plain": _key(ws, "Jot")}


# -- the picker -----------------------------------------------------------------


def _picker(tmp_path, n=9):
    """Jot running, the user's drawings, one sprite sheet and a note."""
    ws, drv = _ws(tmp_path, [])
    root = ws.carts_root
    for i in range(n):
        ws.carts_store.save_file("drawings", "sky_%d" % i, "%d," % i, root)
    ws.carts_store.save_file("sprites", "hero", "0,", root)
    ws.open(next(c for c in ws.carts.all if c.get("title") == "Jot"))
    _frames(drv)
    return ws, drv, ws.player.pid


def _picker_up(ws, drv, title="Choose a picture"):
    _frames(drv)
    c = ws.confirm
    assert c.open and c.title == title and c.line == "For Jot"
    return c


def test_the_picker_lists_the_files_of_the_type(tmp_path):
    ws, drv, pid = _picker(tmp_path, 2)
    assert I.INTENTS.files_of("image/*") == ["Pictures/sky_0.moyimg", "Pictures/sky_1.moyimg",
                                             "Pictures/Sprites/hero.moygfx"]
    assert I.INTENTS.files_of("image/x-moygfx") == ["Pictures/Sprites/hero.moygfx"]
    assert I.INTENTS.files_of("text/markdown") == ["Documents/todo.md"]
    assert not I.INTENTS.pick(pid, "audio/*"), "no file has the type"
    assert not I.INTENTS.pick(pid, "image/*", "w") and not ws.confirm.open
    assert I.INTENTS.pick(pid, "text/*")
    c = _picker_up(ws, drv, "Choose a document")
    assert c.rows == ["todo"] and c.icons == ["notes"]


def test_keys_reach_a_row_past_the_card_and_hand_it_over(tmp_path):
    ws, drv, pid = _picker(tmp_path)
    assert I.INTENTS.pick(pid, "image/*", "rw")
    c = _picker_up(ws, drv)
    assert c.rows[-1] == "hero" and c.icons[-1] == "sprite"
    for _ in range(9):
        _press(drv, "down")
    assert c.focused == "hero"
    x, y, w, h = c._host.box("hero")
    cx, cy, cw, ch = c._host.box(c.title)
    assert (ws.sys_canvas.w, ws.sys_canvas.h) == (320, 240)
    assert cy <= y and y + h <= cy + ch, "the card's body scrolled the row into view"
    _press(drv, "a")
    assert not c.open
    h = _jot_ns(ws)["got"][-1]
    assert (h.name, h.mime, h.mode) == ("hero.moygfx", "image/x-moygfx", "rw")
    assert h.read() == "0,"
    assert h.write("1,") and ws.carts_store.load_file("sprites", "hero", ws.carts_root) == "1,"


def test_the_pad_alone_picks_a_file(tmp_path):
    ws, drv, pid = _picker(tmp_path, 2)
    ui_draw.input_kind("pad")
    assert I.INTENTS.pick(pid, "image/x-moyimg")
    _picker_up(ws, drv)
    _press(drv, "down", "a")
    h = _jot_ns(ws)["got"][-1]
    assert (h.name, h.mode, h.read()) == ("sky_1.moyimg", "r", "1,")
    assert not h.write("x"), "a read grant writes nothing"


def test_touch_alone_picks_a_file(tmp_path):
    ws, drv, pid = _picker(tmp_path, 2)
    ui_draw.input_kind("touch")
    assert I.INTENTS.pick(pid, "image/*")
    c = _picker_up(ws, drv)
    x, y, w, h = c._host.box("sky_1")
    drv.click(x + w // 2, y + h // 2)
    _frames(drv, 2)
    assert _jot_ns(ws)["got"][-1].name == "sky_1.moyimg"


def test_a_cancelled_picker_hands_none_and_the_grant_ends_with_the_run(tmp_path):
    ws, drv, pid = _picker(tmp_path, 1)
    assert I.INTENTS.pick(pid, "image/*")
    _picker_up(ws, drv)
    _press(drv, "b")
    assert _jot_ns(ws)["got"] == [None] and I.INTENTS.picking is None
    assert I.INTENTS.pick(pid, "image/*")
    _picker_up(ws, drv)
    _press(drv, "a")
    h = _jot_ns(ws)["got"][-1]
    assert h.read() == "0,"
    ws.go_home()
    _frames(drv)
    assert h.read() is None and not h.live


# -- Files' "Open with" ---------------------------------------------------------


def test_files_open_with_asks_and_the_pick_becomes_the_choice(tmp_path):
    ws, drv = _ws(tmp_path, ["text/markdown"])
    files = next(c for c in ws.carts.all if c.get("title") == "Files")
    ws.open(files)
    _frames(drv)
    app = ws.files_app
    from runtime import files_app
    app.open_place(files_app.DOCUMENTS, keep="todo")
    assert "WITH" in app._action_labels()
    app._act("WITH")
    c = _menu(ws, drv)
    assert c.line == "todo.md" and c.rows == ["Jot", "Notes"]
    _press(drv, "a")
    assert _jot_ns(ws)["got"][-1].name == "todo.md"
    assert ws.system.get(I.KEY, None) == {"text/markdown": _key(ws, "Jot")}


def test_files_open_with_says_so_where_no_app_opens_it(tmp_path):
    ws, drv = _ws(tmp_path, [])
    ws.carts_store.save_file("music", "tune", "{}", ws.carts_root)
    ws.open(next(c for c in ws.carts.all if c.get("title") == "Files"))
    _frames(drv)
    app = ws.files_app
    from runtime import files_app
    app.open_place(files_app.MUSIC, keep="tune")
    app._act("WITH")
    _frames(drv)
    assert not ws.confirm.open and app.status == "No app opens it"

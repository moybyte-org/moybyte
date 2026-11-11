"""Taking the screen (runtime/system/front.py, docs/os_2026-11/processes.md
§2; owner, 2026-11-08): every way a process brings an app to the front --
`moybyte.open_with` and `share`, `moybyte.pick`, the nav role's `open_app`
-- passes one gate. The run on screen and a process holding `front` go
through; one in the background without it posts a notification whose act
does what it asked; the shell is never gated."""

import json
import os

import pytest

import moybyte
from runtime import front as F
from runtime import intents as I
from runtime import notifications as N
from runtime import scheduler as S
from ws_helpers import build_ws, boot_on_fixture, make_drv, no_unlock_banner, open_cart

_DT = 1 / 30

ASKER = """
import moybyte
got = []
said = []
def ask(item):
    said.append(moybyte.open_with(item))
def give(data, mime):
    said.append(moybyte.share(data, mime))
def want(mime):
    said.append(moybyte.pick(mime))
def go(app_id):
    said.append(open_app(app_id))
def moy_open(h):
    got.append(h)
def _draw():
    cls(3)
"""


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    boot_on_fixture(monkeypatch)
    I.INTENTS.reset()
    N.NOTES.reset()
    yield
    I.INTENTS.reset()
    N.NOTES.reset()
    S.SCHED.reset()


def _asker(carts_dir, perms):
    from runtime import host_app
    host_app.moy_carts.ensure_dirs(carts_dir)
    cart = host_app.moy_carts.create("Asker", carts_dir, src=ASKER, type="app")
    path = os.path.join(cart["path"], "manifest.json")
    with open(path) as f:
        man = json.load(f)
    man["moybyte"]["permissions"] = ["launch"] + list(perms)
    with open(path, "w") as f:
        json.dump(man, f)


def _ws(tmp_path, perms=()):
    _asker(str(tmp_path / "carts"), perms)
    ws = no_unlock_banner(build_ws(tmp_path))
    drv = make_drv(ws)
    _frames(drv)
    ws.carts_store.save_file("docs", "todo", "# milk\n", ws.carts_root)
    return ws, drv


def _frames(drv, n=3):
    for _ in range(n):
        drv.frame(_DT)


def _as(pid, fn, *a):
    """`fn` called as process `pid`, as its frame or callback runs it."""
    import moy_log
    was = moy_log.current()
    moy_log.current(pid)
    try:
        return fn(*a)
    finally:
        moy_log.current(was)


def _on_screen(ws, drv):
    open_cart(ws, "Asker")
    _frames(drv)
    asker = ws.run.player
    assert F.FRONT.on_screen() == asker.pid
    return asker


def _behind(ws, drv):
    """Asker running, minimised on a handheld; Star Catcher in front."""
    asker = _on_screen(ws, drv)
    assert ws.procs.detach()
    ws.go_home()
    open_cart(ws, "Star Catcher")
    _frames(drv)
    assert [p.title() for p in ws.procs.list] == ["Asker"]
    assert F.FRONT.on_screen() != asker.pid
    return asker


def _tap_banner(ws, drv):
    drv.frame(_DT)
    x, y, w, h = ws._note_rect
    assert ws._note_pointer(x + w // 2, y + h // 2, True)
    _frames(drv)


# -- the row ----------------------------------------------------------------------


def test_front_is_a_row_of_the_table_every_tier_may_hold():
    from runtime import app_perms as P
    r = P.row("front")
    assert r is not None and r[P.GRANTS] == "api:front" and r[P.TIERS] == P.ALL
    assert P.line("front") == "Take over the screen from the background"


# -- open_with ----------------------------------------------------------------------


def test_the_run_on_screen_opens_at_once(tmp_path):
    ws, drv = _ws(tmp_path)
    asker = _on_screen(ws, drv)
    said = asker.ns["said"]
    asker.ns["ask"]("Documents/todo.md")
    _frames(drv)
    assert said == [True]
    assert ws.cart["title"] == "Notes" and N.NOTES.list == []


def test_a_run_behind_with_front_opens_at_once(tmp_path):
    ws, drv = _ws(tmp_path, ["front"])
    asker = _behind(ws, drv)
    _as(asker.pid, asker.ns["ask"], "Documents/todo.md")
    _frames(drv)
    assert asker.ns["said"] == [True]
    assert ws.cart["title"] == "Notes" and N.NOTES.list == []


def test_a_run_behind_without_front_asks_by_a_notification(tmp_path):
    ws, drv = _ws(tmp_path)
    asker = _behind(ws, drv)
    _as(asker.pid, asker.ns["ask"], "Documents/todo.md")
    _frames(drv)
    assert asker.ns["said"] == [True], "the request waits"
    assert ws.cart["title"] == "Star Catcher" and not ws.run.player.paused
    n = N.NOTES.up()
    assert n is not None and (n.app, n.summary) == ("Asker", "Wants to open todo.md in Notes")
    assert n.pid == asker.pid
    _tap_banner(ws, drv)
    assert ws.cart["title"] == "Notes"
    assert N.NOTES.list == [] and N.NOTES.up() is None


def test_a_later_request_replaces_the_one_nobody_opened(tmp_path):
    ws, drv = _ws(tmp_path)
    asker = _behind(ws, drv)
    _as(asker.pid, asker.ns["ask"], "Documents/todo.md")
    _as(asker.pid, asker.ns["give"], "a line\n", "text/plain")
    assert len(N.NOTES.list) == 1
    n = N.NOTES.list[0]
    assert n.summary == "Wants to share with Notes"
    assert N.NOTES.act(n.id)
    _frames(drv)
    assert ws.cart["title"] == "Notes"
    assert ws.carts_store.load_file("docs", "shared.txt", ws.carts_root) == "a line\n"


def test_the_shell_is_never_gated(tmp_path):
    ws, drv = _ws(tmp_path)
    _behind(ws, drv)
    assert moybyte.open_with("Documents/todo.md")
    _frames(drv)
    assert ws.cart["title"] == "Notes" and N.NOTES.list == []


# -- services: Python and Lua -------------------------------------------------------


def _service(perms):
    src = ("import moybyte\n"
           "def ask(item):\n    return moybyte.open_with(item)\n"
           "moybyte.every(60000, lambda: None)\n")
    pid = S.SCHED.start({"id": "t.svc", "title": "Svc", "permissions": list(perms),
                         "src": src})
    assert pid is not None
    return pid


@pytest.mark.parametrize("perms", [(), ("front",)])
def test_a_python_service_takes_the_front_only_with_the_permission(tmp_path, perms):
    ws, drv = _ws(tmp_path)
    open_cart(ws, "Star Catcher")
    _frames(drv)
    pid = _service(perms)
    assert _as(pid, S.SCHED.procs[pid].ns["ask"], "Documents/todo.md")
    _frames(drv)
    if perms:
        assert ws.cart["title"] == "Notes" and N.NOTES.list == []
        return
    assert ws.cart["title"] == "Star Catcher"
    n = N.NOTES.list[0]
    assert (n.app, n.summary) == ("Svc", "Wants to open todo.md in Notes")
    assert N.NOTES.act(n.id)
    _frames(drv)
    assert ws.cart["title"] == "Notes"


@pytest.mark.parametrize("perms", [[], ["front"]])
def test_a_lua_service_takes_the_front_only_with_the_permission(tmp_path, perms):
    ws, drv = _ws(tmp_path)
    open_cart(ws, "Star Catcher")
    _frames(drv)
    src = 'moybyte.on("achieve", function() moybyte.open_with("Documents/todo.md") end)\n'
    pid = S.SCHED.start({"id": "t.lua", "title": "Luasvc", "type": "service",
                         "runtime": "lua", "src": src, "permissions": perms,
                         "required": [], "path": "/carts/t.lua.moy"})
    assert pid is not None
    S.SCHED.send(pid, "achieve", ())
    S.SCHED.mail()
    _frames(drv)
    if perms:
        assert ws.cart["title"] == "Notes" and N.NOTES.list == []
        return
    assert ws.cart["title"] == "Star Catcher"
    n = N.NOTES.up()
    assert n is not None and (n.app, n.summary) == ("Luasvc", "Wants to open todo.md in Notes")
    _tap_banner(ws, drv)
    assert ws.cart["title"] == "Notes"


# -- the nav role: open_app -----------------------------------------------------------


def test_open_app_from_the_run_on_screen_opens_it(tmp_path):
    ws, drv = _ws(tmp_path)
    asker = _on_screen(ws, drv)
    said = asker.ns["said"]
    asker.ns["go"]("artwork")
    _frames(drv)
    assert said == [True] and ws.wm.top_kind() == "artwork"
    assert N.NOTES.list == []


def test_open_app_from_behind_without_front_asks_first(tmp_path):
    ws, drv = _ws(tmp_path)
    asker = _behind(ws, drv)
    _as(asker.pid, asker.ns["go"], "artwork")
    _frames(drv)
    assert asker.ns["said"] == [True]
    assert ws.cart["title"] == "Star Catcher" and ws.wm.top_kind() != "artwork"
    n = N.NOTES.up()
    assert n.app == "Asker" and n.summary.startswith("Wants to open ")
    _tap_banner(ws, drv)
    assert ws.wm.top_kind() == "artwork"


def test_open_app_from_behind_with_front_opens_it(tmp_path):
    ws, drv = _ws(tmp_path, ["front"])
    asker = _behind(ws, drv)
    _as(asker.pid, asker.ns["go"], "artwork")
    _frames(drv)
    assert ws.wm.top_kind() == "artwork" and N.NOTES.list == []


# -- the picker ---------------------------------------------------------------------


def test_a_pick_from_behind_brings_its_app_forward_with_the_picker(tmp_path):
    ws, drv = _ws(tmp_path)
    asker = _behind(ws, drv)
    _as(asker.pid, asker.ns["want"], "text/*")
    _frames(drv)
    assert asker.ns["said"] == [True] and not ws.confirm.open
    n = N.NOTES.up()
    assert (n.app, n.summary) == ("Asker", "Wants you to pick a file")
    _tap_banner(ws, drv)
    assert ws.run.player is asker
    c = ws.confirm
    assert c.open and c.title == "Choose a document" and c.line == "For Asker"
    drv.press("a")
    _frames(drv)
    h = asker.ns["got"][-1]
    assert h is not None and h.read() == "# milk\n"


# -- who asks the nav role ------------------------------------------------------------


def test_a_shell_apps_grant_is_the_shells_and_a_grant_no_run_holds_asks(tmp_path):
    import moy_app
    ws, drv = _ws(tmp_path)
    open_cart(ws, "Star Catcher")
    _frames(drv)
    g = ws.app_abi.grant("local.stray", ("nav",), run=True)
    try:
        assert ws.procs.pid_of(g) is None
        assert moy_app.Nav(ws.app_abi, g).open_app("artwork")
        _frames(drv)
        assert ws.wm.top_kind() != "artwork" and N.NOTES.list[0].app == "App"
    finally:
        ws.app_abi.end(g)
    nav = ws.app_context("probe", ("nav",)).nav
    assert ws.procs.pid_of(nav._g) == S.SHELL
    assert nav.open_app("artwork")
    _frames(drv)
    assert ws.wm.top_kind() == "artwork"

"""Notifications (runtime/system/notifications.py, docs/os_2026-11/processes.md
§7): `moybyte.notify` under the `notifications` permission, the banner over
whatever runs, acting on one (its app to the front: the game throttled on the
desk, minimised on a handheld) and dismissing it (the game plays on), the
list in the task manager, and the kernel's copy over a run it drives."""

import ctypes
import json
import os

import pytest

import moybyte
from runtime import moy_play
from runtime import notifications as N
from runtime import scheduler as S
from runtime import tasks_ui
from ws_helpers import build_desktop_ws, build_ws, boot_on_fixture, make_drv, open_cart

_DT = 1 / 30

POSTER = """
posted = []
acted = []
def post(summary, body="", actions=(), urgency=1):
    posted.append(moybyte.notify(summary, body, "heart", actions, urgency))
def moy_action(nid, key):
    acted.append((nid, key))
def _update(dt=None):
    pass
def _draw():
    cls(2)
"""


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    boot_on_fixture(monkeypatch)
    N.NOTES.reset()
    yield
    N.NOTES.reset()
    S.SCHED.reset()


def _poster_cart(carts_dir, title="Mesh", perms=("notifications",), type="app"):
    """A cart whose manifest asks for `perms`: an app, or (on the desk,
    where the running set holds games) a game."""
    from runtime import host_app
    host_app.moy_carts.ensure_dirs(carts_dir)
    cart = host_app.moy_carts.create(title, carts_dir, src="import moybyte\n" + POSTER,
                                     type=type)
    path = os.path.join(cart["path"], "manifest.json")
    with open(path) as f:
        man = json.load(f)
    man["moybyte"]["permissions"] = list(perms)
    with open(path, "w") as f:
        json.dump(man, f)


def _ws(tmp_path, perms=("notifications",)):
    _poster_cart(str(tmp_path / "carts"), perms=perms)
    ws = build_ws(tmp_path)
    drv = make_drv(ws)
    for _ in range(3):
        drv.frame(_DT)
    return ws, drv


def _service(perms=("notifications",)):
    src = ("import moybyte\nheard = []\n"
           "def moy_action(nid, key):\n    heard.append((nid, key))\n"
           "moybyte.every(60000, lambda: None)\n")
    pid = S.SCHED.start({"id": "t.mesh", "title": "Mesh", "permissions": list(perms),
                         "src": src})
    assert pid is not None
    return pid


def _as(pid, fn, *a, **k):
    """`fn` called as process `pid`, as its frame or callback runs it."""
    import moy_log
    was = moy_log.current()
    moy_log.current(pid)
    try:
        return fn(*a, **k)
    finally:
        moy_log.current(was)


def _post_as(pid, *a, **k):
    return _as(pid, moybyte.notify, *a, **k)


# -- posting --------------------------------------------------------------------


def test_a_service_posts_and_the_banner_goes_up_over_the_game(tmp_path):
    ws, drv = _ws(tmp_path)
    open_cart(ws, "Star Catcher")
    drv.frame(_DT)
    pid = _service()
    nid = _post_as(pid, "Ana", "are you coming?", None, ("reply", "Reply"))
    assert nid is not None
    n = N.NOTES.up()
    assert n is not None and n.id == nid and n.app == "Mesh"
    assert n.summary == "Ana" and n.body == "are you coming?" and n.icon == N.ICON
    assert n.actions == (("reply", "Reply"),)
    drv.frame(_DT)
    assert ws._note_layer in ws.wm._cache_overlay
    r = ws._note_rect
    assert r is not None and r[1] < ws.sys_canvas.h // 3, "drawn at the top"
    assert not ws.player.paused and ws.screen == "desktop"


def test_without_the_permission_nothing_is_posted(tmp_path):
    ws, drv = _ws(tmp_path)
    pid = _service(perms=())
    assert _post_as(pid, "Ana") is None
    assert N.NOTES.list == [] and N.NOTES.up() is None


def test_low_urgency_goes_to_the_list_alone_and_replaces_id_updates_in_place(tmp_path):
    ws, drv = _ws(tmp_path)
    pid = _service()
    nid = _post_as(pid, "3 messages", urgency=N.LOW)
    assert N.NOTES.up() is None and [n.id for n in N.NOTES.list] == [nid]
    again = _post_as(pid, "4 messages", replaces_id=nid)
    assert again == nid and len(N.NOTES.list) == 1
    assert N.NOTES.list[0].summary == "4 messages" and N.NOTES.up().id == nid


def test_the_banner_goes_by_itself_and_its_notification_stays_listed(tmp_path,
                                                                    monkeypatch):
    ws, drv = _ws(tmp_path)
    pid = _service()
    now = [100000]
    monkeypatch.setattr(N, "_ticks_ms", lambda: now[0])
    nid = _post_as(pid, "Ana")
    assert N.NOTES.up() is not None
    now[0] += N.BANNER_MS
    assert N.NOTES.up() is None
    assert [n.id for n in N.NOTES.list] == [nid]


def test_the_list_keeps_the_newest(tmp_path):
    ws, drv = _ws(tmp_path)
    pid = _service()
    ids = [_post_as(pid, "m%d" % i, urgency=N.LOW) for i in range(N.KEEP + 3)]
    assert [n.id for n in N.NOTES.list] == ids[::-1][:N.KEEP]


# -- dismissing -----------------------------------------------------------------


def test_dismissing_keeps_the_game_playing(tmp_path):
    ws, drv = _ws(tmp_path)
    open_cart(ws, "Star Catcher")
    drv.frame(_DT)
    pl = ws.player
    pid = _service()
    nid = _post_as(pid, "Ana")
    drv.frame(_DT)
    assert N.NOTES.dismiss()
    for _ in range(3):
        drv.frame(_DT)
    assert N.NOTES.up() is None and [n.id for n in N.NOTES.list] == [nid]
    assert ws.player is pl and ws.screen == "desktop" and not pl.paused
    assert ws.procs.list == []
    assert ws._note_layer not in ws.wm._cache_overlay


def test_a_drag_up_on_the_banner_dismisses_it(tmp_path):
    ws, drv = _ws(tmp_path)
    open_cart(ws, "Star Catcher")
    drv.frame(_DT)
    _post_as(_service(), "Ana")
    drv.frame(_DT)
    x, y, w, h = ws._note_rect
    ws.pointer.down = True
    assert ws._note_pointer(x + w // 2, y + h - 2, False)
    assert ws._note_pointer(x + w // 2, y + 2, False)
    ws.pointer.down = False
    assert ws._note_pointer(x + w // 2, y - 20, True)
    assert N.NOTES.up() is None and len(N.NOTES.list) == 1
    assert ws.screen == "desktop" and not ws.player.paused


# -- acting -----------------------------------------------------------------------


def _mesh_minimised(ws, drv):
    """The Mesh app running, minimised on a handheld; Star Catcher in front."""
    open_cart(ws, "Mesh")
    drv.frame(_DT)
    mesh = ws.player
    assert ws.procs.detach()
    ws.go_home()
    open_cart(ws, "Star Catcher")
    for _ in range(3):
        drv.frame(_DT)
    assert [p.title() for p in ws.procs.list] == ["Mesh"]
    return mesh


def test_acting_on_the_handheld_minimises_the_game_and_focuses_the_app(tmp_path):
    ws, drv = _ws(tmp_path)
    mesh = _mesh_minimised(ws, drv)
    game = ws.player
    _as(mesh.pid, mesh.ns["post"], "Ana", "are you coming?")
    nid = mesh.ns["posted"][-1]
    assert nid is not None and N.NOTES.up().app == "Mesh"
    drv.frame(_DT)
    x, y, w, h = ws._note_rect
    assert ws._note_pointer(x + w // 2, y + h // 2, True)        # a tap on it
    drv.frame(_DT)
    assert ws.player is mesh and ws.cart["title"] == "Mesh"
    assert mesh.ns["acted"] == [(nid, "default")]
    assert [p.title() for p in ws.procs.list] == ["Star Catcher"]
    p = ws.procs.list[0]
    assert p.player is game and p.state == "minimised" and game.paused
    assert N.NOTES.list == [] and N.NOTES.up() is None


def test_acting_on_the_desk_throttles_the_game(tmp_path):
    carts = str(tmp_path / "carts")
    _poster_cart(carts, type="game")
    ws = build_desktop_ws(tmp_path)
    drv = make_drv(ws)
    ws.open_desk()
    drv.frame(_DT)
    for t in ("Mesh", "Star Catcher"):
        open_cart(ws, t)
        drv.frame(_DT)
        win = ws.wm._wins["desktop"]
        if win.saved is not None:
            ws.wm._toggle_max(win)
        drv.frame(_DT)
    assert ws.cart["title"] == "Star Catcher"
    mesh = ws.procs.list[0].player
    game = ws.player
    _as(mesh.pid, mesh.ns["post"], "Ana")
    nid = mesh.ns["posted"][-1]
    assert N.NOTES.act(nid)
    drv.frame(_DT)
    assert ws.player is mesh and mesh.ns["acted"] == [(nid, "default")]
    p = ws.procs.list[0]
    assert p.player is game and p.state == "throttled" and not game.paused


def test_a_services_notification_is_acted_on_in_the_service(tmp_path):
    ws, drv = _ws(tmp_path)
    open_cart(ws, "Star Catcher")
    drv.frame(_DT)
    pid = _service()
    nid = _post_as(pid, "Ana", actions=("default", "Open", "reply", "Reply"))
    assert N.NOTES.act(nid, "reply")
    assert S.SCHED.procs[pid].ns["heard"] == [(nid, "reply")]
    assert ws.screen == "desktop" and not ws.player.paused


# -- the task manager ---------------------------------------------------------------


def test_the_task_manager_lists_them_and_opens_one(tmp_path):
    ws, drv = _ws(tmp_path)
    mesh = _mesh_minimised(ws, drv)
    _as(mesh.pid, mesh.ns["post"], "Ana", "are you coming?",
        ("default", "Open", "reply", "Reply"))
    nid = mesh.ns["posted"][-1]
    assert N.NOTES.up() is not None
    ws.open_tasks()                          # the console's key held: the banner goes
    drv.frame(_DT)
    layer = ws.tasks_layer
    assert ws.screen == "tasks" and N.NOTES.up() is None
    assert layer.tab == tasks_ui.NOTES_TAB and layer.notes[layer.note_cur].id == nid
    n = layer.notes[layer.note_cur]
    assert tasks_ui.note_verbs(n) == ["Open", "Reply", "Clear"]
    layer._do("Reply", n)
    drv.frame(_DT)
    assert ws.screen == "desktop" and ws.player is mesh
    assert mesh.ns["acted"] == [(nid, "reply")]
    assert N.NOTES.list == []


def test_clear_takes_one_from_the_list(tmp_path):
    ws, drv = _ws(tmp_path)
    pid = _service()
    a = _post_as(pid, "one", urgency=N.LOW)
    b = _post_as(pid, "two", urgency=N.LOW)
    ws.open_tasks()
    layer = ws.tasks_layer
    layer.tab = tasks_ui.NOTES_TAB
    drv.frame(_DT)
    assert [n.id for n in layer.notes] == [b, a]
    layer._do("Clear", layer.notes[0])
    drv.frame(_DT)
    assert [n.id for n in layer.notes] == [a]


# -- the kernel's copy ---------------------------------------------------------------


def test_the_kernel_draws_the_banner_over_a_run_it_drives(tmp_path):
    ws, drv = _ws(tmp_path)
    cv = ws.canvas
    w, h = cv.w, cv.h
    pal = [cv._wire[cv._pal_map[i]] for i in range(64)]
    now = 2000000
    moy_play.chrome_notice("", "", True, now)
    moy_play.chrome_toast_arm("", "", "", False, now)
    moy_play.chrome_note("Mesh", "Ana", "heart", now + 5000)
    raw = (ctypes.c_uint16 * (w * h))()
    assert moy_play.chrome_overlay_draw(raw, w, h, pal, now)
    top = [i for i in range(w * h) if raw[i]]
    assert top and max(top) // w < 70
    moy_play.chrome_note("", "", "", 0)
    raw = (ctypes.c_uint16 * (w * h))()
    assert not moy_play.chrome_overlay_draw(raw, w, h, pal, now)


def _key(drv, name):
    """One press of `name`, and its key-up frame."""
    drv.press(name)
    drv.frame(_DT)
    drv.frame(_DT)


def test_the_keys_start_on_the_list(tmp_path):
    ws, drv = _ws(tmp_path)
    pid = _service()
    a = _post_as(pid, "one", urgency=N.LOW)
    b = _post_as(pid, "two")
    ws.open_tasks()
    drv.frame(_DT)
    layer = ws.tasks_layer
    assert layer.tab == tasks_ui.NOTES_TAB and layer.notes[layer.note_cur].id == b
    _key(drv, "down")
    assert layer.notes[layer.note_cur].id == a

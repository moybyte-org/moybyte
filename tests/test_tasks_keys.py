"""The task manager by its keys (runtime/system/tasks_ui.py): what a person
does with it, never its internals. Focus opens on the list, the arrows move
the current row, Enter runs the row's first verb, the foot's verbs follow
the current row, the tab strip above the list changes the tab, and the
console's key held over a banner opens the Notifications tab on that
notification, where Enter opens it and the foot holds its named actions."""

import pytest

import moybyte
from runtime import notifications as N
from runtime import scheduler as S
from ws_helpers import boot_on_fixture, build_ws, make_drv, no_unlock_banner, open_cart

_DT = 1 / 30


@pytest.fixture(autouse=True)
def _night(monkeypatch):
    boot_on_fixture(monkeypatch)
    N.NOTES.reset()


def _hand(tmp_path):
    ws = no_unlock_banner(build_ws(tmp_path))
    drv = make_drv(ws)
    for _ in range(3):
        drv.frame(_DT)
    return ws, drv


def _two_games(ws, drv):
    """Star Catcher minimised, Brick Siege in front, the task manager open."""
    open_cart(ws, "Star Catcher")
    drv.frame(_DT)
    ws.system_key()
    ws.pause.choose(ws.pause.rows.index(ws.pause.MINIMISE))
    drv.frame(_DT)
    open_cart(ws, "Brick Siege")
    for _ in range(3):
        drv.frame(_DT)
    ws.open_tasks()
    drv.frame(_DT)
    drv.frame(_DT)
    assert ws.screen == "tasks"


_SHOT = [0]


def _keys(ws, drv, *names):
    import os
    for k in names:
        drv.press(k)
        drv.frame(_DT)
        drv.frame(_DT)
        if os.environ.get("TASKS_SHOTS"):
            import pngwrite
            from pixel_goldens import rgb565_rows
            cv = ws.glass_canvas
            _SHOT[0] += 1
            pngwrite.write_png("%s/%02d_%s.png" % (os.environ["TASKS_SHOTS"], _SHOT[0], k),
                               rgb565_rows(cv._buf, cv.w, cv.h), cv.w, cv.h)


def test_down_and_enter_switch_to_the_second_row(tmp_path):
    ws, drv = _hand(tmp_path)
    _two_games(ws, drv)
    _keys(ws, drv, "down", "a")
    assert ws.screen == "desktop" and ws.cart["title"] == "Star Catcher"
    assert [p.title() for p in ws.procs.list] == ["Brick Siege"]


def test_enter_on_the_first_row_switches_to_the_game_in_front(tmp_path):
    ws, drv = _hand(tmp_path)
    _two_games(ws, drv)
    _keys(ws, drv, "a")
    assert ws.screen == "desktop" and ws.cart["title"] == "Brick Siege"
    assert [p.title() for p in ws.procs.list] == ["Star Catcher"]


def test_the_foot_under_the_last_row_offers_its_verbs(tmp_path):
    ws, drv = _hand(tmp_path)
    _two_games(ws, drv)
    ach = ws.ach.pid
    _keys(ws, drv, "down", "down", "down", "a")     # past the list's last row: its first verb, Stop
    assert not [r for r in ws.procs.rows() if r[6] == ach and r[3] == "background"]
    assert ws.screen == "tasks" and [p.title() for p in ws.procs.list] == ["Star Catcher"]


def test_the_tab_strip_opens_the_notifications(tmp_path):
    ws, drv = _hand(tmp_path)
    pid = S.SCHED.start({"id": "t.mesh", "title": "Mesh", "permissions": ["notifications"],
                         "src": "import moybyte\nheard = []\n"
                                "def moy_action(nid, key):\n    heard.append((nid, key))\n"
                                "moybyte.every(60000, lambda: None)\n"})
    import moy_log
    was = moy_log.current()
    moy_log.current(pid)
    try:
        nid = moybyte.notify("Ana", "are you coming?", "heart", ("reply", "Reply"), N.LOW)
    finally:
        moy_log.current(was)
    _two_games(ws, drv)
    _keys(ws, drv, "up", "up", "right")      # the tab strip, then the next tab
    _keys(ws, drv, "down", "down", "a")      # the list, then the foot under it: Reply
    assert S.SCHED.procs[pid].ns["heard"] == [(nid, "reply")]
    assert N.NOTES.list == []


def test_the_console_key_held_opens_on_the_notification_and_its_foot_clears_it(tmp_path):
    ws, drv = _hand(tmp_path)
    pid = S.SCHED.start({"id": "t.mesh", "title": "Mesh", "permissions": ["notifications"],
                         "src": "import moybyte\nheard = []\n"
                                "def moy_action(nid, key):\n    heard.append((nid, key))\n"
                                "moybyte.every(60000, lambda: None)\n"})
    import moy_log
    was = moy_log.current()
    moy_log.current(pid)
    try:
        a = moybyte.notify("one", "", "", (), N.LOW)
        b = moybyte.notify("two", "", "", (), N.LOW)
    finally:
        moy_log.current(was)
    open_cart(ws, "Star Catcher")
    drv.frame(_DT)
    ws.open_tasks()
    drv.frame(_DT)
    ws.tasks_layer.open(N.NOTES.list[1])     # as the console's key held over its banner does
    drv.frame(_DT)
    drv.frame(_DT)
    _keys(ws, drv, "up")                     # the newer one
    _keys(ws, drv, "down")                   # the older one again
    _keys(ws, drv, "down", "a")              # the foot under it: Clear, the older one
    assert [n.id for n in N.NOTES.list] == [b] and a != b


def test_back_closes_it(tmp_path):
    ws, drv = _hand(tmp_path)
    _two_games(ws, drv)
    ws.input_policy.back()
    drv.frame(_DT)
    assert ws.screen == "desktop" and ws.cart["title"] == "Brick Siege"


def test_enter_on_a_notification_opens_it(tmp_path):
    ws, drv = _hand(tmp_path)
    pid = S.SCHED.start({"id": "t.mesh", "title": "Mesh", "permissions": ["notifications"],
                         "src": "import moybyte\nheard = []\n"
                                "def moy_action(nid, key):\n    heard.append((nid, key))\n"
                                "moybyte.every(60000, lambda: None)\n"})
    import moy_log
    was = moy_log.current()
    moy_log.current(pid)
    try:
        a = moybyte.notify("one", "", "", (), N.LOW)
        moybyte.notify("two", "", "", (), N.LOW)
    finally:
        moy_log.current(was)
    open_cart(ws, "Star Catcher")
    drv.frame(_DT)
    ws.open_tasks()
    drv.frame(_DT)
    ws.tasks_layer.open(N.NOTES.list[1])
    drv.frame(_DT)
    _keys(ws, drv, "a")
    assert S.SCHED.procs[pid].ns["heard"] == [(a, "default")]

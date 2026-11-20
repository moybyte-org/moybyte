"""The ported pages boot and draw on the boards' MicroPython.

The host suite runs `uitree` on CPython, which has what a board's MicroPython
lacks (frames, signatures, `re.fullmatch`, function attributes, a writable
instance `__dict__`, `collections.Counter`); uitree.py meets each in one guarded
place at its top. This boots the T-Deck's console as tests/test_frame_alloc.py
stages it, frozen, on the boards' model, opens every page ported to the layer,
drags the scrolling ones, and checks that each built and painted and that no
frame raised. The kernel's loop runs on past a frame that raises, so a raise
is caught here, around the frame, or it reads as a frame that drew nothing. A
layer change that reaches for CPython fails here, not on a board.
"""

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_frame_alloc as FA  # noqa: E402
import test_uitree_ports as PORTS  # noqa: E402


class Webhost:
    """The webhost the paired page and the web console read: serving on a
    fixed address, three browsers paired."""
    serving = False
    error = None

    def __init__(self, kids):
        self.kids = dict(kids)

    def start(self):
        self.serving = True

    def stop(self):
        self.serving = False

    def url(self):
        return "http://192.168.1.155"

    def paired_url(self):
        return "http://192.168.1.155/#k=0123456789abcdef"

    def browsers(self):
        return [(k,) + v for k, v in self.kids.items()]

    def forget(self, kid):
        if kid == "all":
            self.kids.clear()
        else:
            self.kids.pop(kid, None)


PAGES = r'''
@SOURCES@
BROWSERS = @BROWSERS@
LOG_RING = @LOG_RING@


def _steps(loop, n=12):
    for _ in range(n):
        loop.step()


def pages(loop):
    import moy_log
    import moy_loop
    import uitree_app
    ws = loop.ws
    painted = {}
    paint = uitree_app.Engine.paint

    def counted(self, *a):
        r = paint(self, *a)
        k = self.pages[0].__name__
        painted[k] = painted.get(k, 0) + 1
        return r
    uitree_app.Engine.paint = counted
    # the kernel's step keeps the loop running past a frame that raised:
    # every raise is kept here, its first traceback printed
    errors = []
    frame = ws.frame

    def checked(dt):
        try:
            frame(dt)
        except Exception as e:
            if not errors:
                sys.print_exception(e)
            errors.append(repr(e))
            raise
    ws.frame = checked
    _steps(loop, 30)
    pids = {"kernel": moy_log.KERNEL, "shell": moy_log.SHELL}
    for t, level, who, text in LOG_RING:
        if who not in pids:
            pids[who] = moy_log.spawn(who)
        moy_log.write(level, pids[who], text)
    ws.webhost = Webhost(BROWSERS)
    ws.updater = Updater(True)
    ws.c6_updater = C6Updater()
    shown = {}
    W, H = ws.sys_canvas.w, ws.sys_canvas.h
    ws.open_settings()
    _steps(loop)
    moy_loop.swipe(W // 2, H * 3 // 4, W // 2, H // 4, 8)
    _steps(loop, 20)
    shown["rows"] = ws.wm.top_kind()
    sl = ws.settings_layer
    ws.wifi = _Station()
    for p in ("wifi", "tz", "paired", "log", "permissions", "licences", "achievements"):
        sl.open_page(p)
        if p == "achievements":
            _steps(loop)
            sl.achievements.open_badge("system first_open")     # a badge's popover
            _steps(loop)
            shown["badge"] = sl.achievements.popover()[1]
            sl.achievements.back()
        if p == "licences":
            _steps(loop)
            sl.licences.read("MicroPython")     # its text: a prose view, dragged
            _steps(loop)
            shown["licence"] = sl.page_title()
        _steps(loop)
        moy_loop.swipe(W // 2, H * 3 // 4, W // 2, H // 4, 8)    # each page dragged
        _steps(loop)
        shown[p] = sl._stack[-1] if sl._stack else None
        sl.close_page()
        _steps(loop, 4)
    ws.toggle_popover("wifi")              # the panel in the bar's popover
    _steps(loop)
    shown["wifi_popover"] = ws.sysmenu.widget if ws.sysmenu.open else None
    ws.close_popover()
    _steps(loop, 4)
    for how in ("open_update", "open_update_online", "open_update_c6"):
        getattr(ws.update_ui, how)()
        _steps(loop)
        shown[how] = ws.wm.top_kind()
        ws.home.go_home()
        _steps(loop, 4)
    ws.updater = Updater(False)
    ws.update_ui.open_update()
    _steps(loop)
    shown["open_update_none"] = ws.wm.top_kind()
    ws.home.go_home()
    _steps(loop, 4)
    ws.web.toggle()
    _steps(loop)
    shown["web"] = ws.web.serving()
    ws.web.toggle()
    _steps(loop, 4)
    import notifications
    notifications.NOTES.post(moy_log.SHELL, "Ana", "We start at eight.", None,
                             ("default", "Open", "reply", "Reply"), notifications.LOW)
    ws.open_tasks()
    _steps(loop)
    shown["tasks"] = ws.tasks_layer.current()[0]
    ws.tasks_layer.open(notifications.NOTES.list[0])
    _steps(loop)
    shown["tasks_notes"] = ws.tasks_layer.current()[1].summary
    ws.close_tasks()
    # the Editor's Code tab: typed into, its find, Go to, completion and a
    # stopped line's card; then theme mode's Config, the same page's other
    for cart in ws.carts.all:
        ws.open_in_editor(cart)
        ws.text.set_menu_view("code")
        if ws.editor is not None:
            break
        ws.spine.exit()
    ed = ws.editor
    ed.set_text("\n".join("def step_%d(n):\n    return n + %d" % (i, i) for i in range(30)))
    _steps(loop)
    cl = ws.code_layer
    for c in b"st":
        ed.key(c)
        ws.mark_dirty()
        _steps(loop, 2)
    shown["code"] = ws.wm.top_kind()
    for name in ("find", "goto_open", "complete_open"):
        cl.run(name)
        _steps(loop)
        shown[name] = cl.page.s.finding or cl.page.s.over
        cl.run("find_close" if name == "find" else "overlay_close")
        _steps(loop, 4)
    ws.code_err_row = 3
    ws.code_err = "NameError: name 'speed' isn't defined"
    ws.crash_popup = ws.code_err
    ws.mark_dirty()
    _steps(loop)
    shown["stopped"] = cl.page.s.stopped
    ws.crash_popup = None
    # the Editor's head row, lent to the bar (editor_ui.py): the bar's zone
    # replays it each strip render; a tap on its Sprites tab goes through it
    bl = ws.bar_layer
    zone = bl.lent_zone("menu", bl.cell("menu", "zone")[0])
    cells = ws.editor_app.zone_cells(zone)
    shown["head"] = [c[0] for c in cells]
    r = [c[2] for c in cells if c[0] == "paint"][0]
    bl._zone_owner("menu").zone_tap(r[0] + r[2] // 2, r[1] + r[3] // 2, zone)
    _steps(loop, 4)
    shown["head_tab"] = ws.menu_view
    # the desk's row (editor_ui.py's desk_head): a T-Deck has no desk, so a
    # stand-in shows its workspaces for one draw and a tap through the zone
    class Desk:
        key = "code"

        def switch(self, k):
            self.key = k
            return True
    desk = Desk()
    ea = ws.editor_app
    ea._desk = lambda: desk
    try:
        owner = bl._zone_owner("menu")
        owner.draw_zone(ws.sys_canvas, zone)
        cells = ea.zone_cells(zone)
        shown["desk_head"] = [c[0] for c in cells]
        r = [c[2] for c in cells if c[0] == "ws:art"][0]
        owner.zone_tap(r[0] + r[2] // 2, r[1] + r[3] // 2, zone)
        shown["desk_ws"] = desk.key
    finally:
        del ea._desk
    ws.spine.exit()
    _steps(loop, 4)
    ws.spine.open_app(ws.app("appearance"))
    _steps(loop, 4)
    shown["config"] = bool(ws.look.edit_file())
    _steps(loop)
    ws.spine.exit()
    _steps(loop, 4)
    # the bridge (ui.legacy): a page on the Editor's kinds hosting a surface
    # on today's verbs, its hosted phase before the host begins the frame
    with open(ROOT + "/fakes/uitree_legacy_app.py", "w") as f:
        f.write(@LEGACY@)
    import uitree_legacy_app as LA
    import uitree_editor
    import ui_draw
    import ui

    class Damage:
        def all(self):
            pass
    lh = ui_draw.AppHost()
    lp = uitree_editor.Page(LA.build, lh, Damage())
    lp.open()
    cv, th = ws.sys_canvas, ws.theme_colors
    box = (0, 0, cv.w, cv.h)
    for _k in range(3):
        lp.hosted(0.033, cv, th, box, False)
        lh.begin(cv, th, box)
        lp.paint(0.033, cv, th, box, False)
        ui.end()
    hs = LA._SURF[id(None)]
    shown["legacy"] = [hs.draws, list(hs.rects[-1]), list(lp.legacies["list"][1])]
    print("RESULT " + json.dumps({"painted": painted, "shown": shown, "errors": errors}))
'''

# each ported page's build function, by name: Settings' rows, its tz, paired,
# System log and App permissions pages, the update screen, the web console,
# Licences' list (a licence's text is a page pushed over it: shown["licence"]),
# Settings and in the bar's popover,
# and the task manager on each of its tabs
# Achievements (a badge's popover: shown["badge"]), the task manager on each
# of its tabs, and the Editor's Code tab and theme mode's Config (the Editor's
# head row draws in the bar's zone, never a page frame: shown["head"], and its
# desk case shown["desk_head"])
BUILDS = ("rows_page", "tz_page", "paired_page", "log_page", "build", "page", "web_page",
          "list_page", "tasks_page", "achievements_page", "code_page", "config_page")


def test_every_ported_page_builds_and_draws_on_the_boards_micropython(tmp_path):
    PORTS._perms_carts(tmp_path / "carts")
    sources = "\n\n".join(inspect.getsource(c) for c in (PORTS.Updater, PORTS.C6Updater, Webhost))
    legacy = (Path(__file__).resolve().parent / "uitree_fixtures" / "uitree_legacy_app.py").read_text()
    more = (PAGES.replace("@SOURCES@", sources).replace("@LEGACY@", repr(legacy))
            .replace("@BROWSERS@", repr(PORTS.BROWSERS))
            .replace("@LOG_RING@", repr(PORTS.LOG_RING)))
    info, text = FA._run(tmp_path, entry="pages", more=more,
                         why="the console imports uitree at boot; this is the check "
                             "that the layer and its pages run on a board's MicroPython")
    shown, painted = info["shown"], info["painted"]
    assert not info["errors"], (info["errors"][:3], text[-3000:])
    assert shown["rows"] == "settings", shown
    assert shown["wifi"] == "wifi" and shown["wifi_popover"] == "wifi", shown
    assert [shown[p] for p in ("tz", "paired", "log", "permissions", "licences", "achievements")] == \
        ["tz", "paired", "log", "permissions", "licences", "achievements"], shown
    assert shown["badge"] == "First Steps", shown
    assert shown["licence"] == "MicroPython", shown
    for how in ("open_update", "open_update_online", "open_update_c6", "open_update_none"):
        assert shown[how] == "update", (how, shown)
    assert shown["web"] is True, shown
    assert shown["tasks"] == "running" and shown["tasks_notes"] == "Ana", shown
    assert shown["code"] == "menu", shown
    assert [shown[k] for k in ("find", "goto_open", "complete_open")] == \
        [True, "goto", "complete"], shown
    assert shown["stopped"] and shown["config"], shown
    assert shown["head"][-3:] == ["\x00undo", "\x00redo", None] and "code" in shown["head"], shown
    assert shown["head_tab"] == "paint", shown
    assert shown["desk_head"][:3] == ["ws:settings", "ws:code", "ws:art"], shown
    assert shown["desk_ws"] == "art", shown
    draws, drew_at, laid_at = shown["legacy"]
    assert draws == 3 and drew_at == laid_at, shown["legacy"]
    missing = [b for b in BUILDS if not painted.get(b)]
    assert not missing, ("pages that never painted", missing, painted, text[-3000:])

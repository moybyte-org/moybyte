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
    for p in ("tz", "paired", "log", "permissions", "licences"):
        sl.open_page(p)
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
    print("RESULT " + json.dumps({"painted": painted, "shown": shown, "errors": errors}))
'''

# each ported page's build function, by name: Settings' rows, its tz, paired,
# System log and App permissions pages, the update screen, the web console, and
# Licences' list (a licence's text is a page pushed over it: shown["licence"])
BUILDS = ("rows_page", "tz_page", "paired_page", "log_page", "build", "page", "web_page",
          "list_page")


def test_every_ported_page_builds_and_draws_on_the_boards_micropython(tmp_path):
    PORTS._perms_carts(tmp_path / "carts")
    sources = "\n\n".join(inspect.getsource(c) for c in (PORTS.Updater, PORTS.C6Updater, Webhost))
    more = (PAGES.replace("@SOURCES@", sources)
            .replace("@BROWSERS@", repr(PORTS.BROWSERS))
            .replace("@LOG_RING@", repr(PORTS.LOG_RING)))
    info, text = FA._run(tmp_path, entry="pages", more=more,
                         why="the console imports uitree at boot; this is the check "
                             "that the layer and its pages run on a board's MicroPython")
    shown, painted = info["shown"], info["painted"]
    assert not info["errors"], (info["errors"][:3], text[-3000:])
    assert shown["rows"] == "settings", shown
    assert [shown[p] for p in ("tz", "paired", "log", "permissions", "licences")] == \
        ["tz", "paired", "log", "permissions", "licences"], shown
    assert shown["licence"] == "MicroPython", shown
    for how in ("open_update", "open_update_online", "open_update_c6", "open_update_none"):
        assert shown[how] == "update", (how, shown)
    assert shown["web"] is True, shown
    missing = [b for b in BUILDS if not painted.get(b)]
    assert not missing, ("pages that never painted", missing, painted, text[-3000:])

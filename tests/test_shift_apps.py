"""Shift frames in the shipped apps (docs/scroll_and_text_2026-10.md §3.3,
§3.4, §13), on the full-screen console the T-Deck runs: Settings' rows, its
Licences and Achievements pages, My Art's drawings, Get Carts' shelf, Files'
list of projects, Storybook's stories and Appearance's rows,
dragged, flung and stepped through by keys, and the text console typed into,
on one, two and three buffers presented in turn.

Each case runs the same session on two consoles: one as it ships, its
frames free to shift (the content's own ring vouching, §3.4), and one whose
WM vouches for nothing, so every frame draws whole; every presented picture
of the first is held to the second's bytes, the share of its painted frames
that shifted or drew rows alone is held above a floor (L8's silent-disable
direction), and the engine's record audit fails a shift frame whose stops
moved outside its floor (L8's correctness direction)."""

import os

import pytest

from cart_store_fixtures import MemNet, Repo
from runtime import host_app, moy_carts, ui_draw
from ws_helpers import no_unlock_banner, quiesce, seed_stories

_DT = 1 / 30
_C = ui_draw._C


_NOW = [100000]


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """One clock for both consoles, a frame's worth a frame: what an app
    times (a cover's budget, a toast) times the same on each."""
    import sys
    from runtime import getcarts_app, myart_app, storybook_app, ticks  # noqa: F401 -- loaded, then patched
    for mod in list(sys.modules.values()):
        f = getattr(mod, "__file__", None) or ""
        if "/runtime/" in f.replace("\\", "/") and hasattr(mod, "_ticks_ms"):
            monkeypatch.setattr(mod, "_ticks_ms", lambda: _NOW[0])
    yield


@pytest.fixture(autouse=True)
def audit():
    _C.api_audit(1)
    yield
    failed = _C.api_audit(0)
    assert failed == 0, ("%d shift frames drew stops unlike the last frame's outside the floor, "
                         "or a stop's reach into R outside it" % failed)


def _shelf_net():
    repo = Repo("https://mit.example/carts", "Test carts")
    for i in range(24):
        repo.add("c%02d" % i, name="Cart %02d" % i)
    return repo, MemNet(repo.routes())


class _SameFree:
    """The store's role, its free space one number on both consoles (the
    shelf's caption says it, and two stores' measured room differs)."""

    def __init__(self, inst):
        self._inst = inst

    def __getattr__(self, name):
        return getattr(self._inst, name)

    def free(self):
        return (1 << 30, 4096)


def _settle_mtimes(root):
    """The drawings' files dated one second apart in their names' order:
    My Art lists them newest first, and two stores written in the same
    second would otherwise order them as the clock fell."""
    import os
    for dirpath, _dirs, files in os.walk(str(root)):
        for f in files:
            if f.lower().startswith("drawing"):
                n = int("".join(ch for ch in f if ch.isdigit()) or 0)
                os.utime(os.path.join(dirpath, f), (1700000000 + n, 1700000000 + n))


# the text console's script: a log longer than its view, and a line that
# answers each line typed
_LOG_SCRIPT = ("for i in range(40):\n    print('row %d of the log, a little longer' % i)\n"
               "def _update(dt):\n"
               "    s = input('> ')\n"
               "    if s is not None:\n"
               "        print('you said', s)\n")


def _console(tmp_path, app, ships, desk=False):
    import json
    from runtime import cart_index as ci
    root = tmp_path / ("ships" if ships else "whole")
    if desk:
        # a desk: what the console runs presents in its window
        ws = host_app.build_workstation(str(root / "carts"), sys_size=(1024, 600),
                                        windowed=True)
    else:
        ws = host_app.build_workstation(str(root / "carts"))
    ws.bar_layer._clock_text = lambda: "10:30"
    no_unlock_banner(ws)
    quiesce(ws)
    ws.pointer.visible = False
    if not ships:
        ws.wm.CONTENT_RING = False
        ws.player.holds = lambda cv: False
    if app.startswith("editor "):
        # an Editor tab over a cart: "editor TAB CART TITLE"
        _e, tab, title = app.split(" ", 2)
        ws.open_in_editor(next(c for c in ws.carts.all if c.get("title") == title))
        ws.editor_app.set_tab(tab)
    elif app in ("settings", "licences", "achievements"):
        ws.open_settings()
        if app != "settings":
            ws.settings_layer.open_page(app)
    elif app == "console":
        stored = moy_carts.save_file("docs", "log.py", _LOG_SCRIPT, ws.carts_root)
        ok, why = ws.run_script("docs", stored)
        assert ok, why
    elif app == "myart":
        for i in range(18):
            moy_carts.save_file("drawings", "Drawing %02d" % i,
                                moy_carts.encode_moyimg(32, 24, bytes([8 + i % 8]) * 768),
                                ws.carts_root)
        _settle_mtimes(os.path.dirname(str(ws.carts_root).rstrip("/")))
        assert ws.open_app(ws._apps_by_id["myart"])
    elif app == "getcarts":
        repo, net = _shelf_net()
        (root / ci.INDEXES_NAME).write_text(json.dumps({"indexes": [repo.url("index.json")]}))
        ws.cart_net = net
        app_ = ws._apps_by_id["getcarts"]
        app_._inst = _SameFree(app_._inst)
        assert ws.open_app(app_) is not False
    else:
        if app == "storybook":
            seed_stories(ws)
        app_ = ws._apps_by_id[app]
        assert ws.open_app(app_) is not False
        if app == "files":
            # the list of projects, every cover made: which rows a frame's
            # floor reaches decides which covers are asked for first
            f = getattr(app_, "_inst", app_)
            f.level = "files"
            f._focus = "files"
            for _ in range(500):
                ws.covers.begin_frame()
                if all(f._nav.cover(r[4]) is not False for r in f.rows):
                    break
    return ws


def _owes(ws):
    """Whether an app of the console has idle work owed."""
    for app, _t in ws._apps:
        if getattr(app, "idle_owed", False):
            return True
    return False


class Pair:
    """The session on both consoles, frame by frame; the first's buffers
    presented in turn, `bufs` of them."""

    def __init__(self, tmp_path, app, bufs, desk=False):
        self.a = _console(tmp_path, app, True, desk)
        self.b = _console(tmp_path, app, False, desk)
        self.da = host_app.ConsoleDriver(self.a)
        self.db = host_app.ConsoleDriver(self.b)
        self.bufs = bufs
        self._state = {}
        root = self.a.sys_canvas
        root.RETAINED_FRAMES = bufs
        self.held = [bytes(root._buf)]
        self.kinds = []
        self.n = 0
        # the open and the idle work it owes (My Art's pictures, made one an
        # idle frame) settle on both before any frame is held to the other
        for _ in range(200):
            self.frame(idle=True, check=False)
            if not (_owes(self.a) or _owes(self.b)):
                break
        for _ in range(3):
            self.frame(check=False)
        for _ in range(3):
            self.frame()

    def frame(self, idle=False, check=True):
        """One frame of both, every frame painted unless `idle` (the gate's
        to decide, as a board's); `check`: its picture held to the other's."""
        _NOW[0] += 33
        for ws, drv in ((self.a, self.da), (self.b, self.db)):
            if not idle:
                ws._dirty = True
            ws.pointer.visible = False
            k = ws._frames_drawn
            self._drive(ws, drv)
            ws.painted = ws._frames_drawn != k
        a, b = self.a, self.b
        assert a.painted == b.painted, "the two consoles' gates parted"
        if not a.painted:
            return
        self.n += 1
        self.kinds.append(a.frame_change)
        shot, want = bytes(a.sys_canvas._buf), bytes(b.sys_canvas._buf)
        if check and shot != want:
            w = a.sys_canvas.w
            at = next(i for i in range(0, len(shot), 2) if shot[i:i + 2] != want[i:i + 2]) // 2
            raise AssertionError("frame %d (%s, %d buffers) differs from a whole repaint at (%d, %d)"
                                 % (self.n, a.frame_change, self.bufs, at % w, at // w))
        self.held.append(shot)
        if self.bufs > 1:
            root = a.sys_canvas
            root._comp.last = shot
            root._buf[:] = self.held[-self.bufs] if len(self.held) > self.bufs else self.held[0]

    def _drive(self, ws, drv):
        """One loop pass of `ws` with the toolkit's per-console state its
        own, as on a board with one console: the painted frame and the host
        that took the keys. The second console's held steps are never dead,
        so where the first's dead-step rule takes a step out its frame must
        show the same."""
        st = self._state.setdefault(id(ws), [None, [-1, False, None] + [-1] * (
            len(ui_draw._PAINT) - 3), False, ui_draw._PF[:]])
        ui_draw._KEYS_HOST[0] = st[0]
        ui_draw._PAINT[:] = st[1]
        ui_draw._PF[:] = st[3]
        ui_draw._EDGE[0] = st[2]
        twin = ws is self.b
        if twin:
            sd, dead = ui_draw.steps_dead, ui_draw.AppHost.dead
            ui_draw.steps_dead = lambda rw, console=None: False
            ui_draw.AppHost.dead = lambda host, rw: False
        try:
            drv.frame(_DT)
        finally:
            if twin:
                ui_draw.steps_dead, ui_draw.AppHost.dead = sd, dead
            st[0] = ui_draw._KEYS_HOST[0]
            st[1] = list(ui_draw._PAINT)
            st[3] = ui_draw._PF[:]
            st[2] = ui_draw._EDGE[0]

    def both(self, verb, *args):
        getattr(self.da, verb)(*args)
        getattr(self.db, verb)(*args)

    def drag(self, x, y0, dy, steps=6):
        self.both("touch", x, y0)
        self.frame()
        for i in range(1, steps + 1):
            self.both("touch_drag", x, y0 + dy * i // steps)
            self.frame()
        self.both("touch_up")
        self.frame()

    def settle(self, most=200):
        for _ in range(most):
            self.frame(idle=True)

    def keys(self, name, n):
        for _ in range(n):
            self.both("press", name)
            self.frame()
            self.frame(idle=True)

    def share(self):
        moved = [k for k in self.kinds if k is not None]
        return len(moved) / max(1, len(self.kinds))


APPS = ("settings", "licences", "achievements", "myart", "getcarts", "files", "storybook",
        "appearance")


@pytest.mark.parametrize("bufs", (1, 2, 3))
@pytest.mark.parametrize("app", APPS)
def test_drags_flings_and_key_steps_shift_byte_for_byte(tmp_path, app, bufs):
    p = Pair(tmp_path, app, bufs)
    lay = p.a.layout
    x = lay.cx + lay.cw // 2
    y = lay.cy + lay.ch * 3 // 4
    del p.kinds[:]
    p.drag(x, y, -lay.ch // 2)
    p.settle()
    p.drag(x, y, -lay.ch // 3, steps=3)
    p.settle()
    p.drag(x, lay.cy + lay.ch // 4, lay.ch // 3, steps=3)
    p.settle()
    p.keys("down", 6)
    p.keys("up", 3)
    p.settle()
    assert p.share() > 0.5, "%s shifted or drew rows on %.2f of its painted frames: %r" % (
        app, p.share(), p.kinds)


def _settings_session(p):
    lay = p.a.layout
    x = lay.cx + lay.cw // 2
    y = lay.cy + lay.ch * 3 // 4
    del p.kinds[:]
    p.drag(x, y, -lay.ch // 2)
    p.settle()
    p.keys("down", 6)
    p.settle()
    return p.share()


@pytest.mark.parametrize("cost", (None, 700, 1000))
def test_a_canvas_whose_copy_costs_the_raster_draws_whole(tmp_path, cost):
    """A canvas declares what its `shift_last` costs against the raster it
    saves (`SHIFT_COST`, the T-Deck's CPU copy beside the feeder): a frame
    whose floor leaves too little of R undrawn to pay for the copy draws
    whole, and every frame is still a whole repaint's bytes."""
    p = Pair(tmp_path / str(cost), "settings", 2)
    p.a.sys_canvas.SHIFT_COST = cost
    share = _settings_session(p)
    if cost == 1000:
        assert share == 0
    else:
        assert share > 0
    if cost == 700:
        q = Pair(tmp_path / "free", "settings", 2)
        assert share < _settings_session(q)


def test_a_move_that_leaves_pictures_undrawn_pays_whatever_the_copy_costs(tmp_path):
    """A picture's rows cost more to draw than any copy (`ui_draw._pays`):
    My Art's drawings, dragged on a canvas whose copy costs a whole row's
    raster, still shift where the floor leaves a picture undrawn."""
    p = Pair(tmp_path, "myart", 2)
    p.a.sys_canvas.SHIFT_COST = 1000
    lay = p.a.layout
    del p.kinds[:]
    p.drag(lay.cx + lay.cw // 2, lay.cy + lay.ch * 3 // 4, -lay.ch // 2)
    p.settle()
    assert p.share() > 0


def _console_session(p):
    """The text console: a prompt typed, a line entered and answered, the
    prompt typed again on the frames after that move of the log, and the
    log dragged back and coasting."""
    cx, top, _cw, _ch = p.a.app_content_rect()
    del p.kinds[:]

    def typed(text):
        for ch in text:
            p.both("key_down", ord(ch), 0)
            p.frame()
            p.both("key_up", ord(ch), 0)
            p.frame()
    typed("hello\r")
    typed("ok")
    p.drag(cx + 40, top + 30, 100)
    for _ in range(6):
        p.frame()


@pytest.mark.parametrize("desk", (False, True))
@pytest.mark.parametrize("bufs", (1, 2, 3))
def test_the_text_console_draws_its_prompt_alone(tmp_path, bufs, desk):
    """The text console under the FPS chip (`overdrew`), whose log the
    chip misses: a typed prompt's frames draw its rows alone -- on two and
    three buffers moving nothing where the buffer the frame paints holds the
    log (`moy_ui_api_lag`), the frames after a move of it too -- the entered
    line's move of the log and a drag of it are shift frames (on a desk the
    drag's finger lies outside the log, so its frames draw rows), and every
    frame is a whole repaint's bytes. On a desk the run's canvas is its
    window's own (`Player.holds`), which holds the log whatever the glass's
    buffers do."""
    p = Pair(tmp_path, "console", bufs, desk)
    assert p.a.show_fps
    if desk:
        assert p.a.player.holds(p.a.canvas)
    _console_session(p)
    k = p.kinds
    assert k[:10] + k[12:16] == ["rows"] * 14, k
    assert k[10] == "shift", k
    assert set(k[17:]) == {"rows" if desk else "shift"}, k


def test_an_overlay_over_the_console_log_moves_none_of_it(tmp_path):
    """Where what the console draws over the page after its pass meets
    the log (the HUD's card, `overdrew`), a frame moves none of the
    buffers it lies in: from the first frame it lies in one, the frames draw
    whole, and every frame is a whole repaint's bytes."""
    p = Pair(tmp_path, "console", 2)
    cx, top, _cw, _ch = p.a.app_content_rect()
    for ws in (p.a, p.b):
        def over(ws=ws):
            ws.canvas.rect(cx + 100, top + 60, 60, 30, 3)
            ui_draw.overdrew(cx + 100, top + 60, 60, 30)
        ws.perf_ui.draw = over
    _console_session(p)
    assert not any(p.kinds[1:]), p.kinds


class _NoMark:
    """An app's damage role that marks nothing: a change made under it
    reaches the next frames by the page's gen alone, as one that came from
    the store would."""

    def __init__(self, d):
        self._d = d

    def __getattr__(self, name):
        return getattr(self._d, name)

    def all(self):
        pass

    def again(self):
        pass


def _app(ws, app):
    a = ws._apps_by_id[app]
    return getattr(a, "_inst", a)


def _renamed(ws):
    """Files: the list's last row renamed (the drag left the list's end in
    view), a row no mark moves."""
    f = _app(ws, "files")
    keep = f.rows[f.cur][0] if f.cur >= 0 else None
    name, kind = f.rows[-1][0], f.rows[-1][1]
    res = f._store.rename(kind, name, name + " b")
    assert res[1] is None
    f._refresh(keep=keep)


def _shown_story(sb):
    """The story whose card shows most and holds no mark: the frames after
    a change to it move its pixels unless its gen moves."""
    g = sb.layout.stories_grid
    best, most = None, 0
    for r in sb._rows:
        b = sb._ui.box(r.o["id"])
        if b is None or r.o["id"] == sb._focused:
            continue
        shows = min(b[1] + b[3], g[1] + g[3]) - max(b[1], g[1])
        if shows > most:
            best, most = r.entry, shows
    assert best is not None, "no story's card shows"
    return best


def _story_renamed(ws):
    sb = _app(ws, "storybook")
    sb._menu_story = _shown_story(sb)
    sb.typed = "Renamed"
    sb._rename_done()


def _story_turned(ws):
    """Storybook: a shown story's first page turned over, another slide
    first."""
    from runtime import story_doc as D
    sb = _app(ws, "storybook")
    e = _shown_story(sb)
    doc = D.copy(e["doc"])
    doc["slides"].insert(0, D.layout_slide("blank", 9))
    e["doc"] = doc
    if sb.cards is not None:
        sb.cards.drop(e["name"])


CHANGES = {"file renamed": ("files", _renamed), "story renamed": ("storybook", _story_renamed),
           "story page turned": ("storybook", _story_turned)}


@pytest.mark.parametrize("bufs", (1, 2, 3))
@pytest.mark.parametrize("change", sorted(CHANGES))
def test_a_change_the_page_shows_moves_its_gen(tmp_path, change, bufs):
    """What a page shows changed with no mark of the app's: the page's gen
    moves, so the frames after it draw it again, not the last frame's pixels
    moved."""
    app, fn = CHANGES[change]
    p = Pair(tmp_path, app, bufs)
    if app == "files":
        from runtime import files_app
        for ws in (p.a, p.b):
            for i in range(20):
                moy_carts.save_file("drawings", "Drawing %02d" % i,
                                    moy_carts.encode_moyimg(32, 24, bytes([8 + i % 8]) * 768),
                                    ws.carts_root)
            _settle_mtimes(os.path.dirname(str(ws.carts_root).rstrip("/")))
            f = _app(ws, "files")
            f._refresh_counts()
            f.open_place(files_app.ART)
        p.settle()
    lay = p.a.layout
    p.drag(lay.cx + lay.cw // 2, lay.cy + lay.ch * 3 // 4, -lay.ch, steps=3)
    p.settle()
    for ws in (p.a, p.b):
        a = _app(ws, app)
        d = a._damage
        a._damage = _NoMark(d)
        try:
            fn(ws)
        finally:
            a._damage = d
    for _ in range(3):
        p.frame()
    p.settle()
    p.keys("down", 2)
    p.keys("up", 1)
    p.settle()


# The Editor's tabs on the verbs (docs/scroll_and_text_2026-10.md §15 P5).
# A cart's picker of pictures (Hop Quest's, Moy Night's) is a part with no
# gen: its page draws whole, and its frames are held to a whole draw's alone.
EDITOR_TABS = ("editor cards Coin Quest", "editor cards Bench", "editor cards Hop Quest",
               "editor cards Moy Night", "editor blocks Coin Quest", "editor blocks Bench",
               "editor music Sky Run", "editor music Coin Quest")
_PICTURES = ("editor cards Hop Quest", "editor cards Moy Night")


@pytest.mark.parametrize("bufs", (1, 2, 3))
@pytest.mark.parametrize("app", EDITOR_TABS)
def test_an_editor_tabs_key_steps_draw_rows_byte_for_byte(tmp_path, app, bufs):
    p = Pair(tmp_path, app, bufs)
    del p.kinds[:]
    p.keys("down", 6)
    p.keys("up", 3)
    if "blocks" in app or "music" in app:
        p.keys("down", 14)              # the rows scroll by whole rows
        p.keys("up", 14)
    if "music" in app:
        p.keys("right", 4)              # the step's value: its row alone
        p.keys("left", 2)
    p.settle()
    if app not in _PICTURES:
        assert p.share() > 0.5, "%s shifted or drew rows on %.2f of its painted frames: %r" % (
            app, p.share(), p.kinds)

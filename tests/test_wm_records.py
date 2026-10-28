"""The windowed WM's records and direct render (docs/scroll_and_text_2026-10.md
§7, §8; docs/surface_model_v1.md §14.3, §14.6, §14.7): on a desk, an app on
the public UI API's verbs draws every frame it draws straight into the glass,
its pages shift where the shown ring vouches for the buffer the last frame
drew, a frame whose only change is the focused window's repaints no desk and
no chrome, the window's buffer is refilled from the glass before anything
reads it, and the debt a buffer owes is copied as the rows that changed.

Each case runs one session on two desks: one as it ships, and one whose WM
takes none of it (no content speaks, no shift is vouched: every frame of the
focused window renders into its buffer and stamps it, as the WM did before);
after every loop pass the glass of the first -- the buffer last presented,
with one, two and three buffers presented in turn -- is held to the
second's bytes."""

import os

import pytest

from cart_store_fixtures import MemNet, Repo
from runtime import ui_draw
from ws_helpers import build_desktop_ws, make_drv, no_unlock_banner, seed_stories

_DT = 1 / 30
_C = ui_draw._C
_NOW = [100000]


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """One clock for both desks, a frame's worth a frame."""
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


class _SameFree:
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


def _open(ws, root, app):
    import json
    from runtime import cart_index as ci, moy_carts
    if app in ("settings", "licences", "achievements"):
        ws.open_settings()
        if app != "settings":
            ws.settings_layer.open_page(app)
    elif app == "myart":
        for i in range(18):
            moy_carts.save_file("drawings", "Drawing %02d" % i,
                                moy_carts.encode_moyimg(32, 24, bytes([8 + i % 8]) * 768),
                                ws.carts_root)
        # every picture made stays: which ones a frame reaches decides what
        # is made first, and an eviction would then differ between the desks
        _settle_mtimes(os.path.dirname(str(ws.carts_root).rstrip("/")))
        ws._apps_by_id["myart"].covers.KEEP = 1000
        assert ws.open_app(ws._apps_by_id["myart"]) is not False
    elif app == "blocks":
        from ws_helpers import select_on_home
        cart = select_on_home(ws, "Coin Quest")
        assert cart is not None
        ws.open_in_editor(cart)
        d = getattr(ws.editor_app, "_desk", None)
        if d is not None and d() is not None:
            d().switch("code")
        ws.editor_app.set_tab("blocks")
        ws._dirty = True
    elif app == "getcarts":
        repo = Repo("https://mit.example/carts", "Test carts")
        for i in range(24):
            repo.add("c%02d" % i, name="Cart %02d" % i)
        (root / ci.INDEXES_NAME).write_text(json.dumps({"indexes": [repo.url("index.json")]}))
        ws.cart_net = MemNet(repo.routes())
        a = ws._apps_by_id["getcarts"]
        a._inst = _SameFree(a._inst)
        assert ws.open_app(a) is not False
    else:
        if app == "storybook":
            seed_stories(ws)
        assert ws.open_app(ws._apps_by_id[app]) is not False


# The Waveshare P4's desk as it ships: the desk's size class at the scale 1
# (the suite's other desk takes the handheld's class at 2).
P4 = {"panel_diagonal_in": 7.0, "font_scale": 1}


def _desk(tmp_path, app, ships, apps=(), board=None):
    root = tmp_path / ("ships" if ships else "whole")
    ws = build_desktop_ws(root, **(board or {}))
    no_unlock_banner(ws)
    ws.bar_layer._clock_text = lambda: "10:30"
    ws.wallpaper.is_animating = lambda dt: False
    ws.pointer.visible = False
    ws._toast_until = 0
    ws.open_desk()
    if not ships:
        ws.wm._speaks = lambda win: False
        ws.wm._vouches = lambda win: False
    for other in apps:
        _open(ws, root, other)
    _open(ws, root, app)
    return ws


def _owes(ws):
    for app, _t in ws._apps:
        if getattr(app, "idle_owed", False):
            return True
    return False


class Pair:
    """The session on both desks, loop pass by loop pass; each desk's
    root presents `bufs` buffers in turn."""

    def __init__(self, tmp_path, app, bufs, apps=(), board=None):
        self.a = _desk(tmp_path, app, True, apps, board)
        self.b = _desk(tmp_path, app, False, apps, board)
        self.da = make_drv(self.a)
        self.db = make_drv(self.b)
        self.bufs = bufs
        self.held = {}
        for ws in (self.a, self.b):
            root = ws.wm._root_canvas
            root.RETAINED_FRAMES = bufs
            self.held[id(ws)] = [bytes(root._buf)]
        self.kinds = []
        self.direct = 0
        rd = self.a.wm._direct_render

        def direct(w, dt):
            r = rd(w, dt)
            self.direct += 1 if r else 0
            return r
        self.a.wm._direct_render = direct
        for _ in range(300):
            self.frame(check=False)
            if not (_owes(self.a) or _owes(self.b)) and not self.painted:
                break
        self.settle(check=False)
        if app == "myart":
            self._all_pictures()
        del self.kinds[:]
        self.direct = 0

    def _all_pictures(self):
        """Every drawing's picture made, at both sizes a card draws one (a
        focused card's rect grows with its mark): which pictures a frame's
        floor reaches decides which are made first, so the desks would
        otherwise show a picture landing on different frames."""
        for ws in (self.a, self.b):
            a = ws._apps_by_id["myart"]
            sizes = set((e[0], e[1]) for v in a.covers._got.values() for e in v)
            w, h = a._pic_wh
            sizes.update(((w, h), (w + 10, h + 10)))
            for name in a.drawings:
                for w, h in sizes:
                    a.covers.get(name, w, h)
            del a._owed[:]
            a._gens_due = True
            ws._dirty = True
        self.settle(check=False)

    def glass(self, ws):
        h = self.held[id(ws)]
        return h[-1] if self.bufs > 1 else bytes(ws.wm._root_canvas._buf)

    def frame(self, check=True):
        _NOW[0] += 33
        painted = False
        for ws, drv in ((self.a, self.da), (self.b, self.db)):
            ws.pointer.visible = False
            k = ws._frames_drawn
            r = ws.wm._repeats_n
            drv.frame(_DT)
            if ws._frames_drawn != k:
                painted = True
                root = ws.wm._root_canvas
                h = self.held[id(ws)]
                if ws is self.a and ws.wm._repeats_n == r:
                    self.kinds.append(ws.frame_change)     # a content frame, not a debt's copy
                if self.bufs > 1:
                    h.append(bytes(root._buf))
                    root._comp.last = h[-1]
                    root._buf[:] = h[-self.bufs] if len(h) > self.bufs else h[0]
        self.painted = painted
        if check:
            shot, want = self.glass(self.a), self.glass(self.b)
            if shot != want:
                w = self.a.wm._root_canvas.w
                at = next(i for i in range(0, len(shot), 2) if shot[i:i + 2] != want[i:i + 2]) // 2
                raise AssertionError("the glass (%s, %d buffers) differs from the whole "
                                     "repaints' at (%d, %d)" % (self.a.frame_change, self.bufs,
                                                               at % w, at // w))
        return painted

    def both(self, verb, *args):
        getattr(self.da, verb)(*args)
        getattr(self.db, verb)(*args)

    def settle(self, most=200, check=True):
        for _ in range(most):
            if not self.frame(check=check) and not self.a.wm.debt_owed():
                quiet = True
                for _ in range(4):
                    quiet = not self.frame(check=check) and quiet
                if quiet:
                    return

    def win(self, kind):
        return self.a.wm._wins[kind]

    def at(self, kind, fx=0.5, fy=0.75):
        cx, cy, cw, ch = self.win(kind).content_rect()
        return cx + int(cw * fx), cy + int(ch * fy)

    def drag(self, kind, dy, steps=6, release=True, fy=0.75):
        x, y = self.at(kind, fy=fy)
        self.both("touch", x, y)
        self.frame()
        for i in range(1, steps + 1):
            self.both("touch_drag", x, y + dy * i // steps)
            self.frame()
        if release:
            self.both("touch_up")
            self.frame()

    def keys(self, name, n, gap=12):
        for _ in range(n):
            self.both("press", name)
            self.frame()
            for _ in range(gap):
                self.frame()

    def hold(self, name, frames):
        self.both("hold", name, True)
        self.both("press", name)
        for _ in range(frames):
            self.frame()
        self.both("hold", name, False)
        self.frame()

    def share(self):
        moved = [k for k in self.kinds if k is not None]
        return len(moved) / max(1, len(self.kinds))


APPS = ("settings", "licences", "achievements", "myart", "getcarts", "files",
        "storybook", "appearance", "calc")
SHIFTS = ("settings", "licences", "achievements", "myart", "getcarts", "files",
          "storybook", "appearance")


@pytest.mark.parametrize("bufs", (1, 2, 3))
@pytest.mark.parametrize("app", APPS)
def test_drags_flings_keys_and_held_keys_match_whole_repaints(tmp_path, app, bufs):
    p = Pair(tmp_path, app, bufs)
    if app == "files":
        _files_list(p)
    _, _, _, ch = p.win(p.a.wm._focus).content_rect()
    kind = p.a.wm._focus
    p.drag(kind, -ch // 2)
    p.settle()
    p.drag(kind, -ch // 3, steps=3)
    p.settle()
    p.drag(kind, ch // 3, steps=3, fy=0.25)
    p.settle()
    p.keys("down", 5)
    p.keys("up", 2)
    p.keys("right", 1)
    p.keys("left", 1)
    p.settle()
    p.hold("down", 40)
    p.settle()
    assert p.direct > 0, "the window never drew straight into the glass"
    if app in SHIFTS:
        assert p.share() > 0.4, "%s shifted or drew rows on %.2f of its painted frames: %r" % (
            app, p.share(), p.kinds)


def _inst(ws, cls):
    """The open app whose class is named `cls`."""
    for app, _t in ws._apps:
        a = getattr(app, "_inst", app)
        if type(a).__name__ == cls:
            return a
    raise AssertionError("no %s open" % cls)


def _files_list(p):
    """Files' list of projects open on both desks (on a column of its own
    the places come first) with every cover made: which rows a frame's
    floor reaches decides which covers are asked for first, so the desks
    would otherwise show a cover landing on different frames."""
    for ws in (p.a, p.b):
        f = _inst(ws, "FilesAppLayer")
        if f.level != "files":
            f.level = "files"
            f._focus = "files"
        for _ in range(500):
            ws.covers.begin_frame()
            if all(f._nav.cover(r[4]) is not False for r in f.rows):
                break
        ws._dirty = True
    p.settle()


# The Waveshare P4's desk: what each app's pages show on it, every frame's
# change byte for byte against whole repaints. Licences' list is the desk's
# scroll region beside the text (the handheld's is a page of its own); Get
# Carts' Check, drawn before its shelf opens, reaches four rows into it.
P4_APPS = ("licences", "files", "storybook", "appearance", "getcarts")


@pytest.mark.parametrize("bufs", (1, 2, 3))
@pytest.mark.parametrize("app", P4_APPS)
def test_the_p4_desk_shifts_its_apps_pages_byte_for_byte(tmp_path, app, bufs):
    p = Pair(tmp_path, app, bufs, board=P4)
    kind = p.a.wm._focus
    if app == "files":
        _files_list(p)
        assert _inst(p.a, "FilesAppLayer").layout.two
    _, _, _, ch = p.win(kind).content_rect()
    del p.kinds[:]
    p.drag(kind, -ch // 2)
    p.settle()
    p.drag(kind, ch // 3, steps=3, fy=0.25)
    p.settle()
    p.keys("down", 4)
    p.keys("up", 2)
    p.settle()
    p.hold("down", 8)
    p.settle()
    assert p.share() > 0.5, "%s shifted or drew rows on %.2f of its painted frames: %r" % (
        app, p.share(), p.kinds)
    if app != "appearance":           # its rows fit the desk's window: they never move
        assert "shift" in p.kinds


def _drawings(ws, n=20):
    from runtime import moy_carts
    for i in range(n):
        moy_carts.save_file("drawings", "Drawing %02d" % i,
                            moy_carts.encode_moyimg(32, 24, bytes([8 + i % 8]) * 768),
                            ws.carts_root)
    _settle_mtimes(os.path.dirname(str(ws.carts_root).rstrip("/")))


def _art(p):
    """Files' Art place on both desks, its drawings listed."""
    from runtime import files_app
    for ws in (p.a, p.b):
        _drawings(ws)
        f = _inst(ws, "FilesAppLayer")
        f._refresh_counts()
        f.open_place(files_app.ART)
        f._focus = "files"
    p.settle()


def _shown_row(f):
    """The current row's name: one the list shows."""
    return f.rows[f.cur][0]


def _file_added(ws):
    from runtime import moy_carts
    f = _inst(ws, "FilesAppLayer")
    moy_carts.save_file("drawings", "Drawing 99",
                        moy_carts.encode_moyimg(32, 24, bytes([3]) * 768), ws.carts_root)
    f._refresh(keep=_shown_row(f))


def _file_renamed(ws):
    """The last row renamed (the drag left the list's end in view): a row
    no mark moves."""
    f = _inst(ws, "FilesAppLayer")
    keep = _shown_row(f)
    name = f.rows[-1][0]
    res = f._store.rename("drawings", name, name + "b")
    assert res[1] is None
    f._refresh(keep=keep)


def _file_deleted(ws):
    f = _inst(ws, "FilesAppLayer")
    f._act("DEL")


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


def _story(ws):
    return _shown_story(_inst(ws, "StorybookAppLayer"))


def _story_added(ws):
    from runtime import story_doc as D, moy_carts
    sb = _inst(ws, "StorybookAppLayer")
    doc = D.new_doc("A new story")
    moy_carts.save_file("stories", "zz/story.json", D.write(doc), ws.carts_root)
    sb._scan()


def _story_renamed(ws):
    sb = _inst(ws, "StorybookAppLayer")
    sb._menu_story = _story(ws)
    sb.typed = "Renamed"
    sb._rename_done()


def _story_turned(ws):
    """The focused story's first page turned over: another slide first."""
    from runtime import story_doc as D
    sb = _inst(ws, "StorybookAppLayer")
    e = _story(ws)
    doc = D.copy(e["doc"])
    doc["slides"].insert(0, D.layout_slide("blank", 9))
    e["doc"] = doc
    if sb.cards is not None:
        sb.cards.drop(e["name"])


def _theme_changed(ws):
    a = _inst(ws, "AppearanceAppLayer")
    a._theme.set("machine" if a._ch["theme"] != "machine" else "moy")
    a._acted()


def _accent_changed(ws):
    a = _inst(ws, "AppearanceAppLayer")
    a._change("accent", a._accents[3][1])


CHANGES = {"file added": ("files", _file_added), "file renamed": ("files", _file_renamed),
           "file deleted": ("files", _file_deleted), "story added": ("storybook", _story_added),
           "story renamed": ("storybook", _story_renamed),
           "story page turned": ("storybook", _story_turned),
           "theme changed": ("appearance", _theme_changed),
           "accent changed": ("appearance", _accent_changed)}


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


_CLS = {"files": "FilesAppLayer", "storybook": "StorybookAppLayer",
        "appearance": "AppearanceAppLayer"}


def _gen(ws, app):
    if app == "files":
        return _inst(ws, "FilesAppLayer")._files_o["gen"]
    if app == "storybook":
        return _inst(ws, "StorybookAppLayer")._stories_o["gen"]
    return _inst(ws, "AppearanceAppLayer")._page_o["gen"]


@pytest.mark.parametrize("bufs", (1, 2, 3))
@pytest.mark.parametrize("change", sorted(CHANGES))
def test_a_change_the_page_shows_moves_its_gen(tmp_path, change, bufs):
    """What a page shows changed under a coast and between key steps: its
    gen moves, so the frames after it draw the page again, not the last
    frame's pixels moved."""
    app, fn = CHANGES[change]
    p = Pair(tmp_path, app, bufs, board=P4)
    kind = p.a.wm._focus
    if app == "files":
        _art(p)
    p.keys("down", 2)
    p.settle()
    _, _, _, ch = p.win(kind).content_rect()
    p.drag(kind, -ch, steps=3)
    p.frame()
    was = _gen(p.a, app)
    for ws in (p.a, p.b):
        a = _inst(ws, _CLS[app])
        d = a._damage
        a._damage = _NoMark(d)
        try:
            fn(ws)
        finally:
            a._damage = d
    p.frame()
    p.frame()
    assert _gen(p.a, app) != was, "%s left the page's gen as it was" % change
    p.settle()
    p.keys("down", 2)
    p.keys("up", 1)
    p.settle()


def _tap_widget(p, name):
    from runtime import shell_layout
    lay = p.a.wm._root_ctx.layout
    i, (x, y, w, h) = shell_layout.cell_of(lay.bars, name)
    bx, by = lay.bars[i][1][:2]
    p.both("click", bx + x + w // 2, by + y + h // 2)


def _strip(p, kind):
    win = p.win(kind)
    return win.x + win.w // 2, win.y + 1 + win.title_h // 2


@pytest.mark.parametrize("bufs", (1, 2, 3))
def test_overlapping_windows_a_focus_change_and_a_move_mid_coast(tmp_path, bufs):
    """Settings over Calc: a fling's coast, a tap on Calc mid-coast (the
    window that lost focus is stamped from its buffer, which direct render
    left behind the glass), back to Settings, a key's glide, and a drag of
    Settings' strip mid-coast (the move stamps its buffer)."""
    p = Pair(tmp_path, "settings", bufs, apps=("calc",))
    w = p.a.wm
    c = p.win("calc")
    for ws in (p.a, p.b):
        ws.wm._move_window(ws.wm._wins["settings"], 4, ws.wm._wins["settings"].y)
        ws._dirty = True
    p.settle()
    s = p.win("settings")
    sx, sy, sw, sh = s.content_rect()
    assert sx + sw <= w._root_canvas.w and sy + sh <= w._root_canvas.h
    from runtime.wm_windowed import _overlap
    assert _overlap((s.x, s.y, s.w, s.h), (c.x, c.y, c.w, c.h))
    _, _, _, ch = s.content_rect()
    p.drag("settings", -ch // 2, steps=3)
    p.frame()
    cx, cy, cw, chh = c.content_rect()
    vis = next((x, y) for y in range(cy + chh - 4, cy, -8) for x in range(cx + 4, cx + cw, 8)
               if not (s.x <= x < s.x + s.w and s.y <= y < s.y + s.h))
    p.both("click", *vis)
    p.frame()
    assert w._focus == "calc"
    p.settle()
    p.both("click", s.x + 24, _strip(p, "settings")[1])
    p.frame()
    p.settle()
    assert w._focus == "settings"
    p.keys("down", 3, gap=4)
    p.drag("settings", -ch // 2, steps=3)
    p.frame()
    x, y = _strip(p, "settings")
    p.both("touch", x, y)
    p.frame()
    for i in range(1, 5):
        p.both("touch_drag", x + 10 * i, y + 8 * i)
        p.frame()
    p.both("touch_up")
    p.frame()
    p.settle()
    p.keys("down", 2)
    p.settle()


@pytest.mark.parametrize("bufs", (1, 2, 3))
@pytest.mark.parametrize("widget", ("clock", "menu"))
def test_a_popover_and_the_menu_scrim_over_a_glide(tmp_path, bufs, widget):
    """A key's glide, a popover (the clock's) or the system menu's scrim
    opened over it mid-glide, closed, and the page's next frames."""
    p = Pair(tmp_path, "settings", bufs)
    p.keys("down", 3, gap=2)
    p.both("press", "down")
    p.frame()
    p.frame()
    _tap_widget(p, widget)
    for _ in range(6):
        p.frame()
    assert p.a.sysmenu.open
    _tap_widget(p, widget)
    for _ in range(6):
        p.frame()
    assert not p.a.sysmenu.open
    p.settle()
    p.keys("down", 3)
    p.settle()


# The apps on the public UI API's verbs whose pages the desk and the
# handheld shift: a mark after the pass is a whole repaint of a page the
# pass drew (`all` is spent with the frame, so it paints nothing it means
# to; `again` owes a whole frame where the engine owes a frame of rows).
VERBS_APPS = ("files_app", "getcarts_app", "storybook_app", "appearance_app",
              "myart_app", "calc_app")


def test_no_verbs_app_marks_its_page_after_the_pass():
    """docs/scroll_and_text_2026-10.md §7: an answer that changed what the
    pass drew before it owes the next frame through the app's host
    (`AppHost.again`); a mark of the page from its draw is a ratchet."""
    import ast
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "runtime"
    bad = []
    for mod in VERBS_APPS:
        tree = ast.parse((root / (mod + ".py")).read_text())
        for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
            for fn in cls.body:
                if not (isinstance(fn, ast.FunctionDef) and fn.name == "draw"):
                    continue
                calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                         and isinstance(n.func, ast.Attribute)]
                begin = min((n.lineno for n in calls if n.func.attr == "begin"), default=None)
                assert begin is not None, "%s.%s.draw begins no frame" % (mod, cls.name)
                for n in calls:
                    v = n.func.value
                    if (n.func.attr in ("all", "again") and isinstance(v, ast.Attribute)
                            and v.attr == "_damage" and n.lineno > begin):
                        bad.append("%s:%d" % (mod, n.lineno))
    assert not bad, "a verbs app marks its page after the pass: %s" % ", ".join(bad)


def _held_at_the_end(ws, drv, n=40):
    """Hold down past a list's end: how many frames painted after the
    press's own (a held key's steps that move nothing are no frame)."""
    from ws_helpers import quiesce
    for _ in range(80):
        drv.press("down")
        drv.frame(_DT)
        for _ in range(8):
            drv.frame(_DT)
    for _ in range(30):
        quiesce(ws)
        drv.frame(_DT)
    focus = ws.ui_moving
    assert not focus
    drv.hold("down", True)
    drv.press("down")
    drv.frame(_DT)
    k = ws._frames_drawn
    for _ in range(n):
        quiesce(ws)
        drv.frame(_DT)
    drv.hold("down", False)
    return ws._frames_drawn - k


@pytest.mark.parametrize("app", ("settings", "licences", "files"))
def test_a_held_key_at_a_lists_end_paints_nothing(tmp_path, app):
    ws = _desk(tmp_path, app, True)
    drv = make_drv(ws)
    assert _held_at_the_end(ws, drv) == 0


def test_a_held_key_at_the_end_paints_nothing_full_screen(tmp_path):
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    no_unlock_banner(ws)
    ws.bar_layer._clock_text = lambda: "10:30"
    ws.pointer.visible = False
    ws.open_settings()
    drv = make_drv(ws)
    assert _held_at_the_end(ws, drv) == 0


@pytest.mark.parametrize("refill", (True, False))
@pytest.mark.parametrize("bufs", (2, 3))
def test_the_debt_is_paid_while_the_window_buffer_lags(tmp_path, bufs, refill):
    """The intermittent stuck debt (#66: `_full_debt` held at 2 after a
    Blocks-outline fling): a frame that drew straight into the glass leaves
    the window's buffer behind it, and a debt that waited on that buffer
    was never paid. The debt is the last frame's rows, copied, whatever the
    buffer holds; the buffer is refilled after, on a frame with nothing to
    paint, byte-equal to its page on the glass."""
    p = Pair(tmp_path, "settings", bufs)
    w = p.a.wm
    if not refill:
        w.refill_step = lambda: False
    win = p.win("settings")
    _, _, _, ch = win.content_rect()
    p.drag("settings", -ch // 3, steps=3)
    p.settle()
    p.keys("down", 2, gap=0)
    assert win._buf_stale and w._full_debt > 0
    reps = w._repeats_n
    p.settle()
    assert w._full_debt <= 0 and w._repeats_n > reps
    if not refill:
        assert win._buf_stale
        return
    assert not win._buf_stale, "the idle frames never refilled the window's buffer"
    cx, cy, cw, chh = win.content_rect()
    glass = p.glass(p.a)
    W = w._root_canvas.w
    for r in range(chh):
        a = ((cy + r) * W + cx) * 2
        assert bytes(win.buf._buf[r * cw * 2:(r + 1) * cw * 2]) == glass[a:a + cw * 2], r


def test_a_frame_whose_change_is_the_window_repaints_no_desk_or_chrome(tmp_path):
    """docs/scroll_and_text_2026-10.md §8: on the desk with the cursor
    hidden, the press, drag, coast, key and glide frames of an app on the
    verbs each draw straight into the glass (an engagement count per frame
    class) and lay no desk and no chrome; the debt that follows copies the
    window's rows, never the whole glass."""
    p = Pair(tmp_path, "settings", 3)
    w = p.a.wm
    win = p.win("settings")
    cx, cy, cw, ch = win.content_rect()
    seen = {}
    px0 = [w._debt_px, w._repeats_n]

    def frame(cls):
        d0, r0 = p.direct, w._repeats_n
        if p.frame() and w._repeats_n == r0:
            assert p.direct == d0 + 1, "a %s frame did not draw into the glass" % cls
            assert not w._desk_painted and w._chrome_quiet, "a %s frame laid the desk" % cls
            assert w._outside_f != p.a._frames_drawn - 1, "a %s frame drew outside" % cls
            seen[cls] = seen.get(cls, 0) + 1
    x, y = p.at("settings")
    p.both("touch", x, y)
    frame("press")
    for i in range(1, 4):
        p.both("touch_drag", x, y - ch // 6 * i)
        frame("drag")
    p.both("touch_up")
    frame("release")
    for _ in range(40):
        frame("coast")
    p.settle()
    for _ in range(3):
        p.both("press", "down")
        frame("key")
        for _ in range(10):
            frame("glide")
    p.settle()
    assert set(seen) >= {"press", "drag", "coast", "key"}, seen
    n = w._repeats_n - px0[1]
    assert n > 0 and w._debt_px - px0[0] <= n * w._root_canvas.w * ch

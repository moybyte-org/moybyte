# Map (grep -n a name to jump there):
#   MyArtLayout                   the page's rects: the caption, the grid, a desk's foot
#   MyArtAppLayer                 the My Art app: the drawings as cards
#   MyArtAppLayer.draw            the frame: the grid, a card's menu, the pages
#   MyArtAppLayer._verb           a drawing's verbs: open, wallpaper, game, rename, trash
"""My Art -- the drawings' page, which Paint's Open... and Files' Art open
alike, drawn with the public UI API's verbs (docs/ui_api_2026-10.md; the
look's docs/media/look_2026-10/APPS_RESTYLE.md, My Art).

The drawings are cards, three a row on the handheld and five on the desk,
each its drawing at 4:3 read a row at a time from its file
(`artwork.DrawingCovers`) and its name; New drawing is the first card. The
drawing that is the wallpaper wears the `wallpaper` badge, and the caption
says it. Enter (a tap) opens a drawing in Paint; M opens a card's menu --
Open in Paint, Use as wallpaper, Put in a game... (a 320 x 240 drawing),
Rename..., Duplicate and, set apart in the danger ink, Move to Trash -- and
each has its key: W, G, R, Ctrl+D, Del. Move to Trash asks nothing: the
Trash gives a drawing back. On the desk the foot carries the focused card's
verbs. With nothing drawn yet the page says so and offers New drawing.
"""

try:
    import ui
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui

try:
    from layout_base import bar_rows, content_of, metric_class, window_foot, DESK
    from moy_store_base import builtin_name
    from artwork import DrawingCovers, picture_size, GAME_W, GAME_H
    from picture_copies import PictureCopies
    from drawing_pages import GamePage, shown, dot
    from ticks import _ticks_ms, _ticks_diff
except ImportError:  # pragma: no cover - direct host import
    from runtime.layout_base import bar_rows, content_of, metric_class, window_foot, DESK
    from runtime.moy_store_base import builtin_name
    from runtime.artwork import DrawingCovers, picture_size, GAME_W, GAME_H
    from runtime.picture_copies import PictureCopies
    from runtime.drawing_pages import GamePage, shown, dot
    from runtime.ticks import _ticks_ms, _ticks_diff


DRAWINGS = "drawings"
TOAST_MS = 2000
NEW = "New drawing"
# The verbs' keys, typed.
_KEYS = {ord("n"): "new", ord("m"): "menu", ord("w"): "wallpaper", ord("g"): "game",
         ord("r"): "rename", 0x04: "dup", 0x7F: "trash", 0x08: "trash"}


class MyArtLayout:
    """My Art's rects over the content rect: the caption row and the grid,
    the hint band on the handheld; on the desk the window's foot under the
    grid."""

    MIN_W = 300
    MIN_H = 220
    FOOT = 44

    def __init__(self, w, h, fs=1, windowed=False, cs=None):
        self.w = int(w)
        self.h = int(h)
        self.fs = fs = max(1, int(fs))
        self.cs = max(fs, int(cs)) if cs else fs
        self.windowed = bool(windowed)
        self.desk = metric_class() == DESK
        self.cx, self.cy, self.cw, self.ch = content_of(
            self.w, self.h, 0 if windowed else bar_rows(self.cs))
        content = (self.cx, self.cy, self.cw, self.ch)
        self.full = content
        if self.desk:
            under = window_foot() if self.windowed else 0
            self.foot, body = ui.cut_bottom(content, self.FOOT - under)
            bx, by, bw, bh = body
            self.caption = (bx + 24, by + 10, bw - 48, 20)
            self.card = (152, 114)
            self.cols = max(1, (bw + 10) // (152 + 22))
            self.grid = (bx + 6, by + 30, bw - 6, bh - 30)
            self.margin = 26
            self.body = body
        else:
            self.foot, body = ui.cut_bottom(content, 22 * fs)
            bx, by, bw, bh = body
            self.caption = (bx + 16 * fs, by + 4 * fs, bw - 32 * fs, 16 * fs)
            self.card = (88 * fs, 66 * fs)
            self.cols = max(1, (bw - 32 * fs + 12 * fs) // (88 * fs + 12 * fs))
            self.grid = (bx, by + 20 * fs, bw, bh - 20 * fs)
            self.margin = 16
            self.body = body


class MyArtAppLayer:
    """The My Art app: the drawings as cards and their verbs."""

    id = "myart"
    domain = "system"
    TITLE = "My Art"
    # The shell roles this app uses (runtime/app_context.py): the drawings
    # (`files`), Paint's door (`nav`), the wallpaper and a project's copy
    # (runtime/picture_copies.py: `wallpaper`, `carts`), and `artwork`, which
    # follows a drawing Paint has open when it is renamed.
    NEEDS = ("surface", "theme", "damage", "files", "nav", "artwork", "carts",
             "wallpaper")
    UI = True

    def __init__(self, ctx, names):
        self.ctx = ctx
        self._surf = ctx.surface
        self._theme = ctx.theme
        self._damage = ctx.damage
        self._files = ctx.files
        self._nav = ctx.nav
        self._art = ctx.artwork
        self._wall = ctx.wallpaper
        self._ui = ctx.ui
        self.copies = PictureCopies(ctx)
        self.covers = DrawingCovers(ctx.files)
        self.names = names
        cv = ctx.surface.canvas()
        self.layout = MyArtLayout(cv.w, cv.h, self._surf.font_scale(),
                                  self._surf.windowed(), self._surf.chrome_scale())
        self.drawings = []            # names, newest first
        self.sizes = {}               # name -> (w, h), read from its head
        self.wallpaper = None         # the drawing the wallpaper was copied from
        self.cur = None               # the drawing focus is on (None: New drawing)
        self.menu = False             # the focused card's menu is open
        self.renaming = None          # [name, text] while Rename asks
        self.game = None              # Put in a game, over the page
        self.toast = None             # (text, icon, until ms)
        self._focus = None
        self._focused = None
        self._key = 0

    def is_app(self, cart):
        """True only for the shipped My Art identity, not a renamed or copied
        cart."""
        if not cart or cart.get("title") != self.TITLE:
            return False
        path = cart.get("path")
        return not path or builtin_name(path) == "moybyte.my_art.moy"

    def relayout(self, w, h, fs, cs=None):
        self.layout = MyArtLayout(w, h, fs, self._surf.windowed(), cs)

    # -- the drawings -----------------------------------------------------------

    def _refresh(self):
        files = self._files
        names, err = files.list(DRAWINGS) if files.ready() else ([], None)
        self.drawings = list(names or []) if err is None else []
        self.sizes = {}
        self.wallpaper = self._wall_source()

    def _size(self, name):
        got = self.sizes.get(name)
        if got is None:
            blob, err = self._files.load(DRAWINGS, name)
            got = (picture_size(blob) if err is None else None) or (0, 0)
            self.sizes[name] = got
        return got

    def _wall_source(self):
        """The drawing the wallpaper in force was copied from, or None."""
        wp = self.copies.wallpaper_id()
        if wp is None or self._wall.current() != wp:
            return None
        blob, err = self._wall.load_copy()
        if err is not None or not blob:
            return None
        got = self._files.provenance(blob)
        src = got[0] if got else None
        if not src or not str(src).startswith(DRAWINGS + "/"):
            return None
        name = str(src)[len(DRAWINGS) + 1:]
        return name if name in self.drawings else None

    # -- the app protocol (docs/app_api_v1.md) ----------------------------------

    def open(self):
        self.menu = False
        self.renaming = None
        self.game = None
        self.toast = None
        self.covers.forget()
        self._refresh()
        self._ui.reset()
        self._focus = self._card_id(self.drawings[0]) if self.drawings else NEW
        self._damage.all()

    def close(self):
        self.menu = False
        self.renaming = None
        self.game = None

    def has_page(self):
        return self.menu or self.renaming is not None or self.game is not None

    def back(self):
        if self.renaming is not None:
            self.renaming = None
        elif self.menu:
            self.menu = False
        elif self.game is not None:
            self.game = None
        else:
            return False
        self._ui.reset()
        if self.cur is not None:
            self._focus = self._card_id(self.cur)
        self._damage.all()
        return True

    def page_title(self):
        return "Put in a game" if self.game is not None and not self.layout.desk else None

    # -- input ------------------------------------------------------------------

    def handle_input(self, inp):
        k = getattr(inp, "last_key", 0) or 0
        if not isinstance(k, int):
            k = ord(k[0]) if k else 0
        typed = k != 0 and k != self._key
        self._key = k
        if self.toast is not None and (typed or inp.any_pressed()):
            self.toast = None
            self._damage.all()
        if inp.pressed("b"):
            self.back()
            return True
        if typed and not self.has_page():
            lk = k + 32 if 65 <= k <= 90 else k
            verb = _KEYS.get(lk)
            if verb is not None:
                self._verb(verb)
                return True
        if self._ui.keys(inp):
            self._damage.all()
        return True

    def handle_pointer(self, px, py, click):
        down = bool(getattr(self._surf.pointer(), "down", False))
        if self._ui.point(px, py, click, down):
            self._damage.all()
        return True

    # -- verbs ------------------------------------------------------------------

    def _say(self, text, icon="check"):
        self.toast = (text, icon, _ticks_ms() + TOAST_MS)
        self._damage.all()

    def _verb(self, verb, name=None):
        name = name or self.cur
        files = self._files
        if verb == "new":
            got, err = files.new_name(DRAWINGS)
            if err is None:
                self._nav.open_image(got, DRAWINGS)
            return
        if verb == "menu":
            if name is not None:
                self.menu = True
                self._damage.all()
            return
        if name is None:
            return
        if verb == "open":
            self._nav.open_image(name, DRAWINGS)
        elif verb == "wallpaper":
            if self.copies.set_wallpaper(name):
                self.wallpaper = name
                self._say(shown(name) + " is the wallpaper")
            else:
                self._say(self.copies.last_error or "Can't use it", "warn")
        elif verb == "game":
            if self._size(name) == (GAME_W, GAME_H):
                self.game = GamePage(self.copies, self._nav, name)
                self._ui.reset()
                self._focus = "Games"
        elif verb == "rename":
            self.renaming = [name, shown(name)]
            self._ui.reset()
        elif verb == "dup":
            got, err = files.duplicate(DRAWINGS, name)
            if err is None:
                self._refresh()
                self._focus = self._card_id(got) if got else None
                self._say("Duplicated as " + shown(got))
            else:
                self._say(str(err), "warn")
        elif verb == "trash":
            _v, err = files.delete(DRAWINGS, name)
            if err is None:
                i = self.drawings.index(name) if name in self.drawings else 0
                self.covers.forget(name)
                self._refresh()
                nxt = self.drawings[min(i, len(self.drawings) - 1)] if self.drawings else None
                self._focus = self._card_id(nxt) if nxt else NEW
                self._say("Moved " + shown(name) + " to the Trash", "trash")
            else:
                self._say(str(err), "warn")
        self._damage.all()

    def _rename(self, name, text):
        new = str(text).strip().replace(" ", "_").lower()
        if not new or new == name:
            return
        got, err = self._files.rename(DRAWINGS, name, new)
        if err is not None:
            self._say(str(err), "warn")
            return
        new = got or new
        self._art.follow(DRAWINGS, name, new)
        self.covers.forget(name)
        self._refresh()
        self._focus = self._card_id(new)

    # -- draw -------------------------------------------------------------------

    def _card_id(self, name):
        return "Drawing " + str(name)

    def draw(self, dt):
        if self.toast is not None:
            if _ticks_diff(_ticks_ms(), self.toast[2]) >= 0:
                self.toast = None
            else:
                self._damage.again()
        cv = self._surf.canvas()
        lay = self.layout
        th = self._theme.colors()
        self._ui.begin(cv, th, (0, 0, cv.w, cv.h), lay.desk, lay.windowed)
        ui.page("")
        was = (self.menu, self.renaming is None, self.game is None, len(self.drawings))
        if self._focus is not None:
            ui.focus(self._focus)
            self._focus = None
        if self.game is not None:
            ui.bar("Put in a game" if not lay.desk else "", (), (), True)
            got = self.game.draw(cv, lay.full if not lay.desk else lay.body, lay.desk, lay.fs)
            if got is not None:
                name = self.game.name
                self.game = None
                if got:
                    self._say(shown(name) + " is the background of " + got)
                else:
                    self._say(self.copies.last_error or "Can't put it there", "warn")
        elif not self.drawings:
            self._empty()
        else:
            self._grid(cv)
            if lay.desk:
                self._foot(cv)
            elif ui.hints_width(("back", "Back")) > 0:
                ui.at(*lay.foot)
                if self.menu:
                    ui.hints(("back", "Close"))
                else:
                    ui.hints(("confirm", "Open", "N", "New", "M", "Menu", "back", "Back"))
            if self.menu:
                self._menu()
        self._rename_dialog()
        if self.toast is not None:
            self._draw_toast()
        f = ui.focus()
        if f != self._focused:
            self._focused = f
            if f and f.startswith("Drawing "):
                self.cur = f[len("Drawing "):]
            elif f == NEW:
                self.cur = None
        ui.end()
        if (self.menu, self.renaming is None, self.game is None, len(self.drawings)) != was:
            self._damage.again()

    def _empty(self):
        lay = self.layout
        fs = lay.fs
        x, y, w, h = lay.full
        top = y + (h // 6 if lay.desk else 10 * fs)
        ui.at(x, top, w, h)
        at = ui.empty("art", "No drawings yet", ("Draw something in Paint", "and it lands here."))
        bw, bh = (180, 40) if lay.desk else (124 * fs, 26 * fs)
        ui.at(x + (w - bw) // 2, at + (6 if lay.desk else 3 * fs), bw, bh)
        ui.icon("new")
        if ui.button(NEW, "primary", {"id": NEW}):
            self._verb("new")
        if not lay.desk and ui.hints_width(("back", "Back")) > 0:
            ui.at(*lay.foot)
            ui.hints(("confirm", NEW, "back", "Back"))

    def _grid(self, cv):
        lay = self.layout
        n = len(self.drawings)
        left = "%d drawing%s" % (n, "" if n == 1 else "s")
        x, y, w, h = lay.caption
        ui.at(x, y, w, h)
        ui.label(left, {"tone": "dim", "face": "caption"})
        if self.wallpaper is not None:
            right = "Wallpaper: " + shown(self.wallpaper)
            ui.face("caption")
            rw = ui.text_size(right)[0]
            ui.face()
            ui.at(x + w - rw, y, rw, h)
            ui.label(right, {"tone": "dim", "face": "caption"})
        ui.at(*lay.grid)
        ui.scroll("drawings")
        cw, ch = lay.card
        ui.grid("drawings", cw, ch, {"cols": lay.cols, "margin": lay.margin,
                                     "foot": 30 if lay.desk else 26, "top": 4})
        x0, y0, w0, h0, st = ui.card(NEW, "", {"id": NEW, "icon": "new", "tone": "value"})
        if st & ui.ACTIVATED:
            self._verb("new")
        for name in self.drawings:
            o = {"id": self._card_id(name)}
            if self.menu and name == self.cur:
                o["current"] = True
            if name == self.wallpaper:
                o["badge"] = "wallpaper"
            cx, cy, cw_, ch_, st = ui.card(shown(name), "", o)
            if cw_:
                pic = self.covers.get(name, cw_, ch_)
                if pic is not None:
                    cv.clip(*lay.grid)
                    cv.picture(pic, cx, cy, cw_, ch_)
                    cv.clip()
            if st & ui.ACTIVATED and not self.menu:
                self._verb("open", name)
        ui.grid()
        ui.end()
        ui.scroll()

    def _menu_rows(self, keys):
        name = self.cur
        rows = [("open", "Open in Paint", "paint", "Enter"),
                ("wallpaper", "Use as wallpaper", "wallpaper", "W")]
        if name is not None and self._size(name) == (GAME_W, GAME_H):
            rows.append(("game", "Put in a game…" if ui.has_symbols() else "Put in a game...",
                         "export", "G"))
        rows.append(("rename", "Rename…" if ui.has_symbols() else "Rename...", "pencil", "R"))
        rows.append(("dup", "Duplicate", "dup", "Ctrl D"))
        if not keys:
            rows = [(r[0], r[1], r[2], "") for r in rows]
        return rows

    def _menu(self):
        lay = self.layout
        name = self.cur
        if name is None:
            self.menu = False
            return
        box = self._ui.box(self._card_id(name))
        u = 1 if lay.desk else lay.fs
        width = 260 if lay.desk else 186 * u
        if box is not None:
            # a point at the card's top: the menu starts there and slides to fit
            ui.at(box[0], box[1] - 8 * u, width, 0)
        keys = ui.hints_width(("back", "Back")) > 0
        rows = self._menu_rows(keys)
        shown_ = ui.popover("Card menu", width, {"wash": True})
        pick = None
        got = ui.menu(shown(name), [r[1] + ("\t" + r[3] if r[3] else "") for r in rows],
                      [r[2] for r in rows])
        if got >= 0:
            pick = rows[got][0]
        got = ui.menu("", ["Move to Trash" + ("\tDel" if keys else "")], ["trash"],
                      kinds=["danger"])
        if got >= 0:
            pick = "trash"
        ui.popover()
        if pick is not None:
            self.menu = False
            self._ui.reset()
            self._verb(pick, name)
            if pick in ("wallpaper", "dup", "trash"):
                pass
            elif pick != "rename" and pick != "game" and self.cur is not None:
                self._focus = self._card_id(self.cur)
        elif not shown_:
            self.menu = False
            if self.cur is not None:
                self._focus = self._card_id(self.cur)
            self._damage.all()

    def _foot(self, cv):
        """The window's foot: the focused drawing's name, size and day at its
        left, its verbs at its right, Open last and primary."""
        lay = self.layout
        fx, fy, fw, fh = lay.foot
        th = self._theme.colors()
        cv.rect(fx + 16, fy, fw - 32, 1, th["border"])
        name = self.cur
        verbs = [("Move to Trash", "trash", "danger", "trash"),
                 ("Use as wallpaper", "wallpaper", "normal", "wallpaper")]
        if name is not None and self._size(name) == (GAME_W, GAME_H):
            verbs.append(("Put in a game", "export", "normal", "game"))
        verbs.append(("Open", "paint", "primary", "open"))
        if name is None:
            verbs = [(NEW, "new", "primary", "new")]
        ui.face("caption")
        widths = [ui.text_size(v[0])[0] + 48 for v in verbs]
        ui.face()
        bx = fx + fw - 16 - sum(widths) - 8 * (len(verbs) - 1)
        by = fy + max(1, (fh - 30) // 2)
        if name is not None:
            w, h = self._size(name)
            status = shown(name) + dot() + ("%d × %d" % (w, h) if ui.has_symbols()
                                            else "%dx%d" % (w, h))
        else:
            status = "%d drawings" % len(self.drawings)
        ui.at(fx + 26, by, max(0, bx - fx - 34), 30)
        ui.label(status, {"tone": "dim", "face": "caption"})
        for i, (label, icon, kind, verb) in enumerate(verbs):
            ui.at(bx, by, widths[i], 30)
            ui.icon(icon)
            if ui.button(label, kind, {"id": "Foot " + verb}):
                self._verb(verb)
            bx += widths[i] + 8

    def _rename_dialog(self):
        if self.renaming is None:
            return
        name, text = self.renaming
        pick = ui.dialog("Rename " + shown(name), ("Cancel", "Rename"))
        text, done = ui.field("Name", text, 32, {"id": "Rename field"})
        self.renaming[1] = text
        ui.dialog()
        if pick == 1 or done:
            self.renaming = None
            self._rename(name, text)
            self._damage.all()
        elif pick == 0:
            self.renaming = None
            self._focus = self._card_id(name)
            self._damage.all()

    def _draw_toast(self):
        lay = self.layout
        text, icon, _until = self.toast
        if lay.desk:
            x, y, w, h = lay.body
            ui.at(x, y, w, h - 12)
        else:
            x, y, w, h = lay.body
            ui.at(x, y, w, h - 6 * lay.fs)
        ui.toast(text, icon)

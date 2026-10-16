# Map (grep -n a name to jump there):
#   AppearanceLayout                 the page's geometry: wide (THEME's grid) or not
#   AppearanceAppLayer.choices       what the rows show, once per token generation
#   AppearanceAppLayer._step         ←→ on a row
#   AppearanceAppLayer._activate     ENTER on a row
#   AppearanceAppLayer._try          a picker's cursor: tried live
#   AppearanceAppLayer._undo         B in a picker: what was there
#   AppearanceAppLayer.handle_input  keys
#   AppearanceAppLayer.handle_pointer  taps
#   AppearanceAppLayer._pick_frame   a picker's rect: popover or page
#   AppearanceAppLayer.draw          the page
"""The Appearance page: one page of rows (docs/theming_2026-09.md §9 phase
6 step 7, built to the mockups in docs/media/theming_2026-09/appearance/).

THEME, MODE, ACCENT, TINT, WALLPAPER, WINDOW STYLE, FONT, CHROME SIZE (where
the panel takes more than one scale) and EDIT FILE…, Settings-shaped. A page
with the room draws THEME as a grid of cards above the rows; a small one
draws it as a row whose picker is the grid. ↑↓ walk the rows, ←→ step the
focused one in place, ENTER opens its picker (or acts), B (Backspace, Esc)
backs out of a picker restoring what was there; a tap is ENTER on the thing
tapped.

A picker -- THEME's grid, ACCENT's sixteen swatches, TINT's ramps,
WALLPAPER's catalogue -- is a popover the page draws over its own rows in
desktop mode, under its row, and a page of its own in console mode
(`docs/shell_layout_2026-10.md` L5's rule for which). It never leaves the
window, so the window's own damage carries it. Moving the cursor in ACCENT,
TINT or WALLPAPER tries the value live; ENTER keeps it, B restores what was
there (the theme the picker opened on, when the try made `custom` of it).

Every row but THEME, MODE, CHROME SIZE, EDIT FILE… and NAME… is a change to
the theme (`ctx.theme.change`), which lands in `custom`. The rows read
`ctx.theme.choices()` once per token generation.

NAME… (on `custom` only) opens a text field on the keyboard in text mode
(`ctx.nav.text_mode`): ENTER renames `custom` to the name typed
(`ctx.theme.name_custom`), a theme of its own in the grid; a name an
installed theme has arms REPLACE? and a second ENTER replaces it; ESC backs
out.
"""

try:
    from layout_base import bar_rows, content_of
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.layout_base import bar_rows, content_of

try:
    from moy_store_base import BUILTIN_NS, builtin_name
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_store_base import BUILTIN_NS, builtin_name

try:
    from chrome import THEME_NAMES, theme_colors, theme_names, RGB, rgb565_of, nearest_of
except ImportError:  # pragma: no cover - direct host import
    from runtime.chrome import (THEME_NAMES, theme_colors, theme_names, RGB, rgb565_of,
                                nearest_of)

try:
    from editors_base import TE_CANCEL, TE_COMMIT, TE_EDIT, TextEntry
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.editors_base import TE_CANCEL, TE_COMMIT, TE_EDIT, TextEntry

try:
    import ui as _ui
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui as _ui
_in = _ui.rect_in   # one hit-test (ui.rect_in)

# The theme's picture's wallpaper id (`Appearance.PICTURE`), and the change
# value that puts the source theme's own wallpaper back (`Appearance.change`).
PICTURE = BUILTIN_NS + ".my_art"
THEIRS = ""

LABELS = {"theme": "THEME", "mode": "MODE", "accent": "ACCENT",
          "tint": "TINT", "wallpaper": "WALLPAPER", "frame": "WINDOW STYLE",
          "font": "FONT", "chrome": "CHROME SIZE", "file": "EDIT FILE...",
          "name": "NAME..."}
# The WINDOW STYLE and FONT presets in their stepping order (the look's
# FRAMES and FONTS), and their labels.
FRAMES = ("classic", "tab", "platinum", "win9x", "flat")
FONTS = ("tiny5", "petme128")
FRAME_NAMES = {"classic": "CLASSIC", "tab": "TABS", "platinum": "PLATINUM",
               "win9x": "WIN9X", "flat": "FLAT"}
FONT_NAMES = {"tiny5": "TINY5", "petme128": "PETME128"}
HINTS = {"accent": "ONE COLOUR FOR TITLES, SELECTION AND FOCUS.",
         "tint": "ONE TINT FOR PANELS, DESK AND BAR.",
         "wallpaper": "THE WALLPAPER IS PART OF THE THEME.",
         "theme": "PICK A THEME.",
         "name": "NAME YOUR THEME."}
ROW_KEYS = "UP/DN row  L/R change  ENTER open"
PICK_KEYS = "ARROWS try  ENTER keep  B undo"
GRID_KEYS = "ARROWS move  ENTER use  B back"
NAME_KEYS = "TYPE  ENTER keep  ESC back"
# NAME…'s refusals (`ctx.theme.name_check`), and the REPLACE? arm.
NAME_WHY = {"grammar": "a-z 0-9 _ -, A LETTER OR DIGIT FIRST",
            "seed": "A BUILT-IN THEME HAS THAT NAME",
            "custom": "CUSTOM IS THE ONE BEING CHANGED",
            "cart": "A CART HAS THAT NAME",
            "store": "CAN'T SAVE HERE",
            "none": "ONLY CUSTOM IS NAMED"}
NAME_HINT = "a-z 0-9 _ -, UP TO 24"



def _fill(cv, x, y, w, h, v):
    """A rect in colour value `v`: a palette index (the live theme's block
    included) through the canvas, an RGB value as its RGB565 word, which
    takes no slot: how a card shows another theme's colours."""
    if v < RGB:
        cv.rect(x, y, w, h, v)
        return
    fill = getattr(cv, "fill565", None)
    if fill is None:                    # a canvas that takes indices only
        cv.rect(x, y, w, h, nearest_of(v))
        return
    fill(x, y, w, h, rgb565_of(v))


def _frame(cv, x, y, w, h, v):
    if v < RGB:
        cv.rectb(x, y, w, h, v)
        return
    _fill(cv, x, y, w, 1, v)
    _fill(cv, x, y + h - 1, w, 1, v)
    _fill(cv, x, y, 1, h, v)
    _fill(cv, x + w - 1, y, 1, h, v)

class AppearanceLayout:
    """The page's geometry for one content size. `wide` puts THEME's grid
    above the rows; otherwise THEME is the first row and its picker is the
    grid. Rows that do not fit scroll (`AppearanceAppLayer._scroll_to`)."""

    # Min-size convention (ui.py): the WM clamps window resizes to these
    # (fs-scaled).
    MIN_W = 310
    MIN_H = 230

    def __init__(self, w, h, fs=1, windowed=False, cs=None):
        self.w = int(w)
        self.h = int(h)
        self.fs = max(1, int(fs))
        fs = self.fs
        # The chrome scale (#203) sizes the rows (a tap target) and the OS
        # bar band above a fullscreen page; a window has no bar over it.
        self.cs = max(fs, int(cs)) if cs else fs
        cs = self.cs
        self.windowed = bool(windowed)
        self.cx, self.cy, self.cw, self.ch = content_of(
            self.w, self.h, 0 if windowed else bar_rows(cs))
        self.row_h = max(20 * fs, 13 * cs)
        self.strip_h = 12 * fs if self.cw < 480 * fs else 16 * fs
        self.pad = 4 * fs
        # THEME's grid cards: the mockups' 86x54 on a wide page.
        self.card_w = 86 * fs
        self.card_h = 54 * fs
        self.gap = 7 * fs
        self.cols = max(1, (self.cw - 20 * fs + self.gap)
                        // (self.card_w + self.gap))
        self.label_h = 12 * fs
        grid_h = self.label_h + 2 * (self.card_h + self.gap) + 4 * fs
        self.wide = (self.windowed and self.cols >= 4
                     and 8 * fs + grid_h + 9 * self.row_h + self.strip_h
                     <= self.ch)
        self.grid_rows = 2
        top = self.cy + (8 * fs if self.wide else 0)
        self.grid_y = top + self.label_h
        self.rows_y = top + (grid_h if self.wide else 0)
        self.strip_y = self.cy + self.ch - self.strip_h
        self.rows_h = max(1, self.strip_y - self.rows_y)
        self.theme_h = max(34 * fs, self.row_h)    # the compact THEME row

    def card_rect(self, i, top=0):
        """Grid card `i`'s rect on a wide page, `top` the first grid row
        shown; None for a card scrolled out."""
        r = i // self.cols - top
        if r < 0 or r >= self.grid_rows:
            return None
        return (self.cx + 10 * self.fs + (i % self.cols) * (self.card_w + self.gap),
                self.grid_y + r * (self.card_h + self.gap),
                self.card_w, self.card_h)


class AppearanceAppLayer:
    """The Appearance page (see the module doc)."""

    id = "appearance"
    domain = "system"
    TITLE = "APPEARANCE"
    # The shell roles this app uses (runtime/app_context.py). `wallpaper` is a
    # CAPABILITY, not a core role: this app and the two that copy a drawing
    # into it (Paint, Files) are its only consumers. A pick is a change to
    # the theme (docs/theming_2026-09.md D6), so it lands in `custom`.
    NEEDS = ("surface", "theme", "damage", "wallpaper", "nav")

    def __init__(self, ctx, names):
        self.ctx = ctx
        # Roles bound ONCE (the hoist mandate, ui_refactor_2026-08 Section 2.4).
        self._surf = ctx.surface
        self._theme = ctx.theme
        self._damage = ctx.damage
        self._wall = ctx.wallpaper
        self._nav = ctx.nav
        self.names = names
        cv = ctx.surface.canvas()
        self.layout = AppearanceLayout(cv.w, cv.h, self._surf.font_scale(),
                                       self._surf.windowed(),
                                       self._surf.chrome_scale())
        self.zone = "rows"          # "grid" (a wide page's cards) or "rows"
        self.row = 0
        self.card = 0
        self.top = 0                # the first grid row a wide page shows
        self.scroll = 0             # the rows' scroll, px
        self.pick = None            # the open picker's row key
        self.cur = 0                # its cursor
        self.orig = None            # what it opened on: (choices, colours)
        self.tried = False
        # NAME…'s field, the name REPLACE? is armed for, the refusal shown,
        # and the strip's note once a name is kept.
        self.entry = TextEntry(24, self._name_char)
        self.armed = None
        self.why = None
        self.note = None
        self._ch = None
        self._gen = None
        self._stale = 0
        self._dt = 0
        self._accents = tuple(_ui.theme_accents())
        self._tints = tuple(_ui.theme_tints())

    @staticmethod
    def is_app(cart):
        if (not cart or cart.get("title") != "Appearance"
                or "appearance" not in (cart.get("permissions") or ())):
            return False
        path = cart.get("path")
        if not path:
            return int(cart.get("version", 0)) >= 1
        return builtin_name(path) == "moybyte.appearance.moy"

    def relayout(self, w, h, fs, cs=None):
        self.layout = AppearanceLayout(w, h, fs, self._surf.windowed(), cs)
        self._stale = 2

    def open(self):
        self.pick = None
        self.zone = "rows"
        self.row = self._rows().index("accent")
        self.scroll = 0
        self._stale = 2
        self._damage.all()

    # -- what the rows show ----------------------------------------------------

    def choices(self):
        """What the rows show (`ctx.theme.choices()`), as `_refresh` last
        read it."""
        if self._ch is None:
            self._refresh()
        return self._ch

    def _refresh(self):
        """Read `ctx.theme.choices()` again when the token generation moved,
        and for the two frames after an action of the page's own (a change
        shows at the next frame). Once per draw and per event. The focused
        row stays a row: NAME… goes with `custom`, under any change."""
        g = self._theme.gen()
        if self._ch is None or g != self._gen or self._stale:
            self._ch = self._theme.choices()
            self._gen = g
            if self._stale:
                self._stale -= 1
            self.row = min(self.row, len(self._rows()) - 1)

    def _rows(self):
        out = [] if self.layout.wide else ["theme"]
        out += ["mode", "accent", "tint", "wallpaper", "frame", "font"]
        if len(self.choices()["chromes"]) > 1:
            out.append("chrome")
        out.append("file")
        if self.choices()["theme"] == "custom":
            out.append("name")
        return out

    def _themes(self):
        """The THEME grid's names: `custom` first, then the store's
        packages (a named theme among them), then the seeds
        (`chrome.theme_names`)."""
        names = list(theme_names())
        mine = [n for n in names if n not in THEME_NAMES]
        if "custom" in mine:
            mine.remove("custom")
            mine.insert(0, "custom")
        return mine + [n for n in names if n in THEME_NAMES]

    def _accent_at(self, a):
        for i, acc in enumerate(self._accents):
            if acc[1] == a:
                return i
        return -1

    def _accent_shown(self, ch, th):
        """(index into the accents, or -1 for the theme's own, A shown)."""
        a = ch["accent"]
        if a is None:
            a = th["accent"]
        return self._accent_at(a), th["accent"]

    def _tint_at(self, name):
        for i, t in enumerate(self._tints):
            if t[0] == name:
                return i
        return -1

    def _walls(self):
        """The WALLPAPER catalogue: (value, label, kind, title), kind "cart",
        "art" (Paint's picture), "theirs" (the source theme's own picture)
        or "none"."""
        ch = self.choices()
        out = []
        if ch["picture"] == "theme":
            out.append((THEIRS, "THEME'S PICTURE", "theirs", ""))
        art = None
        for c in self._wall.carts():
            wid = self._wall.id_for(c)
            title = str(c.get("title", "WALLPAPER"))
            if title == "My Art":
                art = (wid, "MY ART", "art", title)
            else:
                out.append((wid, title.upper(), "cart", title))
        if art is not None:
            out.append(art)
        out.append((None, "NONE", "none", ""))
        return out

    def _wall_at(self, ch):
        wp = ch["wallpaper"]
        walls = self._walls()
        for i, w in enumerate(walls):
            if w[2] == "theirs" and wp == PICTURE:
                return i
            if w[2] == "art" and wp == PICTURE and ch["picture"] == "mine":
                return i
            if w[2] in ("cart", "none") and w[0] == wp:
                return i
        return -1

    def _wall_title(self, ch):
        wp = ch["wallpaper"]
        if wp is None:
            return "NONE"
        if wp == PICTURE:
            return "MY ART" if ch["picture"] == "mine" else "THEME'S PICTURE"
        t = self._wall.title(wp)
        return str(t).upper() if t else "WALLPAPER"

    # -- acting ----------------------------------------------------------------

    def _acted(self):
        self.note = None
        self._stale = 2
        self._damage.all()

    def _change(self, kind, value):
        rc = self._theme.change(kind, value)
        self._acted()
        return rc

    def _step(self, key, d):
        """←→ on row `key`: step its value in place."""
        ch = self.choices()
        if key == "theme":
            names = self._themes()
            at = names.index(ch["theme"]) if ch["theme"] in names else 0
            self._theme.set(names[(at + d) % len(names)])
        elif key == "mode":
            self._theme.set_variant("light" if ch["variant"] == "dark" else "dark")
        elif key == "accent":
            at = self._accent_shown(ch, self._theme.colors())[0]
            at = (at + d) % len(self._accents) if at >= 0 else 0
            self._change("accent", self._accents[at][1])
            return
        elif key == "tint":
            opts = [None] + [t[0] for t in self._tints]
            at = opts.index(ch["tint"]) if ch["tint"] in opts else 0
            self._change("tint", opts[(at + d) % len(opts)])
            return
        elif key == "wallpaper":
            walls = self._walls()
            at = self._wall_at(ch)
            for _ in range(len(walls)):
                at = (at + d) % len(walls)
                if self._change("wallpaper", walls[at][0]) == 0:
                    break
            return
        elif key == "frame":
            opts = FRAMES
            at = opts.index(ch["frame"]) if ch["frame"] in opts else 0
            self._change("frame", opts[(at + d) % len(opts)])
            return
        elif key == "font":
            opts = FONTS
            at = opts.index(ch["font"]) if ch["font"] in opts else -1
            self._change("font", opts[(at + d) % len(opts)])
            return
        elif key == "chrome":
            opts = ch["chromes"]
            at = opts.index(ch["chrome"]) if ch["chrome"] in opts else 0
            self._theme.set_chrome_scale(opts[(at + d) % len(opts)])
        else:
            return
        self._acted()

    def _activate(self, key, seed=0):
        """ENTER on row `key`."""
        if key in ("theme", "accent", "tint", "wallpaper"):
            self._open_picker(key)
        elif key == "file":
            self._theme.edit_file()
            self._acted()
        elif key == "name":
            self._open_name(seed)
        else:
            self._step(key, 1)

    # -- NAME… -----------------------------------------------------------------

    @staticmethod
    def _name_char(c, text):
        """The name grammar's characters (§3), a letter or a digit first."""
        if "A" <= c <= "Z":
            return False
        if c in "_-":
            return bool(text)
        return "a" <= c <= "z" or "0" <= c <= "9"

    def _open_name(self, seed=0):
        """NAME…: the field, empty, its edge guarded so the ENTER that
        opened it is not its first key; the keyboard types."""
        self.entry.open("", seed, guard=True)
        self.armed = None
        self.why = None
        self.pick = "name"
        self.orig = None
        self.tried = False
        self._nav.text_mode(True)
        self._damage.all()

    def _close_name(self):
        self.pick = None
        self.armed = None
        self.why = None
        self._nav.text_mode(False)
        self._acted()

    def _name_input(self, inp):
        ev = self.entry.feed(inp)
        if ev == TE_COMMIT:
            self._name_commit()
        elif ev == TE_CANCEL:
            self._close_name()
        elif ev == TE_EDIT:
            self.armed = None
            self.why = None
            self._damage.all()
        return True

    def _name_commit(self):
        """ENTER in NAME…: rename `custom` to the name typed; a name an
        installed theme has arms REPLACE?, and a second ENTER on the same
        name replaces it."""
        name = self.entry.text
        why = self._theme.name_check(name)
        replace = why == "taken" and self.armed == name
        if why == "taken" and not replace:
            self.armed = name
            self.why = None
            self._damage.all()
            return
        if why is None or replace:
            why = self._theme.name_custom(name, replace)
        if why is not None:
            self.armed = None
            self.why = why
            self._damage.all()
            return
        self._close_name()
        self.note = name + ".moy IS A THEME NOW"
        self._refresh()

    def _open_picker(self, key):
        ch = self.choices()
        th = self._theme.colors()
        if key == "theme":
            names = self._themes()
            self.cur = names.index(ch["theme"]) if ch["theme"] in names else 0
        elif key == "accent":
            self.cur = max(0, self._accent_shown(ch, th)[0])
        elif key == "tint":
            self.cur = self._tint_at(ch["tint"]) + 1
        else:
            self.cur = max(0, self._wall_at(ch))
        self.pick = key
        self.orig = dict(ch)
        self.tried = False
        self._damage.all()

    def _pick_count(self):
        if self.pick == "theme":
            return len(self._themes())
        if self.pick == "accent":
            return len(self._accents)
        if self.pick == "tint":
            return len(self._tints) + 1
        return len(self._walls())

    def _pick_cols(self):
        if self.pick == "accent":
            return 8
        if self.pick == "theme":
            return self._grid_cols()
        return 3

    def _try(self, i):
        """The picker's cursor moved to `i`: ACCENT, TINT and WALLPAPER try
        it live; THEME's grid only moves its cursor."""
        self.cur = i
        if self.pick == "accent":
            self._change("accent", self._accents[i][1])
            self.tried = True
        elif self.pick == "tint":
            self._change("tint", None if i == 0 else self._tints[i - 1][0])
            self.tried = True
        elif self.pick == "wallpaper":
            self._change("wallpaper", self._walls()[i][0])
            self.tried = True
        self._damage.all()

    def _keep(self, i=None):
        """ENTER in a picker: keep (THEME: use) the value under the cursor."""
        if i is not None and i != self.cur:
            self._try(i)
        if self.pick == "theme":
            names = self._themes()
            if 0 <= self.cur < len(names):
                self._theme.set(names[self.cur])
        self.pick = None
        self._acted()

    def _undo(self):
        """B in a picker: what was there before it opened. A try that made
        `custom` of another theme goes back to that theme; on `custom` the
        tried value is changed back."""
        o, key = self.orig, self.pick
        self.pick = None
        if self.tried and o is not None:
            if o["theme"] != "custom":
                self._theme.set(o["theme"])
            elif key == "accent" or key == "tint":
                self._theme.change(key, o[key])
            elif key == "wallpaper":
                wp = o["wallpaper"]
                if wp == PICTURE and o["picture"] == "theme":
                    wp = THEIRS
                self._theme.change("wallpaper", wp)
        self._acted()

    # -- input -----------------------------------------------------------------

    def handle_input(self, inp):
        self._refresh()
        if self.pick == "name":
            return self._name_input(inp)
        if self.pick is not None:
            return self._picker_input(inp)
        if inp.pressed("b"):
            return True
        rows = self._rows()
        if self.zone == "grid":
            n = len(self._themes())
            cols = self.layout.cols
            if inp.pressed("left"):
                self.card = (self.card - 1) % n
            elif inp.pressed("right"):
                self.card = (self.card + 1) % n
            elif inp.pressed("up") and self.card >= cols:
                self.card -= cols
            elif inp.pressed("down"):
                if self.card + cols < n:
                    self.card += cols
                elif self.card // cols < (n - 1) // cols:
                    self.card = n - 1
                else:
                    self.zone = "rows"
                    self.row = 0
            elif inp.pressed("a"):
                self._theme.set(self._themes()[self.card])
                self._acted()
                return True
            else:
                return True
            self._show_card()
            self._damage.all()
            return True
        self.row = min(self.row, len(rows) - 1)
        if inp.pressed("up"):
            if self.row > 0:
                self.row -= 1
            elif self.layout.wide:
                self.zone = "grid"
                names = self._themes()
                at = names.index(self.choices()["theme"]) \
                    if self.choices()["theme"] in names else 0
                self.card = at
                self._show_card()
        elif inp.pressed("down"):
            self.row = min(len(rows) - 1, self.row + 1)
        elif inp.pressed("left"):
            self._step(rows[self.row], -1)
        elif inp.pressed("right"):
            self._step(rows[self.row], 1)
        elif inp.pressed("a"):
            self._activate(rows[self.row], inp.last_key)
        else:
            return True
        self._damage.all()
        return True

    def _picker_input(self, inp):
        n = self._pick_count()
        cols = self._pick_cols()
        i = self.cur
        if inp.pressed("b"):
            self._undo()
            return True
        if inp.pressed("a"):
            self._keep()
            return True
        if inp.pressed("left"):
            i = (i - 1) % n
        elif inp.pressed("right"):
            i = (i + 1) % n
        elif inp.pressed("up"):
            i = i - cols if i >= cols else i
        elif inp.pressed("down"):
            i = i + cols if i + cols < n else i
        else:
            return True
        if i != self.cur:
            self._try(i)
        return True

    def _show_card(self):
        r = self.card // self.layout.cols
        if r < self.top:
            self.top = r
        elif r >= self.top + self.layout.grid_rows:
            self.top = r - self.layout.grid_rows + 1

    def handle_pointer(self, px, py, click):
        if not click:
            return True
        self._refresh()
        if self.pick == "name":
            if not _in(px, py, self._pick_frame()):
                self._close_name()             # a tap outside: backed out
            return True
        if self.pick is not None:
            rects = self._pick_rects()
            for i, r in enumerate(rects):
                if r is not None and _in(px, py, r):
                    self._keep(i)
                    return True
            if not _in(px, py, self._pick_frame()):
                self._keep()                   # a tap outside: closed, kept
            return True
        lay = self.layout
        if lay.wide:
            for i in range(len(self._themes())):
                r = lay.card_rect(i, self.top)
                if r is not None and _in(px, py, r):
                    self.zone, self.card = "grid", i
                    self._theme.set(self._themes()[i])
                    self._acted()
                    return True
        for key, r in self._row_rects():
            if not _in(px, py, r):
                continue
            self.zone = "rows"
            self.row = self._rows().index(key)
            if key == "mode":
                for v, cr in self._mode_chips(r):
                    if _in(px, py, cr):
                        if v != self.choices()["variant"]:
                            self._theme.set_variant(v)
                        self._acted()
                        return True
                self._acted()
                return True
            if key in ("frame", "font", "chrome") and px < self._cycle_x(r) + 8 * lay.fs:
                self._step(key, -1)
                return True
            self._activate(key)
            return True
        return True

    # -- geometry --------------------------------------------------------------

    def _row_h(self, key):
        return self.layout.theme_h if key == "theme" else self.layout.row_h

    def _row_rects(self):
        """(key, rect) of every row on screen, after the scroll."""
        lay = self.layout
        out = []
        y = lay.rows_y - self.scroll
        x, w = lay.cx + lay.pad, lay.cw - 2 * lay.pad
        for key in self._rows():
            h = self._row_h(key)
            if y + h > lay.rows_y and y < lay.strip_y:
                out.append((key, (x, y, w, h)))
            y += h
        return out

    def _scroll_to(self):
        """Keep the focused row inside the rows' band."""
        lay = self.layout
        y = 0
        for i, key in enumerate(self._rows()):
            h = self._row_h(key)
            if i == self.row:
                if y < self.scroll:
                    self.scroll = y
                elif y + h > self.scroll + lay.rows_h:
                    self.scroll = y + h - lay.rows_h
                return
            y += h

    def _mode_chips(self, r):
        fs = self.layout.fs
        x, y, w, h = r
        cw, chh = 36 * fs, 13 * fs
        cx = x + w - 8 * fs - 2 * cw - 4 * fs
        cy = y + (h - chh) // 2
        return (("dark", (cx, cy, cw, chh)), ("light", (cx + cw + 4 * fs, cy, cw, chh)))

    def _cycle_x(self, r):
        """The left edge of a stepped row's `< value >`."""
        return r[0] + r[2] - 8 * self.layout.fs - self._cycle_w(r)

    def _cycle_w(self, r):
        return min(92 * self.layout.fs, r[2] // 3)

    def _grid_cols(self):
        lay = self.layout
        if self._console():
            return max(1, (lay.cw - 8 * lay.fs) // (98 * lay.fs + 6 * lay.fs))
        return 3

    def _console(self):
        """A picker is a page (console mode), not a popover (desktop mode)."""
        return not self.layout.windowed

    def _pick_size(self):
        """(w, h) of the open picker's popover."""
        fs = self.layout.fs
        if self.pick == "accent":
            return 296 * fs, 168 * fs
        if self.pick == "tint":
            return 326 * fs, 196 * fs
        if self.pick == "theme":
            return 3 * 98 * fs + 4 * 6 * fs, 3 * 64 * fs + 30 * fs
        if self.pick == "name":
            return 320 * fs, 162 * fs
        rows = (len(self._walls()) + 2) // 3
        return 3 * 100 * fs + 16 * fs, rows * 76 * fs + 36 * fs

    def _pick_frame(self):
        """The picker's rect: the content (less the strip) as a page, else
        a popover under its row, above it when it does not fit below,
        clamped inside the page."""
        lay = self.layout
        if self._console():
            return (lay.cx, lay.cy, lay.cw, lay.strip_y - lay.cy)
        w, h = self._pick_size()
        w, h = min(w, lay.cw - 2 * lay.pad), min(h, lay.strip_y - lay.cy)
        anchor = None
        for key, r in self._row_rects():
            if key == self.pick:
                anchor = r
        if anchor is None:
            anchor = (lay.cx, lay.rows_y, lay.cw, 0)
        x = anchor[0] + anchor[2] - w
        y = anchor[1] + anchor[3]
        if y + h > lay.strip_y:
            y = max(lay.cy, anchor[1] - h)
        return (max(lay.cx, x), max(lay.cy, min(y, lay.strip_y - h)), w, h)

    def _pick_body(self):
        """The picker's inner rect, under its title (a popover) or its hint
        line (a page)."""
        fs = self.layout.fs
        x, y, w, h = self._pick_frame()
        top = 12 * fs if self._console() else 13 * fs
        return (x + 8 * fs, y + top + 6 * fs, w - 16 * fs, h - top - 12 * fs)

    def _pick_rects(self):
        """Each picker item's rect, None for one scrolled out of view."""
        fs = self.layout.fs
        bx, by, bw, bh = self._pick_body()
        if self.pick == "name":
            return []
        n = self._pick_count()
        cols = self._pick_cols()
        if self.pick == "accent":
            gap = 8 * fs
            s = min(28 * fs, (bw - 7 * gap) // 8)
            return [(bx + (i % 8) * (s + gap), by + (i // 8) * (s + 16 * fs), s, s)
                    for i in range(n)]
        if self.pick == "tint":
            gap = 8 * fs
            tw = (bw - 2 * gap) // 3
            th = min(40 * fs, (bh - 3 * 14 * fs) // 3 - gap)
            return [(bx + (i % 3) * (tw + gap), by + (i // 3) * (th + 14 * fs + gap),
                     tw, th) for i in range(n)]
        if self.pick == "theme":
            gap = 6 * fs
            cw = (bw - (cols - 1) * gap) // cols
            ch = min(58 * fs, cw * 3 // 5)
            per = max(1, (bh + gap) // (ch + gap))
            first = max(0, self.cur // cols - per + 1) * cols
            out = []
            for i in range(n):
                r = (i - first) // cols
                out.append(None if i < first or r >= per else
                           (bx + (i % cols) * (cw + gap), by + r * (ch + gap), cw, ch))
            return out
        gap = 10 * fs
        tw = (bw - 2 * gap) // 3
        th = tw * 9 // 16
        cell = th + 22 * fs
        per = max(1, (bh + 4 * fs) // cell)
        first = max(0, self.cur // 3 - per + 1) * 3
        out = []
        for i in range(n):
            r = (i - first) // 3
            out.append(None if i < first or r >= per else
                       (bx + (i % 3) * (tw + gap), by + r * cell, tw, th))
        return out

    # -- drawing ---------------------------------------------------------------

    def _button(self, cv, label, r, on=False):
        _ui.chip(cv, self._theme.colors(), r, label, on=on, fs=self.layout.fs)

    def _text(self, cv, s, x, y, h, c):
        """`s` at x, centred in a band of height `h` from y."""
        fs = self.layout.fs
        cv.print(s, x, y + (h - _ui.font("ui")[3] * fs) // 2, c, 1)

    def _right(self, cv, s, rx, y, h, c):
        """`s` ending at rx; answers its left edge."""
        x = rx - _ui.print_width(s) * self.layout.fs
        self._text(cv, s, x, y, h, c)
        return x

    def draw(self, dt):
        self._dt = dt
        self._refresh()
        cv = self._surf.canvas()
        lay = self.layout
        th = self._theme.colors()
        ch = self._ch
        fs = lay.fs
        if self.pick is not None and self._console():
            self._draw_page(cv, th, ch)
            return
        cv.rect(lay.cx, lay.cy, lay.cw, lay.ch, th["panel"])
        if lay.wide:
            self._text(cv, "THEME", lay.cx + 10 * fs, lay.cy + 6 * fs, 8 * fs, th["ink"])
            names = self._themes()
            for i, name in enumerate(names):
                r = lay.card_rect(i, self.top)
                if r is not None:
                    self._draw_card(cv, th, r, name, name == ch["theme"],
                                    self.zone == "grid" and i == self.card)
            more = len(names) - (self.top + lay.grid_rows) * lay.cols
            if more > 0:
                self._right(cv, "%d MORE" % more, lay.cx + lay.cw - 10 * fs,
                            lay.cy + 6 * fs, 8 * fs, th["ink_dim"])
        self._scroll_to()
        cv.clip(lay.cx, lay.rows_y, lay.cw, lay.rows_h)
        for key, r in self._row_rects():
            self._draw_row(cv, th, ch, key, r,
                           self.zone == "rows" and self._rows()[self.row] == key)
        cv.clip()
        self._draw_strip(cv, th, ch)
        if self.pick is not None:
            self._draw_popover(cv, th, ch)

    def _draw_strip(self, cv, th, ch):
        lay = self.layout
        fs = lay.fs
        y, h = lay.strip_y, lay.strip_h
        cv.rect(lay.cx, y, lay.cw, h, th["bar"])
        cv.rect(lay.cx, y, lay.cw, fs, th["bar_edge"])
        name = ch["theme"]
        if self.note:
            left = self.note
        elif name == "custom":
            left = "custom - saved as you go"
        elif ch["store"]:
            left = name + " - store"
        else:
            left = name + " - built in"
        keys = ROW_KEYS
        if self.pick == "theme":
            keys = GRID_KEYS
        elif self.pick == "name":
            keys = NAME_KEYS
        elif self.pick is not None:
            keys = PICK_KEYS
        kx = lay.cx + lay.cw - 5 * fs - _ui.print_width(keys) * fs
        lx = lay.cx + 5 * fs
        if lx + _ui.print_width(left) * fs > kx - 8 * fs:
            keys = ""
        self._text(cv, left, lx, y + fs, h - fs, th["chrome_ink"])
        if keys:
            self._text(cv, keys, kx, y + fs, h - fs, th["chrome_ink_dim"])

    def _draw_row(self, cv, th, ch, key, r, focused):
        lay = self.layout
        fs = lay.fs
        x, y, w, h = r
        if focused:
            cv.rect(x, y, w, h, th["surface_alt"])
            cv.rect(x, y, 2 * fs, h, th["focus"])
        cv.rect(x, y + h - fs, w, fs, th["shade_lo"])
        self._text(cv, LABELS[key], x + 8 * fs, y, h, th["ink"])
        rx = x + w - 8 * fs
        if key == "mode":
            for v, cr in self._mode_chips(r):
                self._button(cv, v.upper(), cr, ch["variant"] == v)
            return
        if key in ("frame", "font", "chrome"):
            cw = self._cycle_w(r)
            cx = rx - cw
            ink = th["accent"] if focused else th["ink_dim"]
            self._text(cv, "<", cx, y, h, ink)
            self._right(cv, ">", rx, y, h, ink)
            if key == "frame":
                val = FRAME_NAMES.get(ch["frame"], str(ch["frame"]).upper())
            elif key == "font":
                val = FONT_NAMES.get(ch["font"], "THEME'S OWN")
            else:
                val = "%dx" % ch["chrome"]
            vw = _ui.print_width(val) * fs
            self._text(cv, val, cx + (cw - vw) // 2, y, h, th["ink"])
            if key == "frame":
                sw, sh = 38 * fs, h - 6 * fs
                _ui.frame(cv, th, (cx - sw - 8 * fs, y + 3 * fs, sw, sh - 2 * fs),
                          5 * fs, "", True, False, 1)
            elif key == "font":
                self._right(cv, "Aa", cx - 8 * fs, y, h, th["ink_dim"])
            return
        self._right(cv, ">", rx, y, h, th["ink_dim"])
        vx = rx - 8 * fs
        th_ = h - 8 * fs
        if key == "accent":
            at, a = self._accent_shown(ch, th)
            name = self._accents[at][0].upper() if at >= 0 else "THEME'S OWN"
            nx = self._right(cv, name, vx, y, h, th["ink"])
            s = th_
            cv.rect(nx - s - 6 * fs, y + 4 * fs, s, s, a)
            cv.rectb(nx - s - 6 * fs, y + 4 * fs, s, s, th["shade_dark"])
        elif key == "tint":
            name = ch["tint"].upper() if ch["tint"] else "NONE"
            nx = self._right(cv, name, vx, y, h, th["ink"])
            self._draw_ramp(cv, th, (nx - 34 * fs, y + 4 * fs, 28 * fs, th_),
                            self._ramp(ch["tint"]))
        elif key == "wallpaper":
            nx = self._right(cv, self._wall_title(ch), vx, y, h, th["ink"])
            # The wallpaper shown, as the backdrop's own still (a cart's
            # computed preview, the picture fitted, the fill).
            tw = th_ * 4 // 3
            r = (nx - tw - 6 * fs, y + 4 * fs, tw, th_)
            self._wall.preview(cv, r, self._dt)
            cv.rectb(r[0], r[1], r[2], r[3], th["shade_dark"])
        elif key == "file":
            self._right(cv, "theme.json", vx, y, h, th["ink_dim"])
        elif key == "name":
            self._right(cv, "KEEP IT AS ITS OWN THEME", vx, y, h, th["ink_dim"])
        elif key == "theme":
            name = ch["theme"]
            nx = self._right(cv, name, vx, y, h, th["ink"])
            if ch["from"]:
                nx = self._right(cv, "from " + ch["from"], nx - 8 * fs, y, h,
                                 th["ink_dim"])
            self._draw_card(cv, th, (x + 70 * fs, y + 3 * fs, 52 * fs, h - 6 * fs),
                            name, False, False, label=False)

    def _ramp(self, name):
        i = self._tint_at(name)
        return self._tints[i][1] if i >= 0 else None

    def _draw_ramp(self, cv, th, r, ramp):
        """A tint's ramp as stripes; None (NONE) the theme's own field
        colours, crossed."""
        x, y, w, h = r
        cols = ramp if ramp is not None else (
            th["desktop"], th["panel"], th["surface_alt"], th["dim"])
        n = len(cols)
        for i, c in enumerate(cols):
            x0 = x + i * w // n
            cv.rect(x0, y, x + (i + 1) * w // n - x0, h, c)
        if ramp is None:
            cv.line(x, y + h - 1, x + w - 1, y, th["ink_dim"])
        cv.rectb(x, y, w, h, th["shade_dark"])

    def _draw_wall(self, cv, th, r, item):
        x, y, w, h = r
        kind = item[2]
        if kind == "none":
            cv.rect(x, y, w, h, th["desktop"])
        elif kind == "art":
            pic = self._wall.thumbnail(w, h)
            if pic is not None:
                cv.spr(pic, x, y)
            else:
                self._picture(cv, r)
        elif kind == "theirs":
            self._picture(cv, r)
        else:
            self._cart_scene(cv, x, y, w, h, item[3])
        cv.rectb(x, y, w, h, th["shade_dark"])

    def _picture(self, cv, r):
        x, y, w, h = r
        n = self.names
        cv.rect(x, y, w, h, n["light_grey"])
        cv.circ(x + w * 3 // 4, y + h // 3, max(2, min(w, h) // 8), n["yellow"])

    def _draw_name(self, cv, th):
        """NAME…'s field, under its two lines, the grammar or the refusal
        under it, and the theme's card as it will be in the grid beside
        where its folder goes."""
        fs = self.layout.fs
        bx, by, bw, bh = self._pick_body()
        ink, dim = th["ink"], th["ink_dim"]
        lh = _ui.font("ui")[3] * fs + 3 * fs
        y = by
        if not self._console():
            cv.print("NAME YOUR THEME", bx, y, ink, 1)
            y += lh + 2 * fs
        for line in ("IT JOINS THE THEME GRID AS ITS OWN.",
                     "CUSTOM IS FREE FOR THE NEXT CHANGE."):
            cv.print(_ui.print_fit(line, bw // fs), bx, y, dim, 1)
            y += lh
        y += 3 * fs
        fh = 16 * fs
        _ui.text_field(cv, (bx, y, bw, fh), self.entry.text, "",
                       (th["surface"], th["accent"], ink, dim, th["focus"]), fs)
        y += fh + 4 * fs
        name = self.entry.text
        if self.armed is not None and self.armed == name:
            msg, c = "REPLACE " + name + "? ENTER AGAIN", th["accent"]
        elif self.why is not None:
            msg, c = NAME_WHY.get(self.why, str(self.why).upper()), th["accent"]
        else:
            msg, c = NAME_HINT, dim
        cv.print(_ui.print_fit(msg, bw // fs), bx, y, c, 1)
        y += lh + 4 * fs
        cw, chh = 86 * fs, 54 * fs
        if y + chh > by + bh:
            return
        self._draw_card(cv, th, (bx, y, cw, chh), "custom", True, False,
                        title=name or "custom")
        tx = bx + cw + 10 * fs
        tw = (bx + bw - tx) // fs
        ty = y + 4 * fs
        for line, c in (("THE THEME FOLDER", dim),
                        ((name or "...") + ".moy", ink),
                        ("TRAVELS LIKE A CART:", dim),
                        ("FILES, EXPORT, THE STORE.", dim)):
            cv.print(_ui.print_fit(line, tw), tx, ty, c, 1)
            ty += lh

    def _draw_card(self, cv, th, r, name, selected, focused, label=True,
                   title=None):
        """A theme card: a little desk in theme `name`'s colours, its bar,
        an inactive window behind an active one; its name in a band, the
        page's accent when it is the theme in use, and a tag for `custom`
        and a store package."""
        tok = theme_colors(name, self.choices()["variant"])
        fs = self.layout.fs
        x, y, w, h = r
        band = 9 * fs if label else 0
        if focused:
            _ui.focus(cv, th, r, size="card", fs=fs)
        _fill(cv, x, y, w, h, tok["desktop"])
        iw, ih = w - 2 * fs, h - 2 * fs - band
        _fill(cv, x + fs, y + fs, iw, 3 * fs, tok["bar"])
        wx, wy, ww, wh = x + 4 * fs, y + 7 * fs, iw * 5 // 9, ih - 11 * fs
        if wh > 4 * fs:
            _fill(cv, wx, wy, ww, wh, tok["panel"])
            _frame(cv, wx, wy, ww, wh, tok["shade_dark"])
            _fill(cv, wx + fs, wy + fs, ww - 2 * fs, 3 * fs, tok["title_inactive"])
            ax, ay = x + iw * 3 // 8, y + 10 * fs
            _fill(cv, ax + fs, ay + fs, ww, wh, 0)
            _fill(cv, ax, ay, ww, wh, tok["surface"])
            _frame(cv, ax, ay, ww, wh, tok["shade_dark"])
            _fill(cv, ax + fs, ay + fs, ww - 2 * fs, 3 * fs, tok["title"])
            _fill(cv, ax + 3 * fs, ay + 6 * fs, ww // 3, 3 * fs, tok["accent"])
            if wh > 14 * fs:
                _fill(cv, ax + 3 * fs, ay + 11 * fs, ww - 6 * fs, fs, tok["ink_dim"])
        if label:
            ly = y + h - band - fs
            _fill(cv, x + fs, ly, w - 2 * fs, band, th["accent"] if selected else th["bar"])
            ink = 0 if selected else th["chrome_ink"]
            cv.print(_ui.print_fit(title or name, (w - 8 * fs) // fs), x + 4 * fs,
                     ly + (band - _ui.font("ui")[3] * fs) // 2, ink, 1)
            tag = None if title else "CUSTOM" if name == "custom" else (
                "STORE" if name in self._stores() else None)
            if tag and w >= 80 * fs:
                tw = (_ui.print_width(tag) + 4) * fs
                _fill(cv, x + w - fs - tw, ly, tw, band,
                        th["play"] if tag == "CUSTOM" else th["dim"])
                cv.print(tag, x + w - fs - tw + 2 * fs,
                         ly + (band - _ui.font("ui")[3] * fs) // 2,
                         0 if tag == "CUSTOM" else th["chrome_ink"], 1)
        edge = th["accent"] if selected else th["shade_dark"]
        _frame(cv, x, y, w, h, edge)
        if focused:
            _ui.focus(cv, th, r, size="card", after=True, fs=fs)

    def _stores(self):
        """The THEME grid's installed packages: every name that is no seed
        and not `custom`."""
        return [n for n in theme_names() if n not in THEME_NAMES and n != "custom"]

    # -- pickers ---------------------------------------------------------------

    def _draw_popover(self, cv, th, ch):
        fs = self.layout.fs
        x, y, w, h = self._pick_frame()
        cv.rect(x + 2 * fs, y + 2 * fs, w, h, 0)
        _ui.panel(cv, th, (x, y, w, h), LABELS[self.pick], fs)
        self._draw_items(cv, th, ch)

    def _draw_page(self, cv, th, ch):
        lay = self.layout
        fs = lay.fs
        cv.rect(lay.cx, lay.cy, lay.cw, lay.ch, th["panel"])
        hint = HINTS[self.pick]
        self._text(cv, LABELS[self.pick] + ": " + hint, lay.cx + 8 * fs,
                   lay.cy + 4 * fs, 8 * fs, th["ink_dim"])
        self._draw_items(cv, th, ch)
        self._draw_strip(cv, th, ch)

    def _draw_items(self, cv, th, ch):
        if self.pick == "name":
            self._draw_name(cv, th)
            return
        fs = self.layout.fs
        rects = self._pick_rects()
        for i, r in enumerate(rects):
            if r is None:
                continue
            x, y, w, h = r
            on = i == self.cur
            if on and self.pick != "theme":
                _ui.focus(cv, th, r, size="card", fs=fs)
            if self.pick == "accent":
                cv.rect(x, y, w, h, self._accents[i][1])
                cv.rectb(x, y, w, h, th["shade_dark"])
                if self._accents[i][1] == th["accent"]:
                    cv.rect(x + w // 2 - fs, y + h // 2 - fs, 2 * fs, 2 * fs, 0)
                if on:
                    _ui.focus(cv, th, r, size="card", after=True, fs=fs)
                    name = self._accents[i][0].upper()
                    nw = _ui.print_width(name) * fs
                    cv.print(name, x + (w - nw) // 2, y + h + 4 * fs, th["ink"], 1)
                continue
            if self.pick == "theme":
                names = self._themes()
                self._draw_card(cv, th, r, names[i], names[i] == ch["theme"], on)
                continue
            if self.pick == "tint":
                ramp = None if i == 0 else self._tints[i - 1][1]
                name = "NONE" if i == 0 else self._tints[i - 1][0].upper()
                self._draw_ramp(cv, th, r, ramp)
                kept = (ch["tint"] is None) if i == 0 else \
                    (ch["tint"] == self._tints[i - 1][0])
            else:
                item = self._walls()[i]
                self._draw_wall(cv, th, r, item)
                name = item[1]
                kept = i == self._wall_at(ch)
            cv.print(_ui.print_fit(name, w // fs - 10), x, y + h + 4 * fs,
                     th["ink"] if on else th["ink_dim"], 1)
            if kept:
                self._right(cv, "*", x + w, y + h + 2 * fs, 10 * fs, th["ink"])
            if on:
                _ui.focus(cv, th, r, size="card", after=True, fs=fs)

    def _cart_scene(self, cv, x, y, w, h, title):
        # Distinct miniature visual identities of the wallpaper carts.
        fs = self.layout.fs
        if "Ocean" in title:
            cv.rect(x, y, w, h, self.names["blue"])
            for row in range(3):
                cv.line(x, y + h // 2 + row * 5 * fs,
                        x + w - 1, y + h // 2 + row * 5 * fs, 21 + row)
            cv.circ(x + w * 3 // 4, y + h // 3, max(2, h // 10), 7)
        elif "Sakura" in title:
            cv.rect(x, y, w, h, 22)
            cv.line(x + w // 3, y + h, x + w // 2, y + h // 4, 28)
            cv.circ(x + w // 2, y + h // 3, max(3, h // 4), self.names["pink"])
            cv.circ(x + w * 2 // 3, y + h // 3, max(2, h // 6), self.names["peach"])
        elif "Machine" in title:
            cv.rect(x, y, w, h, self.names["black"])
            step = max(4 * fs, min(w, h) // 6)
            for gy in range(y + step // 2, y + h, step):
                for gx in range(x + step // 2, x + w, step):
                    cv.rect(gx, gy, fs, fs, self.names["dark_blue"])
            cv.line(x + w * 2 // 3, y, x + w - 1, y + h // 3, self.names["indigo"])
            cv.line(x + w - 1, y + h // 3, x + w * 2 // 3, y + h - 1,
                    self.names["indigo"])
        elif "Space" in title or "Night" in title:
            cv.rect(x, y, w, h, 60)
            for i in range(12):
                cv.rect(x + (i * 37) % max(1, w), y + (i * 19) % max(1, h),
                        fs, fs, 7 if i % 3 else 10)
            cv.circ(x + w * 3 // 4, y + h // 3, max(3, h // 6), 10)
        else:
            cv.rect(x, y, w, h, self._theme.colors()["hilite"])
            cv.rect(x, y + h * 2 // 3, w, h // 3, self.names["dark_green"])
            cv.circ(x + w * 3 // 4, y + h // 3, max(2, h // 8), self.names["yellow"])

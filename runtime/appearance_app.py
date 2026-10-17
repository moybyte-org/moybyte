# Map (grep -n a name to jump there):
#   AppearanceLayout                 the page's geometry: the rows' region, the foot
#   AppearanceAppLayer.choices       what the rows show, once per token generation
#   AppearanceAppLayer.draw          the frame: the rows, the wallpapers or the name
#   AppearanceAppLayer._rows_page    the theme cards, then the rows
#   AppearanceAppLayer._walls_page   the wallpapers: tabs over a grid of cards
#   AppearanceAppLayer._name_dialog  NAME: custom kept as a theme of its own
#   AppearanceAppLayer._preview      a theme's card picture: its colours
"""The Appearance page (docs/look_2026-10.md step 20), drawn with the public
UI API's verbs (docs/ui_api_2026-10.md) and nothing under them.

The themes are a grid of cards (`ui.grid`, `ui.card`), each picture the
theme's own ground, bar, surface and accent, the theme in use wearing the
current mark; under them the rows: MODE (a seg), ACCENT (swatches: the
theme's own and seven of the sixteen), TINT, WALLPAPER (a link to the
wallpapers' page), WINDOW STYLE (where windows are), FONT, CODE FONT
(where the image draws more than one), CHROME SIZE (where the panel takes
more than one), the theme's file, and NAME on `custom`. The engine owns focus, the arrows, Enter and taps.

WALLPAPER opens a page of the wallpapers as cards under two tabs
(`ui.tabs`): Pictures (the theme's backdrop, its picture, Paint's) and
Carts (the wallpaper carts). A card puts that wallpaper on and comes back;
B comes back with nothing changed.

Every row but the cards, MODE, CHROME SIZE, the file and NAME is a change to
the theme (`ctx.theme.change`), which lands in `custom`. The rows read
`ctx.theme.choices()` once per token generation.

NAME (on `custom` only) is a dialog holding a field, the keyboard in text
mode (`ctx.nav.text_mode`): Keep renames `custom` to the name typed
(`ctx.theme.name_custom`), a theme of its own among the cards; a name an
installed theme has turns Keep into Replace, which replaces it; Cancel or B
backs out.
"""

try:
    from layout_base import bar_rows, content_of, metric_class, DESK
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.layout_base import bar_rows, content_of, metric_class, DESK

try:
    from moy_store_base import BUILTIN_NS, builtin_name
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_store_base import BUILTIN_NS, builtin_name

try:
    from chrome import (THEME_NAMES, theme_accents, theme_colors, theme_names,
                        theme_tints, RGB, rgb565_of, nearest_of)
except ImportError:  # pragma: no cover - direct host import
    from runtime.chrome import (THEME_NAMES, theme_accents, theme_colors, theme_names,
                                theme_tints, RGB, rgb565_of, nearest_of)

try:
    import ui
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui

# The theme's picture's wallpaper id (`Appearance.PICTURE`), and the change
# value that puts the source theme's own wallpaper back (`Appearance.change`).
PICTURE = BUILTIN_NS + ".my_art"
THEIRS = ""

# The WINDOW STYLE and FONT presets in their stepping order (the look's
# FRAMES and FONTS), and their names.
FRAMES = ("classic", "tab", "platinum", "win9x", "flat")
FONTS = ("tiny5", "petme128")
FRAME_NAMES = ("Classic", "Tabs", "Platinum", "Win9x", "Flat")
FONT_NAMES = ("Tiny5", "PetMe128")
# The CODE FONT presets (`Appearance.CODE_FONTS`) by name.
CODE_FONT_NAMES = {"petme128": "PetMe128", "jbmono": "JetBrains Mono"}
OWN = "Theme's"
# ACCENT's swatches after the theme's own: seven of the sixteen accents
# (style.json's), by name.
ACCENTS = ("orange", "red", "pink", "blue", "mint", "green", "violet")
# The wallpapers' page's tabs.
TABS = ("Pictures", "Carts")
# NAME's refusals (`ctx.theme.name_check`), in words.
NAME_WHY = {"grammar": "a-z 0-9 _ - only, a letter or digit first",
            "seed": "A built-in theme has that name",
            "custom": "Custom is the one being changed",
            "cart": "A cart has that name",
            "store": "Can't save here",
            "none": "Only custom is named"}
NAME_HINT = "a-z 0-9 _ -, up to 24"
NAME_MAX = 24
# The hints by the focused stop's kind.
_STEP = ("left right", "Change")
_OPEN = ("enter", "Open")
_USE = ("enter", "Use")
_HINTS = {"step": _STEP, "link": _OPEN, "card": _USE, "tabs": _STEP}


def _rgb(v):
    """Colour value `v` as 0xRRGGBB: RGB's own, an index's (the palette's or
    the block's) from its RGB565 word, which it comes back to exactly."""
    if v >= RGB:
        return v & 0xffffff
    w = rgb565_of(v)
    return (((w >> 11) * 255 // 31) << 16) | ((((w >> 5) & 63) * 255 // 63) << 8) \
        | ((w & 31) * 255 // 31)


def _fill(cv, x, y, w, h, v):
    """A rect in colour value `v`: a palette index (the live theme's block
    included) through the canvas, an RGB value as its RGB565 word, which
    takes no slot: how a card shows another theme's colours."""
    if w <= 0 or h <= 0:
        return
    if v < RGB:
        cv.rect(x, y, w, h, v)
        return
    fill = getattr(cv, "fill565", None)
    if fill is None:                    # a canvas that takes indices only
        cv.rect(x, y, w, h, nearest_of(v))
        return
    fill(x, y, w, h, rgb565_of(v))


def _index(v):
    """Colour value `v` as an index a canvas draws: its nearest for RGB."""
    return v if v < RGB else nearest_of(v)


def _title(name):
    """A theme's name as its card says it."""
    return name[:1].upper() + name[1:]


class AppearanceLayout:
    """The page's geometry for one content size: the rows' region (the
    cards and rows scroll in it) and, on the handheld, the hints' band under
    it; on the desk, the foot's band (a rule, the theme's status, hints)."""

    # Min-size convention (ui.py): the WM clamps window resizes to these
    # (fs-scaled).
    MIN_W = 310
    MIN_H = 230
    FOOT = 44            # the desk's foot band (the target's)

    def __init__(self, w, h, fs=1, windowed=False, cs=None):
        self.w = int(w)
        self.h = int(h)
        self.fs = fs = max(1, int(fs))
        # The chrome scale (#203) sizes the OS bar band above a fullscreen
        # page; a window has no bar over it.
        self.cs = max(fs, int(cs)) if cs else fs
        self.windowed = bool(windowed)
        self.desk = metric_class() == DESK
        self.cx, self.cy, self.cw, self.ch = content_of(
            self.w, self.h, 0 if windowed else bar_rows(self.cs))
        content = (self.cx, self.cy, self.cw, self.ch)
        if self.desk:
            self.foot, body = ui.cut_bottom(content, self.FOOT)
            self.body = ui.inset(body, 16, 0)
            # The pictures: the target's 192 x 112, three to a 680 window.
            self.card = (192, 112)
        else:
            self.foot, self.body = ui.cut_bottom(content, 20 * fs)
            self.card = (66 * fs, 50 * fs)


class AppearanceAppLayer:
    """The Appearance page (see the module doc)."""

    id = "appearance"
    domain = "system"
    TITLE = "Appearance"
    # The shell roles this app uses (runtime/app_context.py). `wallpaper` is a
    # CAPABILITY, not a core role: this app and the two that copy a drawing
    # into it (Paint, Files) are its only consumers. A pick is a change to
    # the theme (docs/theming_2026-09.md D6), so it lands in `custom`.
    NEEDS = ("surface", "theme", "damage", "wallpaper", "nav")
    UI = True

    def __init__(self, ctx, names):
        self.ctx = ctx
        # Roles bound ONCE (the hoist mandate, ui_refactor_2026-08 Section 2.4).
        self._surf = ctx.surface
        self._theme = ctx.theme
        self._damage = ctx.damage
        self._wall = ctx.wallpaper
        self._nav = ctx.nav
        self._ui = ctx.ui
        self.names = names
        cv = ctx.surface.canvas()
        self.layout = AppearanceLayout(cv.w, cv.h, self._surf.font_scale(),
                                       self._surf.windowed(),
                                       self._surf.chrome_scale())
        self.view = "rows"          # "rows" or "walls"
        self.tab = 0                # the wallpapers' page's tab
        self.naming = False         # NAME's dialog is open
        self.name_text = ""
        self.armed = None           # the name Replace is armed for
        self.why = None             # the refusal shown
        self.note = None            # the status once a name is kept
        self._focus = None          # the id focus moves to on the next frame
        self._focused = None        # the focused id as the last frame left it
        self._kinds = {}            # a stop's id -> its kind, for the hints
        self._ch = None
        self._gen = None
        self._stale = 0
        self._dt = 0
        self._accents = tuple(a for n in ACCENTS for a in theme_accents() if a[0] == n)
        self._tints = tuple(theme_tints())

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
        self.view = "rows"
        self._close_name(acted=False)
        self.note = None
        self._ui.reset()
        self._focus = self._card_id(self.choices()["theme"])
        self._stale = 2
        self._damage.all()

    def show_row(self, label):
        """Focus row `label` ("Tint") from the next frame on."""
        self.view = "rows"
        self._focus = label
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
        shows at the next frame)."""
        g = self._theme.gen()
        if self._ch is None or g != self._gen or self._stale:
            self._ch = self._theme.choices()
            self._gen = g
            if self._stale:
                self._stale -= 1
            if self.naming and self._ch["theme"] != "custom":
                self._close_name(acted=False)

    def _themes(self):
        """The cards' names: `custom` first, then the store's packages (a
        named theme among them), then the seeds (`chrome.theme_names`)."""
        names = list(theme_names())
        mine = [n for n in names if n not in THEME_NAMES]
        if "custom" in mine:
            mine.remove("custom")
            mine.insert(0, "custom")
        return mine + [n for n in names if n in THEME_NAMES]

    @staticmethod
    def _card_id(name):
        return "theme " + name

    def _modes(self, name):
        """A theme's two modes, light first, as its Mode row names them."""
        return ("Day", "Night") if name == "moy" else ("Light", "Dark")

    def _status(self, ch):
        """The desk's foot: the theme, its mode and where it comes from."""
        if self.note:
            return self.note
        dot = " · " if ui.has_symbols() else " - "
        name = ch["theme"]
        if name == "custom":
            return "Custom" + dot + "saved as you go"
        mode = self._modes(name)[0 if ch["variant"] == "light" else 1]
        return _title(name) + " " + mode + dot + ("store" if ch["store"] else "built in")

    def _walls(self, tab):
        """The wallpapers under tab `tab`: (value, title, kind), kind
        "backdrop" (no wallpaper: the theme's own backdrop), "theirs" (the
        source theme's own picture), "art" (Paint's picture) or "cart"."""
        ch = self.choices()
        out = []
        art = None
        carts = []
        for c in self._wall.carts():
            wid = self._wall.id_for(c)
            title = str(c.get("title", "Wallpaper"))
            if title == "My Art":
                art = (wid, "My art", "art")
            else:
                carts.append((wid, title, "cart"))
        if tab == 1:
            return carts
        out.append((None, "Backdrop", "backdrop"))
        if ch["picture"] == "theme":
            out.append((THEIRS, "Theme's picture", "theirs"))
        if art is not None:
            out.append(art)
        return out

    def _wall_kind(self, ch):
        """(tab, title) of the wallpaper on now."""
        wp = ch["wallpaper"]
        if wp is None:
            return 0, "Backdrop"
        if wp == PICTURE:
            return 0, "My art" if ch["picture"] == "mine" else "Theme's picture"
        t = self._wall.title(wp)
        return 1, str(t) if t else "Wallpaper"

    def _wall_on(self, ch, item):
        wp = ch["wallpaper"]
        kind = item[2]
        if kind == "backdrop":
            return wp is None
        if kind == "theirs":
            return wp == PICTURE and ch["picture"] == "theme"
        if kind == "art":
            return wp == PICTURE and ch["picture"] == "mine"
        return wp == item[0]

    # -- acting ----------------------------------------------------------------

    def _acted(self):
        self.note = None
        self._stale = 2
        self._damage.all()

    def _change(self, kind, value):
        rc = self._theme.change(kind, value)
        self._acted()
        return rc

    def _open_name(self):
        self.naming = True
        self.name_text = ""
        self.armed = None
        self.why = None
        self._nav.text_mode(True)
        self._damage.all()

    def _close_name(self, acted=True):
        if self.naming:
            self._nav.text_mode(False)
        self.naming = False
        self.armed = None
        self.why = None
        self._focus = "Name"
        if acted:
            self._acted()

    @staticmethod
    def _name_ok(text):
        """The name grammar's characters (§3), a letter or a digit first."""
        out = ""
        for c in text:
            if "a" <= c <= "z" or "0" <= c <= "9" or (c in "_-" and out):
                out += c
        return out

    def _name_commit(self):
        """Keep in NAME: rename `custom` to the name typed; a name an
        installed theme has arms Replace, and a second Keep replaces it."""
        name = self.name_text
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
        self._focus = self._card_id(name)
        self.note = name + ".moy is a theme now"
        self._refresh()

    # -- input -----------------------------------------------------------------

    def handle_input(self, inp):
        self._refresh()
        k = getattr(inp, "last_key", 0) or 0
        if inp.pressed("b") or (self.naming and k == 27):    # Esc typed in text mode
            self._back()
        if self._ui.keys(inp):
            self._damage.all()
        return True

    def _back(self):
        if self.naming:
            self._close_name()
        elif self.view == "walls":
            self.view = "rows"
            self._focus = "Wallpaper"
        self._damage.all()

    def handle_pointer(self, px, py, click):
        if self._ui.point(px, py, click, bool(getattr(self._surf.pointer(), "down", False))):
            self._damage.all()
        return True

    # -- draw ------------------------------------------------------------------

    def draw(self, dt):
        self._dt = dt
        self._refresh()
        cv = self._surf.canvas()
        lay = self.layout
        th = self._theme.colors()
        self._ui.begin(cv, th, (0, 0, cv.w, cv.h), lay.desk, lay.windowed)
        ui.page("")                   # the ground under the bar's band too
        was = (self.view, self.tab, self.naming, self.armed, self.why)
        if self._focus is not None:
            ui.focus(self._focus)
            self._focus = None
        self._kinds.clear()
        ch = self._ch
        ui.at(*lay.body)
        ui.scroll("page" if self.view == "rows" else "walls")
        if self.view == "walls":
            self._walls_page(cv, th, ch)
        else:
            self._rows_page(cv, th, ch)
        ui.scroll()
        self._foot(cv, th, ch)
        self._name_dialog()
        self._focused = ui.focus()
        if (self.view, self.tab, self.naming, self.armed, self.why) != was:
            self._damage.all()        # an answer changed what the page shows

    def _foot(self, cv, th, ch):
        """The hints for the focused stop; on the desk under a rule, after
        the theme's status."""
        lay = self.layout
        kind = self._kinds.get(self._focused, "step")
        if self.naming:
            items = ("enter", "Keep", "Esc", "Cancel")
        else:
            items = _HINTS.get(kind, _STEP) + ("Esc", "Close" if lay.desk else "Back")
        if not lay.desk:
            ui.at(*lay.foot)
            ui.hints(items)
            return
        x, y, w, h = lay.foot
        bx, _by, bw, _bh = lay.body
        cv.rect(bx, y, bw, 1, th["border"])
        hw = self._hints_w(items)
        ui.at(bx, y + 1, max(0, bw - hw), h - 1)
        ui.label(self._status(ch))
        ui.at(bx + bw - hw, y + 1, hw, h - 1)
        ui.hints(items)

    @staticmethod
    def _hints_w(items):
        """About what `items` take as the desk's hints draw them: the caps
        (an arrow or Enter a square key, a word its legend), then the action."""
        w = 8
        for i in range(0, len(items), 2):
            for k in items[i].split(" "):
                if k in ("left", "right", "up", "down"):
                    w += 20 + 3
                elif k == "enter":
                    w += 23 + 3
                else:
                    w += max(20, ui.text_size(k)[0] + 10) + 3
            w += 1 + ui.text_size(items[i + 1])[0] + 14
        return w

    # -- the rows --------------------------------------------------------------

    def _rows_page(self, cv, th, ch):
        lay = self.layout
        ui.group("Theme")
        self._theme_cards(cv, ch)
        name = ch["theme"]
        kinds = self._kinds
        # MODE
        modes = self._modes(name if name != "custom" else (ch["from"] or name))
        cur = 0 if ch["variant"] == "light" else 1
        kinds["Mode"] = "step"
        got = ui.seg("Mode", modes, cur)
        if got != cur:
            self._theme.set_variant("light" if got == 0 else "dark")
            self._acted()
        # ACCENT: the theme's own, then seven of the sixteen
        own = th["accent"] if ch["accent"] is None else None
        colours = [_rgb(own if own is not None else self._own_accent(ch))]
        colours += [_rgb(a[1]) for a in self._accents]
        cur = 0
        if ch["accent"] is not None:
            cur = -1
            for i, a in enumerate(self._accents):
                if a[1] == ch["accent"]:
                    cur = i + 1
        kinds["Accent"] = "step"
        got = ui.swatches("Accent", colours, cur)
        if got != cur and got >= 0:
            self._change("accent", None if got == 0 else self._accents[got - 1][1])
        # TINT
        tints = [t[0] for t in self._tints]
        words = ["None"] + [_title(t) for t in tints]
        cur = tints.index(ch["tint"]) + 1 if ch["tint"] in tints else 0
        kinds["Tint"] = "step"
        got = ui.cycle("Tint", words, cur)
        if got != cur:
            self._change("tint", None if got == 0 else tints[got - 1])
        # WALLPAPER
        kinds["Wallpaper"] = "link"
        tab, title = self._wall_kind(ch)
        if ui.link("Wallpaper", title):
            self.view = "walls"
            self.tab = tab
            self._focus = "Wallpaper " + title
        # WINDOW STYLE: where windows are
        if lay.desk:
            frame = ch["frame"]
            if frame in FRAMES:
                words, cur, off = FRAME_NAMES, FRAMES.index(frame), 0
            else:
                words, cur, off = (OWN,) + FRAME_NAMES, 0, 1
            kinds["Window style"] = "step"
            got = ui.cycle("Window style", words, cur)
            if got != cur and got >= off:
                self._change("frame", FRAMES[got - off])
        # FONT
        font = ch["font"]
        if font in FONTS:
            words, cur, off = FONT_NAMES, FONTS.index(font), 0
        else:
            words, cur, off = (OWN,) + FONT_NAMES, 0, 1
        kinds["Font"] = "step"
        got = ui.cycle("Font", words, cur)
        if got != cur and got >= off:
            self._change("font", FONTS[got - off])
        # CODE FONT: the Code tab's face, where the image draws more than one
        codes = ch["code_fonts"]
        if len(codes) > 1:
            code = ch["code_font"]
            names = [CODE_FONT_NAMES[k] for k in codes]
            if code in codes:
                words, cur, off = names, codes.index(code), 0
            else:
                words, cur, off = [OWN] + names, 0, 1
            kinds["Code font"] = "step"
            got = ui.cycle("Code font", words, cur)
            if got != cur and got >= off:
                self._change("code_font", codes[got - off])
        # CHROME SIZE: where the panel takes more than one
        chromes = ch["chromes"]
        if len(chromes) > 1:
            x = "×" if ui.has_symbols() else "x"
            cur = chromes.index(ch["chrome"]) if ch["chrome"] in chromes else 0
            kinds["Chrome size"] = "step"
            got = ui.cycle("Chrome size", ["%d%s" % (c, x) for c in chromes], cur)
            if got != cur:
                self._theme.set_chrome_scale(chromes[got])
                self._acted()
        # the theme's file, and NAME on custom
        kinds["Theme file"] = "link"
        if ui.link("Theme file", "theme.json"):
            self._theme.edit_file()
            self._acted()
        if name == "custom":
            kinds["Name"] = "link"
            if ui.link("Name", "Keep as a theme"):
                self._open_name()
        ui.group()

    def _own_accent(self, ch):
        """The source's own accent while the theme sets another."""
        src = ch["from"] or ch["theme"]
        return theme_colors(src, ch["variant"])["accent"]

    def _theme_cards(self, cv, ch):
        lay = self.layout
        w, h = lay.card
        dot = " · " if ui.has_symbols() else " - "
        ui.grid("themes", w, h)
        for name in self._themes():
            cid = self._card_id(name)
            self._kinds[cid] = "card"
            sub = dot.join(self._modes(name))
            x, y, cw, chh, st = ui.card(_title(name), sub,
                                        {"id": cid, "current": name == ch["theme"]})
            if cw:
                self._preview(cv, (x, y, cw, chh), name, ch["variant"])
            if st & ui.ACTIVATED and name != ch["theme"]:
                self._theme.set(name)
                self._acted()
        ui.grid()

    def _preview(self, cv, r, name, variant):
        """Theme `name`'s card picture: its ground and bar, a surface panel
        with "Aa" in its ink, its accent (and its value, where that is
        another colour)."""
        tok = theme_colors(name, variant)
        x, y, w, h = r
        desk = self.layout.desk
        fs = self.layout.fs
        k = 1 if desk else fs
        cv.clip(*self.layout.body)
        _fill(cv, x, y, w, h, tok["desktop"])
        bh = (14 if desk else 8) * k
        _fill(cv, x, y, w, bh, tok["bar"])
        m = (14 if desk else 6) * k
        py = y + bh + (10 if desk else 5) * k
        pw, ph = w - 2 * m, h - (py - y) - (10 if desk else 5) * k
        _fill(cv, x + m, py, pw, ph, tok["surface"])
        big = 2 if desk else 1
        tw, lh = ui.text_size("Aa", big)
        cv.print_scaled("Aa", x + m + m, py + (ph - lh) // 2, _index(tok["ink"]), big) \
            if big > 1 else cv.print("Aa", x + m + m, py + (ph - lh) // 2, _index(tok["ink"]))
        d = (16 if desk else 8) * k
        xr = x + m + pw - m - d
        acc, val = tok["accent"], tok["value"]
        if val != acc:
            self._dot(cv, xr, py + ph // 2 - d - k, d, acc)
            self._dot(cv, xr, py + ph // 2 + k, d, val)
        else:
            self._dot(cv, xr, py + (ph - d) // 2, d, acc)
        cv.clip()

    @staticmethod
    def _dot(cv, x, y, d, v):
        cv.circ(x + d // 2, y + d // 2, d // 2, _index(v))

    # -- the wallpapers --------------------------------------------------------

    def _walls_page(self, cv, th, ch):
        lay = self.layout
        self._kinds["Wallpapers"] = "tabs"
        got = ui.tabs("Wallpapers", TABS, self.tab)
        if got != self.tab:
            self.tab = got
        w, h = lay.card
        ui.grid("walls", w, h)
        for item in self._walls(self.tab):
            cid = "Wallpaper " + item[1]
            self._kinds[cid] = "card"
            x, y, cw, chh, st = ui.card(item[1], "", {"id": cid,
                                                       "current": self._wall_on(ch, item)})
            if cw:
                cv.clip(*lay.body)
                if self._wall_on(ch, item):
                    # the wallpaper in force: the backdrop's own still
                    self._wall.preview(cv, (x, y, cw, chh), self._dt)
                else:
                    self._draw_wall(cv, th, (x, y, cw, chh), item)
                cv.clip()
            if st & ui.ACTIVATED:
                if not self._wall_on(ch, item):
                    self._change("wallpaper", item[0])
                self.view = "rows"
                self._focus = "Wallpaper"
        ui.grid()
        if not self._walls(self.tab):
            ui.label("No wallpaper carts yet")

    def _draw_wall(self, cv, th, r, item):
        x, y, w, h = r
        kind = item[2]
        if kind == "backdrop":
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
            self._cart_scene(cv, x, y, w, h, item[1])

    def _picture(self, cv, r):
        x, y, w, h = r
        n = self.names
        cv.rect(x, y, w, h, n["light_grey"])
        cv.circ(x + w * 3 // 4, y + h // 3, max(2, min(w, h) // 8), n["yellow"])

    def _cart_scene(self, cv, x, y, w, h, title):
        # Distinct miniature visual identities of the wallpaper carts.
        fs = self.layout.fs
        n = self.names
        if "Ocean" in title:
            cv.rect(x, y, w, h, n["blue"])
            for row in range(3):
                cv.line(x, y + h // 2 + row * 5 * fs,
                        x + w - 1, y + h // 2 + row * 5 * fs, 21 + row)
            cv.circ(x + w * 3 // 4, y + h // 3, max(2, h // 10), 7)
        elif "Sakura" in title:
            cv.rect(x, y, w, h, 22)
            cv.line(x + w // 3, y + h, x + w // 2, y + h // 4, 28)
            cv.circ(x + w // 2, y + h // 3, max(3, h // 4), n["pink"])
            cv.circ(x + w * 2 // 3, y + h // 3, max(2, h // 6), n["peach"])
        elif "Machine" in title:
            cv.rect(x, y, w, h, n["black"])
            step = max(4 * fs, min(w, h) // 6)
            for gy in range(y + step // 2, y + h, step):
                for gx in range(x + step // 2, x + w, step):
                    cv.rect(gx, gy, fs, fs, n["dark_blue"])
            cv.line(x + w * 2 // 3, y, x + w - 1, y + h // 3, n["indigo"])
            cv.line(x + w - 1, y + h // 3, x + w * 2 // 3, y + h - 1, n["indigo"])
        elif "Space" in title or "Night" in title:
            cv.rect(x, y, w, h, 60)
            for i in range(12):
                cv.rect(x + (i * 37) % max(1, w), y + (i * 19) % max(1, h),
                        fs, fs, 7 if i % 3 else 10)
            cv.circ(x + w * 3 // 4, y + h // 3, max(3, h // 6), 10)
        else:
            cv.rect(x, y, w, h, 12)
            cv.rect(x, y + h * 2 // 3, w, h - h * 2 // 3, n["dark_green"])
            cv.circ(x + w * 3 // 4, y + h // 3, max(2, h // 8), n["yellow"])

    # -- NAME ------------------------------------------------------------------

    def _name_dialog(self):
        if not self.naming:
            return
        name = self.name_text
        replace = self.armed is not None and self.armed == name
        pick = ui.dialog("Name your theme", ("Cancel", "Replace" if replace else "Keep"))
        text, done = ui.field("Name", name, NAME_MAX, {"id": "Theme name"})
        text = self._name_ok(text)
        if text != name:
            self.name_text = text
            self.armed = None
            self.why = None
        if replace:
            ui.label("Replace " + name + "?")
        elif self.why is not None:
            ui.label(NAME_WHY.get(self.why, str(self.why)))
        else:
            ui.label(NAME_HINT)
        ui.dialog()
        if pick == 1 or done:
            self._name_commit()
        elif pick == 0:
            self._close_name()


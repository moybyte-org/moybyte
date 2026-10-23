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
current mark; under them the rows: MODE (a seg, where the theme has two
modes), ACCENT (eight swatches:
the theme's own and `OFFERED`), TINT, WALLPAPER (a link to the
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
    from layout_base import bar_rows, content_of, metric_class, window_foot, DESK
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.layout_base import bar_rows, content_of, metric_class, window_foot, DESK

try:
    from moy_store_base import BUILTIN_NS, builtin_name
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_store_base import BUILTIN_NS, builtin_name

try:
    from chrome import (THEME_NAMES, theme_accents, theme_colors, theme_look, theme_modes,
                        theme_names, theme_presets, theme_tints, RGB, PAINT, LOOK_NONE,
                        rgb565_of, nearest_of)
except ImportError:  # pragma: no cover - direct host import
    from runtime.chrome import (THEME_NAMES, theme_accents, theme_colors, theme_look,
                                theme_modes,
                                theme_names, theme_presets, theme_tints, RGB, PAINT,
                                LOOK_NONE, rgb565_of, nearest_of)

try:
    import ui
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui

# The theme's picture's wallpaper id (`Appearance.PICTURE`), and the change
# value that puts the source theme's own wallpaper back (`Appearance.change`).
PICTURE = BUILTIN_NS + ".wallpaper"
THEIRS = ""

# The WINDOW STYLE and FONT presets in their stepping order (the look's
# FRAMES and FONTS), and their names.
FRAMES = ("classic", "tab", "platinum", "win9x", "flat")
FONTS = ("tiny5", "petme128")
FRAME_NAMES = ("Classic", "Tabs", "Platinum", "Win9x", "Flat")
FONT_NAMES = ("Tiny5", "PetMe128")
# The CODE FONT presets (`Appearance.CODE_FONTS`) by name.
CODE_FONT_NAMES = {"petme128": "PetMe128", "jbmono": "JetBrains Mono"}
OWN = "Default"     # a cycler's word for the theme's own choice
# ACCENT's seven swatches after the theme's own, by name, in hue order: a
# preset (style.json's `presets`, an accent in each mode's value) or one of
# the sixteen accents. Each reads in Moy's Day and Night, its focus colour on
# the current plate included (tests/test_theme_derive.py); `indigo`, the
# twin of Moy's own, and the rest stay in the data, not offered.
OFFERED = ("coral", "red", "berry", "magenta", "azure", "forest", "slate")
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
# The type roles a card's "Aa" tries, largest first (`_aa`).
_AA_FACES = ("result", "key", "title")
# The hints by the focused stop's kind.
_STEP = ("change", "Change")
_OPEN = ("confirm", "Open")
_USE = ("confirm", "Use")
_HINTS = {"step": _STEP, "link": _OPEN, "card": _USE, "tabs": _STEP}
_BACK = ("back", "Back")
# ...and each with Back after it, where the app is on a page.
_HINTS_BACK = dict((k, v + _BACK) for k, v in _HINTS.items())
_STEP_BACK = _STEP + _BACK
_NAMING = ("confirm", "Keep", "back", "Cancel")


def _rgb(v):
    """Colour value `v` as 0xRRGGBB: RGB's own, an index's (the palette's or
    the block's) from its RGB565 word, which it comes back to exactly."""
    if v >= RGB:
        return v & 0xffffff
    w = rgb565_of(v)
    return (((w >> 11) * 255 // 31) << 16) | ((((w >> 5) & 63) * 255 // 63) << 8) \
        | ((w & 31) * 255 // 31)


_WORDS = {}          # an RGB colour value -> its RGB565 word, two bytes in a canvas's order
_TAKES_RGB = {}      # a canvas class -> whether it draws RGB565 words (`cover`)


def _takes_rgb(cv):
    t = type(cv)
    ok = _TAKES_RGB.get(t)
    if ok is None:
        ok = _TAKES_RGB[t] = getattr(cv, "cover", None) is not None
    return ok


def _word(cv, v):
    """RGB value `v`'s RGB565 word as the picture of one pixel in `cv`'s
    byte order (`swapped565`), made once a value."""
    sw = getattr(cv, "swapped565", False)
    key = -v if sw else v
    b = _WORDS.get(key)
    if b is None:
        w = rgb565_of(v)
        if sw:
            w = ((w & 0xFF) << 8) | (w >> 8)
        b = _WORDS[key] = bytes((w & 0xFF, w >> 8))
    return b


def _fill(cv, x, y, w, h, v):
    """A rect in colour value `v`: a palette index (the live theme's block
    included) through the canvas, an RGB value as its RGB565 word, which
    takes no slot: how a card shows another theme's colours."""
    if w <= 0 or h <= 0:
        return
    if v < RGB:
        cv.rect(x, y, w, h, v)
        return
    if not _takes_rgb(cv):              # a canvas that takes indices only
        cv.rect(x, y, w, h, nearest_of(v))
        return
    cv.blit565_resize(_word(cv, v), 1, 1, x, y, w, h)


def _shape(cv, x, y, w, h, r, v):
    """A rect with anti-aliased corners of radius `r` (a circle at w // 2)
    in colour value `v`: an index through the canvas's `rrect`, an RGB value
    exactly, as a one-word picture `cover` scales into the shape."""
    if w <= 0 or h <= 0:
        return
    if v < RGB:
        cv.rrect(x, y, w, h, r, v)
        return
    if not _takes_rgb(cv):              # a canvas that takes indices only
        cv.rrect(x, y, w, h, r, nearest_of(v))
        return
    cv.cover(_word(cv, v), 1, 0, 0, 1, 1, x, y, w, h, r)


def _look_colour(lk, leaf, default):
    """Look leaf `leaf`'s colour, else `default` (unset, or a paint)."""
    v = lk.get(leaf, LOOK_NONE)
    return default if v == LOOK_NONE or v < 0 or v >= PAINT else v


def _look_px(v, k):
    """A look width `v` (hundredths) in pixels at scale `k`, 0 unset."""
    return 0 if v == LOOK_NONE or v <= 0 else max(1, (v * k + 50) // 100)


def _index(v):
    """Colour value `v` as an index a canvas draws: its nearest for RGB."""
    return v if v < RGB else nearest_of(v)


def _title(name):
    """A theme's name as its card says it."""
    return name[:1].upper() + name[1:]


class _Built:
    """What a frame of the page draws from one read of the choices
    (`AppearanceAppLayer._build`)."""

    def __init__(self):
        self.cards = []            # (name, title, sub, override table, picture)
        self.kinds = {}            # a stop's id -> its kind, for the hints
        self.modes = ()
        self.mode_cur = 0
        self.accents = None        # the swatches' colours, None where none are offered
        self.accent_cur = 0
        self.tint_cur = 0
        self.wall_tab = 0
        self.wall_title = ""
        self.wall_focus = ""
        self.frame_words = self.font_words = ()
        self.frame_cur = self.frame_off = self.font_cur = self.font_off = 0
        self.code_words = None
        self.code_cur = self.code_off = 0
        self.chrome_words = None
        self.chrome_cur = 0
        self.walls = {}            # a tab -> its cards: (item, id, override table)
        self.walls_kinds = {}      # a tab -> its stops' kinds
        self.status_room = -1      # the desk foot's status, cut to its room
        self.status = ""
        self.status_h = 0
        self.aa = {}               # a panel height -> how "Aa" draws there (`_aa`)


class AppearanceLayout:
    """The page's geometry for one content size: the rows' region (the
    cards and rows scroll in it) and, on the handheld, the hints' band under
    it; on the desk, the foot's band (a rule, the theme's status, hints)."""

    # Min-size convention (ui.py): the WM clamps window resizes to these
    # (fs-scaled).
    MIN_W = 310
    MIN_H = 230
    FOOT = 44            # the desk's foot (the target's): the rule to the frame's bottom
    # Where a desk row's label starts in the body: style.json's `group` `pad`
    # (held to it by tests/test_appearance_app.py), the foot's status's x.
    LABEL_X = 10

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
        self.under = 0
        if self.desk:
            # In a window the frame's rounded foot (`window_foot`) is the
            # foot's last rows: the rule drops by it, leaving the rows' region
            # its height with a blank under it, and the status stands as far
            # from the frame's bottom as the rows from its sides (design
            # review R3-27).
            self.under = window_foot() if self.windowed else 0
            foot, body = ui.cut_bottom(content, self.FOOT)
            self.foot = (foot[0], foot[1] + self.under, foot[2], foot[3] - self.under)
            self.body = ui.inset(body, 16, 0)
            # The pictures: the target's 192 x 112, three to a 680 window.
            self.card = (192, 112)
        else:
            # The hints' band: only where the input shows keys (touch shows
            # none, and the rows take the whole content rect, `full`).
            self.foot, self.body = ui.cut_bottom(content, 20 * fs)
            self.card = (66 * fs, 50 * fs)
        self.full = content


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
        self._keys = True           # the input in use shows keys: the hints take a band
        self._ch = None
        self._gen = None
        self._stale = 0
        self._dt = 0
        self._built = None          # what the frame draws from the choices (`_build`)
        self._whole = (0, 0, 0, 0)  # the page's rect, the canvas's
        # (name, the change's value, its swatch in dark, in light): a
        # preset is picked by its dark value and shows each mode's.
        by = dict((p[0], (p[0], p[1], p[1], p[2])) for p in theme_presets())
        for a in theme_accents():
            by.setdefault(a[0], (a[0], a[1], a[1], a[1]))
        self._accents = tuple(by[n] for n in OFFERED)
        self._tints = tuple(theme_tints())
        self._tint_words = ["None"] + [_title(t[0]) for t in self._tints]

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
        self._built = None

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
            self._built = None
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
        """A theme's modes, light first, as its Mode row names them: one for
        a single-mode theme (`_single_mode`), which shows no Mode row."""
        if self._single_mode(name):
            return ("Day",) if theme_modes(name) == ("light",) else ("Night",)
        return ("Day", "Night")

    @staticmethod
    def _single_mode(name):
        """True for a theme that declares one mode (its `modes`)."""
        return len(theme_modes(name)) == 1

    def _status(self, ch):
        """The desk's foot: the theme, its mode and where it comes from."""
        if self.note:
            return self.note
        dot = " · " if ui.has_symbols() else " - "
        name = ch["theme"]
        if name == "custom":
            return "Custom" + dot + "saved as you go"
        modes = self._modes(name)
        mode = modes[0 if ch["variant"] == "light" else len(modes) - 1]
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
            if title == "Wallpaper":
                art = (wid, "Wallpaper", "art")
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
        self._built = None
        self._refresh()

    # -- input -----------------------------------------------------------------

    def handle_input(self, inp):
        self._refresh()
        k = getattr(inp, "last_key", 0) or 0
        if inp.pressed("b") or (self.naming and k == 27):    # Esc typed in text mode
            self.back()
        if self._ui.keys(inp):
            self._damage.all()
        return True

    def has_page(self):
        """Whether a page of the app is open, which `back` steps out of:
        the name field or the wallpapers."""
        return bool(self.naming) or self.view == "walls"

    def back(self):
        """Back one level (B, Esc, the bar's chevron): the name field
        closes, the wallpapers go back to the rows. False on the rows: the
        host leaves the app."""
        if not self.has_page():
            return False
        if self.naming:
            self._close_name()
        else:
            self.view = "rows"
            self._focus = "Wallpaper"
        self._damage.all()
        return True

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
        whole = self._whole
        if whole[2] != cv.w or whole[3] != cv.h:
            whole = self._whole = (0, 0, cv.w, cv.h)
        self._ui.begin(cv, th, whole, lay.desk, lay.windowed)
        ui.page("")                   # the ground under the bar's band too
        view = self.view
        tab = self.tab
        naming = self.naming
        armed = self.armed
        why = self.why
        if self._focus is not None:
            ui.focus(self._focus)
            self._focus = None
        ch = self._ch
        b = self._build(th)
        # Under touch the handheld draws no hints, so no band holds them
        # (design review R3-07): the rows take the whole content rect.
        self._keys = lay.desk or ui.hints_width(_BACK) > 0
        r = lay.body if self._keys else lay.full
        ui.at(r[0], r[1], r[2], r[3])
        if view == "walls":
            ui.scroll("walls")
            self._walls_page(cv, th, ch, b)
        else:
            ui.scroll("page")
            self._kinds = b.kinds
            self._rows_page(cv, ch, b)
        ui.scroll()
        self._foot(cv, th, ch, b)
        self._name_dialog()
        self._focused = ui.focus()
        if (self.view != view or self.tab != tab or self.naming != naming
                or self.armed != armed or self.why != why):
            self._damage.all()        # an answer changed what the page shows

    def _foot(self, cv, th, ch, b):
        """The hints for the focused stop; on the desk under a rule, after
        the theme's status. Back is hinted where it goes back a level: on a
        page, and on the root where the bar's chevron leaves the app; a
        window's root has none, its title strip's X closes it."""
        lay = self.layout
        kind = self._kinds.get(self._focused, "step")
        if self.naming:
            items = _NAMING
        elif self.has_page() or not lay.windowed:
            items = _HINTS_BACK.get(kind, _STEP_BACK)
        else:
            items = _HINTS.get(kind, _STEP)
        if not lay.desk:
            if self._keys:
                r = lay.foot
                ui.at(r[0], r[1], r[2], r[3])
                ui.hints(items)
            return
        foot = lay.foot
        y = foot[1]
        h = foot[3] + lay.under       # centred between the rule and the frame's bottom
        body = lay.body
        bx = body[0]
        bw = body[2]
        cv.rect(bx, y, bw, 1, th["border"])
        hw = ui.hints_width(items)
        # The status: the body face in the dim ink, quieter than the rows,
        # on the rows' label column, cut to its room once per room.
        ui.face("ui")
        room = max(0, bw - hw - lay.LABEL_X)
        if b.status_room != room:
            text = self._status(ch)
            while len(text) > 1 and ui.text_size(text)[0] > room:
                text = text[:-1]
            b.status_room = room
            b.status = text
            b.status_h = ui.text_size(text)[1]
        cv.print(b.status, bx + lay.LABEL_X, y + 1 + (h - 1 - b.status_h) // 2,
                 th["ink_dim"])
        ui.face()
        ui.at(bx + bw - hw, y + 1, hw, h - 1)
        ui.hints(items)

    # -- the rows --------------------------------------------------------------

    def _rows_page(self, cv, ch, b):
        lay = self.layout
        ui.group("Theme")
        self._theme_cards(cv, b)
        # MODE: where the theme has two
        if len(b.modes) > 1:
            cur = b.mode_cur
            got = ui.seg("Mode", b.modes, cur)
            if got != cur:
                self._theme.set_variant("light" if got == 0 else "dark")
                self._acted()
        # ACCENT: the theme's own, then the offered seven in this mode; none
        # where the theme's look offers no accents (Contrast's one accent)
        if b.accents is not None:
            cur = b.accent_cur
            got = ui.swatches("Accent", b.accents, cur)
            if got != cur and got >= 0:
                self._change("accent", None if got == 0 else self._accents[got - 1][1])
        # TINT
        cur = b.tint_cur
        got = ui.cycle("Tint", self._tint_words, cur)
        if got != cur:
            self._change("tint", None if got == 0 else self._tints[got - 1][0])
        # WALLPAPER
        if ui.link("Wallpaper", b.wall_title):
            self.view = "walls"
            self.tab = b.wall_tab
            self._focus = b.wall_focus
        # WINDOW STYLE: where windows are
        if lay.desk:
            cur = b.frame_cur
            got = ui.cycle("Window style", b.frame_words, cur)
            if got != cur and got >= b.frame_off:
                self._change("frame", FRAMES[got - b.frame_off])
        # FONT
        cur = b.font_cur
        got = ui.cycle("Font", b.font_words, cur)
        if got != cur and got >= b.font_off:
            self._change("font", FONTS[got - b.font_off])
        # CODE FONT: the Code tab's face, where the image draws more than one
        if b.code_words is not None:
            cur = b.code_cur
            got = ui.cycle("Code font", b.code_words, cur)
            if got != cur and got >= b.code_off:
                self._change("code_font", ch["code_fonts"][got - b.code_off])
        # CHROME SIZE: where the panel takes more than one
        if b.chrome_words is not None:
            cur = b.chrome_cur
            got = ui.cycle("Chrome size", b.chrome_words, cur)
            if got != cur:
                self._theme.set_chrome_scale(ch["chromes"][got])
                self._acted()
        # the theme's file, and NAME on custom
        if ui.link("Theme file", "theme.json"):
            self._theme.edit_file()
            self._acted()
        if ch["theme"] == "custom":
            if ui.link("Name", "Keep as a theme"):
                self._open_name()
        ui.group()

    def _build(self, th):
        """What the frame draws that only the choices move, made once per
        read of them (`_refresh`): the cards, each with its id, words,
        override table and picture's colours, the rows' words, swatches and
        places, and each stop's kind for the hints. A frame that only
        scrolls makes nothing."""
        b = self._built
        if b is not None:
            return b
        return self._make(th)

    def _make(self, th):
        """`_build`'s making, its own function: the comprehensions' cells
        would otherwise be made on every frame's call of `_build`."""
        b = self._built = _Built()
        ch = self._ch
        variant = ch["variant"]
        name = ch["theme"]
        kinds = b.kinds
        dot = " · " if ui.has_symbols() else " - "
        for n in self._themes():
            cid = self._card_id(n)
            kinds[cid] = "card"
            b.cards.append((n, _title(n), dot.join(self._modes(n)),
                            {"id": cid, "current": n == name}, self._pic(n, variant)))
        b.modes = self._modes(name if name != "custom" else (ch["from"] or name))
        if len(b.modes) > 1:
            b.mode_cur = 0 if variant == "light" else 1
            kinds["Mode"] = "step"
        if self._accents_offered(ch):
            own = th["accent"] if ch["accent"] is None else None
            colours = [_rgb(own if own is not None else self._own_accent(ch))]
            side = 3 if variant == "light" else 2
            colours += [_rgb(a[side]) for a in self._accents]
            cur = 0
            if ch["accent"] is not None:
                cur = -1
                for i, a in enumerate(self._accents):
                    if a[1] == ch["accent"]:
                        cur = i + 1
            b.accents = colours
            b.accent_cur = cur
            kinds["Accent"] = "step"
        tints = [t[0] for t in self._tints]
        b.tint_cur = tints.index(ch["tint"]) + 1 if ch["tint"] in tints else 0
        kinds["Tint"] = "step"
        kinds["Wallpaper"] = "link"
        b.wall_tab, b.wall_title = self._wall_kind(ch)
        b.wall_focus = "Wallpaper " + b.wall_title
        frame = ch["frame"]
        if frame in FRAMES:
            b.frame_words, b.frame_cur, b.frame_off = FRAME_NAMES, FRAMES.index(frame), 0
        else:
            b.frame_words, b.frame_cur, b.frame_off = (OWN,) + FRAME_NAMES, 0, 1
        if self.layout.desk:
            kinds["Window style"] = "step"
        font = ch["font"]
        if font in FONTS:
            b.font_words, b.font_cur, b.font_off = FONT_NAMES, FONTS.index(font), 0
        else:
            b.font_words, b.font_cur, b.font_off = (OWN,) + FONT_NAMES, 0, 1
        kinds["Font"] = "step"
        codes = ch["code_fonts"]
        if len(codes) > 1:
            code = ch["code_font"]
            names = [CODE_FONT_NAMES[k] for k in codes]
            if code in codes:
                b.code_words, b.code_cur, b.code_off = names, codes.index(code), 0
            else:
                b.code_words, b.code_cur, b.code_off = [OWN] + names, 0, 1
            kinds["Code font"] = "step"
        chromes = ch["chromes"]
        if len(chromes) > 1:
            x = "×" if ui.has_symbols() else "x"
            b.chrome_cur = chromes.index(ch["chrome"]) if ch["chrome"] in chromes else 0
            b.chrome_words = ["%d%s" % (c, x) for c in chromes]
            kinds["Chrome size"] = "step"
        kinds["Theme file"] = "link"
        if name == "custom":
            kinds["Name"] = "link"
        return b

    def _accents_offered(self, ch):
        """Whether the theme in use (a custom one's source) offers accent
        picks: its look's `accents`, true where it is silent."""
        name = ch["theme"]
        src = (ch["from"] or name) if name == "custom" else name
        return theme_look(src, ch["variant"]).get("accents") != 0

    def _own_accent(self, ch):
        """The source's own accent while the theme sets another."""
        src = ch["from"] or ch["theme"]
        return theme_colors(src, ch["variant"])["accent"]

    def _theme_cards(self, cv, b):
        lay = self.layout
        w, h = lay.card
        ui.grid("themes", w, h)
        name = self._ch["theme"]
        for c in b.cards:
            r = ui.card(c[1], c[2], c[3])
            if r[2]:
                self._preview(cv, r, c[4], b)
            if r[4] & ui.ACTIVATED and c[0] != name:
                self._theme.set(c[0])
                self._acted()
        ui.grid()

    @staticmethod
    def _pic(name, variant):
        """Theme `name`'s card picture's colours and widths in `variant`:
        (ground, bar, bar line width, bar line, frame width, card radius,
        frame, surface, ink index, accent, value), the look's leaves where
        it sets them, else the tokens'."""
        tok = theme_colors(name, variant)
        lk = theme_look(name, variant)
        return (_look_colour(lk, "home_bg", tok["desktop"]),
                _look_colour(lk, "bar.fill", tok["bar"]),
                lk.get("bar.line.w", LOOK_NONE),
                _look_colour(lk, "bar.line.c", tok["border"]),
                lk.get("win.frame.w", LOOK_NONE),
                lk.get("card_r", 0),
                _look_colour(lk, "win.frame.c", tok["border"]),
                tok["surface"], _index(tok["ink"]), tok["accent"], tok["value"])

    def _preview(self, cv, r, p, b):
        """A theme's card picture (`_pic`), as its home shows it: its ground
        (the look's home ground, else `desktop`), its bar with the bar's
        line, a panel in its window frame (else a `border` hairline) at its
        card radius holding "Aa" in the live theme's title face and its ink,
        and its accent (and its value, where that is another colour) as
        dots."""
        x = r[0]
        y = r[1]
        w = r[2]
        h = r[3]
        lay = self.layout
        desk = lay.desk
        k = 1 if desk else lay.fs
        body = lay.body
        cv.clip(body[0], body[1], body[2], body[3])
        _fill(cv, x, y, w, h, p[0])
        bh = (14 if desk else 8) * k
        _fill(cv, x, y, w, bh, p[1])
        lw = _look_px(p[2], k)
        if lw:
            _fill(cv, x, y + bh - lw, w, lw, p[3])
        m = (14 if desk else 6) * k
        py = y + bh + (10 if desk else 5) * k
        pw = w - 2 * m
        ph = h - (py - y) - (10 if desk else 5) * k
        fw = _look_px(p[4], k) or k
        rad = p[5]
        rad = min(max(0, rad) * k // 2, ph // 3) if rad != LOOK_NONE else 2 * k
        _shape(cv, x + m, py, pw, ph, rad, p[6])
        _shape(cv, x + m + fw, py + fw, pw - 2 * fw, ph - 2 * fw, max(0, rad - fw), p[7])
        self._aa(cv, x + 2 * m, py, ph, p[8], b)
        d = (16 if desk else 8) * k
        xr = x + m + pw - m - d
        acc = p[9]
        val = p[10]
        if val != acc:
            _shape(cv, xr, py + ph // 2 - d - k, d, d, d // 2, acc)
            _shape(cv, xr, py + ph // 2 + k, d, d, d // 2, val)
        else:
            _shape(cv, xr, py + (ph - d) // 2, d, d, d // 2, acc)
        cv.clip()

    @staticmethod
    def _aa(cv, x, y, h, c, b):
        """"Aa" centred down a panel of height `h` at `x`: the live theme's
        largest face whose line is at most half the panel, at its own size
        (its anti-aliasing unscaled), else the print font at the whole scale
        that fits. The face is chosen once per panel height (the focused
        card's panel is taller)."""
        a = b.aa.get(h)
        if a is None:
            a = None
            for role in _AA_FACES:
                if ui.face(role, "Aa"):
                    lh = ui.text_size("Aa")[1]
                    ui.face()
                    if 2 * lh <= h:
                        a = (role, lh, 1)
                        break
            if a is None:
                big = max(1, h // (2 * ui.text_size("Aa")[1]))
                a = (None, ui.text_size("Aa", big)[1], big)
            b.aa[h] = a
        if a[0] is not None and ui.face(a[0], "Aa"):
            cv.print("Aa", x, y + (h - a[1]) // 2, c)
            ui.face()
            return
        cv.print_scaled(b"Aa", x, y + (h - a[1]) // 2, c, a[2])

    # -- the wallpapers --------------------------------------------------------

    def _walls_page(self, cv, th, ch, b):
        lay = self.layout
        got = ui.tabs("Wallpapers", TABS, self.tab)
        if got != self.tab:
            self.tab = got
        cards = b.walls.get(self.tab)
        if cards is None:
            cards = b.walls[self.tab] = []
            kinds = b.walls_kinds[self.tab] = {"Wallpapers": "tabs"}
            for item in self._walls(self.tab):
                cid = "Wallpaper " + item[1]
                kinds[cid] = "card"
                cards.append((item, {"id": cid, "current": self._wall_on(ch, item)}))
        self._kinds = b.walls_kinds[self.tab]
        w, h = lay.card
        body = lay.body
        ui.grid("walls", w, h)
        for c in cards:
            item = c[0]
            on = c[1]["current"]
            r = ui.card(item[1], "", c[1])
            if r[2]:
                cv.clip(body[0], body[1], body[2], body[3])
                if on:
                    # the wallpaper in force: the backdrop's own still
                    self._wall.preview(cv, r[:4], self._dt)
                else:
                    self._draw_wall(cv, th, r[:4], item)
                cv.clip()
            if r[4] & ui.ACTIVATED:
                if not on:
                    self._change("wallpaper", item[0])
                self.view = "rows"
                self._focus = "Wallpaper"
        ui.grid()
        if not cards:
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


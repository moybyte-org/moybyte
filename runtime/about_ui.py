# Map (grep -n a name to jump there):
#   version_words       the firmware's version and channel, as About says them
#   wrap_lines          paragraphs broken into lines a width wide, in the ui face
#   LicencesPage        Settings' Licences: the parts and their licences, a licence's text
#   AboutCard           the system menu's About: Moy, the version, the board, two rows
"""About on the console (docs/look_2026-10.md step 32): the system menu's
About card and Settings' Licences page, on the public UI API's verbs.

* Licences is a page of Settings (its About group's last row): Moybyte's
  licence in a line, then `ui.group`s of `ui.link` rows -- Moybyte's parts,
  and what it is made with (`runtime/licences.py`, generated from the texts
  THIRD_PARTY.md names, the parts this board's image holds). A row opens its
  licence's text, paragraphs kept, as a page under it that the arrows
  scroll; on the desk the list stands at the left and the focused part's
  text at the right, which PgUp and PgDn scroll.
* About is a card over the washed content, hung under the bar: Moy, the
  wordmark, the version and the board, then Updates (the updater's state;
  no row where there is no updater) and Licences. A touch board's card has
  a close in its corner; Esc, B or a tap outside closes it.
"""

try:
    import ui as _ui
    import ui_draw as _ui_draw
    import layout_base as _lb
    import licences as _lic
    import achievements_ui as _ach
except ImportError:  # pragma: no cover - host fallback
    from runtime import ui as _ui
    from runtime import ui_draw as _ui_draw
    from runtime import layout_base as _lb
    from runtime import licences as _lic
    from runtime import achievements_ui as _ach

try:
    from moy_input import KEY_PGUP, KEY_PGDN
except ImportError:  # pragma: no cover - host fallback
    from runtime.moy_input import KEY_PGUP, KEY_PGDN

_LEAD = {"tone": "dim", "wrap": True, "lines": 3, "inset": True}
_LEAD_DESK = {"tone": "dim", "wrap": True, "lines": 3}
_DIM = {"tone": "dim"}
_TEXT = {}
_CAPTION = {"face": "caption", "tone": "dim"}
_NAME = {"face": "title"}
_HINT_LIST = ("confirm", "Read", "back", "Back")
_HINT_TEXT = ("scroll", "Scroll", "back", "Back")
_HINT_DESK = ("scroll", "Move", "PgUp PgDn", "Scroll", "back", "Back")
_CHANNELS = {"stable": "Stable", "unstable": "Beta"}


def _desk():
    return _lb.metric_class() == _lb.DESK


def version_words(ws):
    """The firmware's version and channel as About says them ("0.8 ·
    Stable"), None where the console has no updater (the host)."""
    u = ws.updater
    if u is None:
        return None
    try:
        v = str(u.version_label())
    except Exception:  # noqa: BLE001
        return None
    ch = _CHANNELS.get(ws._ota_channel())
    return v + " · " + ch if ch else v


def board_words(name):
    """A board's name as the ui face can draw it: its inch mark (″) a
    straight quote where the face has none."""
    if name and "″" in name and not _ui.face("ui", name):
        return name.replace("″", '"')
    _ui.face()
    return name


def board_of():
    """This image's board (`[board] ota`), None off a board."""
    try:
        import moy_ota
    except ImportError:
        return None
    return getattr(moy_ota, "BOARD", None)


def wrap_lines(paras, w):
    """`paras` broken at word breaks into lines at most `w` pixels wide in
    the ui face, a "" line between paragraphs."""
    out = []
    _ui.face("ui")
    space = _ui.text_size(" ")[0]
    for p in paras:
        if out:
            out.append("")
        line, lw = "", 0
        for word in p.split():
            ww = _ui.text_size(word)[0]
            if line and lw + space + ww > w:
                out.append(line)
                line, lw = word, ww
            else:
                line = word if not line else line + " " + word
                lw = ww if lw == 0 else lw + space + ww
        if line:
            out.append(line)
    _ui.face()
    return out


class LicencesPage:
    """Settings' Licences page (`SettingsLayer` draws it on its sub page's
    host): the parts and their licences, and a part's licence text."""

    def __init__(self, ws):
        self.ws = ws
        self.entry = None             # the part whose text is open (the handheld's page)
        self._groups = None           # (caption, (entry, ...)) this image holds
        self._ids = {}                # a row's id -> its entry
        self._shown = None            # the entry the text below is
        self._lines = ()              # its text's lines
        self._lw = -1                 # ... broken this wide
        self._gen = -1                # ... in this look
        self._top = 0                 # the first line shown
        self._rows = 0                # the lines that show
        self._key = 0                 # the key typed last frame (a press is its change)
        self._foot_of = None          # the desk foot's entry, and what it says
        self._foot_status = ""
        self._focus = None

    def open(self):
        self.entry = None
        self._top = 0
        self._focus = None

    def title(self):
        if self.entry is not None and not _desk():
            return self.entry[0]
        return "Licences"

    def back(self):
        """Esc, B or the bar's way back: an open text closes onto its row.
        False where none was open."""
        if self.entry is None or _desk():
            return False
        self._focus = self.entry[0]
        self.entry = None
        self._top = 0
        self.ws._dirty = True
        return True

    def keys(self, i):
        """The text's scroll: the arrows on a handheld's text page, PgUp and
        PgDn on the desk. True where it took the keys."""
        k = getattr(i, "last_key", 0) or 0
        if not isinstance(k, int):
            k = 0
        typed = k != 0 and k != self._key
        self._key = k
        step = 0
        if _desk():
            if typed and k in (KEY_PGUP, KEY_PGDN):
                step = (self._rows - 2) * (1 if k == KEY_PGDN else -1)
        elif self.entry is not None:
            if i.steps("down"):
                step = 3
            elif i.steps("up"):
                step = -3
        if not step:
            return False
        top = max(0, min(self._top + step, len(self._lines) - self._rows))
        if top != self._top:
            self._top = top
            self.ws._dirty = True
        return True

    def _parts(self):
        if self._groups is None:
            board = board_of()
            groups = []
            for cap, entries in _lic.GROUPS:
                got = tuple(e for e in entries if _lic.shown(e, board))
                if got:
                    groups.append((cap, got))
                for e in got:
                    self._ids[e[0]] = e
            self._groups = tuple(groups)
        return self._groups

    def _text(self, entry, w):
        """`entry`'s lines `w` wide, broken again when the entry, the width
        or the look moves."""
        gen = self.ws.look.look_gen
        if entry is not self._shown or w != self._lw or gen != self._gen:
            self._shown, self._lw, self._gen = entry, w, gen
            self._lines = tuple(wrap_lines(_lic.text(entry), w))
            self._top = 0
        return self._lines

    # -- the frame -----------------------------------------------------------

    def draw(self, rect):
        if _desk():
            self._draw_desk(rect)
        elif self.entry is not None:
            self._draw_text()
        else:
            self._draw_list()

    def _list(self):
        """The groups' rows; the entry opened this frame, or None."""
        got = None
        for cap, entries in self._parts():
            _ui.group(cap)
            for e in entries:
                if _ui.link(e[0], e[1]):
                    got = e
            _ui.group()
        return got

    def _draw_list(self):
        _ui.page("")
        _ui.hints(_HINT_LIST)
        if self._focus is not None:
            _ui.focus(self._focus)
            self._focus = None
        _ui.label(_lic.LEAD, _LEAD)
        got = self._list()
        if got is not None:
            self.entry = got
            self._top = 0
            self.ws._dirty = True

    def _lines_at(self, x, y, w, h, lh):
        """The text's lines from `_top` placed in the rect, `lh` apart (half
        that between paragraphs), the last that shows faded where the text
        runs on."""
        lines = self._lines
        pad = 4 * (1 if _desk() else self.ws.layout.fs)   # a placed label's own inset
        self._rows = max(1, h // lh)
        k, at, foot = self._top, y, y + h
        while k < len(lines) and at + lh <= foot + lh // 2:
            if lines[k]:
                _ui.at(x - pad, at, w + 4 * pad, lh)
                _ui.label(lines[k], _TEXT)
                at += lh
            else:
                at += lh // 2
            k += 1
        if k < len(lines):
            fh = lh + lh // 2
            _ui_draw.fade(self.ws.sys_canvas, (x - pad, foot - fh, w + 2 * pad, fh),
                          self.ws.theme_colors["panel"])

    def _draw_text(self):
        x, y, w, h = _ui.page("")
        _ui.hints(_HINT_TEXT)
        fs = self.ws.layout.fs
        if _ui.hints_width(_HINT_TEXT) > 0:
            h -= 22 * fs                  # the hint band at the foot
        e = self.entry
        _ui.at(x + 12 * fs, y + 6 * fs, w - 24 * fs, 16 * fs)
        _ui.label(e[2], _CAPTION)
        self._text(e, w - 36 * fs)
        top = y + 24 * fs
        self._lines_at(x + 16 * fs, top, w - 32 * fs, y + h - top, 16 * fs)

    def _draw_desk(self, rect):
        x, y, w, h = _ui.page("")
        h -= _ach.DESK_FOOT
        if self._focus is not None:
            _ui.focus(self._focus)
            self._focus = None
        lw = 268
        _ui.at(x + 12, y + 14, lw, 44)
        _ui.label(_lic.LEAD, _LEAD_DESK)
        _ui.at(x, y + 58, lw + 24, h - 58)
        _ui.scroll("Licences list")
        self._list()
        _ui.scroll()
        e = self._ids.get(_ui.focus())
        if e is None:
            e = self._shown or self._parts()[0][1][0]
        px = x + lw + 36
        cv_th = self.ws.theme_colors
        self.ws.sys_canvas.rect(px - 12, y, 1, h, cv_th["border"])
        tw = x + w - px - 28
        _ui.at(px + 8, y + 16, tw, 28)
        _ui.label(e[0], _NAME)
        _ui.at(px + 8, y + 44, tw, 20)
        _ui.label(e[2], _DIM)
        self._text(e, tw - 16)
        self._lines_at(px + 8, y + 76, tw - 16, h - 76, 21)
        if e is not self._foot_of:
            self._foot_of = e
            self._foot_status = "%s · %s" % (e[0], e[1])
        _ach.desk_foot(self.ws, rect, self._foot_status, _HINT_DESK)


class AboutCard:
    """The system menu's About: a card over the washed content, hung under
    the bar. `draw` answers False the frame it is dismissed."""

    def __init__(self, ws):
        self.ws = ws
        self.host = _ui_draw.AppHost()
        self._words = None            # (version, board, updates) for this open
        self._fresh = True

    def open(self):
        self.host.reset()
        ws = self.ws
        v = version_words(ws)
        b = board_of()
        try:
            from settings_layer import BOARDS
        except ImportError:
            from runtime.settings_layer import BOARDS
        upd = None
        if ws.updater is not None:
            ready = getattr(ws.update_ui, "offered", None)
            upd = ("%s is ready" % ready) if ready else "Up to date"
        self._words = ("Version " + v if v else None,
                       board_words(BOARDS.get(b, b)) if b else None, upd)

    def rect(self):
        """The card's (x, y, w) on the system canvas."""
        ws = self.ws
        lay = ws.layout
        desk = _desk()
        fs = lay.fs
        w = 420 if desk else (300 if lay.cw >= 440 * fs else 248) * fs
        top = lay.cy + (52 if desk else (16 if lay.cw >= 440 * fs else 8) * fs)
        return (lay.cx + (lay.cw - w) // 2, top, w)

    def draw(self):
        ws = self.ws
        cv = ws.sys_canvas
        lay = ws.layout
        desk = _desk()
        fs = 2 if desk else lay.fs
        if self._words is None:
            self.open()
        ver, board, upd = self._words
        x, top, w = self.rect()
        settled = self.host.begin(cv, ws.theme_colors, (lay.cx, lay.cy, lay.cw, lay.ch), desk)
        _ui.at(x, top - 4 * (1 if desk else lay.fs), w, 0)
        shown = _ui.popover("About", w)
        n = 32 * fs
        head = n + (52 if desk else 30) + (44 if desk else 30) * (1 if desk else lay.fs)
        r = _ui.part("About head", 0, head, {"disabled": True, "fill": "surface"})
        if r[2]:
            cx = r[0] + r[2] // 2
            ws.draw_icon("moy", (cx - n // 2, r[1], n, n), ws.theme_colors["value"], cv)
            y = r[1] + n + (14 if desk else 8 * lay.fs)
            lh = (26 if desk else 17 * lay.fs)
            _ui.at(r[0], y, r[2], lh + 6)
            _ui.label("moybyte", _WORDMARK)
            y += lh + 6
            for line in (ver, board):
                if line:
                    _ui.at(r[0], y, r[2], 20 if desk else 15 * lay.fs)
                    _ui.label(line, _CENTRE_DIM)
                    y += 22 if desk else 15 * lay.fs
        _ui.rule()
        if upd is not None and _ui.link("Updates", upd):
            self.close()
            ws.open_settings()
            ws.settings_layer.show_row("ota_channel")
        if _ui.link("Licences"):
            self.close()
            ws.open_settings()
            ws.settings_layer.open_page("licences")
        if desk:
            _ui.hints(_HINT_ABOUT)
        _ui.popover()
        strip = self._strip()
        if strip is not None:
            # the home's hint strip, which the wash leaves clear, says the card's keys
            cv.rect(strip[0], strip[1], strip[2], strip[3], ws.theme_colors["panel"])
            _ui.at(strip[0], strip[1], strip[2], strip[3])
            _ui.hints(_HINT_ABOUT)
        if _ui.input_kind() == "touch":
            c = self.host.box("About")
            if c is not None:
                _ui.at(c[0] + c[2] - 34 * lay.fs, c[1] + 6 * lay.fs, 28 * lay.fs, 28 * lay.fs)
                if _ui.tool("close", "Close"):
                    self.close()
        if not shown:
            self.close()
        if not settled:
            ws.request_frame()

    def _strip(self):
        """The home's hint strip under the card, (x, y, w, h), or None (an
        arrangement with no foot strip)."""
        ws = self.ws
        if ws.screen != "launcher" or _desk():
            return None
        foot = getattr(ws.launcher_layer.grid, "foot", None)
        foot = foot() if foot is not None else None
        if foot is None or foot[5][3] <= 0:
            return None
        return foot[5]

    def close(self):
        ws = self.ws
        ws._about = False
        self._words = None
        ws._dirty = True


_WORDMARK = {"face": "title", "align": "center"}
_HINT_ABOUT = ("confirm", "Open", "back", "Close")
_CENTRE_DIM = {"tone": "dim", "align": "center"}

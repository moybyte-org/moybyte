# Map (grep -n a name to jump there):
#   version_words       the firmware's version and channel, as About says them
#   wrap_lines          paragraphs broken into lines a width wide, in the ui face
#   LicencesPage        Settings' Licences: the parts and their licences, a licence's text
"""About on the console (docs/history/look_2026-10.md step 32): Settings'
Licences page on the public UI API's verbs, and the words About says (the
version, the board) that the system menu's About card
(`console_notices.AboutCard`) and Settings' About rows share.

* Licences is a page of Settings (its About group's last row): Moybyte's
  licence in a line, then `ui.group`s of `ui.link` rows -- Moybyte's parts,
  and what it is made with (`runtime/system/licences.py`, generated from the texts
  THIRD_PARTY.md names, the parts this board's image holds). A row opens its
  licence's text, paragraphs kept, as a page under it that the arrows
  scroll; on the desk the list stands at the left and the focused part's
  text at the right, which PgUp and PgDn scroll.

The page reaches the shell through its AppContext (`LicencesPage.NEEDS`).
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


def version_words(updater, channel):
    """The firmware's version and its update `channel` as About says them
    ("0.8 · Stable"), None where the console has no `updater` (the host)."""
    if updater is None:
        return None
    try:
        v = str(updater.version_label())
    except Exception:  # noqa: BLE001
        return None
    ch = _CHANNELS.get(channel)
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


# The list's override table, the handheld's page and the desk's scroll region
# alike: the parts this image holds never change while it runs, so its gen
# never moves (docs/history/scroll_and_text_2026-10.md §3.3).
_LIST_PAGE = {"gen": 0}


class LicencesPage:
    """Settings' Licences page (`SettingsLayer` draws it on its sub page's
    host): the parts and their licences, and a part's licence text."""

    NEEDS = ("surface", "theme", "damage")

    def __init__(self, ctx):
        self._surf = ctx.surface
        self._theme = ctx.theme
        self._damage = ctx.damage
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
        self._damage.all()
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
            self._damage.all()
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
        gen = self._theme.gen()
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
        _ui.page("", _LIST_PAGE)
        _ui.hints(_HINT_LIST)
        if self._focus is not None:
            _ui.focus(self._focus)
            self._focus = None
        _ui.label(_lic.LEAD, _LEAD)
        got = self._list()
        if got is not None:
            self.entry = got
            self._top = 0
            self._damage.all()

    def _lines_at(self, x, y, w, h, lh):
        """The text's lines from `_top` placed in the rect, `lh` apart (half
        that between paragraphs), the last that shows faded where the text
        runs on."""
        lines = self._lines
        pad = 4 * (1 if _desk() else self._surf.font_scale())   # a placed label's own inset
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
            _ui_draw.fade(self._surf.canvas(), (x - pad, foot - fh, w + 2 * pad, fh),
                          self._theme.colors()["panel"])

    def _draw_text(self):
        x, y, w, h = _ui.page("")
        _ui.hints(_HINT_TEXT)
        fs = self._surf.font_scale()
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
        _ui.scroll("Licences list", _LIST_PAGE)
        self._list()
        _ui.scroll()
        e = self._ids.get(_ui.focus())
        if e is None:
            e = self._shown or self._parts()[0][1][0]
        px = x + lw + 36
        cv = self._surf.canvas()
        th = self._theme.colors()
        cv.rect(px - 12, y, 1, h, th["border"])
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
        _ach.desk_foot(cv, th, rect, self._foot_status, _HINT_DESK)

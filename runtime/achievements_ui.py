# Map (grep -n a name to jump there):
#   AchievementsUI              the three Easter eggs: their trigger state and steps
#   AchievementsPage            Settings' Achievements page: the progress, the badges, a popover
#   AchievementsPage._cards     the grids' rows as the page draws them, made on a change
#   AchievementsPage._popover   a badge opened: its tile, name, how and the day it was found
#   unlock_banner               the system's unlock banner over any surface (GAPS 47)
"""Achievements on the console (#21, docs/look_2026-10.md step 32): the
hidden Easter eggs, Settings' Achievements page and the unlock banner.

The achievement CORE is `widgets.Achievements` on the Workstation (`ws.ach`),
wired by `console_notices.Notices.load_achievements`; the store's document is
moy_carts' achievements.json. This module draws, and keeps the eggs.

* The eggs: the Konami code on the home, seven taps on the bar's clock and
  five on Settings' title, each awarding its hidden badge through
  `ws.ach.award`. A first find shows the unlock banner ("You found a
  secret!", with confetti); a second shows nothing.
* The page is a page of Settings (`SettingsLayer`, Settings' way back): the
  count as `ui.progress`'s `count`, then "Found" and "To find", each a card
  grid a row at a time (a row as tall as its tallest caption) of
  `ui.card(name, how)` over the badge's `icon` tile, a locked one veiled
  (`locked`) and a secret one "Secret" until found. Enter or a tap opens a
  badge's `ui.popover`, hung from its card. The desk's foot names the
  focused badge. Everything a frame draws is made when what it shows
  changes, so an idle or scrolled frame allocates nothing.
* The banner (`unlock_banner`) is native/moy_ui's `moy_ui_unlock`, drawn on
  the system canvas at the top centre: under the bar where there is one, at
  the glass's top over a fullscreen game.
"""

try:
    import ui as _ui
    import ui_draw as _ui_draw
    import layout_base as _lb
    from widgets import ACHIEVEMENTS
except ImportError:  # pragma: no cover - host fallback
    from runtime import ui as _ui
    from runtime import ui_draw as _ui_draw
    from runtime import layout_base as _lb
    from runtime.widgets import ACHIEVEMENTS

try:                                    # device: ticks is frozen flat
    from ticks import _ticks_ms, _ticks_diff
except ImportError:                     # host: the runtime package
    from runtime.ticks import _ticks_ms, _ticks_diff


TITLE = "Achievements"
NONE_LINE = "Play, draw and make things to find them."
SECRET = "Secret"
SECRET_HOW = "Keep exploring"
FOUND, TO_FIND = "Found", "To find"
NOT_FOUND = "Not found yet"
HEAD_BADGE = "You found a badge!"
HEAD_SECRET = "You found a secret!"
BURST_MS = 420                 # the confetti's burst, from the banner's edges out
DESK_IN = 20                   # a desk page's inset from its window's body, each side
DESK_FOOT = 32                 # the desk window's foot: the focused badge and the keys
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December")
_HINT_PAGE = ("confirm", "Open", "back", "Back")
_HINT_POP = ("back", "Close")
_COUNT = {"count": True}
_DIM = {"tone": "dim"}
_LINE = {"tone": "dim", "inset": True}
_DIM_WRAP = {"tone": "dim", "wrap": True, "lines": 2}
_NAME = {"face": "title"}
_VALUE = {"tone": "value"}
_PART = {"disabled": True, "fill": "surface", "id": "Badge picture"}
_LOCKED = {"locked": True}


def head_line(n, total):
    """The page's count, said as its progress row says it."""
    if n == 0:
        return "None found yet"
    if n >= total:
        return "All %d found!" % total
    return "%d of %d found" % (n, total)


def day_line(day, today=0):
    """When a badge was found, as its popover says it: "Found 12 October"
    (its year where it is not this one), "Found" where the console had no
    clock."""
    if not day:
        return "Found"
    out = "Found %d %s" % (day % 100, _MONTHS[(day // 100 % 100 - 1) % 12])
    if today and today // 10000 != day // 10000:
        out += " %d" % (day // 10000)
    return out


def _desk():
    return _lb.metric_class() == _lb.DESK


class AchievementsUI:
    """The three hidden Easter eggs: their trigger state and their steps.
    Each awards its hidden badge (`ws.ach.award`), whose first find is the
    unlock banner; the Konami code's badge, and every secret's, bursts
    confetti from it."""

    _KONAMI = ("up", "up", "down", "down", "left", "right", "left", "right", "b", "a")
    _CLOCK_TAP_GOAL = 7
    _SECRET_TAP_GOAL = 5

    def __init__(self, ws):
        self.ws = ws
        self._konami_pos = 0          # how far into the Konami sequence we are (home)
        self._clock_taps = 0          # clock taps on the bar (Time Traveler)
        self._secret_taps = 0         # Settings-title taps (Secret Finder's door)

    def konami_watch(self, i):
        """The Konami Easter egg (#21) watches every button press on the
        home, the bar's over it included, for the secret sequence: a passive
        observer, so the nav still runs."""
        for b in self._KONAMI:
            if i.pressed(b):
                self._konami_step(b)
                return

    def _konami_step(self, name):
        """Advance the home's Konami sequence on a button press; the full code in
        order awards "Secret Coder". A wrong key restarts (but still counts if it's
        the sequence's first key, so a fresh start works)."""
        seq = self._KONAMI
        if name == seq[self._konami_pos]:
            self._konami_pos += 1
        else:
            self._konami_pos = 1 if name == seq[0] else 0
        if self._konami_pos >= len(seq):
            self._konami_pos = 0
            self.ws.ach.award("konami")

    def _tap_clock(self):
        """Count a clock tap; the _CLOCK_TAP_GOAL'th in a row awards "Time
        Traveler". Any other bar tap resets the run."""
        self._clock_taps += 1
        if self._clock_taps >= self._CLOCK_TAP_GOAL:
            self._clock_taps = 0
            self.ws.ach.award("clock_tinker")

    def _tap_secret_door(self):
        """Count a Settings-title tap (the hidden door); the _SECRET_TAP_GOAL'th
        knocks it open -> "Secret Finder"."""
        self._secret_taps += 1
        if self._secret_taps >= self._SECRET_TAP_GOAL:
            self._secret_taps = 0
            self.ws.ach.award("secret_door")


class _Card:
    """One badge's card as the page draws it, made when the page's rows are."""

    __slots__ = ("id", "title", "sub", "o", "cur", "icon", "got", "how", "status")

    def __init__(self, a, got):
        secret = a[3] and not got
        self.id = "Badge " + a[0]
        self.title = SECRET if secret else a[1]
        self.sub = SECRET_HOW if secret else a[4]
        self.icon = "secret" if secret else a[2]
        self.got = got
        self.how = self.sub
        self.o = {"id": self.id, "icon": self.icon}
        if not got:
            self.o["locked"] = True
        self.cur = dict(self.o)
        self.cur["current"] = True
        self.status = None


class AchievementsPage:
    """Settings' Achievements page: drawn by `SettingsLayer` on its sub
    page's host, its way back Settings'. `draw` answers nothing; `back`
    closes an open popover first."""

    def __init__(self, ws):
        self.ws = ws
        self.pop = None               # the _Card whose popover is open
        self._focus = None            # the card focus moves to next frame
        self._pop_lines = ("", "")    # the open popover's how and state lines
        # the rows as `_cards` made them, and what they were made from
        self._made_n = -1
        self._made_w = -1
        self._made_desk = None
        self._made_gen = -1
        self._head = ""
        self._groups = ()             # ((caption, ((grid id, grid override, (_Card, ...)), ...)), ...)
        self._by_id = {}
        self._card = (0, 0)           # the pictures' size
        self._foot_of = None          # the desk foot's status, by the card it names
        self._foot_status = ""

    def open(self):
        self.pop = None
        self._focus = None
        self._foot_of = None

    def back(self):
        """Esc, B or the bar's way back: an open popover closes. False where
        none was open (Settings then leaves the page)."""
        if self.pop is None:
            return False
        self._focus = self.pop.id
        self.pop = None
        self.ws._dirty = True
        return True

    # -- the rows ------------------------------------------------------------

    def _geometry(self, w, desk, fs):
        """(columns, picture w, picture h, margin) for a page `w` wide (a
        desk's, its window's body less its two DESK_IN): a desk's columns
        104 wide, as many as it holds."""
        if desk:
            cols = max(3, w // 104)
            return cols, 92, 68, (w - cols * 104) // 2 + 6
        if w >= 440 * fs:
            cols, pw, ph = 4, 100 * fs, 72 * fs
        else:
            cols, pw, ph = 3, 84 * fs, 62 * fs
        col = (w - 32 * fs) // cols
        return cols, pw, ph, 16 * fs + (col - pw) // 2

    def _cards(self, w, desk, fs):
        """The page's rows, made again when the badges found, the page's
        width or the look (a face's widths) move."""
        ach = self.ws.ach
        n = ach.count()
        gen = self.ws.look.look_gen
        if (n == self._made_n and w == self._made_w and desk == self._made_desk
                and gen == self._made_gen):
            return
        self._made_n, self._made_w, self._made_desk, self._made_gen = n, w, desk, gen
        cols, pw, ph, margin = self._geometry(w, desk, fs)
        self._card = (pw, ph)
        pad = 7 if desk else 5
        room = pw + 2 * pad * fs - 2 * fs
        _ui.face("ui")
        groups = []
        self._by_id = {}
        k = 0
        for cap, want in ((FOUND, True), (TO_FIND, False)):
            cards = [_Card(a, want) for a in ACHIEVEMENTS if ach.has(a[0]) == want]
            if not cards:
                continue
            rows = []
            for i in range(0, len(cards), cols):
                row = tuple(cards[i:i + cols])
                two = False
                for c in row:
                    self._by_id[c.id] = c
                    tw = _ui.text_size(c.title)[0]
                    if tw > room and " " in c.title:
                        two = True
                # the card's foot: its name's lines and its how-to line
                foot = (64 if two else 40) if desk else (41 if two else 27) * fs
                o = {"cols": cols, "margin": margin, "foot": foot, "wrap": True}
                if i == 0 or desk:
                    o["top"] = 3 if i == 0 else 1
                rows.append(("grid%d" % k, o, row))
                k += 1
            groups.append((cap, tuple(rows)))
        _ui.face()
        self._groups = tuple(groups)
        self._head = head_line(n, len(ACHIEVEMENTS))
        self._foot_of = None

    def _open(self, c):
        """Open card `c`'s popover: its lines made now, once."""
        ach = self.ws.ach
        aid = c.id[6:]
        if c.got:
            state = day_line(ach.found_on(aid) or 0, self.ws._today_ymd())
        else:
            got = ach.progress(aid)
            state = "%d of %d" % got if got and got[0] else NOT_FOUND
        self._pop_lines = (c.how, state)
        self.pop = c
        self.ws._dirty = True

    # -- the frame -----------------------------------------------------------

    def draw(self, rect):
        """The page over `rect`, on the Settings host begun by
        `SettingsLayer`; on the desk its foot under it."""
        desk = _desk()
        fs = self.ws.layout.fs if not desk else 1
        x, y, w, h = _ui.page("")
        if desk:
            # the badges scroll in the body over the window's own foot
            w -= 2 * DESK_IN
            _ui.at(x + DESK_IN, y, w, h - DESK_FOOT)
            _ui.scroll("Achievements")
        else:
            _ui.hints(_HINT_POP if self.pop is not None else _HINT_PAGE)
        self._cards(w, desk, fs)
        if self._focus is not None:
            _ui.focus(self._focus)
            self._focus = None
        n = self.ws.ach.count()
        _ui.progress(self._head, n, len(ACHIEVEMENTS), _COUNT)
        if n == 0:
            _ui.label(NONE_LINE, _LINE)
        pw, ph = self._card
        pop = self.pop
        for cap, rows in self._groups:
            _ui.group(cap)
            for gid, o, cards in rows:
                _ui.grid(gid, pw, ph, o)
                for c in cards:
                    r = _ui.card(c.title, c.sub, c.cur if c is pop else c.o)
                    if r[4] & _ui.ACTIVATED and pop is None:
                        self._open(c)
                _ui.grid()
            _ui.group()
        if desk:
            _ui.scroll()
            self._foot(rect)
        if self.pop is not None:
            self._popover(desk, fs)

    def _foot(self, rect):
        """The desk's foot: the focused badge's name, how it is found and
        its state, the keys at its right."""
        f = self.pop.id if self.pop is not None else _ui.focus()
        if f != self._foot_of:
            self._foot_of = f
            c = self._by_id.get(f)
            self._foot_status = "" if c is None else "%s · %s · %s" % (
                c.title, c.how, "found" if c.got else "not found yet")
        desk_foot(self.ws, rect, self._foot_status,
                  _HINT_POP if self.pop is not None else _HINT_PAGE)

    def _popover(self, desk, fs):
        c = self.pop
        s = 2 if desk else fs
        tw, th = 64 * s, 48 * s
        if not _ui.popover("Badge", 420 if desk else 240 * fs):
            self.back()
        r = _ui.part("Badge picture", tw, th, _PART)
        if r[2]:
            _ui.tile(r[0], r[1], r[2], r[3], c.icon, None if c.got else _LOCKED)
            tx = r[0] + tw + 10 * s
            lw = max(0, (420 if desk else 240 * fs) - tw - 30 * s)
            lh = (24 if desk else 15) * fs
            how, state = self._pop_lines
            _ui.at(tx, r[1] + (6 if desk else 0), lw, lh + 4 * s)
            _ui.label(c.title, _NAME)
            _ui.at(tx, r[1] + (6 if desk else 0) + lh + 4 * s, lw, lh)
            _ui.label(how, _DIM)
            _ui.at(tx, r[1] + (6 if desk else 0) + 2 * lh + 4 * s, lw, lh)
            _ui.label(state, _VALUE if c.got else _DIM)
        _ui.popover()


def desk_foot(ws, rect, status, hints):
    """A desk Settings window's foot under a page of `rect`'s: a rule,
    `status` at its left in the dim ink and the `hints` at its right."""
    x, y, w, h = rect
    fy = y + h - DESK_FOOT
    ws.sys_canvas.rect(x + 16, fy, w - 32, 1, ws.theme_colors["border"])
    hw = _ui.hints_width(hints)
    if status:
        _ui.at(x + 18, fy + 10, max(0, w - hw - 40), 22)
        _ui.label(status, _DIM)
    _ui.at(x + w - hw - 12, fy + 6, hw, 26)
    _ui.hints(hints)


def unlock_banner(ws):
    """The unlock banner over whatever is up (`ws.ach.toast`), at the top
    centre of the glass: under the bar where there is one, at the glass's
    top over a fullscreen game. A secret's bursts confetti from its edges."""
    t = ws.ach.toast
    if t is None:
        return
    cv = ws.sys_canvas
    lay = ws.layout
    desk = _desk()
    wm = ws.wm
    game = (wm.top_is_player() and getattr(wm, "mode", None) != "desktop"
            and not ws._running_cart_shows_bar())
    top = (8 if desk else 6 * lay.fs) if game else lay.cy + (12 if desk else 6 * lay.fs)
    burst = 0
    if t[3]:
        el = _ticks_diff(_ticks_ms(), ws._toast_at)
        burst = 255 if el >= BURST_MS else max(1, el * 255 // BURST_MS)
    _ui_draw.unlock(cv, ws.theme_colors, cv.w // 2, top,
                    HEAD_SECRET if t[3] else HEAD_BADGE, t[1], t[2], burst, desk)

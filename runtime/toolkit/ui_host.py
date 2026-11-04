# Map (grep -n a name to jump there):
#   AppHost                           a shipped app's host side of the verbs (ctx.ui)
#   AppHost.run                       begin a frame on a run snapshot's input (a Player's)
#   wash_page                         wash what a dialog begun now would
#   Hold                              the page under a modal, held while it is up; its rows frames
#   text_at                           the (line, column) under a point of a text view (`ui.text_view`)
#   text_aim                          a text view's move, aimed before its frame begins
#   text_cover                        a text view's rows another pass drew over
#   held_layer                        a console's hold layer, minted; release_held frees one
"""The host side of the public UI API's verbs: `AppHost`, which gathers one
app's input from the app protocol's handlers and begins its frame
(moy_ui_api_host_begin), and `Hold`, the page under a modal held while the
modal is up (docs/surface_model_v1.md §14.5), with the layers a console's
holds share and the moves a host aims at its text views.

It shares the frame's state with ui_draw -- the frame target, the paint
words, the key and edge latches -- which it imports at the FOOT of this
file: ui_draw imports this module at its own foot and re-exports the names
here, so either may load first, and every name imported is bound once
(a list or an array the frame writes in place, or a function), never
rebound. MicroPython-safe; a frame allocates nothing here.
"""

from array import array

import moy_ui as _C


# An app host's words (moy_ui_api.h's MOY_UI_HP_*): the plan, its canvas's
# RETAINED_FRAMES, SHIFT_COST, origin, whether the canvas and the theme are
# the last frame's, the tap, its own (MOY_UI_HH_*); then the answers: the
# dead directions and the moves its canvas makes, x, y, w, h, dy each.
_HP_LEN = 77
_HP_RETAINED = 8
_HP_COST = 9
_HP_OX = 10
_HP_OY = 11
_HP_SAME = 12
_HP_TAP = 13
_HP_HOST = 14
_HP_DEAD = 15
_HP_N = 16
_HP_MOVES = 17
# The pointer word a tap makes (MOY_UI_IN_PTR), by AppHost.tap: none, a
# press (live, held, its edge), its release (live), held.
_TAP_PTR = (0, 7, 1, 3)
_HH_HELD = 2
# moy_ui_api_host_begin's answer (MOY_UI_HB_*)
_HB_SETTLED = 1
_HB_MOVING = 2
_HB_EDGE = 4
_HB_DUP = 8
_HB_SHIFT = 16
_HB_HELD = 32
_HB_FENCE = 64


# AppHost.split_frame's gen for a frame whose surround changes on its own
# (moy_ui_api.h's MOY_UI_SPLIT_WHOLE).
_SPLIT_GEN_WHOLE = -0x80000000


class AppHost:
    """The host side of one shipped app's verbs (`ctx.ui`, for an app class
    with `UI = True`; Settings, the update screen and the wifi popover hold
    their own): its context, and the frame's input gathered from the app
    protocol's handlers in the run snapshot's shape. `keys` and `point`
    answer whether the app owes a frame, a page of it moving on its own
    included (`moving`; its `damage.all()` is the caller's);
    `begin` begins the frame. A tap is a press frame then a release frame
    where it went down; a host that passes `point` the pointer's `down` on
    every sample also drags (the press held while it moves, released where
    it comes up), which a page scrolls by. A frame that was not settled
    owes one more, so the rest state a press or a move leaves is painted,
    then nothing. `app` is the app's id (its buttons take the theme's
    `app_buttons[app]`)."""

    # split_frame's answers (moy_ui_api.h's MOY_UI_SF_*)
    SPLIT_WHOLE, SPLIT_ALONE, SPLIT_LAGS = 0, 1, 2

    def __init__(self, app=None):
        self.app = app
        self.ctx = ui_context()
        self.inp = array("i", [0] * UI_IN_LEN)
        self.tap = 0                    # 1 a press to begin, 3 held, 2 its release
        self.hold = False               # the pointer is down since the press
        self.owed = False
        self.key = 0                    # the key typed last frame (a press is its change)
        self.px = self.py = -1          # where the pointer was last seen, in the app's space
        self.mid = False                # the middle button is held
        # What the frames before left of the page in their buffers, the
        # plan and the lag are the context's (moy_ui_api_host_begin); its
        # words are these, with the canvas and theme of the frame begun last.
        self._hp = array("i", [0] * _HP_LEN)
        self._cv = self._th = None
        self._full = False              # the canvas has every word (`_canvas`)
        self.held = None                # a dialog's rows frame's licence (`held`)
        self._t3 = 0
        self._keys = False              # the frame begun last took the keys
        self.shifting = False           # the frame begun last draws its pages' floor alone
        self._began = -1                # the painted frame it began last
        self._console = None            # ...and the console that painted it
        self._dy = 0                    # the frame begun last moved its page by this
        self._sf = array("i", [0] * 8)  # split_frame's words

    @property
    def held(self):
        """A dialog's rows frame is the host's to allow: `held(plan)`
        answers whether every buffer holds the page under the dialog the
        plan names (its card and drop, plan[0:4], standing plan[7] frames)
        as the frames before left it (`Hold.rows`); None, never."""
        return self._held

    @held.setter
    def held(self, fn):
        self._held = fn
        h = self._hp
        h[_HP_HOST] = (h[_HP_HOST] & ~_HH_HELD) | (_HH_HELD if fn is not None else 0)

    def reset(self):
        """A fresh context: focus, kept state and input forgotten."""
        self.ctx = ui_context()
        self.clear()

    def clear(self):
        """Forget the input gathered for the next frame."""
        a = self.inp
        for n in range(UI_IN_LEN):
            a[n] = 0
        self.tap = 0
        self.hold = False
        self.owed = False
        self.key = 0

    def keys(self, i):
        """Take this frame's buttons and typed key from input state `i`."""
        a = self.inp
        b = 0
        for n in range(len(_HOST_BUTTONS)):
            if i.pressed(_HOST_BUTTONS[n]):
                b |= 1 << n
        k = getattr(i, "last_key", 0) or 0
        if not isinstance(k, int):
            k = ord(k[0]) if k else 0
        a[0] = a[1] = b
        typed = k != 0 and k != self.key
        self.key = k
        a[5] = k if typed else 0        # the engine sees each press once
        a[6] = getattr(i, "mods", 0) or 0
        a[7] = getattr(i, "wheel_x", 0)
        a[8] = getattr(i, "wheel_y", 0)
        rw = getattr(i, "rep_word", 0)
        kn = getattr(i, "key_repeats", 0)
        mid = bool(getattr(i, "pointer_buttons", 0) & _PB_MIDDLE)
        if (rw and not b and not typed and not kn and not mid and mid == self.mid
                and not a[7] and not a[8] and self.dead(rw)):
            rw = 0                      # a held key's steps where they move nothing
        a[_IN_REP] = rw - 0x100000000 if rw >> 31 else rw    # a small-int test: no bigint
        a[_IN_KEYREP] = (i.repeat_key & 0xFFFF) | kn << 16 if kn else 0
        moved = mid or mid != self.mid or a[7] or a[8] or rw or kn
        self.mid = mid
        return (bool(b) or typed or self.owed or self.tap != 0 or bool(moved)
                or _C.api_busy(self.ctx))

    def point(self, x, y, click, down=None):
        """A pointer sample in the app's coordinates; a click starts a tap.
        With `down` (the pointer held), the press stays held while it moves
        and is released where it comes up."""
        self.px = int(x)
        self.py = int(y)
        if click:
            self.inp[2] = int(x)
            self.inp[3] = int(y)
            self.tap = 1
            self.hold = bool(down)
            return True
        if down is None or not self.hold:
            return False
        self.inp[2] = int(x)
        self.inp[3] = int(y)
        if not down:
            self.hold = False
            self.tap = 2
        return True

    def focus(self, id):
        """Move focus to `id` ahead of the next frame, so that frame's keys
        act on it and its begin shows it (`ui.focus` inside a frame moves it
        after them, and owes the next frame)."""
        _C.api_focus_ahead(self.ctx, id)

    def box(self, id):
        """(x, y, w, h) where stop `id` drew in the last frame, or None."""
        return _C.api_box(self.ctx, id)

    def split_at(self, x, y):
        """Whether (x, y) lies on a divider the last frame's splits drew
        (`ui.split`), where a press would hold it: a host that routes its
        own presses gives such a press to the frame."""
        return _C.api_split_at(self.ctx, x, y)

    def split_frame(self, gen, known, top, lag, out):
        """Whether the frame about to begin draws the pane the keys reach
        alone (`moy_ui_api_split_frame`, docs/ui_api_2026-10.md §5a):
        SPLIT_ALONE with that pane's rect below `top` (the header the
        context's own pass draws) in `out`, an array('i') of four;
        SPLIT_LAGS with it where the target lags (`lag`, x0, y0, x1, y1)
        outside it, so the frame draws whole and changes that rect alone;
        SPLIT_WHOLE otherwise. `gen` is what the context draws outside the
        pane is a function of (None: it changes on its own this frame),
        `known` the WM's word that the target holds the page as the last
        frame left it. The pane is the one the last pass's splits named
        with their focus; the context's own input waiting, a popover or a
        divider held makes the frame whole."""
        w = self._sf
        w[0] = _SPLIT_GEN_WHOLE if gen is None else gen
        w[1] = 1 if known else 0
        w[2] = 1 if self.quiet() else 0
        w[3] = top
        w[4] = lag[0]
        w[5] = lag[1]
        w[6] = lag[2]
        w[7] = lag[3]
        return _C.api_split_frame(self.ctx, w, out)

    def split_toward(self, x, y, dx, dy, out):
        """Whether a pane of the last frame's splits lies (dx, dy) from the
        one holding (x, y), nearest along the way first: its rect into
        `out`, an array('i') of four."""
        return _C.api_split_toward(self.ctx, x, y, dx, dy, out)

    @staticmethod
    def within(out):
        """Whether the content drew this frame inside one rect of its canvas
        alone (`drew_within`: a modal's band over the page it left): the
        rect into `out` (x, y, w, h)."""
        return within(out)

    @staticmethod
    def window(v):
        """The window word (`window_frame`) for the frames begun next, a
        content's relay of the WM's word for the part of its window it has
        another host draw: answers the word it replaces, which the caller
        puts back."""
        return window_frame(v)

    def floor_box(self, out):
        """The bounding rect of the floor the last pass drew (its rows
        frame's), into `out`, an array('i') of four: whether it drew one."""
        return _C.api_floor_box(self.ctx, out)

    def change_box(self, out):
        """What the frame begun last changed of its canvas, where its plan
        made it a shift or rows frame: the rect its floor and its moves
        span into `out[0:4]` as x0, y0, x1, y1 (all 0 where they span
        nothing), and its region R, inside which alone it drew no more than
        that, into `out[4:8]` (`moy_ui_api_change_box`); `out` an
        array('i') of eight. False where the frame drew whole: what it
        changed is not known."""
        return self.shifting and _C.api_change_box(self.ctx, out)

    def resume(self, cv, th):
        """Point the verbs at this context again, on `cv` under theme `th`,
        beginning no frame: what its last frame left -- focus, stops, the
        bar it lent -- stands, and a verb that only draws (`ui.cursor`) draws
        as it did. A frame that paints a part of its page again (Paint's
        stroke frame) draws through it."""
        t = UI_TARGET
        t[0] = self.ctx
        t[1] = cv
        t[2] = _look(th)
        t[3] = self._t3
        if self._keys:
            _KEYS_HOST[0] = self

    def begin(self, cv, th, rect, desk=False, window=False, elsewhere=False):
        """Begin the frame on `cv` under theme `th`, its page `rect` (in a
        window's body when `window`): whether it is settled. `elsewhere`:
        the keys are a part's the app drives itself (a story's slide), so no
        stop holds focus this frame, as while the keys are on the shell's
        bar (`on_bar`): one focus mark a screen."""
        a = self.inp
        t = self.tap
        a[4] = _TAP_PTR[t]
        if (self.mid or a[7] or a[8]) and self.px >= 0:
            if not a[4]:                # the wheel and the middle button act where it rests
                a[2] = self.px
                a[3] = self.py
            a[4] |= 1 | (_P_MIDDLE if self.mid else 0)
        r = self.run(cv, th, rect, a, desk, window, elsewhere or _ON_BAR[0], True, t)
        if t:
            self.tap = (3 if self.hold else 2) if t == 1 else 3 if t == 3 else 0
        return r

    def run(self, cv, th, rect, a, desk=False, window=False, away=False, edge=False, tap=0):
        """Begin the frame on input `a` (the run snapshot's shape, its
        pointer's flags as the app's own host made them: a run's Player),
        as `begin` does: whether it is settled. `away`: no stop holds focus;
        `edge`: the key in `a` is a press edge already; `tap`: the pointer's
        tap state (`begin`'s), 0 none."""
        ctx = self.ctx
        u = UI_TARGET
        u[0] = ctx
        u[1] = cv
        u[2] = _look(th)
        if rect is None:
            rect = _WHOLE[0]
            if rect is None or rect[2] != cv.w or rect[3] != cv.h:
                rect = _WHOLE[0] = (0, 0, cv.w, cv.h)
        hp = self._hp
        same = 1
        if cv is not self._cv:
            self._cv = cv
            self._full = (hasattr(cv, "RETAINED_FRAMES") and hasattr(cv, "SHIFT_COST")
                          and hasattr(cv, "_ox") and hasattr(cv, "font_scale"))
            same = 0
        if th is self._th:
            same |= 2
        else:
            self._th = th
        hp[_HP_SAME] = same
        if self._full:
            hp[_HP_RETAINED] = cv.RETAINED_FRAMES
            c = cv.SHIFT_COST
            hp[_HP_COST] = -1 if c is None else c
            hp[_HP_OX] = cv._ox
            hp[_HP_OY] = cv._oy
            fs = cv.font_scale
            if fs < 1:
                fs = 1
        else:
            self._canvas(cv)
            fs = _fs(cv)
        hp[_HP_TAP] = tap
        flags = ((_DESK if desk else 0) | (_WINDOW if window else 0)
                 | (_ELSEWHERE if away else 0) | (_KEY_EDGE if edge else 0))
        self._t3 = u[3] = flags & (_DESK | _WINDOW)
        if self.app is not None:
            flags |= (_app_row(self.app) + 1) << _APP_SHIFT
        # The frame begun last noted, this one begun, and the shift frame it
        # may be planned (moy_ui_api_host_begin): the canvas makes the moves
        # its plan names, then the pass draws the floor alone. A dialog's
        # rows frame is the host's to allow (`held`).
        r = _C.api_host_begin(ctx, a, rect, flags, fs, _PF, hp)
        if r & _HB_DUP:
            print("ui: two stops share the id %r; the later is no stop" % _C.api_dup(ctx))
            r = _C.api_host_begin(ctx, a, rect, flags, fs, _PF, hp)
        self.shifting = False
        self._dy = 0
        if r & _HB_SHIFT and (not r & _HB_HELD or self._held(hp)):
            n = hp[_HP_N]
            k = _HP_MOVES
            while n:
                if not cv.shift_last(hp[k], hp[k + 1], hp[k + 2], hp[k + 3], 0, hp[k + 4]):
                    break
                n -= 1
                k += 5
            if not n:
                if r & _HB_FENCE:
                    cv.shift_fence()    # the floor draws over rows the moves wrote
                self._dy = _C.api_host_arm(ctx)
                self.shifting = True
                _CHANGE[0] = _SHIFT if self._dy else _ROWS
        if r & _HB_MOVING:
            _MOVING[0] = True
        if r & _HB_EDGE:
            _EDGE[0] = True
        self._began = _PAINT[0]
        self._console = _PAINT[2]
        self._keys = not away
        if not away:
            _KEYS_HOST[0] = self
        settled = r & _HB_SETTLED != 0
        self.owed = not settled
        return settled

    def _canvas(self, cv):
        """Canvas `cv`'s words, of a canvas that lacks some: RETAINED_FRAMES
        0 (none), SHIFT_COST unknown, its origin 0, 0."""
        hp = self._hp
        hp[_HP_RETAINED] = getattr(cv, "RETAINED_FRAMES", 0)
        c = getattr(cv, "SHIFT_COST", None)
        hp[_HP_COST] = -1 if c is None else c
        hp[_HP_OX] = getattr(cv, "_ox", 0)
        hp[_HP_OY] = getattr(cv, "_oy", 0)

    def dead(self, rw):
        """Whether a held key's (or the ball's) steps `rw` (the input
        table's `rep_word`) would move nothing: each is a direction the
        steps of the last painted frame that drew (the repeats of it after
        it seen through) took to no effect, and the page owes nothing and
        does not move."""
        d = self._hp[_HP_DEAD]
        return (d != 0 and (_rep_dirs(rw) & ~d) == 0 and self._began == _drew_last()
                and not self.owed and self.tap == 0 and not _C.api_busy(self.ctx))

    def whole(self):
        """The frame begun next draws whole, its page's pixels not moved out
        of the last frame's: its app knows every row of it changes (a text
        view's columns scrolled across), so a move would be copied for
        nothing."""
        _C.api_host_whole(self.ctx)

    def again(self):
        """The pass changed what it had drawn before the change (a page it
        opened, a place it picked): the next frame is owed, as one the
        engine owes (`keys` answers it, and the console paints it with no
        input: `again_take`), never the page marked whole."""
        self.owed = True
        _C.api_host_whole(self.ctx)
        _AGAIN[0] = True

    def tap_armed(self):
        """Whether the pointer is down on a stop its release may activate
        (no page's drag, no fling it caught): the release frame's pass may
        act on it."""
        return bool(_C.api_tap_armed(self.ctx))

    def owes(self):
        """Whether the pass begun last owes the frame after it (a popover
        laid out for another body, a focus it opened): that frame's layout
        is not the last one's."""
        return bool(_C.api_owes(self.ctx))

    def quiet(self):
        """Whether no input waits for the next frame: no button, typed key,
        repeat, wheel or press, and no frame owed."""
        a = self.inp
        return not (a[1] or a[5] or a[7] or a[8] or a[_IN_REP] or a[_IN_KEYREP]
                    or self.tap or self.owed)

    def wheel(self, wx, wy):
        """The wheel alone as this frame's input, at the last point: a page
        whose keys are another's (a text view's are its editor's) still
        takes the wheel. True when it moved something."""
        _WHEEL.wheel_x = wx
        _WHEEL.wheel_y = wy
        return self.keys(_WHEEL)

    def popped(self, box):
        """Whether this context's last pass drew a popover or tooltip; its
        box (x, y, w, h) into `box`."""
        return bool(_C.api_pop_box(self.ctx, box))

    def still(self):
        """Whether the page this context drew last is what it draws next:
        its input moved nothing, no tap waits, it owes no frame and no page
        of it moves on its own."""
        return not (self.owed or self.tap or _C.api_busy(self.ctx))

    @property
    def moving(self):
        """Whether a page of this context flings or glides: its next frame
        is due though no input comes."""
        return _C.api_moving(self.ctx)

    @property
    def moved(self):
        """Whether the frame begun last moved the focus or a scroll, which
        its pass drew: a frame that owes nothing but is not the one before
        it (a popover's card the WM owes every buffer)."""
        return _C.api_moved(self.ctx)


def wash_page(src=None, also=None):
    """Wash, on the frame's canvas, what a dialog begun now would: the
    surface, less the page's hint band at its foot; the rect it washed.
    The dialog then takes `{"wash": False}`. `src` and `also` as `scrim`'s."""
    t = UI_TARGET
    box = _C.api_wash_box(t[0])
    if src is None and also is None:
        _C.api_wash(t[0], t[1], t[2])
    else:
        _C.scrim(t[1], t[2], box, src, also)
    return box


# What the held page's dialog takes: the page under it is washed already.
NO_WASH = {"wash": False}


# What the page under a dialog draws this frame (`Hold.under`): put back
# washed, the dialog drawn over it held; the last frame's, taken from its
# buffer; drawn; or left, with the modal's card about it, where a window's
# target holds both as the frames before drew them (`left`).
HOLD_PUT, HOLD_LAST, HOLD_DRAW, HOLD_LEFT = 0, 1, 2, 3


class Hold:
    """The page under a modal, held while the modal is up
    (docs/surface_model_v1.md §14.5): a dialog's page, the system menu's
    home, the run's frame under the pause card. A modal takes the keys and
    the pointer, so the page under it does not change while it stays: the
    first frame draws the page and washes it (`wash`, or `keep` where the
    scrim stored to the held layer as it landed), the rest of the page's
    rect copied there; each frame after puts that copy back (`put`)
    instead of drawing the page and its wash, and draws the modal over it.
    `key` is what the page's pixels are a function of while the modal is
    up (the modal itself, the page's rect, the look); a key that differs,
    a page that was not still, anything animating, a canvas that retains
    nothing or one drawing into a viewport of its buffer draws it whole.

    A page nothing draws again -- the run's frozen frame under the pause
    card -- is kept as it was too (`snap`), put back for the frame the card
    washes and for the resumed game (`unsnap`).

    A dialog's first frame takes the page from the last frame instead of
    drawing it where that frame drew it alone and still, with no dialog up
    (`shown`), and the input that opened the dialog changed nothing else
    in it with nothing reported before it (`opened`): `last` says so, and
    the wash reads the page from the buffer that frame drew into.

    A frame whose input only typed into the dialog's field or moved its
    focus puts nothing back (a dialog's rows frame:
    docs/history/scroll_and_text_2026-10.md §5): the dialog's frame begins ahead
    of the page (`rows_frame`), and where its host allows it (`ok`, the
    host's `held`) the dialog draws its changed rows alone. That is where
    every buffer holds the page as the frames before left it (`rows`: each
    a frame the dialog drew over the hold alone on the glass, `note`), or
    where the frame is a window's whose target holds it as the last frame
    left it (`window_frame` 2 and up: the desk Editor's split frame of the
    dialog's pane, docs/ui_api_2026-10.md §5a), and only on a frame
    whose dialog gave the gen it gave the last."""

    def __init__(self):
        self.key = None
        self.snapped = False            # the page kept as it was (`snap`)
        self._shown = -1                # the frame that drew the page alone and still
        self._over = -1                 # the frame a dialog opened over it first draws
        self._src = None                # this frame's page: the last frame's buffer
        self._at = PaintRing()          # the frames that drew the dialog over the hold
        self._ask = [None, None, None, 0]   # this frame's console, canvas, key, dt (`ask`)
        self.begun = -1                 # the frame the dialog's host began ahead of the page
        self._gen = None
        self._same = False
        self._drew = None               # the key the page was last drawn under (`under`)
        self._drew_f = -1               # ... the painted frame that drew it
        self._run_f = -1                # ... the first of the painted frames since under it
        self._alone_f = -1              # ... the first since, each alone on the glass

    # -- the page held washed

    def put(self, ws, cv, rect, key, dt=0, pages=True):
        """Put the held page back into `rect`: whether it was held under
        `key` (else the frame draws the page). `pages` False: the page is
        no verbs page, so one moving on its own (the modal's body coasting)
        changes nothing of it (`Workstation._animating`'s word)."""
        held = ws.held_pages
        if (self.key is None or held[1] is not self or self.key != key
                or ws._animating(dt, pages)):
            return False
        x, y, w, h = rect
        cv.blit_strip_rect(held[0], 0, 0, x, y, w, h)
        return True

    def layer(self, ws, cv, still, dt=0):
        """The held layer, where this frame's page may be held: only where
        it is `still`, nothing animates and the canvas keeps its pixels and
        draws into its whole buffer (a pane's viewport is copied from as
        the buffer it lies in, so it is not). None otherwise, or with no
        room for it."""
        if not still or ws._animating(dt) or not _whole_retained(cv):
            return None
        return held_layer(ws, cv)

    def wash(self, ws, cv, rect, key, still, dt=0):
        """Wash the page in `rect` under the dialog begun on the frame's
        verbs (`wash_page`) and hold it under `key` where it may be held
        (`layer`): the wash stores to the held layer as it lands, and what
        it leaves of `rect` -- the page's hint band, which the dialog's
        keys drew this frame -- is copied there after it. Where `last`
        took the page from the last frame, the wash reads it from that
        frame's buffer."""
        src = self._src
        self._src = None
        lay = self.layer(ws, cv, still, dt)
        also = None if lay is None else getattr(lay, "_buf", None)
        box = wash_page(src, also)
        self._store(ws, cv, lay, key, (rect,) if also is None else _outside(rect, box))

    def keep(self, ws, cv, rect, key, still, dt=0, washed=()):
        """Hold `rect` of the frame drawn so far under `key`, where it may
        be held (`layer`); `washed`: rects a scrim already stored to the
        held layer (`scrim`'s `also`), which are not copied again."""
        parts = [rect]
        for w in washed:
            parts = [p for r in parts for p in _outside(r, w)]
        self._store(ws, cv, self.layer(ws, cv, still, dt), key, parts)

    def _store(self, ws, cv, lay, key, parts):
        self.key = None
        if lay is None:
            return
        for r in parts:
            lay.blit_strip_rect(cv, 0, 0, r[0], r[1], r[2], r[3])
        ws.held_pages[1] = self
        self.key = key

    def drop(self):
        """The modal closed: the page is no longer held, nor kept, and the
        next modal asks for room again (`held_layer`)."""
        _NO_ROOM[0] = None
        self.key = None
        self.snapped = False
        self._at.clear()
        self._drew = None

    # -- the page kept as it was

    def snap(self, ws, cv):
        """Keep the whole frame of `cv` as it is, before a modal draws over
        it, where there is room for it (`held_layer`)."""
        lay = held_layer(ws, cv, 2)
        self.snapped = lay is not None
        if lay is not None:
            lay.blit_strip(cv, 0, 0)
            ws.held_pages[3] = self

    def unsnap(self, ws, cv):
        """Put the frame `snap` kept back into `cv`."""
        if self.snapped:
            lay = ws.held_pages[2]
            cv.blit_strip_rect(lay, 0, 0, 0, 0, lay.w, lay.h)

    # -- a dialog's page: its first frame, its rows frames

    def under(self, ws, key, rect, dt=0, pages=True, left=False):
        """What the page in `rect` draws this frame under the dialog up,
        `key` what it is a function of (None with no dialog up): HOLD_PUT,
        HOLD_LAST or HOLD_DRAW; with `left`, HOLD_LEFT too, where the
        modal then draws inside its card's band alone (`_left`). The
        dialog's rows frame is asked first (`rows_frame`). `pages`:
        `put`'s."""
        if key is None:
            if self.key is not None:
                self.drop()
            return HOLD_DRAW
        cv = ws.sys_canvas
        f = _PAINT[0]
        was = left
        left = left and self._left(ws, cv, key, rect, f, dt, pages)
        if self._drew != key:
            self._run_f = self._alone_f = f
        elif self._drew_f != f - 1 and self._drew_f != f:
            self._alone_f = f
        if not _PAINT[1] and self._drew_f != f:
            self._alone_f = f + 1
        self._drew = key
        self._drew_f = f
        if was and self.copies(cv) and f + 1 - self._alone_f < cv.RETAINED_FRAMES:
            # a buffer the next frame paints does not hold the page and the
            # card under `key` yet: that frame comes now, from this one
            # (`_left`), not with the input that next reaches the modal
            ws.request_frame()
        if left:
            if not _PF[_PF_WIN]:
                rows_drawn()
            return HOLD_LEFT
        if self.put(ws, cv, rect, key, dt, pages):
            return HOLD_PUT
        return HOLD_LAST if self.last(ws, cv, rect, dt) else HOLD_DRAW

    def copies(self, cv):
        """Whether the frame painted, alone on the glass, leaves the page
        and the modal's card for the next frame to copy out of its buffer
        (`_left`, a canvas of more than one buffer), where that frame draws
        under the same key: the page then needs no hold (`keep`). One
        buffer keeps the hold, which puts the page back on a frame that
        follows one not alone."""
        return (bool(_PAINT[1]) and not _PF[_PF_WIN] and _whole_retained(cv)
                and cv.RETAINED_FRAMES > 1 and getattr(cv, "copy_last", None) is not None)

    def _left(self, ws, cv, key, rect, f, dt, pages):
        """Whether this frame finds the page under `key`, and the modal over
        it, in the buffer it paints already. A window's frame
        (`window_frame`): 2, its target holds the window as its last draw
        left it, and that draw drew them under `key`; 3, it holds it as the
        frames RETAINED_FRAMES back left it but for their rows, and every
        draw of them since before those frames was under `key` (a frame
        between that repeats the last changes none of it). Else a frame alone on a canvas that
        keeps its pixels (`paint_frame`'s `alone`), each of the RETAINED_FRAMES
        painted frames before it having drawn them under `key`, alone too;
        or fewer of them, the frame before at least, where the canvas
        copies `rect` (the page, the card inside it) out of the buffer that
        frame painted (`copy_last`). `key` names where the modal's card
        lies, so what the card draws outside its band (its corners and
        drop, blended over the page) is there as well."""
        w = _PF[_PF_WIN]
        if self._drew != key or w == 1 or ws._animating(dt, pages):
            return False
        if w == 2:
            return True
        n = getattr(cv, "RETAINED_FRAMES", 0)
        if n < 1:
            return False
        if w:
            return f - self._run_f >= n
        if not (_PAINT[1] and self._drew_f == f - 1 and _whole_retained(cv)):
            return False
        k = f - self._alone_f
        if k >= n:
            return True
        cl = getattr(cv, "copy_last", None)
        return k >= 1 and cl is not None and bool(cl(rect[0], rect[1], rect[2], rect[3]))

    def note(self):
        """The frame drew the dialog over the page held (put back, washed
        into the hold, or left as it was by a rows frame): counted where it
        was alone on the glass."""
        if self.key is None or not _PAINT[1]:
            self._at.clear()
        else:
            self._at.note(_PAINT[0])

    def ask(self, ws, cv, key, dt=0):
        """This frame's terms for `rows`, before the dialog's frame begins."""
        a = self._ask
        a[0] = ws
        a[1] = cv
        a[2] = key
        a[3] = dt

    def rows(self, plan):
        """Whether the dialog's rows frame `plan` (`AppHost.held`) may leave
        the page as it is: it is held under the key `ask` gave, nothing
        animates, and each of the RETAINED_FRAMES painted frames before this
        one drew the dialog the plan names (standing plan[7] frames) over
        the hold, so every buffer holds the page outside the dialog's card."""
        ws, cv, key, dt = self._ask
        self._ask[0] = self._ask[1] = None
        n = getattr(cv, "RETAINED_FRAMES", 0)
        if (ws is None or self.key is None or ws.held_pages[1] is not self or self.key != key
                or n < 1 or n > self._at.size or plan[7] < n or ws._animating(dt)):
            return False
        f = _PAINT[0]
        for k in range(n):
            if self._at.frame(k) != f - 1 - k:
                return False
        return True

    def shown(self, ws, cv, still, dt=0):
        """This frame drew the page with no dialog up: noted where it was
        `still` and is all the frame draws on a canvas that keeps it (no
        banner, overlay, cursor or pill over it, nothing animating)."""
        alone = getattr(ws.wm, "content_alone", None)
        ok = (still and not getattr(cv, "covered", False) and alone is not None
              and alone() and not ws._animating(dt) and _whole_retained(cv))
        self._shown = ws.frame_state.frames_drawn if ok else -1

    def opened(self, ws):
        """A dialog opened over the page from an input that changed nothing
        else in it: its first frame may take the page from the last frame
        (`last`), where that frame drew it (`shown`) and no change was
        reported since (the console's `_dirty_in`)."""
        f = ws.frame_state.frames_drawn
        self._over = f if self._shown == f - 1 and not ws._dirty_in else -1

    def last(self, ws, cv, rect, dt=0):
        """Whether this frame, the first of the dialog `opened` over the
        page, takes the page in `rect` from the last frame instead of
        drawing it (else the frame draws the page). One retained buffer
        holds it already; more give the buffer that frame drew into, which
        the wash reads (`last_buffer`), or copy it out (`copy_last`)."""
        over = self._over
        self._over = -1
        self._src = None
        if over != ws.frame_state.frames_drawn or ws._animating(dt) or not _whole_retained(cv):
            return False
        if cv.RETAINED_FRAMES == 1:
            return True
        lb = getattr(cv, "last_buffer", None)
        src = None if lb is None else lb()
        if src is not None:
            self._src = src
            return True
        cl = getattr(cv, "copy_last", None)
        return cl is not None and bool(cl(rect[0], rect[1], rect[2], rect[3]))

    def rows_frame(self, ws, key, gen, host, rect, desk, dt=0):
        """Begin the dialog's frame on its `host` ahead of the page, once a
        painted frame, `key` what the page is a function of and `gen` the
        dialog's, under the window word its caller set (`window_frame`).
        Whether it is the dialog's rows frame,
        which draws the dialog's changed rows alone and leaves the page as
        it is; else the frame draws the page and the dialog as ever, the
        dialog's draw resuming the frame begun here (`dialog`)."""
        f = _PAINT[0]
        if self.begun == f:
            return False
        cv = ws.sys_canvas
        self.ask(ws, cv, key, dt)
        self._same = gen == self._gen
        self._gen = gen
        host.begin(cv, ws.theme_colors, rect, desk)
        self.begun = f
        return host.shifting

    def dialog(self, host, cv, th, rect, desk):
        """Begin the dialog's draw on its `host`, or resume the frame
        `rows_frame` began ahead of the page this frame."""
        if self.begun == _PAINT[0]:
            self.begun = -1
            host.resume(cv, th)
        else:
            host.begin(cv, th, rect, desk)

    def ok(self, plan):
        """The dialog's host's `held`: whether its rows frame `plan` may
        leave the page as it is."""
        if not self._same:
            return False
        if _PF[_PF_WIN] >= 2:
            return plan[7] >= 1
        return self.rows(plan)


# -- a host's text views (`ui.text_view`): moves aimed before its frame ---------

def text_at(host, id, x, y):
    """The (line, column) under (x, y) in text view `id` of AppHost
    `host`'s context as its last pass drew it, or None off its rows."""
    return _C.api_text_at(host.ctx, id, x, y)


class _Wheel:
    """A frame's input carrying the wheel alone (`AppHost.wheel`)."""

    last_key = mods = rep_word = key_repeats = repeat_key = pointer_buttons = 0

    def __init__(self):
        self.wheel_x = self.wheel_y = 0

    def pressed(self, _b):
        return False


_WHEEL = _Wheel()


def text_aim(host, id, line, lines):
    """Before AppHost `host` begins its frame: text view `id` shows line
    `line` first (-1: its foot, where it stood at its foot and its `lines`
    grew), so the move is the frame's shift; its pass, given the same `top`,
    moves nothing. Whether it moved."""
    return _C.api_text_aim(host.ctx, id, line, lines)


def text_reveal(host, id, lines, line, col=-1):
    """Before AppHost `host` begins its frame: text view `id`, showing
    `lines`, moves only as far as it needs to show line `line`'s row that
    holds column `col` (a caret's; -1: shows that line first), its wrapped
    rows counted as its last pass laid them, so the move is the frame's
    shift. Whether it moved."""
    return _C.api_text_reveal(host.ctx, id, lines, line, col)


def text_cover(host, id, y0, y1):
    """Rows y0..y1 of text view `id` of AppHost `host`'s context were drawn
    over after its pass this frame (an overlay of another pass): its next
    frame draws them again."""
    _C.api_text_cover(host.ctx, id, y0, y1)


def _outside(r, hole):
    """Rect `r` less rect `hole`: up to four rects, the bands above, below,
    left and right of what they share."""
    x0, y0, x1, y1 = r[0], r[1], r[0] + r[2], r[1] + r[3]
    hx0, hy0 = max(x0, hole[0]), max(y0, hole[1])
    hx1, hy1 = min(x1, hole[0] + hole[2]), min(y1, hole[1] + hole[3])
    if hx1 <= hx0 or hy1 <= hy0:
        return [r]
    out = []
    if hy0 > y0:
        out.append((x0, y0, r[2], hy0 - y0))
    if hy1 < y1:
        out.append((x0, hy1, r[2], y1 - hy1))
    if hx0 > x0:
        out.append((x0, hy0, hx0 - x0, hy1 - hy0))
    if hx1 < x1:
        out.append((hx1, hy0, x1 - hx1, hy1 - hy0))
    return out


def _whole_retained(cv):
    """Whether canvas `cv` keeps its pixels from frame to frame and draws
    into its whole buffer (a pane's viewport is not its buffer)."""
    if getattr(cv, "RETAINED_FRAMES", 0) < 1:
        return False
    vp = getattr(cv, "viewport", None)
    return vp is None or vp() == (0, 0, getattr(cv, "_stride", cv.w),
                                  getattr(cv, "_bh", cv.h))


# A console's holds share two layers of its canvas's size, one modal up at
# a time: `ws.held_pages` = [the layer the page is held in washed, the Hold
# whose page it holds, the layer a frame is kept in as it was (`Hold.snap`),
# the Hold that kept it]. Kept across modals (minting one is a third of a
# dialog's first frame on the T-Deck: the pause card mints both on an idle
# frame); the fit check's release frees them.

# The canvas size a hold layer last found no room at: not asked for again
# until a modal closes or a hold layer is given back, because each ask the
# glass cannot serve collects the whole heap first, and `Hold.layer` asks on
# every still frame of a modal.
_NO_ROOM = [None]


def held_layer(ws, cv, i=0):
    """Console `ws`'s hold layer `i` -- 0 the page held washed, 2 the frame
    kept as it was (`Hold.snap`) -- of canvas `cv`'s size: minted where
    there is none of it (an idle frame's work where a modal will want it,
    so the modal's first frame does not mint it); None with no room, or
    while the frame kept in another size is still up."""
    held = ws.held_pages
    lay = held[i]
    if lay is not None and lay.w == cv.w and lay.h == cv.h:
        return lay
    if lay is not None and i and held[3] is not None and held[3].snapped:
        return None
    if _NO_ROOM[0] == (cv.w, cv.h):
        return None
    _release(lay)
    held[i] = held[i + 1] = None
    try:
        lay = held[i] = cv.new_layer(cv.w, cv.h)
    except Exception:  # noqa: BLE001
        _NO_ROOM[0] = (cv.w, cv.h)
        return None
    return lay


def release_held(ws):
    """Free one of console `ws`'s hold layers (the fit check's release):
    the washed page's, then the kept frame's while no modal is up over it.
    True when there was one; the next modal mints it again."""
    held = ws.held_pages
    for i in (0, 2):
        if held[i] is not None and not (i and held[3] is not None and held[3].snapped):
            _release(held[i])
            held[i] = held[i + 1] = None
            _NO_ROOM[0] = None
            return True
    return False


def _release(lay):
    rel = getattr(lay, "release", None)
    if rel is not None:
        rel()


# ui_draw's frame state and helpers, imported last (see above).
try:
    from ui_draw import (
        UI_IN_LEN, UI_TARGET, _AGAIN, _APP_SHIFT, _CHANGE, _DESK, _EDGE, _ELSEWHERE,
        _HOST_BUTTONS, _IN_KEYREP, _IN_REP, _KEYS_HOST, _KEY_EDGE, _MOVING, _ON_BAR,
        _PAINT, _PB_MIDDLE, _PF, _PF_WIN, _P_MIDDLE, _ROWS, _SHIFT, _WHOLE, _WINDOW,
        _app_row, _drew_last, _fs, _look, _rep_dirs, rows_drawn, ui_context,
        window_frame, within)
    from ui_scroll import PaintRing
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.ui_draw import (
        UI_IN_LEN, UI_TARGET, _AGAIN, _APP_SHIFT, _CHANGE, _DESK, _EDGE, _ELSEWHERE,
        _HOST_BUTTONS, _IN_KEYREP, _IN_REP, _KEYS_HOST, _KEY_EDGE, _MOVING, _ON_BAR,
        _PAINT, _PB_MIDDLE, _PF, _PF_WIN, _P_MIDDLE, _ROWS, _SHIFT, _WHOLE, _WINDOW,
        _app_row, _drew_last, _fs, _look, _rep_dirs, rows_drawn, ui_context,
        window_frame, within)
    from runtime.ui_scroll import PaintRing

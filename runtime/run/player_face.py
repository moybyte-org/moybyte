"""The app-cart face of a run: what a cart with the shell's bar (a tool, an
app or a script; `Player.is_app`) shows the host besides its frames -- the
room its `ui` frame lies in and the word that says how it draws, the field
focus (`typing`, `ball_steps`), the host's back and cancel into the app, and
a Python app's pass of `ui` verbs: its input, its drawn-on-change debt and
what it changed of the canvas.

`AppFace` is one object the Player holds (`player.face`). It keeps the
state of a Python app's pass; the run's own state stays on the Player, where
a frame reads it (`ui`, `focus_editor`, `_tool`). A frame calls no
method through it: the Player binds `begin` and `close` once
(`Player.__init__`), and the cart's `cls` and `background` are bound into
the namespace at the start.

Canonical home is runtime/; build.sh stages a copy into the firmware modules/
tree, as it does player.py.
"""

from array import array as _array

from cart_api import CART_BUTTONS as _NET_BUTTONS
from moy_input import pointer_state as _pointer_state
import layout_base as _layout_base
import ui_draw as _ui_draw
from ui_draw import (ui_owes as _ui_owes, ui_moving as _ui_moving, UI_IN_LEN as _UI_IN_LEN, _P_MIDDLE, _PB_MIDDLE,
                     _IN_REP as _UI_IN_REP, _IN_KEYREP as _UI_IN_KEYREP)

# The words of `ui_face` (native/moy_ui/moy_ui_run.h's MOYCORE_UI_FACE_*).
_FACE_WINDOW, _FACE_DESK, _FACE_LOOK, _FACE_TAP, _FACE_FS_SHIFT = 1, 2, 4, 8, 8

_UI_MASKS = [0, 0]                      # reused: the frame's button masks
_P_LIVE = 1                             # moy_ui_api.h's MOY_UI_P_LIVE: a pointer over the page
_P_DOWN = 2 | 4                         # moy_input.h's MOY_INPUT_P_HELD | MOY_INPUT_P_CLICK
_UI_POINTER = [0, 0, 0, 0]              # reused: moy_input.pointer_state's answer


def steps(inp, a, i):
    """The frame's steps beyond its presses for an app's page into a[i] and
    a[i + 1] (moy_ui_api.h's MOY_UI_IN_REP and MOY_UI_IN_KEYREP): the input
    table's `rep_word` (a held direction's repeats, the trackball's steps) and
    a held key's typing repeats, as an app's host gathers them
    (ui_host.AppHost.keys)."""
    rw = getattr(inp, "rep_word", 0)
    kn = getattr(inp, "key_repeats", 0)
    a[i] = rw - 0x100000000 if rw >> 31 else rw
    a[i + 1] = (inp.repeat_key & 0xFFFF) | kn << 16 if kn else 0


class AppFace:
    """The app-cart face of the Player `pl` (above)."""

    def __init__(self, pl):
        self.pl = pl
        self.on_change = False       # an app on the verbs: drawn on change (animates())
        self._masks = None            # ws.input.button_masks, bound for an app's run
        self._owe = 0                 # drawn frames it still owes after the last change
        # An app on the verbs draws through its app host (`begin`):
        # whether its pass is drawing now, whether a clear broke its floor,
        # and what drew over its page after the pass (the FPS chip).
        self._uh = None
        self._pass = False
        self._broke = False
        self._over = _array("i", [0] * 4)
        # What the painted frame `change_f` changed of the run's canvas, as
        # its pass's plan said (`AppHost.change_box`): x0, y0, x1, y1, then
        # the region R it drew no more than that inside (what lies outside R
        # it drew whole), and 1 where known -- which the window's composite
        # reads.
        self.change = _array("i", [0] * 9)
        self.change_f = -1
        # What the last drawn pass drew from (`still`): the pointer's x, y
        # and flags, whether the run was deaf, the look's generation; and
        # whether a pass has drawn since the run's frame of verbs began.
        self._saw = _array("i", [0] * 5)
        self._drew = False
        self._cls = None
        self._bg = None
        self._bg_on = False

    def reset(self):
        """The run's frame of `ui` verbs ends: a start, an exit."""
        self.pl.ui = None
        self._uh = None
        self.on_change = False
        self._pass = False
        self._drew = False

    def bind(self, ns, cart):
        """Give a Python app whose namespace has the `ui` verbs its frame of
        them (`Player.ui`), and draw it on change unless it animates."""
        pl = self.pl
        self._uh = _ui_draw.AppHost()
        pl.ui = [self._uh.ctx, _array("i", [0] * _UI_IN_LEN), pl.run.canvas]
        # Bound once: a getattr that finds a method allocates its
        # bound method every frame.
        self._masks = getattr(pl.ws.input, "button_masks", None)
        # An app on the verbs is drawn on change, as a shipped app is
        # (docs/ui_api_2026-10.md, "Drawn on change"), unless its
        # manifest says it animates. One that plays sound keeps every
        # frame too: the frame is where the host and the browser pull
        # its mix (`audio_out.tick`), and a still frame pulls none.
        self.on_change = (pl._app_id is not None and not pl.script
                          and cart.get("animates") is not True
                          and "audio" not in (cart.get("permissions") or ()))
        self._owe = 2
        self._drew = False
        self._bg_on = False
        if self.on_change and "cls" in ns:
            # a clear lands under any floor: the frame draws whole;
            # a declared background clears every frame
            self._cls = ns["cls"]
            ns["cls"] = self.cls
            self._bg = ns.get("background")
            if self._bg is not None:
                ns["background"] = self.background

    def ui_room(self):
        """The rect the run's `ui` frame lies in: an app's `content_rect()`,
        its surface less the host's bars, so `ui.page` lies below them on
        every tier (each tier's begin reads it here: this face's
        `begin`, the snapshot of a Lua or compiled run, a native or WASI
        app's frame words); None, the whole canvas, for a game."""
        pl = self.pl
        return pl.ws.app_content_rect() if pl._app_id is not None else None

    def ui_face(self):
        """How the run's `ui` frame draws, as one word (native/moy_ui/
        moy_ui_run.h's MOYCORE_UI_FACE_*) a Lua, compiled, WASI or native
        run's snapshot carries, so its verbs draw what this face's
        `begin` draws for a Python app: whether the surface is a window's
        body, whether the frame lays out on the desk's rows, whether it is a
        responsive app's (the shell's look, its faces and colours), whether
        the board's tap floor stands (`ui_draw.tap_floor`), and the font
        scale of the canvas it draws on. A game's is its window bit
        alone: the cart's look at scale 1."""
        pl = self.pl
        ws = pl.ws
        f = _FACE_WINDOW if ws.windowed_chrome else 0
        if pl._app_id is None:
            return f
        run = pl.run
        if run.app_full_canvas and _layout_base.metric_class() == _layout_base.DESK:
            f |= _FACE_DESK
        if pl._responsive:
            f |= _FACE_LOOK
        if _ui_draw.tap_floor():
            f |= _FACE_TAP
        fs = getattr(run.canvas, "font_scale", 1)
        return f | (fs if fs > 1 else 1) << _FACE_FS_SHIFT

    def bar_ctx(self):
        """The ui context whose `ui.bar` lends the host's bar: the Player's
        for a Python app, the run's own (`ui_draw.RUN_UI`) for any other
        (a Lua or compiled app's; one linking a toolkit of its own lends
        none there); None for a game or before a frame."""
        pl = self.pl
        if pl.lua is not None:
            return _ui_draw.RUN_UI if pl._tool else None
        ui = pl.ui
        return ui[0] if ui else None

    def ball_steps(self):
        """Whether the trackball steps this run's page, as it steps every
        surface that takes the keys (`InputPolicy.ball_steps`): an app's,
        unless a caret holds the keyboard (its editor handle takes the roll
        as arrows, `InputRouter.nav`); a game's trackball is its pointer."""
        pl = self.pl
        return pl._tool and pl.focus_editor is None

    def typing(self):
        """Whether the run takes typed bytes, which the input policy puts the
        keyboard in text mode for while it holds the keys: the cart asked
        (`textmode()`, a script's console), an editor handle holds the
        keyboard, or an app's page has a field in focus -- one rule for
        every runtime: the Python verbs' context, or the run face a Lua or
        compiled app draws through. On a T-Deck W A S D then type and press
        no direction (native/moy_input/moy_kbd.c)."""
        pl = self.pl
        if pl.run.textmode or pl.focus_editor is not None:
            return True
        if not pl._tool or pl.cart_error is not None:
            return False
        lua = pl.lua
        if lua is not None:
            return lua.ui_typing() if getattr(lua, "own_ui", False) else _ui_draw.run_typing()
        ui = pl.ui
        return ui is not None and _ui_draw.app_typing(ui[0])

    def begin(self, ws, render=True):
        """Begin a Python app's frame of `ui` verbs (runtime/toolkit/ui.py) over the
        input its btn() and touch() read, in the run snapshot's shape, before
        its update, so `ui.keys_taken()` answers there.

        The frame begins through the run's app host (`AppHost.run`), the
        path every shell app's frames take: where the run has an editor
        handle open and drawn, the engine may plan it a shift frame -- its
        text view's fling, glide or a keyed step moved out of the frame
        before, or its changed rows drawn alone
        (docs/history/scroll_and_text_2026-10.md §5). Any other run draws whole."""
        pl = self.pl
        ctx, a, cv = pl.ui
        host = self._uh
        inp = ws.input
        bm = self._masks
        if bm is not None:
            m = bm(_NET_BUTTONS, None, _UI_MASKS)
        else:
            m = _UI_MASKS
            m[0] = m[1] = 0
            for i in range(len(_NET_BUTTONS)):
                if inp.held(_NET_BUTTONS[i]):
                    m[0] |= 1 << i
                if inp.pressed(_NET_BUTTONS[i]):
                    m[1] |= 1 << i
        st = _pointer_state(inp, _UI_POINTER)
        a[0], a[1] = m[0], m[1]
        a[2], a[3], a[4] = st[0], st[1], st[2]
        a[5] = int(getattr(inp, "last_key", 0) or 0)
        a[6] = int(getattr(inp, "mods", 0) or 0)
        a[7] = getattr(inp, "wheel_x", 0)
        a[8] = getattr(inp, "wheel_y", 0)
        steps(inp, a, _UI_IN_REP)
        if getattr(inp, "pointer_buttons", 0) & _PB_MIDDLE:
            a[4] |= _P_MIDDLE
        fed = pl.focus_editor
        deaf = pl.deaf or pl.detached
        if render:
            saw = self._saw
            saw[0] = st[0]
            saw[1] = st[1]
            saw[2] = st[2]
            saw[3] = 1 if deaf else 0
            saw[4] = ws.look.look_gen
        if deaf:
            a[0] = a[1] = a[5] = a[6] = 0   # the keys are another window's
            a[_UI_IN_REP] = a[_UI_IN_KEYREP] = 0
        elif fed is not None:
            a[0] = a[1] = a[5] = 0      # the keys are the focused editor handle's
            a[_UI_IN_REP] = a[_UI_IN_KEYREP] = 0
        if fed is not None:
            if a[4] and fed.claims(a[2], a[3]):
                a[4] = _P_LIVE          # its own gesture (a mouse's, a selection's): a hover
        # an app on the whole surface (a desk's window, or its glass) lays out
        # on the desk's rows there; the fixed raster is the handheld's
        desk = pl.run.app_full_canvas and _layout_base.metric_class() == _layout_base.DESK
        ed = self._note() if render and not self._bg_on else None
        if ed is None or (self._over[2] and ed.meets(self._over)):
            host.whole()                # nothing vouches for the page's pixels
        else:
            ed.aim(host)                # a caret's step lands as this frame's shift
        self._over[2] = 0
        _ui_draw.say_lost(ctx)
        # On a desk the run's canvas is its window's own, which only the run
        # draws (`holds`): it holds the page as the run's last frame left it.
        own = self.holds(cv)
        if own:
            _ui_draw.window_frame(2)
        try:
            settled = host.run(cv, ws.theme_colors, self.ui_room(), a, desk, ws.windowed_chrome,
                               fed is not None, deaf=deaf)
        finally:
            if own:
                _ui_draw.window_frame(0)
        self._pass = render
        self._broke = False
        if self.on_change:
            # A frame that moved anything owes the next, which paints the
            # state it left (a verb acts after its part drew, a tap on the
            # note moves the caret after the note drew); a frame with nothing
            # going on pays off one.
            if (not settled or self._input_active(inp) or _ui_owes(ctx)
                    or _ui_moving(ctx)):
                self._owe = 2
            elif self._owe:
                self._owe -= 1

    def _note(self):
        """The one editor handle the run's pass drew last, else None."""
        pl = self.pl
        ed = None
        for e in pl.editors:         # a closed one draws nothing
            if e.live:
                if ed is not None:
                    return None
                ed = e
        return ed if ed is not None and ed.drawn_in(self._uh) else None

    def close(self):
        """The app's pass is done: a frame whose pass a clear broke said so
        as it drew (`cls`); the frame's change is the engine's, and
        what it changed of the canvas (`change`) its plan's."""
        self._pass = False
        self._drew = True
        ch = self.change
        self.change_f = _ui_draw.painted()
        if self._broke:
            _ui_draw._CHANGE[0] = None  # it drew whole from the clear on
            ch[8] = 0
        else:
            ch[8] = 1 if self._uh.change_box(ch) else 0

    def still(self):
        """Whether this painted frame may leave the app's page as its last
        pass drew it, without a pass: a Python app drawn on change whose
        canvas only the run draws (`holds`), with no frame of its own to draw
        (`animates`), nothing marked dirty, no key, step or modifier it
        reads, the pointer up and where the last pass saw it, its focus,
        look and layout as they were, and nothing drawn over its page since
        (the FPS chip, `overdrew`). The frames a desk paints for another
        part of it -- a live wallpaper, a game's window -- then cost the app
        nothing (`unchanged`)."""
        pl = self.pl
        ui = pl.ui
        if (not self.on_change or not self._drew or ui is None or pl.lua is not None
                or pl.netplay is not None):
            return False
        ws = pl.ws
        if (ws.frame_state.dirty or self._over[2] or not self.holds(ui[2])
                or self.animates()):
            return False
        deaf = pl.deaf or pl.detached
        i = ws.input
        if not deaf and (getattr(i, "typed", 0) or getattr(i, "key_repeats", 0)
                         or getattr(i, "rep_word", 0) or getattr(i, "mods", 0)):
            return False
        st = _pointer_state(i, _UI_POINTER)
        saw = self._saw
        if (st[2] & _P_DOWN or st[0] != saw[0] or st[1] != saw[1] or st[2] != saw[2]
                or saw[3] != (1 if deaf else 0) or saw[4] != ws.look.look_gen):
            return False
        return not (pl._app_layout is not None and pl._layout_moved(False))

    def unchanged(self):
        """This painted frame changed nothing of the run's canvas (`still`):
        an empty change inside a region of the whole canvas, which the
        window's composite reads as nothing to copy but what the buffer it
        paints lags."""
        ch = self.change
        cv = self.pl.ui[2]
        ch[0] = ch[1] = ch[2] = ch[3] = ch[4] = ch[5] = 0
        ch[6] = cv.w
        ch[7] = cv.h
        ch[8] = 1
        self.change_f = _ui_draw.painted()

    def ui_host(self, cv):
        """The run's app host whose pass draws on `cv` now, else None: an
        editor handle draws its text view in that pass."""
        pl = self.pl
        ui = pl.ui
        if not self._pass or ui is None or cv is not ui[2]:
            return None
        return self._uh

    def overdrew(self, cv, x, y, w, h):
        """Rect x, y, w, h of `cv` was drawn over the run's page after its
        pass (the FPS chip): the next frame moves none of an editor
        handle's rows where it lay."""
        pl = self.pl
        ui = pl.ui
        if ui is None or cv is not ui[2] or w <= 0 or h <= 0:
            return
        o = self._over
        o[0] = x
        o[1] = y
        o[2] = w
        o[3] = h
        ch = self.change
        if ch[8] and self.change_f == _ui_draw.painted():
            if ch[2] <= ch[0] or ch[3] <= ch[1]:
                ch[0] = x
                ch[1] = y
                ch[2] = x + w
                ch[3] = y + h
            else:
                ch[0] = x if x < ch[0] else ch[0]
                ch[1] = y if y < ch[1] else ch[1]
                ch[2] = x + w if x + w > ch[2] else ch[2]
                ch[3] = y + h if y + h > ch[3] else ch[3]

    def holds(self, cv):
        """Whether `cv`, the run's canvas, is one buffer only the run
        draws and the window's composite reads (not the glass): it holds
        what the run drew last, whatever frames the console painted since."""
        pl = self.pl
        return cv is pl.run.canvas and cv is not pl.ws.sys_canvas

    def cls(self, c=0):
        """The cart's `cls`: a clear under a floor makes the frame whole
        from there."""
        if self._pass and self._uh.shifting:
            _ui_draw._C.api_shift_break(self._uh.ctx)
            self._broke = True
        self._cls(c)

    def background(self, *a):
        """The cart's `background`: a declared backdrop repaints the canvas
        before every pass, so the run draws whole."""
        self._bg_on = True
        return self._bg(*a)

    def has_page(self):
        """Whether the running app has a view of its own open, which the
        host's back steps out of before it leaves the app: a field's edit in
        progress, a popover its last frame drew, or a page it lent `ui.bar`'s
        way back for, on any tier (docs/app_api_v1.md section 4). False for a
        game."""
        return self._app_back(False)

    def back(self):
        """The host's back -- the bar's chevron, a window's, Esc, B and the
        T-Deck's K -- into the running app: True where it cancels a field's
        edit in progress (`cancel`) or steps out of a view of its own
        (`has_page`), which the next frame shows; False where it has none,
        and the host leaves it (`InputPolicy.back`)."""
        pl = self.pl
        if not self._app_back(True):
            return False
        self._owe = max(self._owe, 1)
        pl.ws.request_frame()
        return True

    def cancel(self):
        """The host's cancel -- the T-Deck's Mic -- into the running app:
        True where the field its page focuses has an edit in progress, which
        the next frame gives back (its `ui.field` answers the text the edit
        began from) as focus steps up off it; False where none is, and Mic
        keeps its own meaning. Esc, B and the bar's chevron reach the same
        cancel through `back`."""
        pl = self.pl
        if not pl._tool or pl.cart_error is not None:
            return False
        lua = pl.lua
        if lua is not None:
            took = lua.ui_cancel(True) if getattr(lua, "own_ui", False) else _ui_draw.run_cancel(True)
        else:
            ui = pl.ui
            took = ui is not None and _ui_draw.app_cancel(ui[0], True)
        if not took:
            return False
        self._owe = max(self._owe, 1)
        pl.ws.request_frame()
        return True

    def _app_back(self, take):
        pl = self.pl
        if not pl._tool or pl.cart_error is not None:
            return False
        lua = pl.lua
        if lua is not None:             # the run's own context, or the toolkit it links
            if getattr(lua, "own_ui", False):
                return lua.ui_back(take)
            return bool(_ui_draw.run_back(take))
        ui = pl.ui
        return ui is not None and bool(_ui_draw.app_back(ui[0], take))

    def animates(self):
        """Whether the running cart has a frame to draw now. A game always
        has. An app on the verbs (`on_change`) has one while input is going
        on, while a frame it drew still owes the next, while the engine has a
        part in motion (`api_owes`: a toast, a fade; `api_moving`: a page's
        fling or glide) and while an editor
        handle it opened changed since it drew (`EditorHandle.owes`); still,
        it draws nothing, and its window keeps its last frame."""
        pl = self.pl
        if not self.on_change:
            return True
        if self._owe or self._input_active(pl.ws.input):
            return True
        ui = pl.ui
        if ui is not None and (_ui_owes(ui[0]) or _ui_moving(ui[0])):
            return True
        for ed in pl.editors:
            if ed.owes():
                return True
        return False

    def _input_active(self, inp):
        """A key, a button, the wheel or the middle button this frame: what
        an app's `_update` and its verbs read. The pointer's moves are the
        console's own redraw leg (`Workstation._ptr_changed`). A deaf run's
        keys and buttons are another window's."""
        pl = self.pl
        if not pl.deaf and self._keys_active(inp):
            return True
        if getattr(inp, "wheel_x", 0) or getattr(inp, "wheel_y", 0):
            return True
        return bool(getattr(inp, "pointer_buttons", 0) & _PB_MIDDLE)

    def _keys_active(self, inp):
        if getattr(inp, "last_key", 0):
            return True
        bm = self._masks
        if bm is not None:
            m = bm(_NET_BUTTONS, None, _UI_MASKS)
            return bool(m[0] or m[1])
        for i in range(len(_NET_BUTTONS)):
            if inp.held(_NET_BUTTONS[i]) or inp.pressed(_NET_BUTTONS[i]):
                return True
        return False

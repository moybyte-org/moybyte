# Map (grep -n a name to jump there):
#   EditorDesk              the desk Editor: a workspace of panes under the app's row
#   EditorDesk.active       whether the Editor shows workspaces here
#   EditorDesk.switch       change workspace, committing only dirty panes
#   EditorDesk.focus_pane   move the keys to a pane, committing nothing
#   EditorDesk.draw         ground, cards, each pane's content, headers, dividers
#   EditorDesk._tab_pane    an Editor tab drawn in a pane through a viewport
#   EditorDesk._game        the Run slot: the game's frame at a whole scale
#   EditorDesk._sheet       the Sheet: two pages at 2x, a pick sets the sprite and brush
#   EditorDesk.handle_input  the workspace chords, then the focused pane's keys
#   EditorDesk.handle_pointer  the row, the dividers, a pane's header and its content
"""The Editor on the desk (docs/media/look_2026-10/EDITOR_LAYOUT.md, concept
B, and MAXIMISE_VARIANTS.md's Resize): several of its parts at once, as five
workspaces whose dividers move (`runtime/editor_panes.py` holds the
geometry). The app's row is the band the bar lends the Editor
(`EditorApp.draw_zone`): the workspaces, undo, redo and the Play slot.

A pane is a card on the Editor's ground with a 32 px header (its icon and
name, or its tabs, then its tools); the focused one takes the keys and
wears the `value` edge along its top. An Editor tab in a pane (Settings,
Code, Blocks, Sprite, Map, Scene, Sound) is that tab's own surface laid
out at the pane's size with no bar, drawn through a viewport on the
window's canvas, and its taps translated into it; the panes the desk adds
(the Run slot, the Sheet) are drawn here. Moving focus never commits; a
workspace switch commits the panes it leaves that are dirty, through
`EditorApp.commit_later`, as a tab switch does.

On a surface smaller than the workspace's least size (a doubled desk theme's
half-size surface) the focused pane stands alone over the panes area, as a
grown one does, and the keys move it to the next. On a handheld the
Editor is one tab at a time under its one bar (`EditorApp`)."""

try:
    import moybuf
    import ui
    import editor_panes as P
    import editor_parts as Parts
    import editor_sound as Sound
    import layout_base as _lb
    from layers import Layer
    from ticks import _ticks_ms, _ticks_diff
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import moybuf
    from runtime import ui
    from runtime import editor_panes as P
    from runtime import editor_parts as Parts
    from runtime import editor_sound as Sound
    from runtime import layout_base as _lb
    from runtime.layers import Layer
    from runtime.ticks import _ticks_ms, _ticks_diff

from array import array

# The surface each Editor tab draws with and the attribute on it holding the
# layout its `relayout` builds (the window's layout context captures the same).
_OWNERS = {"cards": ("cards_layer", "layout"), "blocks": ("block_ui", "block_layout"),
           "paint": ("paint_layer", "layout"), "map": ("map_ui", "layout"),
           "scene": ("scene_ui", "layout"), "music": ("music_ui", "layout"),
           "theme_config": ("theme_config_layer", "layout"),
           "theme_sprites": ("theme_sprites_layer", "layout")}

# The tabs whose pane layouts `warm_step` builds ahead: those `_pane_layout`
# knows how to lay out.
_OWNERS_OR_CODE = tuple(_OWNERS) + ("code",)

# The Editor tabs whose desk pane is drawn here on the verbs over the tab's
# editor core, not by the tab's own surface: the Sound pane.
_ON_VERBS = ("music",)

# The panes whose frames may be their own: a frame whose input reached that
# pane alone draws it alone (`_pane_rows`); the Sprite pane's while its
# pickers are up.
_OWN_FRAMES = ("blocks", "cards", "code", "theme_config", "map", "music", "paint")

# The Editor tabs drawn as one verbs page whose gen says what it shows: a
# frame of the page may move its pixels or draw its changed rows alone (the
# Code tab's and theme Config's, the text view's rows).
_PAGES = ("cards", "blocks", "code", "theme_config")

# The Editor tabs whose surface, drawn in a pane, fills the pane's whole body
# before anything else (Sprite, Map and Scene their `body_fill`, Code its
# ground round the text view's rows, Settings its `ui.page`): their card is
# drawn down to the header only, its body never seen
# (tests/test_editor_desk.py holds it).
_FILLS_BODY = ("paint", "map", "scene", "code", "cards", "theme_config", "theme_sprites")

# Which workspace shows an Editor tab, for a tab opened from elsewhere (a
# crash's throw to Code, Files opening a sprite sheet).
_HOME = {"cards": "settings", "code": "code", "blocks": "code", "paint": "art",
         "map": "art", "scene": "world", "music": "sound",
         "theme_config": P.THEME, "theme_sprites": P.THEME}

# The tools a pane's header carries before its grow, right to left.
_HEAD_TOOLS = {"paint": (("rotate", "Rotate\tR", "rot"), ("flip", "Flip\tF", "fliph")),
               "map": (("search", "Zoom\tA", "map:zoom"), ("eraser", "Erase", "map:erase"),
                       ("select", "Select", "map:select"), ("bucket", "Fill", "map:flood"),
                       ("rect", "Box", "map:rect"), ("pencil", "Pen", "map:stamp")),
               "scene": (("search", "Zoom\tA", "scene:zoom"), ("check", "Snap", "scene:snap"),
                         ("trash", "Delete", "scene:delete"), ("to_back", "Back", "scene:back"),
                         ("to_front", "Front", "scene:front"), ("flip", "Flip", "scene:flip"))}

# The Code pane's header tools right of its file tabs, and its more menu's
# rows: (icon, word, the Code tab's tool).
_CODE_TOOLS = (("more", "More", "more"), ("arrow_right", "Go to definition", "goto"),
               ("search", "Find\tCtrl+F", "find"))
_CODE_MORE = (("select", "Select", "sel"), ("copy", "Copy\tCtrl+C", "copy"),
              ("cut", "Cut\tCtrl+X", "cut"), ("paste", "Paste\tCtrl+V", "paste"),
              ("indent", "Indent\tTab", "indent"), ("outdent", "Outdent", "outdent"),
              ("linenums", "Line numbers", "gutter"), ("code", "Complete", "auto"))

_TAB_PAD = 28               # a header tab's room beside its word
LOUPE = 3                   # the Sheet's loupe: a held tile at 3x its 16 px
_DOUBLE_MS = 400            # a second press this soon on a header or handle
_FULL_MS = 1000

# The key event (native/moy_input/moy_input.h): the code in the low 16 bits,
# the modifiers above.
_MODS_AT = 16
_CTRL, _SHIFT, _ALT = 1, 2, 4
_F6 = 0x101 + 5
_LEFT, _RIGHT, _UP, _DOWN = 0x110, 0x111, 0x112, 0x113
_ENTERS = (10, 13)
_PAD_DIRS = (("left", -1, 0), ("right", 1, 0), ("up", 0, -1), ("down", 0, 1))
_PB_MIDDLE = 2


class EditorDesk(Layer):
    """The desk Editor's content layer (the "menu" window's, while `active`)."""

    id = "editor_desk"

    def __init__(self, ws, host):
        self.ws = ws
        self._host = host("editor")
        self.key = "settings"           # the workspace shown
        self._focus = {}                # workspace -> the focused pane's index
        self._tabs = {}                 # pane -> its open tab's index
        self.grown = None               # the pane grown over the workspace
        self._arr = None                # the Arrangement on show
        self._placed = None             # its (panes, dividers)
        self._lay = {}                  # (tab, w, h, fs, cs) -> the pane's layout
        self._drag = None               # a divider held: [path, i, axis, start, at, base, live, stop]
        self._press = None              # the pane a press went down in
        self._last = (None, 0)          # the last press: its target, when
        self._ev = 0                    # last frame's key event
        self._shown = None              # the workspace whose panes were built
        self._seen = None               # the Editor's tab as the desk last left it
        self._zoomed = None             # the cart the desk zooms were set for
        self._flash = None              # (pane, why): a divider stopped at its minimum
        self._sheet_page = 0
        self._loupe = None              # (tile, x, y): a press held on the Sheet
        self._still = None
        self._cursors = []
        self.part_state = {}            # the verbs parts' kept state (a typed tag)
        self._mid = None                # the middle button's last place, while held
        self._scale = 0                 # the Run slot's scale, last drawn
        self._still_at = None           # where the game stands on the surface
        self._ref = False               # the sheet drawn is the Reference's
        self._picked = 0                # the sprite picked with no Sprite pane built
        self._was_playing = False
        self.paused = False             # the Run slot's pause: the run held, its still shown
        self._back_tab = {}             # a pane's tab before Play took it to Game
        self._fresh = 0                 # this frame's key event, where it is new
        self._tools_x = {}              # pane index -> its header tools' left edge
        self._warm = {}                 # sig -> (Arrangement, placed) built ahead
        self._warm_for = None           # what the workspaces were warmed for
        self._warm_left = []            # ...and those still to warm
        self._warm_due = False          # a quiet frame has warming to do
        self.dt = 0
        # The shown protocol (docs/surface_model_v1.md §14.3) while a prompt
        # is up: whether the buffer this draw goes into holds the desk as the
        # last draw left it (`retained`), what the desk was a function of then
        # (`_rows_key`), and what the draw changed (`changed`).
        self._known = False
        self._rows_key = None
        self._changed = None
        self._acts = 0                  # input the desk took itself (a press, a chord)
        self._in_code = False           # this press went to the focused pane (its own frames)
        self._box = array("i", [0] * 4)
        # ...and while its focused pane is the Code tab (or theme Config), on
        # every frame: the WM's word for the buffer, the lag it named, what
        # the desk was a function of at the last code frame, and whether the
        # buffer `shift_last` reads holds the page as the last draw left it
        # (`vouched`: 2, 3 where the ring says the buffer this frame paints
        # holds it too, else 1, 0 untold).
        self._ret = array("i", [0] * 5)
        self._own_key = None            # ...and at the last frame of a pane of its own
        self._clip = None               # the rect a frame of the Sound pane's own is clipped to
        # the layout the desk drew in last: its window's, which the WM has
        # not installed yet where it asks HELD and takes_lag
        self._drew_in = None
        self._vouch = 0

    # -- where it applies -------------------------------------------------------

    def active(self):
        """Whether the Editor shows workspaces: a desk, and an open cart or a
        theme in the Editor's theme mode."""
        return _lb.metric_class() == _lb.DESK and (
            self.ws.cart is not None or self.ws.editor_app.theme is not None)

    def theme_shown(self, on):
        """The theme mode opened (`on`) or left: its one workspace, Config
        beside Sprites, else the workspace the cart had."""
        if on and self.key != P.THEME:
            self._was = self.key
            self.key = P.THEME
        elif not on and self.key == P.THEME:
            self.key = getattr(self, "_was", None) or "settings"
        else:
            return
        self.grown = None
        self._arr = None
        self._placed = None
        self._shown = None

    def _unit(self):
        return max(1, _lb.chrome_size(self.ws.look.effective_font_scale())
                   // _lb.render_scale())

    def _area(self):
        lay = self.ws.layout
        g = P.GUTTER * self._unit()
        return (lay.cx + g, lay.cy + g, lay.cw - 2 * g, lay.ch - 2 * g)

    def min_window(self):
        """The least window the workspace on show holds, at the font scale
        (as a registered app's minimum is): its panes' minimums, the gutters
        round them, the band and the window's strip."""
        mw, mh = self.arrangement().minimum()
        u = self._unit()
        fs = max(1, self.ws.look.effective_font_scale())
        g = 2 * P.GUTTER * u
        band = _lb.bar_rows(self.ws.look.effective_chrome_scale())
        return (-(-(mw + g + 2) // fs), -(-(mh + g + band + 48 * u) // fs))

    def _blocks(self):
        cart = self.ws.cart
        return bool(cart is not None and cart.get("blocks"))

    def arrangement(self):
        """The workspace on show at the surface's size, its dividers as the
        board remembers them for this project and size."""
        area = self._area()
        u = self._unit()
        arr = self._arr
        if arr is not None and arr.key == self.key and arr.area == area and arr.unit == u:
            return arr
        got = self._warm.pop(self._warm_sig(self.key, area, u), None) if self._warm else None
        if got is not None:
            self._arr, self._placed = got
            return got[0]
        arr = self._new_arrangement(self.key, area, u)
        self._arr = arr
        self._placed = None
        return arr

    def _new_arrangement(self, key, area, u):
        arr = P.Arrangement(key, area, u, self._blocks())
        arr.restore(self._saved().get(self._mem_key(arr)))
        return arr

    def _warm_sig(self, key, area, u):
        cart = self.ws.cart or {}
        return (key, area, u, self._blocks(), cart.get("path") or cart.get("title"))

    def warm_step(self):
        """On a quiet frame, one workspace not on show made ready as its first
        switch would make it -- its arrangement restored and placed, each of
        its tab panes' layouts built at the pane's size -- so the switch only
        draws. One a call, in the Editor window's layout context; nothing
        once every workspace is ready for this cart and size, and nothing
        while a pane stands alone or a divider is held. Each is taken by
        `arrangement` once, as a fresh one would have been made."""
        if not self._warm_due or self._drag is not None:
            return
        if not self.active() or self.key not in P.KEYS:
            self._warm_due = False
            return
        run = getattr(self.ws.wm, "in_window", None)
        if (run is None or not run("menu", self._warm_next)) and self.ws.screen == "menu":
            self._warm_next()

    def _warm_mark(self, area, u):
        return self._warm_sig(None, area, u) + (self.ws.look.look_gen,)

    def _warm_next(self):
        area = self._area()
        u = self._unit()
        mark = self._warm_mark(area, u)
        if self._warm_for != mark:
            self._warm_for = mark
            self._warm.clear()
            self._warm_left = [k for k in P.KEYS if k != self.key]
        left = self._warm_left
        self._warm_due = len(left) > 1
        while left:
            key = left.pop(0)
            sig = self._warm_sig(key, area, u)
            if key == self.key or sig in self._warm:
                continue
            arr = self._new_arrangement(key, area, u)
            mw, mh = arr.minimum()
            if area[2] < mw or area[3] < mh:
                continue                      # one pane at a time here
            placed = arr.place()
            self._warm[sig] = (arr, placed)
            for pane, r, _path in placed[0]:
                for c in pane:
                    tab = P.CONTENTS[c][2]
                    if tab is not None and c not in _ON_VERBS and tab in _OWNERS_OR_CODE:
                        self._pane_layout(tab, self._body(r, u, pane))
            return

    def placed(self):
        arr = self.arrangement()
        if self._placed is None:
            self._placed = arr.place()
        return self._placed

    def _mem_key(self, arr):
        cart = self.ws.cart or {}
        return "%s|%s|%dx%d" % (cart.get("path") or cart.get("title") or "",
                                arr.key, arr.area[2], arr.area[3])

    def _saved(self):
        got = self.ws.system.get("editor_panes", None)
        return got if isinstance(got, dict) else {}

    def _remember(self):
        arr = self._arr
        if arr is None:
            return
        got = dict(self._saved())
        got[self._mem_key(arr)] = arr.state()
        self.ws.system.set("editor_panes", got)

    # -- workspaces and focus ---------------------------------------------------

    def panes(self):
        return self.placed()[0]

    def focused(self):
        """(pane, rect, path) of the focused pane."""
        panes = self.panes()
        i = self._focus.get(self.key, 0)
        return panes[min(i, len(panes) - 1)]

    def tab_of(self, pane):
        """The content key a pane shows: its open tab."""
        return pane[min(self._tabs.get(pane, 0), len(pane) - 1)]

    def switch(self, key):
        """Show workspace `key`. The panes left behind that hold an edit owe
        their commit (`EditorApp.commit_later`); focus lands where it was."""
        if key == self.key or key not in P.KEYS or self.key == P.THEME:
            return False
        app = self.ws.editor_app
        for pane, _r, _p in self.panes():
            tab = P.CONTENTS[self.tab_of(pane)][2]
            if tab is not None and not app._tab_is_clean(tab):
                app.commit_later(tab)
        if self.key == "sound":
            self.ws.music_ui._stop_music_preview()
        self.key = key
        app.zone_gen += 1               # the row marks the workspace on show
        self.grown = None
        self._arr = None
        self._placed = None
        self._shown = None
        self._land()
        return True

    def step(self, d):
        if self.key not in P.KEYS:
            return False
        i = (P.KEYS.index(self.key) + d) % len(P.KEYS)
        return self.switch(P.KEYS[i])

    def _land(self):
        """The workspace's panes built, and the focused pane's tab the
        Editor's (the history's and the keyboard's)."""
        if self._shown == self.key:
            return
        self._shown = self.key
        app = self.ws.editor_app
        for pane, _r, _p in self.panes():
            for c in pane:
                tab = P.CONTENTS[c][2]
                if tab is not None:
                    app.build_tab(tab)
        self.focus_pane(self._focus.get(self.key, 0))
        self._desk_zooms()

    def _desk_zooms(self):
        """The Map and the Scene open at 2x on a desk (EDITOR_LAYOUT.md),
        once a cart; the wheel and A move them after."""
        ws = self.ws
        path = (ws.cart or {}).get("path")
        if self._zoomed == path:
            return
        mu, su = ws.built("map_ui"), ws.built("scene_ui")
        if mu is None and su is None:
            return
        self._zoomed = path
        if mu is not None and mu.map_zoom == 0:
            mu.map_zoom = 1                 # 16 px a cell: 2x
        if su is not None and su.scene_zoom == 0:
            su.scene_zoom = 1

    def focus_pane(self, i):
        """Move the keys to pane `i` (reading order). Commits nothing."""
        panes = self.panes()
        i %= len(panes)
        self._focus[self.key] = i
        tab = P.CONTENTS[self.tab_of(panes[i][0])][2]
        app = self.ws.editor_app
        if tab is not None:
            app.focus_tab(tab)
        else:
            self.ws._set_text_mode(False)
        self._seen = app.tab
        self.ws._dirty = True

    def follow(self):
        """A tab opened from outside the desk (`EditorApp.set_tab`): its
        workspace, its pane focused."""
        tab = self.ws.editor_app.tab
        if tab == self._seen:
            self._land()
            return
        self._seen = tab
        cur = self.focused()
        if P.CONTENTS[self.tab_of(cur[0])][2] == tab:
            self._land()
            return
        for k in (self.key, _HOME.get(tab)):
            if k is None:
                continue
            if k != self.key:
                self.key = k
                self.ws.editor_app.zone_gen += 1
                self.grown = None
                self._arr = None
                self._placed = None
                self._shown = None
            for n, (pane, _r, _p) in enumerate(self.panes()):
                for t, c in enumerate(pane):
                    if P.CONTENTS[c][2] == tab:
                        self._tabs[pane] = t
                        self._focus[self.key] = n
                        self._land()
                        self.focus_pane(n)
                        return

    def alone(self):
        """The pane shown over the whole panes area, or None: the grown one,
        else the focused one where the area is under the workspace's least
        size (one pane at a time)."""
        if self.grown is not None:
            return self.grown
        _x, _y, w, h = self._area()
        mw, mh = self.arrangement().minimum()
        return self.focused()[0] if w < mw or h < mh else None

    def grow(self, pane=None):
        """Grow a pane over the workspace, or back (Ctrl+Shift+Enter, Esc,
        a double tap on its header, its grow tool)."""
        if pane is None or self.grown == pane:
            self.grown = None
        else:
            self.grown = pane
        self.ws._dirty = True

    # -- a tab in a pane ----------------------------------------------------------

    def _pane_layout(self, tab, r):
        """The layout tab `tab`'s surface takes in a pane `r` big: built by
        its own `relayout` at that size with no bar, once per size (and for
        Code, per whether it shows its symbol keys, and for Sprite, per
        whether its hints have keys to name, which the last input decides:
        a layout that disagrees is one the tab rebuilds as it draws, every
        frame)."""
        ws = self.ws
        fs = ws.look.effective_font_scale()
        cs = ws.look.effective_chrome_scale()
        sym = tab == "code" and ws.code_layer.wants_symbols()
        keys = tab == "paint" and ws.paint_layer._keys()
        key = (tab, r[2], r[3], fs, cs, ws.look.look_gen, sym, keys)
        lay = self._lay.get(key)
        if lay is not None:
            return lay
        cv = ws.sys_canvas
        vp = cv.viewport()
        cv.set_viewport(vp[0] + r[0], vp[1] + r[1], r[2], r[3])
        _lb.in_pane(True)
        old = self._layout_of(tab)
        try:
            if tab == "code":
                ws._relayout_code()
            else:
                name, _attr = _OWNERS[tab]
                getattr(ws, name).relayout(r[2], r[3], fs, cs)
            lay = self._layout_of(tab)
        finally:
            _lb.in_pane(False)
            self._set_layout(tab, old)
            cv.set_viewport(*vp)
        if len(self._lay) > 24:
            self._lay.clear()
        self._lay[key] = lay
        return lay

    def _layout_of(self, tab):
        ws = self.ws
        if tab == "code":
            return ws.code_layout
        name, attr = _OWNERS[tab]
        return getattr(getattr(ws, name), attr)

    def _set_layout(self, tab, lay):
        ws = self.ws
        if tab == "code":
            ws.code_layout = lay
            ed = ws.editor
            if ed is not None and lay is not None and (ed.COLS, ed.ROWS) != (lay.cols, lay.rows):
                ed.set_view_size(lay.cols, lay.rows)
            return
        name, attr = _OWNERS[tab]
        setattr(getattr(ws, name), attr, lay)

    def _in_pane(self, tab, r, fn, *args):
        """Run `fn(*args)` with tab `tab`'s surface laid out in pane rect
        `r` and the canvas's viewport on it; the bar is not the pane's."""
        ws = self.ws
        lay = self._pane_layout(tab, r)
        old = self._layout_of(tab)
        cv = ws.sys_canvas
        vp = cv.viewport()
        self._set_layout(tab, lay)
        cv.set_viewport(vp[0] + r[0], vp[1] + r[1], r[2], r[3])
        bar = ws.bar_layer
        bar.held = True
        try:
            return fn(*args)
        finally:
            bar.held = False
            cv.set_viewport(*vp)
            if tab != "code":
                self._set_layout(tab, old)
            else:
                ws.code_layout = old

    def _tab_pane(self, tab, r, dt, own=False):
        layer = self.ws.layer_of(tab)
        if layer is None:
            return
        if tab in _PAGES:
            # the page's own shift and rows frames, on a frame of its pane's
            # own where the WM says the buffer `shift_last` reads holds it as
            # the last draw left it; a whole frame draws it whole
            page = self.ws.block_ui if tab == "blocks" else layer
            page.vouch = self._vouch if own and self._vouch >= 2 else 1
            try:
                self._in_pane(tab, r, layer.draw, dt)
            finally:
                page.vouch = 0
            return
        if tab == "paint":
            # the pickers draw their body alone over the page the buffer
            # holds where the WM says it holds it as the last draw left it,
            # but for the lag it names (`retained`), in the pane's pixels
            layer.vouch = self._vouch if own else 1
            ret = self._ret
            lag = layer.lag
            lag[0] = ret[1] - r[0]
            lag[1] = ret[2] - r[1]
            lag[2] = ret[3] - r[0]
            lag[3] = ret[4] - r[1]
            try:
                self._in_pane(tab, r, layer.draw, dt)
            finally:
                layer.vouch = 0
            return
        self._in_pane(tab, r, layer.draw, dt)

    # -- drawing ------------------------------------------------------------------

    # -- a prompt's rows (docs/scroll_and_text_2026-10.md §5) -----------------------
    #
    # While a pane's tab has a prompt up (the Blocks entry prompts, Cart info,
    # New script) the desk under it does not change but by input that reaches
    # it, and the window's content speaks the shown protocol: a frame whose
    # input only typed into the prompt's field or moved its focus, into a
    # buffer that holds the desk as the last draw left it, draws the prompt's
    # changed rows alone and says so (`changed`), so the window's stamp copies
    # those rows and nothing else.

    def _prompt(self):
        """(tab, its layer, pane, rect) of the pane on show whose tab has a
        prompt up, else None."""
        ws = self.ws
        alone = self.alone()
        for pane, r, _path in self.panes():
            if alone is not None:
                if pane != alone:
                    continue
                r = self._area()
            tab = P.CONTENTS[self.tab_of(pane)][2]
            layer = ws.block_ui if tab == "blocks" else ws.cards_layer if tab == "cards" else None
            if layer is not None and layer.prompt_host() is not None:
                return tab, layer, pane, r
        return None

    @property
    def HELD(self):
        ws = self.ws
        was = ws.layout
        if self._drew_in is not None:
            ws.layout = self._drew_in
        try:
            return self._prompt() is not None or self._own_pane() is not None
        finally:
            ws.layout = was

    def retained(self, known, x0, y0, x1, y1):
        self._known = bool(known) and (x1 <= x0 or y1 <= y0)
        r = self._ret
        r[0] = 1 if known else 0
        r[1] = x0
        r[2] = y0
        r[3] = x1
        r[4] = y1

    def vouched(self, v):
        self._vouch = v

    def takes_lag(self, lag):
        """Whether this frame's draw repaints `lag` [x0, y0, x1, y1), what
        its target lags (a direct render's): a pane's own frames repaint a
        lag inside that pane; a prompt's rows need the desk as the last
        draw left it, and a lag outside the pane would draw the desk whole
        and leave the next buffer as far behind."""
        if lag[2] <= lag[0] or lag[3] <= lag[1]:
            return True
        ws = self.ws
        was = ws.layout
        if self._drew_in is not None:
            ws.layout = self._drew_in
        try:
            return self._takes(lag)
        finally:
            ws.layout = was

    def _takes(self, lag):
        if self._prompt() is not None:
            return False
        own = self._own_pane()
        if own is None:
            return False
        b = own[3] if own[0] == "music" else own[4]
        return (lag[0] >= b[0] and lag[1] >= b[1] and lag[2] <= b[0] + b[2]
                and lag[3] <= b[1] + b[3])

    def _spills(self):
        """Whether a pane header's words run past the header into the body
        under it (a font scale over the chrome's): a pane's own rows
        there are the header's too, so its frames draw whole."""
        u = self._unit()
        return ui.text_size("M")[1] * self.ws.look.effective_font_scale() > (P.HEADER - 4) * u

    def changed(self):
        return self._changed

    def unclip(self, cv):
        """A draw's own clip let go: back to the frame's (`_pane_rows`)."""
        if self._clip is None:
            cv.clip()
        else:
            cv.clip(*self._clip)

    # -- a pane's own frames (docs/scroll_and_text_2026-10.md §15 P5) -------------
    #
    # While the focused pane shows Blocks, Settings, the Code tab, theme
    # Config, the Map or the Sound pane, the desk speaks the shown protocol:
    # a frame whose input reached only that pane and changed nothing another
    # pane draws (no edit the other panes show: the made code, the sounds
    # list) draws that pane alone and names it as what it changed. A tab's
    # pane draws its body (its header holds nothing the frame moved); the
    # Sound pane, drawn on the desk's own verbs, its card, header and body.

    def _own_pane(self):
        """(content, tab, pane, rect, body, index) of the focused pane where
        its frames may be its own, else None."""
        pane, r, _path = self.focused()
        alone = self.alone()
        if alone is not None:
            if pane != alone:
                return None
            r = self._area()
        c = self.tab_of(pane)
        if c not in _OWN_FRAMES:
            return None
        tab = P.CONTENTS[c][2]
        if c != "music" and self.ws.layer_of(tab) is None:
            return None
        if c == "paint" and self.ws.layer_of(tab)._pop != "pickers":
            return None
        return (c, tab, pane, r, self._body(r, self._unit(), pane),
                self._focus.get(self.key, 0))

    def _own_data(self, c):
        """What the frame's input may change that a pane besides `c` draws or
        `c`'s header shows: an edit's undo step, the sounds, the blocks'
        object, the Map's tools, the Code pane's file and find tool."""
        ws = self.ws
        if c in ("code", "theme_config"):
            return (ws.code_file_name(), bool(ws.layer_of(c)._find_open))
        if c == "blocks":
            bu = ws.block_ui
            be = bu.blocks_ed
            if be is None:
                return None
            return (id(be), be._hist.gen, be.target, bu.blk_graduated)
        if c == "music":
            me = Sound.editor(self)
            if me is None:
                return None
            st = Sound._state(self)
            if st.get("rename") is not None or st.get("menu"):
                return False            # a popover over the panes beside it
            return (id(me), me._hist.gen, me.view, me.sfx_idx, me.track_idx,
                    len(me.bank.sfx), len(me.bank.music), ws.music_ui.music_preview)
        if c == "map":
            mu = ws.map_ui
            return (mu.map_tool, mu.map_erase)
        if c == "paint":
            pe = ws.paint
            if pe is None:
                return False
            return (pe.n, pe.size, pe.sheet, pe.sheet.gen)
        return None

    def _pane_rows(self, own, dt):
        """The frame as the focused pane's alone, where it may be one: True
        where the pane drew and nothing else changed."""
        ws = self.ws
        c, tab, pane, r, body, n = own
        cv = ws.sys_canvas
        look = ws.look
        app = ws.editor_app
        data = self._own_data(c)
        key = (c, self._acts, self.key, n, self.grown, pane, r, self.placed()[0],
               cv.w, cv.h, look.look_gen, look.effective_font_scale(),
               look.effective_chrome_scale(), ws.bar_layer.shown_key("menu"), data,
               self._loupe, self._flash, self.part_state.get("code more"),
               self.part_state.get("head more"))
        ret = self._ret
        same = key == self._own_key
        # a frame the WM did not speak to (the content not held when it
        # asked) arms nothing: its row is the WM's to settle first
        self._own_key = key if ret[0] else None
        if (not same or data is False or not ret[0] or app.playing()
                or ws._animating(dt, False)
                or self._spills() or self._over_panes()):
            return False
        if c == "paint" and ws.paint_layer._pop_tap is not None \
                and ws.paint_layer._host.tap_armed():
            return False                # a release the pickers' pass may pick a sprite from
        if c == "music" and not self._host.quiet():
            return False                # the desk's verbs take input: focus may move off the pane
        b = r if c == "music" else body
        x0, y0, x1, y1 = b[0], b[1], b[0] + b[2], b[1] + b[3]
        if ret[3] > ret[1] and ret[4] > ret[2] and (ret[1] < x0 or ret[2] < y0
                                                     or ret[3] > x1 or ret[4] > y1):
            return False                # the buffer lags outside the pane
        if c == "music":
            # the desk's verbs run whole (focus rests on the first pane's
            # header), their pixels clipped to the pane
            th = ws.theme_colors
            u = self._unit()
            self._corners(cv, th["panel"], r[0], r[1], r[2], r[3], P.RADIUS * u)
            self._card(cv, th, r, True, u)
            self._cursors = []
            self._clip = r
            cv.clip(*r)
            try:
                self._frame(cv, th, self._shown_panes(), u)
            finally:
                self._clip = None
                cv.clip()
        else:
            self._tab_pane(tab, body, dt, True)
            a = ws.paint_layer.alone_rect if c == "paint" else None
            if a is not None:
                # the pickers' body alone, over the page the buffer holds
                x0, y0 = body[0] + a[0], body[1] + a[1]
                x1, y1 = x0 + a[2], y0 + a[3]
        self._changed = [x0, y0, x1, y1]
        return True

    def _over_panes(self):
        """Whether the desk drew over the panes last frame: a loupe, a
        divider's flash, or a popover or tooltip of its pass."""
        return (self._loupe is not None or self._flash is not None and self._drag is not None
                or self._host.popped(self._box))

    def _prompt_rows(self, dt):
        """The frame as a prompt's rows frame, where it may be one: True
        where the prompt drew its changed rows alone."""
        ws = self.ws
        got = self._prompt()
        known = self._known
        self._known = False
        if got is None:
            self._rows_key = None
            return False
        tab, layer, pane, r = got
        cv = ws.sys_canvas
        look = ws.look
        key = (layer.prompt_host(), self._acts, self.key, self._focus.get(self.key, 0),
               self.grown, pane, r, self.placed()[0],
               cv.w, cv.h, look.look_gen, look.effective_font_scale(),
               look.effective_chrome_scale(), ws.bar_layer.shown_key("menu"))
        same = key == self._rows_key
        self._rows_key = key
        rows = layer._rows
        if not same or not known or ws._animating(dt):
            rows.desk_frame(False)
            return False
        u = self._unit()
        body = self._body(r, u, pane)
        # the buffer holds the desk as the frame before left it: the
        # prompt's frame may be its rows alone
        rows.desk_frame(True)
        if not self._in_pane(tab, body, layer.prompt_rows, dt):
            return False
        b = self._box
        if rows.floor_box(layer.prompt_host(), b):
            self._changed = [body[0] + b[0], body[1] + b[1], body[0] + b[0] + b[2],
                             body[1] + b[1] + b[3]]
        else:
            self._changed = [0, 0, 0, 0]
        return True

    def draw(self, dt):
        ws = self.ws
        self._drew_in = ws.layout
        self.dt = dt
        self.follow()
        self._play_tabs()
        if not self._warm_due and self.key in P.KEYS \
                and self._warm_for != self._warm_mark(self._area(), self._unit()):
            self._warm_due = True
        self._changed = None
        try:
            if not (self.HELD and self._prompt_rows(dt)):
                free = self._prompt() is None
                own = self._own_pane() if free else None
                alone = own is not None and self._pane_rows(own, dt)
                if own is None:
                    self._own_key = None
                if not alone:
                    self._draw(dt)
                    if own is not None:
                        cv = ws.sys_canvas
                        self._changed = [0, 0, cv.w, cv.h]
        finally:
            self._known = False
            self._ret[0] = 0
            self._vouch = 0

    def _draw(self, dt):
        ws = self.ws
        cv = ws.sys_canvas
        th = ws.theme_colors
        lay = ws.layout
        u = self._unit()
        panes, divs = self.placed()
        fi = self._focus.get(self.key, 0)
        alone = self.alone()
        self._ground(cv, th["panel"], lay, panes, divs, alone, u)
        shown = []
        self._cursors = []
        self._still_at = None
        for n, (pane, r, path) in enumerate(panes):
            if alone is not None:
                if pane != alone:
                    continue
                r = self._area()
            shown.append((n, pane, r))
            c = self.tab_of(pane)
            body = self._body(r, u, pane)
            tab = P.CONTENTS[c][2]
            filled = (body[1] if tab in _FILLS_BODY and c not in _ON_VERBS
                      and ws.layer_of(tab) is not None else 0)
            self._card(cv, th, r, n == fi, u, filled)
            if tab is not None and c not in _ON_VERBS:
                self._tab_pane(tab, body, dt)
            elif c == "game":
                self._game(cv, th, body, u)
            elif c in ("sheet", "sprites", "stamps"):
                self._ref = c == "sprites"
                self._sheet(cv, th, body, u, c == "stamps")
            elif c == "maps":
                Parts.drawn_before(self, c, cv, th, body, u)
        if alone is None:
            self._dividers(cv, th, divs, u)
        self._frame(cv, th, shown, u)
        if self._loupe is not None and not ws.pointer.down:
            self._loupe = None
        if self._loupe is not None:
            self._draw_loupe(cv, th, u)
        ws.bar_layer._draw_status_strip("menu")
        self._dock()

    def _ground(self, cv, col, lay, panes, divs, alone, u):
        """The Editor's ground where the cards leave it showing: the margin
        round the panes area, the gutters between the panes and each card's
        corners (its rounding is drawn over them). The cards cover the rest,
        so the ground is never filled under them."""
        ax, ay, aw, ah = self._area()
        cx, cy, cw, ch = lay.cx, lay.cy, lay.cw, lay.ch
        cv.rect(cx, cy, cw, ay - cy, col)
        cv.rect(cx, ay + ah, cw, cy + ch - ay - ah, col)
        cv.rect(cx, ay, ax - cx, ah, col)
        cv.rect(ax + aw, ay, cx + cw - ax - aw, ah, col)
        rad = P.RADIUS * u
        if alone is not None:
            self._corners(cv, col, ax, ay, aw, ah, rad)
            return
        for _p, _i, _axis, (dx, dy, dw, dh) in divs:
            cv.rect(dx, dy, dw, dh, col)
        for _pane, (x, y, w, h), _path in panes:
            self._corners(cv, col, x, y, w, h, rad)

    def _corners(self, cv, col, x, y, w, h, rad):
        cv.rect(x, y, rad, rad, col)
        cv.rect(x + w - rad, y, rad, rad, col)
        cv.rect(x, y + h - rad, rad, rad, col)
        cv.rect(x + w - rad, y + h - rad, rad, rad, col)

    def _headless(self, pane):
        """The Settings workspace's game has no header: the game at 2x
        where the pane holds it (EDITOR_LAYOUT.md)."""
        return self.key == "settings" and pane == ("game",)

    def _dock(self):
        """The playtest stands in the Run slot or the Game tab, at the
        slot's whole scale (`WindowedWM.dock`): Play selects the workspace's
        Game tab, Stop gives the tab back, and a workspace with no game on
        show hides it, its run held."""
        ws = self.ws
        playing = ws.editor_app.playing()
        dock = getattr(ws.wm, "dock", None)
        if dock is None or not playing:
            return
        if self._still_at is None or self.paused:
            dock("desktop", False)
            return
        ox, oy = self._origin()
        gx, gy, gw, gh = self._still_at
        dock("desktop", (ox + gx, oy + gy, gw, gh))

    def _play_tabs(self):
        """Play selects the workspace's Game tab; Stop gives the tab back."""
        ws = self.ws
        playing = ws.editor_app.playing()
        if playing != self._was_playing:
            self._was_playing = playing
            self.paused = False
            for _n, pane, _r in self._shown_panes():
                if "game" in pane and len(pane) > 1:
                    if playing:
                        self._back_tab[pane] = self._tabs.get(pane, 0)
                        self._tabs[pane] = pane.index("game")
                    elif pane in self._back_tab:
                        self._tabs[pane] = self._back_tab.pop(pane)
                    ws._dirty = True

    def undock(self):
        """The playtest back in a window of its own (the Editor left the
        desk's workspaces)."""
        dock = getattr(self.ws.wm, "dock", None)
        if dock is not None:
            dock("desktop", None)

    def _body(self, r, u, pane=None):
        h = 0 if pane is not None and self._headless(pane) else P.HEADER * u
        return (r[0], r[1] + h, r[2], max(0, r[3] - h))

    def _card(self, cv, th, r, focused, u, filled=0):
        """Pane `r`'s card; with `filled`, the y its content's own fill
        starts at, the card above it alone."""
        x, y, w, h = r
        rad = P.RADIUS * u
        line = None if th.get("surface_light", False) else th["border"]
        if filled:
            if filled > y:
                cv.clip(x, y, w, filled - y)
                cv.rrect(x, y, w, h, rad, th["surface"], line)
                cv.clip()
        else:
            cv.rrect(x, y, w, h, rad, th["surface"], line)
        if focused:
            cv.clip(x, y, w, P.EDGE * u)
            cv.rrect(x, y, w, h, rad, th["value"])
            cv.clip()

    def _frame(self, cv, th, shown, u):
        """Every pane's header on the verbs: its icon and name, or its tabs;
        its grow tool; the empties of the panes not built yet; the tag of a
        divider stopped at its minimum."""
        host = self._host
        host.begin(cv, th, (0, 0, cv.w, cv.h), True, True,
                   elsewhere=not self._part_focused())
        hh = P.HEADER * u
        cell = 32 * u
        for n, pane, r in shown:
            x, y, w, _h = r
            pid = "pane %d" % n
            if self._headless(pane):
                self._still_tool(pid, u)
                continue
            c = self.tab_of(pane)
            tools_w = cell + 4 * u + (7 * cell if c in _ON_VERBS else cell if c == "sounds" else 0)
            if c == "code" and len(pane) == 1:
                pass                            # its file tabs: `_code_head`
            elif len(pane) == 1:
                label, icon, _tab, _m = P.CONTENTS[pane[0]]
                title = self._title(pane[0], label)
                ui.at(x + 8 * u, y, max(0, w - 8 * u - tools_w), hh)
                ui.icon(icon)
                ui.label(title)
                status = self._status(pane[0])
                if status:
                    sx = x + 8 * u + 28 * u + ui.text_size(title)[0] + 14 * u
                    lh = ui.text_size(status)[1]
                    ui.at(sx, y + (hh - lh) // 2, max(0, x + w - tools_w - sx), lh)
                    ui.label(status, {"tone": "dim"})
            else:
                items = [P.CONTENTS[c][0] for c in pane]
                ui.at(x + 4 * u, y + 2 * u, max(0, w - tools_w - 8 * u), hh - 4 * u)
                cur = self._tabs.get(pane, 0)
                got = ui.tabs(pid, items, cur, {"id": pid})
                if got != cur and got is not None and got >= 0:
                    self._tabs[pane] = got
                    if n == self._focus.get(self.key, 0):
                        self.focus_pane(n)
            ui.at(x + w - cell - 2 * u, y, cell, hh)
            if ui.tool("maximize", "Grow\tCtrl+Shift+Enter", {"id": pid + " grow"}):
                self.grow(pane)
            tx = x + w - cell - 2 * u
            tools, folded = self._head_fit(pane, c, x, w, u)
            if folded:
                tx -= cell
                ui.at(tx, y, cell, hh)
                if ui.tool("more", "More", {"id": pid + " head more"}):
                    self.part_state["head more"] = (tx, y + hh, cell, folded)
                    self.ws._dirty = True
            if c == "game":
                tx -= cell
                ui.at(tx, y, cell, hh)
                if ui.tool("sleep", "Pause", {"id": pid + " pause", "current": self.paused,
                                              "disabled": not self.ws.editor_app.playing()}):
                    self.pause(not self.paused)
            for icon, word, op in tools:
                tx -= cell
                ui.at(tx, y, cell, hh)
                if ui.tool(icon, word, {"id": "%s %s" % (pid, op),
                                        "current": self._tool_on(op)}):
                    self._head_tool(op)
            if c == "music":
                tx = Sound.header(self, pid, tx, y, hh, u)
            elif c == "code" and len(pane) == 1:
                tx = self._code_head(pid, x, y, hh, u, tx)
            elif c == "sounds" and self.key == "sound":
                tx = Parts.sounds_header(self, pid, tx, y, hh, u)
            self._tools_x[n] = tx
            if c == "game":
                self._still_tool(pid, u)
            if c in Parts.PARTS:
                Parts.draw(self, c, self._body(r, u, pane), u)
            elif c == "music":
                Sound.draw(self, cv, th, self._body(r, u, pane), u)
            elif c == "stamps":
                Parts.stamps_label(self, self._body(r, u, pane), u)
            elif c == "sprites":
                self._pick_line(self._body(r, u, pane), u)
        for r in self._cursors:
            ui.cursor(*r)
        if self.part_state.get("code more") is not None:
            self._code_more(u)
        if self.part_state.get("head more") is not None:
            self._head_more(u)
        flash = self._flash
        if flash is not None and self._drag is not None:
            pane, why = flash
            for _n, p, r in shown:
                if p == pane:
                    cv.rrect(r[0] - 2 * u, r[1] - 2 * u, r[2] + 4 * u, r[3] + 4 * u,
                             (P.RADIUS + 2) * u, None, th["value"])
                    ui.at(r[0] + 8 * u, r[1] + r[3] - 28 * u, r[2] - 16 * u, 24 * u)
                    ui.label(why, {"tone": "value"})
        ui.end()

    def _code_head(self, pid, x, y, hh, u, tx):
        """The Code pane's header (EDITOR_LAYOUT.md): the cart's scripts as
        tabs, the one shown current; find, go to definition and more."""
        ws = self.ws
        cell = 32 * u
        cl = ws.code_layer
        for icon, word, op in _CODE_TOOLS:
            tx -= cell
            ui.at(tx, y, cell, hh)
            on = op == "find" and bool(getattr(cl, "_find_open", False))
            if ui.tool(icon, word, {"id": "%s code %s" % (pid, op), "current": on}):
                if op == "more":
                    self.part_state["code more"] = (tx, y + hh, cell)
                else:
                    cl.jump_anchor = (tx, y, cell, hh) if op == "goto" else None
                    cl._run_tool(op, ws.editor)
                ws._dirty = True
        name = ws.code_file_name() or ""
        names = ws.code_sources() or [name]
        cur = names.index(name) if name in names else 0
        ui.at(x + 4 * u, y + 2 * u, max(0, tx - x - 8 * u), hh - 4 * u)
        got = ui.tabs(pid + " files", names, cur, {"id": pid + " files"})
        if got is not None and 0 <= got < len(names) and got != cur:
            ws.open_code_file(names[got])
        return tx

    def _head_fit(self, pane, c, x, w, u):
        """The header tools a pane shows and the ones its more menu holds:
        as many as fit right of its name or tabs, the rest (the leftmost)
        folded, as the Code header's are."""
        tools = _HEAD_TOOLS.get(c, ())
        if not tools:
            return tools, ()
        cell = 32 * u
        if len(pane) == 1:
            lead = 8 * u + 28 * u + ui.text_size(self._title(c, P.CONTENTS[c][0]))[0]
        else:
            lead = 4 * u + sum(ui.text_size(P.CONTENTS[k][0])[0] + _TAB_PAD * u for k in pane)
        room = (w - lead - 2 * u - cell - 4 * u) // cell
        if room >= len(tools):
            return tools, ()
        k = max(0, room - 1)
        return tools[:k], tools[k:]

    def _head_more(self, u):
        """A pane header's more menu, hung under its tool: the tools that
        did not fit."""
        ws = self.ws
        tx, ty, cell, ops = self.part_state["head more"]
        w = 160 * u
        ui.at(max(0, tx + cell - w), ty, w, 0)
        if not ui.popover("head more", w):
            self.part_state["head more"] = None
            ui.popover()
            ws._dirty = True
            return
        got = ui.menu("", [o[1] for o in ops], [o[0] for o in ops])
        ui.popover()
        if got is not None and 0 <= got < len(ops):
            self.part_state["head more"] = None
            self._head_tool(ops[got][2])
            ws._dirty = True

    def _menu_open(self):
        """Whether a header's more menu is open (it takes the keys and the
        pointer)."""
        ps = self.part_state
        return ps.get("code more") is not None or ps.get("head more") is not None

    def _code_more(self, u):
        """The Code pane's more menu, hung under its tool: the Code tab's other
        tools."""
        ws = self.ws
        tx, ty, cell = self.part_state["code more"]
        w = 200 * u
        ui.at(max(0, tx + cell - w), ty, w, 0)
        if not ui.popover("code more", w):
            self.part_state["code more"] = None
            ui.popover()
            ws._dirty = True
            return
        got = ui.menu("", [m[1] for m in _CODE_MORE], [m[0] for m in _CODE_MORE])
        ui.popover()
        if got is not None and 0 <= got < len(_CODE_MORE):
            self.part_state["code more"] = None
            ws.code_layer._run_tool(_CODE_MORE[got][2], ws.editor)
            ws._dirty = True

    def _still_tool(self, pid, u):
        """Play over the Run slot's still frame."""
        if self._still is None:
            return
        gx, gy, gw, gh = self._still
        d = 48 * u
        ui.at(gx + (gw - d) // 2, gy + (gh - d) // 2, d, d)
        if ui.tool("play", "Play\tCtrl+Enter", {"id": pid + " play"}):
            self._play_slot()

    def _play_slot(self):
        """The still frame's Play: the paused playtest goes on, else Play."""
        if self.paused:
            self.pause(False)
        else:
            self.ws.defer(self.ws._leave_menu)

    def pause(self, on):
        """The Run slot's pause: the playtest's window hides with its run
        held (`WindowedWM.dock`) and the slot shows its last frame, dimmed
        under Play; off, the run goes on where it stood and takes the keys."""
        ws = self.ws
        on = bool(on) and ws.editor_app.playing()
        if on == self.paused:
            return
        self.paused = on
        if on:
            self._dock()                    # hidden now, its run held
            ws.wm._move_focus("menu")
        else:
            win = ws.wm._wins.get("desktop")
            if win is not None:
                win.held = False            # the restore lands on the run, not its menu
            ws.player.resume()
            if "desktop" in ws.wm._wins:
                ws.wm._move_focus("desktop")
        ws._dirty = True

    def _title(self, c, label):
        ws = self.ws
        if c == "paint" and ws.paint is not None:
            return "Sprite %d" % ws.paint.n
        if c == "code":
            return ws.code_file_name()
        if c == "music":
            return Sound.title(self)
        return label

    def _head_tool(self, op):
        """A tool in a pane's header: the Sprite pane's flip and rotate, the
        Map pane's tools, eraser and zoom."""
        ws = self.ws
        if op.startswith("scene:"):
            su = ws.scene_ui
            se = su.sceneedit
            what = op[6:]
            if what == "zoom":
                su._cycle_zoom()
            elif se is not None:
                if what == "snap":
                    se.snap = not se.snap
                elif what == "delete":
                    se.delete_sel()
                elif what == "back":
                    se.back_sel()
                elif what == "front":
                    se.front_sel()
                elif what == "flip":
                    se.toggle_flip()
                su._sync_live()
                self._owe_restart("scene")
        elif op.startswith("map:"):
            mu = ws.map_ui
            what = op[4:]
            if what == "erase":
                mu.map_erase = not mu.map_erase
            elif what == "zoom":
                mu._map_cycle_zoom()
            else:
                mu.map_tool = what
                mu.map_erase = False
                mu._map_rect = None
                mu._map_sel = None
        else:
            ws.paint_layer._do_tool(op)
        ws.mark_dirty()

    def _tool_on(self, op):
        """Whether a header tool is the one in use (a current mark)."""
        if op == "scene:snap":
            se = self.ws.scene_ui.sceneedit
            return bool(se is not None and se.snap)
        if not op.startswith("map:"):
            return False
        mu = self.ws.map_ui
        what = op[4:]
        if what == "erase":
            return bool(mu.map_erase)
        return what == mu.map_tool and not mu.map_erase

    def _status(self, c):
        """A pane's dim status after its name: the game's scale."""
        if c == "game" and self._scale:
            return "%d\u00d7" % self._scale
        if c == "made":
            return "made by your blocks"
        return ""

    def _dividers(self, cv, th, divs, u):
        d = self._drag
        for path, i, axis, (x, y, w, h) in divs:
            hw, hl = P.HANDLE[0] * u, P.HANDLE[1] * u
            if axis == "h":
                cv.rrect(x + (w - hw) // 2, y + (h - hl) // 2, hw, hl, hw // 2, th["border"])
            else:
                cv.rrect(x + (w - hl) // 2, y + (h - hw) // 2, hl, hw, hw // 2, th["border"])
            if d is not None and d[0] == path and d[1] == i and d[6]:
                at = d[4]
                if axis == "h":
                    cv.rect(at + (w - 2 * u) // 2, y, 2 * u, h, th["value"])
                else:
                    cv.rect(x, at + (h - 2 * u) // 2, w, 2 * u, th["value"])

    def _game(self, cv, th, r, u):
        """The Run slot: the game's frame at the largest whole scale the pane
        holds, centred; a still frame, dimmed under the Play glyph, while no
        game runs."""
        ws = self.ws
        gc = ws.canvas
        x, y, w, h = r
        pad = 4 * u
        s = max(1, min((w - 2 * pad) // gc.w, (h - 2 * pad) // gc.h))
        gw, gh = gc.w * s, gc.h * s
        self._scale = s
        gx = x + (w - gw) // 2
        gy = y + max(0, (h - gh) // 2)
        cv.clip(x, y, w, h)
        fb = getattr(gc, "flush_batch", None)
        if fb is not None:
            fb()
        live = ws.editor_app.playing() and not self.paused
        if live:
            cv.blit565_resize(gc._buf, gc.w, gc.h, gx, gy, gw, gh)
        elif s == 1:
            cv.tint565_at(gc._buf, gc.w, 0, 0, gc.w, gc.h, gx, gy, th["panel"], 140)
        else:
            # The still dimmed at the game's own size, then scaled: the same
            # pixels as the panel's colour over the scaled frame, at a
            # scale's square fewer blends.
            dim = moybuf.alloc(2 * gc.w * gc.h)
            cv.tint565(dim, gc._buf, gc.w, 0, 0, gc.w, gc.h, th["panel"], 140)
            cv.blit565_resize(dim, gc.w, gc.h, gx, gy, gw, gh)
            moybuf.free(dim)
        cv.clip()
        self._still = None if live else (gx, gy, gw, gh)
        self._still_at = (gx, gy, gw, gh)

    def _sheet(self, cv, th, r, u, stamps=False):
        """The Sheet: two pages of the sprite sheet at 2x side by side (a page
        16 x 8 tiles), captioned; the current sprite ringed."""
        ws = self.ws
        sh = ws.sheet
        if sh is None:
            return
        x, y, w, h = r
        cols, prow = 16, 8
        pages = max(1, sh.rows // prow)
        s = 2 * u
        tw = 8 * s
        pw = cols * tw
        cap = 20 * u
        n = max(1, min(2, (w - cap) // (pw + cap)))
        if stamps or self._ref:
            h -= 40 * u                     # the foot's line
        first = min(self._sheet_page, max(0, pages - n))
        top = y + max(0, (h - prow * tw) // 2)
        cur = self._cur_sprite()
        if stamps:
            se = ws.scene_ui.sceneedit
            cur = se.n if se is not None else -1
        self._sheet_rects = []
        px = x + cap
        cv.clip(x, y, w, h)
        for k in range(n):
            pg = first + k
            cv.print(str(pg + 1), px - cap + 6 * u, top, th["ink_dim"], 1)
            cv.rect(px, top, pw, prow * tw, th["surface_alt"])
            cv.sspr(sh, 0, pg * prow * 8, cols * 8, prow * 8, px, top, pw, prow * tw, 0)
            t = cur - pg * prow * cols
            if 0 <= t < prow * cols:
                self._cursors.append((px + (t % cols) * tw, top + (t // cols) * tw, tw, tw))
            self._sheet_rects.append((pg, (px, top, pw, prow * tw)))
            px += pw + cap
        cv.clip()

    def _draw_loupe(self, cv, th, u):
        """The Sheet's loupe (EDITOR_LAYOUT.md): the tile under a held press
        at 3x its 16 px, on a card over the finger, inside the surface."""
        t, px, py = self._loupe
        sh = self.ws.sheet
        if sh is None:
            return
        s = LOUPE * 2 * u
        d = 8 * s
        m = 4 * u
        bw = d + 2 * m
        x = max(0, min(cv.w - bw, px - bw // 2))
        y = py - bw - 20 * u
        if y < 0:
            y = py + 20 * u
        cv.rrect(x, y, bw, bw, 6 * u, th["surface"], th["border"])
        cv.rect(x + m, y + m, d, d, th["surface_alt"])
        ox, oy = sh.tile_origin(t)
        cv.sspr(sh, ox, oy, 8, 8, x + m, y + m, d, d, 0)

    def _cur_sprite(self):
        """The sprite the Sheet and the Reference mark: the Sprite pane's,
        else the last picked."""
        ws = self.ws
        return ws.paint.n if ws.paint is not None else self._picked

    def _pick_line(self, r, u):
        """The Reference's Sprites foot: the call a pick puts at the Code
        pane's caret, and Insert."""
        ws = self.ws
        x, y, w, h = r
        call = "spr(%d, x, y)" % self._cur_sprite()
        row = 30 * u
        ui.at(x + 10 * u, y + h - row - 4 * u, w - 110 * u, row)
        ui.label(call, {"tone": "dim"})
        ui.at(x + w - 96 * u, y + h - row - 4 * u, 88 * u, row)
        if ui.button("Insert", "normal", {"id": "insert call"}) and ws.editor is not None:
            ws.history.code_burst_open()
            ws.editor.insert_text(call)
            ws._dirty = True

    def _sheet_at(self, px, py):
        for pg, (x, y, w, h) in getattr(self, "_sheet_rects", ()):
            if x <= px < x + w and y <= py < y + h:
                tw = w // 16
                return (pg * 8 + (py - y) // tw) * 16 + (px - x) // tw
        return None

    def _pick(self, t):
        """A sprite picked on the Sheet: the Sprite pane's and the map's
        brush, as a tap on the sheet picks today (no undo step)."""
        ws = self.ws
        self._picked = t
        if ws.paint is not None:
            ws.paint.n = t
        mu = ws.built("map_ui")
        if mu is not None and getattr(mu, "mapedit", None) is not None:
            mu.mapedit.n = t
        ws._dirty = True

    # -- input ----------------------------------------------------------------

    def handle_input(self, i):
        ws = self.ws
        ev = getattr(i, "key_event", 0) or 0
        fresh = ev != 0 and ev != self._ev
        self._ev = ev
        self._fresh = ev if fresh else 0
        if fresh and self._chord(ev & 0xFFFF, ev >> _MODS_AT):
            i.clear_edges()
            self._acts += 1
            return True
        if self._pad(i):
            i.clear_edges()
            ws._dirty = True
            self._acts += 1
            return True
        if self.grown is not None and i.pressed("stop") and self.escape():
            self._acts += 1
            return True
        if self._view_wheel(i):
            self._acts += 1
            return True
        if self._menu_open():
            if self._host.keys(i):
                ws._dirty = True
            self._acts += 1
            return True
        got = self._prompt()
        if got is not None:
            # a prompt up takes the keys, whichever pane holds them
            tab, _layer, pane, r = got
            layer = ws.layer_of(tab)
            return layer is not None and bool(
                self._in_pane(tab, self._body(r, self._unit(), pane), layer.handle_input, i))
        pane, r, _p = self.focused()
        c = self.tab_of(pane)
        tab = P.CONTENTS[c][2]
        if c == "music":
            if Sound.keys(self, i, self._fresh):
                i.spend_edges()                 # a held arrow keeps its repeats
            elif self._host.keys(i):
                ws._dirty = True
            return True
        if tab is not None:
            layer = ws.layer_of(tab)
            if layer is not None:
                if self.alone() is not None:
                    r = self._area()
                got = bool(self._in_pane(tab, self._body(r, self._unit()),
                                         layer.handle_input, i))
                if i.last_key or self._fresh or i.any_pressed():
                    self._owe_restart(tab)     # only a key edits: the check reads the doc
                return got
        if c in ("sheet", "sprites"):
            return self._sheet_keys(i)
        if c in Parts.PARTS:
            if Parts.keys(self, c, i):
                i.spend_edges()
            elif self._host.keys(i):
                ws._dirty = True
            if c == "actor":
                self._owe_restart("scene")
            return True
        return False

    def dock_keys(self, i):
        """The Editor's chords while the docked game holds the keys
        (`WindowedWM._route_key`): Ctrl+Enter, Ctrl+., the workspaces, F6."""
        ev = getattr(i, "key_event", 0) or 0
        fresh = ev != 0 and ev != self._ev
        self._ev = ev
        mods = ev >> _MODS_AT
        if not fresh or not (mods & _CTRL or (ev & 0xFFFF) == _F6):
            return False
        if (ev & 0xFFFF) == _F6:
            self.ws.wm._move_focus("menu")
        if self._chord(ev & 0xFFFF, mods):
            i.clear_edges()
            return True
        return False

    def _origin(self):
        """Where the Editor's surface sits on the glass: its window's
        content rect's corner on the desk."""
        wins = getattr(self.ws.wm, "_wins", None)
        win = wins.get("menu") if wins else None
        if win is None:
            return 0, 0
        r = win.content_rect()
        return r[0], r[1]

    def _view_wheel(self, i):
        """The wheel and the middle button over the Map and the Scene
        (EDITOR_LAYOUT.md's pointing table): the wheel zooms in whole steps
        about the pointer, Shift+wheel and the wheel across pan, a middle
        drag pans. True where one acted."""
        ws = self.ws
        wy = getattr(i, "wheel_y", 0) or 0
        wx = getattr(i, "wheel_x", 0) or 0
        mid = bool((getattr(i, "pointer_buttons", 0) or 0) & _PB_MIDDLE)
        p = ws.pointer
        if not (wx or wy or mid or self._mid is not None):
            return False
        ox, oy = self._origin()
        px, py = p.x - ox, p.y - oy
        hit = None
        for _n, pane, r in self._shown_panes():
            if r[0] <= px < r[0] + r[2] and r[1] <= py < r[1] + r[3]:
                hit = (pane, r)
        if not mid:
            self._mid = None
        if hit is None:
            return False
        tab = P.CONTENTS[self.tab_of(hit[0])][2]
        if tab not in ("map", "scene"):
            if tab is None and self.tab_of(hit[0]) in ("sheet", "sprites") and wy:
                self._sheet_page = max(0, min(3, self._sheet_page + (1 if wy > 0 else -1)))
                ws._dirty = True
                return True
            if tab is None and wy and self.tab_of(hit[0]) == "made":
                # the made code's text view takes the wheel where it rests
                self._host.point(px, py, False)
                if self._host.wheel(0, wy):
                    ws._dirty = True
                return True
            if tab is None and wy:
                return Parts.scroll(self, self.tab_of(hit[0]), 1 if wy > 0 else -1)
            return False
        body = self._body(hit[1], self._unit())
        lx, ly = px - body[0], py - body[1]
        view = ws.map_ui if tab == "map" else ws.scene_ui
        shift = (getattr(i, "mods", 0) or 0) & _SHIFT
        if mid:
            last = self._mid
            self._mid = (p.x, p.y)
            if last is not None and (p.x, p.y) != last:
                self._in_pane(tab, body, view.drag_pan, p.x - last[0], p.y - last[1])
                ws._dirty = True
            return True
        if shift and wy:
            wx, wy = wy, 0
        if wx:
            self._in_pane(tab, body, view.drag_pan, -wx * 24 * self._unit(), 0)
        if wy:
            self._in_pane(tab, body, view.wheel_zoom, lx, ly, -1 if wy < 0 else 1)
        ws._dirty = True
        return True

    def _part_focused(self):
        """Whether the focused pane is one drawn on the verbs, whose stops
        take the keys."""
        if self._menu_open():
            return True
        c = self.tab_of(self.focused()[0])
        return c in Parts.PARTS or c in _ON_VERBS

    def shows(self, content):
        """Whether a pane on show holds `content` as its open tab."""
        return any(self.tab_of(p) == content for _n, p, _r in self._shown_panes())

    def header_rect(self, content):
        """The header of the pane showing `content`, or None."""
        for _n, p, r in self._shown_panes():
            if self.tab_of(p) == content:
                return (r[0], r[1], r[2], P.HEADER * self._unit())
        return None

    def _owe_restart(self, tab):
        """E-02 (`EditorApp.owe_restart`): a map, scene or code edit made in
        a pane waits for the next Play. Sprite, sound and flag edits reach
        the running game at once (the sheet, bank and flags are shared)."""
        app = self.ws.editor_app
        if tab in ("map", "scene", "code") and not app._tab_is_clean(tab):
            app.owe_restart(tab)

    def escape(self):
        """Esc: a grown pane goes back (it never leaves full screen, and the
        Code pane keeps it for its popovers). True where it acted."""
        if self.grown is None or self.tab_of(self.focused()[0]) == "code":
            return False
        self.grow()
        return True

    def _sheet_keys(self, i):
        ws = self.ws
        if ws.sheet is None:
            return False
        dx = i.steps("right") - i.steps("left")
        dy = i.steps("down") - i.steps("up")
        if not (dx or dy):
            return False
        n = max(0, min(ws.sheet.cols * ws.sheet.rows - 1, self._cur_sprite() + dx + dy * 16))
        self._pick(n)
        self._sheet_page = min(self._sheet_page, n // 128)
        if n // 128 > self._sheet_page + 1:
            self._sheet_page = n // 128 - 1
        return True

    def _chord(self, code, mods):
        """The workspace keys (EDITOR_LAYOUT.md's input table): True where
        one acted."""
        ws = self.ws
        app = ws.editor_app
        ctrl = mods & _CTRL
        if ctrl and mods & _ALT and code in (_LEFT, _RIGHT, _UP, _DOWN):
            return self._nudge(code)
        if ctrl and not mods & (_ALT | _SHIFT) and ord("1") <= code <= ord("5"):
            return self.switch(P.KEYS[code - ord("1")]) or True
        if ctrl and code == 9:                          # Ctrl+Tab, Ctrl+Shift+Tab
            self.step(-1 if mods & _SHIFT else 1)
            return True
        if ctrl and code in (ord("]"), ord("[")):       # the browser's Ctrl+Tab
            self.step(1 if code == ord("]") else -1)
            return True
        if code == _F6 and not ctrl:
            self.focus_pane(self._focus.get(self.key, 0) + (-1 if mods & _SHIFT else 1))
            return True
        if ctrl and code in _ENTERS:
            if mods & _SHIFT:
                self.grow(self.focused()[0])
            elif app.playing():
                ws.defer(app.restart)
            else:
                ws.defer(ws._leave_menu)
            return True
        if ctrl and code == ord("."):
            if app.playing():
                ws.wm.close_player()
            return True
        if ctrl and code == ord("0"):
            self.arrangement().reset()
            self._placed = None
            self._remember()
            ws._dirty = True
            return True
        return False

    def _pad(self, i):
        """The pad's rows (EDITOR_LAYOUT.md's input table): Select and the
        d-pad move the keys to the pane that way, Select+A grows the focused
        pane. True where one acted."""
        if not i.held("select"):
            return False
        if i.pressed("a"):
            self.grow(self.focused()[0])
            return True
        for name, dx, dy in _PAD_DIRS:
            if i.pressed(name):
                self._focus_toward(dx, dy)
                return True
        return False

    def _focus_toward(self, dx, dy):
        """The keys to the nearest pane whose centre lies the way (dx, dy)
        from the focused one's, nearest along the way first."""
        shown = self._shown_panes()
        fi = self._focus.get(self.key, 0)
        cur = None
        for n, _p, r in shown:
            if n == fi:
                cur = r
        if cur is None:
            return
        cx, cy = cur[0] + cur[2] // 2, cur[1] + cur[3] // 2
        best = None
        for n, _p, r in shown:
            px, py = r[0] + r[2] // 2 - cx, r[1] + r[3] // 2 - cy
            along = px * dx + py * dy
            if n == fi or along <= 0:
                continue
            k = along + 2 * abs(px * dy + py * dx)
            if best is None or k < best[0]:
                best = (k, n)
        if best is not None:
            self.focus_pane(best[1])

    def select_start(self):
        """Select+Start (the pad's Play): Play, or Restart while the
        playtest runs, where the keys are the Editor's or its docked game's.
        True where it acted, so Start does not also open the system menu."""
        ws = self.ws
        if not self.active():
            return False
        holder = ws.wm.input_holder()
        win = ws.wm._wins.get("desktop")
        if holder != "menu" and not (holder == "desktop" and win is not None and win.docked):
            return False
        app = ws.editor_app
        ws.defer(app.restart if app.playing() else ws._leave_menu)
        return True

    def _nudge(self, code):
        """Ctrl+Alt+arrows: the focused pane's right (left and right) or
        bottom (up and down) divider moves 8 px."""
        arr = self.arrangement()
        pane, _r, path = self.focused()
        axis = "h" if code in (_LEFT, _RIGHT) else "v"
        step = (P.KEY_STEP * self._unit()) * (-1 if code in (_LEFT, _UP) else 1)
        for p, i, a in arr.edges(path):
            if a == axis:
                arr.drag(p, i, arr.sizes[p][i] + step)
                self._placed = None
                self._remember()
                self.ws._dirty = True
        return True

    def handle_pointer(self, px, py, click):
        self._in_code = False
        got = self._pointer(px, py, click)
        if (click or self.ws.pointer.down) and not self._in_code:
            self._acts += 1             # a press no pane's own frames hold
        return got

    def _pointer(self, px, py, click):
        ws = self.ws
        down = ws.pointer.down
        if self._drag is not None:
            return self._divider_drag(px, py, down)
        lay = ws.layout
        if py < lay.cy:
            if click:
                return bool(ws.bar_layer.handle_bar_tap("menu", px, py))
            return False
        if click:
            d = self._divider_at(px, py)
            if d is not None:
                path, i, axis, rect = d
                at = rect[0] if axis == "h" else rect[1]
                now = _ticks_ms()
                if self._double(("div", path, i), now):
                    self.arrangement().reset(path, i)
                    self._placed = None
                    self._remember()
                    ws._dirty = True
                    return True
                self._drag = [path, i, axis, px if axis == "h" else py, at,
                              self.arrangement().sizes[path][i],
                              bool(getattr(ws.pointer, "hovers", False)), -1]
                return True
            self._press = None
            for n, pane, r in self._shown_panes():
                if r[0] <= px < r[0] + r[2] and r[1] <= py < r[1] + r[3]:
                    self._press = (n, pane, r)
                    if n != self._focus.get(self.key, 0):
                        self.focus_pane(n)
                    break
        if self._menu_open():
            if self._host.point(px, py, click, down):
                ws._dirty = True
            return True
        p = self._press
        if p is None:
            return False
        n, pane, r = p
        u = self._unit()
        head = 0 if self._headless(pane) else P.HEADER * u
        if click and py < r[1] + head and px < self._tools_x.get(n, r[0] + r[2]):
            if self._double(("head", pane), _ticks_ms()):
                self.grow(pane)
                return True
        host = self._host
        if py < r[1] + head or click is False and host.hold:
            if host.point(px, py, click, down):
                ws._dirty = True
            return True
        c = self.tab_of(pane)
        body = self._body(r, u, pane)
        tab = P.CONTENTS[c][2]
        if c == "music":
            if Sound.pointer(self, body, u, px, py, click, down) \
                    or host.point(px, py, click, down):
                ws._dirty = True
            self._in_code = True
            return True
        if tab is not None:
            layer = ws.layer_of(tab)
            if layer is None:
                return False
            got = bool(self._in_pane(tab, body, layer.handle_pointer,
                                     px - body[0], py - body[1], click))
            self._in_code = c in _OWN_FRAMES
            if click or tab != "code":
                self._owe_restart(tab)         # Code's pointer edits are taps (its keys)
            if tab == "scene" and self._blocks():
                ws.block_ui._sync_target_from_scene()   # its scripts, in the Blocks pane
            return got
        if c == "game":
            if click and (self.paused or not ws.editor_app.playing()):
                self._play_slot()
            return True
        if c in ("sprites", "stamps") and py >= body[1] + body[3] - 40 * u:
            if host.point(px, py, click, down):     # the foot's line (Insert)
                ws._dirty = True
            return True
        if c in ("sheet", "sprites", "stamps"):
            t = self._sheet_at(px, py)
            if t is not None and (click or down):
                if c == "stamps":
                    se = ws.scene_ui.sceneedit
                    if se is not None:
                        se.set_brush(t)
                        ws._dirty = True
                else:
                    self._pick(t)           # a held slide previews each it passes
            loupe = (t, px, py) if t is not None and down else None
            if loupe != self._loupe:
                self._loupe = loupe
                ws._dirty = True
            return True
        if c in Parts.PARTS:
            if Parts.pointer(self, c, body, u, px, py, click, down) \
                    or host.point(px, py, click, down):
                ws._dirty = True
            if c == "actor":
                self._owe_restart("scene")
            return True
        return True

    def _shown_panes(self):
        alone = self.alone()
        if alone is not None:
            for n, (pane, _r, _p) in enumerate(self.panes()):
                if pane == alone:
                    return [(n, pane, self._area())]
        return [(n, pane, r) for n, (pane, r, _p) in enumerate(self.panes())]

    def _double(self, target, now):
        was, t = self._last
        self._last = (target, now)
        if was == target and _ticks_diff(now, t) < _DOUBLE_MS:
            self._last = (None, 0)
            return True
        return False

    def _divider_at(self, px, py):
        """The divider under a press: anywhere on its gutter for a pointer,
        its handle's 16 x 48 for a finger."""
        if self.alone() is not None:
            return None
        u = self._unit()
        mouse = bool(getattr(self.ws.pointer, "hovers", False))
        for path, i, axis, (x, y, w, h) in self.placed()[1]:
            if mouse:
                if x <= px < x + w and y <= py < y + h:
                    return (path, i, axis, (x, y, w, h))
                continue
            hw, hl = P.HANDLE_HIT[0] * u, P.HANDLE_HIT[1] * u
            if axis == "h":
                r = (x + (w - hw) // 2, y + (h - hl) // 2, hw, hl)
            else:
                r = (x + (w - hl) // 2, y + (h - hw) // 2, hl, hw)
            if r[0] <= px < r[0] + r[2] and r[1] <= py < r[1] + r[3]:
                return (path, i, axis, (x, y, w, h))
        return None

    def _divider_drag(self, px, py, down):
        """A divider held: only its line moves while held; on release the
        two panes beside it lay out. A finger's drag takes after 8 px."""
        ws = self.ws
        d = self._drag
        path, i, axis, start, at, base, live, _stop = d
        arr = self.arrangement()
        p = px if axis == "h" else py
        moved = p - start
        if not live and abs(moved) >= P.DRAG_SLOP * self._unit():
            d[6] = live = True
        lo, hi = arr.limits(path, i)
        size = max(lo, min(hi, base + moved))
        rect = arr.child_rect(path, i)
        origin = rect[0] if axis == "h" else rect[1]
        d[4] = origin + size
        stop = 0 if base + moved <= lo else 1 if base + moved >= hi else -1
        d[7] = stop
        self._flash = None
        if stop >= 0 and live:
            node = arr.node(path)
            child = node[1][i + stop]
            pane = self._first_pane(child)
            label = P.CONTENTS[pane[0]][0]
            mw, mh = P.pane_min(pane, self._unit())
            self._flash = (pane, "%s stops at its minimum, %d %s" % (
                label, mw if axis == "h" else mh, "wide" if axis == "h" else "tall"))
        ws._dirty = True
        if not down:
            self._drag = None
            self._flash = None
            if live:
                arr.drag(path, i, size)
                self._placed = None
                self._remember()
        return True

    @staticmethod
    def _first_pane(node):
        while not P.is_pane(node):
            node = node[1][0]
        return node

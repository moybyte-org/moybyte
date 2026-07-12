"""The EDITOR app (Stage 3 of docs/shell_ux_technical_plan_v1.md).

`EditorApp` is the console's authoring app: ONE app, opened on a `Project`, whose
tabs ARE the already-extracted editor layers -- the view ladder ordered gentlest
-> deepest (spec shell_ux_v1.md Section 6):

    Config -> Blocks -> Code -> Sprites -> Map -> Music        [ PLAY ]

What EditorApp owns (moved verbatim out of Workstation): the current-tab state
(`EditorApp.tab`, the new single source of truth for the active view), the lazy
tab builders (the body of the old `set_menu_view` -- the CodeEditor/PaintEditor/
map/blocks/music builds), the per-tab landing entry points (`open`/`open_paint`/
`open_map`/`open_blocks`/`open_music`, the old `_open_*`), and the PLAY trigger
(`leave`, the old `_leave_menu`): commit the tab then run the cart.

`menu_view` is NOT deleted -- it becomes a projection. `EditorApp.tab` is the
source of truth for the active view; `Workstation.menu_view` stays the string-keyed
router's routing key as a forwarding shim over `EditorApp.tab` (the same trick
`ws.sheet` got in Stage 1), so the router keeps working unmodified. Stage 6 made the
WM back-stack the source of truth for `screen` and retired every PRODUCTION reader/
writer of the `screen` string (they ask the stack / call wm.goto now); `menu_view`
and `screen` are kept as faithful tested-surface projections (plan Section 6 keeps
these shims deliberately -- the future OS-arch capability track is the removal list).

Boundary (Section 1.2): this app switches the tabs' DATA access onto the injected
`Project`, NOT the draw toolkit. The shared UI plumbing (`_glyph`, layouts, pointer,
`_set_text_mode`, the achievements tracker, the editor layer instances) stays reached
through the `ws` back-reference the plan keeps for Stages 2-6; only the tabs' data
reach-through moves to `Project`. The tab layer INSTANCES (code_layer/paint_layer/
map_ui/block_ui/music_ui/cards_layer) still live on `ws` and stay the router's
content layers; EditorApp coordinates the tab state + builds their editor cores.

Canonical home is runtime/; build.sh stages a copy into the firmware modules/ tree
so the device freezes it (same pattern as project.py/player.py). It stays a leaf --
NAMES/_in aren't needed (it reaches the toolkit through `ws`), and CodeEditor/
PaintEditor are imported from the shared editor cores (the same bare-or-package
fallback console.py uses: bare names on the device / once host_app has aliased them,
`runtime.X` when a test loads this module directly).
"""

try:
    from editors import CodeEditor, PaintEditor
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.editors import CodeEditor, PaintEditor

try:
    from bar_layer import _BAR_ICON, _BAR_GAP, _ZONE_LEFT_GAME
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.bar_layer import _BAR_ICON, _BAR_GAP, _ZONE_LEFT_GAME


# The Editor's lent left zone (Stage 4 of docs/shell_ux_technical_plan_v1.md, #46
# zoned bar): PROJECTS (back to the picker) + the tab ladder + PLAY + SAVE, in the
# spec Section 6 order (Projects -> Config -> Blocks -> Code -> Sprites -> Map -> Music
# -> PLAY -> SAVE), rendered as icons inside the rect the bar lends it. Each entry is
# (tab_name_or_None_or_ACTION, icon_kind); `tab_name` is what EditorApp.tab equals when
# that icon's destination is showing (so draw_zone can highlight it). PROJECTS
# (_ZONE_PROJECTS -> open_picker, edit another project), PLAY (None) and SAVE
# (_ZONE_SAVE) are ACTIONS, not destinations, so they're never highlighted -- SAVE
# dispatches to the active tab's persist verb (save_current), the ONE compact save
# affordance the unified bar carries so every editor BODY stays chrome-free (fits 320px:
# 9 icons * 18px = 162px inside the ~202px lent zone). This is what makes the bar
# identical across all six tabs -- code/blocks/music no longer need their own SAVE/RUN/CLOSE.
_ZONE_SAVE = "\x00save"          # sentinel: the SAVE action icon (never a real tab)
_ZONE_PROJECTS = "\x00projects"  # sentinel: back to the project-picker (never a real tab)
_ZONE_TABS = (
    (_ZONE_PROJECTS, "projects"),  # <- back to the PROJECT-PICKER (edit another project)
    ("cards", "edit"),
    ("blocks", "blocks"),
    ("code", "code"),
    ("paint", "paint"),
    ("map", "map"),
    ("music", "music"),
    (None, "run"),          # PLAY
    (_ZONE_SAVE, "save"),   # SAVE -> save_current() (persist the active tab)
)
_ZONE_STRIDE = _BAR_ICON + _BAR_GAP

# The SHELF-density zone (visual identity v1 Phase 3, the Studio mockup): the six
# tabs as LABELED chips (icon + name) via ui.tab_row, PROJECTS as an icon chip on
# the left, PLAY/SAVE as labeled buttons on the right. The 320x240 baseline keeps
# the frozen 9-icon ladder above, byte-identical.
_TAB_CHIPS = (
    ("cards", "CONFIG", "edit"),
    ("blocks", "BLOCKS", "blocks"),
    ("code", "CODE", "code"),
    ("paint", "SPRITES", "paint"),
    ("map", "MAP", "map"),
    ("music", "MUSIC", "music"),
)

try:
    import ui as _ui
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui as _ui
_in = _ui.rect_in   # one hit-test (ui.rect_in)


class EditorApp:
    """The authoring app: a project opened across a tab ladder + PLAY. Holds a `ws`
    back-ref (the shared draw toolkit + services seam every surface uses) and the
    injected `Project`. `tab` is the source of truth for the active view; Workstation
    projects `menu_view` onto it. The methods are the old `set_menu_view`/`_open_*`/
    `_leave_menu` bodies, moved verbatim with `self.` data reads left reaching `ws`."""

    def __init__(self, ws, names=None, in_rect=None):
        self.ws = ws
        self._NAMES = names
        self._in = in_rect if in_rect is not None else _in
        self.project = None           # the open cart's workspace (set by open())
        # Stage 4 (#46 zoned bar): bumped whenever the active tab ACTUALLY changes --
        # the ONLY thing that varies in the Editor's lent left zone (which icon is
        # highlighted). BarLayer folds this into its cache key so the zoned strip
        # re-renders on a real tab switch and never per frame. Set BEFORE `self.tab`
        # below, since the `tab` setter reads it (mirrors Launcher.__init__'s
        # zone_gen-before-sel ordering).
        self.zone_gen = 0
        self.tab = "cards"            # active view -- ws.menu_view projects onto this:
                                      # "cards" | "code" | "paint" | "map" | "blocks"
                                      # | "music" | "theme" (theme = the EDIT-ICONS
                                      # reuse of the paint renderer, set via the
                                      # menu_view setter, not a cart-editor tab)

    # -- the active tab (a self-defending property, Stage 4 review fix) -----------
    #
    # `tab` bumps zone_gen on every ACTUAL change, regardless of the write site --
    # set_tab, the ws.menu_view setter (ThemeLayer sets "theme"), _open_workspace all
    # assign `.tab`/`ws.menu_view` DIRECTLY -- so BarLayer's strip-cache key can trust
    # zone_gen instead of any writer remembering to bump it (mirrors Launcher.sel).
    # Idempotent writes (set_tab called with the current tab) don't bump, so the zoned
    # strip never re-renders on a no-op tab switch.
    @property
    def tab(self):
        return self._tab

    @tab.setter
    def tab(self, value):
        if value != getattr(self, "_tab", None):
            self.zone_gen += 1
            # Stage 6c: a real tab switch keeps screen == "menu" but resolves
            # _content_layer to a different tab, so tell the WM its memoized layer stack
            # is stale (the content layer identity would catch it too, but signalling
            # here keeps every content-change on the WM -- the memo's single owner).
            wm = getattr(self.ws, "wm", None)
            if wm is not None:
                wm.note_content_change()
        self._tab = value

    # -- open the editor on a project (spec Section 4/Section 6: Config-first) -----

    def open(self, project):
        """Open the Editor on `project`, landing on the Config tab (spec Section 6):
        the "Make it mine" cards when the cart exposes an edit schema, else the code
        editor (there are no cards to show). The old Workstation._open_menu."""
        self.project = project
        ws = self.ws
        ws.wm.goto("menu")       # Stage 6e: spawn/return the Editor on the back-stack
        ws.set_menu_view("cards" if ws.cart.get("edit") else "code")

    def open_paint(self):
        ws = self.ws
        ws.wm.goto("menu")       # Stage 6e: spawn/return the Editor on the back-stack
        ws._editing_icons = False        # a CART sheet, not the system theme
        ws.paint_status = None
        ws.set_menu_view("paint")

    def open_map(self):
        ws = self.ws
        ws.wm.goto("menu")       # Stage 6e: spawn/return the Editor on the back-stack
        ws.save_status = None
        ws.map_ui.on_open()          # fresh gesture/zoom state (#37)
        ws.set_menu_view("map")
        # Open with the camera at the top-left so the whole map shows at the default
        # (fit-both) zoom with zero panning. set_menu_view builds the MapEditor.
        if ws.map_ui.mapedit is not None:
            ws.map_ui.mapedit.cam_x = 0
            ws.map_ui.mapedit.cam_y = 0

    def open_blocks(self):
        ws = self.ws
        ws.wm.goto("menu")       # Stage 6e: spawn/return the Editor on the back-stack
        # NB: don't pre-clear blk_status here -- set_menu_view("blocks") sets the
        # "CODE LOCKED" notice when it builds the editor in protected mode, and
        # clearing it after would hide the data-loss guard's message.
        ws.set_menu_view("blocks")

    def open_music(self):
        """Open the music/sound editor (#50): a tracker-style step grid over the
        cart's AudioBank. Mirrors open_map -- reset preview state, then build the
        editor via set_menu_view("music")."""
        ws = self.ws
        ws.wm.goto("menu")       # Stage 6e: spawn/return the Editor on the back-stack
        ws.save_status = None
        ws.music_ui._stop_music_preview()
        ws.set_menu_view("music")

    # -- tab builders (the old set_menu_view body, moved verbatim) -----------------

    def set_tab(self, view):
        """Switch the active tab, building the matching editor and toggling the
        keyboard between game (raw) and text (ASCII) modes. `EditorApp.tab` is the
        source of truth; ws.menu_view projects onto it."""
        ws = self.ws
        ws._dirty = True             # sub-view change always repaints (#44)
        self.tab = view              # the `tab` setter bumps zone_gen on a real change
                                     # (Stage 4, #46: the lent zone's highlight moved)
        if view == "code":
            if ws.editor is None and ws.cart is not None:
                ws.editor = CodeEditor(ws.cart["src"],
                                       cols=ws.code_layout.cols,
                                       rows=ws.code_layout.rows)
                ws.code_layer.reset()   # fresh keyboard-edge tracker for the new editor
                if ws.crash_line is not None:
                    # Opened after a runtime crash -> land on the line that raised.
                    ws._mark_code_error(ws.crash_line - 1,
                                        (ws.cart_error or "crashed")[:32])
                else:
                    ws.code_err = None
                    ws.code_err_row = None
        elif view == "paint":
            if ws.paint is None and ws.sheet is not None:
                ws.paint = PaintEditor(ws.sheet)
        elif view == "map":
            # Mirror the paint branch: build the MapEditor over the cart's TileMap
            # + sheet (both always exist after open()). Edits go straight into the
            # live tilemap, so a running cart picks them up via tilemap.gen (#32).
            ws.map_ui.build()
        elif view == "blocks":
            # Build the BlockEditor over the cart's block program (#29), lazily --
            # see BlockEditorUI.build's docstring (block_editor_ui.py) for the
            # load-or-fresh + data-loss-guard rules (moved verbatim from here).
            ws.block_ui.build()
        elif view == "music":
            # Build the MusicEditor over the open cart's live AudioBank (#50): the
            # SAME bank the running cart plays through, so an edit is heard immediately
            # by the preview AND by the cart on resume. Edits go straight into that
            # bank; SAVE persists it to sounds.json.
            ws.music_ui.build()
        ws._set_text_mode(view == "code")
        # Achievements (#21): visiting each editor (code/paint/map) earns "Toolbox
        # Master". "cards" isn't an editor, so it's ignored by note().
        ws.ach.note("editor", view)

    # -- PLAY: commit the tab, then run the cart (spec Section 2/Section 6) ---------

    def leave(self):
        """PLAY (spec Section 6): leave the current tab by RUNNING the cart. Commits/
        applies the tab's freshest edit, (re)starts the cart, and hands it to the
        Player -- recording SELF (the Editor) as the run caller (Stage 3b), so the
        cart's exit (pause QUIT here, hold-BACKSPACE from Stage 5) returns to the
        Editor on THIS tab (spec Section 2's launch-and-return), not to the home root.
        This is the deliberate navigation-semantics change: today's implicit run-on-
        close returned to the launcher; PLAY returns to the Editor."""
        ws = self.ws
        ws._dirty = True             # back to the desktop repaints (#44)
        ws._set_text_mode(False)
        if self.tab == "music":
            ws.music_ui._stop_music_preview()   # don't let a preview leak into the cart resume
        # Returning to the desktop from the code editor must run whatever source is
        # in the editor now (the kid may have fixed a crash and hit SAVE, or just
        # edited and closed). Re-_start() with the editor text so the FIXED cart
        # actually runs -- otherwise a previously-set cart_error would re-paint the
        # stale "crashed" panel and _update/_draw would stay None forever.
        if self.tab == "code" and ws.editor is not None and ws.cart is not None:
            ws.cart["src"] = ws.editor.text()
            ws._start()
        elif self.tab == "blocks":
            ws.block_ui.on_leave()
            # A saved block edit already recompiled cart["src"] (save_blocks); re-run
            # it so leaving the outline runs the freshest program, just like the code
            # editor does. (Unsaved edits don't touch src, so this re-runs the last
            # saved version -- the kid SAVEs to keep changes, exactly like code.)
            if ws.cart is not None:
                ws._start()
        elif self.tab == "cards":
            # PLAY on Config = the old GO (fix B retired that button): re-run the cart
            # with the freshly-tuned config AND persist config.json. Config binds at
            # _start (make_api), so a re-start is what makes card edits take effect;
            # _save_config commits them (mirrors ws.apply()). Only persist on a clean
            # start so a crash doesn't overwrite config with a half-applied edit.
            if ws.cart is not None and ws._start():
                ws._save_config()
        ws.run(ws.project, self)     # PLAY: run the cart, caller = the Editor (Stage 3b)

    # -- the lent left zone (Stage 4, #46 zoned bar) --------------------------
    #
    # The Editor's tab ladder + PLAY + SAVE, shown on EVERY tab now (Stage-4 rollout):
    # cards_layer.py, paint_layer.py, layers.py's _MapLayer/_MusicLayer, code_layer.py
    # and block_editor_ui.py each call ws.bar_layer._draw_status_strip("menu") from
    # their draw() (+ ws.bar_layer.handle_bar_tap("menu", ...) from handle_pointer), so
    # the bar is identical across all six Editor tabs and each tab's own RUN/CLOSE
    # chrome was dissolved into it (SAVE routes through save_current above).

    def draw_zone(self, cv, rect):
        """Draw the tab ladder + PLAY + SAVE inside the rect the bar lent us,
        highlighting the active tab. `cv` may be the bar's offscreen cache strip
        (#43) -- this draws the SAME pixels either way, which is what makes the
        cached strip pixel-identical to a direct render.

        The icon side + stride derive from the lent rect's HEIGHT (16*fs -- the
        bar hands over an icon-high rect), so the ladder scales with the system
        font (#39): at fs=1 this is byte-identical to the frozen 16px/18px
        constants, at fs=2+ the icons no longer overlap."""
        ws = self.ws
        NAMES = self._NAMES
        if not ws.layout._base:
            # SHELF density (visual identity v1 Phase 3): labeled tabs + labeled
            # PLAY/SAVE buttons -- the Studio mockup's tab row, on the zoned bar.
            th = ws.theme_colors
            proj, tabs_area, save_r, play_r = self._zone_parts(rect)
            band_ink = th["ink"] if ws.bar_layer.zone_band_light("menu") else None
            _ui.button(cv, th, proj, "", glyph="projects", kind="normal")
            _ui.tab_row(cv, th, tabs_area, _TAB_CHIPS, self.tab,
                        icon_for=getattr(ws, "_icon_image_keyed", None),
                        ink=band_ink)
            _ui.button(cv, th, save_r, "SAVE", kind="normal")
            _ui.button(cv, th, play_r, "PLAY", kind="play", glyph="run")
            return
        x0, y0, w, h = rect
        ic = h if h > 0 else _BAR_ICON      # icon side (16*fs)
        stride = ic + (ic // 8)             # _BAR_GAP (2) scaled: 2*fs == ic//8
        for i, (tab, glyph) in enumerate(_ZONE_TABS):
            x = x0 + i * stride
            if x + ic > x0 + w:
                break                       # ran out of lent width -- draw what fits
            if tab is not None and tab == self.tab:
                cv.rect(x, y0, ic, ic, NAMES["indigo"])
            ws._icon(glyph, x, y0, cv)

    def _zone_parts(self, rect):
        """PURE shelf-zone geometry (shared by draw_zone and zone_tap so a strip-
        cached draw and a later tap can't desync): PROJECTS chip | labeled tab row
        | SAVE | PLAY, the buttons right-aligned. Scales off the lent rect's
        height (16*fs, like the frozen ladder)."""
        fs = max(1, rect[3] // 16)
        gap = 4 * fs
        proj, rest = _ui.cut_left(rect, 22 * fs)
        play_r, rest = _ui.cut_right(rest, 54 * fs)
        _pad, rest = _ui.cut_right(rest, gap)
        save_r, rest = _ui.cut_right(rest, 46 * fs)
        _pad, rest = _ui.cut_right(rest, gap)
        tabs_area = (rest[0] + gap, rest[1], max(0, rest[2] - 2 * gap), rest[3])
        return proj, tabs_area, save_r, play_r

    def zone_tap(self, px, py, rect=None):
        """Hit-test the tab ladder + PLAY + SAVE and dispatch. `rect` is the lent
        left-zone rect BarLayer drew into -- the fixed game-canvas _ZONE_LEFT_GAME
        for a game-canvas tab, or the responsive layout.zone_left for the
        system-canvas tabs (both hit-test in the same coord space the bar drew
        in). Defaults to _ZONE_LEFT_GAME so a bare call is unchanged. The icon
        side + stride derive from the rect height, matching draw_zone; the shelf
        tiers resolve against the SAME _zone_parts geometry the draw used."""
        if rect is not None and not self.ws.layout._base:
            proj, tabs_area, save_r, play_r = self._zone_parts(rect)
            if self._in(px, py, proj):
                return self._activate_zone_tab(_ZONE_PROJECTS)
            if self._in(px, py, play_r):
                return self._activate_zone_tab(None)
            if self._in(px, py, save_r):
                return self._activate_zone_tab(_ZONE_SAVE)
            fs = max(1, rect[3] // 16)
            slim = [(tid, label) for tid, label, _ic in _TAB_CHIPS]
            for tid, r, _labels_on in _ui.tab_row_rects(tabs_area, slim, fs):
                if self._in(px, py, r):
                    return self._activate_zone_tab(tid)
            return False
        x0, y0, w, h = rect if rect is not None else _ZONE_LEFT_GAME
        ic = h if h > 0 else _BAR_ICON
        stride = ic + (ic // 8)
        for i, (tab, _glyph) in enumerate(_ZONE_TABS):
            x = x0 + i * stride
            if x + ic > x0 + w:
                break
            if self._in(px, py, (x, y0, ic, ic)):
                return self._activate_zone_tab(tab)
        return False

    def _activate_zone_tab(self, tab):
        ws = self.ws
        if tab == _ZONE_PROJECTS:   # <- back to the PROJECT-PICKER (edit another project)
            ws.open_picker()
        elif tab == "cards":
            ws._open_menu()
        elif tab == "code":
            ws.set_menu_view("code")
        elif tab == "paint":
            ws._open_paint()
        elif tab == "map":
            ws._open_map()
        elif tab == "blocks":
            ws._open_blocks()
        elif tab == "music":
            ws._open_music()
        elif tab == _ZONE_SAVE:   # SAVE: persist the active tab (bar's one save affordance)
            self.save_current()
        else:                     # PLAY (tab is None)
            ws._leave_menu()
        return True

    def save_current(self):
        """Persist the ACTIVE tab -- the SAVE bar icon's dispatch (spec Section 7's
        commit; auto-save is a later stage). Each tab keeps its own persist verb; the
        bar just routes to whichever tab is up, so every editor BODY drops its SAVE
        button. Config persists via commit_config (no re-run -- PLAY runs). The theme
        (EDIT ICONS) tab has no bar, so it's never routed here; paint/map keep their
        own body SAVE too (the theme reuse pins paint's), so a bar SAVE on those tabs
        is a harmless second route to the same verb."""
        ws = self.ws
        tab = self.tab
        if tab == "code":
            ws.save_code()
        elif tab == "paint":
            ws.save_sprites()
        elif tab == "map":
            ws.save_map()
        elif tab == "music":
            ws.save_sounds()
        elif tab == "blocks":
            ws.block_ui.save_blocks()
        elif tab == "cards":
            ws._save_config()

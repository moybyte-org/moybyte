"""The EDITOR app (Stage 3 of docs/history/shell_ux_technical_plan_v1.md).

`EditorApp` is the console's authoring app: ONE app, opened on a `Project`, whose
tabs ARE the already-extracted editor layers -- the view ladder ordered gentlest
-> deepest (spec shell_ux_v1.md Section 6):

    Config -> Blocks -> Code -> Sprites -> Map -> Scene -> Music        [ PLAY ]

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
    from bar_layer import _BAR_ICON, _ZONE_LEFT_GAME
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.bar_layer import _BAR_ICON, _ZONE_LEFT_GAME


# The Editor's lent left zone (Stage 4 of docs/history/shell_ux_technical_plan_v1.md, #46
# zoned bar): PROJECTS (back to the picker) + the tab ladder + UNDO/REDO + PLAY, in
# the spec Section 6 order (Projects -> Config -> Blocks -> Code -> Sprites -> Map
# -> Music -> UNDO -> REDO -> PLAY), rendered as icons inside the rect the bar lends
# it. Each entry is (tab_name_or_None_or_ACTION, icon_kind); `tab_name` is what
# EditorApp.tab equals when that icon's destination is showing (so draw_zone can
# highlight it). PROJECTS (_ZONE_PROJECTS -> open_picker, edit another project) and
# UNDO/REDO (_ZONE_UNDO/_ZONE_REDO -> ws.undo()/ws.redo(), #88) are ACTIONS, not
# destinations, so they're never highlighted; so is PLAY (None).
#
# SAVE is GONE (#111, owner decision 2026-07-21): autosave is the only model now --
# an idle-typing debounce commits mid-edit, and every tab-leaving event (a tab
# switch, PLAY, PROJECTS, a window/context-X, a workspace swap, going home) hard-
# commits whichever tab was showing via save_current() -- the exact verb the SAVE
# icon used to dispatch, now called automatically instead of from a tap. See
# set_tab/leave below and the exit-path call sites in console.py/wm_windowed.py.
#
# Fits 320px: 11 icons * 16px = 176px inside the ~202px lent zone -- UNDO/REDO (#88)
# pushed the ladder to 11, well inside budget; _ZONE_STRIDE keeps the 0-gap stride
# (icons flush) that #88 settled on for the (then 12-icon) ladder.
_ZONE_PROJECTS = "\x00projects"  # sentinel: back to the project-picker (never a real tab)
_ZONE_UNDO = "\x00undo"          # sentinel: UNDO action icon -> ws.undo() (#88)
_ZONE_REDO = "\x00redo"          # sentinel: REDO action icon -> ws.redo() (#88)
_ZONE_TABS = (
    (_ZONE_PROJECTS, "projects"),  # <- back to the PROJECT-PICKER (edit another project)
    ("cards", "edit"),
    ("blocks", "blocks"),
    ("code", "code"),
    ("paint", "paint"),
    ("map", "map"),
    ("scene", "scene"),     # placed-actor placement editor (#85 Stage 2)
    ("music", "music"),
    (_ZONE_UNDO, "undo"),   # UNDO -> ws.undo() (#88), dimmed when there's nothing to undo
    (_ZONE_REDO, "redo"),   # REDO -> ws.redo() (#88), dimmed when there's nothing to redo
    (None, "run"),          # PLAY
)
_ZONE_STRIDE = _BAR_ICON        # 0-gap ladder (#88) -- see the block comment above

# The SHELF-density zone (visual identity v1 Phase 3, the Studio mockup): the six
# tabs as LABELED chips (icon + name) via ui.tab_row, PROJECTS as an icon chip on
# the left, PLAY a labeled button on the right (SAVE dropped, #111). The 320x240
# baseline keeps the frozen 9-icon ladder above, byte-identical.
_TAB_CHIPS = (
    ("cards", "CONFIG", "edit"),
    ("blocks", "BLOCKS", "blocks"),
    ("code", "CODE", "code"),
    ("paint", "SPRITES", "paint"),
    ("map", "MAP", "map"),
    ("scene", "SCENE", "scene"),
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

    def open_scene(self):
        """Open the scene placement editor (#85 Stage 2): the WYSIWYG placed-
        actor view over the cart's default scene. Mirrors open_map -- reset
        gesture/zoom state, then build via set_menu_view("scene")."""
        ws = self.ws
        ws.wm.goto("menu")       # Stage 6e: spawn/return the Editor on the back-stack
        ws.save_status = None
        ws.scene_ui.on_open()        # fresh gesture/zoom/props state
        ws.set_menu_view("scene")
        # Open with the camera at the world origin so the game viewport's frame
        # shows immediately (screen-space carts place inside it, no panning).
        if ws.scene_ui.sceneedit is not None:
            ws.scene_ui.sceneedit.cam_x = 0
            ws.scene_ui.sceneedit.cam_y = 0

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
        if view != self.tab and self.project is ws.project:
            # (#111) autosave-only: a tab switch is an exit path for the OUTGOING
            # tab -- hard-commit whatever it holds (the exact verb the removed SAVE
            # icon used to dispatch) before the ladder moves on. Reads self.tab
            # BEFORE it's reassigned below, so save_current() persists the right
            # target. A same-tab call (view == self.tab, e.g. open()'s landing
            # set_menu_view) is a no-op here -- nothing changed to commit. The
            # `self.project is ws.project` guard skips a stale/never-opened editor
            # (self.tab defaults to "cards" from __init__, meaningless if EditorApp.
            # open() was never actually called for the CURRENT project -- e.g. a
            # bare ws.set_menu_view("code") on a workspace opened via the RUN path,
            # never the Editor) -- without it a plain RUN followed by a direct
            # set_menu_view call would spuriously commit_config() a "cards" tab
            # that was never really open (confirmed by test_journal_wiring.py).
            self.save_current()
        self.tab = view              # the `tab` setter bumps zone_gen on a real change
                                     # (Stage 4, #46: the lent zone's highlight moved)
        if view == "code":
            if ws.editor is None and ws.cart is not None:
                ws.editor = CodeEditor(ws.cart["src"],
                                       cols=ws.code_layout.cols,
                                       rows=ws.code_layout.rows,
                                       clip=ws.clipboard)
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
        elif view == "scene":
            # Mirror the map branch: build the SceneEditor over the cart's
            # default scene (#85 Stage 2). Committed gestures sync into the live
            # widgets.Scenes, so a PLAY runs the freshest placement.
            ws.scene_ui.build()
        elif view == "blocks":
            # Build the BlockEditor over the cart's block program (#29), lazily --
            # see BlockEditorUI.build's docstring (block_editor_ui.py) for the
            # load-or-fresh + data-loss-guard rules (moved verbatim from here).
            ws.block_ui.build()
        elif view == "music":
            # Build the MusicEditor over the open cart's live AudioBank (#50): the
            # SAME bank the running cart plays through, so an edit is heard immediately
            # by the preview AND by the cart on resume. Edits go straight into that
            # bank; a tab-leave/PLAY hard-commit persists it to sounds.json (#111).
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
        if self.tab == "scene" and ws.scene_ui.tag_edit:
            ws.scene_ui._tag_commit()   # PLAY mid-typing keeps the typed tag (#85)
        # (#111) PLAY is a hard-commit trigger too -- autosave is the only model now
        # (no SAVE tap exists to rely on), so persist whatever the active tab holds
        # BEFORE running. Config is the one exception: it keeps its OWN commit below,
        # gated on a CLEAN _start() (a crash must not overwrite good config with a
        # half-applied edit) -- every other tab commits unconditionally, here.
        if self.tab != "cards":
            self.save_current()
        # Returning to the desktop from the code editor must run whatever source is
        # in the editor now (the kid may have fixed a crash and kept typing, or just
        # edited and left). Re-_start() with the editor text so the FIXED cart
        # actually runs -- otherwise a previously-set cart_error would re-paint the
        # stale "crashed" panel and _update/_draw would stay None forever.
        if self.tab == "code" and ws.editor is not None and ws.cart is not None:
            ws.cart["src"] = ws.editor.text()
            ws._start()
        elif self.tab == "blocks":
            ws.block_ui.on_leave()
            # save_current() above just recompiled + committed cart["src"] (save_blocks);
            # re-run it so leaving the outline runs the freshest program, just like the
            # code editor does. (A refused save -- protected/graduated -- leaves src at
            # its last good state, so this re-runs THAT.)
            if ws.cart is not None:
                ws._start()
        elif self.tab == "scene":
            # Scenes are consumed ONCE at _init (#85 Variant A) -- a resumed cart
            # would never re-read scene(), so PLAY from the scene tab re-starts:
            # the fresh _init spawns the placement the kid just made (the editor
            # live-syncs each gesture into ws.scenes; Player.start resets the
            # parse cache). Mirrors the blocks tab's re-run-on-leave.
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
    # The Editor's tab ladder + UNDO/REDO + PLAY, shown on EVERY tab now (Stage-4
    # rollout): cards_layer.py, paint_layer.py, layers.py's _MapLayer/_MusicLayer,
    # code_layer.py and block_editor_ui.py each call ws.bar_layer._draw_status_strip
    # ("menu") from their draw() (+ ws.bar_layer.handle_bar_tap("menu", ...) from
    # handle_pointer), so the bar is identical across all six Editor tabs and each
    # tab's own RUN/SAVE/CLOSE chrome was dissolved into it -- SAVE itself is GONE
    # now (#111): every tab-leaving event calls save_current() automatically (see
    # set_tab/leave above), so there is no tap-driven affordance left to draw here.

    def draw_zone(self, cv, rect):
        """Draw the tab ladder + UNDO/REDO + PLAY inside the rect the bar lent us,
        highlighting the active tab. `cv` may be the bar's offscreen cache strip
        (#43) -- this draws the SAME pixels either way, which is what makes the
        cached strip pixel-identical to a direct render.

        The icon side + stride derive from the lent rect's HEIGHT (16*fs -- the
        bar hands over an icon-high rect), so the ladder scales with the system
        font (#39): at fs=1 this is byte-identical to the frozen 16px/0-gap
        constants, at fs=2+ the icons no longer overlap. UNDO/REDO (#88) each
        query ws.can_undo()/ws.can_redo() -- an SD-backed journal read, but one
        that only runs HERE, inside a cache-miss re-render (never per-frame)."""
        ws = self.ws
        NAMES = self._NAMES
        if not ws.layout._base:
            # SHELF density (visual identity v1 Phase 3): labeled tabs + a labeled
            # PLAY button -- the Studio mockup's tab row, on the zoned bar (SAVE
            # dropped, #111).
            th = ws.theme_colors
            proj, tabs_area, play_r = self._zone_parts(rect)
            band_ink = th["ink"] if ws.bar_layer.zone_band_light("menu") else None
            _ui.button(cv, th, proj, "", glyph="projects", kind="normal",
                       glyph_draw=ws._glyph)
            _ui.tab_row(cv, th, tabs_area, _TAB_CHIPS, self.tab,
                        icon_for=getattr(ws, "_icon_image_keyed", None),
                        ink=band_ink)
            _ui.button(cv, th, play_r, "PLAY", kind="play", glyph="run",
                       glyph_draw=ws._glyph)
            return
        x0, y0, w, h = rect
        ic = h if h > 0 else _BAR_ICON      # icon side (16*fs)
        stride = ic                         # 0-gap ladder (#88) -- see _ZONE_STRIDE
        for i, (tab, glyph) in enumerate(_ZONE_TABS):
            x = x0 + i * stride
            if x + ic > x0 + w:
                break                       # ran out of lent width -- draw what fits
            if tab is not None and tab == self.tab:
                # Frozen indigo on the dark bar; the theme hilite on a light band
                # (the base ladder under a light variant, owner 2026-07-23).
                cv.rect(x, y0, ic, ic,
                        ws.theme_colors["hilite"]
                        if ws.theme_colors.get("bar_light") else NAMES["indigo"])
            if tab == _ZONE_UNDO:
                self._draw_history_icon(cv, glyph, x, y0, ic, ws.can_undo())
            elif tab == _ZONE_REDO:
                self._draw_history_icon(cv, glyph, x, y0, ic, ws.can_redo())
            else:
                ws._icon(glyph, x, y0, cv)

    def _draw_history_icon(self, cv, glyph, x, y, ic, enabled):
        """Draw the UNDO/REDO bar icon (#88), dimmed when the journal has nothing to
        walk. `enabled` reads the themeable 16x16 IconSheet sprite like any other bar
        icon; disabled falls back to the plain _glyph bitmap tinted dim -- there's no
        separate 'dimmed' sprite variant, so the fallback vocabulary carries the
        color instead."""
        ws = self.ws
        if enabled:
            ws._icon(glyph, x, y, cv)
        else:
            th = ws.theme_colors
            ws._glyph(glyph, (x, y, ic, ic),
                      th["chrome_ink_dim"] if th.get("bar_light")
                      else self._NAMES["dark_blue"], cv)

    def _zone_parts(self, rect):
        """PURE shelf-zone geometry (shared by draw_zone and zone_tap so a strip-
        cached draw and a later tap can't desync): PROJECTS chip | labeled tab row
        | PLAY, the button right-aligned (SAVE dropped, #111). Scales off the
        lent rect's height (16*fs, like the frozen ladder)."""
        fs = max(1, rect[3] // 16)
        gap = 4 * fs
        proj, rest = _ui.cut_left(rect, 22 * fs)
        play_r, rest = _ui.cut_right(rest, 54 * fs)
        _pad, rest = _ui.cut_right(rest, gap)
        tabs_area = (rest[0] + gap, rest[1], max(0, rest[2] - 2 * gap), rest[3])
        return proj, tabs_area, play_r

    def zone_tap(self, px, py, rect=None):
        """Hit-test the tab ladder + PLAY and dispatch. `rect` is the lent
        left-zone rect BarLayer drew into -- the fixed game-canvas _ZONE_LEFT_GAME
        for a game-canvas tab, or the responsive layout.zone_left for the
        system-canvas tabs (both hit-test in the same coord space the bar drew
        in). Defaults to _ZONE_LEFT_GAME so a bare call is unchanged. The icon
        side + stride derive from the rect height, matching draw_zone; the shelf
        tiers resolve against the SAME _zone_parts geometry the draw used."""
        if rect is not None and not self.ws.layout._base:
            proj, tabs_area, play_r = self._zone_parts(rect)
            if self._in(px, py, proj):
                return self._activate_zone_tab(_ZONE_PROJECTS)
            if self._in(px, py, play_r):
                return self._activate_zone_tab(None)
            fs = max(1, rect[3] // 16)
            slim = [(tid, label) for tid, label, _ic in _TAB_CHIPS]
            for tid, r, _labels_on in _ui.tab_row_rects(tabs_area, slim, fs):
                if self._in(px, py, r):
                    return self._activate_zone_tab(tid)
            return False
        x0, y0, w, h = rect if rect is not None else _ZONE_LEFT_GAME
        ic = h if h > 0 else _BAR_ICON
        stride = ic                         # 0-gap ladder (#88) -- matches draw_zone
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
        elif tab == "scene":
            ws._open_scene()
        elif tab == "blocks":
            ws._open_blocks()
        elif tab == "music":
            ws._open_music()
        elif tab == _ZONE_UNDO:   # UNDO (#88): the shared journal walk, any tab
            ws.undo()
        elif tab == _ZONE_REDO:   # REDO (#88)
            ws.redo()
        else:                     # PLAY (tab is None)
            # #184: deferred -- the hard-commit (~850ms SD, #154) + compile +
            # exec + first-world build run behind the next painted frame
            # (LOADING toast), never inside the bar tap that asked for them.
            ws.defer(ws._leave_menu)
        return True

    def _tab_is_clean(self, tab):
        """True when `tab` PROVABLY has nothing to persist, so save_current can
        skip the whole commit.

        Why this exists (on-glass P4, 2026-07-25): every exit path hard-commits
        the outgoing tab, and a commit is expensive -- serialize the asset
        (`to_hex`, ~220ms), write it to flash (~800ms: _write_atomic costs five
        littlefs metadata ops), then append a full-file snapshot to the undo
        journal (~175ms). With no guard that ran even when the kid had merely
        LOOKED at a tab, so walking the tab ladder cost 0.5-1.4s PER SWITCH
        ("slow switching between project tabs"). Measured on glass: map 1356ms,
        paint 1145ms, music 919ms, code 579ms, cards 534ms -- against a
        32-247ms redraw.

        Conservative by construction: it returns True only for tabs whose
        cleanliness is provable, AND whose #111 op-history has no pending batch.
        Anything else -- an unknown tab, a missing editor -- falls through to
        the commit exactly as before, so this can only skip writes that would
        have been byte-identical no-ops.

        Per-tab signal, and why they differ: paint/map/blocks/scene/music carry
        a `dirty` flag their editor cores set at the single mutation chokepoint
        AND that their undo codecs re-set on replay (_PaintOps psets through the
        sheet, _MapOps sets tm.dirty, BlockEditor._after_history sets dirty,
        _SceneOps._replay and _MusicOps both mark the doc), so the
        flag is trustworthy there. The CODE tab's is NOT: CodeEditor.set_text is
        the LOADER (it clears dirty) and op_history.TextEditCodec rewrites the
        buffer through it, so an undo/redo leaves a changed document flagged
        clean. Code therefore compares content against the last persisted source
        (moy_carts.save_code keeps cart["src"] in step) -- an O(n) compare of a
        few KB, nothing next to the ~800ms flash write it guards."""
        ws = self.ws

        def _quiet(hist):
            return hist is None or not hist.peek()

        proj = self.project
        if tab == "code":
            ed = ws.editor
            cart = ws.cart
            if ed is None or not cart:
                return False
            return (ed.text() == cart.get("src")
                    and _quiet(proj._code_history() if proj is not None else None))
        if tab == "paint":
            sh = ws.sheet
            return (sh is not None and not getattr(sh, "dirty", True)
                    and _quiet(proj._paint_history() if proj is not None else None))
        if tab == "map":
            tm = getattr(ws, "tilemap", None)
            return (tm is not None and not getattr(tm, "dirty", True)
                    and _quiet(proj._map_history() if proj is not None else None))
        if tab == "blocks":
            blk = getattr(getattr(ws, "block_ui", None), "editor", None)
            return blk is not None and not getattr(blk, "dirty", True)
        if tab == "scene":
            se = getattr(getattr(ws, "scene_ui", None), "sceneedit", None)
            return (se is not None and not getattr(se, "dirty", True)
                    and _quiet(proj._scene_history() if proj is not None else None))
        if tab == "music":
            me = getattr(getattr(ws, "music_ui", None), "musicedit", None)
            return (me is not None and not getattr(me, "dirty", True)
                    and _quiet(proj._music_history() if proj is not None else None))
        if tab == "cards":
            # No editor core: the config IS the document. It's clean when the
            # live values already match what the cart dict carries (what a
            # commit would write) and no field tweak is pending in the history.
            if proj is None or not proj.cart:
                return False
            return (dict(proj.config) == dict(proj.cart.get("cfg") or {})
                    and _quiet(proj.config_hist))
        return False                 # unknown tab: commit as before

    def save_current(self):
        """Hard-commit the ACTIVE tab (#111: the autosave-only model's persist verb --
        SAVE was a tap dispatching here; now every exit path calls this directly: a
        tab switch (set_tab), PLAY (leave), a workspace swap (console.py's
        _open_workspace, reached from PROJECTS -> pick a project) and going home
        (console.py's go_home), and a window/context-X close (wm_windowed.py's
        close_window_kind)). Each tab keeps its own persist verb; this just routes
        to whichever tab is up. Config persists via commit_config (no re-run -- PLAY
        runs, handled separately in leave() so a crash can't overwrite good config).
        The theme (EDIT ICONS) tab has no bar zone, so it's never routed here -- its
        own CLOSE/leave hard-commits via ws.save_icons() (paint_layer.ThemeLayer.leave)."""
        ws = self.ws
        tab = self.tab
        if self._tab_is_clean(tab):
            return                   # nothing changed -> nothing to persist
        if tab == "code":
            ws.save_code()
        elif tab == "paint":
            ws.save_sprites()
        elif tab == "map":
            ws.save_map()
        elif tab == "scene":
            ws.save_scene()
        elif tab == "music":
            ws.save_sounds()
        elif tab == "blocks":
            ws.block_ui.save_blocks()
            ws.block_ui.commit_workspace_scene()   # persist the side-by-side scene pane (#93/#85)
        elif tab == "cards":
            ws._save_config()

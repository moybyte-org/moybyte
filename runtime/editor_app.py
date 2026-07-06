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
`ws.sheet` got in Stage 1), so the router keeps working unmodified through Stages
3-5. It is deleted only at the END of the split Stage 6, once the back-stack is the
router and nothing reads it.

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
    from bar_layer import _BAR_ICON, _BAR_GAP, _BAR_Y, _ZONE_LEFT_GAME
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.bar_layer import _BAR_ICON, _BAR_GAP, _BAR_Y, _ZONE_LEFT_GAME


def _in(px, py, rect):
    x, y, w, h = rect
    return x <= px < x + w and y <= py < y + h


# The Editor's lent left zone (Stage 4 of docs/shell_ux_technical_plan_v1.md, #46
# zoned bar): the tab ladder + PLAY, in the spec Section 6 order (Config -> Blocks
# -> Code -> Sprites -> Map -> Music -> PLAY), rendered as icons inside the rect
# the bar lends it. Each entry is (tab_name_or_None, icon_kind); `tab_name` is
# what EditorApp.tab equals when that icon's destination is showing (so draw_zone
# can highlight it); PLAY has no tab of its own -- it's an action, not a
# destination, so it's never highlighted.
_ZONE_TABS = (
    ("cards", "edit"),
    ("blocks", "blocks"),
    ("code", "code"),
    ("paint", "paint"),
    ("map", "map"),
    ("music", "music"),
    (None, "run"),          # PLAY
)
_ZONE_STRIDE = _BAR_ICON + _BAR_GAP


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
        self.tab = "cards"            # active view -- ws.menu_view projects onto this:
                                      # "cards" | "code" | "paint" | "map" | "blocks"
                                      # | "music" | "theme" (theme = the EDIT-ICONS
                                      # reuse of the paint renderer, set via the
                                      # menu_view setter, not a cart-editor tab)
        # Stage 4 (#46 zoned bar): bumped whenever the active tab changes -- the
        # ONLY thing that varies in the Editor's lent left zone (which icon is
        # highlighted). BarLayer folds this into its cache key so the zoned strip
        # re-renders on a real tab switch and never per frame.
        self.zone_gen = 0

    # -- open the editor on a project (spec Section 4/Section 6: Config-first) -----

    def open(self, project):
        """Open the Editor on `project`, landing on the Config tab (spec Section 6):
        the "Make it mine" cards when the cart exposes an edit schema, else the code
        editor (there are no cards to show). The old Workstation._open_menu."""
        self.project = project
        ws = self.ws
        ws.screen = "menu"
        ws.set_menu_view("cards" if ws.cart.get("edit") else "code")

    def open_paint(self):
        ws = self.ws
        ws.screen = "menu"
        ws._editing_icons = False        # a CART sheet, not the system theme
        ws.paint_status = None
        ws.set_menu_view("paint")

    def open_map(self):
        ws = self.ws
        ws.screen = "menu"
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
        ws.screen = "menu"
        # NB: don't pre-clear blk_status here -- set_menu_view("blocks") sets the
        # "CODE LOCKED" notice when it builds the editor in protected mode, and
        # clearing it after would hide the data-loss guard's message.
        ws.set_menu_view("blocks")

    def open_music(self):
        """Open the music/sound editor (#50): a tracker-style step grid over the
        cart's AudioBank. Mirrors open_map -- reset preview state, then build the
        editor via set_menu_view("music")."""
        ws = self.ws
        ws.screen = "menu"
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
        self.tab = view
        self.zone_gen += 1           # Stage 4 (#46): the lent zone's highlight moved
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
        ws.run(ws.project, self)     # PLAY: run the cart, caller = the Editor (Stage 3b)

    # -- the lent left zone (Stage 4, #46 zoned bar) --------------------------
    #
    # The Editor's tab ladder + PLAY, replacing the pause-only tool switcher for
    # the game-domain tabs wired to show it: cards_layer.py, paint_layer.py, and
    # layers.py's _MapLayer each call ws.bar_layer._draw_status_strip("menu") from
    # their draw() (+ ws.bar_layer.handle_bar_tap("menu", ...) from handle_pointer).
    # CODE/BLOCKS/MUSIC keep their own pre-existing top-of-screen title + action
    # row for now and don't call it -- see the Stage-4 report for why.

    def draw_zone(self, cv, rect):
        """Draw the tab ladder + PLAY inside the rect the bar lent us, highlighting
        the active tab. `cv` may be the bar's offscreen cache strip (#43) -- this
        draws the SAME pixels either way, which is what makes the cached strip
        pixel-identical to a direct render."""
        ws = self.ws
        NAMES = self._NAMES
        x0, y0, w, h = rect
        for i, (tab, glyph) in enumerate(_ZONE_TABS):
            x = x0 + i * _ZONE_STRIDE
            if x + _BAR_ICON > x0 + w:
                break                       # ran out of lent width -- draw what fits
            if tab is not None and tab == self.tab:
                cv.rect(x, y0, _BAR_ICON, _BAR_ICON, NAMES["indigo"])
            ws._icon(glyph, x, y0, cv)

    def zone_tap(self, px, py):
        """Hit-test the tab ladder + PLAY and dispatch to the matching landing
        entry point (or PLAY). Uses the SAME fixed game-canvas rect draw_zone is
        given today (only the game-domain tabs wire this call -- see the module
        docstring); a future code/blocks wiring would need to pass/derive the
        responsive rect the same way BarLayer's draw dispatch does."""
        x0, y0, w, h = _ZONE_LEFT_GAME
        for i, (tab, _glyph) in enumerate(_ZONE_TABS):
            x = x0 + i * _ZONE_STRIDE
            if x + _BAR_ICON > x0 + w:
                break
            if self._in(px, py, (x, y0, _BAR_ICON, _BAR_ICON)):
                return self._activate_zone_tab(tab)
        return False

    def _activate_zone_tab(self, tab):
        ws = self.ws
        if tab == "cards":
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
        else:                     # PLAY (tab is None)
            ws._leave_menu()
        return True

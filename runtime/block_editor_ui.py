"""The block editor's UI layer (issue #29 Part 2): the structured-outline
screen, its modal insert menu, and the inline number/name/text prompts drawn
over a cart's block program.

Two layers, the same split as the console's other sub-editors:
  * BlockEditor (runtime/editors.py) -- the pure tree-edit core: flattens the
    program into cursor-addressable rows, inserts/deletes/moves blocks, sets
    slot values. No canvas, no input.
  * BlockEditorUI (here) -- the UI: draws the outline/menu/prompts on the
    shared canvas and drives them from button/pointer input. Everything
    button-driven or drawn lives here; everything about *what the program
    means* stays in the core.

Extracted from Workstation (runtime/console.py), which used to hold these
~45 methods + their state directly as part of a much larger class. This class
holds a back-reference to the owning Workstation (`self.ws`) for the handful
of primitives it shares with the rest of the console (canvas, input, cart,
carts_store, _with_sd, ach, set_menu_view, _set_text_mode, _leave_menu,
_leave_or_home, _btn/_icon_btn, editor, cart_error, can_manage, pointer,
sys_canvas). `NAMES` / `_in` / `_err_text` are injected at construction
instead of imported back from console.py, which would be a genuine circular
import: console.py imports BlockEditorUI to build the one instance it holds.

Kept name-for-name with the pre-extraction Workstation methods/fields (no
renaming): Workstation.set_menu_view/_relayout/_leave_menu/go_home/open all
just gained one level of indirection (`self.block_ui.X` instead of `self.X`),
and so did the handful of tests that poke the block editor's internals
directly, and tools/make_site_gifs.py's demo-recording script.
"""

try:
    import ui as _ui
except ImportError:  # pragma: no cover - host fallback
    from runtime import ui as _ui


from editors import BlockEditor, KeyEdge, _clone_tree
# The block vocabulary/compiler (#29). Mirrors console.py's own import (see its
# comment there): bare `blocks` on the device (frozen top-level) and once
# host_app has aliased it on the host, or `runtime.blocks` when a test loads
# this module directly without that alias.
try:
    import blocks as _blocks_mod
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import blocks as _blocks_mod

# The shared pre-literate glyph vocabulary (#93 icon pass): the action bar's UNDO/REDO
# draw glyph-only ("..." IS its own icon and stays text). Imported for the membership
# check that keeps the word label as a fallback (chrome._gbtn owns it now).
try:
    from chrome import _gbtn as _chrome_gbtn
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.chrome import _gbtn as _chrome_gbtn


# --- Layout geometry (baseline 320x240 constants + BlockLayout) -------------
#
# Mirrors CodeLayout in console.py (same responsive-reflow contract, #39 step
# 2): at (320, 240, 1) every field below equals the frozen `_BLK_*` constant
# verbatim (the `_base` branch), so the reflow is pixel-identical to the
# pre-#39 fixed layout. _BASE_W/_BASE_H/_FONT_W are duplicated (not imported)
# from console.py's own copies -- they're foundational, unchanging screen
# constants shared by every Layout class, and importing them back would be
# the same circular import BlockEditorUI's __init__ comment explains below.
_BASE_W = 320
_BASE_H = 240
_FONT_W = 8                 # petme128 cell width at scale 1 (one char advance)

# Blocks + Scene side-by-side workspace (#93 blocks / #85 scene): on a wide enough
# canvas the Blocks tab grows an INTERACTIVE scene pane on the right -- Scratch-
# style, "objects on the right, their programming on the left". Below these
# thresholds (notably the 320x240 T-Deck) the tab renders exactly as before
# (blocks-only): the split can't fit a small screen, and the T-Deck stays a player
# that keeps its existing single-tab editors. Big-screen (P4 7"/console/host) only.
_WORKSPACE_MIN_W = 640      # min system-canvas width to show the scene pane
_WORKSPACE_MIN_H = 360      # min height (a full scene panel + block outline)
_WORKSPACE_SCENE_PCT = 58   # the scene (stage) pane gets the larger share of the width
_WORKSPACE_MIN_BLOCK_W = 320  # ...but the block pane never shrinks below this (its
                              # action bar -- ADD/DEL/^/v/CODE/.../UNDO/REDO -- needs
                              # ~306px; the scene takes whatever's left over)
_ADD_SPRITE = "\x00add_sprite"  # the sprite-list "+" chip sentinel (never a real tag)

# Block editor (#29 Part 2): the structured outline. A vertical scrolling list of
# Scratch-style colored block rows (the flattened script) under the unified zoned bar
# (Stage-4 rollout: the old title bar was dissolved into it) and over a bottom action
# bar. Built 320x240-first (the responsive pass is #39 step 2), drawn on the SYSTEM
# canvas through the same primitives as the other editors so host == device. Pressing
# A on an insert `+` row opens the category->block insert menu.
_BLK_HINT_Y = 20         # hint/status strip, just below the 18px unified bar
_BLK_X0 = 6                # left edge of the outline
_BLK_W = 308              # outline width
_BLK_Y0 = 30             # first row's top (below the bar + the hint/status strip)
_BLK_ROW_H = 16          # one block row's height (8px text + padding)
_BLK_INDENT = 12         # px of indent per nesting depth
_BLK_ROWS = 10           # visible rows (Y0 .. just above the action bar)
_BLK_AREA = (_BLK_X0, _BLK_Y0, _BLK_W, _BLK_ROWS * _BLK_ROW_H)
# Bottom action bar: ADD / DEL / up / down on the left, then CODE (graduate). SAVE and
# CLOSE dissolved into the unified bar (Stage-4 rollout): SAVE -> the bar's SAVE icon
# (save_current -> save_blocks), CLOSE -> the bar's context X. CODE stays -- it's the
# one-way #29 GRADUATION action, not navigation.
_BLK_ADD = (6, 196, 40, 22)
_BLK_DEL = (48, 196, 34, 22)
_BLK_UP = (84, 196, 22, 22)
_BLK_DN = (108, 196, 22, 22)
_BLK_CODE = (138, 196, 56, 22)   # graduate to code
# The #93 edit cluster on the free right end of the action bar: "..." opens the
# BLOCK ACTIONS menu (copy / duplicate / paste / move to...), UNDO / REDO walk the
# in-session outline history. Touch-first (a tap fires each); host also binds
# Ctrl+Z / Ctrl+Y to undo/redo.
_BLK_ACT = (198, 196, 30, 22)    # "..." -> the block-actions menu
_BLK_UNDO = (230, 196, 42, 22)
_BLK_REDO = (274, 196, 40, 22)
# The insert menu: a modal list overlay (category list, then the block list for the
# chosen category, then for some slots a small option picker). Drawn over a frozen
# outline; navigated with up/down + A, B backs out one level.
_BLK_MENU = (40, 24, 240, 192)        # the modal panel
_BLK_MENU_ROW_H = 16
_BLK_MENU_ROWS = 10                   # visible menu rows (scrolls if more)
# The variable name-entry prompt: a small modal with the live name + touch buttons
# (the kid types on the keyboard; OK/DEL/X are for touch). #29 Bug 2.
_BLK_KBD = (40, 78, 240, 84)          # the prompt panel
_BLK_KBD_DEL = (52, 124, 50, 24)      # backspace
_BLK_KBD_OK = (188, 124, 40, 24)      # confirm
_BLK_KBD_X = (232, 124, 36, 24)       # cancel
# The number-entry prompt (#29): a taller modal with an on-screen DIGIT GRID so a
# kid can TAP a literal in (touch-only / device sym-key-free), plus type it on the
# keyboard. The grid is 0-9 . - laid out 6-per-row; OK/DEL/X/BLOCK along the bottom.
_BLK_NUM = (24, 36, 272, 168)         # the prompt panel
_BLK_NUM_GX = 34                      # digit grid left
_BLK_NUM_GY = 78                      # digit grid top
_BLK_NUM_BW = 40                      # one key's width
_BLK_NUM_BH = 26                      # one key's height
_BLK_NUM_BPR = 6                      # keys per row
_BLK_NUM_KEYS = ["1", "2", "3", "4", "5", "6",
                 "7", "8", "9", "0", ".", "-"]
_BLK_NUM_DEL = (34, 168, 56, 26)      # backspace
_BLK_NUM_BLOCK = (96, 168, 60, 26)    # swap to a reporter block (expr slots only)
_BLK_NUM_OK = (200, 168, 40, 26)      # confirm
_BLK_NUM_X = (244, 168, 40, 26)       # cancel
# In-row slot editing: tapping/right-step on a selected block cycles to its NEXT
# editable slot; that slot is highlighted, and A opens its editor (number bump,
# variable/dropdown picker, expr -> a nested expression insert).
# Kid-facing category names for the insert menu (the catalog ids are terse keys).
_CAT_LABEL = {
    "events": "When...", "control": "Control", "draw": "Draw", "input": "Buttons",
    "variables": "Variables", "lists": "Lists", "actors": "Sprite", "looks": "Looks",
    "operators": "Math", "sound": "Sound", "myblocks": "My Blocks",
}

# Sentinel menu row: "make a brand-new variable + name it". It heads the Variables
# block list AND the variable-slot picker, so a kid can always create + name a
# variable with just ▲▼ + A (no dragging) and then use it everywhere (#29 Bug 2).
_NEW_VAR_ITEM = "\x00new_var"
_NEW_VAR_LABEL = "+ new variable"

# The list analogue (#48): heads the Lists block list AND the list-slot picker, so a
# kid creates + names a list the same way they make a variable.
_NEW_LIST_ITEM = "\x00new_list"
_NEW_LIST_LABEL = "+ new list"

# Custom blocks (#48: My Blocks / procedures). "+ new block" heads the My Blocks
# category (create + name a proc), then one "call NAME" row per defined proc --
# encoded as the _CALL_PREFIX + the proc name so a menu item stays a plain string.
_NEW_PROC_ITEM = "\x00new_proc"
_NEW_PROC_LABEL = "+ new block"
_CALL_PREFIX = "\x00call:"
# The PROC ACTIONS menu (opened with A on a define-hat): add/remove an input, rename
# or delete the whole block. Sentinels so they never collide with a real block id.
_PROC_ADD = "\x00proc_add"
_PROC_RENAME = "\x00proc_rename"
_PROC_DELP = "\x00proc_delp"
_PROC_DEL = "\x00proc_del"
_PROC_LABELS = {
    _PROC_ADD: "Add an input",
    _PROC_RENAME: "Rename block",
    _PROC_DELP: "Remove last input",
    _PROC_DEL: "Delete this block",
}

# Sentinel menu row in the expr-slot chooser: "type a number" -- the Scratch white
# editable oval. It heads the reporter list so a typed literal (the common case:
# `set score to 0`, `> 100`) is the first, obvious choice; picking it opens the
# number keypad instead of dropping a block (#29).
_NUM_LITERAL_ITEM = "\x00num_lit"
_NUM_LITERAL_LABEL = "123 type a number"

# The #93 BLOCK ACTIONS menu items (context-sensitive rows, built by
# _blk_open_actions). Sentinels so they never collide with a real block id.
_ACT_COPY = "\x00act_copy"
_ACT_DUP = "\x00act_dup"
_ACT_PASTE = "\x00act_paste"
_ACT_MOVE = "\x00act_move"
_ACT_LABELS = {
    _ACT_COPY: "Copy",
    _ACT_DUP: "Duplicate",
    _ACT_PASTE: "Paste here",
    _ACT_MOVE: "Move to...",
}


def _blk_plain_label(label):
    """A block's display label with the {slot} placeholders stripped to bare names,
    so a menu/row reads like 'repeat times' or 'if cond'. The renderer fills the
    real slot values inline; this is the human template without the braces."""
    out = ""
    for ch in str(label):
        if ch == "{":
            out += ""              # drop the brace; keep the slot name that follows
        elif ch == "}":
            out += ""
        else:
            out += ch
    return out


# Kid-facing one-line hints for the surprising blocks (shown under the title).
_BLK_HINTS = {
    "forever": "forever = repeats fast every frame (not endless)",
    "wait": "wait = a friendly pause (each frame keeps drawing)",
    "repeat_until": "repeat until = loops fast until true (not endless)",
    "wait_until": "wait until = each frame keeps drawing till it's true",
    "stop": "stop = end this script now (this frame)",
    "break_loop": "break = jump out of the loop around it",
    "for_each": "for each = run the body once per item in the list",
    "proc_def": "define = your own block; press A to add inputs",
    "call": "call = run your custom block right here",
}


class BlockLayout:
    """Responsive block-editor geometry (#39 step 2): the scrolling outline (X0/W/
    Y0, row height + indent, visible ROWS), the bottom action bar, and the modal
    insert menu -- derived from the SYSTEM canvas size (w, h) + font scale instead of
    the 320x240 `_BLK_*` constants. A larger panel shows MORE rows + WIDER blocks; a
    bigger font scales the rows/text/buttons.

    Same hard contract as `Layout`: at (320, 240, 1) every field equals the frozen
    `_BLK_*` constant verbatim (the `_base` branch), so the degradation path is
    pixel-identical to today."""

    def __init__(self, w=_BASE_W, h=_BASE_H, font_scale=1, bounds=None):
        self.w = int(w)
        self.h = int(h)
        self.fs = max(1, int(font_scale))
        fs = self.fs
        self.cell = _FONT_W * fs
        # `bounds` (bx, by, bw, bh) confines the OUTLINE + action bar to a sub-rect
        # -- the left pane of the combined Blocks+Scene workspace (blocks-left /
        # objects-right). The modal overlays (insert menu / prompts) stay centered
        # on the full canvas. A bounded layout never takes the frozen 320x240 branch
        # (big-screen feature), so `_base` excludes it and the T-Deck is unchanged.
        self._base = (self.w == _BASE_W and self.h == _BASE_H and fs == 1
                      and bounds is None)
        # The hint + SAVE-status strip sits just below the 18px unified bar (the old
        # title row was dissolved into the bar, Stage-4 rollout).
        self.hint_y = _BLK_HINT_Y * fs
        self.status_x = 198 * fs                  # the SAVE-status text x (right side)
        self.row_h = _BLK_ROW_H * fs
        self.indent = _BLK_INDENT * fs
        self.x0 = _BLK_X0 * fs
        self.y0 = _BLK_Y0 * fs
        # -- action bar: a row of buttons anchored to the bottom -----------------
        # SAVE/CLOSE moved to the unified bar (Stage-4 rollout); only ADD / DEL / up /
        # dn + CODE (the #29 graduation action) stay in the body.
        if self._base:
            self.bar_y = 196
            self.bar_h = 22
            self.add_btn, self.del_btn = _BLK_ADD, _BLK_DEL
            self.up_btn, self.dn_btn = _BLK_UP, _BLK_DN
            self.code_btn = _BLK_CODE
            # #93 edit cluster (frozen constants at base).
            self.act_btn = _BLK_ACT
            self.undo_btn = _BLK_UNDO
            self.redo_btn = _BLK_REDO
        else:
            self.bar_h = 22 * fs
            self.bar_y = self.h - self.bar_h - 2 * fs
            by, bh = self.bar_y, self.bar_h
            # left cluster: ADD / DEL / up / dn ; CODE (graduate) after them; then the
            # #93 edit cluster ("..." / UNDO / REDO) packed after CODE.
            x = self.x0
            self.add_btn = (x, by, 40 * fs, bh); x += 42 * fs
            self.del_btn = (x, by, 34 * fs, bh); x += 36 * fs
            self.up_btn = (x, by, 22 * fs, bh); x += 24 * fs
            self.dn_btn = (x, by, 22 * fs, bh); x += 24 * fs
            self.code_btn = (x, by, 56 * fs, bh); x += 58 * fs
            self.act_btn = (x, by, 30 * fs, bh); x += 32 * fs
            self.undo_btn = (x, by, 42 * fs, bh); x += 44 * fs
            self.redo_btn = (x, by, 40 * fs, bh)
        # -- outline width + visible rows ----------------------------------------
        if self._base:
            self.outline_w = _BLK_W              # 308
            self.rows = _BLK_ROWS                # 11
        else:
            self.outline_w = self.w - 2 * self.x0
            avail_h = self.bar_y - self.y0
            self.rows = max(3, avail_h // self.row_h)
        # -- modal insert menu (centered, scales with the canvas/font) -----------
        self.menu_row_h = _BLK_MENU_ROW_H * fs
        if self._base:
            self.menu = _BLK_MENU                # (40, 24, 240, 192)
            self.menu_rows = _BLK_MENU_ROWS      # 10
        else:
            mw = min(self.w - 16 * fs, 240 * fs)
            mh = min(self.h - 24 * fs, self.bar_y - 24 * fs)
            mx = (self.w - mw) // 2
            my = 24 * fs
            self.menu = (mx, my, mw, mh)
            self.menu_rows = max(3, (mh - 16 * fs) // self.menu_row_h)
        # Confine the outline + action bar to the workspace's left pane (the modals
        # above keep full-canvas centering, so they float over both panes).
        if bounds is not None and not self._base:
            self._apply_bounds(bounds)

    def _apply_bounds(self, bounds):
        """Relocate the outline + action bar into `bounds` (bx, by, bw, bh) -- the
        left pane of the combined Blocks+Scene workspace. Overrides the fields the
        draw/hit-test read (x0/y0/hint_y/status_x/bar/outline_w/rows); the modal
        insert-menu/prompt geometry stays centered on the full canvas."""
        bx, by, bw, bh = bounds
        fs = self.fs
        pad = _BLK_X0 * fs
        self.hint_y = by + 2 * fs
        self.status_x = bx + bw - 56 * fs      # SAVE-status/dirty-* on the pane's right
        self.x0 = bx + pad
        self.y0 = by + 12 * fs                  # below the hint/status line
        self.bar_h = 22 * fs
        self.bar_y = by + bh - self.bar_h - 2 * fs
        by2, bhh = self.bar_y, self.bar_h
        x = self.x0
        self.add_btn = (x, by2, 40 * fs, bhh); x += 42 * fs
        self.del_btn = (x, by2, 34 * fs, bhh); x += 36 * fs
        self.up_btn = (x, by2, 22 * fs, bhh); x += 24 * fs
        self.dn_btn = (x, by2, 22 * fs, bhh); x += 24 * fs
        self.code_btn = (x, by2, 56 * fs, bhh); x += 58 * fs
        self.act_btn = (x, by2, 30 * fs, bhh); x += 32 * fs
        self.undo_btn = (x, by2, 42 * fs, bhh); x += 44 * fs
        self.redo_btn = (x, by2, 40 * fs, bhh)
        self.outline_w = bw - 2 * pad
        avail_h = self.bar_y - self.y0
        self.rows = max(3, avail_h // self.row_h)

    def area(self):
        return (self.x0, self.y0, self.outline_w, self.rows * self.row_h)


class BlockEditorUI:
    """The structured-outline UI over a cart's block program: cursor + insert menu
    + inline prompts (draw + input/pointer). One instance lives on Workstation
    (`self.block_ui`), built once in Workstation.__init__; `ws.block_ui.build()` is
    called lazily from `set_menu_view("blocks")` the first time a cart's block
    editor is opened, exactly like the pre-extraction code did inline."""

    def __init__(self, ws, names, in_rect, err_text, clamp_scroll):
        self.ws = ws
        # Injected instead of imported back from console.py (see module docstring):
        # console.py builds this instance, so `import console` here would be a real
        # circular import at BlockEditorUI's own module-load time. `clamp_scroll` is
        # the tiny shared scrolloff helper (#dedup, stage 1) also used by
        # Workstation._settings_scroll -- injecting it here (instead of duplicating
        # it) keeps that dedup meaningful.
        self._NAMES = names
        self._in = in_rect
        self._err_text = err_text
        self._clamp_scroll = clamp_scroll
        # Block editor (#29 Part 2): a BlockEditor over the cart's block program +
        # the structured-outline UI state. `blocks_ed` is built lazily on first open.
        self.blocks_ed = None         # BlockEditor while menu_view == "blocks"
        self.blk_top = 0              # first outline row scrolled into view
        self._dragv = None            # drag-to-scroll anchor (held vertical drag)
        self.blk_slot = 0             # which slot of the selected block is highlighted
        self.blk_menu = None          # active insert menu state dict, or None
        self.blk_status = None        # last block-editor SAVE result text
        self.blk_protect = False      # block editor opened on a hand-written-code cart
        self.blk_graduated = False    # cart has GRADUATED (Stage 8): blocks read-only
        self.blk_kbd = None           # inline name-entry prompt state dict, or None
        self._blk_ekey = KeyEdge()    # #93: Ctrl+Z/Y edge tracker
        self.block_layout = BlockLayout()
        # Blocks+Scene workspace: which pane owns the keyboard ("blocks"|"scene"),
        # and whether a pointer gesture in flight belongs to the scene pane (so a
        # drag that started there keeps routing to it until release).
        self._ws_focus = "blocks"
        self._ws_scene_drag = False
        # Per-object scripts (#85/#93): the SPRITE LIST -- [(rect, tag_or_None), ...]
        # for STAGE + every sprite chip drawn atop the block pane in the workspace
        # (rebuilt each draw, hit-tested in _blocks_pointer). Scratch's sprite pane.
        self._blk_roster_btns = []

    def relayout(self, w, h, fs):
        """Rebuild the responsive layout from the live system-canvas size + the
        effective font scale (mirrors Workstation._relayout), and re-clamp the
        outline scroll if an editor is already open."""
        self.block_layout = BlockLayout(w, h, fs)
        if self.blocks_ed is not None:
            self._blk_reveal()

    # -- Blocks + Scene side-by-side workspace (#93/#85) ----------------------

    def _workspace_active(self):
        """True when the canvas is big enough (and the cart has a sheet + scenes)
        to show the interactive scene pane beside the blocks. Big-screen only --
        the 320x240 T-Deck and any narrow window fall through to blocks-only."""
        ws = self.ws
        sc = ws.sys_canvas
        proj = ws.project
        if sc is None or proj is None or getattr(proj, "cart", None) is None:
            return False
        if getattr(proj, "sheet", None) is None or getattr(proj, "scenes", None) is None:
            return False
        return sc.w >= _WORKSPACE_MIN_W and sc.h >= _WORKSPACE_MIN_H

    def _workspace_panes(self):
        """The (left, right) pane rects: blocks-left / scene-right, both below the
        18px OS bar. The scene (stage) takes the larger share (~_WORKSPACE_SCENE_PCT
        of the width), but the block pane is floored at _WORKSPACE_MIN_BLOCK_W so its
        action bar always fits -- the scene gets whatever's left over."""
        sc = self.ws.sys_canvas
        fs = max(1, getattr(sc, "font_scale", 1))
        top = 18 * fs
        body_h = sc.h - top
        gap = 3 * fs
        scene_w = (sc.w * _WORKSPACE_SCENE_PCT) // 100
        scene_w = min(scene_w, sc.w - _WORKSPACE_MIN_BLOCK_W * fs - gap)
        scene_w = max(scene_w, 1)
        left_w = sc.w - scene_w - gap
        left = (0, top, left_w, body_h)
        right = (left_w + gap, top, scene_w, body_h)
        return left, right

    def _layout_workspace(self):
        """If the workspace is active, split the canvas and BOUND both editors'
        layouts to their panes; return (left, right) or None. Called at the top of
        draw AND pointer/input so the geometry is consistent within a frame (input
        runs before draw) and re-derives on a window resize."""
        if not self._workspace_active():
            return None
        left, right = self._workspace_panes()
        sc = self.ws.sys_canvas
        fs = max(1, getattr(sc, "font_scale", 1))
        self.block_layout = BlockLayout(sc.w, sc.h, fs, bounds=left)
        if self.blocks_ed is not None:
            self._blk_reveal()               # re-clamp scroll to the pane's row count
        # Lazily build the scene editor the first time the workspace opens, then
        # bound it to the right pane (mirrors EditorApp.set_tab's "scene" arm).
        su = self.ws.scene_ui
        if su.sceneedit is None:
            su.build()
            su.on_open()
        su.relayout_bounded(right)
        return left, right

    def _scene_pane_pointer(self, px, py, click):
        """Route a pointer event into the scene pane, mirroring _SceneLayer.
        handle_pointer (tap = place/select, drag = move/pan, else release). After the
        gesture, point the block outline at the selected object's scripts (Scratch:
        pick a sprite -> see its code)."""
        ws = self.ws
        self._ws_focus = "scene"
        if click:
            ws.scene_ui._scene_click(px, py)
        elif ws.pointer.down:
            ws.scene_ui._scene_drag(px, py)
        else:
            ws.scene_ui._scene_release(px, py)
        # Sync after EVERY gesture edge: selecting an existing actor lands on press,
        # but PLACING a new one completes on release -- both must focus its scripts.
        self._sync_target_from_scene()

    def _selected_scene_tag(self):
        """The tag of the currently-selected scene actor, or None (Stage)."""
        se = self.ws.scene_ui.sceneedit
        if se is not None:
            sel = getattr(se, "sel", None)
            if sel is not None and 0 <= sel < len(se.rows):
                tag = se.rows[sel].get("tag")
                if tag:
                    return str(tag)
        return None

    def _sync_target_from_scene(self):
        """Point the block outline at the selected object's scripts. Selecting/placing
        an object focuses its scripts; a tag change is followed too. No selection keeps
        whatever target is active (so a stray tap doesn't yank the kid back to Stage)."""
        be = self.blocks_ed
        if be is None:
            return
        tag = self._selected_scene_tag()
        if tag is not None:
            be.set_target(tag)

    def _draw_scene_pane(self, right):
        """Draw the interactive scene into the right pane: a thin divider, then the
        SceneEditorUI panel (it clears only its own body_fill = the pane)."""
        ws = self.ws
        cv = ws.sys_canvas
        fs = self.block_layout.fs
        th = ws.theme_colors
        cv.rect(right[0] - 2 * fs, right[1], fs, right[3],
                th.get("edge", self._NAMES["dark_grey"]))
        ws.scene_ui._draw_scene()

    def _roster_tags(self):
        """The ordered, distinct list of sprite tags -- every object placed in the
        scene PLUS any object that has scripts (so a sprite shows in the list whether
        or not it's on the stage yet). Scene-placement order first."""
        tags = []
        seen = {}
        se = self.ws.scene_ui.sceneedit
        if se is not None:
            for r in se.rows:
                t = r.get("tag")
                if t and t not in seen:
                    seen[t] = True
                    tags.append(t)
        be = self.blocks_ed
        if be is not None:
            for o in (be.program.get("objects", []) or []):
                t = o.get("tag")
                if t and t not in seen:
                    seen[t] = True
                    tags.append(t)
        return tags

    def _draw_sprite_list(self, lay):
        """The SPRITE LIST atop the block pane (#85/#93) -- Scratch's sprite pane: a
        STAGE chip (the global program) then one chip per sprite in the game. Tapping a
        chip edits that sprite's scripts; the active target is highlighted. Chips that
        run past the pane width are dropped (the list clips, like the tab ladder)."""
        ws = self.ws
        cv = ws.sys_canvas
        NAMES = self._NAMES
        fs = lay.fs
        be = self.blocks_ed
        tgt = be.target if be is not None else None
        y = lay.hint_y - 2 * fs
        h = 11 * fs
        gap = 3 * fs
        right = lay.x0 + lay.outline_w
        self._blk_roster_btns = []
        x = [lay.x0]

        def chip(label, active, tag, base_col):
            w = (len(label) + 1) * lay.cell + 4 * fs
            if x[0] + w > right:
                return False
            cv.rect(x[0], y, w, h, NAMES["yellow"] if active else base_col)
            cv.print(label, x[0] + 3 * fs, lay.hint_y,
                     NAMES["black"] if active else NAMES["white"], 1)
            self._blk_roster_btns.append(((x[0], y, w, h), tag))
            x[0] += w + gap
            return True

        chip("STAGE", tgt is None, None, NAMES["indigo"])
        for t in self._roster_tags():
            if not chip(t[:10], tgt == t, t, NAMES["green"]):
                break
        chip("+", False, _ADD_SPRITE, NAMES["dark_grey"])   # add a new sprite

    def _new_sprite_tag(self):
        """A fresh, unique sprite tag ('sprite1', 'sprite2', ...) for + Add sprite."""
        existing = set(self._roster_tags())
        i = 1
        while ("sprite" + str(i)) in existing:
            i += 1
        return "sprite" + str(i)

    def _add_sprite(self):
        """+ Add sprite (Scratch's sprite-pane +): drop a new sprite (fresh tag, the
        current brush costume) near the middle of the stage, select it, and open its
        scripts. The kid names/repositions/re-costumes it from there."""
        se = self.ws.scene_ui.sceneedit
        if se is None:
            return
        se.stamp_tag = self._new_sprite_tag()
        se.place(152, 112)               # ~centre of the 320x240 stage, grid-snapped
        self.ws.scene_ui._sync_live()
        self._sync_target_from_scene()   # the placed actor is selected -> target its blocks

    def _select_sprite(self, tag):
        """Edit a sprite's scripts (tag) or the global Stage (tag None), and mirror the
        pick onto the stage -- selecting the first actor of that tag, like Scratch
        highlighting the chosen sprite. The two-way twin of _sync_target_from_scene."""
        be = self.blocks_ed
        if be is None:
            return
        be.set_target(tag)
        self._ws_focus = "blocks"
        se = self.ws.scene_ui.sceneedit
        if tag is not None and se is not None:
            for i, r in enumerate(se.rows):
                if r.get("tag") == tag:
                    se.sel = i
                    se.n = r.get("tile", se.n)
                    se.stamp_tag = tag
                    break

    def build(self):
        """Build the BlockEditor over the cart's block program (#29), lazily --
        called from Workstation.set_menu_view("blocks"). A cart authored from
        blocks carries blocks.json (cart["blocks"]); a code-only cart starts a
        fresh empty program -- saving it makes the cart block-authored from then
        on. The Part-1 `blocks` module is injected so the editor core stays
        dependency-free."""
        ws = self.ws
        if self.blocks_ed is not None or ws.project.cart is None:
            return
        prog = None
        if ws.carts_store is not None and ws.project.cart.get("path"):
            try:
                prog = ws._with_sd(
                    lambda: ws.carts_store.load_blocks(ws.project.cart))
            except Exception as exc:  # noqa: BLE001
                print("Moybyte load blocks failed:", self._err_text(exc))
        if prog is None:
            # A DEEP COPY, never the cart's own tree (#93): the editor mutates its
            # program in place and undo/redo rebinds it -- editing an aliased
            # cart["blocks"] would drift the cart's snapshot away from both the
            # disk file and the editor (save_blocks re-syncs it with a fresh copy).
            prog = _clone_tree(ws.project.cart.get("blocks"))
        # DATA-LOSS GUARD (#29): a cart whose main.py is hand-written code (no
        # blocks.json, and main.py wasn't emitted by the block compiler) must
        # NEVER have that code clobbered by saving an empty block program. When
        # that's the case, run the block editor in PROTECTED mode: the outline
        # still opens (read-only-ish), but SAVE / graduate refuse to overwrite
        # main.py and tell the kid why. A genuinely block-authored cart (has
        # blocks.json) -- or an empty/new-template cart with no real code -- is
        # unprotected and round-trips exactly as before.
        self.blk_protect = (prog is None and ws._cart_has_handwritten_code())
        # GRADUATION (Stage 8, spec Section 8): a STORED, one-way project fact (read
        # from the manifest via cart["graduated"]), NOT the re-derived blk_protect
        # heuristic. A graduated cart's blocks.json is FROZEN -- it still opens (so the
        # kid sees the last-good program), but renders read-only + celebrates, and
        # SAVE/graduate refuse to overwrite the diverged main.py (see save_blocks).
        self.blk_graduated = bool(ws.project.cart.get("graduated"))
        self.blocks_ed = BlockEditor(_blocks_mod, prog)
        self.blk_top = 0
        self.blk_slot = 0
        self.blk_menu = None
        if self.blk_graduated:
            self.blk_status = None            # the celebration banner carries the message
        elif self.blk_protect:
            self.blk_status = "CODE LOCKED"
        else:
            self.blk_status = None

    def reset(self):
        """Drop the active editor + any open menu/prompt (a stale one must never
        leak into an unrelated cart or back to the launcher). Called from both
        Workstation.open() (switching carts) and Workstation.go_home()."""
        self.blocks_ed = None
        self.blk_menu = None
        self.blk_kbd = None
        self.blk_protect = False
        self.blk_graduated = False
        self._ws_focus = "blocks"
        self._ws_scene_drag = False

    def on_leave(self):
        """Called from Workstation._leave_menu() when menu_view == "blocks"."""
        self.blk_menu = None
        self.blk_kbd = None
        self._ws_focus = "blocks"
        self._ws_scene_drag = False
        # #93: don't leave a half-started MOVE armed when the kid steps away.
        if self.blocks_ed is not None:
            self.blocks_ed.cancel_move()

    def commit_workspace_scene(self):
        """Persist the scene pane if it was edited in the combined workspace
        (#93/#85). Called from EditorApp.save_current when leaving the Blocks tab --
        the scene has its OWN commit path (save_scene -> Project.commit_scene),
        separate from save_blocks. No-op unless a scene editor is built AND dirty,
        so a blocks-only cart / an untouched scene never writes."""
        su = self.ws.scene_ui
        se = getattr(su, "sceneedit", None)
        if se is not None and getattr(se, "dirty", False):
            self.ws.save_scene()

    # -- block editor (#29 Part 2) -------------------------------------------
    #
    # The structured outline. The cursor moves over the flattened script (block rows
    # + the `+` insert points between them); A inserts (at an insert point) or steps
    # through / edits the selected block's slots. No dragging -- the decided
    # device-friendly interaction. The vocabulary/compiler is Part 1's blocks module;
    # the BlockEditor core (runtime/editors.py) owns the tree edits, this owns the UI.

    def _blk_reveal(self):
        """Keep the block cursor inside the visible outline window (scrolloff). The
        window height is the layout's reflowed row count (#39 step 2)."""
        be = self.blocks_ed
        if be is None:
            return
        nrows = self.block_layout.rows
        self.blk_top = self._clamp_scroll(self.blk_top, be.cur, nrows, len(be.rows))

    def _blk_move_cursor(self, d):
        be = self.blocks_ed
        if be is None:
            return
        be.move(d)
        self.blk_slot = 0          # a new selection resets the slot highlight
        self._blk_reveal()

    def _blk_a(self):
        """The A/primary action in the outline: open the insert menu on a `+` row,
        or edit the highlighted slot of the selected block (a c-block with no slots
        falls back to opening the insert menu for its body's first gap)."""
        be = self.blocks_ed
        if be is None:
            return
        if be.moving():
            # MOVE in progress (#93): the next insert point is the destination.
            if be.at_insert():
                if be.complete_move():
                    self.blk_slot = 0
                    self._blk_reveal()
                    self.blk_status = "MOVED"
                else:
                    self.blk_status = "CAN'T MOVE THERE"
            return
        if be.at_insert():
            self._blk_open_categories()
            return
        b = be.selected_block()
        if b is None:
            return
        if b.get("t") == _blocks_mod.PROC_DEF:
            # A define-hat (#48): A opens the PROC ACTIONS menu (add/remove input,
            # rename, delete) instead of a slot editor -- its name/params aren't slots.
            self._blk_open_proc_menu(b)
            return
        slots = be.slots(b)
        if slots:
            self._blk_edit_slot(b, slots[self.blk_slot % len(slots)])

    def _blk_next_slot(self):
        """Step the slot highlight to the selected block's next slot (wraps)."""
        be = self.blocks_ed
        if be is None:
            return
        slots = be.slots()
        if slots:
            self.blk_slot = (self.blk_slot + 1) % len(slots)

    # -- the insert menu (category -> block, plus slot pickers) --------------
    def _blk_open_categories(self):
        """Open the modal insert menu at the category level."""
        self.blk_menu = {"mode": "cat", "sel": 0, "top": 0,
                         "items": _blocks_mod.categories()}

    # -- copy / paste / duplicate / move + undo (#93) ------------------------
    def _blk_open_actions(self):
        """Open the modal BLOCK ACTIONS menu (copy / duplicate / paste here / move
        to...). Context-sensitive: a selected block offers Copy/Duplicate/Move; an
        insert point with a filled clipboard offers Paste here. Reuses the shared
        modal-menu machinery (up/down + A, B backs out, tap-outside dismisses)."""
        be = self.blocks_ed
        if be is None:
            return
        items = []
        if be.selected_block() is not None:
            items.append(_ACT_COPY)
            items.append(_ACT_DUP)
            items.append(_ACT_MOVE)
        if be.at_insert() and be.has_clipboard():
            items.append(_ACT_PASTE)
        if not items:
            # nothing applies here -- point the kid at what to do rather than open
            # an empty menu (an insert point with nothing copied, say).
            self.blk_status = "COPY A BLOCK FIRST" if be.at_insert() \
                else "SELECT A BLOCK"
            return
        self.blk_menu = {"mode": "actions", "sel": 0, "top": 0, "items": items}

    def _blk_do_action(self, item):
        """Run a chosen BLOCK ACTIONS item and close the menu."""
        be = self.blocks_ed
        self.blk_menu = None
        if be is None:
            return
        if item == _ACT_COPY:
            self.blk_status = "COPIED" if be.copy_block() else "CAN'T COPY THAT"
        elif item == _ACT_DUP:
            if be.duplicate() is not None:
                self.blk_slot = 0
                self._blk_reveal()
                self.blk_status = "DUPLICATED"
        elif item == _ACT_PASTE:
            if be.paste() is not None:
                self.blk_slot = 0
                self._blk_reveal()
                self.blk_status = "PASTED"
            else:
                self.blk_status = "CAN'T PASTE HERE"
        elif item == _ACT_MOVE:
            if be.start_move():
                self.blk_status = "TAP A + SPOT"

    def _blk_undo(self):
        be = self.blocks_ed
        if be is None:
            return
        be.cancel_move()
        self.blk_menu = None
        if be.undo():
            self.blk_slot = 0
            self._blk_reveal()
            self.blk_status = "UNDO"
        else:
            self.blk_status = "NOTHING TO UNDO"

    def _blk_redo(self):
        be = self.blocks_ed
        if be is None:
            return
        be.cancel_move()
        self.blk_menu = None
        if be.redo():
            self.blk_slot = 0
            self._blk_reveal()
            self.blk_status = "REDO"
        else:
            self.blk_status = "NOTHING TO REDO"

    def _blk_open_blocks(self, category):
        ids = _blocks_mod.blocks_in_category(category)
        # Events are the lifecycle hats -- they live at the top level and the empty
        # program already has all three, so they're not insertable into a body.
        if category == _blocks_mod.CAT_EVENTS:
            ids = []
        # Variables: head the list with "+ new variable" so creating + naming one is
        # the first, obvious thing in the category (#29 Bug 2).
        if category == _blocks_mod.CAT_VARIABLES:
            ids = [_NEW_VAR_ITEM] + list(ids)
        # Lists: the same affordance -- "+ new list" heads the category (#48).
        if category == _blocks_mod.CAT_LISTS:
            ids = [_NEW_LIST_ITEM] + list(ids)
        # My Blocks (#48): "+ new block" heads the category, then one "call NAME" row
        # per defined proc (the catalog's own proc_def/call ids never list here).
        if category == _blocks_mod.CAT_PROCS:
            be = self.blocks_ed
            names = be.proc_names() if be is not None else []
            ids = [_NEW_PROC_ITEM] + [_CALL_PREFIX + n for n in names]
        self.blk_menu = {"mode": "blk", "cat": category, "sel": 0, "top": 0,
                         "items": ids}

    def _blk_menu_label(self, i):
        """The display label for menu item i (a category name or a block label)."""
        m = self.blk_menu
        if not m:
            return ""
        item = m["items"][i]
        if item == _NEW_VAR_ITEM:
            return _NEW_VAR_LABEL
        if item == _NEW_LIST_ITEM:
            return _NEW_LIST_LABEL
        if item == _NUM_LITERAL_ITEM:
            return _NUM_LITERAL_LABEL
        if item == _NEW_PROC_ITEM:
            return _NEW_PROC_LABEL
        if item[:len(_CALL_PREFIX)] == _CALL_PREFIX:      # a "call NAME" palette row
            return "call " + item[len(_CALL_PREFIX):]
        if item in _ACT_LABELS:
            return _ACT_LABELS[item]
        if item in _PROC_LABELS:
            return _PROC_LABELS[item]
        if m["mode"] == "cat":
            return _CAT_LABEL.get(item, item).upper()
        if m["mode"] == "blk":
            d = _blocks_mod.block_def(item)
            return _blk_plain_label(d["label"]) if d else item
        if m["mode"] in ("dropdown", "variable", "list"):
            return str(item)
        return str(item)

    def _blk_menu_move(self, d):
        m = self.blk_menu
        if not m or not m["items"]:
            return
        mrows = self.block_layout.menu_rows
        m["sel"] = max(0, min(len(m["items"]) - 1, m["sel"] + d))
        if m["sel"] < m["top"]:
            m["top"] = m["sel"]
        elif m["sel"] > m["top"] + mrows - 1:
            m["top"] = m["sel"] - mrows + 1

    def _blk_menu_select(self):
        """Activate the highlighted menu item (drill in or commit)."""
        m = self.blk_menu
        if not m or not m["items"]:
            return
        item = m["items"][m["sel"]]
        if item == _NEW_VAR_ITEM:
            # "+ new variable": create one with a default name and immediately open
            # the on-screen-keyboard name prompt so the kid names it (#29 Bug 2).
            self._blk_new_variable()
            return
        if item == _NEW_LIST_ITEM:
            # "+ new list": same flow, for lists (#48).
            self._blk_new_list()
            return
        if item == _NEW_PROC_ITEM:
            # "+ new block": create a custom block + name it (#48).
            self._blk_new_proc()
            return
        if item == _NUM_LITERAL_ITEM:
            # "type a number": close the chooser and open the number keypad on this
            # expr slot (the Scratch white oval -- a literal instead of a block).
            blk, name = m["block"], m["slot"]
            self.blk_menu = None
            self._blk_open_number_prompt(blk, name, None)
            return
        if m["mode"] == "actions":
            self._blk_do_action(item)
        elif m["mode"] == "proc":
            self._blk_do_proc_action(item)
        elif m["mode"] == "cat":
            self._blk_open_blocks(item)
        elif m["mode"] == "blk":
            if item[:len(_CALL_PREFIX)] == _CALL_PREFIX:   # "call NAME" (#48)
                self._blk_insert_call(item[len(_CALL_PREFIX):])
            else:
                self._blk_insert_chosen(item)
        elif m["mode"] == "dropdown":
            self.blocks_ed.set_slot(m["slot"], item, m["block"])
            self.blk_menu = None
        elif m["mode"] in ("variable", "list"):
            self.blocks_ed.set_slot(m["slot"], item, m["block"])
            self.blk_menu = None
        elif m["mode"] == "expr":
            self._blk_insert_expr(item)

    def _blk_menu_back(self):
        """Back out one menu level (block list -> categories), or close the menu."""
        m = self.blk_menu
        if not m:
            return
        if m["mode"] == "blk":
            self._blk_open_categories()
        else:
            self.blk_menu = None

    def _blk_insert_chosen(self, type_id):
        """Insert the chosen block type at the cursor and close the menu."""
        be = self.blocks_ed
        be.insert_block(type_id)
        self.blk_slot = 0
        self.blk_menu = None
        self._blk_reveal()

    # -- custom blocks (#48: My Blocks / procedures) -------------------------
    def _blk_insert_call(self, name):
        """Insert a call to custom block `name` (args pre-filled with 0s) and close."""
        be = self.blocks_ed
        if be is not None:
            be.insert_call(name)
        self.blk_slot = 0
        self.blk_menu = None
        self._blk_reveal()

    def _blk_new_proc(self):
        """Create a fresh custom block and open the name prompt so the kid names it
        (mirrors _blk_new_variable). The define-hat + empty body appear in the outline;
        confirming renames the default. `proc` carries the just-created proc_def."""
        be = self.blocks_ed
        if be is None:
            return
        pd = be.new_proc("block")
        self.blk_menu = None
        self._blk_reveal()
        self.blk_kbd = {"kind": "proc", "text": "", "var": _blocks_mod.proc_name(pd),
                        "proc": pd, "slot_target": None, "armed": False}
        self._blk_arm_prompt()

    def _blk_open_proc_menu(self, pd):
        """Open the PROC ACTIONS menu on a define-hat: add/remove an input, rename or
        delete the whole custom block. `proc` is the target proc_def block."""
        items = [_PROC_ADD, _PROC_RENAME]
        if _blocks_mod.proc_params(pd):
            items.append(_PROC_DELP)
        items.append(_PROC_DEL)
        self.blk_menu = {"mode": "proc", "sel": 0, "top": 0, "items": items,
                         "proc": pd}

    def _blk_do_proc_action(self, item):
        """Run a chosen PROC ACTIONS item. Add/rename open a name prompt; remove-input
        and delete apply immediately."""
        be = self.blocks_ed
        m = self.blk_menu
        pd = m.get("proc") if m else None
        self.blk_menu = None
        if be is None or pd is None:
            return
        if item == _PROC_ADD:
            # name the new input, then add_param on confirm.
            self.blk_kbd = {"kind": "param", "text": "", "var": "", "proc": pd,
                            "slot_target": None, "armed": False}
            self._blk_arm_prompt()
        elif item == _PROC_RENAME:
            self.blk_kbd = {"kind": "proc", "text": "",
                            "var": _blocks_mod.proc_name(pd), "proc": pd,
                            "slot_target": None, "armed": False}
            self._blk_arm_prompt()
        elif item == _PROC_DELP:
            self.blk_status = "INPUT REMOVED" if be.remove_last_param(pd) \
                else "NO INPUTS"
        elif item == _PROC_DEL:
            if be.delete_proc(pd):
                self.blk_slot = 0
                self._blk_reveal()
                self.blk_status = "BLOCK DELETED"

    # -- slot editors --------------------------------------------------------
    def _blk_edit_slot(self, block, slot):
        """Open the right editor for a slot's type: a number slot (and an expr slot
        holding a literal) opens the on-screen number pad so the kid TYPES the value;
        an expr slot is the Scratch white oval -- type a number OR drop in a reporter
        block; variable + dropdown open a picker; text opens the keyboard."""
        be = self.blocks_ed
        t = slot["type"]
        name = slot["name"]
        if t == _blocks_mod.SLOT_DROPDOWN:
            # A dropdown opens its option list (16 colors is a lot to cycle through);
            # left/right still cycle it in place for a quick one-step tweak.
            self._blk_open_dropdown_picker(block, slot)
        elif t == _blocks_mod.SLOT_NUMBER:
            # Type the number directly (the +/- bump still lives on left/right).
            self._blk_open_number_prompt(block, name, slot)
        elif t == _blocks_mod.SLOT_TEXT:
            cur = str((block.get("p", {}) or {}).get(name, ""))
            self._blk_open_text_prompt(block, name, cur)
        elif t == _blocks_mod.SLOT_VARIABLE:
            self._blk_open_variable_picker(block, name)
        elif t == _blocks_mod.SLOT_LIST:
            self._blk_open_list_picker(block, name)
        elif t == _blocks_mod.SLOT_EXPR:
            # Scratch's editable oval: a literal opens the number pad (with an
            # "insert a block" escape hatch); a slot already holding a reporter block
            # re-opens the block chooser so the kid can swap/clear it.
            val = (block.get("p", {}) or {}).get(name)
            if _blocks_mod.is_literal_value(val):
                self._blk_open_number_prompt(block, name, slot)
            else:
                self._blk_open_expr_menu(block, name)

    def _blk_bump_number(self, block, name, d):
        # +/- nudge of a numeric slot (left/right in the outline). Works on a number
        # slot AND on an expr slot holding a numeric literal -- a quick tweak without
        # the keypad. A float keeps its fraction (e.g. 4.5 -> 5.5).
        be = self.blocks_ed
        cur = (block.get("p", {}) or {}).get(name, 0)
        if isinstance(cur, float):
            val = cur + d
        else:
            try:
                val = int(cur) + d
            except (TypeError, ValueError):
                val = d
        be.set_slot(name, val, block)

    # -- typed literal prompts (number / text), shared on-screen keypad -------
    def _blk_arm_prompt(self):
        """Neutralise the input edge that OPENED a prompt so its first frame can't
        carry the still-latched A/Enter/tap straight into commit/cancel (#29). Shared
        by every blk_kbd prompt (variable name, number, text)."""
        self.ws._set_text_mode(True)            # ASCII keyboard for typing
        self.ws.input.release_all()             # drop held buttons (host + device)
        try:
            self.ws.input._pressed = set()
            self.ws.input._released = set()
            self.ws.input._last = set()         # device InputState edge snapshot
            self.ws.input._prev = set()         # host InputState edge snapshot
        except AttributeError:
            pass
        # Seed the typed-key edge with the byte held RIGHT NOW so a held A/Enter byte
        # (last_key) isn't re-read as a fresh keystroke on the prompt's first frame.
        self.ws._ekey_prev = getattr(self.ws.input, "last_key", 0) or 0
        if self.ws.pointer is not None:
            self.ws.pointer.click = False       # a tap that opened the prompt != OK

    def _blk_open_number_prompt(self, block, name, slot):
        """Open the on-screen number pad to TYPE a literal into a number / expr slot
        (#29 blocking gap: you can now set `score to 0`, `x to 50`, compare `> 100`).
        `slot` is the catalog slot (or None when reopened from the expr chooser): an
        expr slot can ALSO hold a reporter, so the pad offers a "BLOCK" escape hatch
        for it. The pad starts EMPTY (default-on-OK is the slot's current value), so
        the kid types a fresh number."""
        cur = (block.get("p", {}) or {}).get(name)
        allow_block = self._blk_slot_is_expr(block, name, slot)
        self.blk_menu = None
        self.blk_kbd = {"kind": "num", "text": "", "cur": cur,
                        "block": block, "slot": name, "allow_block": allow_block,
                        "armed": False}
        self._blk_arm_prompt()

    def _blk_slot_is_expr(self, block, name, slot):
        """True if the named slot on `block` is an expr slot (so the number pad can
        offer 'BLOCK' to drop a reporter instead). Uses `slot` when given; else looks
        the slot up in the catalog by name."""
        if slot is not None:
            return slot["type"] == _blocks_mod.SLOT_EXPR
        d = _blocks_mod.block_def(block.get("t"))
        if d is None:
            return False
        for s in d["slots"]:
            if s["name"] == name:
                return s["type"] == _blocks_mod.SLOT_EXPR
        return False

    def _blk_open_text_prompt(self, block, name, cur):
        """Open the on-screen keyboard to TYPE a text literal into a text slot."""
        self.blk_menu = None
        self.blk_kbd = {"kind": "text", "text": str(cur or ""),
                        "block": block, "slot": name, "armed": False}
        self._blk_arm_prompt()

    def _blk_open_variable_picker(self, block, name):
        # The variable-slot picker: "+ new variable" first (so a kid can create +
        # name one right here and have the slot use it), then the enclosing custom
        # block's PARAMETERS (#48, if the cursor is inside one -- they read like
        # variables in its body), then every declared variable.
        be = self.blocks_ed
        params = be.current_params()
        items = [_NEW_VAR_ITEM] + params + [v for v in be.variables()
                                            if v not in params]
        self.blk_menu = {"mode": "variable", "sel": 0, "top": 0, "items": items,
                         "block": block, "slot": name}

    def _blk_open_list_picker(self, block, name):
        # The list-slot picker (#48): "+ new list" first, then every declared list.
        be = self.blocks_ed
        items = [_NEW_LIST_ITEM] + be.lists()
        self.blk_menu = {"mode": "list", "sel": 0, "top": 0, "items": items,
                         "block": block, "slot": name}

    # -- variable create + name (on-screen keyboard) -------------------------
    def _blk_new_variable(self):
        """Create a fresh variable (default name) and open the name-entry prompt so
        the kid types its name with the on-screen keyboard. Remembers the menu that
        was open (variable-slot picker) so that slot gets filled with the named var
        once the kid confirms (#29 Bug 2)."""
        be = self.blocks_ed
        if be is None:
            return
        m = self.blk_menu
        slot_target = None
        if m is not None and m.get("mode") == "variable":
            slot_target = (m.get("block"), m.get("slot"))
        name = be.new_var("var")
        self.blk_menu = None
        # An inline prompt: `text` is the live edit buffer (starts EMPTY so the kid
        # types a fresh name instead of appending to the default), `var` is the
        # just-created variable's CURRENT name -- confirm renames it old->typed, and a
        # blank/invalid entry keeps this default. `slot_target`, if set, is the
        # (block, slot) to fill with the final name. `kind` routes the shared keypad
        # (var / num / text); `armed` is the one-frame guard (#29): the prompt ignores
        # commit/cancel until its first input pass arms it, so the very input that
        # *selected* "+ new variable" (a held A / Enter, or the tap) can't carry into
        # the fresh prompt and instantly close it.
        self.blk_kbd = {"kind": "var", "text": "", "var": name,
                        "slot_target": slot_target, "armed": False}
        # Neutralise the triggering input so the prompt's first frame can't consume it
        # (#29): drop held buttons, wipe this frame's edges, and seed the typed-key
        # snapshot -- otherwise the still-latched A/Enter edge (or the held Enter byte
        # on the device) lands on the prompt as commit and it flashes shut.
        self._blk_arm_prompt()

    def _blk_new_list(self):
        """Create a fresh list (default name) and open the name-entry prompt (#48).
        Mirrors _blk_new_variable: if a list-slot picker was open, that slot gets
        filled with the named list on confirm; the `list` prompt kind renames it."""
        be = self.blocks_ed
        if be is None:
            return
        m = self.blk_menu
        slot_target = None
        if m is not None and m.get("mode") == "list":
            slot_target = (m.get("block"), m.get("slot"))
        name = be.new_list("list")
        self.blk_menu = None
        self.blk_kbd = {"kind": "list", "text": "", "var": name,
                        "slot_target": slot_target, "armed": False}
        self._blk_arm_prompt()

    def _blk_kbd_commit(self):
        """Confirm a prompt: a name prompt renames the var; a number prompt parses the
        typed text into a numeric literal and writes it to the slot; a text prompt
        stores the string. Falls back to the slot's current value on a blank entry."""
        be = self.blocks_ed
        k = self.blk_kbd
        if be is None or k is None:
            self.blk_kbd = None
            self.ws._set_text_mode(False)
            return
        kind = k.get("kind", "var")
        if kind == "num":
            cur = k.get("cur")
            default = cur if _blocks_mod.is_literal_value(cur) and cur is not None else 0
            val = _blocks_mod.parse_number_literal(k["text"], default)
            be.set_slot(k["slot"], val, k["block"])
            self.blk_status = "= " + str(val)
        elif kind == "text":
            be.set_slot(k["slot"], k["text"], k["block"])
            self.blk_status = "TEXT SET"
        elif kind == "list":                   # "list": rename the freshly-created list
            old = k["var"]
            applied = be.rename_list(old, k["text"])
            final = applied if applied else old
            bt = k.get("slot_target")
            if bt is not None and bt[0] is not None:
                be.set_slot(bt[1], final, bt[0])
            self.blk_status = "LIST: " + final[:12]
        elif kind == "proc":                   # "proc": rename the custom block (#48)
            old = k["var"]
            applied = be.rename_proc(old, k["text"])
            self.blk_status = "BLOCK: " + (applied if applied else old)[:12]
        elif kind == "param":                  # "param": add an input to the block (#48)
            applied = be.add_param(k.get("proc"), k["text"])
            self.blk_status = ("input: " + applied[:12]) if applied \
                else "bad input name"
        else:                                  # "var": rename the freshly-created var
            old = k["var"]
            applied = be.rename_var(old, k["text"])
            final = applied if applied else old   # blank/dup/invalid keeps the default
            bt = k.get("slot_target")
            if bt is not None and bt[0] is not None:
                be.set_slot(bt[1], final, bt[0])
            self.blk_status = "VAR: " + final[:12]
        self.blk_kbd = None
        self.ws._set_text_mode(False)

    def _blk_kbd_cancel(self):
        """Cancel a prompt: a number/text prompt just discards the edit (the slot keeps
        its old value); a name prompt keeps the default-named variable/list (it's already
        declared and usable) and fills the slot with that default."""
        be = self.blocks_ed
        k = self.blk_kbd
        if be is not None and k is not None and k.get("kind", "var") in ("var", "list"):
            bt = k.get("slot_target")
            if bt is not None and bt[0] is not None:
                be.set_slot(bt[1], k["var"], bt[0])
        self.blk_kbd = None
        self.ws._set_text_mode(False)

    def _blk_kbd_key(self, ch):
        """Apply one typed character to the prompt buffer: backspace deletes, Enter
        confirms, Esc cancels, and an allowed char appends. The allowed set depends on
        the prompt kind -- digits/'-'/'.' for a number, name-legal chars for a var,
        any printable for free text."""
        k = self.blk_kbd
        if k is None:
            return
        if ch in (8, 127):                    # backspace / delete
            k["text"] = k["text"][:-1]
            return
        if ch in (13, 10):                    # Enter -> confirm
            self._blk_kbd_commit()
            return
        if ch == 27:                          # Esc -> cancel
            self._blk_kbd_cancel()
            return
        if not (32 <= ch < 127):
            return
        c = chr(ch)
        if len(k["text"]) >= 16:              # cap so it always fits a row
            return
        kind = k.get("kind", "var")
        if kind == "num":
            # digits, a single leading '-', and at most one '.' (parse_number_literal
            # tolerates more, but filtering here keeps the on-screen buffer honest).
            if c.isdigit():
                k["text"] += c
            elif c == "-" and not k["text"]:
                k["text"] += c
            elif c == "." and "." not in k["text"]:
                k["text"] += c
        elif kind == "text":
            k["text"] += c                    # any printable char for a text literal
        else:                                  # "var": name-legal chars only
            if c.isalpha() or c.isdigit() or c in ("_", " ", "-"):
                k["text"] += c

    def _blk_open_dropdown_picker(self, block, slot):
        opts = _blocks_mod.slot_options(slot)
        self.blk_menu = {"mode": "dropdown", "sel": 0, "top": 0, "items": opts,
                         "block": block, "slot": slot["name"]}

    def _blk_open_expr_menu(self, block, name):
        """Open the expression chooser for an expr slot. Heads the list with "type a
        number" (the Scratch white oval -- a typed literal is the DEFAULT a kid wants),
        then every reporter block (operator / input / variable -- everything with an
        `expr` shape). Selecting "type a number" opens the number pad; selecting a
        block writes a fresh nested reporter into the slot."""
        ids = [_NUM_LITERAL_ITEM]
        for cat in _blocks_mod.categories():
            for bid in _blocks_mod.blocks_in_category(cat):
                if _blocks_mod.is_expr(bid):
                    ids.append(bid)
        self.blk_menu = {"mode": "expr", "sel": 0, "top": 0, "items": ids,
                         "block": block, "slot": name}

    def _blk_insert_expr(self, type_id):
        """Write a fresh expression block of `type_id` into the target expr slot."""
        m = self.blk_menu
        self.blocks_ed.set_slot(m["slot"], _blocks_mod.make_block(type_id), m["block"])
        self.blk_menu = None

    # -- save / graduate -----------------------------------------------------
    def save_blocks(self):
        """Compile-on-save the block program to blocks.json + main.py, surfacing
        SAVE_OK / a syntax problem like save_code does. Returns True on success.
        A non-SD/embedded cart just validates + applies in RAM."""
        ws = self.ws
        be = self.blocks_ed
        if not (be and ws.project.cart):
            return False
        if self.blk_graduated:
            # GRADUATED (Stage 8, spec Section 8): the kid has leveled up to code; the
            # blocks are a FROZEN, read-only render. Regenerating from them would
            # discard the diverged main.py, so SAVE refuses (the one-way door). The
            # only way back is undoing past the graduating commit (Stage 7 journal).
            self.blk_status = "LEVELED UP TO CODE"
            return False
        if self.blk_protect:
            # DATA-LOSS GUARD (#29): this cart's main.py is hand-written code that a
            # block save would replace. Refuse and tell the kid -- their code stays.
            self.blk_status = "CART HAS CODE -- NOT SAVED"
            return False
        # Drop scene objects the kid selected but never gave a block (#85/#93), so
        # blocks.json stays clean and an empty object never forces a draw_scene(). The
        # live editor keeps its entries (prune returns a copy), so the outline is
        # unchanged; a program with no objects is returned as-is (byte-identical).
        prog = _blocks_mod.prune_empty_objects(be.program)
        # Always compile-check first so the kid sees a problem before it persists.
        try:
            src = _blocks_mod.compile_blocks(prog)
        except Exception as exc:  # noqa: BLE001 -- BlockError on a corrupt tree
            self.blk_status = "BAD: " + self._err_text(exc)
            return False
        ok, msg = ws.carts_store.compile_check(src) if ws.carts_store else (True, "")
        if not ok:
            self.blk_status = "SYNTAX " + msg
            return False
        if not (ws.project.cart.get("path") and ws.can_manage and ws.carts_store):
            # nothing to persist (embedded / writes deferred): apply in RAM so RUN
            # works. A DEEP COPY, never the live tree (#93) -- the editor keeps
            # mutating `prog` in place and undo/redo rebinds it, so an aliased
            # cart["blocks"] would drift from what this save captured (mirrors
            # moy_carts.save_blocks' own snapshot copy).
            ws.project.cart["src"] = src
            ws.project.cart["blocks"] = _clone_tree(prog)
            be.dirty = False
            self.blk_status = "SAVED"
            ws.ach.note("code_save")
            return True
        try:
            status, smsg = ws._with_sd(
                lambda: ws.carts_store.save_blocks(ws.project.cart, prog))
            if status != ws.carts_store.SAVE_OK:
                self.blk_status = "CAN'T SAVE " + str(smsg)
                return False
            be.dirty = False
            self.blk_status = "SAVED"
            ws.ach.note("code_save")          # "Code Wizard": a program saved (#21)
            ws.cart_error = None
            return True
        except Exception as exc:  # noqa: BLE001
            self.blk_status = "CAN'T SAVE"
            print("Moybyte save blocks failed:", self._err_text(exc))
            return False

    def graduate_to_code(self):
        """The one-way 'graduate to code' rung: compile the block program and open
        the generated main.py in the code editor, so a kid moves from blocks to text
        on the same cart. Saves first (so blocks.json + main.py are in lockstep), then
        switches to the code view on the freshly compiled source."""
        ws = self.ws
        be = self.blocks_ed
        if not (be and ws.project.cart):
            return
        if self.blk_protect or self.blk_graduated:
            # Protected (hand-written main.py) OR already GRADUATED (Stage 8): don't
            # recompile the blocks over the kid's real/diverged main.py. Just open the
            # code editor on the EXISTING source -- "graduate" here means "go edit the
            # code you already have" (for a graduated cart, that door is one-way).
            ws.editor = None
            self.blk_menu = None
            ws.set_menu_view("code")
            return
        try:
            src = _blocks_mod.compile_blocks(be.program)
        except Exception as exc:  # noqa: BLE001
            self.blk_status = "BAD: " + self._err_text(exc)
            return
        self.save_blocks()                       # persist (best-effort) before leaving
        ws.project.cart["src"] = src                     # ensure the editor opens the latest
        ws.editor = None                         # rebuild on the new source
        self.blk_menu = None
        ws.set_menu_view("code")

    # -- block-editor input / pointer ----------------------------------------
    def _blocks_input(self):
        """Keyboard/button input for the outline + insert menu. Mirrors the other
        editors' edge-driven nav. The menu, when open, captures nav + A/B."""
        ws = self.ws
        i = ws.input
        # Blocks+Scene workspace (#93/#85): route the keyboard to the focused pane. A
        # block modal (name/number entry, insert menu) always keeps it on blocks;
        # otherwise the scene owns it while focused (e.g. typing an actor's TAG).
        if (self.blk_kbd is None and self.blk_menu is None
                and (self._ws_focus == "scene" or ws.scene_ui.tag_edit)
                and self._workspace_active()):
            ws.scene_ui._scene_input()
            return
        if self.blk_kbd is not None:
            # The variable name-entry prompt owns input: type the name (one insert per
            # physical press, edge-detected like the code editor), Enter/A confirm, B
            # cancels. last_key carries the resolved ASCII byte (text mode is on).
            # One-frame guard (#29): the FIRST input pass after the prompt opens only
            # arms it -- never commits/cancels -- so the A/Enter/tap that *selected*
            # "+ new variable" (which can still be latched/held this frame) can't carry
            # in and instantly close the prompt before the kid types a name.
            if not self.blk_kbd.get("armed"):
                self.blk_kbd["armed"] = True
                ws._ekey_prev = i.last_key   # don't read the trigger byte as a key
                return
            k = i.last_key
            if k and k != ws._ekey_prev:
                self._blk_kbd_key(k)
            ws._ekey_prev = k
            if i.pressed("a") or i.pressed("run"):
                self._blk_kbd_commit()
            elif i.pressed("b"):
                self._blk_kbd_cancel()
            return
        if self.blk_menu is not None:
            if i.pressed("up"):
                self._blk_menu_move(-1)
            if i.pressed("down"):
                self._blk_menu_move(1)
            if i.pressed("a") or i.pressed("run"):
                self._blk_menu_select()
            elif i.pressed("b"):
                self._blk_menu_back()
            return
        be = self.blocks_ed
        if be is not None and be.moving():
            # MOVE mode (#93) is modal: nav to a destination, A drops it at an insert
            # point, B cancels. (A/complete + the cancel are the only exits, so
            # BACKSPACE/B doesn't leave the editor mid-move.)
            if i.pressed("up"):
                self._blk_move_cursor(-1)
            if i.pressed("down"):
                self._blk_move_cursor(1)
            if i.pressed("a"):
                self._blk_a()
            elif i.pressed("b"):
                be.cancel_move()
                self.blk_status = "MOVE OFF"
            return
        # Host convenience (#93): Ctrl+Z / Ctrl+Y walk the in-session outline undo,
        # mirroring the code editor's shortcut (0x1A / 0x19 arrive via last_key). The
        # on-screen UNDO/REDO buttons are the touch/device affordance.
        k = getattr(i, "last_key", 0) or 0
        self._blk_ekey.undo_redo(k, self._blk_undo, self._blk_redo)
        if i.pressed("up"):
            self._blk_move_cursor(-1)
        if i.pressed("down"):
            self._blk_move_cursor(1)
        if i.pressed("right"):
            self._blk_next_slot()                # step the highlighted slot
        if i.pressed("left"):
            # left on a number slot decrements it (a quick tweak without the menu)
            self._blk_left()
        if i.pressed("a"):
            self._blk_a()
        elif i.pressed("run"):
            self.save_blocks()
        else:
            ws._leave_or_home(ws._leave_menu)

    def _blk_left(self):
        be = self.blocks_ed
        if be is None:
            return
        b = be.selected_block()
        slots = be.slots(b)
        if not slots:
            return
        slot = slots[self.blk_slot % len(slots)]
        if slot["type"] == _blocks_mod.SLOT_NUMBER:
            self._blk_bump_number(b, slot["name"], -1)
        elif slot["type"] == _blocks_mod.SLOT_DROPDOWN:
            be.cycle_dropdown(slot["name"], -1, b)
        elif slot["type"] == _blocks_mod.SLOT_EXPR:
            # quick -1 nudge on an expr slot holding a numeric literal (a block in the
            # slot is left alone -- you can't decrement an expression).
            cur = (b.get("p", {}) or {}).get(slot["name"])
            if _blocks_mod.is_literal_value(cur) and isinstance(cur, (int, float)) \
                    and not isinstance(cur, bool):
                self._blk_bump_number(b, slot["name"], -1)

    def _outline_drag(self, px, py):
        """A held vertical drag on the outline scrolls it one row per row-height
        of travel (the Settings-rows drag contract: must start on the rows, may
        continue past the edge). Keyboard nav re-clamps around the cursor as
        before -- the drag just moves the window."""
        ws = self.ws
        lay = self.block_layout
        be = self.blocks_ed
        if not ws.pointer.down or be is None or self.blk_kbd is not None \
                or self.blk_menu is not None:
            self._dragv = None
            return
        if self._dragv is None:
            area = (lay.x0, lay.y0, lay.outline_w, lay.rows * lay.row_h)
            if len(be.rows) <= lay.rows or not self._in(px, py, area):
                return
            self._dragv = py
            return
        step = max(1, lay.row_h)
        delta = self._dragv - py
        top_max = max(0, len(be.rows) - lay.rows)
        moved = False
        while delta >= step and self.blk_top < top_max:
            self.blk_top += 1
            delta -= step
            moved = True
        while delta <= -step and self.blk_top > 0:
            self.blk_top -= 1
            delta += step
            moved = True
        self._dragv = py + delta
        if moved:
            ws._dirty = True

    def _blocks_pointer(self, px, py, click):
        # SYSTEM coords + the responsive BlockLayout (#39 step 2).
        panes = self._layout_workspace()   # None unless the wide workspace is active
        # Modal overlays (full-canvas insert menu / entry prompt) own a click first.
        if click and self.blk_kbd is not None:
            self._blk_kbd_click(px, py)
            return
        if click and self.blk_menu is not None:
            self._blk_menu_click(px, py)
            return
        # Scene pane (#93/#85): a gesture that STARTS in the right pane routes into
        # the scene and keeps routing until release (a drag/pan can leave the pane).
        # Runs BEFORE the outline drag so a scene pan/move isn't also read as an
        # outline scroll. Suppressed while a block modal is open.
        if panes is not None and self.blk_kbd is None and self.blk_menu is None:
            if click:
                self._ws_scene_drag = self._in(px, py, panes[1])
            if self._ws_scene_drag:
                self._scene_pane_pointer(px, py, click)
                if not click and not self.ws.pointer.down:
                    self._ws_scene_drag = False
                return
        self._outline_drag(px, py)         # held drag scrolls the outline
        if not click:
            return
        # No modal up: the unified bar (tab ladder + PLAY + SAVE + X) claims its slice
        # FIRST (Stage-4 rollout), before any outline/action-bar tap -- SAVE here
        # dispatches to save_blocks, X exits, PLAY runs. System coords (blocks is a
        # system-canvas tab), same space the bar drew in.
        if self.ws.bar_layer.handle_bar_tap("menu", px, py):
            return
        if panes is not None:
            self._ws_focus = "blocks"      # a tap that reached the outline focuses blocks
            # Sprite list (#85/#93): tapping a chip edits that sprite's scripts (STAGE =
            # the global program); the "+" chip adds a new sprite. Scratch's sprite pane.
            for rect, tag in self._blk_roster_btns:
                if self._in(px, py, rect):
                    if tag == _ADD_SPRITE:
                        self._add_sprite()
                    else:
                        self._select_sprite(tag)
                    return
        be = self.blocks_ed
        if be is None:
            return
        lay = self.block_layout
        # Action bar: editing controls + CODE (graduate) only (SAVE/CLOSE in the bar).
        if self._in(px, py, lay.add_btn):
            self._blk_open_categories(); return
        if self._in(px, py, lay.del_btn):
            be.delete(); self.blk_slot = 0; self._blk_reveal(); return
        if self._in(px, py, lay.up_btn):
            be.move_block(-1); self._blk_reveal(); return
        if self._in(px, py, lay.dn_btn):
            be.move_block(1); self._blk_reveal(); return
        if self._in(px, py, lay.code_btn):
            self.graduate_to_code(); return
        # #93 edit cluster: "..." opens the block-actions menu, UNDO/REDO walk history.
        if self._in(px, py, lay.act_btn):
            self._blk_open_actions(); return
        if self._in(px, py, lay.undo_btn):
            self._blk_undo(); return
        if self._in(px, py, lay.redo_btn):
            self._blk_redo(); return
        # Tap a row in the outline: select it (and on a block, advance the slot
        # highlight / open the insert menu on a `+` row -- a tap == the A action).
        if self._in(px, py, lay.area()):
            ridx = self.blk_top + (py - lay.y0) // lay.row_h
            if 0 <= ridx < len(be.rows):
                if be.moving():
                    # MOVE mode (#93): a single tap on an insert point drops the block
                    # there; a tap on a block just re-homes the cursor.
                    be.cur = ridx
                    self.blk_slot = 0
                    self._blk_reveal()
                    if be.rows[ridx].kind == "insert":
                        self._blk_a()
                elif ridx == be.cur:
                    # Second tap: edit the tapped slot directly (tap the 2nd arg to
                    # edit the 2nd arg), Scratch-style -- fall back to the highlighted
                    # slot when the tap missed a value (e.g. on the block's name).
                    row = be.rows[ridx]
                    if row.kind == "block" and not row.is_else:
                        row_x = lay.x0 + row.depth * lay.indent
                        si = self._blk_slot_at_x(row.block, row_x, px)
                        if si is not None:
                            self.blk_slot = si
                    self._blk_a()                # a second tap acts (insert / edit)
                else:
                    # First tap: select the block AND highlight whichever slot was
                    # tapped, so the very next tap edits that argument.
                    be.cur = ridx
                    self.blk_slot = 0
                    row = be.rows[ridx]
                    if row.kind == "block" and not row.is_else:
                        row_x = lay.x0 + row.depth * lay.indent
                        si = self._blk_slot_at_x(row.block, row_x, px)
                        if si is not None:
                            self.blk_slot = si
                    self._blk_reveal()

    def _blk_menu_click(self, px, py):
        m = self.blk_menu
        if not m:
            return
        lay = self.block_layout
        mx, my, mw, mh = lay.menu
        if not self._in(px, py, lay.menu):
            self.blk_menu = None                 # tap outside dismisses
            return
        ridx = m["top"] + (py - (my + 16 * lay.fs)) // lay.menu_row_h
        if 0 <= ridx < len(m["items"]):
            m["sel"] = ridx
            self._blk_menu_select()

    def _blk_kbd_click(self, px, py):
        """Touch handling for the entry prompts. One-frame guard (#29): the tap that
        OPENED the prompt must not carry into this first pass and immediately commit."""
        if self.blk_kbd is not None and not self.blk_kbd.get("armed"):
            self.blk_kbd["armed"] = True
            return
        if self.blk_kbd is not None and self.blk_kbd.get("kind") == "num":
            self._blk_num_click(px, py)
            return
        # var / text prompt: DEL backspaces, OK confirms, X cancels (typing is the
        # on-screen / T-Deck keyboard).
        if self._in(px, py, _BLK_KBD_DEL):
            self._blk_kbd_key(8); return
        if self._in(px, py, _BLK_KBD_OK):
            self._blk_kbd_commit(); return
        if self._in(px, py, _BLK_KBD_X):
            self._blk_kbd_cancel(); return
        # taps inside the panel are ignored (no dismiss-on-tap-outside: a stray tap
        # shouldn't discard a half-typed name).

    def _blk_num_click(self, px, py):
        """Touch handling for the number pad: the on-screen digit grid types a literal
        (so it works touch-only / without sym keys), DEL backspaces, OK/X confirm/cancel,
        and BLOCK (expr slots) swaps in a reporter block instead."""
        k = self.blk_kbd
        # the digit grid
        for idx in range(len(_BLK_NUM_KEYS)):
            r = idx // _BLK_NUM_BPR
            c = idx % _BLK_NUM_BPR
            rx = _BLK_NUM_GX + c * _BLK_NUM_BW
            ry = _BLK_NUM_GY + r * _BLK_NUM_BH
            if self._in(px, py, (rx, ry, _BLK_NUM_BW - 3, _BLK_NUM_BH - 3)):
                self._blk_kbd_key(ord(_BLK_NUM_KEYS[idx]))
                return
        if self._in(px, py, _BLK_NUM_DEL):
            self._blk_kbd_key(8); return
        if k is not None and k.get("allow_block") and self._in(px, py, _BLK_NUM_BLOCK):
            self._blk_num_to_block(); return
        if self._in(px, py, _BLK_NUM_OK):
            self._blk_kbd_commit(); return
        if self._in(px, py, _BLK_NUM_X):
            self._blk_kbd_cancel(); return

    def _blk_num_to_block(self):
        """From the number pad, switch to dropping a reporter BLOCK into the slot
        (the Scratch white-oval -> drop-a-block move). Discards the typed number and
        opens the expr chooser on the same slot."""
        k = self.blk_kbd
        if k is None:
            return
        block, name = k["block"], k["slot"]
        self.blk_kbd = None
        self.ws._set_text_mode(False)
        self._blk_open_expr_menu(block, name)

    # -- block editor drawing (#29 Part 2) -----------------------------------

    def _gbtn(self, kind, label, rect, fill, cv):
        # #93 icon pass -- one shared body, chrome._gbtn.
        _chrome_gbtn(self.ws, self._NAMES, kind, label, rect, fill, cv)

    def _draw_blocks(self, dt=0):
        """The structured outline: a title bar, a scrolling list of Scratch-style
        colored block rows (the flattened script with the cursor highlighted and the
        insert points shown as `+`), and a bottom action bar. Drawn with the indexed
        API + petme128 font only, so host == device. Responsive (#39 step 2): on the
        SYSTEM canvas at native size, geometry from BlockLayout (verbatim at 320x240/
        1x) -- a bigger panel shows MORE rows + WIDER blocks, a bigger font scales it.

        On a wide canvas (#93/#85) the outline is BOUND to the left pane and an
        interactive scene pane is drawn on the right (Scratch-style objects-right);
        below the gate it's blocks-only, byte-identical to before (T-Deck path)."""
        ws = self.ws
        NAMES = self._NAMES
        cv = ws.sys_canvas
        panes = self._layout_workspace()     # split + bound both layouts, or None
        lay = self.block_layout
        fs = lay.fs
        be = self.blocks_ed
        # Phase 3 (visual identity v1): the warm tool surface + dark ink on the
        # shelf tiers; the frozen dark-blue body at 320x240, byte-identical. The
        # block PIECES keep their own colorful language (self-backed rows).
        th = ws.theme_colors
        light = (not lay._base) or ws.light_chrome()  # tokens on every responsive tier; _base stays frozen only in DARK chrome
        cv.cls(th["surface"] if light else NAMES["dark_blue"])
        # The old "BLOCKS <title>" row was dissolved into the unified bar (Stage-4
        # rollout). Just below the bar sits a thin hint/status strip: the kid-facing
        # hint for surprising blocks on the left, the SAVE-status / "CODE LOCKED"
        # notice on the right (a dirty * rides the status). A GRADUATED cart (Stage 8)
        # replaces that whole strip with a celebration banner -- it's a read-only
        # render now, so there's no edit hint/status to show, just the good news.
        if self.blk_graduated:
            self._draw_grad_banner()
        else:
            if self.blk_status:
                cv.print(self.blk_status[:14], lay.status_x, lay.hint_y,
                         th["author"] if light else NAMES["yellow"], 1)
            elif be is not None and be.dirty:
                cv.print("*", lay.status_x, lay.hint_y,
                         th["author"] if light else NAMES["yellow"], 1)
        if be is None:
            return
        # A kid-facing hint for the surprising blocks (forever-is-bounded / wait).
        # Suppressed on a graduated cart -- the banner owns that row.
        # In the workspace the hint row becomes the SPRITE LIST (#85/#93) -- which
        # sprite you're editing matters more there than a block hint. Below the gate
        # (blocks-only) the kid hint shows as before.
        if panes is not None and not self.blk_graduated:
            self._draw_sprite_list(lay)
        else:
            self._blk_roster_btns = []
            hint = None if self.blk_graduated else self._blk_hint()
            if hint:
                # truncate to leave the right end for the status slot
                hmax = max(8, (lay.status_x - lay.x0) // lay.cell - 1)
                cv.print(hint[:hmax], lay.x0, lay.hint_y,
                         th["ink_dim"] if light else NAMES["light_grey"], 1)
        rows = be.rows
        for vi in range(lay.rows):
            ridx = self.blk_top + vi
            if ridx >= len(rows):
                break
            self._draw_blk_row(rows[ridx], vi, ridx == be.cur)
        # scroll cue
        _ui.scroll_cues(
            cv, (lay.x0 + lay.outline_w - 8 * fs, lay.y0),
            (lay.x0 + lay.outline_w - 8 * fs, lay.y0 + (lay.rows - 1) * lay.row_h),
            self.blk_top > 0, self.blk_top + lay.rows < len(rows),
            th["ink"] if light else NAMES["white"])
        # action bar: editing controls + the #29 graduation action only (SAVE/CLOSE
        # moved to the unified bar).
        ws._icon_btn("plus", "ADD", lay.add_btn, NAMES["green"], cv)
        ws._btn("DEL", lay.del_btn, NAMES["red"], cv)
        ws._btn("^", lay.up_btn, NAMES["indigo"], cv)
        ws._btn("v", lay.dn_btn, NAMES["indigo"], cv)
        ws._icon_btn("code", "CODE", lay.code_btn, NAMES["dark_purple"], cv)
        # #93 edit cluster: "..." (block-actions menu) + UNDO/REDO. Undo/redo dim to
        # dark_grey when the stack is empty so their availability reads at a glance;
        # "..." glows yellow while a MOVE is armed (the "TAP A + SPOT" state).
        act_col = NAMES["yellow"] if be.moving() else NAMES["blue"]
        ws._btn("...", lay.act_btn, act_col, cv)
        self._gbtn("undo", "UNDO", lay.undo_btn,
                   NAMES["indigo"] if be.can_undo() else NAMES["dark_grey"], cv)
        self._gbtn("redo", "REDO", lay.redo_btn,
                   NAMES["indigo"] if be.can_redo() else NAMES["dark_grey"], cv)
        # The interactive SCENE pane (#93/#85): objects on the right, their
        # programming on the left. Only on a wide canvas -- below the gate the tab
        # is blocks-only (unchanged, T-Deck included).
        if panes is not None:
            self._draw_scene_pane(panes[1])
        # The unified zoned bar (tab ladder + PLAY + SAVE + X), drawn BEFORE the modal
        # insert menu / entry prompt so those still sit on top (Stage-4 rollout).
        ws.bar_layer._draw_status_strip("menu")
        if self.blk_menu is not None:
            self._draw_blk_menu()
        if self.blk_kbd is not None:
            self._draw_blk_kbd()

    def _draw_grad_banner(self):
        """The celebration banner for a GRADUATED cart (Stage 8, spec Section 8):
        'YOU LEVELED UP TO CODE!' in place of the edit hint/status strip. Graduation
        is a one-way door the Editor CELEBRATES rather than apologizes for; the
        blocks below render read-only. Drawn ONLY when blk_graduated, so a
        non-graduated blocks screen is pixel-identical (the golden star_catcher
        blocks screen -- code-only, never graduated -- is untouched). Indexed API +
        petme128 only (host == device), responsive via BlockLayout."""
        NAMES = self._NAMES
        cv = self.ws.sys_canvas
        lay = self.block_layout
        fs = lay.fs
        x = lay.x0
        y = lay.hint_y - 2 * fs
        w = lay.outline_w
        h = 11 * fs
        _ui.dialog(cv, (x, y, w, h), ring=NAMES["yellow"])
        msg = "YOU LEVELED UP TO CODE!"
        mmax = max(8, w // lay.cell - 1)
        cv.print(msg[:mmax], x + 2 * fs, lay.hint_y, NAMES["yellow"], 1)

    def _draw_blk_kbd(self):
        """Render whichever entry prompt is up: the number pad (kind == 'num') or the
        variable-name / text prompt. Indexed API + petme128 only (host == device)."""
        if self.blk_kbd.get("kind") == "num":
            self._draw_blk_num()
            return
        NAMES = self._NAMES
        # The prompt is part of the EDITOR, so it draws on the SYSTEM canvas -- NOT
        # ws.canvas (the 320x240 game canvas). They're the same object at 320x240 (so
        # the device/small tier was fine), but on a bigger canvas (the Blocks+Scene
        # workspace, P4, windowed web) the game canvas is a hidden 320x240 buffer, so
        # drawing here left the modal INVISIBLE -- the editor looked frozen (#85/#93).
        cv = self.ws.sys_canvas
        x, y, w, h = _BLK_KBD
        _ui.dialog(cv, (x, y, w, h))
        kind = self.blk_kbd.get("kind")
        is_text = kind == "text"
        title = {"text": "TYPE SOME TEXT", "proc": "NAME YOUR BLOCK",
                 "param": "NAME AN INPUT"}.get(kind, "NAME YOUR VARIABLE")
        cv.print(title, x + 10, y + 8, NAMES["white"], 1)
        cv.print("type, then OK", x + 10, y + 18, NAMES["light_grey"], 1)
        # the live edit buffer in a field with a blinking-ish caret bar
        fx, fy, fw = x + 10, y + 30, w - 20
        txt = (self.blk_kbd.get("text") or "")[:24]
        # empty buffer: the default name shows as a dim placeholder (OK keeps it)
        ph = "" if is_text else str(self.blk_kbd.get("var", ""))[:24]
        _ui.text_field(cv, (fx, fy, fw, 14), txt, ph)
        self.ws._btn("DEL", _BLK_KBD_DEL, NAMES["red"], cv)
        self.ws._btn("OK", _BLK_KBD_OK, NAMES["green"], cv)
        self.ws._btn("X", _BLK_KBD_X, NAMES["dark_grey"], cv)

    def _draw_blk_num(self):
        """The number-entry pad: a live value field + an on-screen digit grid (tap a
        number in, or type it) + DEL/OK/X and a BLOCK swap for expr slots (#29)."""
        NAMES = self._NAMES
        cv = self.ws.sys_canvas    # the EDITOR's canvas, not the 320x240 game canvas
                                   # (see _draw_blk_kbd -- same invisible-modal bug, #85/#93)
        k = self.blk_kbd
        x, y, w, h = _BLK_NUM
        _ui.dialog(cv, (x, y, w, h))
        cv.print("TYPE A NUMBER", x + 10, y + 6, NAMES["white"], 1)
        # live value field; an empty buffer shows the slot's current value, dim (OK keeps it)
        fx, fy, fw = x + 10, y + 18, w - 20
        txt = (k.get("text") or "")[:30]
        cur = k.get("cur")
        ph = str(cur) if _blocks_mod.is_literal_value(cur) and cur is not None else "0"
        _ui.text_field(cv, (fx, fy, fw, 14), txt, ph[:30])
        # the digit grid (0-9 . -)
        for idx in range(len(_BLK_NUM_KEYS)):
            r = idx // _BLK_NUM_BPR
            c = idx % _BLK_NUM_BPR
            rx = _BLK_NUM_GX + c * _BLK_NUM_BW
            ry = _BLK_NUM_GY + r * _BLK_NUM_BH
            self.ws._btn(_BLK_NUM_KEYS[idx],
                      (rx, ry, _BLK_NUM_BW - 3, _BLK_NUM_BH - 3), NAMES["indigo"], cv)
        self.ws._btn("DEL", _BLK_NUM_DEL, NAMES["red"], cv)
        if k.get("allow_block"):
            self.ws._btn("BLOCK", _BLK_NUM_BLOCK, NAMES["green"], cv)
        self.ws._btn("OK", _BLK_NUM_OK, NAMES["green"], cv)
        self.ws._btn("X", _BLK_NUM_X, NAMES["dark_grey"], cv)

    def _blk_hint(self):
        be = self.blocks_ed
        b = be.selected_block() if be is not None else None
        if b is not None:
            return _BLK_HINTS.get(b.get("t"))
        return None

    def _blk_ink(self, fill_idx):
        """Black or white text, whichever reads on the fill colour (luminance)."""
        try:
            r, g, b = self.ws.sys_canvas.palette[fill_idx]
        except Exception:  # noqa: BLE001 - a non-RGB palette entry
            return self._NAMES["white"]
        return self._NAMES["black"] if (r * 30 + g * 59 + b * 11) > 13000 \
            else self._NAMES["white"]

    def _is_hat_block(self, b):
        d = _blocks_mod.block_def(b.get("t"))
        return bool(d) and d.get("shape") == _blocks_mod.SHAPE_HAT

    def _is_body_block(self, b):
        """A block that WRAPS children (hat / c-block / proc def) -- gets a Scratch mouth."""
        tid = b.get("t")
        return (_blocks_mod.is_cblock(tid) or _blocks_mod.is_def(tid)
                or self._is_hat_block(b))

    def _blk_body_span(self, ridx, depth):
        """How many rows after `ridx` belong to its body (depth deeper than `depth`)."""
        rows = self.blocks_ed.rows
        j = ridx + 1
        while j < len(rows) and rows[j].depth > depth:
            j += 1
        return j - ridx - 1

    def _draw_blk_row(self, row, vi, is_cursor):
        NAMES = self._NAMES
        cv = self.ws.sys_canvas
        lay = self.block_layout
        fs = lay.fs
        cell = lay.cell
        rh = lay.row_h
        be = self.blocks_ed
        th = self.ws.theme_colors
        light = (not lay._base) or self.ws.light_chrome()
        bg = th["surface"] if light else NAMES["dark_blue"]
        y = lay.y0 + vi * rh
        x = lay.x0 + row.depth * lay.indent
        w = lay.outline_w - row.depth * lay.indent
        if row.kind == "insert":
            # a slim Scratch-style drop slot (a notch between stacked blocks).
            c = NAMES["yellow"] if is_cursor else NAMES["dark_grey"]
            cv.rect(x + 2 * fs, y + rh // 2 - fs, 12 * fs, 2 * fs, c)
            if is_cursor:
                cv.print("+ add a block", x + 16 * fs, y + 3 * fs, NAMES["light_grey"], 1)
            else:
                cv.print("+", x + 4 * fs, y + 3 * fs, c, 1)
            return
        b = row.block
        cat = self._blk_block_cat(b)
        fill = NAMES[_blocks_mod.CATEGORY_COLOR.get(cat, "dark_grey")]
        if row.is_else:
            fill = NAMES["orange"]
        label = self._blk_row_text(b, row.is_else)
        is_body = (not row.is_else) and self._is_body_block(b)
        # tile width: fit the content (a discrete Scratch block, not a full-width bar).
        tw = min(w - 2 * fs, (len(label) + 2) * cell + 2 * fs)
        ty, tht = y + fs, rh - 2 * fs
        # C-block/hat MOUTH: a coloured spine down the visible body + a bottom lip,
        # so the nested blocks read as sitting INSIDE the wrapper (Scratch's C-shape).
        if is_body:
            span = self._blk_body_span(self.blk_top + vi, row.depth)
            vis = min(span, lay.rows - vi - 1)
            if vis > 0:
                sy = y + rh
                sh = vis * rh
                cv.rect(x, sy, 3 * fs, sh, fill)                        # left spine
                cv.rect(x, sy + sh - fs, min(w - 2 * fs, 14 * fs), fs, fill)  # bottom lip
        # the block tile + a 1px bevel (white top highlight / black bottom shadow).
        cv.rect(x, ty, tw, tht, fill)
        cv.rect(x, ty, tw, fs, NAMES["white"] if not is_cursor else NAMES["yellow"])
        cv.rect(x, ty + tht - fs, tw, fs, NAMES["black"])
        cv.print(label[:(tw - 4 * fs) // cell], x + 3 * fs, y + 4 * fs,
                 self._blk_ink(fill), 1)
        # rounded corners: knock the 4 corner pixels back to the background.
        for cx, cy in ((x, ty), (x + tw - fs, ty),
                       (x, ty + tht - fs), (x + tw - fs, ty + tht - fs)):
            cv.rect(cx, cy, fs, fs, bg)
        # a cursor ring around the whole tile.
        if is_cursor:
            cv.rectb(x, ty, tw, tht, NAMES["yellow"])
        # highlight the selected block's active slot with a small caret under it
        if is_cursor and not row.is_else:
            self._draw_blk_slot_caret(b, x, y)

    def _draw_blk_slot_caret(self, b, x, y):
        slots = self.blocks_ed.slots(b)
        if not slots:
            return
        # underline the active slot's value within the rendered label so the kid
        # sees which one A/left/right will edit.
        NAMES = self._NAMES
        cv = self.ws.sys_canvas
        lay = self.block_layout
        fs = lay.fs
        cell = lay.cell
        si = self.blk_slot % len(slots)
        col0 = self._blk_slot_text_col(b, si)
        if col0 is None:
            return
        sval = self._blk_slot_display(b, slots[si])
        cv.rect(x + 4 * fs + col0 * cell, y + 12 * fs,
                max(cell, len(sval) * cell), fs, NAMES["yellow"])

    def _blk_block_cat(self, b):
        d = _blocks_mod.block_def(b.get("t"))
        return d["category"] if d else "control"

    def _blk_row_text(self, b, is_else=False):
        """Render a block's inline label: the catalog template with each {slot}
        replaced by its value's compact display (a literal, a color/button name, a
        variable, or a compact expression). Mirrors the Scratch-style inline look."""
        if is_else or b.get("t") == _blocks_mod.ELSE_MARKER:
            return "else"
        d = _blocks_mod.block_def(b.get("t"))
        if d is None:
            return str(b.get("t"))
        # Program-aware label + slots (#48): proc_def reads "define NAME p1 p2" and a
        # call reads "NAME {arg0} {arg1}" (one hole per proc parameter); every other
        # block is its static catalog label. The rest of the inline fill is unchanged.
        prog = self.blocks_ed.program if self.blocks_ed is not None else {}
        out = ""
        tmpl = _blocks_mod.block_label(prog, b)
        i = 0
        n = len(tmpl)
        slot_by_name = {}
        for s in _blocks_mod.block_slots(prog, b):
            slot_by_name[s["name"]] = s
        while i < n:
            ch = tmpl[i]
            if ch == "{":
                j = tmpl.find("}", i)
                if j < 0:
                    out += ch
                    i += 1
                    continue
                name = tmpl[i + 1:j]
                slot = slot_by_name.get(name)
                out += self._blk_slot_display(b, slot) if slot else name
                i = j + 1
            else:
                out += ch
                i += 1
        return out

    def _blk_slot_text_col(self, b, si):
        """The character column where slot `si`'s value starts in the rendered row
        label (so the caret underlines the right run). None if it can't be found."""
        prog = self.blocks_ed.program if self.blocks_ed is not None else {}
        bslots = _blocks_mod.block_slots(prog, b)
        if si >= len(bslots):
            return None
        target = bslots[si]["name"]
        tmpl = _blocks_mod.block_label(prog, b)
        col = 0
        i = 0
        n = len(tmpl)
        while i < n:
            ch = tmpl[i]
            if ch == "{":
                j = tmpl.find("}", i)
                if j < 0:
                    col += 1
                    i += 1
                    continue
                name = tmpl[i + 1:j]
                slot = None
                for s in bslots:
                    if s["name"] == name:
                        slot = s
                        break
                disp = self._blk_slot_display(b, slot) if slot else name
                if name == target:
                    return col
                col += len(disp)
                i = j + 1
            else:
                col += 1
                i += 1
        return None

    def _blk_slot_at_x(self, b, row_x, px):
        """Which slot of block `b` the tap `px` falls on (its value's character run in
        the rendered row), or None if the tap missed every slot. Lets a kid tap the
        SECOND argument (or any) directly, Scratch-style, instead of stepping with the
        right-arrow. `row_x` is the row's left edge; the label prints at row_x + 3*fs."""
        lay = self.block_layout
        be = self.blocks_ed
        if be is None:
            return None
        slots = be.slots(b)
        if not slots:
            return None
        col = (px - (row_x + 3 * lay.fs)) // lay.cell
        if col < 0:
            return None
        for si in range(len(slots)):
            c0 = self._blk_slot_text_col(b, si)
            if c0 is None:
                continue
            disp = self._blk_slot_display(b, slots[si])
            if c0 <= col < c0 + max(1, len(disp)):
                return si
        return None

    def _blk_slot_display(self, b, slot):
        """A compact string for one slot's current value (for the inline row)."""
        name = slot["name"]
        val = (b.get("p", {}) or {}).get(name)
        t = slot["type"]
        if t == _blocks_mod.SLOT_EXPR:
            return self._blk_expr_display(val)
        if t == _blocks_mod.SLOT_TEXT:
            return '"' + str(val) + '"'
        if val is None:
            return "0" if t == _blocks_mod.SLOT_NUMBER else "?"
        return str(val)

    def _blk_expr_display(self, val):
        """A compact, brace-free rendering of an expression slot value: a literal
        stays itself; an expression block renders its own label recursively (so a
        nested `(x + 1)` reads inline)."""
        if val is None:
            return "0"
        if isinstance(val, dict):
            return self._blk_row_text(val)
        if isinstance(val, str):
            return '"' + val + '"'
        return str(val)

    def _draw_blk_menu(self):
        """The modal insert/picker menu over the frozen outline: a titled panel with
        a scrolling list of choices (categories, blocks, dropdown options, or
        variables). Navigated up/down + A; B backs out. On the SYSTEM canvas; panel +
        rows scale with the layout/font (#39 step 2)."""
        NAMES = self._NAMES
        cv = self.ws.sys_canvas
        lay = self.block_layout
        fs = lay.fs
        cell = lay.cell
        mrh = lay.menu_row_h
        m = self.blk_menu
        mx, my, mw, mh = lay.menu
        cv.rect(mx, my, mw, mh, NAMES["black"])
        cv.rectb(mx, my, mw, mh, NAMES["yellow"])
        titles = {"cat": "PICK A KIND", "blk": "PICK A BLOCK",
                  "dropdown": "PICK ONE", "variable": "PICK A VARIABLE",
                  "expr": "PICK A VALUE", "actions": "BLOCK ACTIONS",
                  "proc": "EDIT THIS BLOCK"}
        cv.print(titles.get(m["mode"], "PICK"), mx + 6 * fs, my + 4 * fs, NAMES["yellow"], 1)
        items = m["items"]
        if not items:
            cv.print("NOTHING HERE YET", mx + 8 * fs, my + 22 * fs, NAMES["light_grey"], 1)
            cv.print("B = BACK", mx + 8 * fs, my + mh - 12 * fs, NAMES["light_grey"], 1)
            return
        for vi in range(lay.menu_rows):
            ridx = m["top"] + vi
            if ridx >= len(items):
                break
            y = my + 16 * fs + vi * mrh
            sel = ridx == m["sel"]
            if sel:
                cv.rect(mx + 3 * fs, y, mw - 6 * fs, mrh - fs, NAMES["indigo"])
            # color the category/block swatch chips so the look matches the outline
            chip = self._blk_menu_chip(ridx)
            if chip is not None:
                cv.rect(mx + 5 * fs, y + 2 * fs, 8 * fs, mrh - 5 * fs, chip)
            label = self._blk_menu_label(ridx)
            cv.print(label[:(mw - 24 * fs) // cell], mx + 16 * fs, y + 3 * fs,
                     NAMES["white"] if sel else NAMES["light_grey"], 1)
        cv.print("B = BACK", mx + 6 * fs, my + mh - 12 * fs, NAMES["light_grey"], 1)

    def _blk_menu_chip(self, ridx):
        """The category-color chip for menu row `ridx` (categories + block lists);
        None for plain pickers (dropdown/variable rows have no category color)."""
        NAMES = self._NAMES
        m = self.blk_menu
        item = m["items"][ridx]
        if item == _NUM_LITERAL_ITEM:
            return NAMES["white"]      # the white editable-oval look (Scratch)
        # My Blocks rows (#48): "+ new block" and every "call NAME" carry the category
        # color so the palette reads consistently.
        if item == _NEW_PROC_ITEM or item[:len(_CALL_PREFIX)] == _CALL_PREFIX:
            return NAMES[_blocks_mod.CATEGORY_COLOR.get(_blocks_mod.CAT_PROCS,
                                                        "dark_grey")]
        if m["mode"] == "cat":
            return NAMES[_blocks_mod.CATEGORY_COLOR.get(item, "dark_grey")]
        if m["mode"] in ("blk", "expr"):
            d = _blocks_mod.block_def(item)
            if d:
                return NAMES[_blocks_mod.CATEGORY_COLOR.get(d["category"], "dark_grey")]
        return None

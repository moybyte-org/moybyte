# Map (grep -n a name to jump there):
#   BlockLayout                     the page's geometry: the outline's rows and the foot
#   BlockEditorUI                   the structured-outline UI over a cart's block program
#   BlockEditorUI.build             build the BlockEditor over the cart's program
#   BlockEditorUI.save_blocks       compile-on-save to the cart's source
#   BlockEditorUI.graduate_to_code  the one-way graduation to code
"""The block editor's UI layer (issue #29 Part 2): the structured outline of a
cart's block program, its insert menu, and the number/name/text prompts.

Two layers, the same split as the console's other sub-editors:
  * BlockEditor (runtime/editors.py) -- the pure tree-edit core: flattens the
    program into cursor-addressable rows, inserts/deletes/moves blocks, sets
    slot values. No canvas, no input.
  * BlockEditorUI (here) -- the UI: the outline's taps, drags and keys, the
    menus' and the prompts' state, save and graduation. The page on the
    verbs, `runtime/editor_blocks.py`, draws the outline and the menus, on
    the handheld and in a desk Editor's Blocks pane alike; the prompts are
    drawn here over it.

One instance lives on Workstation (`ws.block_ui`), with a back-reference to
it for the primitives it shares with the rest of the console (canvas, input,
cart, carts_store, _with_sd, set_menu_view, _set_text_mode, _leave_or_home,
_btn, editor, pointer, sys_canvas). `NAMES` / `_err_text` are injected at
construction instead of imported back from console.py, which would be a
circular import.
"""

try:
    import ui as _ui
    import ui_draw as _ui_draw
except ImportError:  # pragma: no cover - host fallback
    from runtime import ui as _ui
    from runtime import ui_draw as _ui_draw
_in = _ui.rect_in   # one hit-test (ui.rect_in)


from editors import (BlockEditor, KeyEdge, TextEntry, TE_COMMIT, TE_CANCEL, _clone_tree,
                     text_key as _text_key)
# The block vocabulary/compiler (#29). Mirrors console.py's own import (see its
# comment there): bare `blocks` on the device (frozen top-level) and once
# host_app has aliased it on the host, or `runtime.blocks` when a test loads
# this module directly without that alias.
try:
    import blocks as _blocks_mod
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import blocks as _blocks_mod

try:
    from widgets import arm_prompt as _arm_prompt
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.widgets import arm_prompt as _arm_prompt

try:
    from layout_base import LayoutBase, BASE_W as _BASE_W, BASE_H as _BASE_H
    import layout_base as _layout_base
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.layout_base import LayoutBase, BASE_W as _BASE_W, BASE_H as _BASE_H
    from runtime import layout_base as _layout_base

# The page (editor_blocks.py): the objects' tab row over the outline, a row's
# pitch and a depth's indent, and the foot under it.
HAND_HEAD = 28
HAND_ROW = 22
HAND_INDENT = 14
HAND_FOOT = 34


# A block's label cells are the print font's glyph box, never under this.
_FONT_W = 8

# The insert menu (category list, then the block list for the chosen category,
# then for some slots a small option picker), the page's popover; up/down + A
# walk it, B backs out one level, scrolling by this many rows.
_BLK_MENU_ROWS = 10
def _strip(ws, lay, keys, desk):
    """`keys` on a band of their own at the foot of layout `lay`'s content,
    over the page's foot (a dialog's keys; touch alone shows none)."""
    if _ui_draw.hint_kind() == "touch":
        return
    b = _ui_draw.metrics("value_row")[17 if desk else 16] * lay.fs
    ws.sys_canvas.rect(lay.cx, lay.cy + lay.ch - b, lay.cw, b, ws.theme_colors["panel"])
    _ui.at(lay.cx, lay.cy + lay.ch - b, lay.cw, b)
    _ui.hints(keys)


# The entry prompts (`_draw_blk_kbd`): their titles by kind, the line under a
# name's, and the field's caption by kind (a number's is its slot's).
_KBD_TITLES = {"num": "Type a number", "text": "Type some text", "proc": "Name your block",
               "param": "Name an input"}
_KBD_NAME_RULE = "Use letters, numbers and underscores."
_KBD_CAPTIONS = {"text": "Text"}
_KBD_SUB = {"tone": "dim", "wrap": True, "lines": 2}
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


def _blk_num_allow(c, text):
    """A number literal: digits, one leading '-' and at most one '.'
    (parse_number_literal tolerates more; the filter keeps the buffer honest)."""
    return c.isdigit() or (c == "-" and not text) or (c == "." and "." not in text)


def _blk_name_allow(c, text):
    """A variable / list / block / input name: name-legal characters only."""
    return c.isalpha() or c.isdigit() or c in ("_", " ", "-")


# Which characters each prompt kind types; a kind not listed is a NAME.
_BLK_KBD_ALLOW = {"num": _blk_num_allow, "text": None}

class BlockLayout(LayoutBase):
    """The block editor's geometry, from the SYSTEM canvas size (w, h) and
    font scale: the page `editor_blocks.BlocksPage` draws, on the handheld
    and in a desk Editor's Blocks pane alike -- the objects' tabs, the
    outline under them (a row's pitch, a depth's indent, the visible rows)
    and the foot under it. A block's label cells are the print font's glyph
    box (a tap on a row finds its slot by them)."""

    def __init__(self, w=_BASE_W, h=_BASE_H, font_scale=1,
                 chrome_scale=None):
        LayoutBase.__init__(self, w, h, font_scale,
                            chrome_scale=chrome_scale)
        cx, cy, cw, ch = self.cx, self.cy, self.cw, self.ch
        self.cell = max(_FONT_W, _ui_draw.font("ui")[1]) * self.fs
        self.x0 = cx + 8
        self.y0 = cy + HAND_HEAD
        self.row_h = HAND_ROW
        self.indent = HAND_INDENT
        self.outline_w = cw - 16
        self.bar_h = HAND_FOOT
        self.bar_y = cy + ch - HAND_FOOT
        self.rows = max(3, (self.bar_y - 2 - self.y0) // self.row_h)
        self.menu_rows = _BLK_MENU_ROWS

    def area(self):
        return (self.x0, self.y0, self.outline_w, self.rows * self.row_h)


class BlockEditorUI:
    """The structured-outline UI over a cart's block program: cursor + insert menu
    + inline prompts (draw + input/pointer). One instance lives on Workstation
    (`self.block_ui`), built once in Workstation.__init__; `ws.block_ui.build()` is
    called lazily from `set_menu_view("blocks")` the first time a cart's block
    editor is opened, exactly like the pre-extraction code did inline."""

    def __init__(self, ws, names, err_text, clamp_scroll):
        self.ws = ws
        # Injected instead of imported back from console.py (see module docstring):
        # console.py builds this instance, so `import console` here would be a real
        # circular import at BlockEditorUI's own module-load time. `clamp_scroll` is
        # the tiny shared scrolloff helper (#dedup, stage 1) chrome holds --
        # injected here instead of duplicated.
        self._NAMES = names
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
        self._page = None             # the page on the verbs (editor_blocks.py)
        self._held = _ui_draw.HeldPage()   # the page under blk_kbd (`_draw_blk_kbd`)

    def page(self):
        """The Blocks page on the verbs, built at its first use."""
        if self._page is None:
            try:
                from editor_blocks import BlocksPage
            except ImportError:  # pragma: no cover - host fallback
                from runtime.editor_blocks import BlocksPage
            self._page = BlocksPage(self, _ui_draw.AppHost("editor_blocks"))
        return self._page

    def relayout(self, w, h, fs, cs=None):
        """Rebuild the responsive layout from the live system-canvas size + the
        effective font scale (mirrors Workstation._relayout), and re-clamp the
        outline scroll if an editor is already open."""
        self.block_layout = BlockLayout(w, h, fs, chrome_scale=cs)
        if self.blocks_ed is not None:
            self._blk_reveal()

    # -- Blocks + Scene side-by-side workspace (#93/#85) ----------------------

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

    def _select_sprite(self, tag):
        """Edit a sprite's scripts (tag) or the global Stage (tag None), and mirror the
        pick onto the stage -- selecting the first actor of that tag, like Scratch
        highlighting the chosen sprite. The two-way twin of _sync_target_from_scene."""
        be = self.blocks_ed
        if be is None:
            return
        be.set_target(tag)
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

    def on_leave(self):
        """Called from Workstation._leave_menu() when menu_view == "blocks"."""
        self.blk_menu = None
        self.blk_kbd = None
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
        m["sel"] = max(0, min(len(m["items"]) - 1, m["sel"] + d))
        m["top"] = self._clamp_scroll(m["top"], m["sel"],
                                      self.block_layout.menu_rows, len(m["items"]))

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
        self._blk_open_kbd("proc", var=_blocks_mod.proc_name(pd), proc=pd,
                           slot_target=None)

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
            self._blk_open_kbd("param", var="", proc=pd, slot_target=None)
        elif item == _PROC_RENAME:
            self._blk_open_kbd("proc", var=_blocks_mod.proc_name(pd), proc=pd,
                               slot_target=None)
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

    # -- the entry prompts (name / number / text), one on-screen keypad -------
    def _blk_open_kbd(self, kind, text="", **fields):
        """Open an entry prompt as `self.blk_kbd`: `kind` routes the keypad
        (num / text / var / list / proc / param) and picks which characters
        its TextEntry accepts; `fields` are the prompt's own facts (the slot or
        variable it edits). Opened through widgets.arm_prompt with a GUARDED
        edge (#29): the tap/key that opened it is never its first keystroke."""
        entry = TextEntry(16, _BLK_KBD_ALLOW.get(kind, _blk_name_allow))
        _arm_prompt(self.ws)
        seed = getattr(self.ws.input, "last_key", 0) or 0
        entry.open(text, seed, guard=True)
        host = _ui_draw.AppHost()
        host.key = seed                # the byte that opened it types nothing
        fields["kind"] = kind
        fields["entry"] = entry
        fields["host"] = host
        self.blk_kbd = fields

    def cancel_prompt(self):
        """Close the open entry prompt as its Cancel does (the T-Deck's Mic,
        `Workstation.cancel_field`); False where none is open."""
        if self.blk_kbd is None:
            return False
        self._blk_kbd_cancel()
        return True

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
        self._blk_open_kbd("num", cur=cur, block=block, slot=name,
                           allow_block=allow_block)

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
        self._blk_open_kbd("text", str(cur or ""), block=block, slot=name)

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
        # The entry starts EMPTY so the kid types a fresh name instead of
        # appending to the default; `var` is the just-created variable's CURRENT
        # name: confirm renames it old->typed, and a blank/invalid entry keeps
        # the default. `slot_target`, if set, is the (block, slot) to fill with
        # the final name.
        self._blk_open_kbd("var", var=name, slot_target=slot_target)

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
        self._blk_open_kbd("list", var=name, slot_target=slot_target)

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
            val = _blocks_mod.parse_number_literal(k["entry"].text, default)
            be.set_slot(k["slot"], val, k["block"])
            self.blk_status = "= " + str(val)
        elif kind == "text":
            be.set_slot(k["slot"], k["entry"].text, k["block"])
            self.blk_status = "TEXT SET"
        elif kind == "list":                   # "list": rename the freshly-created list
            old = k["var"]
            applied = be.rename_list(old, k["entry"].text)
            final = applied if applied else old
            bt = k.get("slot_target")
            if bt is not None and bt[0] is not None:
                be.set_slot(bt[1], final, bt[0])
            self.blk_status = "LIST: " + final[:12]
        elif kind == "proc":                   # "proc": rename the custom block (#48)
            old = k["var"]
            applied = be.rename_proc(old, k["entry"].text)
            self.blk_status = "BLOCK: " + (applied if applied else old)[:12]
        elif kind == "param":                  # "param": add an input to the block (#48)
            applied = be.add_param(k.get("proc"), k["entry"].text)
            self.blk_status = ("input: " + applied[:12]) if applied \
                else "bad input name"
        else:                                  # "var": rename the freshly-created var
            old = k["var"]
            applied = be.rename_var(old, k["entry"].text)
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
        """One typed byte into the open prompt (editors_base.text_key: trim,
        commit, cancel, or append what the kind allows)."""
        k = self.blk_kbd
        if k is None:
            return
        ev = k["entry"].key(ch)
        if ev == TE_COMMIT:
            self._blk_kbd_commit()
        elif ev == TE_CANCEL:
            self._blk_kbd_cancel()

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
            self.blk_status = None        # save is invisible (#111): no "SAVED"
            ws.ach.note("code_save")
            return True
        try:
            status, smsg = ws._with_sd(
                lambda: ws.carts_store.save_blocks(ws.project.cart, prog))
            if status != ws.carts_store.SAVE_OK:
                self.blk_status = "CAN'T SAVE " + str(smsg)
                return False
            be.dirty = False
            self.blk_status = None        # save is invisible (#111): no "SAVED"
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
        page = self._page
        if page is not None:
            if page.owed():
                ws._dirty = True
            if self.blk_kbd is None and self.blk_menu is not None:
                if page.keys(i):
                    ws._dirty = True
                if i.pressed("b"):
                    self._blk_menu_back()
                    ws._dirty = True
                return
        if self.blk_kbd is not None:
            # The entry prompt owns input: its dialog's engine takes the typed
            # bytes, the moves between its stops and Enter; Esc cancels (the
            # T-Deck's Mic does through Workstation.cancel_field). The key that
            # opened it types nothing (its host's seed, #29).
            host = self.blk_kbd["host"]
            if host.keys(i):
                ws._dirty = True
            if host.inp[5] == 27 or i.pressed("stop"):
                host.inp[5] = 0
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
        moved = self._blk_ekey.undo_redo(k, self._blk_undo, self._blk_redo)
        if i.pressed("up"):
            self._blk_move_cursor(-1)
            moved = True
        if i.pressed("down"):
            self._blk_move_cursor(1)
            moved = True
        if i.pressed("right"):
            self._blk_next_slot()                # step the highlighted slot
            moved = True
        if i.pressed("left"):
            # left on a number slot decrements it (a quick tweak without the menu)
            self._blk_left()
            moved = True
        if i.pressed("a"):
            self._blk_a()
            if self.blk_kbd is not None and not moved:
                self._held.opened(ws)            # a slot's prompt over the page as it was
        elif i.pressed("run"):
            self.save_blocks()
        else:
            ws._leave_or_home(ws.exit)

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
            if len(be.rows) <= lay.rows or not _in(px, py, area):
                return
            self._dragv = py
            return
        was = self.blk_top
        self._dragv, self.blk_top = _ui_draw.row_drag(
            self._dragv, py, max(1, lay.row_h), self.blk_top,
            max(0, len(be.rows) - lay.rows))
        if self.blk_top != was:
            ws._dirty = True

    def _blocks_pointer(self, px, py, click):
        """A pointer sample on the tab, in SYSTEM coords: the outline's taps
        and drags are this method's and a prompt's taps its own; the rest
        are the page's verbs (the bar's first)."""
        ws = self.ws
        lay = self.block_layout
        if (self.blk_kbd is None
                and (self.blk_menu is not None
                     or not _in(px, py, lay.area()) and self._dragv is None)):
            if click and ws.bar_layer.handle_bar_tap("menu", px, py):
                return
            if self.page().point(px, py, click, ws.pointer.down):
                ws._dirty = True
            return
        if self.blk_kbd is not None:
            if self.blk_kbd["host"].point(px, py, click, ws.pointer.down):
                ws._dirty = True
            return
        self._outline_drag(px, py)         # held drag scrolls the outline
        if not click:
            return
        if ws.bar_layer.handle_bar_tap("menu", px, py):
            return
        be = self.blocks_ed
        if be is None:
            return
        # Tap a row in the outline: select it (and on a block, advance the slot
        # highlight / open the insert menu on a `+` row -- a tap == the A action).
        if _in(px, py, lay.area()):
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

    def _draw_blocks(self, dt=0):
        """The Blocks tab: the page (`editor_blocks.BlocksPage`) in the content
        rect, the bar's strip, and an entry prompt over them."""
        ws = self.ws
        k = self.blk_kbd
        held = self._held
        if k is None:
            if held.key is not None:
                held.drop()
        elif held.put(ws, ws.sys_canvas, self._page_rect(), self._held_key(k), dt):
            ws.bar_layer._draw_status_strip("menu")
            self._draw_blk_kbd(True, dt)
            return
        if k is None or not held.last(ws, ws.sys_canvas, self._page_rect(), dt):
            self.page().draw(ws.sys_canvas, ws.theme_colors, self._page_rect(), False)
        ws.bar_layer._draw_status_strip("menu")
        if self.blk_kbd is not None:
            self._draw_blk_kbd(False, dt)
        else:
            held.shown(ws, ws.sys_canvas, self._page is None or self._page.host.still(), dt)

    def _page_rect(self):
        lay = self.block_layout
        return (lay.cx, lay.cy, lay.cw, lay.ch)

    def _held_key(self, k):
        """What the page under prompt `k` is a function of while it is up."""
        ws = self.ws
        return (k, ws.sys_canvas, self._page_rect(), ws.layout.fs, ws.look.look_gen)

    def _draw_blk_kbd(self, held=False, dt=0):
        """The entry prompt up (GAPS 52): `ui.dialog` with a captioned
        `ui.field`, over the Blocks page dimmed -- "Name your variable" (a
        block's, an input's) with the rule for a name, "Type some text", or
        "Type a number" with the slot's value as its placeholder and Use a
        block where the slot takes one. The keys type, so it has no DEL or
        digit pad; the T-Deck's keys are its hint strip's, where a touch
        glass and the desk add Cancel and OK. On the SYSTEM canvas: the
        game canvas is a hidden buffer on a bigger glass (#85/#93). `held`:
        the page under it was put back washed (`HeldPage`), else it was
        drawn this frame and is washed and held here."""
        ws = self.ws
        k = self.blk_kbd
        kind = k.get("kind")
        entry = k["entry"]
        host = k["host"]
        desk = _layout_base.metric_class() == _layout_base.DESK
        lay = self.block_layout
        host.begin(ws.sys_canvas, ws.theme_colors, (lay.cx, lay.cy, lay.cw, lay.ch), desk)
        num = kind == "num"
        to_block = num and k.get("allow_block")
        keys = (("confirm", "OK") + (("scroll", "Use a block") if to_block else ())
                + ("cancel", "Cancel"))
        _strip(ws, lay, keys, desk)
        if not held:
            self._held.wash(ws, ws.sys_canvas, self._page_rect(), self._held_key(k),
                            self._page is None or self._page.host.still(), dt)
        tdeck = _ui.input_kind() == "tdeck"
        pick = _ui.dialog(_KBD_TITLES.get(kind, "Name your variable"),
                          () if tdeck else ("Cancel", "OK"), "primary", _ui_draw.NO_WASH)
        if not num and kind != "text":
            _ui.label(_KBD_NAME_RULE, _KBD_SUB)
        if num:
            cur = k.get("cur")
            hint = str(cur) if _blocks_mod.is_literal_value(cur) and cur is not None else "0"
            cap = str(k.get("slot") or "Number").replace("_", " ")
            cap = cap[:1].upper() + cap[1:]
        else:
            hint = "" if kind == "text" else str(k.get("var", ""))
            cap = _KBD_CAPTIONS.get(kind, "Name")
        got, done = _ui.field(cap, entry.text, 24, {"id": "entry", "hint": hint[:24]})
        if got != entry.text:
            # what the kind takes of what was typed (a number's digits, a name's letters)
            t = ""
            for ch in got:
                t, _ev = _text_key(t, ord(ch), entry.cap, entry.allow)
            entry.text = t
        used = False
        if to_block:
            _ui.icon("blocks")
            used = _ui.button("Use a block")
        _ui.dialog()
        if used:
            self._blk_num_to_block()
        elif pick == 1 or done:
            self._blk_kbd_commit()
        elif pick == 0:
            self._blk_kbd_cancel()
        if host.owed or host.tap == 2:
            ws.request_frame()

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

    def _cat_color(self, cat):
        """A block category's colour: the theme's `block_<category>` content
        role (the Scratch look by default), dark grey for no category."""
        c = self.ws.look.content_colors().get("block_" + str(cat))
        return self._NAMES["dark_grey"] if c is None else c

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

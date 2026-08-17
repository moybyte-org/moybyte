"""BlockEditor (#29 Part 2) -- the structured-outline block program + cursor
(+ BlockRow / _clone_tree). Split out of editors.py (which re-exports them);
history via the shared editors_base discipline."""

try:
    from op_history import History, OpHistoryMixin
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.op_history import History, OpHistoryMixin


_BLK_UNDO_MAX = 40


def _clone_tree(node):
    """A deep copy of a block / program tree -- dicts, lists, and json-safe scalars
    (int / float / str / bool / None). So a pasted/duplicated block or an undo
    snapshot shares NO mutable state with the original (#93). MicroPython-safe (no
    `copy`/`json` needed): the schema is exactly these three shapes."""
    if isinstance(node, dict):
        out = {}
        for k in node:
            out[k] = _clone_tree(node[k])
        return out
    if isinstance(node, list):
        return [_clone_tree(v) for v in node]
    return node


class _BlockOps:
    """OpCodec for BlockEditor (#111 phase 4): one op is the whole-program
    before/after pair ["blk", before, after] -- both JSON-able clones of the
    (tiny) block tree, so an op carries enough pre-state to invert (undo restores
    `before`, redo restores `after`) and round-trips through a persistence layer
    unchanged. A full-tree op (not a per-mutation delta) is deliberate: the tree
    is small, and inverting ~18 heterogeneous structural edits op-by-op
    (cross-parent move, rename-with-ref-rewrite, param add/remove) would be far
    more error-prone than swapping a clone. The doc is the BlockEditor; apply/
    invert rebind `program` to a FRESH clone (never the stored op's tree, which
    later edits would corrupt) and re-flatten via the editor's own tail."""

    def apply(self, doc, op):          # redo -> the AFTER program
        doc.program = _clone_tree(op[2])
        doc._after_history()

    def invert(self, doc, op):         # undo -> the BEFORE program
        doc.program = _clone_tree(op[1])
        doc._after_history()


class BlockRow:
    """One visual line of the flattened block-outline (what the cursor moves over).

    Two flavors share this struct so the cursor is a single index over both:
      kind == "block"  -- an existing block; `block` is its dict, `parent` is the
                          list it lives in, `index` its position in that list.
      kind == "insert" -- an empty insert point (a `+` slot) where a NEW statement
                          can be added; `parent`/`index` say where it would land.
    `depth` is the indent level (for the renderer); `is_else` marks the synthetic
    "else" divider row of an if_else (a block row the kid can't delete directly)."""

    def __init__(self, kind, depth, parent, index, block=None, is_else=False):
        self.kind = kind          # "block" | "insert"
        self.depth = depth
        self.parent = parent      # the children list this row belongs to
        self.index = index        # position of the block (or the insert point) in it
        self.block = block        # the block dict for a "block" row, else None
        self.is_else = is_else    # the if_else divider (a non-deletable label row)


class BlockEditor(OpHistoryMixin):
    """The structured-outline block program + a cursor over its flattened script
    (issue #29 Part 2). Pure logic -- no rendering, no I/O -- so it backs both the
    host console and the frozen device console. The `blocks` module (Part 1) is the
    vocabulary/compiler; it's INJECTED (not imported) so this stays dependency-free
    and freezes cleanly: the host passes runtime.blocks, the device passes the
    frozen `blocks`.

    The program is the Part-1 `{vars, scripts}` tree. The kid navigates a flattened
    list of rows (events, statements, and the `+` insert points between them) with
    a single cursor; A inserts at an insert point or edits a block, and the edit ops
    (delete / move / set-slot) mutate the tree and re-flatten. No dragging -- exactly
    the decided device-friendly interaction."""

    def __init__(self, blocks, program=None):
        self.blocks = blocks
        self.program = program if program is not None else blocks.empty_program()
        self.cur = 0              # cursor index into self.rows
        self.rows = []
        self.dirty = False
        # #85/#93 per-object scripts: which script set the outline is editing --
        # None = the global "Stage" (program["scripts"] + procs), or a scene object's
        # tag string (program["objects"] entry). set_target() flips it; reflow()
        # flattens whichever is active. Stays None for a cart with no scene objects,
        # so a Stage-only program behaves exactly as before.
        self.target = None
        # -- #93 clipboard + cross-parent move + in-session undo/redo -----------
        self.clipboard = None     # a deep-copied block subtree (copy/paste/duplicate)
        self._move_src = None     # the block object marked for a cross-parent MOVE
        # #111 phase 4: the outline's in-session undo/redo runs on the SHARED
        # op-history core (op_history.History) over the whole-program before/after
        # codec, so the #88 bar UNDO/REDO icons (ws.undo()/redo(), routed by
        # Project's per-tab history registry) drive the SAME stack as the host
        # Ctrl+Z. Bounded to _BLK_UNDO_MAX RAM steps (an INVERT codec, so the
        # depth ring is sound). In-session only -- blocks saves don't journal
        # ops, so this History never flushes into a commit (unlike paint/map);
        # it's dropped when a different cart rebuilds the editor.
        self._hist = History(self, _BlockOps(), max_undo=_BLK_UNDO_MAX)
        # A mutating edit snapshots the program PRE-state here (at _record, before
        # it changes anything); the matching POST-state is captured lazily when the
        # NEXT edit opens or an undo/redo/flush seals it -- a "burst" close exactly
        # like Writer's typing burst and Map's open batch. So an edit is one op.
        self._pending_pre = None
        self.reflow()

    # -- flattening ----------------------------------------------------------
    def reflow(self):
        """Rebuild the flat row list from the tree, then clamp the cursor. Called
        after every structural edit so rows/cursor stay in sync with the program."""
        rows = []
        scripts = self.active_scripts()           # Stage or the target object's scripts
        for si in range(len(scripts)):
            self._flatten_block(rows, scripts, si, 0)
        # custom-block definitions (#48) render after the event scripts: each proc's
        # define-hat + its indented body, using the same flatten machinery as a hat.
        # Procs are GLOBAL, so they only show on the Stage -- not inside an object.
        if self.target is None:
            procs = self.program.get("procs", []) or []
            for pi in range(len(procs)):
                self._flatten_block(rows, procs, pi, 0)
        if not rows:                              # an empty program still needs a row
            rows.append(BlockRow("insert", 0, scripts, 0))
        self.rows = rows
        if self.cur >= len(rows):
            self.cur = len(rows) - 1
        if self.cur < 0:
            self.cur = 0

    # -- per-object scripts (#85/#93) ----------------------------------------
    #
    # The outline edits ONE script set at a time: the global Stage (target None) or a
    # scene object's scripts (target = its tag). Every structural edit (insert/delete/
    # move/slot) works through the BlockRow's `parent` list, so it operates on whatever
    # `active_scripts()` reflow flattened -- no per-edit changes needed. Objects live in
    # program["objects"] = [{"tag": str, "scripts": [on_start/on_update/on_draw hats]}],
    # the OPTIONAL key the compiler reads via `.get(...) or []` (empty => byte-identical).

    def objects(self):
        return list(self.program.get("objects", []) or [])

    # A sprite's standard event hats (Scratch's per-sprite events): green-flag start,
    # per-frame update + draw, and "when I'm tapped" (on_tap). A sprite authored before
    # on_tap existed gains it on next open (never losing existing scripts).
    _OBJECT_HATS = ("on_start", "on_update", "on_draw", "on_tap")

    def _ensure_object_hats(self, o):
        """Ensure `o` carries the standard sprite hats, appending any missing one in
        canonical order (e.g. on_tap for an older sprite). Never removes/reorders a
        kid's existing scripts. Idempotent."""
        scripts = o.setdefault("scripts", [])
        have = set(h.get("t") for h in scripts)
        for t in self._OBJECT_HATS:
            if t not in have:
                scripts.append(self.blocks.make_block(t))
        return o

    def object_entry(self, tag, create=False):
        """The program["objects"] entry for `tag`, or None. A found entry is normalized
        to the standard sprite hats (so "when I'm tapped" appears on older sprites too);
        with create=True a missing entry is materialized with those hats, so a
        just-selected sprite opens on a familiar, editable outline. Empty entries are
        pruned at save (blocks.prune_empty_objects)."""
        for o in (self.program.get("objects", []) or []):
            if o.get("tag") == tag:
                return self._ensure_object_hats(o)
        if not create:
            return None
        o = self._ensure_object_hats({"tag": tag, "scripts": []})
        self.program.setdefault("objects", []).append(o)
        return o

    def active_scripts(self):
        """The script list the outline is currently editing: program["scripts"] on the
        Stage, else the target object's scripts (materialized on demand)."""
        if self.target is None:
            return self.program.setdefault("scripts", [])
        return self.object_entry(self.target, create=True)["scripts"]

    def set_target(self, tag):
        """Switch the outline to a scene object's scripts (`tag`) or the global Stage
        (`tag` is None), then reflow. Idempotent -- a no-op when already on `tag`."""
        if tag == self.target:
            return
        if tag is not None:
            self.object_entry(tag, create=True)
        self.target = tag
        self.cur = 0
        self.reflow()

    def _all_roots(self):
        """Every top-level script root -- global scripts + procs + EVERY object's hats.
        Used by the tree-wide walks (locate-for-move, variable rename, proc rename) so
        a global rename rewrites references inside per-object scripts too, and a MOVE
        can find its block whichever script set is active."""
        roots = list(self.program.get("scripts", []) or [])
        roots += list(self.program.get("procs", []) or [])
        for o in (self.program.get("objects", []) or []):
            roots += list(o.get("scripts", []) or [])
        return roots

    def _flatten_block(self, rows, parent, index, depth):
        b = parent[index]
        tid = b.get("t")
        if tid == self.blocks.ELSE_MARKER:        # the if_else divider, drawn as a label
            rows.append(BlockRow("block", depth, parent, index, b, is_else=True))
            return
        rows.append(BlockRow("block", depth, parent, index, b))
        if self.blocks.is_cblock(tid) or self._is_hat(tid) or self.blocks.is_def(tid):
            # A block with a body (hat / c-block / proc definition) shows its children
            # indented, with
            # an insert point before each child and one trailing insert point so the
            # body can always be appended to even when empty. Ensure the body list
            # exists (make_block omits "c" on hats), so inserts have a real target.
            children = b.setdefault("c", [])
            cdepth = depth + 1
            for ci in range(len(children)):
                rows.append(BlockRow("insert", cdepth, children, ci))
                self._flatten_block(rows, children, ci, cdepth)
            rows.append(BlockRow("insert", cdepth, children, len(children)))

    def _is_hat(self, tid):
        d = self.blocks.block_def(tid)
        return bool(d) and d.get("shape") == self.blocks.SHAPE_HAT

    # -- cursor --------------------------------------------------------------
    def move(self, d):
        """Move the cursor by d rows (honors magnitude, like the code editor)."""
        n = len(self.rows)
        if n == 0:
            return
        self.cur = max(0, min(n - 1, self.cur + d))

    def row(self):
        """The row under the cursor (or None for an empty editor)."""
        if 0 <= self.cur < len(self.rows):
            return self.rows[self.cur]
        return None

    def selected_block(self):
        """The block dict under the cursor, or None if the cursor is on an insert
        point (or the synthetic else divider)."""
        r = self.row()
        if r is not None and r.kind == "block" and not r.is_else:
            return r.block
        return None

    def at_insert(self):
        r = self.row()
        return r is not None and r.kind == "insert"

    # -- in-session undo/redo (#93) ------------------------------------------
    # Every mutating edit snapshots the WHOLE program (a tiny tree) onto a bounded
    # stack BEFORE it changes anything, so undo restores the prior program verbatim.
    # In-session only: the stack lives on this editor instance and is dropped when a
    # different cart opens (BlockEditorUI rebuilds the editor). It never crosses a
    # save or graduation -- those are the durable code journal's job (spec Section 7).
    def _record(self):
        """Open a new undo op: seal the previous pending op (its after-state is the
        current program -- that mutation has completed) and snapshot the fresh
        pre-state. Called at the top of every mutating op, after its guards, so a
        guarded no-op records nothing (#111 phase 4)."""
        self._seal_pending()
        self._pending_pre = _clone_tree(self.program)

    def _seal_pending(self):
        """Close the open pending op into ONE History op = the net before/after
        program pair. A no-op when nothing is pending, or when the edit ended up
        not changing the program. Also the seam the undo/redo verbs (and the bar's
        ws._seal_active_local) call FIRST, so a just-made, not-yet-sealed edit is
        undo's first target -- mirrors Writer's burst close + Map's end_edit."""
        before = self._pending_pre
        self._pending_pre = None
        if before is None:
            return
        after = _clone_tree(self.program)
        if before != after:
            self._hist.record(["blk", before, after])

    def _pending_changed(self):
        """True iff an un-sealed edit actually changed the program -- so can_undo()
        reports it WITHOUT the side effect of sealing (the dim-state read path)."""
        return (self._pending_pre is not None
                and self._pending_pre != self.program)

    # undo/redo are OpHistoryMixin's over self._hist (#111). The #88 bar
    # (ws.undo/redo) and the host Ctrl+Z (block_editor_ui) both drive them.

    def _hist_before(self):
        # Seal any open edit first, so a just-made edit is the step's target.
        self._seal_pending()

    def can_undo(self):
        # ...which is also why an UN-sealed changed edit must light the bar's
        # UNDO without the side effect of sealing (the dim-state read path).
        return self._hist.can_undo() or self._pending_changed()

    def _after_history(self):
        """Shared undo/redo tail: a restored program is a fresh tree, so any marked
        move source is stale -- drop it -- and the outline must re-flatten."""
        self._move_src = None
        self.dirty = True
        self.reflow()

    # -- structural edits ----------------------------------------------------
    def insert_block(self, type_id, params=None, children=None):
        """Insert a freshly-built block (make_block) at the cursor's insert point.
        No-op (returns None) if the cursor isn't on an insert point. The new block
        becomes the selection so editing/nesting flows continue on it. Returns the
        new block dict on success."""
        r = self.row()
        if r is None or r.kind != "insert":
            return None
        blk = self.blocks.make_block(type_id, params, children)
        self._record()
        r.parent.insert(r.index, blk)
        self.dirty = True
        self.reflow()
        self._select_block(blk)
        return blk

    def insert_else(self):
        """Add an else divider to the if_else c-block under the cursor (so the kid
        can build the else branch). No-op if the selected block isn't an if_else or
        already has an else. The else marker goes at the END of the children, after
        the if-body. Returns True on success."""
        b = self.selected_block()
        if b is None or b.get("t") != "if_else":
            return False
        children = b.setdefault("c", [])
        for c in children:
            if c.get("t") == self.blocks.ELSE_MARKER:
                return False                      # only one else per if_else
        self._record()
        children.append(self.blocks.make_block(self.blocks.ELSE_MARKER))
        self.dirty = True
        self.reflow()
        return True

    def delete(self):
        """Delete the selected block (and its whole subtree). Refuses to delete an
        event hat (a script must keep its lifecycle) and the synthetic else divider.
        Returns True if something was removed."""
        r = self.row()
        if r is None or r.kind != "block" or r.is_else:
            return False
        tid = r.block.get("t")
        if self._is_hat(tid):
            return False                          # never delete an event hat
        self._record()
        del r.parent[r.index]
        self.dirty = True
        self.reflow()
        # keep the cursor near where the block was (clamp handles the tail case)
        self.cur = max(0, min(len(self.rows) - 1, self.cur))
        return True

    def move_block(self, d):
        """Reorder the selected block up (d<0) / down (d>0) among its SIBLINGS in
        the same body. Won't move a hat (events stay ordered by kind) or the else
        divider, and won't move past the ends of its sibling list. Returns True if
        it moved."""
        r = self.row()
        if r is None or r.kind != "block" or r.is_else:
            return False
        if self._is_hat(r.block.get("t")):
            return False
        siblings = r.parent
        i = r.index
        j = i + (1 if d > 0 else -1)
        if j < 0 or j >= len(siblings):
            return False
        if siblings[j].get("t") == self.blocks.ELSE_MARKER:
            # Don't shuffle a statement across the else boundary by a single step --
            # that silently changes branches. The kid moves it explicitly instead
            # (the #93 MOVE flow crosses the divider on purpose).
            return False
        self._record()
        siblings[i], siblings[j] = siblings[j], siblings[i]
        self.dirty = True
        self.reflow()
        self._select_block(siblings[j])
        return True

    # -- copy / paste / duplicate + cross-parent move (#93) -------------------
    def copy_block(self):
        """Deep-copy the selected block (and its whole subtree: if/else arms, loop
        bodies, nested reporters) into the clipboard. Refuses an event hat (it can't
        live inside a body) and the synthetic else divider. Returns True on success."""
        r = self.row()
        if r is None or r.kind != "block" or r.is_else:
            return False
        tid = r.block.get("t")
        if self._is_hat(tid) or self.blocks.is_def(tid) or tid == self.blocks.ELSE_MARKER:
            return False
        self.clipboard = _clone_tree(r.block)
        return True

    def has_clipboard(self):
        return self.clipboard is not None

    def duplicate(self):
        """Insert a deep copy of the selected block immediately after it (same body).
        Same guards as copy (no hats, no else divider). The copy shares no mutable
        state with the original. Returns the new block, or None. Undoable."""
        r = self.row()
        if r is None or r.kind != "block" or r.is_else:
            return None
        tid = r.block.get("t")
        if self._is_hat(tid) or self.blocks.is_def(tid) or tid == self.blocks.ELSE_MARKER:
            return None
        clone = _clone_tree(r.block)
        self._record()
        r.parent.insert(r.index + 1, clone)
        self.dirty = True
        self.reflow()
        self._select_block(clone)
        return clone

    def paste(self):
        """Paste a deep copy of the clipboard at the cursor's insert point. Only at
        an insert point, only when the clipboard holds a paste-able block (never a
        hat / else divider -- copy already refuses those). Each paste is an
        independent deep copy, so repeated pastes never alias. Returns the new block,
        or None. Undoable."""
        if self.clipboard is None:
            return None
        r = self.row()
        if r is None or r.kind != "insert":
            return None
        tid = self.clipboard.get("t")
        if self._is_hat(tid) or self.blocks.is_def(tid) or tid == self.blocks.ELSE_MARKER:
            return None
        clone = _clone_tree(self.clipboard)
        self._record()
        r.parent.insert(r.index, clone)
        self.dirty = True
        self.reflow()
        self._select_block(clone)
        return clone

    def start_move(self):
        """Mark the selected block as the source of a cross-parent MOVE (the
        destination is then any insert point -- across the if/else divider or into a
        different body, which the single-step reorder can't reach). Refuses a hat /
        else divider. Returns True if the block can be moved."""
        r = self.row()
        if r is None or r.kind != "block" or r.is_else:
            return False
        if self._is_hat(r.block.get("t")) or self.blocks.is_def(r.block.get("t")):
            return False
        self._move_src = r.block
        return True

    def moving(self):
        return self._move_src is not None

    def cancel_move(self):
        self._move_src = None

    def complete_move(self):
        """Move the marked block to the cursor's insert point (across parents / the
        if-else divider) -- ONE undoable op that preserves the block's identity (the
        same object is re-parented, not cloned). Rejects a destination inside the
        block's own subtree (that would orphan it). Returns True on success."""
        src = self._move_src
        if src is None:
            return False
        r = self.row()
        if r is None or r.kind != "insert":
            return False
        dest_parent = r.parent
        dest_index = r.index
        if self._within(src, dest_parent):
            return False                          # can't drop a block inside itself
        loc = self._locate(src)
        if loc is None:                           # source vanished (e.g. undone away)
            self._move_src = None
            return False
        src_parent, src_index = loc
        self._record()
        del src_parent[src_index]
        # same body, dropping after the removed slot: the destination shifted up one.
        if dest_parent is src_parent and dest_index > src_index:
            dest_index -= 1
        dest_parent.insert(dest_index, src)
        self._move_src = None
        self.dirty = True
        self.reflow()
        self._select_block(src)
        return True

    def _locate(self, block):
        """Find (parent_list, index) of `block` by identity in the statement tree, or
        None. Walks child bodies only -- a movable block is always a body statement,
        which may live inside an event script OR a custom-block body (#48)."""
        return self._locate_in(self._all_roots(), block)

    def _locate_in(self, lst, block):
        for i in range(len(lst)):
            c = lst[i]
            if c is block:
                return (lst, i)
            kids = c.get("c") if isinstance(c, dict) else None
            if kids:
                found = self._locate_in(kids, block)
                if found is not None:
                    return found
        return None

    def _within(self, block, target_list):
        """True if `target_list` is `block`'s own children list or any body nested
        under it (so a MOVE can't drop a block into itself)."""
        kids = block.get("c")
        if kids is target_list:
            return True
        if kids:
            for c in kids:
                if isinstance(c, dict) and self._within(c, target_list):
                    return True
        return False

    # -- slot editing --------------------------------------------------------
    def slots(self, block=None):
        """The slot descriptors for a block (defaults to the selection). Program-aware
        (#48): a `call` yields one expr slot per parameter of the proc it targets, a
        `proc_def` yields none (edited via the PROC menu). Empty for unknown/None."""
        b = block if block is not None else self.selected_block()
        if b is None:
            return []
        return self.blocks.block_slots(self.program, b)

    def slot_value(self, slot_name, block=None):
        b = block if block is not None else self.selected_block()
        if b is None:
            return None
        return (b.get("p", {}) or {}).get(slot_name)

    def set_slot(self, slot_name, value, block=None):
        """Write a slot value on a block (defaults to the selection). The caller is
        responsible for passing a value the slot's type accepts (a number/string
        literal, a variable name, a dropdown option, or an expression block dict for
        an expr slot). Returns True if the block exists."""
        b = block if block is not None else self.selected_block()
        if b is None:
            return False
        # A call's args (#48) live in a positional list p["args"], not as named slots;
        # the dynamic slot names are arg0/arg1/... -> write into the list, growing it
        # (padded with 0) so an arg can be set even before earlier ones were touched.
        if b.get("t") == self.blocks.CALL and slot_name[:3] == "arg":
            try:
                i = int(slot_name[3:])
            except ValueError:
                return False
            self._record()
            args = b.setdefault("p", {}).setdefault("args", [])
            while len(args) <= i:
                args.append(0)
            args[i] = value
            self.dirty = True
            return True
        self._record()
        p = b.setdefault("p", {})
        p[slot_name] = value
        self.dirty = True
        return True

    def cycle_dropdown(self, slot_name, d=1, block=None):
        """Step a dropdown slot to the next/previous option (wrapping). Convenience
        for the picker UI. Returns the new option, or None if the slot isn't a known
        dropdown. (number/text slots are edited via set_slot from the keyboard.)"""
        b = block if block is not None else self.selected_block()
        if b is None:
            return None
        for slot in self.slots(b):
            if slot["name"] == slot_name and slot["type"] == self.blocks.SLOT_DROPDOWN:
                opts = self.blocks.slot_options(slot)
                if not opts:
                    return None
                cur = (b.get("p", {}) or {}).get(slot_name)
                try:
                    i = opts.index(cur)
                except ValueError:
                    i = 0
                val = opts[(i + d) % len(opts)]
                # inline the write (don't call set_slot) so this records ONE undo
                # snapshot for the whole cycle, not a nested double.
                self._record()
                b.setdefault("p", {})[slot_name] = val
                self.dirty = True
                return val
        return None

    # -- variables -----------------------------------------------------------
    def add_var(self, name):
        """Declare a new variable (so variable slots can reference it). The name is
        sanitized into a safe identifier (kid free-text -> `my_score`), de-duplicated,
        and blanks / names already used by a list are ignored. Returns the variable
        list."""
        name = self.blocks.sanitize_var_name(name)
        vars_ = self.program.setdefault("vars", [])
        if name and name not in vars_ and name not in self.lists() \
                and name not in self.proc_names():
            self._record()
            vars_.append(name)
            self.dirty = True
        return vars_

    def new_var(self, base="var"):
        """Create a freshly-named variable with a sensible default (var, var2, ...)
        the kid can rename. The name is unique across BOTH variables and lists (they
        share the module-level global namespace). Returns the new variable's name."""
        taken = self._all_names()
        name = self.blocks.unique_var_name(taken, base)
        self._record()
        self.program.setdefault("vars", []).append(name)
        self.dirty = True
        return name

    def rename_var(self, old, new):
        """Rename a declared variable AND rewrite every variable-slot reference to it
        across the whole tree, so set/change/expr slots keep pointing at it. The new
        name is sanitized; a blank or duplicate (other than `old` itself) is rejected.
        Returns the applied name, or None if the rename didn't happen."""
        new = self.blocks.sanitize_var_name(new)
        vars_ = self.program.setdefault("vars", [])
        if not new or old not in vars_:
            return None
        if new != old and (new in vars_ or new in self.lists()
                           or new in self.proc_names()):
            return None                       # would collide with a var/list/proc
        self._record()
        vars_[vars_.index(old)] = new
        self._rewrite_name_refs(old, new, self.blocks.SLOT_VARIABLE)
        self.dirty = True
        return new

    def _rewrite_name_refs(self, old, new, slot_type):
        """Walk the tree and rewrite every slot of `slot_type` whose value equals `old`
        to `new` (statements' params, nested expression params, child bodies). Shared by
        the variable and list renamers (#48)."""
        def walk(node):
            if isinstance(node, list):            # a call's positional args (#48)
                for it in node:
                    walk(it)
                return
            if not isinstance(node, dict):
                return
            d = self.blocks.block_def(node.get("t"))
            params = node.get("p", {}) or {}
            if d is not None:
                for slot in d["slots"]:
                    nm = slot["name"]
                    if slot["type"] == slot_type and params.get(nm) == old:
                        params[nm] = new
            for v in params.values():
                walk(v)                       # nested expression blocks in expr slots
            for c in node.get("c", []) or []:
                walk(c)

        for s in self._all_roots():               # #85/#93: rename inside objects too
            walk(s)

    def variables(self):
        return list(self.program.get("vars", []) or [])

    # -- lists (#48) ---------------------------------------------------------
    # Lists mirror variables: declared at the program level, picked into list slots,
    # created + named through the same on-screen-keyboard flow. A list and a variable
    # can't share a name (both compile to module-level globals).
    def add_list(self, name):
        """Declare a new list. The name is sanitized into a safe identifier, blanks /
        duplicates / names already used by a variable are ignored. Returns the list."""
        name = self.blocks.sanitize_var_name(name)
        lists_ = self.program.setdefault("lists", [])
        if name and name not in lists_ and name not in self.variables() \
                and name not in self.proc_names():
            self._record()
            lists_.append(name)
            self.dirty = True
        return lists_

    def new_list(self, base="list"):
        """Create a freshly-named list (list, list2, ...) the kid can rename. The name
        is unique across BOTH lists and variables. Returns the new list's name."""
        taken = self._all_names()
        name = self.blocks.unique_var_name(taken, base)
        self._record()
        self.program.setdefault("lists", []).append(name)
        self.dirty = True
        return name

    def rename_list(self, old, new):
        """Rename a declared list AND rewrite every list-slot reference to it. Sanitized;
        a blank / duplicate / clash with a variable is rejected. Returns the applied
        name, or None."""
        new = self.blocks.sanitize_var_name(new)
        lists_ = self.program.setdefault("lists", [])
        if not new or old not in lists_:
            return None
        if new != old and (new in lists_ or new in self.variables()
                           or new in self.proc_names()):
            return None
        self._record()
        lists_[lists_.index(old)] = new
        self._rewrite_name_refs(old, new, self.blocks.SLOT_LIST)
        self.dirty = True
        return new

    def lists(self):
        return list(self.program.get("lists", []) or [])

    # -- custom blocks (#48: My Blocks / procedures) -------------------------
    # Procs mirror vars/lists at the program level: a definition list, created +
    # named through the same on-screen-keyboard flow, with a name unique across the
    # whole module-global namespace (vars + lists + procs) and NOT a reserved cart-API
    # verb. A proc's define-hat + body render in the outline after the event scripts.
    def procs(self):
        return list(self.program.get("procs", []) or [])

    def proc_names(self):
        return [self.blocks.proc_name(pd) for pd in self.procs()]

    def _all_names(self):
        return self.variables() + self.lists() + self.proc_names()

    def new_proc(self, base="block"):
        """Create a fresh, empty custom block with a unique default name the kid can
        rename, and select its define-hat. Returns the new proc_def block."""
        base = self.blocks.sanitize_var_name(base) or "block"
        if self.blocks.is_reserved_name(base):
            base = base + "_"                 # never seed a name that shadows the API
        name = self.blocks.unique_var_name(self._all_names(), base)
        self._record()
        pd = self.blocks.make_block(self.blocks.PROC_DEF, {"name": name, "params": []})
        self.program.setdefault("procs", []).append(pd)
        self.dirty = True
        self.reflow()
        self._select_block(pd)
        return pd

    def rename_proc(self, old, new):
        """Rename a custom block AND rewrite every call targeting it. The new name is
        sanitized; a blank / reserved / duplicate / clash with a var/list is rejected.
        Returns the applied name, or None."""
        new = self.blocks.sanitize_var_name(new)
        if not new or self.blocks.is_reserved_name(new):
            return None
        pd = self.blocks.find_proc(self.program, old)
        if pd is None:
            return None
        if new != old and new in self._all_names():
            return None
        self._record()
        pd.setdefault("p", {})["name"] = new
        self._rewrite_call_names(old, new)
        self.dirty = True
        return new

    def delete_proc(self, proc_def):
        """Remove a custom block entirely (stray calls to it then compile to `pass`).
        Returns True if it was removed."""
        procs = self.program.get("procs", []) or []
        for i in range(len(procs)):
            if procs[i] is proc_def:
                self._record()
                del procs[i]
                self.dirty = True
                self.reflow()
                self.cur = max(0, min(len(self.rows) - 1, self.cur))
                return True
        return False

    def add_param(self, proc_def, name):
        """Append a parameter to a custom block. The name is sanitized + made unique
        within the proc (blank/reserved/duplicate rejected). Returns the applied name
        or None; existing calls keep their args (a new trailing arg defaults to 0)."""
        if proc_def is None:
            return None
        name = self.blocks.sanitize_var_name(name)
        if not name or self.blocks.is_reserved_name(name):
            return None
        params = self.blocks.proc_params(proc_def)
        if name in params:
            return None
        self._record()
        params.append(name)
        proc_def.setdefault("p", {})["params"] = params
        self.dirty = True
        self.reflow()
        return name

    def remove_last_param(self, proc_def):
        """Drop the last parameter of a custom block. Returns True if one was removed."""
        if proc_def is None:
            return False
        params = self.blocks.proc_params(proc_def)
        if not params:
            return False
        self._record()
        params.pop()
        proc_def.setdefault("p", {})["params"] = params
        self.dirty = True
        self.reflow()
        return True

    def insert_call(self, name):
        """Insert a call to proc `name` at the cursor's insert point, pre-filling one
        default (0) arg per parameter. Returns the new call block, or None."""
        pd = self.blocks.find_proc(self.program, name)
        if pd is None:
            return None
        n = len(self.blocks.proc_params(pd))
        return self.insert_block(self.blocks.CALL, {"name": name, "args": [0] * n})

    def _rewrite_call_names(self, old, new):
        """Rewrite every call targeting `old` to target `new` (after a proc rename)."""
        def walk(node):
            if isinstance(node, list):
                for it in node:
                    walk(it)
                return
            if not isinstance(node, dict):
                return
            if node.get("t") == self.blocks.CALL and \
                    self.blocks.proc_name(node) == old:
                node.setdefault("p", {})["name"] = new
            for v in (node.get("p", {}) or {}).values():
                walk(v)
            for c in node.get("c", []) or []:
                walk(c)
        for s in self._all_roots():               # #85/#93: rename inside objects too
            walk(s)

    def enclosing_proc(self):
        """The proc_def whose body contains the cursor row (or None). Lets the editor
        offer a proc's PARAMETERS as variables while editing inside its body (#48)."""
        r = self.row()
        if r is None:
            return None
        for pd in self.procs():
            if self._within(pd, r.parent):
                return pd
        return None

    def current_params(self):
        """The parameter names in scope at the cursor (the enclosing proc's params),
        or [] outside any custom-block body."""
        pd = self.enclosing_proc()
        return self.blocks.proc_params(pd) if pd is not None else []

    # -- helpers -------------------------------------------------------------
    def _select_block(self, blk):
        """Park the cursor on the row that holds `blk` after a reflow (so an insert/
        move leaves the new/moved block selected)."""
        for i in range(len(self.rows)):
            r = self.rows[i]
            if r.kind == "block" and r.block is blk:
                self.cur = i
                return


# -- music / sound editor (#50) ----------------------------------------------

# Editable bounds for an SFX step, mirrored from runtime/audio.py so this core
# stays dependency-free (the docstring contract): a step is [pitch, wave, vol].
# pitch is a semitone index 0..95 (C0..B7) or -1 for a rest; wave is 0..3
# (square/triangle/saw/noise); vol is 0..7. The console renders pitch as a note
# name; this core only ever stores/clamps the integers, so it never imports audio.

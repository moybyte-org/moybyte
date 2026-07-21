"""SceneEditor (#85 Stage 2) -- placed-actor placement state over a scene's
row table, the scene analogue of MapEditor. MapEditor places tile ids onto map
CELLS; this places tagged ACTORS at world-space pixel positions. Pure logic:
the shell (scene_editor_ui.py) maps taps on the world view / tile palette to
these calls and renders the result; undo/redo rides the shared #111
op-history core (runtime/op_history.py).

The rows are plain dicts in the .moyscene shape ({tag, tile, x, y, flip,
flags}) so serialize() round-trips the exact on-disk format; list order IS the
spawn order and the default draw order (#85 Section 2), which is why z-order
editing is just moving a row within the list.

Undo/redo (#111 phase 4): each GESTURE (a placement, a finished drag, a
delete, a z-order move, a tag/flip commit) records ONE typed op carrying its
own pre-image -- a place/remove op carries the row + its list index, a move op
its before/after (x, y), a tag/flip op its before/after value, a z-order op
its before/after index -- so invert() is O(1) per op, replacing the earlier
full-row-list-snapshot UndoStack (a scene is tens of tiny rows, so either
approach is cheap; typed ops match the paint/map/sheets codec model and let a
project commit drain the SAME batch into its journal line, #111)."""

import json

try:
    from op_history import History, OpCodec
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.op_history import History, OpCodec


# A fresh actor's tag: a real word (not "") so the props row never shows an
# empty field, and a kid sees "this is the thing you rename".
DEFAULT_TAG = "actor"


def parse_rows(text):
    """Parse a .moyscene blob into editable row dicts (the widgets.Scenes parse,
    kept dict-shaped for editing). Missing/malformed -> [] (never raises), and
    every row is normalized to ints/str so the editor never trips on a
    hand-edited file."""
    rows = []
    if text:
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            data = None
        if isinstance(data, list):
            for r in data:
                if isinstance(r, dict):
                    rows.append(_norm_row(r))
    return rows


def _norm_row(r):
    row = {
        "tag": str(r.get("tag", "")),
        "tile": int(r.get("tile", 0)),
        "x": int(r.get("x", 0)),
        "y": int(r.get("y", 0)),
    }
    flip = r.get("flip", 0)
    if flip:
        row["flip"] = 1
    flags = r.get("flags")
    if isinstance(flags, dict) and flags:
        row["flags"] = flags
    return row


class _SceneOps(OpCodec):
    """OpCodec for SceneEditor (#111 phase 4): one JSON-able op per GESTURE
    TYPE, each carrying its own pre-image so invert() is O(1) -- no whole-
    row-list snapshot needed. `t` selects the shape:

      place  {"t":"place","i":idx,"row":dict}                   -- appended
      remove {"t":"remove","i":idx,"row":dict}                  -- deleted
      move   {"t":"move","i":idx,"ox":x,"oy":y,"nx":x,"ny":y}   -- dragged/nudged
      tag    {"t":"tag","i":idx,"o":old,"n":new}                -- TAG committed
      flip   {"t":"flip","i":idx,"o":0/1,"n":0/1}               -- FLIP toggled
      z      {"t":"z","i":from,"j":to}                          -- front/back_sel

    Every op names a row by its LIST INDEX at record time. Ops are only ever
    undone/redone in strict stack order (a fresh edit truncates the redo
    tail), so an index recorded here is still valid whenever apply()/invert()
    replays it -- no other structural edit can have landed in between. Plain
    dicts of ints/strings (MicroPython-safe, JSON-able as-is)."""

    def apply(self, doc, op):          # redo -> forward
        self._replay(doc, op, True)

    def invert(self, doc, op):         # undo -> reverse
        self._replay(doc, op, False)

    def _replay(self, doc, op, forward):
        t = op["t"]
        rows = doc.rows
        i = op["i"]
        if t == "place":
            if forward:
                rows.insert(i, dict(op["row"]))
                doc.sel = i
            else:
                if 0 <= i < len(rows):
                    rows.pop(i)
                doc.sel = None
        elif t == "remove":
            if forward:
                if 0 <= i < len(rows):
                    rows.pop(i)
                doc.sel = None
            else:
                rows.insert(i, dict(op["row"]))
                doc.sel = i
        elif t == "move":
            if 0 <= i < len(rows):
                if forward:
                    rows[i]["x"], rows[i]["y"] = op["nx"], op["ny"]
                else:
                    rows[i]["x"], rows[i]["y"] = op["ox"], op["oy"]
                doc.sel = i
        elif t == "tag":
            if 0 <= i < len(rows):
                rows[i]["tag"] = op["n"] if forward else op["o"]
                doc.sel = i
        elif t == "flip":
            if 0 <= i < len(rows):
                v = op["n"] if forward else op["o"]
                if v:
                    rows[i]["flip"] = 1
                elif "flip" in rows[i]:
                    del rows[i]["flip"]
                doc.sel = i
        elif t == "z":
            j = op["j"]
            if forward:
                if 0 <= i < len(rows):
                    row = rows.pop(i)
                    rows.insert(j, row)
                    doc.sel = j
            else:
                if 0 <= j < len(rows):
                    row = rows.pop(j)
                    rows.insert(i, row)
                    doc.sel = i
        doc.dirty = True


class SceneEditor:
    """Placement state over one scene's actor rows (#85 Stage 2).

    `n` is the brush -- the sheet tile a placed actor gets; `sel` the selected
    row index (None = nothing selected); `snap` toggles grid placement (moves/
    places quantize to the 8px tile grid); `cam_x`/`cam_y` are the view's
    world-space top-left in PIXELS (world-space coordinates, #85 Section 7).
    Every completed gesture (a placement, a finished drag, a delete, a z-order
    move, a tag/flip commit) is one undo step."""

    UNDO_MAX = 32     # bounded like MapEditor (#91); a step is one tiny typed op
    GRID = 8          # the snap grid: the sheet's tile size
    TAG_MAX = 16      # tag length ceiling (fits the props field at 320px)

    def __init__(self, text=""):
        self.rows = parse_rows(text)
        self.n = 0            # brush tile id (the sprite a new actor shows)
        self.sel = None       # selected row index, or None
        self.snap = True      # grid placement on by default (kid-friendly)
        self.cam_x = 0        # view top-left, world px
        self.cam_y = 0
        self.dirty = False    # unsaved edits (the title's "*", cleared on SAVE)
        self._hist = History(self, _SceneOps(), max_undo=self.UNDO_MAX)
        self._pre = None      # pre-gesture (i, ox, oy) for an open MOVE drag, or None

    # -- serialization -------------------------------------------------------

    def serialize(self):
        """The compact .moyscene JSON blob of the current rows -- the exact
        format widgets.Scenes parses and moy_carts.save_scene persists."""
        return json.dumps(self.rows)

    # -- gesture batching (a drag = ONE undo step, #111) ----------------------
    #
    # begin_edit/end_edit/abort_edit now bracket ONLY the MOVE gesture (a drag
    # of the selected actor): a place/remove/tag/flip/z-order edit is a single
    # atomic call, so it records its typed op directly (below) without needing
    # a pre-snapshot at all.

    def begin_edit(self):
        """Open a MOVE gesture: remember the selected actor's pre-drag (index,
        x, y). Idempotent while open; a no-op with nothing selected."""
        if self._pre is None:
            r = self.selected()
            if r is not None:
                self._pre = (self.sel, r["x"], r["y"])

    def end_edit(self):
        """Close the gesture: record a "move" op iff the actor actually ended
        up somewhere else (a select-only tap or a zero-move drag records
        nothing). Returns True when an undo step was recorded."""
        pre = self._pre
        self._pre = None
        if pre is None:
            return False
        i, ox, oy = pre
        if not (0 <= i < len(self.rows)):
            return False
        r = self.rows[i]
        nx, ny = r["x"], r["y"]
        if (nx, ny) == (ox, oy):
            return False
        self.dirty = True
        self._hist.record({"t": "move", "i": i, "ox": ox, "oy": oy,
                           "nx": nx, "ny": ny})
        return True

    def abort_edit(self):
        """Discard the open gesture AND restore the actor's pre-drag position
        (a drag that turned out to be a pan must leave the rows untouched).
        Records nothing."""
        pre = self._pre
        self._pre = None
        if pre is not None:
            i, ox, oy = pre
            if 0 <= i < len(self.rows):
                self.rows[i]["x"], self.rows[i]["y"] = ox, oy

    # -- undo / redo (over the shared #111 op-history) ------------------------

    @property
    def _undo(self):
        return self._hist._undo          # the op undo stack (tests inspect it)

    @property
    def _redo(self):
        return self._hist._redo

    def can_undo(self):
        return self._hist.can_undo()

    def can_redo(self):
        return self._hist.can_redo()

    def undo(self):
        """Revert the last recorded gesture; True iff a step was taken. Closes
        any open drag first (an in-flight gesture can't be half-undone)."""
        if self._pre is not None:
            self.end_edit()
        return self._hist.undo() is not None

    def redo(self):
        """Re-apply the last undone gesture; True iff a step was taken."""
        return self._hist.redo() is not None

    # -- placement / selection ----------------------------------------------

    def snap_xy(self, x, y):
        """Quantize a world position to the tile grid when snap is on."""
        if self.snap:
            g = self.GRID
            return (int(x) // g * g, int(y) // g * g)
        return (int(x), int(y))

    def place(self, x, y, tag=DEFAULT_TAG):
        """Append (spawn) a new actor at world (x, y) -- snapped when snap is on
        -- with the brush tile, and select it. One undo step."""
        x, y = self.snap_xy(x, y)
        row = {"tag": str(tag)[:self.TAG_MAX], "tile": self.n, "x": x, "y": y}
        i = len(self.rows)
        self.rows.append(row)
        self.sel = i
        self.dirty = True
        self._hist.record({"t": "place", "i": i, "row": dict(row)})
        return self.sel

    def actor_at(self, x, y):
        """The TOPMOST row index whose 8x8 tile box contains world (x, y), or
        None. Topmost = latest in the list (list order is draw order), so a tap
        picks what the kid sees in front."""
        g = self.GRID
        for i in range(len(self.rows) - 1, -1, -1):
            r = self.rows[i]
            if r["x"] <= x < r["x"] + g and r["y"] <= y < r["y"] + g:
                return i
        return None

    def select_at(self, x, y):
        """Select the actor under world (x, y); returns the index or None
        (selection itself is not an edit -- no undo step)."""
        self.sel = self.actor_at(x, y)
        return self.sel

    def selected(self):
        if self.sel is not None and 0 <= self.sel < len(self.rows):
            return self.rows[self.sel]
        return None

    def move_sel(self, x, y):
        """Move the selected actor to world (x, y) (snapped). NOT an undo step
        by itself -- a drag calls this per frame inside one begin/end_edit."""
        r = self.selected()
        if r is None:
            return
        r["x"], r["y"] = self.snap_xy(x, y)

    def nudge_sel(self, dx, dy):
        """Arrow-key nudge: move the selected actor by one grid step (snap on)
        or one pixel (snap off). One undo step per nudge."""
        r = self.selected()
        if r is None:
            return False
        step = self.GRID if self.snap else 1
        i = self.sel
        ox, oy = r["x"], r["y"]
        nx, ny = ox + dx * step, oy + dy * step
        if (nx, ny) == (ox, oy):
            return False
        r["x"], r["y"] = nx, ny
        self.dirty = True
        self._hist.record({"t": "move", "i": i, "ox": ox, "oy": oy,
                           "nx": nx, "ny": ny})
        return True

    def delete_sel(self):
        """Remove the selected actor. One undo step."""
        r = self.selected()
        if r is None:
            return False
        i = self.sel
        row = dict(r)
        if "flags" in row:
            row["flags"] = dict(row["flags"])
        self.rows.pop(i)
        self.sel = None
        self.dirty = True
        self._hist.record({"t": "remove", "i": i, "row": row})
        return True

    # -- z-order (list order IS draw order, #85 Section 2) -------------------

    def front_sel(self):
        """Send the selected actor to the FRONT (end of the list -- drawn last,
        on top). One undo step; a no-op when it's already frontmost."""
        r = self.selected()
        if r is None or self.sel == len(self.rows) - 1:
            return False
        i = self.sel
        j = len(self.rows) - 1
        row = self.rows.pop(i)
        self.rows.append(row)
        self.sel = j
        self.dirty = True
        self._hist.record({"t": "z", "i": i, "j": j})
        return True

    def back_sel(self):
        """Send the selected actor to the BACK (start of the list -- drawn
        first, behind everything). One undo step."""
        r = self.selected()
        if r is None or self.sel == 0:
            return False
        i = self.sel
        j = 0
        row = self.rows.pop(i)
        self.rows.insert(j, row)
        self.sel = j
        self.dirty = True
        self._hist.record({"t": "z", "i": i, "j": j})
        return True

    # -- props (the inline tag/flip row, #85 Section 4) ----------------------

    def set_tag(self, tag):
        """Set the selected actor's tag (length-capped). One undo step; a
        same-tag set records nothing."""
        r = self.selected()
        if r is None:
            return False
        i = self.sel
        old = r.get("tag", "")
        new = str(tag)[:self.TAG_MAX]
        if new == old:
            return False
        r["tag"] = new
        self.dirty = True
        self._hist.record({"t": "tag", "i": i, "o": old, "n": new})
        return True

    def toggle_flip(self):
        """Flip the selected actor horizontally (0 <-> 1). One undo step."""
        r = self.selected()
        if r is None:
            return False
        i = self.sel
        old = 1 if r.get("flip") else 0
        new = 0 if old else 1
        if new:
            r["flip"] = 1
        elif "flip" in r:
            del r["flip"]
        self.dirty = True
        self._hist.record({"t": "flip", "i": i, "o": old, "n": new})
        return True

    # -- world extents (the pan clamp reads these) ---------------------------

    def world_size(self, tilemap=None, min_w=320, min_h=240):
        """How far the editable world extends: at least the fixed game viewport
        (320x240 -- screen-space is the degenerate case, #85 Section 7), grown
        to cover the tilemap and every placed actor, plus a one-viewport margin
        past the furthest content so there is always room to place beyond it."""
        w = min_w
        h = min_h
        if tilemap is not None:
            tw = tilemap.w * self.GRID
            th = tilemap.h * self.GRID
            if tw > w:
                w = tw
            if th > h:
                h = th
        for r in self.rows:
            if r["x"] + self.GRID > w:
                w = r["x"] + self.GRID
            if r["y"] + self.GRID > h:
                h = r["y"] + self.GRID
        return (w + min_w, h + min_h)

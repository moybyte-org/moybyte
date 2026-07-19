"""SceneEditor (#85 Stage 2) -- placed-actor placement state over a scene's
row table, the scene analogue of MapEditor. MapEditor places tile ids onto map
CELLS; this places tagged ACTORS at world-space pixel positions. Pure logic:
the shell (scene_editor_ui.py) maps taps on the world view / tile palette to
these calls and renders the result; history rides the shared editors_base
discipline.

The rows are plain dicts in the .moyscene shape ({tag, tile, x, y, flip,
flags}) so serialize() round-trips the exact on-disk format; list order IS the
spawn order and the default draw order (#85 Section 2), which is why z-order
editing is just moving a row within the list. Undo snapshots the WHOLE row
list per gesture -- a scene is tens of tiny rows (the biggest shipped one is
11), so a full deep-copy per step costs bytes, not the per-cell delta
machinery the map needs."""

import json

try:
    from editors_base import UndoStack, UndoRedoMixin
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.editors_base import UndoStack, UndoRedoMixin


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


def _copy_rows(rows):
    """Deep-enough copy for snapshots: each row dict copied, flags dict copied
    (flag VALUES are kid-tunable scalars, shared by reference is fine)."""
    out = []
    for r in rows:
        c = dict(r)
        if "flags" in c:
            c["flags"] = dict(c["flags"])
        out.append(c)
    return out


class SceneEditor(UndoRedoMixin):
    """Placement state over one scene's actor rows (#85 Stage 2).

    `n` is the brush -- the sheet tile a placed actor gets; `sel` the selected
    row index (None = nothing selected); `snap` toggles grid placement (moves/
    places quantize to the 8px tile grid); `cam_x`/`cam_y` are the view's
    world-space top-left in PIXELS (world-space coordinates, #85 Section 7).
    Every completed gesture (a placement, a finished drag, a delete, a z-order
    move, a tag/flip commit) is one undo step."""

    UNDO_MAX = 32     # bounded like MapEditor (#91); a step is a tiny row list
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
        self._hist = UndoStack(self.UNDO_MAX)
        self._pre = None      # pre-gesture snapshot ((rows, sel)) while one is open

    # -- serialization -------------------------------------------------------

    def serialize(self):
        """The compact .moyscene JSON blob of the current rows -- the exact
        format widgets.Scenes parses and moy_carts.save_scene persists."""
        return json.dumps(self.rows)

    # -- gesture batching (a drag = ONE undo step) ---------------------------

    def begin_edit(self):
        """Open an edit gesture: snapshot the rows once. Idempotent while open."""
        if self._pre is None:
            self._pre = (_copy_rows(self.rows), self.sel)

    def end_edit(self):
        """Close the gesture: commit its pre-snapshot iff the rows actually
        changed (a select-only tap or a zero-move drag records nothing).
        Returns True when an undo step was recorded."""
        pre = self._pre
        self._pre = None
        if pre is not None and pre[0] != self.rows:
            self._hist.push(pre)
            self.dirty = True
            return True
        return False

    def abort_edit(self):
        """Discard the open gesture AND restore the pre-snapshot (a drag that
        turned out to be a pan must leave the rows untouched)."""
        pre = self._pre
        self._pre = None
        if pre is not None:
            self.rows = pre[0]
            self.sel = pre[1]

    def _commit(self, mutate):
        """Run `mutate()` as one self-contained undo step."""
        self.begin_edit()
        mutate()
        return self.end_edit()

    # -- undo/redo (UndoRedoMixin over full-list snapshots) ------------------

    def _hist_before(self):
        if self._pre is not None:
            self.end_edit()

    def _hist_reverse(self, entry):
        return (_copy_rows(self.rows), self.sel)

    def _hist_apply(self, entry, is_redo):
        self.rows, self.sel = entry
        if self.sel is not None and not (0 <= self.sel < len(self.rows)):
            self.sel = None
        self.dirty = True

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

        def mutate():
            self.rows.append({"tag": str(tag)[:self.TAG_MAX], "tile": self.n,
                              "x": x, "y": y})
            self.sel = len(self.rows) - 1
        self._commit(mutate)
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

        def mutate():
            r["x"] = r["x"] + dx * step
            r["y"] = r["y"] + dy * step
        return self._commit(mutate)

    def delete_sel(self):
        """Remove the selected actor. One undo step."""
        if self.selected() is None:
            return False

        def mutate():
            self.rows.pop(self.sel)
            self.sel = None
        return self._commit(mutate)

    # -- z-order (list order IS draw order, #85 Section 2) -------------------

    def front_sel(self):
        """Send the selected actor to the FRONT (end of the list -- drawn last,
        on top). One undo step; a no-op when it's already frontmost."""
        r = self.selected()
        if r is None or self.sel == len(self.rows) - 1:
            return False

        def mutate():
            row = self.rows.pop(self.sel)
            self.rows.append(row)
            self.sel = len(self.rows) - 1
        return self._commit(mutate)

    def back_sel(self):
        """Send the selected actor to the BACK (start of the list -- drawn
        first, behind everything). One undo step."""
        r = self.selected()
        if r is None or self.sel == 0:
            return False

        def mutate():
            row = self.rows.pop(self.sel)
            self.rows.insert(0, row)
            self.sel = 0
        return self._commit(mutate)

    # -- props (the inline tag/flip row, #85 Section 4) ----------------------

    def set_tag(self, tag):
        """Set the selected actor's tag (length-capped). One undo step; a
        same-tag set records nothing."""
        r = self.selected()
        if r is None:
            return False
        return self._commit(lambda: r.__setitem__("tag", str(tag)[:self.TAG_MAX]))

    def toggle_flip(self):
        """Flip the selected actor horizontally (0 <-> 1). One undo step."""
        r = self.selected()
        if r is None:
            return False

        def mutate():
            if r.get("flip"):
                del r["flip"]
            else:
                r["flip"] = 1
        return self._commit(mutate)

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

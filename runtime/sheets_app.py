"""Sheets -- the kid spreadsheet system app (#78, the Desk Lab trio's third tile).

Presented like Writer/Paint: a `.moy` cartridge identity on the launcher
(`sheets.moy`, so it seeds/versions/exports like every cart) backed by a
responsive SYSTEM process the console spawns instead of the Player -- a grid app
must reflow to a P4/web window, while Player is the fixed 320x240 contract.

A sheet is a tiny grid of cells. A cell holds a literal (`3`, `hello`) or a
formula that starts with `=` (`=A1+B1*2`, `=sum(A1:A5)`). Formulas are compiled by
the hand-rolled engine in `runtime/formula.py` (the portable subset bans
eval/exec) and reuse the block-operator vocabulary (#48: mod/round/abs/min/max) so
Sheets and Blocks teach the same words. A reference cycle shows `#LOOP`, a bad
formula `#ERR` -- never a crash.

Each sheet is a USER FILE (#108): a named `files/tables/*.moysheet` item in the
store, auto-named (`table_1`, ...), browsed through the SHARED `FileGridView`
picker (the exact widget Paint's OPEN mode + the Files app use) and AUTOSAVED on
every cell commit + view change. The legacy single-file `sheets.json` workbook
is migrated once (`moy_carts.migrate_tables`) into one file per sheet. A sheet's
`moysheet-v1` blob is exactly what `table(name)` reads (#78), so copying it into
a cart works unchanged.

The ATTACH flow (#78, preserved on the named-file model): from an open sheet's
grid an ATTACH button opens a picker listing every GAME/story cart on the store
(the SAME row-list widget the shell's list views use -- ListShellLayout row_rect,
no new chrome), and picking one copies the sheet's CURRENT computed cells into
that cart's folder as tables/<slug>.moysheet via `moy_carts.save_table` -- the
same blob `table(name)` reads. The slug now comes from the sheet's file name, and
the sheet stays open + editable in Sheets after attaching (copy, not move)."""

try:
    import ui as _ui
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui as _ui

import json

try:
    from formula import Sheet, index_to_col, ERR, LOOP
except ImportError:  # pragma: no cover - direct host import
    from runtime.formula import Sheet, index_to_col, ERR, LOOP

try:
    from file_widgets import FileGridView
except ImportError:  # pragma: no cover - direct host import
    from runtime.file_widgets import FileGridView

try:
    from app_shell import ListShellLayout, ListShellApp
except ImportError:  # pragma: no cover - direct host import
    from runtime.app_shell import ListShellLayout, ListShellApp

try:
    from op_history import History, OpCodec
except ImportError:  # pragma: no cover - direct host import
    from runtime.op_history import History, OpCodec


DEFAULT_COLS = 8
DEFAULT_ROWS = 20
CELL_MAX = 48            # chars of raw formula/text per cell
MAX_NAME = 24            # rename entry cap
_ERRORS = (ERR, LOOP)


class _SheetCellCodec(OpCodec):
    """The #111 op codec for a formula.Sheet: an op is `{"c": col, "r": row,
    "o": old_raw, "n": new_raw}` -- a plain dict of ints/strings (MicroPython-
    safe, JSON-able as-is). invert() is O(1): write the OLD raw text back and
    let Sheet.set_cell's own recalc() recompute every dependent formula, so
    undo/redo never needs to touch `values` by hand. snapshot() backs
    History.keyframe() (the sidecar's periodic full-state checkpoint); undo/
    redo always go through invert()/apply(), so restore() is never needed."""

    def apply(self, doc, op):
        doc.set_cell(op["c"], op["r"], op["n"])

    def invert(self, doc, op):
        doc.set_cell(op["c"], op["r"], op["o"])

    def snapshot(self, doc):
        return doc.to_dict()


def _ops_since_keyframe(recs):
    """Flatten every op-segment recorded AFTER the last keyframe in `recs` (a
    moy_carts.load_history() list) into one ordered list of ops -- the same
    "keyframe + trailing segments" window moy_carts.prune_history keeps on
    disk. The doc these ops apply to is loaded from the sheet's OWN
    files/tables/<name>.moysheet file (the source of truth), not replayed from
    here -- this only rebuilds History's in-RAM undo STACK on a reopen."""
    last_kf = -1
    for i, rec in enumerate(recs):
        if rec.get("t") == "kf":
            last_kf = i
    ops = []
    for rec in recs[last_kf + 1:]:
        if rec.get("t") == "seg":
            ops.extend(rec.get("ops") or [])
    return ops


def _fmt(v, width):
    """A computed value as a short display string that fits `width` chars."""
    if isinstance(v, float):
        if v == int(v):
            v = int(v)
        else:
            v = round(v, 3)
    s = str(v)
    if len(s) > width:
        s = s[:width]
    return s


class SheetsLayout(ListShellLayout):
    """Responsive geometry: a toolbar band, a formula-entry row, then the grid
    (column headers + row headers + cells). Reflows to any window size (#39)."""

    MIN_W = 200
    MIN_H = 200

    def __init__(self, w, h, fs=1, windowed=False, cols=DEFAULT_COLS):
        self._init_frame(w, h, fs, windowed)
        fs = self.fs
        self.cols = int(cols)
        self.toolbar_h = 24 * fs
        x = 6 * fs
        y = self.bar_h + 3 * fs
        bh = self.toolbar_h - 6 * fs
        # Grid-view toolbar: SHEETS (back), CLEAR (cell), RENAME, TRASH, ATTACH.
        self.back_btn = (x, y, 48 * fs, bh)
        self.clr_btn = (x + 52 * fs, y, 44 * fs, bh)
        self.name_btn = (x + 100 * fs, y, 52 * fs, bh)
        self.del_btn = (x + 156 * fs, y, 44 * fs, bh)
        self.attach_btn = (x + 204 * fs, y, 54 * fs, bh)  # ATTACH to a game (#78)
        # List-view: + NEW.
        self.new_btn = (x, y, 62 * fs, bh)
        self.status_x = x + 262 * fs
        self.status_y = y + max(0, (bh - 8 * fs) // 2)
        # Formula entry row: the raw text of the selected cell, edited in place.
        self.entry_h = 14 * fs
        ex = x - 2 * fs
        ey = self.bar_h + self.toolbar_h
        ew_full = self.w - 2 * (x - 2 * fs)
        # Compact icon-only undo/redo pair (#111 phase 3): the toolbar row above
        # is already full at the 320px tier (five chips fill it edge to edge, and
        # the status label there is already squeezed to a handful of chars), but
        # the formula-entry row is nearly EMPTY -- it just shows a short
        # "A1: text" ref+value. Anchoring undo/redo to its right end costs that
        # row only ~30px of a ~300px budget instead of starving the toolbar's
        # status text further, so this is the least-cramped single-row option
        # (no second row, no shrinking the toolbar chips).
        ubtn = self.entry_h
        ugap = 1 * fs
        self.redo_btn = (ex + ew_full - ubtn, ey, ubtn, self.entry_h)
        self.undo_btn = (self.redo_btn[0] - ubtn - ugap, ey, ubtn, self.entry_h)
        self.entry = (ex, ey, self.undo_btn[0] - ex - 2 * fs, self.entry_h)
        # The grid.
        self.rowhdr_w = 20 * fs
        self.colhdr_h = 10 * fs
        self.gx = 2 * fs
        self.gy = self.bar_h + self.toolbar_h + self.entry_h + 2 * fs + self.colhdr_h
        avail_w = self.w - self.gx - self.rowhdr_w - 2 * fs
        self.cell_w = max(24 * fs, avail_w // max(1, self.cols))
        self.cell_h = 12 * fs
        self.vis_cols = max(1, min(self.cols, avail_w // self.cell_w))
        self.vis_rows = max(1, (self.h - self.gy - 2 * fs) // self.cell_h)
        # The picker (list view): the shared thumbnail grid fills the body.
        gy_body = self.bar_h + self.toolbar_h + 2 * fs
        self.body = (4 * fs, gy_body, self.w - 8 * fs,
                     max(40, self.h - gy_body - 4 * fs))
        # The attach view reuses the ListShellLayout row geometry below the toolbar.
        self._init_list(self.bar_h + self.toolbar_h)

    def cell_rect(self, ci, ri, left, top):
        """Screen rect for grid column `ci`, row `ri` given the scroll origin."""
        x = self.gx + self.rowhdr_w + (ci - left) * self.cell_w
        y = self.gy + (ri - top) * self.cell_h
        return (x, y, self.cell_w - 1, self.cell_h - 1)


class SheetsAppLayer(ListShellApp):
    """A sheet picker (shared FileGridView) -> one sheet's grid, over the
    formula.Sheet model on named files/tables/*.moysheet user files (#108),
    with an ATTACH picker that copies the open sheet into a game cart (#78)."""

    id = "sheets"
    domain = "system"
    RENAME_MAX = MAX_NAME
    # The shipped identity (ListShellApp.is_app gates on these).
    APP_TITLE = "Sheets"
    APP_PERM = "sheets"
    APP_FOLDER = "sheets.moy"
    TITLE = "SHEETS"

    def __init__(self, ws, names, in_rect):
        self.ws = ws
        self.names = names
        self._in = in_rect
        self.layout = SheetsLayout(ws.sys_canvas.w, ws.sys_canvas.h,
                                   ws._effective_font_scale(), ws.windowed_chrome)
        self.mode = "list"            # list | grid | rename | attach
        self.grid = FileGridView(ws, "tables")
        self.sheet = None             # the open formula.Sheet
        self.sheet_name = None        # its files/tables/<name> file name
        self.history = None           # op_history.History over the open sheet (#111)
        self.cur_col = 0              # grid selection
        self.cur_row = 0
        self.left = 0                 # grid scroll origin
        self.gtop = 0
        self.editing = False          # the formula entry is open for typing
        self.edit_buf = ""            # the raw text being typed
        self.status = "MY SHEETS"
        self._ekey_prev = 0
        self._unsaved = False         # a change since the last flush
        self.rename_text = ""
        self._pending_open = None     # a name to open on the next open() (Files jump)
        self._save_failed = False
        self.sel = 0                  # attach-list selection
        self.top = 0                  # attach-list scroll origin

    # -- store ---------------------------------------------------------------
    # (is_app / _store_ready / _load_blob / _persist / _edge_key: ListShellApp)

    def open_named(self, name):
        """Point Sheets at a named sheet to open on its next open() -- the Files
        app's OPEN verb (tables open in Sheets)."""
        self._pending_open = name

    def flush(self, force=False):
        """Persist the open sheet to its files/tables/<name>.moysheet file. The
        autosave verb: commits any open edit, then writes when something changed
        (or force). Cheap to call, no-ops on an unchanged sheet."""
        self._commit_edit()
        if self.sheet is None or self.sheet_name is None:
            return True
        if not (self._unsaved or force):
            return True
        if not self._store_ready():
            self._save_failed = True
            self.status = "CAN'T SAVE HERE"
            return False
        name = self.sheet_name
        blob = json.dumps(self.sheet.to_dict())
        ok = self._persist(lambda: self.ws.carts_store.save_file(
            "tables", name, blob, self.ws.carts_root))
        if ok:
            self._unsaved = False
            self.grid.invalidate(name)
            self._flush_history(name)
        return ok

    def _flush_history(self, name):
        """The #111 op-history sidecar write, at the SAME autosave point as the
        sheet file itself (just above): drain History's pending ops (+ a fresh
        keyframe once History.needs_keyframe() trips) into
        files/.history/tables/<name>.jsonl via moy_carts.history_commit. Best-
        effort and silent -- the .moysheet file above is already the durable
        save, so a sidecar write failure only costs undo depth, never data."""
        hist = self.history
        if hist is None:
            return
        kf = hist.keyframe() if hist.needs_keyframe() else None
        ops = hist.flush()
        if kf is None and not ops:
            return
        try:
            self.ws._with_sd(lambda: self.ws.carts_store.history_commit(
                "tables", name, ops, keyframe=kf, root=self.ws.carts_root))
        except Exception:  # noqa: BLE001 -- best-effort sidecar, never crash the shell
            return
        if kf is not None:
            hist.mark_keyframe()

    # -- lifecycle -----------------------------------------------------------

    def relayout(self, w, h, fs):
        cols = self.sheet.cols if self.sheet is not None else DEFAULT_COLS
        self.layout = SheetsLayout(w, h, fs, self.ws.windowed_chrome, cols)
        self._scroll_grid()

    def open(self):
        if self._store_ready():
            try:
                self.ws._with_sd(
                    lambda: self.ws.carts_store.migrate_tables(self.ws.carts_root))
            except Exception:  # noqa: BLE001 -- migration is best-effort
                pass
        self.grid.refresh()
        pending = self._pending_open
        self._pending_open = None
        if pending and pending in self.grid.names:
            self._open_file(pending)
        else:
            self.mode = "list"
            self.sheet = None
            self.sheet_name = None
            self.history = None
            self.status = "MY SHEETS"
        self.editing = False
        self.edit_buf = ""
        self._ekey_prev = 0
        self.ws._dirty = True

    # -- sheet verbs ---------------------------------------------------------

    def _enter_grid(self, sheet, name):
        self.flush()
        self.sheet = sheet
        self.sheet_name = name
        self.mode = "grid"
        self.cur_col = 0
        self.cur_row = 0
        self.left = 0
        self.gtop = 0
        self.editing = False
        self.edit_buf = ""
        self._ekey_prev = 0
        self.layout = SheetsLayout(self.layout.w, self.layout.h, self.layout.fs,
                                   self.ws.windowed_chrome, sheet.cols)
        self.status = name.upper()
        self.history = self._build_history(sheet, name)
        self.ws._dirty = True

    def _build_history(self, sheet, name):
        """A fresh op_history.History over `sheet`, its undo stack rebuilt from
        the #111 sidecar so a REOPENED sheet keeps undo depth across sessions
        (not just within the one that made the edits). This is NOT a doc replay
        -- `sheet` was just loaded from its own .moysheet FILE (the source of
        truth), so it is already at the state the sidecar's ops produced;
        History.seed() only primes the in-RAM undo STACK with the ops recorded
        since the last on-disk keyframe. A brand-new sheet (no sidecar yet)
        seeds an empty stack -- load_history() degrades to [] on a missing
        file, same as everywhere else in moy_carts."""
        hist = History(sheet, _SheetCellCodec())
        if self._store_ready():
            try:
                recs = self.ws._with_sd(lambda: self.ws.carts_store.load_history(
                    "tables", name, self.ws.carts_root))
            except Exception:  # noqa: BLE001 -- a bad/missing sidecar just starts empty
                recs = None
            if recs:
                hist.seed(_ops_since_keyframe(recs))
        return hist

    def _open_file(self, name):
        blob = None
        if self._store_ready():
            try:
                blob = self.ws._with_sd(lambda: self.ws.carts_store.load_file(
                    "tables", name, self.ws.carts_root))
            except Exception:  # noqa: BLE001
                blob = None
        data = None
        if blob:
            try:
                data = json.loads(blob)
            except (ValueError, TypeError):
                data = None
        sheet = Sheet.from_dict(data) if isinstance(data, dict) else Sheet()
        self._enter_grid(sheet, name)

    def _new_sheet(self):
        self.flush()
        name = None
        if self._store_ready():
            try:
                name = self.ws._with_sd(lambda: self.ws.carts_store.new_file_name(
                    "tables", self.ws.carts_root))
            except Exception:  # noqa: BLE001
                name = None
        name = name or "table_1"
        sheet = Sheet(name, DEFAULT_ROWS, DEFAULT_COLS)
        self._enter_grid(sheet, name)
        self._unsaved = True            # a new sheet writes its first file eagerly
        self.flush()

    def _back_to_list(self):
        self.flush(force=True)
        keep = self.sheet_name
        self.mode = "list"
        self.sheet = None
        self.sheet_name = None
        self.history = None
        self.editing = False
        self.grid.refresh()
        self.grid.select(keep)
        self.status = "MY SHEETS"
        self.ws._dirty = True

    def _delete_current(self):
        if self.sheet_name is None:
            return
        name = self.sheet_name
        if self._persist(lambda: self.ws.carts_store.delete_file(
                "tables", name, self.ws.carts_root)):
            self.grid.invalidate(name)
            self.status = "IN TRASH"
        self.sheet = None
        self.sheet_name = None
        self.history = None
        self._unsaved = False
        self.mode = "list"
        self.grid.refresh()
        self.grid.select(None)
        self.ws._dirty = True

    def _begin_rename(self):
        if self.sheet_name is None:
            return
        self.rename_text = self.sheet_name[:MAX_NAME]
        self._ekey_prev = 0
        self.mode = "rename"
        self.status = "TYPE A NAME"
        self.ws._dirty = True

    def _rename_commit(self):
        name = self.sheet_name
        text = self.rename_text
        new = [name]

        def _do():
            new[0] = self.ws.carts_store.rename_file(
                "tables", name, text, self.ws.carts_root)

        if name and self._persist(_do):
            self.grid.invalidate(name)
            self.sheet_name = new[0]
            if self.sheet is not None:
                self.sheet.name = new[0]
            self.status = new[0].upper()
        self.mode = "grid"
        self.ws._dirty = True

    # -- attach to a game (#78: the Sheets-to-game UI, table() feeds it) ------
    #
    # `moy_carts.save_table` (the write) already existed for a hand-placed file;
    # this is the picker flow, preserved on the #108 named-file model. Reuses the
    # shell's row-list geometry (ListShellLayout.row_rect + _list_nav) over the
    # eligible GAME/story carts, and copies the OPEN sheet's cells into the chosen
    # cart as tables/<file-name slug>.moysheet -- the same moysheet-v1 blob the
    # sheet's own file holds, decoded by table() at cart load.

    def _attach_targets(self):
        """Every cart a sheet can be attached to: GAME/story carts with a store
        path -- table() is game data (inventories/waves/scores), so system apps
        (Sheets/Writer/Storybook/... are type 'app') and wallpapers are excluded."""
        return [c for c in self.ws._all_carts
                if c.get("path") and c.get("type") in ("game", "story")]

    def _open_attach(self):
        if self.sheet is None:
            return
        if not self._store_ready():
            self.status = "CAN'T ATTACH HERE"
            self.ws._dirty = True
            return
        self._commit_edit()
        self.mode = "attach"
        self.sel = 0
        self.top = 0
        self.status = "ATTACH TO WHICH GAME?"
        self.ws._dirty = True

    def _close_attach(self):
        self.mode = "grid"
        self.status = (self.sheet_name or "SHEETS").upper()
        self.ws._dirty = True

    def _attach_to(self, cart):
        """Write the open sheet's CURRENT computed cells into `cart`'s folder as
        tables/<slug>.moysheet (moy_carts.save_table -- the same moysheet-v1 blob
        the sheet's own file holds, decoded by table() at cart load)."""
        if self.sheet is None or not self._store_ready():
            self.status = "CAN'T SAVE HERE"
            self.ws._dirty = True
            return
        store = self.ws.carts_store
        name = store.slug(self.sheet_name or self.sheet.name)
        blob = json.dumps(self.sheet.to_dict())
        try:
            self.ws._with_sd(lambda: store.save_table(cart, name, blob))
        except Exception as exc:  # noqa: BLE001 -- surface, never crash the shell
            self.status = ("ATTACH FAILED " + str(exc))[:28]
            self.ws._dirty = True
            return
        self.status = ("ATTACHED TO " + (cart.get("title") or "GAME"))[:28]
        self.mode = "grid"
        self.ws._dirty = True

    def _tap_row(self, i):
        """The shared _list_nav A-button verb -- only the attach picker uses it
        (the sheet picker is the FileGridView)."""
        if self.mode == "attach":
            targets = self._attach_targets()
            if 0 <= i < len(targets):
                self._attach_to(targets[i])

    # -- cell editing --------------------------------------------------------

    def _cur_raw(self):
        if self.sheet is None:
            return ""
        return self.sheet.raw_at(self.cur_col, self.cur_row)

    def _begin_edit(self, seed=None):
        self.editing = True
        self.edit_buf = self._cur_raw() if seed is None else seed
        self.ws._dirty = True

    def _commit_edit(self):
        if not self.editing or self.sheet is None:
            return
        before = self._cur_raw()
        after = self.edit_buf
        self.sheet.set_cell(self.cur_col, self.cur_row, after)
        if after != before:
            self._unsaved = True
            self._record_cell_op(self.cur_col, self.cur_row, before, after)
        self.editing = False
        self.edit_buf = ""

    def _record_cell_op(self, col, row, before, after):
        """The #111 op-history record point: the surface has ALREADY applied
        the change to `self.sheet` (History.record()'s contract) -- one op per
        cell commit, carrying its own pre/post text so undo/redo is O(1)."""
        if self.history is not None:
            self.history.record({"c": col, "r": row, "o": before, "n": after})

    def _move(self, dc, dr):
        if self.editing:
            self._commit_edit()
        if self.sheet is None:
            return
        self.cur_col = max(0, min(self.sheet.cols - 1, self.cur_col + dc))
        self.cur_row = max(0, min(self.sheet.rows - 1, self.cur_row + dr))
        self._scroll_grid()
        self.status = index_to_col(self.cur_col) + str(self.cur_row + 1)
        self.ws._dirty = True

    def _scroll_grid(self):
        lay = self.layout
        if self.cur_col < self.left:
            self.left = self.cur_col
        elif self.cur_col >= self.left + lay.vis_cols:
            self.left = self.cur_col - lay.vis_cols + 1
        if self.cur_row < self.gtop:
            self.gtop = self.cur_row
        elif self.cur_row >= self.gtop + lay.vis_rows:
            self.gtop = self.cur_row - lay.vis_rows + 1

    def _copy_cell(self):
        """Copy the current cell's raw text to the system clipboard (#132)."""
        clip = getattr(self.ws, "clipboard", None)
        if clip is None or self.sheet is None:
            return
        clip.put_text(self.edit_buf if self.editing else self._cur_raw())
        self.status = "COPIED"
        self.ws._dirty = True

    def _paste_cell(self):
        """Paste the system clipboard into the current cell (#132) -- naive v1:
        multi-line text lands as its first line. A cell edit like any other,
        so the same cell OpCodec makes it undoable (the _clear_cell shape)."""
        clip = getattr(self.ws, "clipboard", None)
        if clip is None or self.sheet is None or not clip.text():
            return
        t = clip.text().split("\n")[0][:CELL_MAX]
        if self.editing:
            self.edit_buf = (self.edit_buf + t)[:CELL_MAX]
            self.ws._dirty = True
            return
        before = self._cur_raw()
        self.sheet.set_cell(self.cur_col, self.cur_row, t)
        if t != before:
            self._unsaved = True
            self._record_cell_op(self.cur_col, self.cur_row, before, t)
        self.status = index_to_col(self.cur_col) + str(self.cur_row + 1)
        self.ws._dirty = True

    def _clear_cell(self):
        # CLEAR (#111 phase 3 marquee win): this is a cell edit to "" like any
        # other, so the SAME cell OpCodec makes it undoable -- no separate
        # "clear" op type needed.
        if self.sheet is None:
            return
        self.editing = False
        self.edit_buf = ""
        before = self._cur_raw()
        self.sheet.set_cell(self.cur_col, self.cur_row, "")
        if before != "":
            self._unsaved = True
            self._record_cell_op(self.cur_col, self.cur_row, before, "")
        self.status = index_to_col(self.cur_col) + str(self.cur_row + 1)
        self.ws._dirty = True

    # -- undo / redo (#111): one History per open sheet ------------------------

    def _cancel_edit(self):
        """Drop an in-progress, uncommitted edit WITHOUT recording it -- undo/
        redo only walk COMMITTED ops, so a stray open edit must not become one
        via _commit_edit()'s normal path."""
        self.editing = False
        self.edit_buf = ""

    def _undo(self):
        if self.sheet is None or self.history is None or not self.history.can_undo():
            return
        if self.editing:
            self._cancel_edit()
        op = self.history.undo()
        if op is not None:
            self._unsaved = True
            self.cur_col, self.cur_row = op["c"], op["r"]
            self._scroll_grid()
            self.status = "UNDO " + index_to_col(op["c"]) + str(op["r"] + 1)
        self.ws._dirty = True

    def _redo(self):
        if self.sheet is None or self.history is None or not self.history.can_redo():
            return
        if self.editing:
            self._cancel_edit()
        op = self.history.redo()
        if op is not None:
            self._unsaved = True
            self.cur_col, self.cur_row = op["c"], op["r"]
            self._scroll_grid()
            self.status = "REDO " + index_to_col(op["c"]) + str(op["r"] + 1)
        self.ws._dirty = True

    # -- input ---------------------------------------------------------------

    def handle_input(self, inp):
        if self.mode == "list":
            hit = self.grid.nav(inp)
            if hit and hit[0] == "pick":
                self._open_file(hit[1])
            elif hit:
                self.status = hit[1].upper()
            return True
        if self.mode == "rename":
            self._typed_rename(inp)
            return True
        if self.mode == "attach":
            # the ATTACH target rows -- the shared list nav (A opens a target)
            return self._list_nav(inp, max(1, len(self._attach_targets())))
        # -- grid: trackball arrows move the selection, keys type into a cell ----
        if inp.pressed("left"):
            self._move(-1, 0)
        elif inp.pressed("right"):
            self._move(1, 0)
        elif inp.pressed("up"):
            self._move(0, -1)
        elif inp.pressed("down"):
            self._move(0, 1)
        elif inp.pressed("a"):
            if self.editing:
                self._commit_edit()
            else:
                self._begin_edit()
        self._typed_keys(inp)
        return True

    def _typed_keys(self, inp):
        if self.sheet is None:
            return
        k = self._edge_key(inp)
        if k:
            if k == 0x1A:                        # Ctrl+Z: undo (#111, code_layer parity)
                self._undo()
            elif k == 0x19:                      # Ctrl+Y: redo
                self._redo()
            elif k == 0x03:                      # Ctrl+C: cell -> system clipboard (#132)
                self._copy_cell()
            elif k == 0x16:                      # Ctrl+V: system clipboard -> cell (#132)
                self._paste_cell()
            elif k == 0x18:                      # Ctrl+X: copy + clear (#132)
                self._copy_cell()
                if not self.editing:
                    self._clear_cell()
            elif self.editing:
                if k in (0x0D, 0x0A):            # Enter: commit + step down
                    self._commit_edit()
                    self._move(0, 1)
                elif k in (0x08, 0x7F):          # Backspace
                    self.edit_buf = self.edit_buf[:-1]
                    self.ws._dirty = True
                elif 0x20 <= k <= 0x7E and len(self.edit_buf) < CELL_MAX:
                    self.edit_buf += chr(k)
                    self.ws._dirty = True
            else:
                if k in (0x0D, 0x0A):            # Enter: open the cell for editing
                    self._begin_edit()
                elif k in (0x08, 0x7F):          # Backspace: clear the cell
                    self._clear_cell()
                elif 0x20 <= k <= 0x7E:          # a keystroke starts a fresh entry
                    self._begin_edit("")
                    self.edit_buf = chr(k)


    def handle_pointer(self, px, py, click):
        ws = self.ws
        lay = self.layout
        if click and not ws.windowed_chrome and py < lay.bar_h:
            self.flush(force=True)              # the X must never lose data
            return bool(ws.bar_layer.handle_bar_tap("tool", px, py))
        if self.mode == "list":
            if not click:
                return True
            if self._in(px, py, lay.new_btn):
                self._new_sheet()
                return True
            hit = self.grid.tap(px, py)
            if hit and hit[0] in ("pick", "sel"):
                self._open_file(hit[1])
            return True
        if self.mode == "rename":
            if click and self._in(px, py, lay.del_btn):
                self._rename_commit()
            return True
        if self.mode == "attach":
            if not click:
                return True
            if self._in(px, py, lay.back_btn):
                self._close_attach()
                return True
            targets = self._attach_targets()
            for i in range(self.top, min(self.top + lay.list_rows, len(targets))):
                if self._in(px, py, lay.row_rect(i - self.top)):
                    self.sel = i
                    self._attach_to(targets[i])
                    return True
            return True
        # -- grid view -----------------------------------------------------------
        if not click:
            return True
        if self._in(px, py, lay.back_btn):
            self._back_to_list()
            return True
        if self._in(px, py, lay.clr_btn):
            self._clear_cell()
            return True
        if self._in(px, py, lay.name_btn):
            self._begin_rename()
            return True
        if self._in(px, py, lay.del_btn):
            self._delete_current()
            return True
        if self._in(px, py, lay.attach_btn):
            self._open_attach()
            return True
        if self._in(px, py, lay.undo_btn):
            self._undo()
            return True
        if self._in(px, py, lay.redo_btn):
            self._redo()
            return True
        # A tap inside the grid selects that cell (and commits any open edit).
        if self.sheet is not None:
            for ri in range(self.gtop, min(self.gtop + lay.vis_rows,
                                           self.sheet.rows)):
                for ci in range(self.left, min(self.left + lay.vis_cols,
                                               self.sheet.cols)):
                    if self._in(px, py, lay.cell_rect(ci, ri, self.left, self.gtop)):
                        if self.editing:
                            self._commit_edit()
                        self.cur_col = ci
                        self.cur_row = ri
                        self.status = index_to_col(ci) + str(ri + 1)
                        self.ws._dirty = True
                        return True
        return True

    # -- draw ----------------------------------------------------------------

    def _button(self, cv, label, r, hot=False):
        _ui.chip(cv, self.ws.theme_colors, r, label, hot=hot, fs=self.layout.fs)

    def draw(self, dt):
        cv = self.ws.sys_canvas
        lay = self.layout
        th = self.ws.theme_colors
        cv.cls(th["panel"])
        _ui.toolbar(cv, th, (0, lay.bar_h, lay.w, lay.toolbar_h))
        if self.mode == "grid":
            self._button(cv, "SHEETS", lay.back_btn)
            self._button(cv, "CLEAR", lay.clr_btn)
            self._button(cv, "RENAME", lay.name_btn)
            self._button(cv, "TRASH", lay.del_btn)
            self._button(cv, "ATTACH", lay.attach_btn)
            label = self.status[:max(1, (lay.w - lay.status_x) // (8 * lay.fs) - 1)]
            cv.print(label, lay.status_x, lay.bar_h + 8 * lay.fs, th["title_ink"], 1)
            self._draw_grid(cv)
        elif self.mode == "rename":
            self._button(cv, "OK", lay.del_btn, hot=True)
            self._draw_rename(cv)
        elif self.mode == "attach":
            self._button(cv, "BACK", lay.back_btn)
            sx = lay.back_btn[0] + lay.back_btn[2] + 6 * lay.fs
            cv.print(self.status[:max(1, (lay.w - sx) // (8 * lay.fs) - 1)],
                     sx, lay.bar_h + 8 * lay.fs, th["title_ink"], 1)
            self._draw_attach(cv)
        else:
            self._button(cv, "+ NEW", lay.new_btn)
            cv.print(self.status[:max(1, lay.w // (8 * lay.fs) - 2)],
                     lay.new_btn[0] + lay.new_btn[2] + 8 * lay.fs,
                     lay.bar_h + 8 * lay.fs, th["title_ink"], 1)
            self.grid.set_rect(lay.body, lay.fs)
            self.grid.draw(cv, th)
        if not self.ws.windowed_chrome:
            self.ws.bar_layer._draw_status_strip("tool")

    def _draw_rename(self, cv):
        lay = self.layout
        th = self.ws.theme_colors
        fs = lay.fs
        ex, ey, ew, eh = lay.entry
        cv.rect(ex, ey, ew, eh, self.names["white"])
        cv.rectb(ex, ey, ew, eh, th.get("accent", 10))
        cv.print(self.rename_text + "_", ex + 4 * fs, ey + (eh - 8 * fs) // 2,
                 self.names["black"], 1)

    def _draw_attach(self, cv):
        lay = self.layout
        th = self.ws.theme_colors
        fs = lay.fs
        targets = self._attach_targets()
        if not targets:
            cv.print("NO GAMES YET", 6 * fs, lay.list_y + 4 * fs, th["dim"], 1)
            return
        for i in range(self.top, min(self.top + lay.list_rows, len(targets))):
            x, y, w, h = lay.row_rect(i - self.top)
            selected = i == self.sel
            cv.rect(x, y, w, h, 7)
            title = targets[i].get("title") or "GAME"
            cv.print(title[:max(1, w // (8 * fs) - 2)],
                     x + 6 * fs, y + (h - 8 * fs) // 2, 0, 1)
            cv.rectb(x, y, w, h, th["accent"] if selected else th["dim"])

    def _draw_undo_redo(self, cv):
        """The #111 op-history undo/redo pair: compact icon-only buttons (the
        chrome #88 undo/redo glyphs) at the right end of the formula-entry row.
        Dimmed via History.can_undo()/can_redo() -- exactly the shared contract
        the Editor bar icons will read too."""
        lay = self.layout
        hist = self.history
        self._icon_btn(cv, "undo", lay.undo_btn, bool(hist and hist.can_undo()))
        self._icon_btn(cv, "redo", lay.redo_btn, bool(hist and hist.can_redo()))

    def _icon_btn(self, cv, kind, r, enabled):
        th = self.ws.theme_colors
        x, y, w, h = r
        cv.rect(x, y, w, h, th["panel"])
        cv.rectb(x, y, w, h, th["accent"] if enabled else th["dim"])
        self.ws._glyph(kind, r, th["title_ink"] if enabled else th["dim"], cv)

    def _draw_grid(self, cv):
        lay = self.layout
        th = self.ws.theme_colors
        fs = lay.fs
        sheet = self.sheet
        if sheet is None:
            return
        # Formula entry row: the selected cell's raw text (editable in place).
        ex, ey, ew, eh = lay.entry
        cv.rect(ex, ey, ew, eh, self.names["black"])
        cv.rectb(ex, ey, ew, eh, th["accent"] if self.editing else th["dim"])
        ref = index_to_col(self.cur_col) + str(self.cur_row + 1)
        shown = self.edit_buf if self.editing else self._cur_raw()
        cv.print((ref + ": " + shown)[:max(1, ew // (8 * fs) - 1)],
                 ex + 3 * fs, ey + (eh - 8 * fs) // 2,
                 self.names["green"] if self.editing else self.names["white"], 1)
        self._draw_undo_redo(cv)
        # Column headers (A B C ...).
        colw = max(1, lay.cell_w // (8 * fs))
        for ci in range(self.left, min(self.left + lay.vis_cols, sheet.cols)):
            cx = lay.gx + lay.rowhdr_w + (ci - self.left) * lay.cell_w
            hot = ci == self.cur_col
            cv.print(index_to_col(ci)[:colw], cx + 2 * fs,
                     lay.gy - lay.colhdr_h, th["accent"] if hot else th["dim"], 1)
        # Row headers + cells.
        for ri in range(self.gtop, min(self.gtop + lay.vis_rows, sheet.rows)):
            ry = lay.gy + (ri - self.gtop) * lay.cell_h
            hot_r = ri == self.cur_row
            cv.print(str(ri + 1), lay.gx + 2 * fs, ry + 2 * fs,
                     th["accent"] if hot_r else th["dim"], 1)
            for ci in range(self.left, min(self.left + lay.vis_cols, sheet.cols)):
                x, y, w, h = lay.cell_rect(ci, ri, self.left, self.gtop)
                selected = ci == self.cur_col and ri == self.cur_row
                val = sheet.value_at(ci, ri)
                if val in _ERRORS:
                    cv.rect(x, y, w, h, self.names["red"])
                    fg = self.names["white"]
                else:
                    cv.rect(x, y, w, h, 7 if not selected else th["hilite"])
                    fg = 0 if not selected else th["selection_ink"]
                cv.rectb(x, y, w, h, th["accent"] if selected else th["dim"])
                if val != "":
                    cv.print(_fmt(val, max(1, w // (8 * fs) - 1)),
                             x + 2 * fs, y + 2 * fs, fg, 1)

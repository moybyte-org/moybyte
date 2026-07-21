"""Writer -- the kid notebook system app (notes, stories, project docs).

Presented exactly like Paint: a `.moy` cartridge identity on the launcher
(`writer.moy`, so it seeds/versions/exports like every cart) backed by a
responsive SYSTEM process the console spawns instead of the Player -- a text app
must reflow to a P4/web window, while Player is the fixed 320x240 contract.

Docs are USER FILES (#108): named ``files/docs/*.moytext`` items in the store,
auto-named (`doc_1`, ...), browsed through the SHARED ``FileGridView`` picker
(the exact widget Paint's OPEN mode + the Files app use) and AUTOSAVED on an
idle debounce -- a kid never presses save. The legacy single-file `notes.json`
notebook is migrated once (`moy_carts.migrate_docs`) into one doc file per note.

The buffer/caret core is the shared `CodeEditor` (the same editing behavior a
kid already knows from the Code tab), drawn as a ruled paper page. A doc's body
persists as a tiny `moytext-v1` blob, so copying it into a cart reads back
unchanged through the `text(name)` cart verb (#78). Titling is optional
(RENAME); the auto-name is always visible under the thumbnail."""

try:
    import ui as _ui
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui as _ui


import json

try:
    from editors import CodeEditor
except ImportError:  # pragma: no cover - direct host import
    from runtime.editors import CodeEditor

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


MAX_CHARS = 8000        # per doc -- bounds the SD write + device memory
MAX_NAME = 24           # rename entry cap -- a label, not a paragraph
PAPER = 7               # white -- same paper index Paint uses
INK = 0                 # black

# #111 phase 3: a typing/delete BURST closes (records one op) on a punctuation
# key -- the other edge is Enter (0x0D/0x0A, checked separately) and the #108
# autosave idle debounce (draw()'s existing AUTOSAVE_S timer -- flush() closes
# the live burst before it saves, so "pause to let it autosave" IS "pause to
# get an undo step", no second timer needed).
_BURST_BREAK = ".,!?;:"


def _diff_op(before, after):
    """The smallest (pos, deleted, inserted) edit turning `before` into `after`
    -- a common-prefix/suffix diff over the two burst-edge text snapshots, so
    one op carries a whole typing/delete burst's net change. `pos` + the two
    strings make _WriterOpCodec.invert a trivial swap (ints/strings only, #111
    MicroPython-safe). Returns None for a no-op pair (callers already guard
    before == after -- this is just belt-and-braces)."""
    n = min(len(before), len(after))
    i = 0
    while i < n and before[i] == after[i]:
        i += 1
    max_suffix = n - i
    j = 0
    while j < max_suffix and before[len(before) - 1 - j] == after[len(after) - 1 - j]:
        j += 1
    deleted = before[i:len(before) - j]
    inserted = after[i:len(after) - j]
    if not deleted and not inserted:
        return None
    return ("edit", i, deleted, inserted)


def _place_offset(ed, off):
    """Land the CodeEditor caret at an absolute flat-text offset -- used after
    apply()/invert() rewrite the buffer via set_text() (which always resets
    row/col to the top). Newline counting + the editor's own public goto_row(),
    no reach into its private row/col-offset helpers."""
    text = ed.text()
    off = max(0, min(len(text), off))
    row = text.count("\n", 0, off)
    col = off - (text.rfind("\n", 0, off) + 1)
    ed.goto_row(row, col)


class _WriterOpCodec(OpCodec):
    """#111 phase 3: one op is a whole typing/delete BURST's net effect --
    ("edit", pos, deleted, inserted) as flat character offsets into the doc's
    joined text. apply() and invert() are the SAME shape with deleted/inserted
    swapped, so invert never needs a base snapshot -- op_history.History picks
    the (preferred) invert path automatically. `doc` is the live CodeEditor;
    apply/invert only ever go through its public set_text()/goto_row()."""

    def apply(self, doc, op):
        _, pos, deleted, inserted = op
        text = doc.text()
        doc.set_text(text[:pos] + inserted + text[pos + len(deleted):])
        _place_offset(doc, pos + len(inserted))

    def invert(self, doc, op):
        _, pos, deleted, inserted = op
        text = doc.text()
        doc.set_text(text[:pos] + deleted + text[pos + len(inserted):])
        _place_offset(doc, pos + len(deleted))

    def snapshot(self, doc):
        # Only for History.keyframe() -- the sidecar's full-text keyframe blob
        # when needs_keyframe() trips (#111's segment cap). Undo itself never
        # calls this (invert() is the preferred path checked above).
        return doc.text()


def _body_of(blob):
    """The body string of a moytext-v1 blob (empty on anything malformed)."""
    try:
        data = json.loads(blob)
    except (ValueError, TypeError):
        return ""
    if isinstance(data, dict):
        b = data.get("body", "")
        if isinstance(b, str):
            return b
    return ""


def _encode(body):
    return json.dumps({"format": "moytext-v1", "body": body})


class WriterLayout(ListShellLayout):
    def __init__(self, w, h, fs=1, windowed=False):
        self._init_frame(w, h, fs, windowed)
        fs = self.fs
        self.toolbar_h = 24 * fs
        self.cell = 8 * fs
        self.lh = 10 * fs
        x = 6 * fs
        y = self.bar_h + 3 * fs
        bh = self.toolbar_h - 6 * fs
        # Edit-view toolbar buttons: DOCS (back to the picker), RENAME, TRASH.
        self.back_btn = (x, y, 52 * fs, bh)
        self.name_btn = (x + 56 * fs, y, 58 * fs, bh)
        self.del_btn = (x + 118 * fs, y, 46 * fs, bh)
        # List-view: a single + NEW button (same x as back).
        self.new_btn = (x, y, 62 * fs, bh)
        # Edit-view UNDO/REDO (#111 phase 3): icon-only squares right-aligned
        # with margin `x` (symmetric with the left margin), so the row still
        # fits any responsive width (#39) without moving DOCS/RENAME/TRASH.
        # Clamped to never overlap TRASH at the WM's minimum window width
        # (160*fs, #73's min_w) -- past that floor they clip off the right
        # edge instead, since TRASH must stay the reliably-tappable rect.
        gap = 4 * fs
        redo_x = self.w - x - bh
        undo_x = redo_x - gap - bh
        min_x = self.del_btn[0] + self.del_btn[2] + gap
        if undo_x < min_x:
            undo_x = min_x
            redo_x = undo_x + bh + gap
        self.undo_btn = (undo_x, y, bh, bh)
        self.redo_btn = (redo_x, y, bh, bh)
        self.status_x = x + 170 * fs
        self.status_y = y + max(0, (bh - 8 * fs) // 2)
        # The page (edit view): a text grid sized to the window, like CodeLayout.
        self.tx = 8 * fs
        self.ty = self.bar_h + self.toolbar_h + 3 * fs
        self.cols = max(1, (self.w - 2 * self.tx) // self.cell)
        self.rows = max(1, (self.h - self.ty - 4 * fs) // self.lh)
        self.text_area = (self.tx - 2 * fs, self.ty - 2 * fs,
                          self.cols * self.cell + 4 * fs,
                          self.rows * self.lh + 4 * fs)
        # The picker (list view): the shared thumbnail grid fills the body.
        gy = self.bar_h + self.toolbar_h + 2 * fs
        self.body = (4 * fs, gy, self.w - 8 * fs, max(40, self.h - gy - 4 * fs))
        # Rename entry field (below the toolbar).
        self.entry = (x - 2 * fs, self.bar_h + self.toolbar_h + 2 * fs,
                      self.w - 2 * (x - 2 * fs), 16 * fs)
        # Kept so ListShell helpers that read list geometry stay valid.
        self._init_list(self.bar_h + self.toolbar_h)


class WriterAppLayer(ListShellApp):
    """A doc picker (shared FileGridView) + a ruled-paper text page over the
    shared CodeEditor core, on named files/docs/*.moytext user files (#108)."""

    id = "writer"
    domain = "system"
    TITLE = "WRITER"
    # The shipped identity (ListShellApp.is_app gates on these).
    APP_TITLE = "Writer"
    APP_PERM = "notebook"
    APP_FOLDER = "writer.moy"
    AUTOSAVE_S = 2.5

    def __init__(self, ws, names, in_rect):
        self.ws = ws
        self.names = names
        self._in = in_rect
        self.layout = WriterLayout(ws.sys_canvas.w, ws.sys_canvas.h,
                                   ws._effective_font_scale(), ws.windowed_chrome)
        self.mode = "list"            # list | edit | rename
        self.grid = FileGridView(ws, "docs")
        self.doc_name = None          # the open doc's file name (None = none open)
        self.editor = None            # CodeEditor while mode == "edit"
        self.status = "MY DOCS"
        self._ekey_prev = 0           # typed-key edge tracker (code_layer idiom)
        self._drag = None             # page drag-scroll anchor
        self._unsaved = False         # dirty since the last flush (idle autosave)
        self._idle = 0.0
        self.rename_text = ""
        self._pending_open = None     # a name to open on the next open() (Files jump)
        self._save_failed = False
        self.history = None           # op_history.History over the open doc (#111)
        self._burst_before = None     # text() snapshot at the live burst's start

    # -- store ---------------------------------------------------------------
    # (is_app / _store_ready / _load_blob / _persist / _edge_key: ListShellApp)

    def open_named(self, name):
        """Point Writer at a named doc to open on its next open() -- the Files
        app's OPEN verb (docs open in Writer)."""
        self._pending_open = name

    def flush(self, force=False):
        """Persist the open doc to its files/docs/<name>.moytext file. The
        autosave verb: cheap to call, no-ops when nothing changed. Closes any
        live typing/delete burst into a #111 op FIRST (the idle debounce is
        the natural burst edge, so this is also where an in-progress burst
        becomes one committed undo step) -- even on a no-op text save, so a
        burst is never left dangling across a mode switch."""
        self._close_burst()
        ed = self.editor
        if ed is None or self.doc_name is None:
            return True
        if not (ed.dirty or self._unsaved or force):
            return True
        body = ed.text()[:MAX_CHARS]
        store = self.ws.carts_store
        name = self.doc_name
        ok = self._persist(lambda: store.save_file(
            "docs", name, _encode(body), self.ws.carts_root))
        if ok:
            ed.dirty = False
            self._unsaved = False
            self._idle = 0.0
            self.grid.invalidate(name)
            self._commit_history(name)
        return ok

    # -- op-history (#111 phase 3) --------------------------------------------

    def can_undo(self):
        # A still-open burst (typed but not yet paused/punctuated/flushed) is
        # undoable too -- _do_undo() closes it before walking the History, so
        # the toolbar/dim state must agree with what a press actually does.
        if self.history is None:
            return False
        if self.history.can_undo():
            return True
        ed = self.editor
        return bool(self._burst_before is not None and ed is not None
                    and ed.text() != self._burst_before)

    def can_redo(self):
        return self.history.can_redo() if self.history is not None else False

    def _close_burst(self):
        """Finalize the in-progress typing/delete burst (if any) into ONE op
        on the History: the net text diff since the burst started. Burst EDGES
        are the idle-debounce flush, an Enter/punctuation key, and an undo
        press (so undo always reaches a just-typed, not-yet-paused burst).
        A no-op when nothing is pending, or when the burst net-cancelled back
        to its start (typed then fully backspaced)."""
        before = self._burst_before
        self._burst_before = None
        ed = self.editor
        hist = self.history
        if before is None or ed is None or hist is None:
            return
        after = ed.text()
        if before == after:
            return
        op = _diff_op(before, after)
        if op is not None:
            hist.record(op)

    def _commit_history(self, name):
        """The #111 adapter call: drain the History's pending batch (+ a fresh
        keyframe when the segment cap trips) into the sidecar, at the SAME
        cadence flush() already saves the doc body on. A pure no-op (no ops,
        no keyframe) never touches SD."""
        hist = self.history
        if hist is None:
            return
        kf = hist.keyframe() if hist.needs_keyframe() else None
        ops = hist.flush()
        if not ops and kf is None:
            return
        store = self.ws.carts_store
        if self._persist(lambda: store.history_commit(
                "docs", name, ops, keyframe=kf, root=self.ws.carts_root)):
            if kf is not None:
                hist.mark_keyframe()

    def _seed_history(self, name):
        """Rebuild undo DEPTH (not the doc -- it's already loaded from the
        current save) from the #111 sidecar on doc open: flatten every kept
        segment's ops, oldest .. newest, straight onto the fresh History's
        undo stack via History.seed(). Cheap -- it's a JSON read + list
        concat, no apply()/invert() runs until a kid actually presses undo --
        so this ships EAGER on open rather than lazy-on-first-undo."""
        hist = self.history
        if hist is None or not self._store_ready():
            return
        try:
            recs = self.ws._with_sd(lambda: self.ws.carts_store.load_history(
                "docs", name, self.ws.carts_root))
        except Exception:  # noqa: BLE001 -- a bad/missing sidecar just starts undo-empty
            recs = []
        ops = []
        for rec in recs:
            if rec.get("t") == "seg":
                ops.extend(rec.get("ops") or [])
        hist.seed(ops)

    def _do_undo(self):
        if self.history is None or self.editor is None:
            return
        self._close_burst()               # a live burst is undo's first target
        if self.history.undo() is not None:
            self._after_history_change()

    def _do_redo(self):
        if self.history is None or self.editor is None:
            return
        if self.history.redo() is not None:
            self._after_history_change()

    def _after_history_change(self):
        # undo()/redo() rewrite the buffer via set_text(), which clears
        # CodeEditor.dirty -- so the debounce/persist path needs its OWN
        # dirty mark or the reverted text would never make it back to SD.
        self._unsaved = True
        self._idle = 0.0
        self.status = self.doc_name.upper() if self.doc_name else "DOC"
        self.ws._dirty = True

    # -- lifecycle -----------------------------------------------------------

    def relayout(self, w, h, fs):
        self.layout = WriterLayout(w, h, fs, self.ws.windowed_chrome)
        if self.editor is not None:
            self.editor.set_view_size(self.layout.cols, self.layout.rows)

    def open(self):
        if self._store_ready():
            try:
                self.ws._with_sd(
                    lambda: self.ws.carts_store.migrate_docs(self.ws.carts_root))
            except Exception:  # noqa: BLE001 -- migration is best-effort
                pass
        self.grid.refresh()
        pending = self._pending_open
        self._pending_open = None
        if pending and pending in self.grid.names:
            self._open_doc(pending)
        else:
            self.mode = "list"
            self.editor = None
            self.doc_name = None
            self.history = None
            self._burst_before = None
            self.status = "MY DOCS"
        self.ws._dirty = True

    # -- doc verbs -----------------------------------------------------------

    def _open_doc(self, name):
        self.flush()
        blob = None
        if self._store_ready():
            try:
                blob = self.ws._with_sd(lambda: self.ws.carts_store.load_file(
                    "docs", name, self.ws.carts_root))
            except Exception:  # noqa: BLE001
                blob = None
        lay = self.layout
        self.editor = CodeEditor(_body_of(blob) if blob else "", lay.cols, lay.rows)
        self.doc_name = name
        self.mode = "edit"
        self._unsaved = False
        self._idle = 0.0
        self._ekey_prev = 0
        self.status = name.upper()
        self.history = History(self.editor, _WriterOpCodec())
        self._burst_before = None
        self._seed_history(name)          # #111: undo reaches past this open
        self.ws._dirty = True

    def _new_doc(self):
        self.flush()
        name = None
        if self._store_ready():
            try:
                name = self.ws._with_sd(lambda: self.ws.carts_store.new_file_name(
                    "docs", self.ws.carts_root))
            except Exception:  # noqa: BLE001
                name = None
        lay = self.layout
        self.editor = CodeEditor("", lay.cols, lay.rows)
        self.doc_name = name or "doc_1"
        self.mode = "edit"
        self._unsaved = False           # written on first change (no empty litter)
        self._idle = 0.0
        self._ekey_prev = 0
        self.status = "NEW DOC"
        self.history = History(self.editor, _WriterOpCodec())   # fresh doc -> no sidecar to seed
        self._burst_before = None
        self.ws._dirty = True

    def _back_to_list(self):
        self.flush()                    # change-gated: an untouched doc never litters
        keep = self.doc_name
        self.mode = "list"
        self.editor = None
        self.doc_name = None
        self.history = None
        self._burst_before = None
        self.grid.refresh()
        self.grid.select(keep)
        self.status = "MY DOCS"
        self.ws._dirty = True

    def _delete_doc(self):
        if self.doc_name is None:
            return
        name = self.doc_name
        if self._persist(lambda: self.ws.carts_store.delete_file(
                "docs", name, self.ws.carts_root)):
            self.grid.invalidate(name)
            self.status = "IN TRASH"
        self.editor = None
        self.doc_name = None
        self.history = None
        self._burst_before = None
        self.mode = "list"
        self.grid.refresh()
        self.grid.select(None)
        self.ws._dirty = True

    def _begin_rename(self):
        if self.doc_name is None:
            return
        self.rename_text = self.doc_name[:MAX_NAME]
        self._ekey_prev = 0
        self.mode = "rename"
        self.status = "TYPE A NAME"
        self.ws._dirty = True

    def _rename_commit(self):
        name = self.doc_name
        text = self.rename_text
        new = [name]

        def _do():
            new[0] = self.ws.carts_store.rename_file(
                "docs", name, text, self.ws.carts_root)

        if name and self._persist(_do):
            self.grid.invalidate(name)
            self.doc_name = new[0]
            self.status = new[0].upper()
        self.mode = "edit"
        self.ws._dirty = True

    # -- input -----------------------------------------------------------------

    def handle_input(self, inp):
        if self.mode == "list":
            hit = self.grid.nav(inp)
            if hit and hit[0] == "pick":
                self._open_doc(hit[1])
            elif hit:
                self.status = hit[1].upper()
            return True
        if self.mode == "rename":
            self._typed_name(inp)
            return True
        self._typed_keys(inp)
        return True

    def _typed_keys(self, inp):
        ed = self.editor
        if ed is None:
            return
        k = self._edge_key(inp)
        if not k:
            return
        # Host Ctrl+Z / Ctrl+Y (#111): control bytes never reach ed.key below,
        # so they can't be typed into the buffer (the code/paint/map editors'
        # shared shortcut convention -- see editors.KeyEdge.undo_redo).
        if k == 0x1A:
            self._do_undo()
            return
        if k == 0x19:
            self._do_redo()
            return
        if len(ed.text()) >= MAX_CHARS and k not in (0x08, 0x7F):
            self.status = "PAGE FULL"
            return
        if self._burst_before is None:
            self._burst_before = ed.text()    # a fresh burst starts here (#111)
        if ed.key(k):
            self._unsaved = True
            self._idle = 0.0
            self.status = self.doc_name.upper() if self.doc_name else "DOC"
            if k in (0x0D, 0x0A) or (0x20 <= k <= 0x7E and chr(k) in _BURST_BREAK):
                self._close_burst()           # Enter/punctuation is a burst edge

    def _typed_name(self, inp):
        k = self._edge_key(inp)
        if not k:
            return
        if k in (0x0D, 0x0A):
            self._rename_commit()
        elif k in (0x08, 0x7F):
            self.rename_text = self.rename_text[:-1]
        elif 0x20 <= k < 0x7F and len(self.rename_text) < MAX_NAME:
            self.rename_text += chr(k)
        self.ws._dirty = True

    def handle_pointer(self, px, py, click):
        ws = self.ws
        lay = self.layout
        if click and not ws.windowed_chrome and py < lay.bar_h:
            # The OS bar (context-X exits): flush FIRST so an exit never loses text
            # (change-gated -- an untouched blank doc is not written just to exit).
            self.flush()
            return bool(ws.bar_layer.handle_bar_tap("tool", px, py))
        if self.mode == "list":
            if not click:
                return True
            if self._in(px, py, lay.new_btn):
                self._new_doc()
                return True
            hit = self.grid.tap(px, py)
            if hit and hit[0] in ("pick", "sel"):
                self._open_doc(hit[1])       # the picker opens on one tap
            return True
        if self.mode == "rename":
            if click and self._in(px, py, lay.del_btn):
                self._rename_commit()
            return True
        # -- edit view ---------------------------------------------------------
        self._page_drag(px, py)
        if not click:
            return True
        if self._in(px, py, lay.back_btn):
            self._back_to_list()
            return True
        if self._in(px, py, lay.name_btn):
            self._begin_rename()
            return True
        if self._in(px, py, lay.del_btn):
            self._delete_doc()
            return True
        if self._in(px, py, lay.undo_btn):
            self._do_undo()
            return True
        if self._in(px, py, lay.redo_btn):
            self._do_redo()
            return True
        if self.editor is not None and self._in(px, py, lay.text_area):
            self.editor.place((px - lay.tx) // lay.cell, (py - lay.ty) // lay.lh)
            self.ws._dirty = True
        return True

    def _page_drag(self, px, py):
        # Touch drag inside the page pans the text (content follows the finger).
        ed = self.editor
        lay = self.layout
        if ed is None or not self.ws.pointer.down \
                or not self._in(px, py, lay.text_area):
            self._drag = None
            return
        if self._drag is None:
            self._drag = (px, py)
            return
        drows = (py - self._drag[1]) // lay.lh
        dcols = (px - self._drag[0]) // lay.cell
        if drows or dcols:
            ed.scroll(-drows, -dcols)
            self._drag = (px, py)
            self.ws._dirty = True

    # -- draw --------------------------------------------------------------------

    def _button(self, cv, label, r, hot=False):
        _ui.chip(cv, self.ws.theme_colors, r, label, hot=hot, fs=self.layout.fs)

    def _hist_btn(self, cv, kind, r, enabled):
        """The UNDO/REDO toolbar pair (#111 phase 3): the same quiet chip shell
        `_button` draws (theme panel/dim, so it matches DOCS/RENAME/TRASH),
        with the shared #88 chrome glyph ('undo'/'redo') in place of a label,
        dimmed to a flat grey via can_undo()/can_redo() -- there's no separate
        dimmed sprite, so (like paint_layer._draw_tools and the Editor bar's
        _draw_history_icon) the ink color carries the disabled affordance."""
        th = self.ws.theme_colors
        x, y, w, h = r
        cv.rect(x, y, w, h, th.get("panel", self.names["black"]))
        cv.rectb(x, y, w, h, th.get("dim", self.names["light_grey"]))
        ink = th.get("title_ink", self.names["black"]) if enabled else self.names["dark_grey"]
        self.ws._glyph(kind, r, ink, cv)

    def draw(self, dt):
        cv = self.ws.sys_canvas
        lay = self.layout
        th = self.ws.theme_colors
        # The #108 autosave debounce (Paint's idle-flush model).
        if self.mode == "edit" and self._unsaved:
            self._idle += dt
            if self._idle >= self.AUTOSAVE_S:
                self.flush()
        cv.cls(th["panel"])
        _ui.toolbar(cv, th, (0, lay.bar_h, lay.w, lay.toolbar_h))
        if self.mode == "list":
            self._button(cv, "+ NEW", lay.new_btn)
        elif self.mode == "rename":
            self._button(cv, "OK", lay.del_btn, hot=True)
        else:
            self._button(cv, "DOCS", lay.back_btn)
            self._button(cv, "RENAME", lay.name_btn)
            self._button(cv, "TRASH", lay.del_btn)
            self._hist_btn(cv, "undo", lay.undo_btn, self.can_undo())
            self._hist_btn(cv, "redo", lay.redo_btn, self.can_redo())
        right = lay.undo_btn[0] - 4 * lay.fs if self.mode == "edit" else lay.w
        label = self.status[:max(1, (right - lay.status_x) // (8 * lay.fs) - 1)]
        sx = lay.status_x if self.mode != "list" else lay.new_btn[0] + lay.new_btn[2] + 8 * lay.fs
        cv.print(label, sx, lay.bar_h + 8 * lay.fs, th["title_ink"], 1)
        if self.mode == "list":
            self.grid.set_rect(lay.body, lay.fs)
            self.grid.draw(cv, th)
        elif self.mode == "rename":
            self._draw_rename(cv)
        else:
            self._draw_page(cv)
        if not self.ws.windowed_chrome:
            self.ws.bar_layer._draw_status_strip("tool")

    def _draw_rename(self, cv):
        lay = self.layout
        th = self.ws.theme_colors
        fs = lay.fs
        r = lay.entry
        cv.rect(r[0], r[1], r[2], r[3], self.names["white"])
        cv.rectb(r[0], r[1], r[2], r[3], th.get("accent", 10))
        cv.print(self.rename_text + "_", r[0] + 4 * fs, r[1] + 4 * fs,
                 self.names["black"], 1)

    def _draw_page(self, cv):
        lay = self.layout
        ed = self.editor
        fs = lay.fs
        ax, ay, aw, ah = lay.text_area
        cv.rect(ax, ay, aw, ah, PAPER)
        cv.rectb(ax, ay, aw, ah, self.names["light_grey"])
        if ed is None:
            return
        # Ruled lines under each text row + a red margin line: it reads as paper.
        for r in range(lay.rows):
            cv.rect(ax + 2 * fs, lay.ty + (r + 1) * lay.lh - 2 * fs,
                    aw - 4 * fs, 1, self.names["light_grey"])
        cv.rect(ax + 4 * fs, ay + fs, fs, ah - 2 * fs, self.names["pink"])
        for r, line in enumerate(ed.visible_lines()):
            seg = line[ed.left:ed.left + lay.cols]
            if seg:
                cv.print(seg, lay.tx, lay.ty + r * lay.lh, INK, 1)
        # A solid caret (no blink -- keeps the idle page free under the redraw gate).
        crow = ed.row - ed.top
        ccol = ed.col - ed.left
        if 0 <= crow < lay.rows and 0 <= ccol <= lay.cols:
            cv.rect(lay.tx + ccol * lay.cell, lay.ty + crow * lay.lh + 8 * fs,
                    lay.cell, max(1, 2 * fs), self.names["blue"])

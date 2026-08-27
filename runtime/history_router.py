"""The #111 UNDO ROUTER (#209 landing E) -- `Workstation.history`.

One bar pair -- UNDO and REDO in the Editor's lent top-bar zone -- has to mean
the right thing on seven tabs, over two completely different undo mechanisms,
without the kid ever learning that there are two. That routing is what this
object is: the last collaborator the architecture doc carves out, and the one
it told to have its own design pass first.

## The two mechanisms, and the order they are asked in

  1. The FINE-GRAINED in-RAM ops (`op_history.History`): a paint stroke, a map
     drag, a typed burst, a block edit, a scene placement, a music pitch tweak,
     a config field. Each Editor tab keeps its own stack; `Project._HISTORY_TABS`
     is the registry that maps the active `menu_view` to it, so a stale editor
     from a tab the kid left is never consulted.
  2. The DURABLE journal (`moy_carts` / `moy_journal`): whole commits, full-file
     snapshots on disk, walked one commit at a time.

`undo()` seals whatever edit is still open, unwinds (1) while it has anything,
and falls through to (2). The boundary is CLEAN and deliberately so: a journal
walk reloads the cart and hands every tab a FRESH, EMPTY History, so continued
presses keep walking commits until new fine-grained edits are made. Seeding a
History from the journal's op batches stays DEFERRED -- the ops ride along in
the commit lines for exactly that future, and nothing here reads them back.

## The walk is SCOPED to the active tab's files

`active_tab_files()` is the #111 owner decision: the journal walk sees only the
file(s) the tab in front of the kid owns, so an undo on Sprites never reverts
the Code tab's newest commit and REDO only lights up where something is
actually ahead. The Blocks tab walks a PAIR (`blocks.json` + the cart's main
file) because block saves write their JSON straight to disk while the generated
source is what gets journaled -- walking one without the other would desync
them, and it is also how a graduated cart's read-only Blocks tab reaches the
graduating commit whose rider un-graduates it.

## Writer, Sheets and the Desk Lab apps are NOT routed here

They keep their own `History` on their own app object and their own persistence
(`files/.history/` op sidecars, a different mechanism from the per-project
journal). This object resolves the ACTIVE EDITOR surface only; the bar pair it
serves is unreachable outside the Editor. Nothing here should grow an app case
-- an app that wants undo implements it where its document lives.

## The typing burst lives here, not on `CodeEditor`

A code burst opens on the first edit and closes -- recording ONE net-diff op --
on the three edges the spec names: the autosave debounce, an Enter, and an undo
press. It is state about KEYBOARD INPUT rather than about the document, which is
why it never went onto the editor core; it sat on the kernel beside the input
handler until this landing, and now it sits with the router that consumes it.

## The two warm paths, and why neither is a forward

`idle_tick()` runs every frame from `Workstation.frame`, BEFORE the redraw gate,
so the durable autosave-commit lands in a typing GAP on a frame the console was
going to skip anyway. It is one early-out on the common no-pending-edit path,
and `frame()` calls it directly on this object (doc 3b: the frame loop never
goes through a forward).

`bar_undo_bits()` runs once per bar-strip cache key, i.e. per painted bar frame.
It reads RAM ONLY, on purpose -- see its docstring; keying the strip cache on
the journal would put an SD read in the paint path.

## Reading late-injected things through `ws`, per call

Same rule as the other five collaborators (doc 3c). This object is built in
`Workstation.__init__`, before the store is wired and before there is a project,
an editor or a layer stack -- so `project`, `editor`, `menu_view`, `cart`,
`bar_layer` and the store 4-tuple (through the shared `StoreHandle`) are all
reached through `ws` at the moment of use and never captured here.
"""

try:
    from op_history import History, TextEditCodec, text_diff_op
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.op_history import History, TextEditCodec, text_diff_op

try:
    from chrome import _err_text
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.chrome import _err_text

try:
    from ticks import _ticks_ms, _ticks_diff
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.ticks import _ticks_ms, _ticks_diff


class HistoryRouter:
    """The kid-facing UNDO/REDO verbs, the code tab's typing burst, and the
    journal walk they fall through to.

    UI TRIGGER, resolved by owner decision (#88, 2026-07-18): shared UNDO/REDO
    icons in the Editor's lent top-bar zone (`EditorApp.draw_zone`), reachable
    from every tab, on top of the code editor's Ctrl+Z / Ctrl+Y host shortcut
    (`code_layer`). Both drive the SAME `undo()`/`redo()` below, so neither
    affordance can drift from the other."""

    def __init__(self, ws, handle):
        self.ws = ws
        self.store = handle
        # #111 phase 4: the code editor's in-RAM op-history (a typing-burst
        # codec over the live CodeEditor) + the open burst's pre-image text.
        # Created/rebound lazily by code_op_history() over whatever editor is
        # live, so a rebuilt editor (fresh cart / reopen) starts a fresh, empty
        # History.
        self._code_hist = None
        self._code_burst_before = None
        # The idle-typing debounce (Stage 7 of
        # docs/history/shell_ux_technical_plan_v1.md). `edit_ms` is the ticks of
        # the last keystroke in the code editor (None = no pending edit) and is
        # a PLAIN PUBLIC ATTRIBUTE because `Workstation.handle_input` stores to
        # it on every keypress that reaches the code tab -- one attribute store,
        # never a call, never a forward (doc 3e). `idle_tick()` below fires a
        # durable, INVISIBLE autosave-commit once `edit_debounce_ms` of no
        # keystroke elapse, so the SD write lands in a typing GAP (never
        # mid-burst, where it would stall the keystroke echo) -- the soft
        # trigger alongside the hard SAVE/PLAY/tab-leave commits. The ~1.5s
        # default is v1.1's pinned starting point.
        self.edit_ms = None
        self.edit_debounce_ms = 1500

    # -- which History is in front of the kid --------------------------------

    def active_history(self):
        """The FINE-GRAINED op-history (#111) of the active Editor tab --
        paint/map (phase 2) + code/blocks/scene/music/config (phase 4): every tab
        keeps an in-RAM op stack now, or None with no live editor. Resolved
        through the project's per-tab registry keyed on `menu_view`
        (`Project.history_for`), so a stale editor from an earlier tab is never
        consulted; the caller (the bar UNDO/REDO icons, only reachable inside the
        Editor) guarantees the Editor is the focused surface, so no back-stack
        check is needed."""
        ws = self.ws
        return ws.project.history_for(ws.menu_view)

    # -- code editor op-history + typing burst (#111 phase 4) ----------------

    def code_op_history(self):
        """The code editor's History, created lazily over the live CodeEditor and
        rebound when a fresh editor is built (a new cart / reopen). None with no
        editor open. Rebinding resets the open burst so a stale pre-image can't
        leak across editors."""
        ed = self.ws.editor
        if ed is None:
            return None
        h = self._code_hist
        if h is None or h.doc is not ed:
            self._code_hist = History(ed, TextEditCodec())
            self._code_burst_before = None
        return self._code_hist

    def code_burst_open(self):
        """Mark a code typing/delete burst's start: snapshot the buffer text ONCE
        (idempotent while a burst is open), so the net change since it began
        becomes one undo op when it closes. No-op with no code editor."""
        ed = self.ws.editor
        if ed is None:
            return
        self.code_op_history()          # ensure the History is bound to this editor
        if self._code_burst_before is None:
            self._code_burst_before = ed.text()

    def close_code_burst(self):
        """Close the live code burst into ONE op = the net text diff since it
        began. A no-op when nothing is pending or the burst net-cancelled back to
        its start (typed then fully backspaced). Called on the burst edges: the
        autosave debounce (commit_code), an Enter (code_layer), and an undo press
        (below)."""
        before = self._code_burst_before
        self._code_burst_before = None
        hist = self.code_op_history()
        ed = self.ws.editor
        if before is None or hist is None or ed is None:
            return
        after = ed.text()
        if before == after:
            return
        op = text_diff_op(before, after)
        if op is not None:
            hist.record(op)

    def _code_burst_pending(self):
        """True iff a live code burst holds an un-recorded net change -- so
        can_undo() reports it WITHOUT the side effect of closing the burst (the
        dim-state read)."""
        ed = self.ws.editor
        return (self._code_burst_before is not None and ed is not None
                and ed.text() != self._code_burst_before)

    def _seal_active_local(self):
        """Close any in-progress edit on the active tab BEFORE an undo/redo, so a
        just-made, not-yet-recorded edit is undo's first target: a live code
        typing burst, an un-sealed block edit. A no-op for tabs without one
        (paint/map seal their own stroke/batch on release)."""
        ws = self.ws
        v = ws.menu_view
        if v == "code":
            self.close_code_burst()
        elif v == "blocks":
            be = getattr(ws.block_ui, "blocks_ed", None)
            if be is not None:
                be._seal_pending()

    def _active_local_pending(self):
        """True iff the active tab has an in-progress edit not yet on its History
        (a live code burst / an un-sealed block edit), so can_undo() dims
        correctly without sealing it."""
        ws = self.ws
        v = ws.menu_view
        if v == "code":
            return self._code_burst_pending()
        if v == "blocks":
            be = getattr(ws.block_ui, "blocks_ed", None)
            return be is not None and be._pending_changed()
        return False

    def bar_undo_bits(self):
        """RAM-only dim-state bits for the bar cache key: the active tab's local
        op-history depth + its live-burst/edit flag, so a stroke/typing burst
        un-dims the bar UNDO icon on the very next frame (a stroke records into a
        History without any journal commit, and the strip cache otherwise only
        rebuilds on a clock tick or zone change). Deliberately EXCLUDES the
        SD-backed journal check (`_journal_check` reads the journal log -- keying
        the per-frame cache on it would cost a disk read per frame); journal-level
        dim flips already invalidate the bar explicitly (`Project._journal`,
        `_journal_walk`)."""
        hist = self.active_history()
        local_undo = (hist is not None and hist.can_undo()) or self._active_local_pending()
        return (local_undo, hist is not None and hist.can_redo())

    def _after_local_history(self):
        """After a fine-grained (paint/map/code/blocks) undo/redo: the editor
        mutated its LIVE doc in place (sheet/tilemap gen bumped for a running
        preview; the code buffer rewritten), so there's no cart reload -- just
        repaint and re-check the bar's dimmed state (#111). The code buffer's
        set_text() cleared its dirty flag, so re-arm it: the reverted text must
        persist at the next commit (autosave/exit). The one other exception is
        SCENE: its rows are a separate in-editor list that only reaches the
        running cart's `scene()` via an explicit sync (the same one every
        committed gesture calls, `scene_editor_ui._sync_live`), so a bar-driven
        undo/redo must call it too."""
        ws = self.ws
        ws._dirty = True
        ws.bar_layer.invalidate()
        if ws.menu_view == "code" and ws.editor is not None:
            ws.editor.dirty = True
            # An undo/redo rewrote the buffer: RE-CHECK the marked error (owner
            # 2026-07-23) -- it retires only if the restored code actually
            # parses again; a still-broken restore keeps a live marker.
            ws.code_layer._recheck_err()
        elif ws.menu_view == "scene":
            ws.scene_ui._sync_live()

    # -- the bar pair --------------------------------------------------------

    def undo(self):
        """Undo one step for the active Editor tab (#111). A tab with an in-RAM
        op-history (paint/map strokes, code typing bursts, block edits) UNWINDS it
        FIRST (one stroke/gesture/burst/edit/field tweak), and only once that's
        exhausted falls through to the durable journal walk (one whole commit) --
        so the SAME bar icon crosses the local->commit boundary; every Editor tab
        keeps an in-RAM op stack now (#111 phase 4). Returns True iff a step was
        taken. NOTE the boundary is CLEAN: falling into the journal reloads the
        editor with a fresh (empty) History, so continued presses walk whole
        commits until new fine-grained edits are made (the seed-from-journal
        option was deferred)."""
        self._seal_active_local()         # a live burst/edit is undo's first target
        hist = self.active_history()
        if hist is not None and hist.can_undo() and hist.undo() is not None:
            self._after_local_history()
            return True
        return self._journal_walk(False)

    def redo(self):
        """Re-apply one step (the inverse of undo): local op-history redo first,
        then the durable journal redo. Returns True iff a step was taken."""
        self._seal_active_local()         # close a stray open edit before walking redo
        hist = self.active_history()
        if hist is not None and hist.can_redo() and hist.redo() is not None:
            self._after_local_history()
            return True
        return self._journal_walk(True)

    def can_undo(self):
        """Read-only: True iff undo() would restore something (#88/#111, the bar
        icon's dimmed state). Consults the active tab's op-history FIRST (a cheap
        in-RAM check, no I/O) -- including a live-but-unrecorded code burst / block
        edit -- then the journal (a journal.jsonl parse -- an SD read, so only ask
        when about to REPAINT, never on a per-frame hot path)."""
        hist = self.active_history()
        if hist is not None and (hist.can_undo() or self._active_local_pending()):
            return True
        return self._journal_check(False)

    def can_redo(self):
        """Read-only counterpart to can_undo() for redo()."""
        hist = self.active_history()
        if hist is not None and hist.can_redo():
            return True
        return self._journal_check(True)

    # -- the durable journal walk --------------------------------------------

    def active_tab_files(self):
        """The journal file set the bar UNDO/REDO should walk for the ACTIVE
        Editor tab (#111 owner decision): the fallback journal walk is scoped to
        the tab's own file(s), so an undo on one tab never reverts another's
        newest commit and REDO only lights on the tab that has something ahead. A
        tuple of journal file names, or None (the legacy whole-project walk) for a
        tab with no defined set / outside the Editor. `main` is the cart's actual
        main file (#67: main.lua for a lua cart), matching how commits name it
        (`_journal_code`)."""
        ws = self.ws
        v = ws.menu_view
        cart = ws.cart or {}
        mainf = cart.get("main", "main.py")
        if v == "code":
            return (mainf,)
        if v == "blocks":
            # blocks.json is not itself journaled today (block saves write it straight to
            # disk); main.py IS -- so the pair is walked together and can't desync, and a
            # GRADUATED cart's read-only Blocks tab reaches the main.py graduating commit
            # (its grad rider un-graduates on the same press).
            return ("blocks.json", mainf)
        if v == "cards":
            return ("config.json",)
        if v == "paint":
            return ("sprites.moygfx",)
        if v == "map":
            return ("map.moymap",)
        if v == "music":
            return ("sounds.json",)
        if v == "scene":
            name = getattr(ws.scene_ui, "scene_name", None)
            store = ws.carts_store
            if name and store is not None:
                sd = getattr(store, "SCENES_DIR", "scenes")
                ext = getattr(store, "SCENE_EXT", ".moyscene")
                return (sd + "/" + name + ext,)
            return None
        return None

    def _journal_check(self, redo):
        """Is there a commit behind (or ahead of) the cursor for this tab's
        files? The shared `StoreHandle` is the guard: no store, no root or no
        writes means there is no journal to walk."""
        ws = self.ws
        if not self.store.writable() or not ws.cart:
            return False
        store = ws.carts_store
        path = ws.cart.get("path")
        name = "journal_can_redo" if redo else "journal_can_undo"
        if not (path and hasattr(store, name)):
            return False
        fn = getattr(store, name)
        files = self.active_tab_files()
        try:
            return bool(self.store.call(lambda: fn(path, files)))
        except Exception as exc:  # noqa: BLE001 -- a check failure must never crash the shell
            print("Moybyte journal check failed:", _err_text(exc))
            return False

    def _journal_walk(self, redo):
        ws = self.ws
        if not self.store.writable() or not ws.cart:
            return False
        store = ws.carts_store
        path = ws.cart.get("path")
        if not (path and hasattr(store, "journal_undo")):
            return False
        fn = store.journal_redo if redo else store.journal_undo
        files = self.active_tab_files()
        try:
            changed = self.store.call(lambda: fn(path, files))
        except Exception as exc:  # noqa: BLE001 -- a walk failure must never crash the shell
            print("Moybyte journal walk failed:", _err_text(exc))
            return False
        if not changed:
            return False           # at a floor/ceiling -- nothing to restore
        self._reload_after_walk(changed)
        ws._dirty = True
        # The walk just moved the journal cursor, flipping can_undo()/can_redo() --
        # invalidate so the bar's UNDO/REDO icons re-check + repaint their dimmed
        # state on the NEXT frame (#88) instead of showing a stale enabled/disabled
        # look until some unrelated zone_gen bump happens to force a re-render.
        ws.bar_layer.invalidate()
        return True

    def _reload_after_walk(self, file):
        """After the journal rewrote a live cart file on SD (undo/redo), re-adopt
        the fresh data into the OPEN workspace and rebuild the affected editor so
        the kid SEES the revert. Reloads the whole cart (uniform across file types)
        but keeps the current tab; re-_start()s so a running preview reflects the
        restored code. `file` is which live file the walk touched (informational --
        the reload is wholesale)."""
        ws = self.ws
        store = ws.carts_store
        path = ws.cart["path"]
        try:
            fresh = self.store.call(lambda: store.load(path))
        except Exception as exc:  # noqa: BLE001
            print("Moybyte reload after undo failed:", _err_text(exc))
            return
        if not fresh:
            return
        ws.cart = fresh
        ws.config = dict(fresh.get("cfg", {}))
        ws.project.reset_config_history()  # #111 phase 4: fresh baseline post-walk
        ws.sheet = ws._build_sheet()
        ws.tilemap = ws._build_tilemap()
        ws.images = fresh.get("images") or {}
        ws.tables = fresh.get("tables") or {}
        ws.texts = fresh.get("texts") or {}
        ws.scenes = ws._build_scenes()   # a scene undo must reach the live rows (#85)
        ws.cart_error = None
        ws.crash_line = None
        ws.crash_popup = None        # the popup is transient -- any walk ends it
        # Keep the kid's place in the code: the rebuild below resets the fresh
        # CodeEditor's caret to the top, which read as "undo threw me to the
        # start of the file" (owner report 2026-07-23). goto_row clamps, so a
        # shrunken restore lands on the nearest surviving line.
        caret = None
        if ws.menu_view == "code" and ws.editor is not None:
            caret = (ws.editor.row, ws.editor.col)
        # Drop the editor cores + rebuild the ACTIVE tab's over the fresh data, then
        # re-run so a running cart / a subsequent PLAY uses the restored source/art.
        ws.editor = None
        ws.paint = None
        ws.map_ui.reset()
        ws.scene_ui.reset()
        ws.music_ui.reset()
        ws.block_ui.reset()
        view = ws.menu_view
        if ws.wm.top_is("menu") and view in ("code", "paint", "map", "scene",
                                             "blocks", "music"):
            ws.set_menu_view(view)     # rebuild the active editor from fresh data
            if caret is not None and ws.editor is not None:
                ws.editor.goto_row(caret[0], caret[1])
            if view == "code":
                # Re-check the restored text (owner 2026-07-23): a marker only
                # retires when the code actually parses again.
                ws.code_layer._recheck_err()
        ws._start()

    # -- the idle-typing autosave (Stage 7 soft trigger) ---------------------

    def _autosave_code(self):
        """The idle-debounce autosave-COMMIT (Stage 7): persist + journal the code
        editor's buffer once the kid has stopped typing, WITHOUT the SAVE UI (save
        is invisible, spec Section 7). Only commits parseable source -- a mid-edit
        syntax error just waits (no nag) -- and only a real, writable edit.
        commit_code does the persist + the durable journal append + clears
        editor.dirty."""
        ws = self.ws
        ed = ws.editor
        if ed is None or not getattr(ed, "dirty", False):
            return
        if (ws.carts_store is None or not ws.cart
                or not ws.cart.get("path") or not ws.can_manage):
            ed.dirty = False              # nothing persistable (embedded/non-SD) -> disarm
            return
        src = ed.text()
        ok, _msg = ws.carts_store.compile_check(src)
        if not ok:
            return                        # don't autosave/journal un-parseable source
        # quiet=True keeps the autosave invisible (spec Section 7): it suppresses
        # the "Code Wizard" achievement toast, and commit_* no longer writes a
        # "SAVED" status at all (save_status carries FAILURES only) -- so the old
        # save/restore dance around this call is gone. A failed store write still
        # surfaces via save_status/cart_error, as it must.
        ws.project.commit_code(src, quiet=True)   # persists + journals; clears ed.dirty

    def idle_tick(self):
        """Fire the idle-typing autosave-commit once the code editor has sat quiet
        for `edit_debounce_ms`. Called every frame by `Workstation.frame` BEFORE
        the redraw gate so it runs even while a static editor screen is skipping
        its redraw -- the exact idle moment the between-frames SD write should
        land. Cheap: one early-out on the common no-pending-edit path."""
        if self.edit_ms is None:
            return
        ed = self.ws.editor
        if ed is None or not getattr(ed, "dirty", False):
            self.edit_ms = None           # the edit was saved/cleared elsewhere -> disarm
            return
        if _ticks_diff(_ticks_ms(), self.edit_ms) < self.edit_debounce_ms:
            return                        # not idle long enough -- the kid is still typing
        self.edit_ms = None
        self._autosave_code()

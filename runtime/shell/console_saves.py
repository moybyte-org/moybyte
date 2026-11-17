"""The Editor's SAVE and PLAY verbs on the Workstation: the compile-gated
`save_code` and its inline error marker, `run_code`/`apply` (PLAY from the Code
and Config tabs), the per-tab commit entry points the surfaces dispatch to
(`save_sprites`/`save_map`/`save_scene`/`save_sounds`/`save_config` -- thin over
`Project.commit_*`), and the #18 cross-cart sprite reuse over the shared sheet.
The console's `ws.saves`.
"""

from editors_sheet import SpriteSheet


class Saves:

    def __init__(self, ws):
        self.ws = ws

    def save_code(self, force=False):
        """Persist the edited source. Returns True iff it was written.

        The gate is SPLIT (#154, owner 2026-09-06). A source that won't compile is
        REFUSED on the soft paths -- the idle debounce and PLAY's run gate -- so a
        half-typed line is never published mid-typing and a broken cart is never
        RUN; the syntax error surfaces via cart_error and the caret
        moves onto the bad line. `force` is what every HARD EXIT passes
        (EditorApp.save_current): going home, a tab switch, a window close, the OTA
        reboot. Those write anyway -- a kid who quits mid-line must not lose the
        line for not having finished it -- and keep the syntax badge, WITHOUT
        yanking the caret, because leaving is not a request to be taken to the
        error. Broken code that reaches disk is caught at the next run, as
        crash-to-code. Non-SD carts (no path) just no-op True."""
        ws = self.ws
        if not (ws.editor and ws.run.project.cart):
            return False
        src = ws.editor.text()
        name = ws.text.code_file_name()
        # Always gate, even for embedded/non-SD carts, so the kid sees a syntax
        # error before run_code execs it into a hard failure. The gate is the
        # cart's RUNTIME's: a lua cart used to be handed to the PYTHON compiler
        # here, which refused every commit of its Code tab with a Python message.
        ok, msg = ws.carts_store.runtime_compile_check(ws.run.project.cart, src)
        if not ok:
            if not force:
                ws.run.player.cart_error = "Syntax error -- " + msg
                self.set_code_error(msg)    # mark the bad line in the editor (#24)
                return False
            self.set_code_error(msg, move=False)   # badge it, leave the caret alone
        else:
            ws.code_err = None             # parses now -> clear the inline marker
            ws.code_err_row = None
            ws.run.player.crash_line = None           # a re-run will re-detect any crash
        if not (ws.run.project.cart.get("path") and ws.can_manage):
            if ok:
                ws.ach.award("first_code")   # Code Wizard: valid code saved
            return True
        # The store-write half moved to Project.commit_code (Stage 1b); the compile-
        # check + code-UI half above stays here (the code surface).
        return ws.run.project.commit_code(src, force=force, name=name)

    def set_code_error(self, msg, move=True):
        """Record a syntax error so the code view can mark the offending line
        inline (#24). compile_check formats messages as "line N: <reason>"; pull
        N out for the marker, keep the short reason for the inline note, and move
        the caret onto that line so the fix is one tap away (`move=False` for
        the live typing re-check, which must never yank the caret)."""
        row = None
        short = msg
        if msg.startswith("line "):
            rest = msg[5:]
            p = rest.find(":")
            if p > 0 and rest[:p].strip().isdigit():
                row = int(rest[:p].strip()) - 1
                short = rest[p + 1:].strip()
        self.mark_code_error(row, short, move=move)

    def mark_code_error(self, row, short, move=True):
        """Record an inline error marker (#24) and, if the editor is open, move
        the caret onto `row` (0-based) so the fix is one tap away. `move=False`
        (the live re-check while the kid types) updates the marker WITHOUT
        yanking the caret."""
        ws = self.ws
        ws.code_err = short
        ws.code_err_row = row
        if move and row is not None and ws.editor is not None:
            ed = ws.editor
            ed.row = max(0, min(len(ed.lines) - 1, row))
            ed._clamp_col()
            ed._scroll()

    def run_code(self):
        # Refuse to run un-parseable source: keep the kid in the editor with the
        # syntax error shown rather than dropping to a blank/broken desktop.
        ws = self.ws
        if ws.editor is not None:
            if not self.save_code():
                return                               # syntax/save error -> stay in editor
            # in-RAM apply (validated above), into the slot the OPEN file came
            # out of -- a cart with no path never reaches the store write, so
            # this is the only thing that makes a PLAY run what was just typed.
            ws.carts_store.set_source(ws.run.project.cart, ws.text.code_file_name(),
                                        ws.editor.text())
        if ws.start():
            ws.ach.award("first_run")         # Lift Off!: a cart was RUN
            # PLAY from the code tab (Stage 3b): caller = the Editor, so the cart's
            # exit returns to the code tab (not the launcher home).
            ws.spine.play(ws.editor_app)
        else:
            # Compiled but raised at exec/_init: show the error panel on the desktop
            # (still reachable -> the kid can reopen the editor to fix it).
            ws.spine.play(ws.editor_app)

    # (The #111 bar UNDO/REDO pair, the code typing burst, the tab-scoped
    # journal walk and the post-walk workspace reload all live on
    # self.history now -- history_router.py, #209 landing E.)

    def save_sprites(self):
        # Store-write moved to Project.commit_sprites (Stage 1b); this stays as the
        # tested ws. entry point PaintLayer's SAVE dispatches to.
        ws = self.ws
        ws.run.project.commit_sprites()

    def save_map(self):
        # Store-write moved to Project.commit_map (Stage 1b); this stays as the tested
        # ws. entry point MapEditorUI's SAVE dispatches to.
        ws = self.ws
        ws.run.project.commit_map()

    def save_scene(self):
        # The scene tab's persist verb (#85 Stage 2): the editor serializes its
        # rows and commits through Project.commit_scene (atomic write + manifest
        # registration + the durable undo journal). The ws entry point the bar's
        # SAVE (EditorApp.save_current) dispatches to.
        ws = self.ws
        ws.scene_ui.save()

    # -- music / sound editor (#50) ------------------------------------------

    def save_sounds(self):
        # Store-write moved to Project.commit_sounds (Stage 1b); this stays as the
        # tested ws. entry point MusicEditorUI's SAVE dispatches to.
        ws = self.ws
        ws.run.project.commit_sounds()

    # -- cross-cart sprite reuse (#18) ---------------------------------------
    #
    # The shared sheet is a single .moygfx living beside the carts dir. PUT copies
    # the tile a kid is painting INTO that shared sheet; GET copies a tile back
    # OUT of it into whatever cart they're painting next -- so a sprite travels
    # between carts without being repainted. Both go through SpriteSheet.copy_tile
    # (the import primitive) and the moy_carts shared-sheet store.

    def _load_shared_sheet(self):
        """Read the shared sheet into a SpriteSheet (empty one if never saved).

        Spec-shaped (16 x 32) like every cart sheet -- explicit here because it is
        load-bearing twice over. It has to span all 512 tile ids or copy_tile()
        refuses a PUT/GET of anything past id 255 (which is what the old 16x16
        default did, silently, as "CAN'T PUT"); and it is a SpriteSheet like any
        other, so a shape libmoy would refuse has no business being one. An older
        128-line shared.moygfx parses into the top half with ids unchanged."""
        ws = self.ws
        try:
            hexs = ws.with_sd(lambda: ws.carts_store.load_shared_sheet(ws.carts_root))
        except Exception as exc:  # noqa: BLE001
            print("Moybyte load shared sheet failed:", exc)
            return None
        if hexs:
            try:
                return SpriteSheet.from_hex(hexs, cols=16, rows=32)
            except Exception:  # noqa: BLE001
                pass
        return SpriteSheet(16, 32)

    def share_tile_get(self):
        """Import the current tile FROM the shared sheet into this cart's sheet
        (same tile id). The kid then SAVEs the cart sheet to keep it."""
        ws = self.ws
        if not (ws.paint and ws.run.project.sheet):
            return False
        shared = self._load_shared_sheet()
        if shared is None:
            ws.paint_status = "NO SHARED"
            return False
        if shared.is_blank():
            ws.paint_status = "SHARED EMPTY"   # nothing painted there yet
            return False
        n = ws.paint.n
        if ws.run.project.sheet.copy_tile(shared, n, dst_n=n) is None:
            ws.paint_status = "CAN'T GET"
            return False
        ws.paint_status = "GOT SPR " + str(n)
        return True

    def share_tile_put(self):
        """Save the current tile TO the shared sheet (persisted), so another cart
        can GET it. Loads the shared sheet, drops this tile in at the same id, and
        writes it back."""
        ws = self.ws
        if not (ws.paint and ws.run.project.sheet):
            return False
        if not (ws.carts_root and ws.can_manage):
            ws.paint_status = None             # writes deferred -- nothing to persist
            return False
        shared = self._load_shared_sheet()
        if shared is None:
            ws.paint_status = "CAN'T PUT"
            return False
        n = ws.paint.n
        if shared.copy_tile(ws.run.project.sheet, n, dst_n=n) is None:
            ws.paint_status = "CAN'T PUT"
            return False
        try:
            hexs = shared.to_hex()
            ws.with_sd(lambda: ws.carts_store.save_shared_sheet(hexs, ws.carts_root))
        except Exception as exc:  # noqa: BLE001
            ws.paint_status = "CAN'T PUT"
            print("Moybyte save shared sheet failed:", exc)
            return False
        ws.paint_status = "PUT SPR " + str(n)
        return True

    def send_sprites_to_files(self):
        """Export the open cart's sprite sheet to files/Pictures/Sprites/ as a named user
        file (#108 the "send to Files" producer for the sprites kind). The whole
        sheet travels as one .moygfx (the same hex the cart stores), auto-named
        (sheet_1, ...) and browsable in the Files app; from there it re-imports
        into any project through the file picker (the #18 cross-cart reuse hub).
        Returns the stored file name, or None. Surfaces a paint status."""
        ws = self.ws
        sheet = ws.run.project.sheet if ws.run.project is not None else None
        if sheet is None or not (ws.carts_root and ws.can_manage):
            ws.paint_status = None       # writes deferred -- nothing to persist
            return None
        try:
            hexs = sheet.to_hex()

            def _write():
                name = ws.carts_store.new_file_name("sprites", ws.carts_root)
                return ws.carts_store.save_file("sprites", name, hexs,
                                                  ws.carts_root)
            name = ws.with_sd(_write)
        except Exception as exc:  # noqa: BLE001 -- surface, never crash the editor
            ws.paint_status = "CAN'T SEND"
            print("Moybyte send sprites to files failed:", exc)
            return None
        ws.paint_status = "SENT " + str(name).upper()[:8]
        return name

    def apply(self):
        # GO (Config tab): re-run with the new config. Always return to the desktop:
        # on success it runs, on failure frame() paints the error panel there (still
        # reachable). PLAY from the Config tab (Stage 3b): caller = the Editor, so the
        # cart's exit returns to the Config cards, not the launcher home.
        ws = self.ws
        ok = ws.start()
        ws.spine.play(ws.editor_app)
        if ok:
            ws.ach.award("first_run")         # Lift Off!: GO re-ran the cart
            self.save_config()

    def save_config(self):
        # Moved to Project.commit_config (Stage 1b); this stays as the tested ws. name
        # apply() dispatches to.
        ws = self.ws
        ws.run.project.commit_config()

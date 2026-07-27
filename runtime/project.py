"""The open cart's live WORKSPACE (Stage 1 of docs/shell_ux_technical_plan_v1.md).

`Project` holds the DATA of the one cart currently open in the console -- the cart
dict (src/cfg/sprites/map/sounds/blocks/path, as loaded by moy_carts.load), its
live `config` dict, the built SpriteSheet/TileMap/Pmem, and the paint-image assets
(`images`) -- plus the persistence verbs (commit_*) that write that data back
through the injected store. It is the one object a tab edits and (Stage 2) the one
object the Player runs; it is NOT a copy -- the editors and a re-run share the same
live sheet/tilemap/bank exactly as before (edits reach a running cart via `gen`
bumps).

Boundary (docs/shell_ux_technical_plan_v1.md Section 1.2): Project owns the cart's
DATA, not the draw toolkit. It keeps a `ws` back-reference -- the seam the plan
explicitly keeps for Stage 1 -- and reaches `ws.<X>` for the Workstation-owned deps
the builders/commits need (the SD-session wrapper `ws._with_sd`, the cart store
`ws.carts_store`, `ws.can_manage`, the per-cart audio backend `ws.audio` +
`ws.make_audio`, the save-status UI fields `ws.save_status`/`ws.cart_error`, and the
achievements tracker `ws.ach`). The four builders + the commit verbs were moved
VERBATIM from Workstation (console.py); the six live-data fields Project holds are
exposed back on Workstation as forwarding properties, so every surface file + every
test keeps working unmodified.

Canonical home is runtime/; build.sh stages a copy into the firmware modules/ tree
so the device freezes it (same pattern as console.py/editors.py). It stays
dependency-free apart from the shared editor cores + the widgets leaf below (the
same bare-or-package import fallback console.py uses -- bare names on the device /
once host_app has aliased them, `runtime.X` when a test loads this module directly).
"""

import json

try:
    from editors import SpriteSheet, TileMap
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.editors import SpriteSheet, TileMap
try:
    from audio import AudioBank, AudioEngine
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.audio import AudioBank, AudioEngine
try:
    from widgets import Pmem, Scenes, _SilentAudio, _err_text, _ticks_ms, _ticks_diff
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.widgets import Pmem, Scenes, _SilentAudio, _err_text, _ticks_ms, _ticks_diff
# The #111 op-history core: CONFIG's fine-grained undo lives directly on Project
# (there is no separate ConfigEditor class the way paint/map/scene/music have one --
# see _ConfigOps below).
try:
    from op_history import History, OpCodec
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.op_history import History, OpCodec
# The block vocabulary/compiler (#29) -- graduation detection recompiles the cart's
# frozen blocks.json + normalize-compares (Stage 8). Same bare-or-package fallback
# console.py/block_editor_ui use: bare `blocks` on the device / once host_app aliased
# it, `runtime.blocks` when a test imports this module directly.
try:
    import blocks as _blocks_mod
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import blocks as _blocks_mod
# The Storybook deck compiler (#78) -- graduation detection for a DECK-authored
# story cart recompiles what the deck would generate and compares it to the
# committed source, the deck-cart mirror of the blocks check just above (same
# bare-or-package import fallback storybook_app/console use). No cycle: neither
# storybook_app nor its own imports (ui/editors/app_shell) touch project.py.
try:
    from storybook_app import deck_to_code as _deck_to_code, STORY_TYPE as _STORY_TYPE
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.storybook_app import (deck_to_code as _deck_to_code,
                                       STORY_TYPE as _STORY_TYPE)


class _ConfigOps(OpCodec):
    """OpCodec for the CONFIG tab (#111 phase 4): an op is `{"k":key,"o":old,
    "n":new}` -- one field's old/new value, the exact Sheets cell-codec shape
    (invert is O(1): write `o`/`n` straight back). The doc is the Project
    itself (config lives directly in `doc.config`, a plain dict -- there is no
    separate ConfigEditor instance the way paint/map/scene/music each have
    one, so no editor-identity guard is needed; `Project.reset_config_history`
    re-baselines whenever the live dict is replaced wholesale)."""

    def apply(self, doc, op):
        doc.config[op["k"]] = op["n"]

    def invert(self, doc, op):
        doc.config[op["k"]] = op["o"]


class Project:
    """The open cart's live data + its persistence verbs (Stage 1). The six data
    fields (cart/config/sheet/tilemap/images/pmem) are exposed back on Workstation
    as forwarding properties; the builders + commit_* verbs were moved verbatim."""

    def __init__(self, ws):
        self.ws = ws
        self.cart = None
        self.config = None
        self.sheet = None             # SpriteSheet for the open cart (built on open)
        self.tilemap = None           # TileMap for the open cart (built on open, #32)
        self.images = None            # {name: .moyimg text} for the open cart (#63);
                                      # make_api decodes each lazily via image(name)
        self.tables = None            # {name: rows} Sheets docs, read via table() (#78)
        self.texts = None             # {name: lines} Writer docs, read via text() (#78)
        self.pmem = None              # Pmem (persistent cart store) for the open cart
        self.scenes = None            # Scenes (#85): the open cart's placed-actor
                                      # scenes; make_api binds scene()/load_scene()
        self.config_hist = History(self, _ConfigOps())  # #111: CONFIG tab op-history

    # -- builders (moved verbatim from Workstation) --------------------------

    def _build_sheet(self, cart=None):
        # Build `cart`'s sprite sheet (default: the open cart), or a blank one when
        # there's no/bad art. The wallpaper runner passes a cart explicitly.
        cart = cart if cart is not None else self.cart
        hexs = cart.get("sprites") if cart else None
        if hexs:
            try:
                return SpriteSheet.from_hex(hexs)
            except Exception:  # noqa: BLE001
                pass
        return SpriteSheet()

    def _build_pmem(self):
        """Load the open cart's persistent memory (pmem.json) into a Pmem, wiring
        its writes back through the SD store when the cart is writable. An
        embedded/non-SD cart still gets working (volatile) RAM."""
        ws = self.ws
        path = self.cart.get("path") if self.cart else None
        cells = None
        if path and ws.carts_store is not None:
            try:
                cells = ws._with_sd(lambda: ws.carts_store.load_pmem(path))
            except Exception as exc:  # noqa: BLE001
                print("Moybyte pmem load failed:", exc)
                cells = None

        on_write = None
        if path and ws.carts_store is not None and ws.can_manage:
            def on_write(values, cart=self.cart):
                # Runs from Pmem.flush() (#66 deferred persistence) -- cart
                # exit/crash + the Player's periodic flush -- never per pmem()
                # call anymore: the per-write version of this save WAS Letter
                # Blitz's 81-130ms "word-event" logic spike (measured on glass
                # 2026-07-14 by the PMEM diag line below, which stays to show
                # the deferred cadence; perf_capture-gated like every diag line).
                t0 = _ticks_ms()
                try:
                    ws._with_sd(lambda: ws.carts_store.save_pmem(cart, values))
                except Exception as exc:  # noqa: BLE001
                    # No serial in the device run loop, but a failed pmem write must
                    # not crash the cart -- the kid just loses that one save.
                    print("Moybyte pmem save failed:", _err_text(exc))
                    return
                if ws.perf_capture:
                    print("PMEM save=%dms cart=%s"
                          % (_ticks_diff(_ticks_ms(), t0), cart.get("title")))
        return Pmem(cells, on_write)

    def _build_tilemap(self, cart=None):
        """Build `cart`'s TileMap from its map.moymap blob (#32) (default: the open
        cart), or an empty map when the cart has none -- the mirror of _build_sheet,
        so map()/mget()/mset() are always callable (an empty map just blits
        nothing). The wallpaper runner passes a cart explicitly."""
        cart = cart if cart is not None else self.cart
        blob = cart.get("map") if cart else None
        if blob:
            try:
                return TileMap.from_hex(blob)
            except Exception:  # noqa: BLE001
                pass
        return TileMap()

    def _build_scenes(self, cart=None):
        """Build `cart`'s Scenes (#85) from its scenes/*.moyscene blobs (default: the
        open cart), or an empty Scenes when the cart has none -- the mirror of
        _build_tilemap, so scene()/load_scene() are always callable (an empty scene
        just yields no actors). Names come from the cart's manifest-ordered
        scene_names (moy_carts.load), so element 0 is the default active scene."""
        cart = cart if cart is not None else self.cart
        blobs = cart.get("scenes") if cart else None
        names = cart.get("scene_names") if cart else None
        return Scenes(blobs, names)

    def _build_audio(self):
        """Build the per-cart audio backend (#16): an AudioEngine over the cart's
        sound bank (sounds.json), wrapped by the injected host/device backend. The
        mirror of _build_sheet. A cart with no bank gets the friendly default bank
        so beep()/the editor still work. Falls back to a silent backend if no
        make_audio was injected (keeps make_api callable everywhere)."""
        ws = self.ws
        data = self.cart.get("sounds") if self.cart else None
        bank = AudioBank.from_dict(data) if data else AudioBank.default()
        engine = AudioEngine(bank)
        if ws.make_audio is not None:
            ws.audio = ws.make_audio(engine)
        else:
            ws.audio = _SilentAudio(engine)

    # -- the undo journal (Stage 7 of docs/shell_ux_technical_plan_v1.md) -------
    #
    # A commit PERSISTS and JOURNALS: after each successful store write below, the
    # exact bytes that landed on disk are appended to the cart's durable undo journal
    # (moy_carts.journal_append). The journal store owns the O(1) raw append, the
    # snapshot ceiling (no-op commits write nothing), and rotation; here we only feed
    # it the (file, bytes) each commit produced, in the SAME between-frames SD-session
    # discipline (ws._with_sd) as the save it shadows.

    def _journal(self, file, new_bytes, grad=None, ops=None):
        """Append a durable undo-journal entry for `file`. Best-effort: a journal
        failure must NEVER break the save it shadows -- the edit is already on disk,
        the kid just loses one undo step. The store's dedup drops a no-op commit, so
        journaling an unchanged file costs nothing. `grad` (Stage 8) is the optional
        0/1 graduation rider on a main.py commit (see _journal_code); `ops` (#111) is
        the optional fine-grained op batch (from a paint/map History.flush()) embedded
        additively so the bar UNDO can cross the stroke->commit boundary."""
        ws = self.ws
        store = ws.carts_store
        if store is None or not self.cart or new_bytes is None:
            return
        path = self.cart.get("path")
        if not path or not ws.can_manage or not hasattr(store, "journal_append"):
            return
        try:
            seq = ws._with_sd(lambda: store.journal_append(
                path, file, new_bytes, grad=grad, ops=ops))
        except Exception as exc:  # noqa: BLE001 -- journaling can't be allowed to fail a save
            print("Moybyte journal append failed:", _err_text(exc))
            return
        if seq is not None:
            # A real (non-dedup) commit just made an UNDO step available -- invalidate
            # the bar so its UNDO/REDO icons re-check can_undo()/can_redo() and repaint
            # their enabled state on the next frame (#88), even when the active tab
            # hasn't changed (zone_gen wouldn't otherwise bump for a plain autosave).
            ws.bar_layer.invalidate()

    def _journal_code(self, src, ops=None):
        """Journal a main.py code commit, DETECTING GRADUATION for a block- OR
        deck-authored cart (spec Section 8, the MakeCode model -- #78 folds
        Storybook's decks into the SAME mechanism as the block editor). Bound to
        the code-commit path (so it rides Stage 7's idle-debounce, never a
        keystroke), it is the ONE place either origin can graduate:

          * code-only cart (no blocks.json, no deck.json) -> a plain journal; it
            has no generating program, so it never 'graduates'.
          * a block-authored cart -> _journal_code_toward (below) decides
            sticky/round-trips/GRADUATE against the frozen blocks.json's own
            regenerated source (source_roundtrips/compile_blocks).
          * a deck-authored story cart (#78) -> the same decision against the
            deck's own regenerated source (deck_to_code) -- a kid hand-editing a
            story's main.py past Storybook's page/art/bg vocabulary graduates it
            exactly like outgrowing blocks.

        `prog` (blocks.json) wins when both somehow exist -- a cart is one origin
        or the other in practice (Storybook never writes blocks.json)."""
        cart = self.cart
        prog = cart.get("blocks") if cart else None
        # The journal entry names the cart's ACTUAL main file (#67: main.lua for a
        # lua cart), so an undo restores into the file the runtime loads from.
        mainf = cart.get("main", "main.py") if cart else "main.py"
        if prog is not None:
            self._journal_code_toward(
                mainf, src, cart,
                lambda: _blocks_mod.source_roundtrips(prog, src),
                lambda: _blocks_mod.compile_blocks(prog), ops=ops)
            return
        deck = self._deck_for_graduation(cart)
        if deck is not None:
            expected = _deck_to_code(deck, cart.get("title") or "My Story")
            self._journal_code_toward(
                mainf, src, cart,
                lambda: src == expected,
                lambda: expected, ops=ops)
            return
        self._journal(mainf, src, ops=ops)                # code-only: never graduates

    def _journal_code_toward(self, mainf, src, cart, roundtrips, baseline_fn, ops=None):
        """Shared graduation body for BOTH origins (blocks/deck -- #78):

          * already-graduated cart -> sticky: journal with grad=1 (a one-way
            door; every later code commit stays graduated).
          * still round-trips (`roundtrips()` True) -> journal grad=0; stays
            editable from its origin (the Blocks tab / Storybook).
          * DIVERGED past the origin's vocabulary -> GRADUATE: journal a grad=0
            BASELINE (`baseline_fn()`'s regenerated source, the pre-graduation
            round-tripping state) so an undo restores it, THEN the diverged src
            as grad=1 (which flips the manifest flag on the same durable step).
            Undoing past the graduating commit lands on the grad=0 baseline ->
            source AND graduated:false both restored (the §8 back-door).

        Conservative by construction: a `roundtrips` that treats an unreadable
        origin as still round-tripping (as blocks.source_roundtrips does for a
        corrupt tree) never graduates a kid over a transient hiccup."""
        # The fine-grained op batch (#111 phase 4) always rides the commit that
        # represents the NEW state (the current/diverged src), never the baseline
        # restore-point -- so undoing INTO this commit walks the same net text edit.
        if bool(cart.get("graduated")):
            self._journal(mainf, src, grad=1, ops=ops)    # sticky one-way door
            return
        if roundtrips():
            self._journal(mainf, src, grad=0, ops=ops)    # still origin-regenerable
            return
        # DIVERGED -> GRADUATE. Baseline first (restore point), then the diverged src.
        try:
            baseline = baseline_fn()
            self._journal(mainf, baseline, grad=0)        # no-op if already the current state
        except Exception as exc:  # noqa: BLE001 -- shouldn't raise (it just compiled), be safe
            print("Moybyte graduation baseline failed:", _err_text(exc))
        self._journal(mainf, src, grad=1, ops=ops)        # flips manifest graduated -> True
        cart["graduated"] = True                          # sync the open workspace in RAM

    def _deck_for_graduation(self, cart):
        """The parsed deck.json for a Storybook-authored cart (#78 graduation), or
        None for anything that isn't a deck-backed story right now (not a story
        cart, no deck.json yet, or a store/SD hiccup) -- the deck-cart mirror of
        `cart['blocks']` feeding the block graduation check above."""
        ws = self.ws
        if (cart is None or cart.get("type") != _STORY_TYPE
                or ws.carts_store is None or not cart.get("path")):
            return None
        try:
            blob = ws._with_sd(lambda: ws.carts_store.load_deck(cart))
        except Exception:  # noqa: BLE001 -- a bad/missing deck: not deck-graduating
            return None
        if not blob:
            return None
        try:
            data = json.loads(blob)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    # -- persistence verbs (moved from Workstation's save_* -- Stage 1b) ------
    #
    # These write the cart's live data back through the injected store inside the
    # SD session (ws._with_sd) exactly as before. The Workstation ws.save_* /
    # ws._save_config names stay as one-line forwards (tested surface); the
    # save-status UI fields (ws.save_status/ws.cart_error) + achievements (ws.ach)
    # they touch stay on Workstation, reached via the ws back-reference.

    # -- CONFIG tab op-history (#111 phase 4) ---------------------------------

    def reset_config_history(self):
        """Fresh #111 op-history for `config`: called whenever the live dict is
        replaced WHOLESALE (Project.__init__ via a fresh workspace open, and a
        journal walk's console._reload_after_walk) so a stale field op from a
        superseded config can never be replayed against the new one -- the same
        "clean boundary" reset paint/map/scene/music get from dropping their
        whole editor instance on a reload."""
        self.config_hist = History(self, _ConfigOps())

    def record_config(self, key, old, new):
        """Record one field's old/new value (#111): called by every config
        mutation point (Workstation.adjust's left/right stepper, the CardsLayer
        choice-cell tap) AFTER the field is already written, mirroring the
        paint/map/scene/sheets record() contract. A same-value set records
        nothing."""
        if old != new:
            self.config_hist.record({"k": key, "o": old, "n": new})

    def commit_config(self):
        # Persist edits to the SD cartridge (embedded fallback carts have no path).
        ws = self.ws
        if self.cart and self.cart.get("path"):
            self.cart["cfg"] = dict(self.config)   # in-RAM sync (always)
            if not ws.can_manage:
                return                             # writes deferred on device
            try:
                ws._with_sd(lambda: ws.carts_store.save_config(self.cart))
            except Exception as exc:  # noqa: BLE001
                print("Moybyte save failed:", exc)
                return
            # #111: drain the CONFIG History's op batch into the journal line
            # (fine-grained cross-boundary undo), then re-baseline (clear) -- the
            # same clean-boundary contract commit_sprites documents (in-RAM undo
            # covers edits SINCE the last commit, the journal covers commit-to-
            # commit, so the two never double-count a field tweak).
            ops = self.config_hist.flush()
            self._journal("config.json", json.dumps(self.cart["cfg"]), ops=ops)
            self.config_hist.clear()

    def commit_manifest(self, title=None, author=None):
        """Persist edited manifest metadata (title/author, #94's "CART INFO"
        editor) through the store. Mirrors commit_config's shape (sync the
        in-RAM cart dict always, defer the write when the device can't manage
        SD) but skips the undo journal: title/author are a small immediate-
        commit modal (explicit OK), not typing-idle-debounced asset content, and
        manifest.json already carries the graduation flag's own one-way-door
        journal riders (moy_carts._journal_code) -- folding plain metadata edits
        into that general file journal would blur the two. A blank/whitespace-
        only title is rejected here (never reaches the store), so a cart can
        never lose its name; author has no such requirement (blank = unset).
        Returns True on success (incl. a deferred-on-device no-write), False
        only on an actual store-write failure."""
        ws = self.ws
        cart = self.cart
        if not (cart and cart.get("path")):
            return False
        t = title.strip() if isinstance(title, str) else None
        if t == "":
            t = None                      # reject a blank title, keep the old one
        a = author.strip() if isinstance(author, str) else None
        if t is None and a is None:
            return False
        if t is not None:
            cart["title"] = t             # in-RAM sync (always)
        if a is not None:
            cart["author"] = a
        if not ws.can_manage:
            return True                   # write deferred on device (cfg pattern)
        try:
            ws._with_sd(lambda: ws.carts_store.save_manifest_meta(
                cart["path"], title=t, author=a))
        except Exception as exc:  # noqa: BLE001
            print("Moybyte save manifest failed:", _err_text(exc))
            return False
        return True

    def commit_code(self, src, quiet=False):
        """Persist validated source through the store -- the store-write half of the
        old Workstation.save_code. The compile-check + code-UI half stays on the code
        surface (ws.save_code), which calls this once the source is known to parse.
        Returns True iff the write succeeded.

        `quiet` is set by the Stage-7 idle-debounce autosave (ws._autosave_code): that
        save is INVISIBLE (spec Section 7), so it must NOT pop the "Code Wizard"
        achievement toast -- a visible side effect on a nominally-invisible save. The
        badge stays earnable via the explicit SAVE / PLAY paths (quiet defaults False)."""
        ws = self.ws
        try:
            # moy_carts.save_code always returns a (status, message) 2-tuple.
            status, smsg = ws._with_sd(lambda: ws.carts_store.save_code(self.cart, src))
            if status != ws.carts_store.SAVE_OK:
                ws.save_status = "CAN'T SAVE " + str(smsg)
                ws.cart_error = "Could not save -- " + str(smsg)
                return False
            ws.editor.dirty = False
            # Save is invisible (spec Section 7 / #111): no "SAVED" happy path --
            # save_status carries FAILURES only, so a successful commit just
            # CLEARS any stale failure text (the old "SAVED" write also did the
            # clearing, by overwrite; same across every commit_* verb).
            ws.save_status = None
            # #111 phase 4: close any live typing burst, drain the code History's op
            # batch into this commit's journal line (mirrors commit_sprites/map), then
            # re-baseline (clear) -- the CLEAN boundary: in-RAM undo covers edits SINCE
            # this commit, the journal covers commit-to-commit. Snapshots stay the
            # source of truth for graduation, so the additive ops never disturb it (the
            # journal WALK reloads snapshots + a fresh empty History; the ops ride along
            # for parity/future replay). flush() only after the store write succeeded.
            ws._close_code_burst()
            hist = ws._code_op_history()
            ops = hist.flush() if hist is not None else None
            self._journal_code(src, ops=ops)  # durable undo (Stage 7) + graduation (Stage 8)
            if hist is not None:
                hist.clear()                  # re-baseline (subsumes mark_keyframe)
            if not quiet:
                ws.ach.note("code_save")      # "Code Wizard": manual SAVE/PLAY only (#21)
            # A successful save means the source now compiles and persisted: clear
            # any stale crash text so returning to the desktop re-runs the fixed
            # cart instead of re-painting the old "crashed" panel. (run_code/the
            # _leave_menu re-_start() then actually re-exec it.)
            ws.cart_error = None
            return True
        except Exception as exc:  # noqa: BLE001
            txt = _err_text(exc)
            ws.save_status = "CAN'T SAVE"
            ws.cart_error = "Could not save -- " + txt
            print("Moybyte save code failed:", txt)
            return False

    def _paint_history(self):
        """The op-history of the OPEN cart-sprite PaintEditor (#111), or None. Guarded
        so the theme/icon editor (whose PaintEditor rides a DIFFERENT sheet) can never
        have its ops folded into a cart's journal -- only the editor bound to THIS
        project's live sheet counts."""
        pe = getattr(self.ws, "paint", None)
        if pe is not None and getattr(pe, "sheet", None) is self.sheet:
            return getattr(pe, "_hist", None)
        return None

    def _map_history(self):
        """The op-history of the OPEN MapEditor (#111), or None -- guarded on the live
        tilemap identity like _paint_history."""
        me = getattr(self.ws.map_ui, "mapedit", None)
        if me is not None and getattr(me, "tilemap", None) is self.tilemap:
            return getattr(me, "_hist", None)
        return None

    def _code_history(self):
        """The op-history of the OPEN code editor (#111 phase 4), or None -- created
        lazily on the Workstation over the live CodeEditor and rebound when a fresh
        editor is built (ws._code_op_history). The code burst + this History live on
        ws where the keyboard input is handled; Project just references it (and
        commit_code drains it, mirroring commit_sprites/commit_map)."""
        return self.ws._code_op_history()

    def _blocks_history(self):
        """The op-history of the OPEN BlockEditor (#111 phase 4), or None. A
        GRADUATED cart's Blocks tab is a FROZEN, read-only render (spec Section 8),
        so its History is deliberately ABSENT there -- the bar UNDO then falls
        straight to the durable journal walk, which un-graduates when it crosses the
        graduating commit (moy_carts). In-session only: blocks saves don't journal
        ops, so unlike paint/map this History never flushes into a commit."""
        ui = getattr(self.ws, "block_ui", None)
        if ui is None or getattr(ui, "blk_graduated", False):
            return None
        be = getattr(ui, "blocks_ed", None)
        return getattr(be, "_hist", None) if be is not None else None

    # #111 phase 4: the per-tab op-history REGISTRY -- the active Editor tab's
    # menu_view maps to the Project method returning that tab's live History (or
    # None for a tab with no in-RAM op stack). One entry per surface, ADDITIVE:
    # paint/map (#111 phase 2) + code/blocks (phase 4) here; scene/music/config
    # wire theirs in the same shape. Console._active_history reads it -- keeping
    # this a data table (not a switch ladder in console) is why new tabs merge
    # cleanly (one line each) instead of colliding in one growing if/elif.
    _HISTORY_TABS = {
        "paint": "_paint_history",
        "map": "_map_history",
        "code": "_code_history",
        "blocks": "_blocks_history",
        "scene": "_scene_history",
        "music": "_music_history",
        "cards": "_config_history",
    }

    def history_for(self, view):
        """The live op-history for Editor tab `view` (a menu_view key), or None for
        a tab with no in-RAM op stack. Keyed via _HISTORY_TABS so a stale editor
        from another tab is never consulted -- the caller (the bar UNDO/REDO icons,
        only reachable inside the Editor) guarantees the Editor is focused."""
        name = self._HISTORY_TABS.get(view)
        if name is None:
            return None
        return getattr(self, name)()

    def _config_history(self):
        """The op-history of this project's config cards (#111 phase 4) -- lives
        directly on the Project (there is no separate ConfigEditor instance);
        reset_config_history() re-baselines it whenever self.config is replaced
        wholesale (fresh open, journal-walk reload)."""
        return getattr(self, "config_hist", None)

    def _scene_history(self):
        """The op-history of the OPEN SceneEditor (#111 phase 4), or None. Unlike
        paint/map there is only ever one live SceneEditor per open project (no
        theme-editor-style alias rides the same class on different data), so no
        identity guard beyond "an editor is actually open" is needed."""
        se = getattr(self.ws.scene_ui, "sceneedit", None)
        return getattr(se, "_hist", None) if se is not None else None

    def _music_history(self):
        """The op-history of the OPEN MusicEditor (#111 phase 4), or None --
        guarded on the live AudioBank identity like _paint_history (the bank a
        MusicEditor edits is `ws.audio.engine.bank`; commit_sounds only wants
        the ops for THIS project's bank)."""
        me = getattr(self.ws.music_ui, "musicedit", None)
        au = getattr(self.ws, "audio", None)
        if me is not None and au is not None and getattr(me, "bank", None) is au.engine.bank:
            return getattr(me, "_hist", None)
        return None

    def commit_sprites(self):
        ws = self.ws
        if not (self.sheet and self.cart and self.cart.get("path") and ws.can_manage):
            return
        hexs = self.sheet.to_hex()
        try:
            ws._with_sd(lambda: ws.carts_store.save_sprites(self.cart, hexs))
            self.sheet.dirty = False
            ws.save_status = None             # clear stale failure text (see commit_code)
            # #111: this snapshot IS a keyframe, so drain the paint History's op batch
            # into the journal line (fine-grained cross-boundary undo). Then re-baseline
            # the History (clear): a commit is the CLEAN boundary -- in-RAM undo covers
            # edits SINCE the last commit, the journal covers commit-to-commit, so the
            # two never double-count the same stroke. flush() runs only after the store
            # write succeeded, so a failed save doesn't silently swallow the batch.
            # (Paint commits only on tab-leave/exit, never mid-session, so clearing
            # here never costs a kid an in-progress stroke's undo.)
            hist = self._paint_history()
            ops = hist.flush() if hist is not None else None
            self._journal("sprites.moygfx", hexs, ops=ops)   # durable undo (Stage 7/#111)
            if hist is not None:
                hist.clear()                  # re-baseline (subsumes mark_keyframe)
            ws.ach.note("paint_save")         # "Little Artist": a sprite saved (#21)
        except Exception as exc:  # noqa: BLE001
            # Mirror the save_code contract: a failed sprite save must be VISIBLE on
            # device (no serial in the run loop), not silent. _err_text-guarded so a
            # weird exception's __str__ can't itself escape this handler.
            txt = _err_text(exc)
            ws.save_status = "CAN'T SAVE"
            ws.cart_error = "Could not save sprites -- " + txt
            print("Moybyte save sprites failed:", txt)

    def commit_map(self):
        # Persist the cart's tilemap to map.moymap (#32) -- the exact mirror of
        # commit_sprites (to_hex -> SD wrapper -> save_map). The running cart already
        # holds this same TileMap, so a save only persists what it's already using.
        ws = self.ws
        if not (self.tilemap and self.cart and self.cart.get("path") and ws.can_manage):
            return
        hexs = self.tilemap.to_hex()
        try:
            ws._with_sd(lambda: ws.carts_store.save_map(self.cart, hexs))
            self.tilemap.dirty = False
            ws.save_status = None             # clear stale failure text (see commit_code)
            hist = self._map_history()        # #111: drain the map op batch (see commit_sprites)
            ops = hist.flush() if hist is not None else None
            self._journal("map.moymap", hexs, ops=ops)   # durable undo (Stage 7/#111)
            if hist is not None:
                hist.clear()                  # re-baseline the clean boundary (see commit_sprites)
            ws.ach.note("map_save")           # "Map Maker": a map saved (#21)
        except Exception as exc:  # noqa: BLE001
            txt = _err_text(exc)
            ws.save_status = "CAN'T SAVE"
            ws.cart_error = "Could not save map -- " + txt
            print("Moybyte save map failed:", txt)

    def commit_scene(self, name, text):
        """Persist one scene to scenes/<name>.moyscene (#85) -- the mirror of commit_map
        (save_scene -> SD wrapper -> journal). `text` is the compact .moyscene JSON blob
        (an ordered actor list). Stage 1 has no placement editor yet; this is the
        persistence verb the editor (Stage 2) calls, and the surface tests drive it
        directly. The journal `file` is the real relative path (scenes/<name>.moyscene),
        so undo restores into the file the loader reads from. Returns True on a
        persisted commit (the caller's success signal -- this used to ride the
        save_status "SAVED" happy path, removed with the rest of it)."""
        ws = self.ws
        if not (self.cart and self.cart.get("path") and ws.can_manage):
            return False
        try:
            ws._with_sd(lambda: ws.carts_store.save_scene(self.cart, name, text))
            ws.save_status = None             # clear stale failure text (see commit_code)
            rel = ws.carts_store.SCENES_DIR + "/" + name + ws.carts_store.SCENE_EXT
            # #111 phase 4: drain the SceneEditor's op batch into the journal line
            # (see commit_sprites for the clean-boundary contract).
            hist = self._scene_history()
            ops = hist.flush() if hist is not None else None
            self._journal(rel, text, ops=ops)     # durable undo (Stage 7/#111)
            if hist is not None:
                hist.clear()                      # re-baseline
            return True
        except Exception as exc:  # noqa: BLE001
            txt = _err_text(exc)
            ws.save_status = "CAN'T SAVE"
            ws.cart_error = "Could not save scene -- " + txt
            print("Moybyte save scene failed:", txt)
            return False

    def commit_sounds(self):
        """Persist the cart's AudioBank to sounds.json (#50) -- the mirror of
        commit_map. The MusicEditor edits the LIVE bank (ws.audio.engine.bank), so a
        save just serializes what the cart already plays through."""
        ws = self.ws
        me = ws.music_ui.musicedit
        if not (me and self.cart and self.cart.get("path") and ws.can_manage):
            return
        bank_dict = me.bank.to_dict()
        try:
            ws._with_sd(lambda: ws.carts_store.save_sounds(self.cart, bank_dict))
            me.dirty = False
            ws.save_status = None             # clear stale failure text (see commit_code)
            # #111 phase 4: drain the MusicEditor's op batch into the journal line
            # (see commit_sprites for the clean-boundary contract).
            hist = self._music_history()
            ops = hist.flush() if hist is not None else None
            self._journal("sounds.json", json.dumps(bank_dict), ops=ops)  # (Stage 7/#111)
            if hist is not None:
                hist.clear()                      # re-baseline
            ws.ach.note("sound_save")          # "Sound Designer": a bank saved (#21)
        except Exception as exc:  # noqa: BLE001
            txt = _err_text(exc)
            ws.save_status = "CAN'T SAVE"
            ws.cart_error = "Could not save sounds -- " + txt
            print("Moybyte save sounds failed:", txt)

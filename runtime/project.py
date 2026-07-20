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
# The block vocabulary/compiler (#29) -- graduation detection recompiles the cart's
# frozen blocks.json + normalize-compares (Stage 8). Same bare-or-package fallback
# console.py/block_editor_ui use: bare `blocks` on the device / once host_app aliased
# it, `runtime.blocks` when a test imports this module directly.
try:
    import blocks as _blocks_mod
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import blocks as _blocks_mod


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

    def _journal(self, file, new_bytes, grad=None):
        """Append a durable undo-journal entry for `file`. Best-effort: a journal
        failure must NEVER break the save it shadows -- the edit is already on disk,
        the kid just loses one undo step. The store's dedup drops a no-op commit, so
        journaling an unchanged file costs nothing. `grad` (Stage 8) is the optional
        0/1 graduation rider on a main.py commit (see _journal_code)."""
        ws = self.ws
        store = ws.carts_store
        if store is None or not self.cart or new_bytes is None:
            return
        path = self.cart.get("path")
        if not path or not ws.can_manage or not hasattr(store, "journal_append"):
            return
        try:
            seq = ws._with_sd(lambda: store.journal_append(path, file, new_bytes, grad=grad))
        except Exception as exc:  # noqa: BLE001 -- journaling can't be allowed to fail a save
            print("Moybyte journal append failed:", _err_text(exc))
            return
        if seq is not None:
            # A real (non-dedup) commit just made an UNDO step available -- invalidate
            # the bar so its UNDO/REDO icons re-check can_undo()/can_redo() and repaint
            # their enabled state on the next frame (#88), even when the active tab
            # hasn't changed (zone_gen wouldn't otherwise bump for a plain autosave).
            ws.bar_layer.invalidate()

    def _journal_code(self, src):
        """Journal a main.py code commit, DETECTING GRADUATION for a block-authored
        cart (spec Section 8, the MakeCode model). Bound to the code-commit path (so
        it rides Stage 7's idle-debounce, never a keystroke), it is the ONE place a
        block cart can graduate:

          * code-only cart (no blocks.json) -> a plain journal; it has no block
            program, so it never 'graduates'.
          * already-graduated block cart -> sticky: journal with grad=1 (a one-way
            door; every later code commit stays graduated).
          * block cart that still round-trips (source_roundtrips) -> journal grad=0;
            stays blockifiable.
          * block cart whose commit DIVERGED past the vocabulary -> GRADUATE: journal
            a grad=0 BASELINE (the frozen block program's own regenerated source, the
            pre-graduation round-tripping state) so an undo restores it, THEN the
            diverged src as grad=1 (which flips the manifest flag on the same durable
            step). Undoing past the graduating commit lands on the grad=0 baseline ->
            source AND graduated:false both restored (the §8 back-door).

        Conservative by construction: source_roundtrips treats an un-recompilable tree
        as still round-tripping, so a transient block problem never graduates a kid."""
        cart = self.cart
        prog = cart.get("blocks") if cart else None
        # The journal entry names the cart's ACTUAL main file (#67: main.lua for a
        # lua cart), so an undo restores into the file the runtime loads from.
        mainf = cart.get("main", "main.py") if cart else "main.py"
        if prog is None:
            self._journal(mainf, src)                     # code-only: never graduates
            return
        if bool(cart.get("graduated")):
            self._journal(mainf, src, grad=1)             # sticky one-way door
            return
        if _blocks_mod.source_roundtrips(prog, src):
            self._journal(mainf, src, grad=0)             # still blockifiable
            return
        # DIVERGED -> GRADUATE. Baseline first (restore point), then the diverged src.
        try:
            baseline = _blocks_mod.compile_blocks(prog)
            self._journal(mainf, baseline, grad=0)        # no-op if already the current state
        except Exception as exc:  # noqa: BLE001 -- shouldn't raise (it just compiled), be safe
            print("Moybyte graduation baseline failed:", _err_text(exc))
        self._journal(mainf, src, grad=1)                 # flips manifest graduated -> True
        cart["graduated"] = True                          # sync the open workspace in RAM

    # -- persistence verbs (moved from Workstation's save_* -- Stage 1b) ------
    #
    # These write the cart's live data back through the injected store inside the
    # SD session (ws._with_sd) exactly as before. The Workstation ws.save_* /
    # ws._save_config names stay as one-line forwards (tested surface); the
    # save-status UI fields (ws.save_status/ws.cart_error) + achievements (ws.ach)
    # they touch stay on Workstation, reached via the ws back-reference.

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
            self._journal("config.json", json.dumps(self.cart["cfg"]))

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
                ws.save_status = "SAVE FAILED " + str(smsg)
                ws.cart_error = "Could not save -- " + str(smsg)
                return False
            ws.editor.dirty = False
            ws.save_status = "SAVED"
            self._journal_code(src)           # durable undo (Stage 7) + graduation (Stage 8)
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
            ws.save_status = "SAVE FAILED"
            ws.cart_error = "Could not save -- " + txt
            print("Moybyte save code failed:", txt)
            return False

    def commit_sprites(self):
        ws = self.ws
        if not (self.sheet and self.cart and self.cart.get("path") and ws.can_manage):
            return
        hexs = self.sheet.to_hex()
        try:
            ws._with_sd(lambda: ws.carts_store.save_sprites(self.cart, hexs))
            self.sheet.dirty = False
            ws.save_status = "SAVED"
            self._journal("sprites.moygfx", hexs)   # durable undo (Stage 7)
            ws.ach.note("paint_save")         # "Little Artist": a sprite saved (#21)
        except Exception as exc:  # noqa: BLE001
            # Mirror the save_code contract: a failed sprite save must be VISIBLE on
            # device (no serial in the run loop), not silent. _err_text-guarded so a
            # weird exception's __str__ can't itself escape this handler.
            txt = _err_text(exc)
            ws.save_status = "SAVE FAILED"
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
            ws.save_status = "SAVED"
            self._journal("map.moymap", hexs)       # durable undo (Stage 7)
            ws.ach.note("map_save")           # "Map Maker": a map saved (#21)
        except Exception as exc:  # noqa: BLE001
            txt = _err_text(exc)
            ws.save_status = "SAVE FAILED"
            ws.cart_error = "Could not save map -- " + txt
            print("Moybyte save map failed:", txt)

    def commit_scene(self, name, text):
        """Persist one scene to scenes/<name>.moyscene (#85) -- the mirror of commit_map
        (save_scene -> SD wrapper -> journal). `text` is the compact .moyscene JSON blob
        (an ordered actor list). Stage 1 has no placement editor yet; this is the
        persistence verb the editor (Stage 2) calls, and the surface tests drive it
        directly. The journal `file` is the real relative path (scenes/<name>.moyscene),
        so undo restores into the file the loader reads from."""
        ws = self.ws
        if not (self.cart and self.cart.get("path") and ws.can_manage):
            return
        try:
            ws._with_sd(lambda: ws.carts_store.save_scene(self.cart, name, text))
            ws.save_status = "SAVED"
            rel = ws.carts_store.SCENES_DIR + "/" + name + ws.carts_store.SCENE_EXT
            self._journal(rel, text)              # durable undo (Stage 7)
        except Exception as exc:  # noqa: BLE001
            txt = _err_text(exc)
            ws.save_status = "SAVE FAILED"
            ws.cart_error = "Could not save scene -- " + txt
            print("Moybyte save scene failed:", txt)

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
            ws.save_status = "SAVED"
            self._journal("sounds.json", json.dumps(bank_dict))   # durable undo (Stage 7)
            ws.ach.note("sound_save")          # "Sound Designer": a bank saved (#21)
        except Exception as exc:  # noqa: BLE001
            txt = _err_text(exc)
            ws.save_status = "SAVE FAILED"
            ws.cart_error = "Could not save sounds -- " + txt
            print("Moybyte save sounds failed:", txt)

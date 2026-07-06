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

try:
    from editors import SpriteSheet, TileMap
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.editors import SpriteSheet, TileMap
try:
    from audio import AudioBank, AudioEngine
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.audio import AudioBank, AudioEngine
try:
    from widgets import Pmem, _SilentAudio, _err_text
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.widgets import Pmem, _SilentAudio, _err_text


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
        self.pmem = None              # Pmem (persistent cart store) for the open cart

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
                try:
                    ws._with_sd(lambda: ws.carts_store.save_pmem(cart, values))
                except Exception as exc:  # noqa: BLE001
                    # No serial in the device run loop, but a failed pmem write must
                    # not crash the cart -- the kid just loses that one save.
                    print("Moybyte pmem save failed:", _err_text(exc))
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

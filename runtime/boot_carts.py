"""The boot's cart step: read the store's catalogue, seed it against that, fall
back to the built-in carts -- `DeviceBoot`'s store half, one body for every board.

`BootCarts` is a mixin `DeviceBoot` (runtime/device_boot.py) takes: it speaks
through the boot's own screen (`say`, `note`), so the serial lines and the
splash's progress bar are the ones every board already prints. The store
arrives as an argument -- `moy_catalogue`, the store's interface -- so nothing
here imports a board module or the store's implementation, and the step is a
fake in tests/test_device_boot.py. The split is the native kernel's line
(docs/native_kernel_2026-09.md, sprint 1b): this file crosses with the store;
device_boot.py's runtime probe crosses later.
"""

try:
    from device_util import sram_census
except ImportError:  # pragma: no cover - host lane: no device tier staged
    def sram_census(stage):
        """No second region off-board, so nothing to weigh."""

try:
    from ticks import _ticks_ms, _ticks_diff
except ImportError:  # pragma: no cover - host package lane
    from runtime.ticks import _ticks_ms, _ticks_diff

# The seed's progress bar repaints at most this often. A cart the seed WRITES
# takes ~550ms, so every one still moves the bar; a seed that writes nothing
# checks a cart in milliseconds, against ~16ms for a repaint of the splash.
SEED_PAINT_MS = 250


class BootCarts:
    """The cart load, seed and scan of a boot. Needs `say` and `note` from the
    class that takes it (`DeviceBoot`)."""

    def seed_progress(self, done, total, title):
        """`seed_builtins`' progress callback: a repaint per cart at most every
        SEED_PAINT_MS, one serial line every eighth.

        This is the only stretch of the boot that knows how much of itself is
        left, which is what a first boot -- every built-in written out -- needs
        to show. A repaint costs nothing against ~550ms of flash writes per
        cart (measured: the P4 boot stays at 25.4s) and ~16ms on an S3 against
        a cart the seed only checks, so a seed that writes nothing paints once.
        Every eighth also goes to the wire, because a repaint says nothing to
        someone watching over serial -- and one line per cart would drown the
        boot log.
        """
        if done % 8 == 0:
            self.say("boot: loading cartridges %d/%d" % (done + 1, total))
        now = _ticks_ms()
        last = getattr(self, "_seed_painted", None)
        if done and last is not None and _ticks_diff(now, last) < SEED_PAINT_MS:
            return
        self._seed_painted = now
        self.note("loading cartridges  %d/%d" % (done + 1, total),
                  frac=float(done) / total if total else 1.0)

    def load_carts(self, store, seed, root=None, session=None, media="SD",
                   fallback_root=None, fallback_media="flash"):
        """Seed + scan the cart store, falling back to the embedded carts.

        Returns `(carts, carts_root)`; a None root means "management disabled",
        which is what `wire_workstation_core` turns into `can_manage=False`.

        `session` is the board's storage lifecycle wrapper -- on the T-Deck
        `moybyte_sd.with_sd_live` (SD shares the panel's SPI host, so the mount
        must bracket the whole seed+scan), on the P4s and the Guition S3 nothing
        at all, because the card has a bus of its own (device/card_store.py) and
        internal flash races no one. `media` is the word that appears in the
        serial lines ("SD" / "flash").

        `fallback_root` is the SECOND STORE to try before giving up, and it is
        what keeps a card-less board WRITABLE. Without it a T-Deck with no card
        in the slot booted to the embedded carts with a None root, i.e. a
        read-only console: every cart visible, none editable, nothing saveable,
        and the only explanation a single serial line nobody was attached to
        read. The retry runs with NO session, because a board only ever needs a
        lifecycle wrapper for the bus it just failed on -- internal flash races
        nobody on any board here.

        Why a retry rather than a probe: on this hardware there is nothing to
        probe. The Guition mounts its card once at boot and asks the mount, but
        the T-Deck's card shares the panel's SPI host and is mounted PER
        SESSION, so the only honest question is "did a real session work" --
        which is this call. The boards differ because the buses do.
        """
        sram_census("rd-entry")
        try:
            # BEFORE the scan, which is what fragments the heap: the PICO-8
            # machine's 81KB has to be a contiguous run, and after the store is
            # up an S3 has none (moycore_glue.reserve_p8_memory has the numbers).
            try:
                from moycore_glue import reserve_p8_memory
                if reserve_p8_memory():
                    self.say("p8 machine memory reserved")
            except ImportError:
                pass                    # a build with no moycore staged
            if root is None:
                root = store.CARTS_DIR
            carts = self._try_store(store, seed, root, session, media)
            if carts:
                return carts, root
            if fallback_root is not None and fallback_root != root:
                carts = self._try_store(store, seed, fallback_root, None,
                                        fallback_media)
                if carts:
                    return carts, fallback_root
            self.say("using built-in carts")
            return store.embedded_floor(seed), None
        finally:
            sram_census("carts")

    def _try_store(self, store, seed, root, session, media):
        """One store attempt: scan it, seed it, say what happened. [] on any
        failure OR an empty scan -- the caller decides whether another store is
        left to try."""
        try:
            def _scan_and_seed():
                store.ensure_dirs(root)
                store.sweep_store(root, seed)
                shelf = store.catalogue(root)  # the shelf; a cart's payloads at open
                sram_census("scanned")
                # The seed after the scan: the shelf's versions say what to
                # write, and only what it wrote is read again.
                shelf = store.seed(seed, root, shelf, progress=self.seed_progress)
                sram_census("seeded")
                return shelf

            carts = _scan_and_seed() if session is None else session(_scan_and_seed)
            if carts:
                self.say("loaded %d carts from %s" % (len(carts), media))
                return carts
        except Exception as exc:  # noqa: BLE001 -- any store failure degrades
            self.say("%s carts unavailable: %s" % (media, exc))
        return []

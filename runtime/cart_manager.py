"""The shelf's ROSTER (#209 landing C) -- `Workstation.carts`.

The scanned cart list and everything that changes it: the store-writing verbs
(new / duplicate / delete), the re-scan the browser sync fires, the #66 live-set
diet (slim / rehydrate / re-slim) and the #105 favorites + recents. One object
owns the list, so "what carts exist" has one author and one place a second cart
SOURCE registers (docs/history/console_architecture_2026-08.md Section 5: #123/#124/#125
gallery browse/install/publish).

## `all` is a plain attribute with no `ws` mirror

A re-scan builds a NEW list (`apply` rebinds it), so there is no alias trick to
be had -- `ws.system`'s in-place `clear()`+`update()` works because that dict is
never rebound, and this list is rebound on every create, duplicate, delete,
re-seed and browser sync. So the consumers read `ws.carts.all` and they all
migrated in the landing that moved it: the desk icon column and its statics key
(`wm_windowed`), the dev channel's `state` snapshot, the sysmenu's delete
detection, `app_context`'s `Carts.all()` role, the cover prefetch and icon
prune, `tools/make_site_gifs.py`, `tools/p4_conformance.py`'s serial surgery and
the web runner's boot + re-scan.

## What stays on the kernel, and why

`_launcher_items` / `_launcher_view_items` / `_picker_items` / `_real_selected`
are GRID PRESENTATION, not roster: each is keyed on kernel state this object has
no business knowing -- `can_manage`, `wm.has_desk`, the registered-app list, the
search query, the pinned pseudo tiles. They read `carts.all` like every other
consumer, and keeping them out means a future cart SOURCE plugs into the roster
without also having to know what a shelf card looks like. `_fat_cart` stays
kernel too: it is the OPEN WORKSPACE's cart, written by `_open_workspace`, not a
property of the roster.

## Reading the store through `ws`, per call

Same `StoreHandle` `SystemStore` takes, and for the same reason: `carts_store`,
`carts_root`, `can_manage` and `_with_sd` are all `None`-or-wrong at
construction time and are injected (or swapped, on the boards) afterwards. The
handle reads them through `ws` at the moment of use, so there is no wiring-order
trap to get right.
"""

# The heavy per-cart payloads the launcher list does NOT need (#66 live-set diet):
# kept resident they are ~300-500KB of permanently-live strings the GC MARK phase
# pays for on every collect (~0.2ms/KB on device -- most of the 93-161ms pauses).
# slim() strips them after the icons are cached; opening a cart rehydrates
# from the store, and switching carts re-slims the previous one.
_HEAVY_CART_KEYS = ("src", "sprites", "sounds", "map", "images", "blocks", "scenes")


class CartManager:
    """The scanned cart list, its store-writing verbs and the #105 favorites.

    Plain methods, no properties (a property forward measured +5.1us against a
    plain hop's +0.5us on this codebase), and the one member the grids call per
    card -- `is_favorite` -- is handed to the launcher as a BOUND METHOD rather
    than reached through the console."""

    # -- favorites + recents (#105) -------------------------------------------
    #
    # Both ride the SAME system.json persistence Settings already uses for
    # theme/wallpaper/font/OTA channel (ws.system + ws.prefs.persist) -- no new
    # store surface. `favorites` is a plain path list (order = the order a kid
    # starred them, oldest first); `desk_mru` (issue #105's own naming note) is a
    # capped most-recently-run path list, newest first. Cart identity is the
    # store PATH (stable across a rename/rescan, unlike an in-memory dict).

    _MRU_CAP = 8          # how many recents system.json remembers

    def __init__(self, ws, handle, carts=None):
        self.ws = ws
        self.store = handle
        # The scanned cart list is the single source both grids derive from:
        # the LAUNCHER grid is the pinned "Make" tile + the run-grid carts, and
        # the Editor's PROJECT-PICKER grid is the pinned "+ New" tile + every
        # editable cart. Kept whole here so wallpaper discovery + the wifi-tool
        # lookup read the FULL list rather than either display grid (the
        # Make/New pseudo tiles never leak out).
        self.all = list(carts) if carts else []

    # -- the roster ----------------------------------------------------------

    def apply(self, items):
        """Adopt a fresh scan as the live roster and re-sync BOTH display grids,
        re-deriving the pinned pseudo tiles so a create/dup/delete lands in both.

        The ONE body every roster change runs -- create, duplicate, delete, the
        browser sync's re-scan, and `app_context`'s `Carts.apply`."""
        if items:
            self.all = list(items)
            ws = self.ws
            # Cover bitmaps, parsed sources, the cover-less set AND the icon
            # cache: a re-scan may carry new or changed art and may take a cart
            # away entirely. BEFORE slim(), and that order is load-bearing:
            # slim() bakes each cart's icon and then DELETES its sprite art,
            # so it is the last moment the art exists in RAM -- clearing after it
            # would leave a slimmed cart with no icon and nothing to rebuild one
            # from, and clearing nothing (what this did before #209 landing C)
            # let slim()'s bake hit the STALE entry and make it permanent.
            ws.covers.invalidate_all()
            self.slim()                    # #66: a rescan reloads FULL carts -- re-slim
            ws.launcher.set_items(ws._launcher_view_items())   # #105: keep an active filter
            ws.picker.set_items(ws._picker_items(items))

    def rescan(self):
        """Re-read the store and rebuild both shelves -- the sync push's board
        half (moy_webhost wires it as `on_sync`), fired when a browser batch
        changed what the launcher shows: a manifest, a cover sheet, a cart
        created or deleted. `apply` does the whole refresh (re-slim, cover
        caches dropped, generation bumped), the same body every create/dup/
        delete already runs. Safe between frames: the webhost polls at the frame
        tail, after present."""
        if not self.store.ready():
            return
        ws = self.ws
        try:
            self.apply(self.store.call(
                lambda: ws.carts_store.scan(ws.carts_root)))
        except Exception as exc:  # noqa: BLE001 -- a failed scan keeps the old shelf
            print("Moybyte rescan failed:", exc)
        ws._dirty = True

    # -- the #66 live-set diet -----------------------------------------------

    def slim(self):
        """The #66 live-set diet: after the backend wires the cart store, drop every
        SD-backed cart's heavy payloads (source/sprites/sounds/map/images/blocks)
        from the scanned list -- the launcher only needs metadata + the icon, which
        is baked into the icon cache here first. Cuts the permanently-live heap by
        ~300-500KB, which is most of a GC collect's mark cost (~0.2ms/KB on device).
        Embedded carts (no path / no store) stay fat -- they cannot be reloaded."""
        ws = self.ws
        if ws.carts_store is None:
            return
        for cart in self.all:
            if not cart.get("path") or cart.get("lazy"):
                continue
            try:
                ws.covers.icon_sheet_for(cart)   # bake the grid icon while the art is here
            except Exception:  # noqa: BLE001 -- a bad sheet just gets the type glyph
                pass
            for k in _HEAVY_CART_KEYS:
                if k in cart:
                    del cart[k]
            cart["lazy"] = True
        try:
            import gc
            gc.collect()                       # reclaim the dropped payloads NOW
        except Exception:  # noqa: BLE001
            pass

    def rehydrate(self, cart):
        """Load a slimmed cart's full payloads back from the store IN PLACE (the
        launcher/picker hold the same dict, so every reference fattens at once).
        No-op for fat/embedded carts; a failed load leaves the cart slim and the
        caller's error handling surfaces it (missing src -> the crash panel)."""
        ws = self.ws
        if not cart.get("lazy") or ws.carts_store is None or not cart.get("path"):
            return cart
        try:
            full = self.store.call(lambda: ws.carts_store.load(cart["path"]))
        except Exception:  # noqa: BLE001 -- SD hiccup: stay slim, surface downstream
            full = None
        if full:
            cart.update(full)
            cart["lazy"] = False
        return cart

    def reslim(self, cart):
        # Re-slim a previously-opened cart when the workspace moves on (keeps at
        # most ~one fat cart live). Only SD-backed carts that slim() managed.
        if cart is None or not cart.get("path") or cart.get("lazy") is not False:
            return
        for k in _HEAVY_CART_KEYS:
            if k in cart:
                del cart[k]
        cart["lazy"] = True

    # -- cart management (SD) ------------------------------------------------
    #
    # Each action mounts the SD card, mutates, and re-scans within a single
    # storage session, then the card is unmounted before the next flush.

    def new(self):
        """Create a fresh GAME cart from the store's NEW_TEMPLATE, adopt the new
        roster and return the created cart -- None on a read-only store (a device
        without SD writes) or a failed write, so the caller can stay put rather
        than crash.

        ONE body, where there were two: `new_cart` and the first half of
        `new_cart_and_edit` were the same six lines written twice, differing only
        in which element of the (create, scan) tuple they kept. `new_cart` had no
        callers left anywhere in the tree."""
        if not self.store.writable():
            return None
        ws = self.ws
        try:
            new, items = self.store.call(lambda: (
                ws.carts_store.new_from_template(ws.carts_root),
                ws.carts_store.scan(ws.carts_root)))
        except Exception as exc:  # noqa: BLE001
            print("Moybyte new cart failed:", exc)
            return None
        self.apply(items)
        return new

    def dup(self):
        # DUP now fires from the Editor picker's zone (docs/shell_ux_v1.md: the picker
        # manages projects, the launcher only plays) -- so it acts on the PICKER's
        # selection, not the launcher's.
        ws = self.ws
        sel = ws._real_selected(ws.picker)
        if not self.store.writable() or sel is None:
            return
        self.rehydrate(sel)   # #66: duplicate() copies src/cfg FROM the dict
        try:
            self.apply(self.store.call(lambda: (
                ws.carts_store.duplicate(sel, ws.carts_root),
                ws.carts_store.scan(ws.carts_root))[1]))
        except Exception as exc:  # noqa: BLE001
            print("Moybyte duplicate failed:", exc)

    def delete(self):
        # Delete the OPEN cart when one is open (the sysmenu DELETE CART -- a cart opened
        # from the picker is NOT necessarily the picker's current selection), else the
        # picker's selection (the picker-zone DEL, docs/shell_ux_v1.md -- moved off the
        # launcher, which no longer has a management cluster at all). Keep at least one
        # real cart on the device.
        ws = self.ws
        target = ws.cart if (ws.cart is not None and ws.cart.get("path")) \
            else ws._real_selected(ws.picker)
        if not self.store.writable() or target is None or len(self.all) <= 1:
            return
        try:
            self.apply(self.store.call(lambda: (
                ws.carts_store.delete(target),
                ws.carts_store.scan(ws.carts_root))[1]))
        except Exception as exc:  # noqa: BLE001
            print("Moybyte delete failed:", exc)

    # -- favorites + recents (#105) ------------------------------------------

    def is_favorite(self, cart):
        path = cart.get("path") if cart else None
        if not path:
            return False
        return path in self.ws.system.get("favorites", [])

    def toggle_favorite(self, cart):
        """Star/unstar `cart` (the launcher card's corner badge tap) and persist.
        A no-op for a pseudo tile (no path)."""
        path = cart.get("path") if cart else None
        if not path:
            return
        ws = self.ws
        favs = list(ws.system.get("favorites", []))
        if path in favs:
            favs.remove(path)
        else:
            favs.append(path)
        ws.system["favorites"] = favs
        ws._dirty = True
        ws.prefs.persist()

    def note_recent(self, cart):
        """Record `cart` as most-recently-run: move its path to the front of
        system.json's `desk_mru` list (issue #105's naming), capped at
        _MRU_CAP. Called from every launcher-driven run/open (open/open_app) --
        a pseudo tile (no path) is never recorded."""
        path = cart.get("path") if cart else None
        if not path:
            return
        ws = self.ws
        mru = [p for p in ws.system.get("desk_mru", []) if p != path]
        mru.insert(0, path)
        ws.system["desk_mru"] = mru[:self._MRU_CAP]
        ws.prefs.persist()

    def recent(self):
        """The desk_mru path list resolved back to live cart dicts (newest first),
        silently dropping any path that no longer scans (deleted/renamed since).
        Read-only convenience for a future recents surface; #105 only settled the
        system.json key, not where it renders."""
        by_path = {c.get("path"): c for c in self.all if c.get("path")}
        out = []
        for path in self.ws.system.get("desk_mru", []):
            c = by_path.get(path)
            if c is not None:
                out.append(c)
        return out

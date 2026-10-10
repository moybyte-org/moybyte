"""`system.json`'s owner (#209 landing B) -- `StoreHandle` + `SystemStore`.

`Workstation.prefs`. Every persisted Settings choice, the achievement badges,
the crash guard's strikes, the pairing pin, favorites and recents all live in
ONE store on ONE file, and until this landing four separate bodies re-derived
the same "is there a writable store?" guard around it.

## The rows are the store, and `set` is the only way in

`ws.system` is `prefs.rows`, the spine's `Settings` (native/moy_spine):
one row per key holding the value's JSON text, the kernel's own rows on a
board (`moy_spine.kernel_settings`). It is the most-aliased object in
the shell -- the launcher reads `favorites` on every home paint,
the prefs role reads and writes it namespaced (native/moy_app), the crash guard keeps its
ledger in it, the goldens poke a pin into it -- and it is created once and never
rebound, so `load()` replaces its rows IN PLACE and every alias, including one
captured before the store was wired, stays honest.

Reads are `ws.system.get(key, default)`, a value decoded afresh from its row, so
what a reader does to it cannot change the store. A write is `ws.system.set(key,
value)`: it marks the store dirty and persists it through `SystemStore._write`,
the rows' save hook, so a write cannot be left out of the file by a caller that
forgot to ask. A write that fails stays dirty and rides on the next. There is no
dict beside the rows to write to, and none to push from: the rows a native
component owns (the strike ledger, sprint 2) are written by that component
through the same store.

## Reading the store through `ws`, per call

`StoreHandle` captures nothing. The store, the root, `can_manage` and the SD
session wrapper are all read through `ws` at the moment they are used, because
none of them is knowable when the collaborator is built: `carts_store` and
`carts_root` are `None` until `wire_workstation_core` injects them, `can_manage`
is set in that same call, and on the boards `_with_sd` is swapped for the
native mount *after* construction. A handle that snapshotted any of them at
`__init__` would be a wiring-order trap waiting for its first board; reading
through `ws` makes that trap structurally impossible rather than merely
avoided by ordering.
"""

try:
    from chrome import _err_text
    from moy_spine import Settings
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.chrome import _err_text
    from runtime.moy_spine import Settings


def _rows(save):
    """The settings rows: the kernel's where the console runs on a kernel (a
    board), so a VM stop leaves them and a prefs write made with no VM waits
    in them for the next start; elsewhere this console's own. Every start but
    a return start empties the kernel's, and `load()` fills them."""
    try:
        import moy_kernel
        from moy_spine import kernel_settings
    except ImportError:
        return Settings(save)
    start = getattr(moy_kernel, "start", None)
    return kernel_settings(save, not (start is not None and start() == "return"))


def safe_mode():
    """True when the kernel started this boot SAFE from its recovery screen
    (native/moy_kernel): the settings are ignored -- the defaults, so no
    wallpaper -- and the file is left as it was for the next ordinary boot."""
    try:
        import moy_kernel
    except ImportError:
        return False
    return moy_kernel.mode() == "safe"


class StoreHandle:
    """The (store, root, can_manage, with_sd) guard 4-tuple, as an object.

    Four bodies re-derived it before this landing (`_persist_system`,
    `_persist_wallpaper`, `_save_achievements`, `rescan_carts`) and
    `app_context._StoreRole` re-derives it once more for the storage roles --
    which is why this is a standalone object and not private to `SystemStore`:
    CartManager and the roles take one too.

    Plain methods, no properties: a property forward measured +5.1us against a
    plain hop's +0.5us on this codebase, and `writable()` sits in front of every
    settings write."""

    def __init__(self, ws):
        self.ws = ws

    def ready(self):
        """A store exists to READ from (an embedded boot has neither)."""
        ws = self.ws
        return ws.carts_store is not None and ws.carts_root is not None

    def writable(self):
        """A store exists AND writes are enabled. `can_manage` is False where
        the carts are baked into the image, so a write there is not a failure
        to report -- it is a build that has nowhere to put one."""
        return self.ready() and bool(self.ws.can_manage)

    def call(self, fn):
        """Run `fn()` inside ONE storage session. On the T-Deck this mounts the
        SD card for the duration and releases it after, so the render loop's
        flushes never collide with it on the shared SPI bus; on the host and the
        flash-backed boards it is a passthrough."""
        return self.ws._with_sd(fn)


class SystemStore:
    """The `system.json` rows and the hook that writes them.

    Holds the `Settings` `ws.system` aliases (see the module docstring) plus the
    achievements list's two store halves. What it deliberately does NOT own is
    what the settings MEAN: `load_system`'s apply cascade -- eight `set_*` verbs
    and `select_wallpaper` -- stays kernel policy, and so does the unlock beep
    and the icon-sheet bake."""

    def __init__(self, ws, handle):
        self.ws = ws
        self.store = handle
        # THE store. `Workstation.__init__` aliases it as `ws.system` and
        # nothing rebinds either name again -- `load()` replaces its rows.
        self.rows = _rows(self._write)
        self.safe = safe_mode()

    # -- system.json ---------------------------------------------------------

    def load(self):
        """Read `system.json` into the rows, IN PLACE. Safe no-op if no store or
        root is wired (an embedded boot keeps whatever it already had).

        A store that raises leaves the settings EMPTY rather than half-read: a
        bad card must not crash boot, and a partially-applied settings file is
        worse than the defaults, which are all valid."""
        if not self.store.ready():
            return self.rows
        if self.safe:
            print("Moybyte SAFE start: system.json ignored")
            return self.rows
        # A return start's rows may carry a write the stopped VM never saved:
        # it reaches the file before the file is read back.
        self.rows.flush()
        ws = self.ws
        try:
            loaded = self.store.call(
                lambda: ws.carts_store.load_system(ws.carts_root)) or {}
        except Exception as exc:  # noqa: BLE001 -- a bad store must not crash boot
            print("Moybyte system load failed:", _err_text(exc))
            loaded = {}
        self.rows.adopt(loaded)
        return self.rows

    def _write(self, text):
        """The rows' save hook: `text` is the file, written when a writable
        store is wired. False when the write failed, so the rows stay dirty and
        the next write carries it; True when it landed or when this build has
        nowhere to put one (`can_manage` is False where the carts are baked
        into the image: that is not a failure to retry)."""
        if self.safe or not self.store.writable():
            return True
        ws = self.ws
        try:
            self.store.call(lambda: ws.carts_store.save_system(text, ws.carts_root))
        except Exception as exc:  # noqa: BLE001 -- a failed write just isn't remembered
            print("Moybyte system save failed:", _err_text(exc))
            return False
        return True

    # -- achievements.json (#21) ---------------------------------------------

    def load_achievements(self):
        """The unlocked-id list off the store, or [] on an embedded/no-store
        boot (the badges then stay in volatile RAM -- still awarded and toasted
        this session, just not remembered)."""
        if not self.store.ready():
            return []
        ws = self.ws
        try:
            return self.store.call(
                lambda: ws.carts_store.load_achievements(ws.carts_root)) or []
        except Exception as exc:  # noqa: BLE001 -- a bad store must not crash boot
            print("Moybyte achievements load failed:", _err_text(exc))
            return []

    def save_achievements(self, ids):
        """Persist the unlocked-id list -- `Achievements`' `on_save` hook.

        No try/except here on purpose: `Achievements.award` already wraps the
        hook and prints the failure, so a second one would only decide the same
        thing twice. A disabled write is simply not remembered (the badge still
        shows this session)."""
        if not self.store.writable():
            return
        ws = self.ws
        self.store.call(
            lambda: ws.carts_store.save_achievements(ids, ws.carts_root))

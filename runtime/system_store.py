"""`system.json`'s owner (#209 landing B) -- `StoreHandle` + `SystemStore`.

`Workstation.prefs`. Every persisted Settings choice, the achievement badges,
the crash guard's strikes, the pairing pin, favorites and recents all live in
ONE dict on ONE file, and until this landing four separate bodies re-derived
the same "is there a writable store?" guard around it.

## The dict is never rebound, and that is the whole mechanism

`ws.system` is the most-aliased object in the shell: settings_layer writes it
raw, the dev channel writes it raw, the launcher reads `favorites` on every
home paint, `app_context.Prefs` reads and writes it namespaced, and the goldens
poke a pin into it. Handing all of those a collaborator to call would have been
a cross-module migration of ~a dozen sites for no behavioural gain.

So the dict object itself is the seam: `SystemStore` owns it, `load()` mutates
it IN PLACE (`clear()` + `update()`), and `Workstation.__init__` binds
`ws.system` to it once. Neither name is ever rebound again, so every alias --
including one captured before the store was even wired -- stays honest forever
with no consumer migration at all. The `CrashGuard`-takes-a-callable wart
(which existed only because the old `load_system()` REBOUND the dict) retires
with it.

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
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.chrome import _err_text


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
    """The `system.json` dict and the four funnels that read and write it.

    Holds the dict `ws.system` aliases (see the module docstring) plus the
    achievements list's two store halves. What it deliberately does NOT own is
    what the settings MEAN: `load_system`'s apply cascade -- eight `set_*` verbs
    and `select_wallpaper` -- stays kernel policy, and so does the unlock beep
    and the icon-sheet bake."""

    def __init__(self, ws, handle):
        self.ws = ws
        self.store = handle
        # THE dict. `Workstation.__init__` aliases it as `ws.system` and
        # nothing rebinds either name again -- `load()` clears and updates it.
        self.settings = {}

    # -- system.json ---------------------------------------------------------

    def load(self):
        """Read `system.json` into the dict, IN PLACE. Safe no-op if no store or
        root is wired (an embedded boot keeps whatever it already had).

        A store that raises leaves the settings EMPTY rather than half-read: a
        bad card must not crash boot, and a partially-applied settings file is
        worse than the defaults, which are all valid."""
        if not self.store.ready():
            return self.settings
        ws = self.ws
        try:
            loaded = self.store.call(
                lambda: ws.carts_store.load_system(ws.carts_root)) or {}
        except Exception as exc:  # noqa: BLE001 -- a bad store must not crash boot
            print("Moybyte system load failed:", _err_text(exc))
            loaded = {}
        self.settings.clear()
        self.settings.update(loaded)
        return self.settings

    def persist(self):
        """Write the dict to `system.json` when a writable store is wired.

        The ONE funnel behind every persisting Settings toggle (theme, skin,
        font scale, wallpaper, diagnostics, frameskip, 2P, crisp pixels, the FPS
        chip, the OTA channel), favorites/recents, the crash guard's strikes and
        the pairing pin. A failed write just isn't remembered."""
        if not self.store.writable():
            return
        ws = self.ws
        try:
            self.store.call(
                lambda: ws.carts_store.save_system(self.settings, ws.carts_root))
        except Exception as exc:  # noqa: BLE001 -- a failed write just isn't remembered
            print("Moybyte system save failed:", _err_text(exc))

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

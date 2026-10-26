# The Desk Lab apps' shared app-layer mixin (#78 family: Files / Storybook /
# Get Carts). `ListShellApp` carries what the three layers share:
#
#   the shipped-identity gate -- `is_app` over APP_TITLE/APP_PERM/APP_FOLDER;
#   the persist tail          -- `_persist`, the CAN'T SAVE HERE / CAN'T SAVE
#                                <why> status contract over a role write's
#                                `(value, err)` pair;
#   the rename entry cap      -- RENAME_MAX.
#
# The host class binds `self._store` to its storage role (see
# runtime/app_context.py) and draws its own views.
#
# MicroPython-safe; staged to both boards like every runtime/ module.

try:
    from moy_store_base import builtin_name
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_store_base import builtin_name

try:
    from app_context import NO_STORE
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.app_context import NO_STORE


class ListShellApp:
    """Mixin for the app layers. `_persist` expects the host class to provide
    `status` and `_save_failed`."""

    ball_steps = True           # the trackball steps its rows (Workstation.ball_steps)
    APP_TITLE = None            # the shipped cart's title ("Files", ...)
    APP_PERM = None             # its identity permission ("browse", ...)
    APP_FOLDER = None           # its store folder ("moybyte.files.moy", ...)

    @classmethod
    def is_app(cls, cart):
        """True only for the shipped app identity, not a renamed/copied cart."""
        if (not cart or cart.get("title") != cls.APP_TITLE
                or cls.APP_PERM not in (cart.get("permissions") or ())):
            return False
        path = cart.get("path")
        if not path:                 # embedded fallback cart (no writable store)
            return int(cart.get("version", 0)) >= 1
        return builtin_name(path) == cls.APP_FOLDER

    def _persist(self, res):
        """Apply the shared status contract to a role write's `(value, err)`:
        no store -> CAN'T SAVE HERE; a failure -> CAN'T SAVE <why>; sets
        _save_failed both ways and returns True on success."""
        _value, err = res
        if err is None:
            self._save_failed = False
            return True
        self._save_failed = True
        self.status = ("CAN'T SAVE HERE" if err is NO_STORE
                       else ("CAN'T SAVE " + str(err))[:28])
        return False

    # Rename entry cap -- a label, not a paragraph.
    RENAME_MAX = 24

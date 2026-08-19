# The Desk Lab apps' shared "list shell" (#78 family: Sheets / Writer /
# Storybook -- written in sequence, so each had hand-copied the same
# scaffolding). Two small bases, extracted MECHANICALLY so every derived
# number and drawn pixel stays byte-identical (#39):
#
#   ListShellLayout -- the chrome-inset frame head (w/h/fs/bar_h) + the
#                      notebook-list geometry (row_h/list_y/list_rows +
#                      the ONE row_rect formula).
#   ListShellApp    -- the app-layer scaffolding: the shipped-identity gate
#                      (is_app over APP_TITLE/APP_PERM/APP_FOLDER), the store
#                      readiness probe, the list scroll-window + up/down/A
#                      nav, the guarded blob load, and the persist tail with
#                      its CAN'T SAVE HERE / CAN'T SAVE status contract.
#
# Since the Phase 6 AppContext (runtime/app_context.py) the storage half of
# this mixin speaks the roles' `(value, err)` contract instead of hand-rolling
# it: the host class binds `self._store` to its storage role (`ctx.files` for
# the document apps, `ctx.carts` for Storybook, which authors CARTS) and
# `self._damage` to `ctx.damage`. `_persist`/`_load_json` take a RESULT PAIR
# rather than a callable -- the try/except they used to own now lives in the
# role, once, for every consumer.
#
# MicroPython-safe (json only); staged to both boards like every runtime/
# module. The apps keep their own views, draw code and store verbs.

import json

try:
    from app_context import NO_STORE
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.app_context import NO_STORE


class ListShellLayout:
    """Base for the apps' Layout classes. Subclass __init__ calls _init_frame
    first, lays out its own bands/views, then _init_list(top) for the list
    geometry. Nothing here reads a subclass field except what it set."""

    def _init_frame(self, w, h, fs, windowed):
        self.w = int(w)
        self.h = int(h)
        self.fs = max(1, int(fs))
        self.bar_h = 0 if windowed else 18 * self.fs

    def _init_list(self, top):
        """The notebook-list rows below `top` (the title/toolbar band's bottom)."""
        fs = self.fs
        self.row_h = 20 * fs
        self.list_y = top
        self.list_rows = max(1, (self.h - self.list_y - 2 * fs) // self.row_h)

    def row_rect(self, i):
        return (4 * self.fs, self.list_y + i * self.row_h,
                self.w - 8 * self.fs, self.row_h - 2 * self.fs)


class ListShellApp:
    """Mixin for the app layers. Expects the host class to provide `_store`
    (its AppContext storage role), `_damage`, layout, sel, top, status
    (+ _save_failed where _persist is used) and a _tap_row verb for the A
    button."""

    APP_TITLE = None            # the shipped cart's title ("Writer", ...)
    APP_PERM = None             # its identity permission ("notebook", ...)
    APP_FOLDER = None           # its store folder ("writer.moy", ...)

    @classmethod
    def is_app(cls, cart):
        """True only for the shipped app identity, not a renamed/copied cart."""
        if (not cart or cart.get("title") != cls.APP_TITLE
                or cls.APP_PERM not in (cart.get("permissions") or ())):
            return False
        path = cart.get("path")
        if not path:                 # embedded fallback cart (no writable store)
            return int(cart.get("version", 0)) >= 1
        return str(path).replace("\\", "/").rsplit("/", 1)[-1] == cls.APP_FOLDER

    # -- store -----------------------------------------------------------------

    def _store_ready(self):
        """A writable store is wired (the app's own storage role says so)."""
        return self._store.ready()

    def _load_json(self, res):
        """json-parse a role read's `(blob, err)`; None on any failure (a
        bad/missing document starts fresh, never crashes the shell)."""
        blob, err = res
        if err is not None or not blob:
            return None
        try:
            return json.loads(blob)
        except Exception:  # noqa: BLE001
            return None

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

    # -- typed keys ------------------------------------------------------------

    def _edge_key(self, inp):
        """The typed-key edge (one key per physical press -- the code_layer
        idiom): the keyboard reports the byte for the frame it is down then 0.
        Returns the fresh byte or 0. Hosts keep `self._ekey_prev = 0` in
        __init__/mode resets."""
        k = inp.last_key
        fresh = k if (k and k != self._ekey_prev) else 0
        self._ekey_prev = k
        return fresh

    # Rename entry cap -- a label, not a paragraph (Files narrows it to 20).
    RENAME_MAX = 24

    def _typed_rename(self, inp):
        """The rename-buffer keystroke handler the Desk-Lab apps share: Enter
        commits, Backspace trims, printable ASCII appends up to RENAME_MAX.
        Hosts supply `rename_text` and `_rename_commit()`."""
        k = self._edge_key(inp)
        if not k:
            return
        if k in (0x0D, 0x0A):
            self._rename_commit()
        elif k in (0x08, 0x7F):
            self.rename_text = self.rename_text[:-1]
        elif 0x20 <= k < 0x7F and len(self.rename_text) < self.RENAME_MAX:
            self.rename_text += chr(k)
        self._damage.all()

    # -- the list view's scroll window + nav -------------------------------------

    def _scroll_list(self):
        rows = self.layout.list_rows
        if self.sel < self.top:
            self.top = self.sel
        elif self.sel >= self.top + rows:
            self.top = self.sel - rows + 1

    def _list_nav(self, inp, count):
        """The list mode's trackball verbs: up/down wrap the selection (keeping
        the scroll window on it), A opens the row. Always handled (True)."""
        if inp.pressed("up"):
            self.sel = (self.sel - 1) % count
            self._scroll_list()
        elif inp.pressed("down"):
            self.sel = (self.sel + 1) % count
            self._scroll_list()
        elif inp.pressed("a"):
            self._tap_row(self.sel)
        return True

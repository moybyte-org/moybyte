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
#                      its CAN'T SAVE HERE / SAVE FAILED status contract.
#
# MicroPython-safe (json only); staged to both boards like every runtime/
# module. The apps keep their own views, draw code and store verbs.

import json


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
    """Mixin for the app layers. Expects the host class to provide ws, layout,
    sel, top, status (+ _save_failed where _persist is used) and a _tap_row
    verb for the A button."""

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
        ws = self.ws
        return bool(ws.carts_store is not None and ws.carts_root is not None
                    and ws.can_manage)

    def _load_blob(self, loader):
        """Run a store read through _with_sd and json-parse it; None on any
        failure (a bad/missing document starts fresh, never crashes the shell)."""
        if self.ws.carts_store is None or self.ws.carts_root is None:
            return None
        try:
            blob = self.ws._with_sd(loader)
            return json.loads(blob) if blob else None
        except Exception:  # noqa: BLE001
            return None

    def _persist(self, save_call):
        """Run a store write through _with_sd with the shared status contract:
        no store -> CAN'T SAVE HERE; an exception -> SAVE FAILED <why>; sets
        _save_failed both ways and returns True on success."""
        if not self._store_ready():
            self._save_failed = True
            self.status = "CAN'T SAVE HERE"
            return False
        try:
            self.ws._with_sd(save_call)
            self._save_failed = False
            return True
        except Exception as exc:  # noqa: BLE001 -- surface, never crash the shell
            self._save_failed = True
            self.status = ("SAVE FAILED " + str(exc))[:28]
            return False

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

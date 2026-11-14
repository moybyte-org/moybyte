"""The intents' hooks (runtime/system/intents.py, docs/os_2026-11/between_apps.md
§1) are this console's: the app carts that may open an item, a cart's key,
the delivery through Paint's and the text app's doors or a user app's
`moy_open(h)`, the "open with" menu as `DeleteConfirm.choose`, and a file
grant's store. A mixin of `Workstation`.
"""

from front import FRONT as _FRONT
from intents import INTENTS as _INTENTS
import intents as _intents
import iconsets as _iconsets
import system_api as _system_api
import text_modes as _text_modes
import moybyte as _moybyte


_FRONT_KEY = "desktop"            # the front run's window key (procs.FRONT)
_OPEN_WITH = "Open with"           # the "open with" menu's title
# The picker's title by the type asked for, and a row's icon by its file's kind.
_PICK_TITLE = (("image/", "Choose a picture"), ("text/", "Choose a document"),
               ("audio/", "Choose a song"), ("", "Choose a file"))
_PICK_ICON = {"docs": "notes", "drawings": "art", "sprites": "sprite", "music": "music",
              "recordings": "music", "stories": "storybook"}


class IntentVerbs:

    def _init_intents(self):
        """The intents' hooks are this console's (runtime/system/intents.py):
        the app carts that may open an item, a cart's key, the delivery, the
        "open with" menu (the console's question, `DeleteConfirm.choose`),
        the user-files store a file grant reads through, the row the last
        choices are kept in, and the picker: its listing, its question and
        the running app it hands a pick to. The front's gate
        (runtime/system/front.py) reads the run on screen and brings a
        process's run forward as acting on a notification does, and what a
        process asked of it runs at the start of this console's frame."""
        _INTENTS.bind(self._intent_handlers, self.app_cart_id, self._intent_deliver,
                      self._intent_ask, self._intent_read, self._intent_write,
                      self.system, self._intent_list, self._intent_ask_pick,
                      self._intent_hand)
        _FRONT.bind(self._front_pid, self._bring, True)

    def _front_pid(self):
        """The pid of the run the user is in: the front run while its
        window holds the input, else None (the shell's)."""
        pl = self.run.player
        if pl is None or pl.pid is None or self.cart is None:
            return None
        return pl.pid if self.wm.input_holder() == _FRONT_KEY else None

    def _intent_handlers(self):
        return [c for c in self.carts.all
                if c.get("type") == "app" and c.get("handles") and not c.get("broken")]

    def _intent_app(self, cart):
        """The registered app whose identity `cart` is, else None."""
        for app in self._apps:
            if app.is_app(cart):
                return app
        return None

    def _intent_icon(self, cart):
        app = self._intent_app(cart)
        if app is not None:
            return _iconsets.ALIASES.get(app.id, app.id)
        return "notes" if _system_api.is_text_app(cart) else "app"

    def _intent_ask(self, label, carts, then):
        return self.confirm.choose(_OPEN_WITH, label,
                                   [str(c.get("title") or "") for c in carts],
                                   [self._intent_icon(c) for c in carts], then)

    def _intent_ask_pick(self, pid, mime, items, then):
        """The picker: the console's one question, its title the type asked
        for, a row a file (its name as the Files app shows it, its kind's
        icon), the asking app's title under the title."""
        title = _PICK_TITLE[-1][1]
        for head, t in _PICK_TITLE:
            if mime.startswith(head):
                title = t
                break
        rows, icons = [], []
        for item in items:
            kn = _intents.split(item)
            rows.append(kn[1] if kn else item)
            icons.append(_PICK_ICON.get(kn[0], "doc") if kn else "doc")
        try:
            who = self.procs.who(pid)[0]
        except Exception:  # noqa: BLE001 -- a caption is never worth a crash
            who = ""
        return self.confirm.choose(title, ("For " + who) if who else "", rows, icons, then)

    def _intent_list(self, kind):
        h = self.store
        if self.carts_store is None or not h.ready():
            return ()
        try:
            return h.call(lambda: self.carts_store.list_files(kind, self.carts_root))
        except Exception:  # noqa: BLE001 -- an unreadable kind lists nothing
            return ()

    def _intent_hand(self, pid, g):
        """A pick to the process that asked: the running app's `moy_open(h)`
        where its run defines one (a compiled app's posts it), any other
        process's the message `moy_open` (moybyte.hand_item: a native
        command's or a service's). True where it took it (None, a cancel,
        reaches it too)."""
        if pid is None:
            return False
        pl = self.run.player
        if pl is not None and pl.pid == pid:
            if pl.cart_error is not None:
                return False
            if (pl.ns or {}).get("moy_open") is not None:
                return pl.window_event("moy_open", g)
        return _moybyte.hand_item(pid, g)

    def _intent_read(self, kind, name):
        h = self.store
        if self.carts_store is None or not h.ready():
            return None
        try:
            return h.call(lambda: self.carts_store.load_file(kind, name, self.carts_root))
        except Exception:  # noqa: BLE001 -- an unreadable file reads as gone
            return None

    def _intent_write(self, kind, name, data):
        h = self.store
        if self.carts_store is None or not h.writable():
            return False
        try:
            h.call(lambda: self.carts_store.save_file(kind, name, data, self.carts_root))
        except Exception:  # noqa: BLE001 -- the store refused: the grant says so
            return False
        return True

    def _intent_keep(self, g, kind):
        """Shared data a shell door opens as a file: written into `kind` as a
        new item named for its type, `(kind, name)`, or None."""
        h = self.store
        if self.carts_store is None or not h.writable():
            return None
        ext = ""
        for e, m in _intents.TYPES.items():
            if m == g.mime and e != ".md":
                ext = e
        store, root = self.carts_store, self.carts_root
        try:
            name = h.call(lambda: store.free_file_name(kind, "Shared" + ext, root))
            h.call(lambda: store.save_file(kind, name, g.read(), root))
        except Exception:  # noqa: BLE001 -- nothing to open
            return None
        return kind, name

    def _intent_deliver(self, cart, g):
        """Hand `g` to `cart`'s app: Paint and the text app through their
        doors (the shell opens the one file for them), any other app cart
        launched and handed the grant by its `moy_open(h)`. The receiving
        pid, the shell's for a door, or None where nothing opened."""
        app = self._intent_app(cart)
        door = "paint" if app is not None and app.id == "artwork" else (
            "text" if _system_api.is_text_app(cart) else None)
        if door is not None:
            if g.url is not None:
                return None
            kn = (g.kind, g.file) if g.file is not None else self._intent_keep(
                g, "drawings" if door == "paint" else "docs")
            if kn is None:
                return None
            if door == "paint":
                ok = self.open_image(kn[1], kn[0])
            else:
                ok = self.open_text_cart(kn[1], kn[0], _text_modes.mode_for(
                    _text_modes.file_name(kn[0], kn[1])))
            return _intents.SHELL if ok else None
        if app is not None:
            return None
        self.open(cart)
        pl = self.run.player
        if pl is None or self.cart is None or pl.cart_error is not None \
                or self.app_cart_id(self.cart) != self.app_cart_id(cart):
            return None
        if not pl.window_event("moy_open", g):
            return None
        return pl.pid

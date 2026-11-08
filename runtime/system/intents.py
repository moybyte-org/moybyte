"""Intents and MIME types (docs/os_2026-11/between_apps.md sections 1 and 2):
the one table of the types the console knows, and the routing of an item to
the app that opens it.

THE TABLE. `TYPES` maps a file's extension to its MIME type, our formats
under `x-moy*` names; a URL's type is `x-scheme-handler/<scheme>`; anything
else is `application/octet-stream`. `mime_of(item)` answers an item's type.

AN ITEM is a URL (`"https://..."`), a user file named `"<kind>/<name>"` the
way the user-files layer names it (`"docs/todo"`, `"docs/hi.py"`,
`"drawings/cat"`), or a Grant an app was handed.

THE ROUTE. An app declares the types it opens in its manifest
(`"moybyte": {"handles": ["text/markdown", "image/*"]}`; `type/*` matches
every subtype). `open_with(pid, item)` and `share(pid, data, mime)` route to
the one app that matches; where several do, to the LAST ONE CHOSEN for that
type, and where none was chosen yet, the "open with" menu asks. There is no
per-type "always": the menu changes the choice (`open_with(..., choose=True)`
opens it whatever was chosen), and the console keeps one choice a type in
`system/settings`' `open_with` row. Both answer True where the item went to
an app or the menu is up to choose one, False where no app opens it.

THE GRANT. The receiver gets the item as a Grant, never a permission: its
`name`, `mime`, `url` and `mode`, `read()` and, where it is "rw", `write(data)`,
until its run ends (`end(pid)`, from moybyte's ENDS). A file opened with
`open_with` is "rw"; shared data and a URL are "r".

What the console supplies (`bind`): the carts that may handle an item
(`handlers()`), a cart's key (`key(cart)`, the id its grant is made under),
the delivery (`deliver(cart, grant)`: the receiving process's pid, or None
where it did not open), the menu (`ask(label, carts, then)`: `then(i)` with
the row picked, None where it was cancelled) and the file store (`read(kind, name)`,
`write(kind, name, data)`)."""

TYPES = {
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".json": "application/json",
    ".py": "text/x-python",
    ".lua": "text/x-lua",
    ".moyimg": "image/x-moyimg",
    ".moygfx": "image/x-moygfx",
    ".moysong": "audio/x-moysong",
    ".png": "image/png",
}
OCTET = "application/octet-stream"
SCHEME = "x-scheme-handler/"
KEY = "open_with"                 # system/settings' row: {mime: app key}
SHELL = 1


def _ext(name):
    name = str(name)
    dot = name.rfind(".")
    slash = name.rfind("/")
    return name[dot:].lower() if dot > slash + 1 else ""


def _file_name(kind, name):
    try:
        import text_modes
    except ImportError:  # host: the runtime package
        from runtime import text_modes
    return text_modes.file_name(kind, name)


def _kinds():
    try:
        from moy_store_base import FILE_KIND_NAMES
    except ImportError:  # host: the runtime package
        from runtime.moy_store_base import FILE_KIND_NAMES
    return FILE_KIND_NAMES


def split(item):
    """`"<kind>/<name>"` as (kind, name), a kind of the user-files layer's
    and one name in it, else None."""
    if not isinstance(item, str) or "://" in item:
        return None
    k = item.find("/")
    if k <= 0 or k == len(item) - 1 or "/" in item[k + 1:] or item[:k] not in _kinds():
        return None
    return item[:k], item[k + 1:]


def mime_of(item):
    """The MIME type of `item`: a URL's scheme handler, a Grant's own type,
    a user file's by its stored name's extension."""
    if isinstance(item, Grant):
        return item.mime
    if not isinstance(item, str) or not item:
        return OCTET
    k = item.find("://")
    if k > 0:
        return SCHEME + item[:k].lower()
    kn = split(item)
    name = _file_name(kn[0], kn[1]) if kn is not None else item
    return TYPES.get(_ext(name), OCTET)


def matches(pattern, mime):
    """True where manifest type `pattern` takes `mime`: the same type, or
    `type/*` for any of its subtypes."""
    if not isinstance(pattern, str):
        return False
    if pattern == mime:
        return True
    return pattern[-2:] == "/*" and mime[:len(pattern) - 1] == pattern[:-1]


def handles(cart, mime):
    """True where `cart`'s manifest says its app opens `mime`."""
    hs = cart.get("handles") if cart else None
    if not isinstance(hs, list):
        return False
    for p in hs:
        if matches(p, mime):
            return True
    return False


class Grant:
    """One item handed to the app that opens it, for its run."""

    def __init__(self, mime, mode="r", name=None, url=None, data=None,
                 kind=None, file=None):
        self.mime = mime
        self.mode = mode
        self.name = name
        self.url = url
        self.kind = kind          # a user file's kind and name, else None
        self.file = file
        self._data = data
        self._io = None           # the Intents whose store reads a file
        self.live = True

    def read(self):
        """The item's content (a text file's str, else bytes; a URL's None),
        None once the grant has ended or the file is gone."""
        if not self.live:
            return None
        if self.file is None:
            return self._data
        return self._io.read(self.kind, self.file) if self._io else None

    def write(self, data):
        """Write the file back: True where it was written. False for a grant
        that is not "rw", has ended, or a store that refused."""
        if not self.live or self.mode != "rw" or self.file is None or self._io is None:
            return False
        return self._io.write(self.kind, self.file, data)

    def end(self):
        self.live = False
        self._data = None


class Intents:
    """The routing, the last choices and the grants each process holds."""

    def __init__(self):
        self.handlers = None      # fn() -> carts that may handle an item
        self.key = None           # fn(cart) -> its app key
        self.deliver = None       # fn(cart, grant) -> receiving pid, or None
        self.ask = None           # fn(label, carts, then) -> bool
        self.store_read = None    # fn(kind, name) -> content, or None
        self.store_write = None   # fn(kind, name, data) -> bool
        self.prefs = None         # the console's system/settings rows
        self.held = {}            # pid -> the grants its run holds
        self.asking = None        # (grant, candidates) while the menu is up

    def bind(self, handlers, key, deliver, ask, read, write, prefs):
        self.handlers, self.key, self.deliver, self.ask = handlers, key, deliver, ask
        self.store_read, self.store_write, self.prefs = read, write, prefs

    def reset(self):
        for gs in self.held.values():
            for g in gs:
                g.end()
        self.held = {}
        self.asking = None

    # -- the store a file grant reads through ----------------------------------

    def read(self, kind, name):
        return self.store_read(kind, name) if self.store_read else None

    def write(self, kind, name, data):
        return bool(self.store_write(kind, name, data)) if self.store_write else False

    # -- the choices -------------------------------------------------------------

    def last(self, mime):
        """The key of the app last chosen for `mime`, or None."""
        got = self.prefs.get(KEY, None) if self.prefs is not None else None
        return got.get(mime) if isinstance(got, dict) else None

    def _remember(self, mime, key):
        if self.prefs is None:
            return
        got = self.prefs.get(KEY, None)
        got = dict(got) if isinstance(got, dict) else {}
        if got.get(mime) != key:
            got[mime] = key
            self.prefs.set(KEY, got)

    def candidates(self, mime):
        """The carts whose apps open `mime`: the last chosen first, the rest
        by title."""
        got = [c for c in (self.handlers() if self.handlers else ()) if handles(c, mime)]
        got.sort(key=lambda c: str(c.get("title") or ""))
        last = self.last(mime)
        if last is not None:
            for i in range(len(got)):
                if self.key(got[i]) == last:
                    got.insert(0, got.pop(i))
                    break
        return got

    def route(self, mime):
        """`(candidates, pick)`: the cart the item goes to without asking
        (the one match, else the last chosen), None where the menu asks."""
        cands = self.candidates(mime)
        if len(cands) == 1:
            return cands, cands[0]
        last = self.last(mime)
        if cands and last is not None and self.key(cands[0]) == last:
            return cands, cands[0]
        return cands, None

    # -- the verbs ---------------------------------------------------------------

    def grant_for(self, item):
        """The Grant `item` names, or None where it names nothing."""
        if isinstance(item, Grant):
            if not item.live:
                return None
            g = Grant(item.mime, item.mode, item.name, item.url, item._data,
                      item.kind, item.file)
            g._io = self
            return g
        if not isinstance(item, str) or not item:
            return None
        mime = mime_of(item)
        if mime[:len(SCHEME)] == SCHEME:
            return Grant(mime, "r", item, url=item)
        kn = split(item)
        if kn is None:
            return None
        g = Grant(mime, "rw", _file_name(kn[0], kn[1]), kind=kn[0], file=kn[1])
        g._io = self
        return g

    def open_with(self, pid, item, choose=False):
        g = self.grant_for(item)
        return False if g is None else self._send(g, choose)

    def share(self, pid, data, mime):
        if not isinstance(mime, str) or "/" not in mime or data is None:
            return False
        return self._send(Grant(mime, "r", data=data), False)

    def _send(self, g, choose):
        cands, pick = self.route(g.mime)
        if not cands:
            return False
        if pick is not None and not choose:
            return self._give(pick, g)
        if self.ask is None:
            return False
        if not self.ask(g.name or "", cands, self.chose):
            return False
        self.asking = (g, cands)     # after: a menu it replaced was answered None
        return True

    def chose(self, i):
        """The menu's answer: row `i` (None: cancelled). The choice is
        remembered for the item's type and the item goes to it."""
        asking, self.asking = self.asking, None
        if asking is None or i is None:
            return False
        g, cands = asking
        if not 0 <= i < len(cands):
            return False
        self._remember(g.mime, self.key(cands[i]))
        return self._give(cands[i], g)

    def _give(self, cart, g):
        pid = self.deliver(cart, g) if self.deliver else None
        if pid is None:
            g.end()
            return False
        if pid == SHELL:
            g.end()               # a shell door read what it opens itself
        else:
            self.held.setdefault(pid, []).append(g)
        return True

    def end(self, pid):
        """Process `pid` ended: the grants it was handed end with it."""
        for g in self.held.pop(pid, ()):
            g.end()


INTENTS = Intents()

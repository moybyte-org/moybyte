"""A compiled cart's written files (moy-spec SPEC.md 16.12), on every tier.

A compiled cart declares the paths it writes (the manifest's "writable"),
and libmoy's binding holds every `write`, `erase` and `list` to them before a
path reaches this module. What is here is the store: where a cart's files are
kept, a write that is whole or not at all, and the listing.

WHERE: beside the carts store, `written/<cart>/` -- <cart> the cart folder's
name less ".moy" (`cart_id`) -- so an install, an update or a push replaces
the cart's folder and never touches them, and Get Carts' REMOVE takes them
with the cart (`remove`, from runtime/cart_index.py's).

ONE FLAT FOLDER PER CART: a path is one file named by its key (`key`), the
rule moy-spec's libmoy/port/moy_files.c keeps for the desktop player: a-z,
0-9, '_', '-' and an inner '.' as they are, every other byte '%' and two
lowercase hex digits, and a name Windows keeps for a device escaped at its
first byte. A key is lowercase, so the T-Deck's FAT card keeps "Save" and
"save" apart, and it decodes back to its path for `list`.

A WRITE IS WHOLE OR NOT AT ALL: it goes to "<key>~part", becomes
"<key>~done" once the file is closed, and then replaces "<key>". A key never
holds '~'. Opening a cart's files finishes what a power loss left: a "~done"
replaces its file and a "~part" goes. On the T-Deck's card a rename over a
file is a remove and a rename, which is the window "~done" covers.

ONE WRITER: the cart's session is the only thing that writes the folder, so
what is written is read once when the session opens and kept, and `where`
and `list` never touch the store after that.

THE TIERS: the boards' and the browser's moycore call this on the
MicroPython task (native/moycore/modmoycore.c, through moy_wasm_on_vm) and the
host's binding through ctypes (runtime/wasm_binding.py). `gate(fn)` runs fn
inside the store's session -- the T-Deck's SD gate. In the browser the VFS is
memory and OPFS is the store of record: `keep` is the page's keeper
(firmware/web_runner/carts_link.py's WebCartKeep), which a write and an erase
hand to the worker to make durable there, as an install's files are.

Paths cross as bytes, so every path the rule allows is kept as it is.
MicroPython-safe: os only, no os.path.
"""

try:
    import os
except ImportError:  # pragma: no cover
    os = None

try:
    from moy_store_base import _sibling_path, _rmtree, _is_dir
    from moy_fs import _mkdir, _remove
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_store_base import _sibling_path, _rmtree, _is_dir
    from runtime.moy_fs import _mkdir, _remove

WRITTEN_DIR = "written"
NO_ROOM, FAILED = -2, -3              # SPEC.md 16.12's write answers
PART, DONE = "~part", "~done"
ENOSPC = 28
DEPTH_MAX = 16                        # how deep `list` walks the cart's folder

_KEPT = b"abcdefghijklmnopqrstuvwxyz0123456789_-"
_DEVICES = ("con", "prn", "aux", "nul")


def _encode(path, first_out):
    out = []
    n = len(path)
    for i in range(n):
        c = path[i]
        kept = c in _KEPT or (c == 0x2E and 0 < i < n - 1)
        if kept and not (i == 0 and first_out):
            out.append(chr(c))
        else:
            out.append("%%%02x" % c)
    return "".join(out)


def key(path):
    """The file name the written path `path` (bytes) is kept under."""
    k = _encode(path, False)
    stem = k.split(".", 1)[0]
    if stem in _DEVICES or (len(stem) == 4 and stem[:3] in ("com", "lpt")
                            and "1" <= stem[3] <= "9"):
        k = _encode(path, True)
    return k


_HEX = "0123456789abcdefABCDEF"


def path_of(name):
    """A key back to its path (bytes), or None for a name that is not one: a
    name holding anything a key never holds ('~' among them), or an escape
    that is not '%' and two hex digits."""
    if not name:
        return None
    out = bytearray()
    i = 0
    while i < len(name):
        c = name[i]
        if c == "%":
            h = name[i + 1:i + 3]
            if len(h) != 2 or h[0] not in _HEX or h[1] not in _HEX:
                return None
            out.append(int(h, 16))
            i += 3
        elif c == "." or ord(c) < 128 and ord(c) in _KEPT:
            out.append(ord(c))
            i += 1
        else:
            return None
    return bytes(out)


def cart_id(folder):
    """The name a cart's files are kept under: its folder's, less ".moy"."""
    return folder[:-4] if folder.endswith(".moy") and len(folder) > 4 else folder


def written_root(carts_root):
    return _sibling_path(carts_root, WRITTEN_DIR)


def folder_of(cart_path):
    """The folder the cart at `cart_path` keeps its written files in."""
    parts = cart_path.rstrip("/").rsplit("/", 1)
    root, folder = (parts[0], parts[1]) if len(parts) == 2 else ("", parts[0])
    return written_root(root) + "/" + cart_id(folder)


def remove(carts_root, folder):
    """Every file the cart in `folder` wrote. Inside the store's session."""
    _rmtree(written_root(carts_root) + "/" + cart_id(folder))


def _errno(exc):
    return exc.args[0] if getattr(exc, "args", None) else None


def _call(gate, fn):
    return gate(fn) if gate is not None else fn()


class CartFiles:
    """The written files of the cart at `cart_path`, for one session."""

    def __init__(self, cart_path, gate=None, keep=None):
        self.cart = cart_path.rstrip("/")
        self.dir = folder_of(self.cart)
        self.id = self.dir.rsplit("/", 1)[-1]
        self.gate = gate
        self.keep = keep
        self._names = None            # the listing, sorted, built on first list
        self._made = False
        self.written = set()
        _call(gate, self._recover)

    # -- the folder ------------------------------------------------------------

    def _recover(self):
        """What a power loss left, put right, and what is written, known."""
        try:
            names = os.listdir(self.dir)
        except OSError:
            return
        self._made = True
        for n in names:
            if n.endswith(DONE):
                self._replace(self.dir + "/" + n, self.dir + "/" + n[:-len(DONE)])
            elif n.endswith(PART):
                _remove(self.dir + "/" + n)
        for n in os.listdir(self.dir):
            p = path_of(n)
            if p is not None:
                self.written.add(p)

    @staticmethod
    def _replace(src, dst):
        try:
            os.rename(src, dst)
        except OSError:
            _remove(dst)
            os.rename(src, dst)

    def _ensure(self):
        if self._made:
            return
        root = self.dir.rsplit("/", 1)[0]
        _mkdir(root.rsplit("/", 1)[0])
        _mkdir(root)
        _mkdir(self.dir)
        if not _is_dir(self.dir):
            raise OSError(ENOSPC)
        self._made = True

    # -- the binding's four ----------------------------------------------------

    def where(self, path):
        """The file holding the written copy of `path`, or None."""
        if path not in self.written:
            return None
        return self.dir + "/" + key(path)

    def read(self, path, offset, n):
        """read's contract on the written copy: its bytes from `offset`, at
        most `n` of them, or with `n` 0 how many remain (an int). None when
        there is no written copy."""
        p = self.where(path)
        if p is None:
            return None

        def _read():
            with open(p, "rb") as f:
                size = f.seek(0, 2)
                if offset >= size:
                    return b"" if n else 0
                if not n:
                    return size - offset
                f.seek(offset)
                return f.read(min(n, size - offset))
        try:
            return _call(self.gate, _read)
        except OSError:
            return None

    def write(self, path, data):
        """Replace the written copy of `path` with `data`, whole: 0, NO_ROOM
        or FAILED."""
        if self.keep is not None and not self.keep.fits(len(data)):
            return NO_ROOM
        k = key(path)
        base = self.dir + "/" + k

        def _write():
            self._ensure()
            try:
                with open(base + PART, "wb") as f:
                    if len(data) and f.write(data) != len(data):
                        raise OSError(ENOSPC)
            except OSError as exc:
                _remove(base + PART)
                return NO_ROOM if _errno(exc) == ENOSPC else FAILED
            _remove(base + DONE)
            os.rename(base + PART, base + DONE)
            self._replace(base + DONE, base)
            return 0
        try:
            r = _call(self.gate, _write)
        except OSError as exc:
            return NO_ROOM if _errno(exc) == ENOSPC else FAILED
        if r == 0:
            if path not in self.written:
                self.written.add(path)
                self._names = None
            if self.keep is not None:
                self.keep.wrote(self.id, k)
        return r

    def erase(self, path):
        """Remove the written copy: 0, or -1 when there is none."""
        if path not in self.written:
            return -1
        k = key(path)
        try:
            _call(self.gate, lambda: os.remove(self.dir + "/" + k))
        except OSError:
            return -1
        self.written.discard(path)
        self._names = None
        if self.keep is not None:
            self.keep.erased(self.id, k)
        return 0

    def name(self, prefix, index):
        """The `index`-th path (bytes), in bytewise order, of the cart's files
        that begin with `prefix`: its folder's and the written ones, each path
        once. None past the last."""
        names = self._listing()
        lo, hi = 0, len(names)
        while lo < hi:
            mid = (lo + hi) // 2
            if names[mid] < prefix:
                lo = mid + 1
            else:
                hi = mid
        i = lo + index
        if i < len(names) and names[i][:len(prefix)] == prefix:
            return names[i]
        return None

    # -- list --------------------------------------------------------------------

    def _listing(self):
        if self._names is None:
            if self._shipped is None:
                found = []
                _call(self.gate, lambda: self._walk(self.cart, b"", found, 0))
                self._shipped = found
            names = set(self._shipped)
            names.update(self.written)
            self._names = sorted(names)
        return self._names

    _shipped = None

    def _walk(self, folder, rel, out, depth):
        try:
            names = os.listdir(folder)
        except OSError:
            return
        for n in names:
            p = folder + "/" + n
            r = rel + n.encode("utf-8")
            if _is_dir(p):
                if depth < DEPTH_MAX:
                    self._walk(p, r + b"/", out, depth + 1)
            else:
                out.append(r)

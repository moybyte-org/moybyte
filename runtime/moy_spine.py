"""The kernel's spine: the interface the native module `moy_spine` exposes,
call for call, and its Python twin (docs/kernel_spine_2026-10.md).

Six components, each holding numbers and strings only -- no runtime's
object outlives a call into one (docs/native_kernel_2026-09.md section 4.3):

  Table        a handle table: rows named by kinded, generation-checked ints
  AppRegistry  the registered system apps: id, title, text mode, min size
  BackStack    the process back-stack: kinds, launcher root at the bottom
  Returns      where a leaving surface lands: a run's caller, an app's return
  Leases       the WiFi radio's holders, a closed set of tags
  Settings     system.json as the store holds it: key -> the value's JSON text,
               written through one setter that persists

A handle is gen << GEN_SHIFT | kind << KIND_SHIFT | slot: never 0, always
below 2**30 (a small int on every VM), its generation in the same bits as
runtime/moy_index.py's, so the index is this table with the kind field folded
into a 12-bit slot. A handle presented to a table that did not make it, or
after its row was released, raises StaleHandle (a ValueError); a handle that is
not an int raises TypeError.

A kind (an app id or a back-stack kind) is a str of 1 .. ID_MAX bytes:
TypeError otherwise, ValueError when empty or longer. Settings keys are any
non-empty str and values are JSON text.

Errors are the native module's: OSError(ENOSPC) when a fixed table is full,
ValueError for a refused argument, TypeError for a wrong type. The arguments the
native module cannot hold are refused here too: a table's name is a str, an
app's min size fits 32 bits, a settings key holds no lone surrogate, and a
settings value nests at most 31 containers (the file's object is the 32nd).
Settings.load re-encodes each value, so for text json.dumps wrote the rows are
the native scanner's byte for byte; the native module keeps any other text as
it was written.
"""

import json

SLOT_BITS = 8
KIND_SHIFT = 8
GEN_SHIFT = 12
GEN_MAX = (1 << 18) - 1
SLOTS = 1 << SLOT_BITS
ID_MAX = 15
JSON_DEPTH = 32

KIND_APP = 1           # the kinds, one per client table (moy_htab.h)
KIND_BUF = 2
KIND_CANVAS = 3
KIND_SURF = 4
KIND_OWNER = 5
KIND_SRC = 6
KIND_PEER = 7
KIND_AUDIO = 8
KIND_CLIP = 9

_SLOT_MASK = SLOTS - 1
_KIND_MASK = 0xF
_ENOSPC = 28


class StaleHandle(ValueError):
    """A handle that names no live row of the table it was given to."""


def _kind(k):
    """`k` if it is a legal kind, else the error the native call raises."""
    if not isinstance(k, str):
        raise TypeError("a kind is a str")
    n = len(k.encode())
    if n == 0 or n > ID_MAX:
        raise ValueError("kind %r is not 1..%d bytes" % (k, ID_MAX))
    return k


class Table:
    """Rows in slots, each slot with a generation that moves on when its row is
    released; a freed slot is taken again lowest-first. `name` is what a stale
    handle's error says it was."""

    def __init__(self, kind, name, slots=SLOTS):
        if not isinstance(name, str):
            raise TypeError("a table name is a str")
        if not 1 <= kind <= _KIND_MASK or not 1 <= slots <= SLOTS:
            raise ValueError("table kind 1..15, slots 1..%d" % SLOTS)
        self.kind = kind
        self.name = name
        self._cap = slots
        self._row = []
        self._gen = []
        self._live = []
        self._free = 0

    def _h(self, s):
        return (self._gen[s] << GEN_SHIFT) | (self.kind << KIND_SHIFT) | s

    def _check(self, h):
        s = h & _SLOT_MASK
        if (h <= 0 or (h >> KIND_SHIFT) & _KIND_MASK != self.kind
                or s >= len(self._gen) or not self._live[s]
                or self._gen[s] != h >> GEN_SHIFT):
            raise StaleHandle("stale %s handle %d" % (self.name, h))
        return s

    def new(self, row):
        if self._free:
            s = self._live.index(False)
            self._free -= 1
        elif len(self._gen) < self._cap:
            s = len(self._gen)
            self._row.append(None)
            self._gen.append(1)
            self._live.append(False)
        else:
            raise OSError(_ENOSPC, "%s table full" % self.name)
        self._row[s] = row
        self._live[s] = True
        return self._h(s)

    def get(self, h):
        return self._row[self._check(h)]

    def put(self, h, row):
        self._row[self._check(h)] = row

    def valid(self, h):
        try:
            self._check(h)
        except (StaleHandle, TypeError):
            return False
        return True

    def release(self, h):
        s = self._check(h)
        row = self._row[s]
        self._row[s] = None
        self._live[s] = False
        g = self._gen[s] + 1
        self._gen[s] = 1 if g > GEN_MAX else g
        self._free += 1
        return row

    def handles(self):
        return [self._h(s) for s in range(len(self._gen)) if self._live[s]]

    def count(self):
        return len(self._gen) - self._free


class AppRegistry:
    """The system apps, in registration order (which is dispatch precedence).
    A row is (id, title, text_mode, min_size); apps never unregister."""

    SLOTS = 64

    def __init__(self):
        self._t = Table(KIND_APP, "app", self.SLOTS)
        self._by_id = {}

    def register(self, app_id, title, text_mode=False, min_size=None):
        _kind(app_id)
        if app_id in self._by_id:
            raise ValueError("duplicate app id: " + app_id)
        if min_size is not None:
            min_size = (int(min_size[0]), int(min_size[1]))
            if not all(-(1 << 31) <= v < (1 << 31) for v in min_size):
                raise ValueError("min_size must fit 32 bits")
        h = self._t.new((app_id, str(title), bool(text_mode), min_size))
        self._by_id[app_id] = h
        return h

    def find(self, app_id):
        """The app's handle, or 0 when no app has that id."""
        if not isinstance(app_id, str):
            raise TypeError("an app id is a str")
        return self._by_id.get(app_id, 0)

    def app_id(self, h):
        return self._t.get(h)[0]

    def title(self, h):
        return self._t.get(h)[1]

    def text_mode(self, h):
        return self._t.get(h)[2]

    def min_size(self, h):
        return self._t.get(h)[3]

    def valid(self, h):
        return self._t.valid(h)

    def handles(self):
        return self._t.handles()

    def count(self):
        return self._t.count()


# goto's answers
STAYED = 0
PUSHED = 1
RETURNED = 2

ROOT = "launcher"


class BackStack:
    """The process back-stack, bottom to top. The launcher is the root and is
    never popped or removed; a kind appears at most once."""

    DEPTH = 32

    def __init__(self):
        self._k = [ROOT]

    def goto(self, kind):
        """Make `kind` the top: STAYED when it already is, RETURNED when it was
        open below (everything above it goes), PUSHED when it was not open."""
        st = self._k
        if st[-1] == _kind(kind):
            return STAYED
        for i in range(len(st) - 2, -1, -1):
            if st[i] == kind:
                del st[i + 1:]
                return RETURNED
        if len(st) >= self.DEPTH:
            raise OSError(_ENOSPC, "back-stack full")
        st.append(kind)
        return PUSHED

    def remove(self, kind):
        """Take `kind` out wherever it stands; False when it is absent or the
        root."""
        st = self._k
        if _kind(kind) == ROOT or kind not in st:
            return False
        st.remove(kind)
        return True

    def top(self):
        return self._k[-1]

    def has(self, kind):
        return kind in self._k

    def index(self, kind):
        """`kind`'s depth from the root, or -1."""
        st = self._k
        for i in range(len(st)):
            if st[i] == kind:
                return i
        return -1

    def depth(self):
        return len(self._k)

    def kinds(self):
        return list(self._k)


# The back-stack kind the Editor runs as, and so the caller kind of its PLAY.
EDITOR = "menu"

# Where a run's exit lands (Returns.route).
ROUTE_HOME = 0      # the launcher root, or back into the app that opened the surface
ROUTE_EDITOR = 1    # the Editor, on the tab it left
ROUTE_APP = 2       # the calling app's own surface
ROUTE_WINDOW = 3    # the windowed desk: close the player window only


class Returns:
    """The return records. A run records its caller's kind; an app-to-app jump
    records the app it left, which survives nested runs and is cleared only by
    going home."""

    def __init__(self, apps):
        self._apps = apps
        self._caller = None
        self._back = None

    def run(self, caller):
        """Record the kind a starting run returns to (None: home)."""
        self._caller = None if caller is None else _kind(caller)

    def caller(self):
        return self._caller

    def spend(self):
        """The caller, cleared: a run's caller is spent by its return."""
        c = self._caller
        self._caller = None
        return c

    def route(self, windowed):
        """Where the recorded run's exit lands; spends nothing. `windowed` is
        whether the desk is open and its WM closes player windows."""
        c = self._caller
        if windowed:
            return ROUTE_WINDOW
        if c == EDITOR:
            return ROUTE_EDITOR
        if c is not None and self._apps.find(c):
            return ROUTE_APP
        return ROUTE_HOME

    def note(self, kind):
        """Record `kind` as the app a jump is leaving, when it is a registered
        app. Only ever sets; True when it did."""
        if not self._apps.find(_kind(kind)):
            return False
        self._back = kind
        return True

    def back(self):
        return self._back

    def take_back(self):
        b = self._back
        self._back = None
        return b


LEASE_TAGS = ("web", "update", "settings", "cart", "link", "carts", "dev")


def _lease_bit(tag):
    if not isinstance(tag, str):
        raise TypeError("a lease tag is a str")
    for i in range(len(LEASE_TAGS)):
        if LEASE_TAGS[i] == tag:
            return 1 << i
    raise ValueError("unknown lease tag %r" % tag)


class Leases:
    """The radio's holders as a mask over LEASE_TAGS. A hold is idempotent per
    tag and releasing a tag nobody holds is not an error; an unknown tag is."""

    def __init__(self):
        self._mask = 0

    def hold(self, tag):
        """Take `tag`'s lease; the mask after."""
        self._mask |= _lease_bit(tag)
        return self._mask

    def release(self, tag):
        """Drop `tag`'s lease; the mask after (0: nobody holds the radio)."""
        self._mask &= ~_lease_bit(tag)
        return self._mask

    def mask(self):
        return self._mask

    def held(self, tag):
        return bool(self._mask & _lease_bit(tag))

    def holders(self):
        """The holding tags, in LEASE_TAGS order."""
        return [LEASE_TAGS[i] for i in range(len(LEASE_TAGS))
                if self._mask & (1 << i)]


def _key(k):
    if not isinstance(k, str):
        raise TypeError("a settings key is a str")
    if not k:
        raise ValueError("an empty settings key")
    k.encode()              # a lone surrogate holds no UTF-8: ValueError
    return k


def _depth(v):
    """The containers `v` nests: 0 for a scalar, 1 for an empty list or dict."""
    if isinstance(v, dict):
        v = list(v.values())
    elif not isinstance(v, (list, tuple)):
        return 0
    return 1 + max([_depth(x) for x in v] or [0])


def _value(text):
    """`text`'s value, ValueError unless it is one JSON value a row may hold."""
    if not isinstance(text, str):
        raise TypeError("a settings value is JSON text")
    v = json.loads(text)
    if _depth(v) >= JSON_DEPTH:
        raise ValueError("a settings value nests too deep")
    return v


class Settings:
    """system.json as rows: one per top-level key, holding that value's JSON
    text. `dump` is the file: the object json.dumps writes, keys in row order
    (a new key goes last). The rows are the store of record: `get` decodes a
    row to a fresh value, so nothing a caller does to what it got can change a
    row, and `set` is the only way in.

    A write marks the store dirty and persists it: `save(text)` is the hook
    that writes `dump()` where the file lives, and the store stays dirty until
    it has (a hook that returns False, or none at all, leaves it dirty, and the
    next write or `flush` tries again), so a change cannot be left out of the
    file by a caller that forgot to ask. `persist=False` defers the write to
    the next one: the change is dirty, never lost. A component that owns a key
    reads and writes its row."""

    def __init__(self, save=None):
        self._keys = []
        self._text = {}
        self._save = save
        self._dirty = 0

    def load(self, text):
        """Replace every row with the object `text` holds; ValueError when it
        is not a JSON object, and then nothing changes. The row count. What
        was read is what is on disk, so the store is clean."""
        d = json.loads(text)
        if not isinstance(d, dict):
            raise ValueError("system.json is not an object")
        self.adopt(d)
        return len(self._keys)

    def adopt(self, d):
        """Replace every row with `d`'s items, each value encoded, and mark the
        store clean; a key or a value that is refused leaves the rows as they
        were."""
        keys = [_key(k) for k in d]
        rows = {}
        for k in keys:
            rows[k] = json.dumps(d[k])
            _value(rows[k])
        self._keys = keys
        self._text = rows
        self._dirty = 0

    def get(self, key, default=None):
        """The value of `key`'s row, decoded afresh, or `default` when the key
        has no row."""
        t = self._text.get(_key(key))
        return default if t is None else json.loads(t)

    def text(self, key):
        """The row's JSON text, or None when the key has no row."""
        return self._text.get(_key(key))

    def set(self, key, value, persist=True):
        """Store `value` (anything json.dumps writes) as `key`'s row, mark the
        store dirty and, unless `persist` is False, write it."""
        self.set_text(key, json.dumps(value), persist)

    def set_text(self, key, text, persist=True):
        """`set` for a value that is already JSON text, kept as written."""
        _value(text)
        if _key(key) not in self._text:
            self._keys.append(key)
        self._text[key] = text
        self._dirty += 1
        if persist:
            self.flush()

    def delete(self, key, persist=True):
        """Drop `key`'s row: True when it had one, which marks the store dirty
        and, unless `persist` is False, writes it."""
        if _key(key) not in self._text:
            return False
        del self._text[key]
        self._keys.remove(key)
        self._dirty += 1
        if persist:
            self.flush()
        return True

    def dirty(self):
        """True while a change has not reached the file."""
        return self._dirty > 0

    def flush(self):
        """Write the rows when the store is dirty: True when it is clean
        afterwards. `save` is called with `dump()`; a False answer is a write
        that failed."""
        if not self._dirty:
            return True
        if self._save is None or self._save(self.dump()) is False:
            return False
        self._dirty = 0
        return True

    def keys(self):
        return list(self._keys)

    def dump(self):
        return "{" + ", ".join(json.dumps(k) + ": " + self._text[k]
                               for k in self._keys) + "}"

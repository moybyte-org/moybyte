"""cart_index -- the console's `moy install`: carts other people publish,
browsed and installed over WiFi with no PC involved (#124).

THE FORMAT IS moy-spec's (cartindex.py, index version 1), read as it is. A
carts repository lists its carts in one index.json: id, name, version, licence,
the release assets with every file's size and sha256, and any file a cart needs
that is not in its release (an external file: Doom's WAD), with where to fetch
it and under what licence. moybyte-org/carts is the first (DEFAULT_INDEXES).

What it does the way `moy install` does:
  * every byte is checked against the index: each release asset's size and
    sha256, every file in it, each external archive and the file inside it;
  * an external file is fetched only after its licence text, itself checked by
    sha256, has been shown and accepted (the app's job; `Install` refuses an
    external nobody accepted) -- bare from its `mirror`, the copy its
    repository gives away beside the index, when the index names one, and
    from its archive when it does not or the mirror does not answer;
  * nothing reaches the carts folder until every file has been checked: the
    cart is built in a staging folder and moved into place by one rename.

What it does differently, because a console is not a PC:
  * it installs only THIS console's compiled module (`main.<chip>.f<format>.aot`,
    tools/wasm_cart.py's `aot_name`) beside main.wasm; the other modules stream
    past unwritten. With none for this console the cart plays on the
    interpreter (docs/wasm_tier_plan_2026-09.md, "A cart survives its firmware").
  * it streams. The release asset is a STORED zip (a carts repository builds
    it sorted and uncompressed), so its members are cut out of the socket as
    they pass, and an external file from its mirror goes straight to the store
    the same way. An external file's tar.gz is kept whole while its hash is
    checked -- in RAM, or in a file beside the build where memory is short
    (`_TarGzReader` says why) -- and then inflated. `Install.step` does a
    bounded slice per frame.
  * it updates in place: a newer version, or files that differ from what was
    installed (a module for this console's format, say), replaces the folder
    through the same staging, carrying the kid's saves and an edited config.
  * it installs only what a sandbox runs (RUNTIMES): a compiled cart, whose
    native module a board loads only by its signature, or a Lua cart. A board
    fetches over TLS without checking certificates (the OTA's arrangement),
    so an index is not proof of anything; what lands is sandboxed or signed.

The store layout sits BESIDE the carts folder (moy_store_base._sibling_path):
`installed.json`, the record of what this store installed (index, id, version,
module, every file's sha256), and `install/`, where an install is built. A cart
folder holds only its index's files; the record is what says which version it
is. An `indexes.json` there replaces DEFAULT_INDEXES ({"indexes": [url, ...]}),
the way /sd/update/ota.json points the OTA check at a LAN host.

A crash at any point leaves the old cart or the new one, never half of either:
`recover`, which the app runs when it opens, removes an unfinished build and
puts back an old copy whose replacement never moved in.

Host == device == browser: MicroPython-safe (`deflate` inflates there, `zlib`
on CPython), and the network is injected -- urllib on the host
(runtime/host_app.py), the board's TLS client on a board (device/cart_net.py),
the page's fetch in the browser (firmware/web_runner/carts_link.py). A
transport's `open(url)` answers an object with `status`, `length` (None when
unknown), `readinto(buf)` and `close()`.

A transport may be NON-BLOCKING, and the browser's is: its bytes arrive between
frames, while no Python runs, so nothing here may wait for them inside a step.
Its answer has `status` None until the response head is in, and `ready(n)`,
True once `n` bytes (or the end of the body, or a failure) are in hand; a
blocking transport has no `ready`. `Fetch`, `Reach` and `Install` all give the
frame back while a non-blocking answer is on its way, and each reads only what
`ready` vouches for.

A transport with `ranges` set (the browser's) can also be asked for part of a
file -- `open(url, span=(start, n))`, answered 206 with those `n` bytes -- and
then a release asset this console keeps only some of is read in PIECES: its
zip's central directory first, then each run of members it keeps, so the
other chips' modules are never fetched (`_Ranged`). Every member it keeps is
checked against the index's sha256 as ever; the asset's own hash, which also
covers what was skipped, is the one check a ranged read cannot make, and
nothing it would vouch for is written. A server that answers a range with the
whole file (200) gets the whole-asset read, which is what every console
without `ranges` makes.

A transport with `cors` set (the browser's) can read only what a server lets
another origin read. A GitHub release download is not that, so it reads an
asset's `mirror` -- the same bytes beside the index on the repository's Pages
site (moy-spec's cartindex.py says how a repository publishes one) -- before
its release `url`; every other console reads the release first and falls back
to the mirror. An external file's mirror is on that site too, so such a
console reads it like any other; one it can read from neither the mirror nor
its archive's hosts is one the player SUPPLIES instead (`Install`'s
`supplied`, the Get Carts app's file picker), checked like a download.

Where the store of record is not the files this module writes (the browser's
OPFS behind its in-memory VFS), a KEEPER is injected (`Install`'s `keep`):
`commit(folder, stage, record, done)` makes the staged folder and the record
durable together and then calls `done(why, full)` once -- `why` empty when
they are, else the reason, `full` when the store had no room;
`landed(folder)` hears that the folder moved into the carts folder;
`record(text)` makes a record without a folder durable (a removal).
"""

import binascii
import hashlib
import json
import sys

try:
    import os
except ImportError:  # pragma: no cover
    os = None

try:
    import io as _io
except ImportError:  # pragma: no cover
    _io = None

try:
    from ticks import _ticks_ms, _ticks_diff
    from moy_store_base import _sibling_path, _rmtree, _is_dir, COVER_MAX_BYTES
    from moy_fs import _exists, _mkdir
    import moy_carts as _store
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.ticks import _ticks_ms, _ticks_diff
    from runtime.moy_store_base import (_sibling_path, _rmtree, _is_dir,
                                        COVER_MAX_BYTES)
    from runtime.moy_fs import _exists, _mkdir
    from runtime import moy_carts as _store


INDEX_VERSION = 1
DEFAULT_INDEXES = (
    "https://moybyte-org.github.io/carts/index.json",
)
INDEXES_NAME = "indexes.json"
RECORD_NAME = "installed.json"
STAGE_DIR = "install"
RUNTIMES = ("wasm", "lua")

INDEX_LIMIT = 262144          # an index read whole into RAM
TEXT_LIMIT = 65536            # a licence text, likewise

# The pipeline's three knobs. A socket read lands straight in the write buffer;
# a full buffer, or a file's end, is ONE store session (on the T-Deck that
# session is the SD gate, with a panel sync in front of it); a step is as many
# reads as fit in STEP_MS, then the frame draws the progress bar.
#
# Set against all four boards (2026-10-02, figures on #124): every one is
# bound by its store, not its WiFi -- the cards write slower than the radio
# reads, the P4s' flash stores many times slower -- so the knobs are about the
# store and the screen. The writes: on a card the session size does not move
# the rate; on a P4's flash 16 KB is slower than 64 KB and 128 KB is no faster
# while a single flush holds the screen twice as long. The step: a frame
# between steps costs under a tenth of an install at 120 ms, and 250 ms bought
# about one percent. A step is never shorter than the unit inside it -- a
# TLS connect, a 64 KB flush to flash -- which is what the screen waits on.
READ_CHUNK = 16384
WRITE_CHUNK = 65536
STEP_MS = 120

# The most one unit of the pipeline reads from the network at once: a zip
# member's local header with the longest name and extra field it can carry.
# A non-blocking transport is read only once this much (or the rest of the
# body and its end) is in hand, so no unit ever waits inside a step.
GATE = 4 + 26 + 65535 + 65535

# A ranged read's first piece: the end of the zip, sized for the central
# directory the index's file names make (`_cd_bytes`) and this much more for
# extra fields and a comment. A directory that begins before it is fetched
# again from where it begins.
TAIL_SLACK = 256

SAVES = "pmem.json"           # always carried across an update
CONFIG = "config.json"        # carried when the kid changed it

# What the kid reads when an install stops. The detail goes to serial.
UNREACHABLE = "Can't reach the cart shelf."
STOPPED = "The download stopped."
MISMATCH = "A file came back wrong."
PACKING = "This cart is packed in a way this console can't open."
FULL = "The store is full."
NO_WRITE = "Can't write to the store."
NO_LICENCE = "Its licence wasn't accepted."
NO_MEMORY = "Not enough memory free to unpack it."
NET_MEMORY = "Not enough memory free to download. Restart the console, then try again."

_MP = getattr(sys, "implementation", None) is not None \
    and sys.implementation.name == "micropython"


def _log(*a):
    try:
        print("Moybyte carts:", *a)
    except Exception:  # noqa: BLE001 -- a log line never stops an install
        pass


class InstallError(Exception):
    """`text` is what the kid reads; `detail` is what serial gets."""

    def __init__(self, text, detail=None):
        super().__init__(text)          # MicroPython has no Exception.__init__
        self.text = text
        self.detail = detail or text


def net_text(net, text):
    """`text` for a network failure, or NET_MEMORY when the transport says
    this console has no memory left for a connection (`out_of_memory`, a
    board transport's; the host's has none). A TLS read that cannot get the
    memory it needs fails with a bare errno, which reads as a dropped
    connection unless something asks."""
    starved = getattr(net, "out_of_memory", None)
    if starved is None:
        return text
    try:
        return NET_MEMORY if starved() else text
    except Exception:  # noqa: BLE001 -- the question never costs the answer
        return text


def _hex(digest):
    return binascii.hexlify(digest).decode()


# -- references -----------------------------------------------------------------

def is_url(ref):
    return isinstance(ref, str) and (ref.startswith("http://")
                                     or ref.startswith("https://"))


def resolve(base, ref):
    """`ref` (a URL, or a path relative to the index) against the index's URL."""
    if is_url(ref):
        return ref
    start = base.find("://") + 3
    if ref.startswith("/"):
        slash = base.find("/", start)
        return (base if slash < 0 else base[:slash]) + ref
    if ref.startswith("./"):
        ref = ref[2:]
    cut = base.rfind("/")
    return (base + "/" + ref) if cut < start else (base[:cut + 1] + ref)


def plain_name(name):
    """A file or folder name an index may make this console write: one path
    segment, no dot first (moy-spec's rule, so `..` and hidden files never)."""
    return (isinstance(name, str) and bool(name) and "/" not in name
            and "\\" not in name and "\0" not in name and not name.startswith("."))


def mirror_ok(ref):
    """An asset's `mirror` as moy-spec's check_index allows it: a path relative
    to the index that stays beside it."""
    if not isinstance(ref, str) or not ref or ref.startswith("/") or "\\" in ref \
            or ":" in ref:
        return False
    for seg in ref.split("/"):
        if seg in ("", ".", ".."):
            return False
    return True


def asset_urls(asset, cors=False):
    """Where `asset` is fetched from, in the order to try: its release `url`
    then its mirror -- or, for a transport that can only read what CORS lets
    it (`cors`), the mirror first."""
    m = asset.get("mirror")
    if not mirror_ok(m):
        return [asset["url"]]
    return [m, asset["url"]] if cors else [asset["url"], m]


def external_mirror(ext):
    """An external file's `mirror` -- its repository's own copy of the bare
    file, a path relative to the index -- or None when it names none (or one
    moy-spec's check_index would refuse)."""
    m = ext.get("mirror")
    return m if mirror_ok(m) else None


def external_sources(ext):
    """Where an external file can come from, in the order to try: its mirror,
    then its archive's hosts."""
    m = external_mirror(ext)
    return ([m] if m is not None else []) + list(ext["archive"]["urls"])


def _sha(s):
    if not isinstance(s, str) or len(s) != 64:
        return False
    for ch in s:
        if ch not in "0123456789abcdef":
            return False
    return True


def _sized(m):
    return (isinstance(m, dict) and isinstance(m.get("size"), int)
            and m["size"] >= 0 and _sha(m.get("sha256")))


def module_parts(fn):
    """`(stem, chip, format)` for a compiled module's file name
    (`main.esp32s3.f2.aot`), else None."""
    if not fn.endswith(".aot"):
        return None
    bits = fn[:-4].split(".")
    if len(bits) < 3 or not bits[-1].startswith("f") or not bits[-1][1:].isdigit():
        return None
    return (".".join(bits[:-2]), bits[-2], bits[-1][1:])


# -- the index ------------------------------------------------------------------

def check_cart(c):
    """None when `c` is an index entry this console can install, else why not."""
    if not isinstance(c, dict):
        return "not an object"
    for key in ("id", "name", "folder", "runtime"):
        if not isinstance(c.get(key), str) or not c[key]:
            return "no %s" % key
    if not isinstance(c.get("version"), int):
        return "no version"
    folder = c["folder"]
    if not plain_name(folder) or not folder.endswith(".moy"):
        return "folder %r" % folder
    lic = c.get("licence")
    if not _sized(lic) or not isinstance(lic.get("url"), str):
        return "licence"
    assets = c.get("assets")
    if not isinstance(assets, list) or not assets:
        return "no assets"
    names = {}
    for a in assets:
        if not _sized(a) or not isinstance(a.get("url"), str) \
                or not isinstance(a.get("files"), dict):
            return "asset"
        for fn, meta in a["files"].items():
            if not plain_name(fn) or not _sized(meta) or fn in names:
                return "file %r" % fn
            names[fn] = True
    for e in c.get("external") or ():
        if not _sized(e) or not plain_name(e.get("path")) or e["path"] in names:
            return "external"
        names[e["path"]] = True
        el = e.get("licence")
        arc = e.get("archive")
        if not _sized(el) or not isinstance(el.get("url"), str) or not _sized(arc) \
                or not isinstance(arc.get("urls"), list) or not arc["urls"] \
                or not isinstance(arc.get("member"), str):
            return "external %s" % e["path"]
    return None


def cover_ref(c):
    """The cover an index entry names -- {"url", "size", "sha256", "w", "h"},
    a cover.png of SPEC.md 3.6's profile (128 x 128, at most 64 KB) -- or
    None. A cover is the row's picture and nothing else: a missing or
    unreadable one is no cover, never a reason to leave the cart out."""
    ref = c.get("cover")
    if (_sized(ref) and isinstance(ref.get("url"), str)
            and 0 < ref["size"] <= COVER_MAX_BYTES
            and ref.get("w") == 128 and ref.get("h") == 128):
        return ref
    return None


def parse_index(data, url):
    """The carts in an index document, each stamped with the index it came
    from (`index`) and that index's name (`shelf`). An entry this console
    cannot read is left out and logged, and so is a `cover` it cannot (the
    cart stays); a document that is not an index is an InstallError."""
    try:
        index = json.loads(data.decode() if isinstance(data, (bytes, bytearray)) else data)
    except (ValueError, UnicodeError) as exc:
        raise InstallError(UNREACHABLE, "%s is not JSON: %s" % (url, exc))
    if not isinstance(index, dict) or index.get("version") != INDEX_VERSION:
        raise InstallError(UNREACHABLE, "%s is not a version %d index"
                           % (url, INDEX_VERSION))
    out = []
    for c in index.get("carts") or ():
        why = check_cart(c)
        if why is not None:
            _log("%s: left out %r (%s)" % (url, c.get("id") if isinstance(c, dict)
                                           else c, why))
            continue
        c = dict(c)
        if "cover" in c and cover_ref(c) is None:
            _log("%s: %r's cover left out" % (url, c["id"]))
            del c["cover"]
        assets = []
        for a in c["assets"]:
            if "mirror" in a and not mirror_ok(a["mirror"]):
                _log("%s: %r's mirror %r left out" % (url, c["id"], a["mirror"]))
                a = dict(a)
                del a["mirror"]
            assets.append(a)
        c["assets"] = assets
        exts = []
        for e in c.get("external") or ():
            if "mirror" in e and external_mirror(e) is None:
                _log("%s: %r's %s mirror %r left out" % (url, c["id"], e["path"],
                                                          e["mirror"]))
                e = dict(e)
                del e["mirror"]
            exts.append(e)
        if "external" in c:
            c["external"] = exts
        c["index"] = url
        c["shelf"] = str(index.get("name") or "")
        out.append(c)
    return out


def load_indexes(root):
    """The indexes this console browses: `indexes.json` beside the carts
    folder when it names any, else DEFAULT_INDEXES."""
    def pick(d):
        got = d.get("indexes") if isinstance(d, dict) else None
        if not isinstance(got, list):
            return None
        urls = [u for u in got if is_url(u)]
        return urls or None
    try:
        got = _store._load_store_json(_sibling_path(root, INDEXES_NAME), pick)
    except Exception:  # noqa: BLE001 -- an unreadable override is no override
        got = None
    return list(got) if got else list(DEFAULT_INDEXES)


# -- what this console takes ------------------------------------------------------

def _cd_bytes(cart, asset):
    """The central directory and end record a zip of `asset`'s files takes
    with no extra fields or comments."""
    n = 22
    for fn in asset["files"]:
        n += 46 + len(cart["folder"]) + 1 + len(fn)
    return n


def _tail_bytes(cart, asset):
    return min(asset["size"], _cd_bytes(cart, asset) + TAIL_SLACK)


def _ranged_bytes(cart, asset, keep):
    """What a ranged read of `asset` fetches when it keeps `keep`: its tail
    and each kept member with its local header (30 bytes and the name, more
    when a member carries an extra field)."""
    n = _tail_bytes(cart, asset)
    for fn in keep:
        n += 30 + len(cart["folder"]) + 1 + len(fn) + asset["files"][fn]["size"]
    return n


def _kept(asset, files):
    """The members of `asset` a console keeps, when it keeps some and not
    all; else None, and the asset is best read whole."""
    keep = [fn for fn in asset["files"] if fn in files]
    return keep if 0 < len(keep) < len(asset["files"]) else None


def plan(cart, chip=None, fmt=None, ranges=False):
    """What installing `cart` means on a console of `chip` running compiled-code
    format `fmt` (both None where no compiled tier exists, the host), whose
    transport can (`ranges`) read part of a file or cannot:

      files           {name: {"size", "sha256"}} -- everything written
      module          this console's module's name, or None
      slow            a compiled cart with no module for a console that has a
                      compiled tier -- it plays on the interpreter
      external        the cart's external files (each needs its licence accepted)
      store_bytes     what the written files add up to
      download_bytes  what is fetched: whole assets, or with `ranges` the pieces
                      of one this console keeps only some of; an external file
                      from its mirror, else its whole archive
      load_bytes      the file a compiled cart's load reads -- this console's
                      module, or main.wasm when it plays on the interpreter --
                      which the fit check sizes it by; None for other runtimes
    """
    files = {}
    module = None
    fmt = None if fmt is None else str(fmt)
    for a in cart["assets"]:
        for fn, meta in a["files"].items():
            parts = module_parts(fn)
            if parts is None:
                files[fn] = meta
            elif chip and parts[1] == chip and parts[2] == fmt:
                files[fn] = meta
                module = fn
    ext = list(cart.get("external") or ())
    for e in ext:
        files[e["path"]] = {"size": e["size"], "sha256": e["sha256"]}
    store = 0
    for meta in files.values():
        store += meta["size"]
    down = 0
    for a in cart["assets"]:
        keep = _kept(a, files) if ranges else None
        down += a["size"] if keep is None else _ranged_bytes(cart, a, keep)
    for e in ext:
        down += e["size"] if external_mirror(e) is not None else e["archive"]["size"]
    load = None
    if cart["runtime"] == "wasm":
        if module is not None:
            load = files[module]["size"]
        else:
            stem = "main"
            for a in cart["assets"]:
                for fn in a["files"]:
                    parts = module_parts(fn)
                    if parts is not None:
                        stem = parts[0]
            main = files.get(stem + ".wasm")
            load = main["size"] if main is not None else None
    return {"files": files, "module": module, "external": ext, "load_bytes": load,
            "slow": cart["runtime"] == "wasm" and bool(chip) and module is None,
            "store_bytes": store, "download_bytes": down}


def need_bytes(p, block):
    """Store space `p` takes, each file rounded up to the store's block (a FAT
    cluster on a card, a littlefs block on flash), plus the folder itself."""
    block = max(1, int(block))
    n = 2 * block
    for meta in p["files"].values():
        n += (meta["size"] + block - 1) // block * block
    return n


# -- the record -------------------------------------------------------------------

def load_record(root):
    """{folder: entry} for every cart this store installed."""
    def pick(d):
        carts = d.get("carts") if isinstance(d, dict) else None
        return carts if isinstance(carts, dict) else None
    try:
        got = _store._load_store_json(_sibling_path(root, RECORD_NAME), pick)
    except Exception:  # noqa: BLE001 -- no record reads as nothing installed
        got = None
    return dict(got) if got else {}


def record_text(rec):
    return json.dumps({"version": 1, "carts": rec})


def save_record(root, rec):
    _store._write_sibling(root, RECORD_NAME, record_text(rec))


def record_entry(cart, p):
    return {"id": cart["id"], "index": cart.get("index", ""), "name": cart["name"],
            "version": cart["version"], "module": p["module"],
            "files": dict((fn, meta["sha256"]) for fn, meta in p["files"].items())}


def cart_state(cart, p, entry, present, manifest=None):
    """Where `cart` stands on this console:

      "get"        not here
      "installed"  here, and what the index lists for this console
      "update"     here, and the index has something newer or different
      "taken"      a different cart already has its folder name

    `entry` is the record's entry for the cart's folder (or None), `present`
    whether that folder exists, `manifest` its parsed manifest.json when there
    is no record to go by."""
    if not present:
        return "get"
    if entry is not None and entry.get("id") == cart["id"]:
        if int(entry.get("version") or 0) < cart["version"]:
            return "update"
        had = entry.get("files") or {}
        want = p["files"]
        if len(had) != len(want):
            return "update"
        for fn, meta in want.items():
            if had.get(fn) != meta["sha256"]:
                return "update"
        return "installed"
    if isinstance(manifest, dict) and manifest.get("title") == cart["name"] \
            and manifest.get("runtime", "python") == cart["runtime"]:
        return "update"
    return "taken"


def stage_root(root):
    return _sibling_path(root, STAGE_DIR)


def recover(root):
    """Finish what an interrupted install or removal left in `install/`: an
    unfinished build goes, an old copy goes once its replacement is in place
    and comes back when it is not, a removed cart goes. Call inside one store
    session. Returns how many entries it handled."""
    base = stage_root(root)
    try:
        names = os.listdir(base)
    except OSError:
        return 0
    for n in names:
        p = base + "/" + n
        if n.endswith(".old") and not _exists(root + "/" + n[:-4]):
            os.rename(p, root + "/" + n[:-4])
            _log("put back %s" % n[:-4])
        elif _is_dir(p):
            _rmtree(p)
        else:
            os.remove(p)
    return len(names)


def remove(root, folder, keep=None):
    """Take `folder` off the shelf: one rename out of the carts folder (so a
    crash mid-delete never leaves half a cart listed), then the delete, then
    the record. Call inside one store session.

    With a keeper the record without the cart goes to the store of record
    FIRST, ahead of the folder's deletion: a record that outlives its folder
    would one day offer an UPDATE over a kid's own cart of the same name,
    while a folder that outlives its record is only offered again."""
    rec = load_record(root)
    had = rec.pop(folder, None) is not None
    if keep is not None and had:
        keep.record(record_text(rec))
    base = stage_root(root)
    _mkdir(base)
    gone = base + "/" + folder + ".gone"
    if _exists(gone):
        _rmtree(gone)
    os.rename(root + "/" + folder, gone)
    _rmtree(gone)
    if had:
        save_record(root, rec)


# -- fetching -------------------------------------------------------------------

class Fetch:
    """One small document whole -- an index, a licence text, a cover -- at
    most `limit` bytes and, given `size`/`sha`, exactly those. `step()` is True
    while the answer is still on its way (only a non-blocking transport ever
    says so) and raises an InstallError when it cannot be had; then `data`
    holds the bytes."""

    def __init__(self, net, url, limit, size=None, sha=None):
        self.net = net
        self.url = url
        self.size = size
        self.sha = sha
        self.limit = limit if size is None else size
        self.data = None
        self._out = bytearray()
        self._buf = bytearray(4096)
        try:
            self._resp = net.open(url)
        except Exception as exc:  # noqa: BLE001 -- every transport error is "unreachable"
            raise InstallError(net_text(net, UNREACHABLE), "%s: %s" % (url, exc))

    def step(self):
        resp = self._resp
        if resp is None:
            return False
        try:
            if resp.status is None:
                return True
            if resp.status != 200:
                raise InstallError(UNREACHABLE, "%s: HTTP %d" % (self.url, resp.status))
            ready = getattr(resp, "ready", None)
            mv = memoryview(self._buf)
            while True:
                if ready is not None and not ready(1):
                    return True
                try:
                    n = resp.readinto(mv)
                except Exception as exc:  # noqa: BLE001
                    raise InstallError(net_text(self.net, STOPPED), "%s: %s"
                                       % (self.url, exc))
                if not n:
                    break
                self._out.extend(mv[:n])
                if len(self._out) > self.limit:
                    raise InstallError(MISMATCH, "%s is larger than %d bytes"
                                       % (self.url, self.limit))
        except InstallError:
            self.close()
            raise
        self.close()
        out, self._out = self._out, None
        if self.size is not None and len(out) != self.size:
            raise InstallError(MISMATCH, "%s is %d bytes, the index says %d"
                               % (self.url, len(out), self.size))
        if self.sha is not None:
            got = _hex(hashlib.sha256(out).digest())
            if got != self.sha:
                raise InstallError(MISMATCH, "%s hashes to %s, the index says %s"
                                   % (self.url, got, self.sha))
        self.data = bytes(out)
        return False

    def close(self):
        resp, self._resp = self._resp, None
        if resp is not None:
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass


def index_fetch(net, url):
    return Fetch(net, url, INDEX_LIMIT)


def cover_fetch(net, cart):
    """The cover.png `cart`'s index entry names (`cover_ref`), to be checked
    against its size and sha256."""
    ref = cart["cover"]
    return Fetch(net, resolve(cart["index"], ref["url"]), COVER_MAX_BYTES,
                 ref["size"], ref["sha256"])


def licence_fetch(net, cart, ref):
    """A licence the index names (`ref`: {"url", "size", "sha256", "name"}),
    to be checked; `as_text` reads what it fetched."""
    return Fetch(net, resolve(cart["index"], ref["url"]), TEXT_LIMIT,
                 ref["size"], ref["sha256"])


def as_text(data):
    try:
        return data.decode()
    except UnicodeError:
        return "".join(chr(b) if b < 128 else "?" for b in data)


class Reach:
    """Which of `urls` this console can read: the first whose answer comes
    back 200, the body never read. `step()` is True while an answer is on its
    way; then `url` is the one that answered, or None. Asked only by a
    console whose transport cannot read every host (the browser's), about an
    external file's archive, before it offers the player a file of their
    own."""

    def __init__(self, net, urls):
        self.net = net
        self.urls = list(urls)
        self.url = None
        self.why = []
        self._resp = None
        self._at = None

    def step(self):
        while True:
            resp = self._resp
            if resp is None:
                if not self.urls:
                    return False
                self._at = self.urls.pop(0)
                try:
                    resp = self._resp = self.net.open(self._at)
                except Exception as exc:  # noqa: BLE001 -- try the next one
                    self.why.append("%s: %s" % (self._at, exc))
                    continue
            if resp.status is None:
                return True
            self.close()
            if resp.status == 200:
                self.url = self._at
                self.urls = []
                return False
            self.why.append("%s: HTTP %d" % (self._at, resp.status))

    def close(self):
        resp, self._resp = self._resp, None
        if resp is not None:
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass


class FileCheck:
    """A file the player supplied (`path`), held to the index's `size` and
    `sha` a slice per `step()`, so a 4 MB file never stops a frame. `step()`
    is True while more remains; then `why` is None when it is the file, or
    what is wrong with it in the player's words."""

    CHUNK = 65536

    def __init__(self, path, size, sha):
        self.path = path
        self.size = size
        self.sha = sha
        self.why = None
        self.done = 0
        self._h = hashlib.sha256()
        self._buf = bytearray(self.CHUNK)
        self._f = None

    def step(self, budget_ms=STEP_MS):
        t = _ticks_ms()
        try:
            if self._f is None:
                self._f = open(self.path, "rb")
            mv = memoryview(self._buf)
            while True:
                n = self._f.readinto(mv)
                if not n:
                    break
                self.done += n
                if self.done > self.size:
                    return self._end("it is larger than the %d bytes it should be"
                                     % self.size)
                self._h.update(mv[:n])
                if _ticks_diff(_ticks_ms(), t) >= budget_ms:
                    return True
        except OSError as exc:
            return self._end("it could not be read (%s)" % exc)
        if self.done != self.size:
            return self._end("it is %d bytes, and the right one is %d"
                             % (self.done, self.size))
        if _hex(self._h.digest()) != self.sha:
            return self._end("its bytes are not the ones the cart was made with")
        return self._end(None)

    def _end(self, why):
        self.why = why
        self.close()
        return False

    def close(self):
        f, self._f = self._f, None
        if f is not None:
            try:
                f.close()
            except Exception:  # noqa: BLE001
                pass


# -- the streaming install --------------------------------------------------------

class _Fetched:
    """One download, checked as it streams: at most `size` bytes, whose sha256
    must be `sha` once they are all in. Counts the job's progress and the time
    it spends waiting on the network."""

    def __init__(self, job, resp, size, sha, what):
        self.job = job
        self.resp = resp
        self.size = size
        self.left = size
        self.sha = sha
        self.what = what
        self.h = hashlib.sha256() if sha is not None else None
        self.checked = False

    def readinto(self, buf):
        if self.left <= 0:
            return 0
        mv = memoryview(buf)
        if len(mv) > self.left:
            mv = mv[:self.left]
        job = self.job
        t = _ticks_ms()
        try:
            n = self.resp.readinto(mv)
        except Exception as exc:  # noqa: BLE001 -- a dropped socket is a stopped download
            raise InstallError(net_text(job.net, STOPPED), "%s: %s at %d of %d bytes"
                               % (self.what, exc, self.size - self.left, self.size))
        job.t_net += _ticks_diff(_ticks_ms(), t)
        if not n:
            raise InstallError(STOPPED, "%s ended at %d of %d bytes"
                               % (self.what, self.size - self.left, self.size))
        if self.h is not None:
            self.h.update(mv[:n])
        self.left -= n
        job.done += n
        return n

    def ready(self):
        """True when the next unit can read what it needs without waiting --
        always, from a blocking transport; from a non-blocking one, once GATE
        bytes are in hand, or the rest of the body and its end."""
        if self.checked:
            return True
        r = getattr(self.resp, "ready", None)
        if r is None:
            return True
        return r(self.left + 1 if self.left < GATE else GATE)

    def drain(self):
        """Read what is left, hashing it, without keeping it."""
        while self.left > 0:
            self.readinto(self.job.scratch)

    def finish(self):
        """The whole download is in: nothing may follow it, and it must hash
        to what the index says."""
        if self.checked:
            return
        self.drain()
        try:
            extra = self.resp.readinto(memoryview(self.job.scratch)[:1])
        except Exception:  # noqa: BLE001 -- a closed socket after the body is the normal end
            extra = 0
        if extra:
            raise InstallError(MISMATCH, "%s is larger than the index says (%d bytes)"
                               % (self.what, self.size))
        if self.h is None:              # a ranged piece: its members are checked
            self.checked = True
            return
        got = _hex(self.h.digest())
        if got != self.sha:
            raise InstallError(MISMATCH, "%s hashes to %s, the index says %s"
                               % (self.what, got, self.sha))
        self.checked = True

    def close(self):
        try:
            self.resp.close()
        except Exception:  # noqa: BLE001
            pass


def _exact(src, n, job):
    """Exactly `n` bytes from `src`, or an InstallError (a short archive)."""
    out = bytearray(n)
    mv = memoryview(out)
    got = 0
    while got < n:
        k = src.readinto(mv[got:])
        if not k:
            raise InstallError(PACKING, "an archive ended inside a header")
        got += k
    return out


def _skip(src, n, job):
    mv = job.scratch_mv
    while n > 0:
        k = src.readinto(mv[:min(n, len(mv))])
        if not k:
            raise InstallError(PACKING, "an archive ended inside a member")
        n -= k


def _u16(b, i):
    return b[i] | (b[i + 1] << 8)


def _u32(b, i):
    return b[i] | (b[i + 1] << 8) | (b[i + 2] << 16) | (b[i + 3] << 24)


class _Sink:
    """The staging folder's writer. Network bytes are read straight into `buf`;
    a full buffer, or the end of a file, goes to the store in ONE session, and
    the file stays open between sessions the way the OTA download's does."""

    def __init__(self, job, size):
        self.job = job
        self.buf = bytearray(size)
        self.mv = memoryview(self.buf)
        self.fill = 0
        self.name = None
        self.cart_file = True
        self.path = None
        self.f = None
        self.size = 0

    def begin(self, name, path=None):
        """Start the cart's file `name`, or with `path` a file outside the
        cart (an external file's archive), which `end` does not count."""
        self.name = name
        self.cart_file = path is None
        self.path = path or self.job.stage + "/" + name
        self.fill = 0
        self.size = 0
        self.f = None

    def space(self, want):
        """A view to read up to `want` bytes into."""
        if self.fill == len(self.buf):
            self._flush(False)
        n = len(self.buf) - self.fill
        if n > want:
            n = want
        if n > READ_CHUNK:
            n = READ_CHUNK
        return self.mv[self.fill:self.fill + n]

    def wrote(self, n):
        self.fill += n
        self.size += n

    def end(self):
        self._flush(True)
        if self.cart_file:
            self.job.written[self.name] = self.size
        self.name = None

    def _flush(self, close):
        path = self.path
        data = self.mv[:self.fill]

        def _io():
            if self.f is None:
                self.f = open(path, "wb")
            if len(data):
                self.f.write(data)
            if close:
                f, self.f = self.f, None
                f.close()
        self.job.in_store(_io)
        self.fill = 0

    def abort(self):
        f, self.f = self.f, None
        if f is not None:
            try:
                f.close()
            except Exception:  # noqa: BLE001
                pass


class _ZipReader:
    """A STORED zip, member by member, as it streams past. The members this
    console keeps go to the sink, each hashed against the index; the rest
    (another chip's module) are read and dropped. The central directory at
    the end is read for the asset's hash and otherwise ignored: the local
    headers already said everything, and the asset's sha256 vouches for all of
    it."""

    def __init__(self, job, src, cart, asset, seen=None, run=False):
        self.job = job
        self.src = src
        self.prefix = cart["folder"] + "/"
        self.files = asset["files"]
        self.seen = {} if seen is None else seen
        self.run = run            # a ranged read's run: members, then its end
        self.member = None        # [name, bytes left, sha256 or None, data descriptor]
        self.tail = False

    def unit(self):
        """One bounded piece of work. False once the asset (or the run) is
        done."""
        if self.member is not None:
            return self._data()
        if self.run and not self.src.left:
            return False
        if self.tail:
            if self.src.left:
                self.src.readinto(self.job.scratch)
                return True
            return False
        sig = _exact(self.src, 4, self.job)
        if sig == b"PK\x03\x04":
            self._local()
            return True
        if sig in (b"PK\x01\x02", b"PK\x05\x06") and not self.run:
            missing = [fn for fn in self.files if fn not in self.seen]
            if missing:
                raise InstallError(MISMATCH, "the zip has no %s" % ", ".join(missing))
            self.tail = True
            return True
        raise InstallError(PACKING, "unexpected zip record %r" % bytes(sig))

    def _local(self):
        h = _exact(self.src, 26, self.job)
        flags, method = _u16(h, 2), _u16(h, 4)
        csize, usize = _u32(h, 14), _u32(h, 18)
        name = bytes(_exact(self.src, _u16(h, 22), self.job)).decode()
        if _u16(h, 24):
            _skip(self.src, _u16(h, 24), self.job)
        if method != 0:
            raise InstallError(PACKING, "%s is compressed (method %d)" % (name, method))
        fn = name[len(self.prefix):] if name.startswith(self.prefix) else None
        meta = self.files.get(fn) if fn is not None else None
        if meta is None or fn in self.seen:
            raise InstallError(MISMATCH, "the zip holds %s, which the index does "
                                         "not list (or lists once)" % name)
        size = meta["size"]
        if not (flags & 8) and (csize != size or usize != size):
            raise InstallError(MISMATCH, "%s is %d bytes, the index says %d"
                               % (name, usize, size))
        self.seen[fn] = True
        keep = fn in self.job.plan["files"]
        if keep:
            self.job.sink.begin(fn)
        self.member = [fn, size, hashlib.sha256() if keep else None, bool(flags & 8)]

    def _data(self):
        m = self.member
        left = m[1]
        if left:
            if m[2] is not None:
                mv = self.job.sink.space(left)
                n = self.src.readinto(mv)
                m[2].update(mv[:n])
                self.job.sink.wrote(n)
            else:
                mv = self.job.scratch_mv
                n = self.src.readinto(mv[:min(left, len(mv))])
            m[1] = left - n
            if m[1]:
                return True
        if m[2] is not None:
            self.job.sink.end()
            got = _hex(m[2].digest())
            if got != self.files[m[0]]["sha256"]:
                raise InstallError(MISMATCH, "%s hashes to %s, the index says %s"
                                   % (m[0], got, self.files[m[0]]["sha256"]))
        if m[3]:                       # a data descriptor, signature optional
            d = _exact(self.src, 4, self.job)
            _skip(self.src, 12 if bytes(d) == b"PK\x07\x08" else 8, self.job)
        self.member = None
        return True


# The most of a zip's end its end record can be from: the record and the
# longest comment.
ZIP_END_MAX = 22 + 65535


def _zip_end(tail):
    """Where the zip's end record starts in `tail` (the end of the file), or
    -1: the last signature whose comment runs exactly to the end."""
    i = len(tail) - 22
    while i >= 0:
        if tail[i] == 0x50 and tail[i + 1] == 0x4B and tail[i + 2] == 5 \
                and tail[i + 3] == 6 and i + 22 + _u16(tail, i + 20) == len(tail):
            return i
        i -= 1
    return -1


class _Ranged:
    """A release asset read in pieces (the module docstring's `ranges`): the
    end of its zip, whose central directory says where every member starts,
    then each run of members this console keeps, read as a stored zip's
    members are (`_ZipReader`), each checked against the index. It is the
    job's source (`ready`, `finish`, `close`) and its reader (`unit`) at
    once. It asks the asset's first URL (the mirror, on a console with
    `cors`); an answer to its first range that is not 206 -- the whole file,
    a refusal, no answer -- sends the asset back to the queue, to be read
    whole from every URL the way a console without `ranges` reads it."""

    def __init__(self, job, url, cart, asset, keep):
        self.job = job
        self.url = resolve(cart["index"], url)
        self.cart = cart
        self.asset = asset
        self.size = asset["size"]
        self.keep = keep
        self.what = asset["name"]
        self.counted = _ranged_bytes(cart, asset, keep)   # its share of job.total
        self.tail_n = _tail_bytes(cart, asset)
        self.spent = 0            # the end's bytes read so far
        self.resp = None          # the answer awaited, or being read
        self.need = 0             # what that answer must have in hand
        self.runs = None          # [(start, end)] once the directory is read
        self.src = None           # a run's bytes
        self.zip = None           # and its members
        self.seen = {}
        self.whole = False        # the server sent the file: read it whole
        self._open(self.size - self.tail_n, self.tail_n)

    def _open(self, start, n):
        """Ask for `n` bytes from `start`."""
        job = self.job
        t = _ticks_ms()
        try:
            self.resp = job.net.open(self.url, span=(start, n))
        except Exception as exc:  # noqa: BLE001 -- a dropped request is a stopped one
            raise InstallError(net_text(job.net, STOPPED), "%s: %s" % (self.url, exc))
        finally:
            job.t_net += _ticks_diff(_ticks_ms(), t)
        self.need = n

    def _count(self, n):
        """The read fetches `n` bytes in all, as far as it now knows."""
        self.job.total += n - self.counted
        self.counted = n

    def ready(self):
        if self.src is not None:
            return self.src.ready()
        resp = self.resp
        if resp is None or resp.status is None:
            return resp is None
        if resp.status != 206:
            return True
        r = getattr(resp, "ready", None)
        return r is None or r(self.need)

    def unit(self):
        if self.zip is not None:
            if self.zip.unit():
                return True
            self.src.finish()
            self.src.close()
            self.src = self.zip = None
            return True
        resp = self.resp
        if resp is not None:
            return self._answered(resp)
        if not self.runs:
            return False
        start, end = self.runs.pop(0)
        self._open(start, end - start)
        return True

    def _answered(self, resp):
        """The head of the answer in hand is in: read on, or decide."""
        if resp.status != 206 and self.runs is None:
            _log("%s: no range from %s (HTTP %s), reading it whole"
                 % (self.what, self.url, resp.status))
            self.close()
            self.whole = True
            self.job.whole.append(self.asset)
            self.job._queue.insert(0, ("asset", self.asset))
            self._count(self.spent + self.size)
            return False
        if resp.status != 206:
            self.close()
            raise InstallError(STOPPED, "%s: HTTP %d for a range of %s"
                               % (self.url, resp.status, self.what))
        if self.runs is not None:
            self.resp = None
            self.src = _Fetched(self.job, resp, self.need, None, self.what)
            self.zip = _ZipReader(self.job, self.src, self.cart, self.asset,
                                  self.seen, run=True)
            return True
        tail = bytes(_exact(_Fetched(self.job, resp, self.need, None, self.what),
                            self.need, self.job))
        self.spent += len(tail)
        self.close()
        self._directory(tail)
        return True

    def _directory(self, tail):
        """The runs to read, from the zip's end in `tail`."""
        i = _zip_end(tail)
        if i < 0:
            whole = min(self.size, ZIP_END_MAX)
            if len(tail) >= whole:
                raise InstallError(PACKING, "%s has no zip end record" % self.what)
            # A comment longer than the slack: ask for all the end could be.
            self._count(self.counted + whole)
            self._open(self.size - whole, whole)
            return
        count, cd_off = _u16(tail, i + 10), _u32(tail, i + 16)
        base = self.size - len(tail)
        if cd_off < base:
            # A directory longer than the index's names make: fetch it whole.
            self._count(self.counted + self.size - cd_off)
            self._open(cd_off, self.size - cd_off)
            return
        prefix = self.cart["folder"] + "/"
        names = {}
        j = cd_off - base
        for _ in range(count):
            if j + 46 > len(tail) or bytes(tail[j:j + 4]) != b"PK\x01\x02":
                raise InstallError(PACKING, "%s's zip directory is malformed" % self.what)
            n, x, c = _u16(tail, j + 28), _u16(tail, j + 30), _u16(tail, j + 32)
            name = bytes(tail[j + 46:j + 46 + n]).decode()
            fn = name[len(prefix):] if name.startswith(prefix) else None
            if fn not in self.asset["files"] or fn in names:
                raise InstallError(MISMATCH, "the zip holds %s, which the index does "
                                             "not list (or lists once)" % name)
            names[fn] = _u32(tail, j + 42)
            j += 46 + n + x + c
        missing = [fn for fn in self.asset["files"] if fn not in names]
        if missing:
            raise InstallError(MISMATCH, "the zip has no %s" % ", ".join(missing))
        order = sorted((off, fn) for fn, off in names.items())
        runs = []
        for k, (off, fn) in enumerate(order):
            end = order[k + 1][0] if k + 1 < len(order) else cd_off
            if fn in self.keep:
                if runs and runs[-1][1] == off:
                    runs[-1] = (runs[-1][0], end)
                else:
                    runs.append((off, end))
        fetched = self.spent
        for start, end in runs:
            fetched += end - start
        self._count(fetched)
        self.runs = runs

    def finish(self):
        if self.whole:
            return
        missing = [fn for fn in self.keep if fn not in self.seen]
        if missing:
            raise InstallError(MISMATCH, "the zip has no %s" % ", ".join(missing))

    def close(self):
        resp, self.resp = self.resp, None
        if resp is not None:
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass
        src, self.src = self.src, None
        if src is not None:
            src.close()


class _ZlibStream:
    """CPython's stand-in for MicroPython's `deflate.DeflateIO(src, GZIP)`:
    the same pull-shaped `readinto`, over zlib."""

    def __init__(self, src):
        try:
            import zlib
        except ImportError:
            zlib = None
        if zlib is None:
            raise InstallError(PACKING, "no inflater: neither deflate nor zlib")
        self.src = src
        self.z = zlib.decompressobj(31)
        self.raw = bytearray(READ_CHUNK)
        self.pending = b""

    def readinto(self, buf):
        mv = memoryview(buf)
        while True:
            if self.z.eof:
                return 0
            if not self.pending:
                n = self.src.readinto(self.raw)
                if not n:
                    raise InstallError(PACKING, "the archive's gzip stream is cut short")
                self.pending = bytes(self.raw[:n])
            out = self.z.decompress(self.pending, len(mv))
            self.pending = self.z.unconsumed_tail
            if out:
                mv[:len(out)] = out
                return len(out)


def _gunzip(src):
    try:
        import deflate
    except ImportError:
        deflate = None
    if deflate is None:
        return _ZlibStream(src)
    return deflate.DeflateIO(src, deflate.GZIP)


def _cstr(b):
    b = bytes(b)
    cut = b.find(b"\0")
    return (b if cut < 0 else b[:cut]).decode()


class _TarGzReader:
    """An external file's archive, in two halves. FETCH: the archive's bytes
    are kept whole as they arrive -- in RAM, or (`job.archive == "store"`) in
    a file beside the build -- hashed against the index, and the hash is
    settled before anything is inflated, so the inflater only ever sees bytes
    the index vouched for. INFLATE: the tar inside is walked from those bytes
    and the one member the index names is written as the external's path,
    hashed against its own sha256.

    Why whole and not off the socket: MicroPython's `deflate.DeflateIO` pulls
    its source ONE BYTE per call, and through a Python-level stream that is a
    method call per compressed byte (1.76 million for Doom's WAD). Over a
    native stream -- `io.BytesIO`, or an open file -- the same inflate is a C
    loop: 84ms against 1.5s for the whole WAD on the desktop build.

    RAM or a file is the console's call (`Install`'s `archive`). In RAM,
    MicroPython's `BytesIO(n)` preallocates without a copy, so the archive
    costs its own size once -- but on a board whose Python heap grows and
    never shrinks, that size stays out of what a compiled cart can load into
    until a reboot. In a file it costs a write and a read of its size."""

    def __init__(self, job, src, ext):
        self.job = job
        self.src = src
        self.ext = ext
        self.want = ext["archive"]["member"]
        self.path = None
        self.buf = None
        if job.archive == "store":
            self.path = stage_root(job.root) + "/" + job.folder + ".archive"
            job.sink.begin(None, self.path)
        else:
            self.buf = _io.BytesIO(ext["archive"]["size"]) if _MP else _io.BytesIO()
        self.f = None
        self.gz = None
        self.member = None        # [bytes left, sha256 or None, padding]
        self.long = None
        self.found = False

    def unit(self):
        if self.gz is None:
            return self._fetch()
        if self.f is None:
            return self._step()
        return self.job.in_store(self._step)

    def _step(self):
        if self.member is not None:
            return self._data()
        hdr = bytearray(512)
        got = 0
        while got < 512:
            k = self.gz.readinto(memoryview(hdr)[got:])
            if not k:
                break
            got += k
        if got == 0 or not any(hdr):
            return False              # end of the archive
        if got < 512:
            raise InstallError(PACKING, "an archive ended inside a header")
        name = _cstr(hdr[0:100])
        if bytes(hdr[257:262]) == b"ustar" and hdr[345]:
            name = _cstr(hdr[345:500]) + "/" + name
        if self.long is not None:
            name, self.long = self.long, None
        try:
            size = int(_cstr(hdr[124:136]).strip() or "0", 8)
        except ValueError:
            raise InstallError(PACKING, "a tar header with no size")
        pad = (512 - size % 512) % 512
        kind = hdr[156]
        if kind in (ord("L"), ord("x")):
            body = bytes(_exact(self.gz, size, self.job)) if size else b""
            _skip(self.gz, pad, self.job)
            if kind == ord("L"):
                self.long = _cstr(body)
            else:
                for line in body.split(b"\n"):
                    k = line.find(b" path=")
                    if k > 0:
                        self.long = line[k + 6:].decode()
            return True
        keep = (kind in (0, ord("0"))) and name == self.want and not self.found
        if keep:
            if size != self.ext["size"]:
                raise InstallError(MISMATCH, "%s is %d bytes, the index says %d"
                                   % (name, size, self.ext["size"]))
            self.found = True
            self.job.sink.begin(self.ext["path"])
        self.member = [size, hashlib.sha256() if keep else None, pad]
        return True

    def _fetch(self):
        if self.buf is None:
            sink = self.job.sink
            mv = sink.space(self.src.left)
            sink.wrote(self.src.readinto(mv))
        else:
            mv = self.job.scratch_mv
            n = self.src.readinto(mv)
            self.buf.write(mv[:n])
        if self.src.left:
            return True
        self.src.finish()
        self.src.close()
        if self.buf is None:
            self.job.sink.end()
            self.f = self.job.in_store(lambda: open(self.path, "rb"))
            self.gz = _gunzip(self.f)
        else:
            self.buf.seek(0)
            self.gz = _gunzip(self.buf)
        return True

    def _data(self):
        m = self.member
        if m[0]:
            if m[1] is not None:
                mv = self.job.sink.space(m[0])
                n = self.gz.readinto(mv)
                if n:
                    m[1].update(mv[:n])
                    self.job.sink.wrote(n)
                    self.job.done += n
            else:
                mv = self.job.scratch_mv
                n = self.gz.readinto(mv[:min(m[0], len(mv))])
            if not n:
                raise InstallError(PACKING, "the archive ended inside a member")
            m[0] -= n
            if m[0]:
                return True
        if m[1] is not None:
            self.job.sink.end()
            got = _hex(m[1].digest())
            if got != self.ext["sha256"]:
                raise InstallError(MISMATCH, "%s hashes to %s, the index says %s"
                                   % (self.ext["path"], got, self.ext["sha256"]))
        if m[2]:
            _skip(self.gz, m[2], self.job)
        self.member = None
        return True

    def check(self):
        if not self.found:
            raise InstallError(MISMATCH, "the archive has no %s" % self.want)
        self.close()

    def close(self):
        """Let the archive go: the buffer, or the file and its bytes."""
        self.buf = None
        self.gz = None
        f, self.f = self.f, None
        path, self.path = self.path, None
        if f is None and path is None:
            return

        def _drop():
            if f is not None:
                f.close()
            if path is not None and _exists(path):
                os.remove(path)
        self.job.in_store(_drop)


class _Bare:
    """An external file from its mirror: the file itself, written as it
    streams. Its hash is the download's own, which `_Fetched.finish` checks
    before the build can commit."""

    def __init__(self, job, src, ext):
        self.job = job
        self.src = src
        self.left = ext["size"]
        job.sink.begin(ext["path"])

    def unit(self):
        sink = self.job.sink
        if self.left:
            mv = sink.space(self.left)
            n = self.src.readinto(mv)
            sink.wrote(n)
            self.left -= n
            if self.left:
                return True
        sink.end()
        return False


class _Local:
    """An external file the player supplied, where this console could not
    fetch it from its archive's hosts (the browser): copied from where it was
    put into the build and held to the index's size and sha256 on the way,
    exactly as the file out of a downloaded archive is."""

    def __init__(self, job, path, ext):
        self.job = job
        self.ext = ext
        self.left = ext["size"]
        self.h = hashlib.sha256()
        self.f = job.in_store(lambda: open(path, "rb"))
        job.sink.begin(ext["path"])

    def unit(self):
        return self.job.in_store(self._unit)

    def _unit(self):
        job = self.job
        name = self.ext["path"]
        if self.left:
            mv = job.sink.space(self.left)
            n = self.f.readinto(mv)
            if not n:
                raise InstallError(MISMATCH, "%s is %d bytes short" % (name, self.left))
            self.h.update(mv[:n])
            job.sink.wrote(n)
            job.done += n
            self.left -= n
            if self.left:
                return True
        if self.f.read(1):
            raise InstallError(MISMATCH, "%s is larger than the index says (%d bytes)"
                               % (name, self.ext["size"]))
        job.sink.end()
        got = _hex(self.h.digest())
        if got != self.ext["sha256"]:
            raise InstallError(MISMATCH, "%s hashes to %s, the index says %s"
                               % (name, got, self.ext["sha256"]))
        return False

    def close(self):
        f, self.f = self.f, None
        if f is not None:
            try:
                f.close()
            except Exception:  # noqa: BLE001
                pass


class _Opening:
    """A download being opened: the URLs still to try, the answer awaited,
    and -- for an external file's mirror -- the external whose archive is
    opened instead when none of them answers."""

    def __init__(self, urls, size, sha, what, then, fallback=None):
        self.urls = list(urls)
        self.size = size
        self.sha = sha
        self.what = what
        self.then = then              # ("asset" | "bare" | "external", the index's item)
        self.fallback = fallback
        self.url = None
        self.resp = None
        self.why = []
        self.text = UNREACHABLE       # what its failures add up to, in the kid's words

    def close(self):
        resp, self.resp = self.resp, None
        if resp is not None:
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass


class Install:
    """Install (or update) one cart, a slice per frame:

        job = Install(cart, plan(cart, chip, fmt), net, root, session, accepted)
        while job.step(): draw the progress bar (job.done / job.total)
        job.error is None and job.path   -> the cart is on the shelf

    `session(fn)` runs `fn()` inside one store session (the console's SD gate,
    a call-through on flash). `accepted` names the external files whose
    licence the kid accepted; an install with one not accepted refuses before
    it fetches anything. `supplied` maps an external file's path to a file the
    player gave instead of its archive. On any failure the staging folder goes
    and the shelf is exactly as it was; `cancel()` does the same on purpose.

    With a `keep` (the module docstring's keeper) the checked build goes to the
    store of record before it moves into place, and moves only once that store
    has it: `keeping` is True meanwhile, and there is no cancelling -- every
    byte is already checked, and the keeper finishes the job when its answer
    comes, whether or not anybody is still stepping it."""

    def __init__(self, cart, p, net, root, session, accepted=(), chunk=None,
                 step_ms=None, archive="ram", supplied=None, keep=None):
        self.cart = cart
        self.plan = p
        self.net = net
        self.root = root
        self.session = session
        self.accepted = tuple(accepted)
        self.supplied = dict(supplied or {})
        self.keep = keep
        self.keeping = False
        self.step_ms = STEP_MS if step_ms is None else step_ms
        self.archive = archive
        self._in_session = False
        self.folder = cart["folder"]
        self.stage = stage_root(root) + "/" + self.folder
        self.target = root + "/" + self.folder
        self.ranges = bool(getattr(net, "ranges", False))
        self.whole = []                   # assets a ranged read handed back
        self.total = 0
        for a in cart["assets"]:
            keep = _kept(a, p["files"]) if self.ranges else None
            self.total += a["size"] if keep is None else _ranged_bytes(cart, a, keep)
        for e in p["external"]:
            self.total += e["size"]       # the inflate, the copy or the mirror's bytes
            if e["path"] not in self.supplied and external_mirror(e) is None:
                self.total += e["archive"]["size"]
        self.done = 0
        self.error = None
        self.detail = None
        self.path = None
        self.finished = False
        self.t_net = 0
        self.t_store = 0
        self.t_all = 0
        self.scratch = bytearray(READ_CHUNK)
        self.scratch_mv = memoryview(self.scratch)
        self.sink = _Sink(self, WRITE_CHUNK if chunk is None else chunk)
        self.written = {}
        self._queue = [("asset", a) for a in cart["assets"]] \
            + [("local" if e["path"] in self.supplied else "external", e)
               for e in p["external"]]
        self._reader = None
        self._src = None
        self._opening = None
        self._started = None
        self._reported = False

    # -- the store, timed ------------------------------------------------------

    def in_store(self, fn):
        """`fn()` in a store session -- the one already open, if a caller
        holds one (an inflate step reads the archive and writes the member
        inside a single session)."""
        if self._in_session:
            return fn()
        t = _ticks_ms()
        self._in_session = True
        try:
            return self.session(fn)
        finally:
            self._in_session = False
            self.t_store += _ticks_diff(_ticks_ms(), t)

    # -- the frame's slice ---------------------------------------------------------

    def step(self, budget_ms=None):
        """Do up to `budget_ms` of work. True while more remains -- including
        while a non-blocking transport's bytes, or the keeper's answer, are
        still on their way."""
        if self.finished:
            return False
        if self.keeping:
            return True
        budget = self.step_ms if budget_ms is None else budget_ms
        t = _ticks_ms()
        try:
            if self._started is None:
                self._begin()
            while True:
                if self._reader is None:
                    if self._opening is None and not self._next():
                        self._commit()
                        return not self.finished
                    if self._reader is None and not self._open_step():
                        return True
                if self._src is not None and not self._src.ready():
                    return True
                if not self._reader.unit():
                    self._end_source()
                if _ticks_diff(_ticks_ms(), t) >= budget:
                    return True
        except Exception as exc:  # noqa: BLE001 -- an install never takes the shell down
            self._failed(exc)
        finally:
            self.t_all += _ticks_diff(_ticks_ms(), t)
            self._report_once()
        return False

    def _report_once(self):
        if self.finished and self.done and not self._reported:
            self._reported = True
            _log(self.report())

    def _failed(self, exc):
        """End the job on `exc`, in the kid's words."""
        if isinstance(exc, InstallError):
            self._fail(exc.text, exc.detail)
        elif isinstance(exc, MemoryError):
            self._fail(NO_MEMORY, "out of memory at %d of %d bytes"
                       % (self.done, self.total))
        elif isinstance(exc, OSError):
            self._fail(FULL if _store.store_full(exc) else NO_WRITE,
                       "%s: %s" % (self.folder, exc))
        else:
            self._fail(STOPPED, "%s: %r" % (self.folder, exc))

    def cancel(self):
        """Stop, keeping nothing. False once the keeper has the build: past
        that point the cart lands."""
        if self.keeping:
            return False
        if not self.finished:
            self._fail(None, "cancelled")
        return True

    def rate(self):
        """Bytes per second so far, or 0."""
        return self.done * 1000 // self.t_all if self.t_all else 0

    def report(self):
        """One line for serial: what moved, how fast, and where the time went."""
        return ("%s %s %d bytes in %dms (%d KB/s): network %dms, store %dms, "
                "other %dms" % (self.folder, "ok" if self.path else
                                ("failed" if self.error else "stopped"),
                                self.done, self.t_all, self.rate() // 1024,
                                self.t_net, self.t_store,
                                self.t_all - self.t_net - self.t_store))

    # -- phases --------------------------------------------------------------------

    def _begin(self):
        self._started = _ticks_ms()
        for e in self.plan["external"]:
            if e["path"] not in self.accepted:
                raise InstallError(NO_LICENCE, "%s: %s not accepted"
                                   % (self.folder, e["path"]))

        def _fresh():
            _mkdir(stage_root(self.root))
            if _exists(self.stage):
                _rmtree(self.stage)
            os.mkdir(self.stage)
        self.in_store(_fresh)

    def _next(self):
        """The next file to fetch or copy: True when there was one."""
        if not self._queue:
            return False
        kind, item = self._queue.pop(0)
        if kind == "local":
            self._reader = _Local(self, self.supplied[item["path"]], item)
        elif kind == "asset":
            urls = asset_urls(item, getattr(self.net, "cors", False))
            keep = _kept(item, self.plan["files"]) \
                if self.ranges and item not in self.whole else None
            if keep is not None:
                self._reader = self._src = _Ranged(self, urls[0], self.cart, item, keep)
                return True
            self._opening = _Opening(urls, item["size"], item["sha256"], item["name"],
                                     (kind, item))
        elif external_mirror(item) is not None:
            self._opening = _Opening([external_mirror(item)], item["size"], item["sha256"],
                                     item["path"], ("bare", item), fallback=item)
        else:
            self._opening = self._archive(item)
        return True

    def _archive(self, item):
        """The opening of an external file's archive."""
        arc = item["archive"]
        if arc.get("format") != "tar.gz":
            raise InstallError(PACKING, "%s: archive format %r"
                               % (item["path"], arc.get("format")))
        return _Opening(arc["urls"], arc["size"], arc["sha256"],
                        "the archive holding " + item["path"], ("external", item))

    def _open_step(self):
        """Open the download in hand from the first of its URLs that answers
        200 with the size the index says. False while an answer is awaited."""
        o = self._opening
        while True:
            if o.resp is None:
                if not o.urls and o.fallback is not None:
                    # The mirror did not answer: the archive, as a console that
                    # does not know mirrors fetches it.
                    _log("%s: %s; its archive instead" % (o.what, "; ".join(o.why)))
                    item = o.fallback
                    self._opening = o = self._archive(item)
                    self.total += item["archive"]["size"]
                    continue
                if not o.urls:
                    raise InstallError(o.text, "could not get %s: %s"
                                       % (o.what, "; ".join(o.why)))
                o.url = resolve(self.cart["index"], o.urls.pop(0))
                t = _ticks_ms()
                try:
                    o.resp = self.net.open(o.url)
                except Exception as exc:  # noqa: BLE001 -- try the next mirror
                    o.why.append("%s: %s" % (o.url, exc))
                    o.text = net_text(self.net, o.text)
                    continue
                finally:
                    self.t_net += _ticks_diff(_ticks_ms(), t)
            resp = o.resp
            if resp.status is None:
                return False
            if resp.status != 200 or (resp.length is not None and resp.length != o.size):
                o.why.append("%s: HTTP %d, %s bytes" % (o.url, resp.status, resp.length))
                o.close()
                continue
            _log("fetching %s (%d bytes)" % (o.url, o.size))
            self._src = _Fetched(self, resp, o.size, o.sha, o.what)
            o.resp = None
            kind, item = o.then
            if kind == "asset":
                self._reader = _ZipReader(self, self._src, self.cart, item)
            elif kind == "bare":
                self._reader = _Bare(self, self._src, item)
            else:
                self._reader = _TarGzReader(self, self._src, item)
            self._opening = None
            return True

    def _end_source(self):
        if self._src is not None:
            self._src.finish()
        check = getattr(self._reader, "check", None)
        if check is not None:
            check()
        if self._src is not None:
            self._src.close()
        close = getattr(self._reader, "close", None)
        if close is not None and self._src is None:
            close()
        self._src = None
        self._reader = None

    def _commit(self):
        want = self.plan["files"]
        for fn, meta in want.items():
            if self.written.get(fn) != meta["size"]:
                raise InstallError(MISMATCH, "%s was not written whole" % fn)
        if len(self.written) != len(want):
            raise InstallError(MISMATCH, "%s holds files the plan does not"
                               % self.folder)
        entry = record_entry(self.cart, self.plan)
        if self.keep is None:
            self.in_store(lambda: self._swap(entry, True))
            self._landed()
            return

        def _prepare():
            rec = load_record(self.root)
            if _exists(self.target):
                self._carry(rec.get(self.folder))
            rec[self.folder] = entry
            return record_text(rec)
        text = self.in_store(_prepare)
        self.keeping = True
        self.keep.commit(self.folder, self.stage, text,
                         lambda why, full=False: self._kept(entry, why, full))

    def _kept(self, entry, why, full):
        """The keeper's answer: the build is durable (`why` empty) and moves
        into place, or it is not and nothing changed."""
        t = _ticks_ms()
        self.keeping = False
        try:
            if why:
                raise InstallError(FULL if full else NO_WRITE,
                                   "%s: %s" % (self.folder, why))
            self.in_store(lambda: self._swap(entry, False))
            self.keep.landed(self.folder)
            self._landed()
        except Exception as exc:  # noqa: BLE001 -- an install never takes the shell down
            self._failed(exc)
        finally:
            self.t_all += _ticks_diff(_ticks_ms(), t)
            self._report_once()

    def _swap(self, entry, carry):
        """The build into the carts folder by one rename (the old copy aside
        first), and the record. Inside one store session."""
        rec = load_record(self.root)
        old = rec.get(self.folder)
        if _exists(self.target):
            if carry:
                self._carry(old)
            aside = self.stage + ".old"
            if _exists(aside):
                _rmtree(aside)
            os.rename(self.target, aside)
            os.rename(self.stage, self.target)
            _rmtree(aside)
        else:
            os.rename(self.stage, self.target)
        rec[self.folder] = entry
        save_record(self.root, rec)

    def _landed(self):
        self.path = self.target
        self._close(ok=True)

    def _carry(self, old):
        """The kid's own files from the copy being replaced: the saves always,
        the config when it is not the one this store wrote."""
        prev = self.target + "/" + SAVES
        if _exists(prev):
            _copy_bytes(prev, self.stage + "/" + SAVES)
        prev = self.target + "/" + CONFIG
        if not _exists(prev):
            return
        wrote = (old or {}).get("files", {}).get(CONFIG)
        with open(prev, "rb") as f:
            have = _hex(hashlib.sha256(f.read()).digest())
        if have != wrote:
            _copy_bytes(prev, self.stage + "/" + CONFIG)

    def _fail(self, text, detail):
        self.error = text
        self.detail = detail
        if text is not None:
            _log("%s: %s" % (self.folder, detail))
        self._close(ok=False)

    def _close(self, ok):
        self.finished = True
        self.sink.abort()
        if self._opening is not None:
            self._opening.close()
            self._opening = None
        if self._src is not None:
            self._src.close()
            self._src = None
        reader, self._reader = self._reader, None
        drop = getattr(reader, "close", None)
        if drop is not None:
            try:
                drop()
            except Exception as exc:  # noqa: BLE001 -- recover() finishes it next open
                _log("%s: archive left for recover: %s" % (self.folder, exc))
        if ok:
            return
        try:
            self.in_store(lambda: _rmtree(self.stage) if _exists(self.stage) else None)
        except Exception as exc:  # noqa: BLE001 -- recover() finishes it next open
            _log("%s: staging left for recover: %s" % (self.folder, exc))


def _copy_bytes(src, dst):
    with open(src, "rb") as fi:
        data = fi.read()
    with open(dst, "wb") as fo:
        fo.write(data)

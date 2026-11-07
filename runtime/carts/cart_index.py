# Map (grep -n a name to jump there):
#   InstallError                a failure: the kid's text and serial's detail
#   -- references               is_url, resolve, plain_name, mirror_ok
#   -- the sources              parse_sources, sources_text, load_indexes, save_sources
#   -- the signature            split_signed, check_signature
#   -- the index                check_cart, cover_ref, parse_index (_read_listing)
#   -- depends                  needs
#   -- what this console takes  plan, need_bytes
#   -- the record               load_record, save_record, record_entry, staged, recover_steps, remove_steps, drain, stepped
#   -- fetching                 Worker, Fetch, Reach, FileCheck, index_fetch, cover_fetch
#   Worker                      a transport with the http worker: documents whole, downloads streamed
#   Fetch                       one small document, whole
#   Reach                       which of a cart's URLs this console can read
#   FileCheck                   a file the player supplied, held to the index
#   -- the streaming install    a download checked as it streams (_Fetched)
#   -- the stream readers       cart_unpack's _Sink, _ZipReader, _TarGzReader, re-exported
#   Install                     install or update one cart, a slice per frame
#   Install.step                up to budget_ms of work
#   Install.cancel              stop, keeping nothing
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
    interpreter (native/moy_wasm/README.md, "A cart survives its firmware").
  * it streams. The release asset is a STORED zip (a carts repository builds
    it sorted and uncompressed), so its members are cut out of the socket as
    they pass, and an external file from its mirror goes straight to the store
    the same way. An external file's tar.gz is kept whole while its hash is
    checked -- in RAM, or in a file beside the build where memory is short
    (`_TarGzReader` says why) -- and then inflated. `Install.step` does a
    bounded slice per frame.
  * it updates in place: a newer version, or files that differ from what was
    installed (a module for this console's format, say), replaces the folder
    through the same staging, carrying the kid's saves and an edited config;
    the files a compiled cart wrote live beside the store
    (runtime/carts/cart_files.py), so an update never touches them, and REMOVE
    takes them with the cart.
  * it installs only what a sandbox runs or the loader validates (RUNTIMES):
    a compiled cart, whose native module a board loads only by its
    signature, a Lua cart, or a theme package ("runtime": "theme"), data
    the theme loader parses, validates and clamps (docs/theme_and_shell_2026-11.md
    §6) and nothing runs. A board's TLS checks the server's certificate,
    but a server is not proof of what it serves; what lands is sandboxed,
    signed or validated data.

The store layout sits BESIDE the carts folder (moy_store_base._sibling_path):
`installed.json`, the record of what this store installed (index, id, version,
module, every file's sha256), and `install/`, where an install is built. A cart
folder holds only its index's files; the record is what says which version it
is. `system/sources` there (docs/os_2026-11/system.md §6), one index URL a
line, replaces DEFAULT_INDEXES, the way /sd/update/ota.json points the OTA
check at a LAN host; a legacy `indexes.json` reads as absent.

AN INDEX MAY BE SIGNED as OTA manifests are (device/moy_ota.py's keys, RSA-2048
PKCS#1 v1.5 over SHA-256): `tools/ota_sign.py sign-index` appends
`, "sig": "<hex>"}` in place of the document's closing brace, and the signature
covers SIG_SCHEME and the document as it was before. A signed index whose
signature does not verify is refused, wherever this console can verify (the
kernel's `moy_net.ota_verify` on a board, its host twin on CPython); the
browser has no verifier and needs none, its fetch checking the server's
certificate. An unsigned index is read.

An entry's `depends` (a list of cart ids, optional) names the carts it needs
on the console first; `needs` resolves them over the indexes.

A crash at any point leaves the old cart or the new one, never half of either:
`recover_steps`, which the app steps when it opens, removes an unfinished build
and puts back an old copy whose replacement never moved in.

Host == device == browser: MicroPython-safe (`deflate` inflates there, `zlib`
on CPython), and the network is injected -- urllib on the host
(runtime/host/host_app.py), the kernel's client on a board (device/wire_links.CartNet),
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
    from moy_store_base import (_sibling_path, _is_dir, COVER_MAX_BYTES,
                                cart_path)
    from moy_fs import _exists, _mkdir, _read_recover, _write_atomic, _write_atomic_steps
    import moy_carts as _store
    import cart_files as _cart_files
    import cart_listing as _cl
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.ticks import _ticks_ms, _ticks_diff
    from runtime.moy_store_base import (_sibling_path, _is_dir,
                                        COVER_MAX_BYTES, cart_path)
    from runtime.moy_fs import (_exists, _mkdir, _read_recover, _write_atomic,
                                _write_atomic_steps)
    from runtime import moy_carts as _store
    from runtime import cart_files as _cart_files
    from runtime import cart_listing as _cl

# The record and the removal are native/moy_store/moy_shelf.h's on every tier:
# the image's moy_store, or the same C over ctypes on CPython.
try:
    import moy_store as _shelf
    _shelf.shelf_record
except (ImportError, AttributeError):  # CPython
    from runtime import moy_shelf_binding as _shelf


INDEX_VERSION = 1
DEFAULT_INDEXES = (
    "https://moybyte-org.github.io/carts/index.json",
)
SOURCES_NAME = "system/sources"
RECORD_NAME = "installed.json"
SIG_SCHEME = b"moybyte-index-v1\n"
SIG_TAIL = b', "sig": "'
INDEX_KEYS = None             # the keys an index verifies against; None: the image's
STAGE_DIR = "install"
RUNTIMES = ("wasm", "lua", "theme")

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
BAD_SIGNATURE = "The cart shelf's signature is wrong."

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
    deps = c.get("depends", ())
    if not isinstance(deps, list) and deps != ():
        return "depends"
    for d in deps:
        if not isinstance(d, str) or not d:
            return "depends"
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


def _read_listing(c):
    """The description, genre and tags of an entry (SPEC.md 3.7) as a host
    reads them, in place: each stays when it reads and is dropped when it does
    not, so the three keys are either what the row shows or absent."""
    for key, val in (("description", _cl.description(c.get("description"))),
                     ("genre", _cl.genre(c.get("genre"))),
                     ("tags", _cl.tags(c.get("tags")))):
        if val:
            c[key] = val
        elif key in c:
            del c[key]


def parse_index(data, url):
    """The carts in an index document, each stamped with the index it came
    from (`index`) and that index's name (`shelf`). An entry this console
    cannot read is left out and logged, and so is a `cover` it cannot (the
    cart stays); a document that is not an index, or whose signature does not
    verify (`check_signature`), is an InstallError."""
    if isinstance(data, str):
        data = data.encode()
    signed = check_signature(data, url)
    try:
        index = json.loads(data.decode())
    except (ValueError, UnicodeError) as exc:
        raise InstallError(UNREACHABLE, "%s is not JSON: %s" % (url, exc))
    if not isinstance(index, dict) or index.get("version") != INDEX_VERSION:
        raise InstallError(UNREACHABLE, "%s is not a version %d index"
                           % (url, INDEX_VERSION))
    if "sig" in index and signed is False:
        raise InstallError(BAD_SIGNATURE, "%s: a sig that is not the last member"
                           % url)
    out = []
    for c in index.get("carts") or ():
        why = check_cart(c)
        if why is not None:
            _log("%s: left out %r (%s)" % (url, c.get("id") if isinstance(c, dict)
                                           else c, why))
            continue
        c = dict(c)
        _read_listing(c)
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
        c["signed"] = bool(signed)
        out.append(c)
    return out


# -- the sources ------------------------------------------------------------------

def parse_sources(text):
    """The index URLs a `system/sources` text names: one a line, blank lines
    and `#` comments passed over, a line that is not a URL left out."""
    out = []
    for line in text.split("\n"):
        line = line.strip()
        if line and line[0] != "#" and is_url(line) and line not in out:
            out.append(line)
    return out


def sources_text(urls):
    return "".join(u + "\n" for u in urls)


def load_indexes(root):
    """The indexes this console browses: `system/sources` beside the carts
    folder when it names any, else DEFAULT_INDEXES."""
    try:
        got = parse_sources(_read_recover(_sibling_path(root, SOURCES_NAME)))
    except Exception:  # noqa: BLE001 -- an unreadable override is no override
        got = None
    return got or list(DEFAULT_INDEXES)


def save_sources(root, urls):
    """Write `system/sources`; an empty list is the defaults."""
    _mkdir(_sibling_path(root, "system"))
    _write_atomic(_sibling_path(root, SOURCES_NAME), sources_text(urls))


# -- the signature ----------------------------------------------------------------

def split_signed(data):
    """`(payload, sig)` of a signed index's bytes -- the bytes its signature
    covers and the signature's hex -- or None for an unsigned one."""
    end = len(data)
    while end and data[end - 1] in (32, 9, 13, 10):
        end -= 1
    if end < 2 or data[end - 2:end] != b'"}':
        return None
    at = data.rfind(SIG_TAIL, 0, end)
    if at < 0:
        return None
    sig = data[at + len(SIG_TAIL):end - 2]
    if not sig:
        return None
    for b in sig:
        if not (48 <= b <= 57 or 97 <= b <= 102 or 65 <= b <= 70):
            return None
    return SIG_SCHEME + data[:at] + b"}", sig.decode()


def _verifier():
    try:
        import moy_net
    except ImportError:
        try:
            from runtime import net_binding as moy_net
        except ImportError:  # the browser: its fetch checks the certificate
            return None
    return getattr(moy_net, "ota_verify", None)


def check_signature(data, url):
    """True for an index whose signature verifies, False for an unsigned one,
    None for a signed one where nothing can verify it (the browser). An
    InstallError for a signature that does not verify."""
    got = split_signed(data)
    if got is None:
        return False
    verify = _verifier()
    if verify is None:
        return None
    ok = verify(got[0], got[1]) if INDEX_KEYS is None \
        else verify(got[0], got[1], INDEX_KEYS)
    if not ok:
        raise InstallError(BAD_SIGNATURE, "%s: its signature does not verify" % url)
    return True


# -- depends ----------------------------------------------------------------------

def needs(carts, cart, have):
    """The carts installing `cart` takes, dependencies first and `cart` last:
    each of its `depends` (and theirs) the console does not `have` (a set of
    ids), found in `carts` (the indexes' entries; the first with an id wins).
    An InstallError when one is in no index or the chain comes back on
    itself."""
    by_id = {}
    for c in carts:
        by_id.setdefault(c["id"], c)
    out = []
    seen = []

    def visit(c, path):
        if c["id"] in seen:
            return
        for d in c.get("depends") or ():
            if d in have:
                continue
            if d in path:
                raise InstallError("It depends on itself.", "%s: a cycle through %s"
                                   % (cart["id"], d))
            dep = by_id.get(d)
            if dep is None:
                raise InstallError("It needs %s, which no cart shelf has." % d,
                                   "%s needs %s" % (c["id"], d))
            visit(dep, path + [d])
        seen.append(c["id"])
        out.append(c)

    visit(cart, [cart["id"]])
    return out


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
    """{folder: entry} for every cart this store installed: the record as
    moy_shelf.h reads it (the file, else its backup healed into place)."""
    text = _shelf.shelf_record(root)
    return dict(json.loads(text)["carts"]) if text is not None else {}


def find_installed(root, ident):
    """The folder of the installed cart `ident` names (its folder or its id),
    or None."""
    return _shelf.shelf_find(root, ident)


def record_text(rec):
    return json.dumps({"version": 1, "carts": rec})


def save_record(root, rec):
    _store._write_sibling(root, RECORD_NAME, record_text(rec))


def _record_steps(root, text, again):
    """`yield from` it inside a generator whose every resume is a store op:
    the record `text` written in two of them (moy_fs._write_atomic_steps).
    `again()` is the text as it would be made now, asked before the second:
    a record written meanwhile (another face's install or removal) is written
    whole from it instead, or not at all when it is None."""
    path = _sibling_path(root, RECORD_NAME)
    steps = _write_atomic_steps(path, text)
    next(steps)
    yield
    now = again()
    if now == text:
        next(steps, None)
    elif now is not None:
        _write_atomic(path, now)


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


def _moy_carts():
    try:
        import moy_carts
    except ImportError:  # pragma: no cover - host fallback when not yet aliased
        from runtime import moy_carts
    return moy_carts


def stage_root(root):
    return _sibling_path(root, STAGE_DIR)


def staged(root):
    """Whether `install/` holds anything for `recover_steps` to finish."""
    try:
        return bool(os.listdir(stage_root(root)))
    except OSError:
        return False


def recover_steps(root):
    """Finish what an interrupted install or removal left in `install/`, a
    store op a resume (`stepped` runs one a frame): an unfinished build goes,
    an old copy goes once its replacement is in place (as a `.gone` first,
    `discard_steps`) and comes back when it is not, a removed cart goes. A cut
    anywhere leaves entries the next pass finishes."""
    base = stage_root(root)
    try:
        names = os.listdir(base)
    except OSError:
        return
    for n in names:
        yield
        p = base + "/" + n
        if n.endswith(".old"):
            if not _exists(cart_path(root, n[:-4])):
                os.rename(p, cart_path(root, n[:-4]))
                _log("put back %s" % n[:-4])
            else:
                yield from discard_steps(p, base + "/" + n[:-4] + ".gone")
        elif _is_dir(p):
            yield from rmtree_steps(p)
        else:
            try:
                os.remove(p)
            except OSError:
                pass                      # a `.gone` an `.old` above already took


def recover(root):
    """`recover_steps` in one go. Call inside one store session. Returns how
    many entries `install/` held."""
    try:
        n = len(os.listdir(stage_root(root)))
    except OSError:
        return 0
    for _ in recover_steps(root):
        pass
    return n


def discard_steps(path, gone):
    """An old copy at `path` (an `.old`) deleted for good, a store op a
    resume: renamed to `gone` (a `.gone` a cut left deleted first) and that
    deleted an entry at a time. Half an `.old` left by a cut could come back
    as a cart once its replacement is removed; half a `.gone` only goes."""
    if _exists(gone):
        yield from rmtree_steps(gone)
        yield
    os.rename(path, gone)
    yield from rmtree_steps(gone)


def remove_steps(root, folder, keep=None):
    """`folder` taken off the shelf, a store op a resume (`stepped` runs one
    a frame): the record without it made (native/moy_store/moy_shelf.h), the
    folder renamed out of the carts folder (so a crash mid-delete never leaves
    half a cart listed; `recover` deletes what one left), the record written,
    then the folder and the files the cart wrote (runtime/carts/cart_files.py),
    which live beside the store, deleted an entry a resume.

    With a keeper the record without the cart goes to the store of record
    FIRST, ahead of the folder's deletion: a record that outlives its folder
    would one day offer an UPDATE over a kid's own cart of the same name,
    while a folder that outlives its record is only offered again."""
    text = _shelf.shelf_without(root, folder)
    if keep is not None and text is not None:
        keep.record(text)
    yield
    gone = _shelf.shelf_take(root, folder)
    if text is not None:
        yield
        yield from _record_steps(root, text, lambda: _shelf.shelf_without(root, folder))
    yield
    yield from rmtree_steps(gone)
    yield
    yield from rmtree_steps(_shelf.shelf_written(root, folder))
    erased = getattr(keep, "erased", None)
    if erased is not None:
        erased(_cart_files.cart_id(folder), None)


def remove(root, folder, keep=None):
    """`remove_steps` in one go. Call inside one store session."""
    for _ in remove_steps(root, folder, keep):
        pass


def rmtree_steps(path):
    """The tree at `path` deleted, files before their folder, one entry a
    resume (each a store op); nothing where there is none."""
    try:
        names = os.listdir(path)
    except OSError:
        return
    for n in names:
        yield
        p = path + "/" + n
        if _is_dir(p):
            yield from rmtree_steps(p)
        else:
            try:
                os.remove(p)
            except OSError:
                pass
    yield
    try:
        os.rmdir(path)
    except OSError:
        pass


_DONE = object()


def drain(job):
    """`yield from` it: an Install stepped until it ends, one step a resume."""
    while job.step():
        yield


def stepped(op, steps):
    """`yield from` it: the generator `steps` resumed once a frame, each
    resume inside `op` (one store op: the console's SD gate, a call-through on
    flash), so no frame holds more than one of its units."""
    while op(lambda: next(steps, _DONE)) is not _DONE:
        yield


# -- fetching -------------------------------------------------------------------

class _Whole:
    """A document fetched whole off the VM's thread (a transport's
    `get(url, cap)`: the http worker's request, runtime/system/dev_http.py)
    as the response Fetch reads: no status while it runs."""

    def __init__(self, req):
        self.req = req
        self.at = 0
        self.status = None
        self.length = None

    def poll(self):
        st = self.req.state()
        if st is not None:
            self.status, self.length = st

    def ready(self, n):
        return True

    def readinto(self, mv):
        n = self.req.readinto(mv, self.at)
        self.at += n
        return n

    def close(self):
        self.req.close()


class _Streamed:
    """A download streamed through the http worker's window (a transport's
    `stream(url)`: runtime/system/dev_http.py) as the response Install
    reads: no status while its head is on its way, then its bytes as they
    come, never waited for."""

    def __init__(self, req, window):
        self.req = req
        self.window = window
        self.status = None
        self.length = None
        self._failed = None

    def poll(self):
        st = self.req.state()
        if st is not None:
            self.status = st[0]

    def ready(self, n):
        if self._failed is not None:
            return True
        try:
            self.req.state()
        except OSError as exc:           # broke off: the read says so
            self._failed = exc
            return True
        have = self.req.available()
        return have >= n or have >= self.window or self.req.ended()

    def readinto(self, mv):
        if self._failed is not None:
            raise self._failed
        return self.req.readinto(mv)

    def close(self):
        self.req.close()


class Worker:
    """A transport whose requests the http worker serves off the VM's
    thread (runtime/system/dev_http.py): Fetch's documents up to GET_MAX
    fetched whole, and Install's downloads of any size streamed through a
    window, so no TLS handshake or read holds a frame. The rest is the
    transport's."""

    def __init__(self, net, http):
        self._net = net
        self.get = http.get
        self.stream = http.stream
        self.GET_MAX = http.CAP_MAX

    def __getattr__(self, name):
        return getattr(self._net, name)


# A stream's window: a reader's gate (GATE, a zip member's header whole) must
# fit in it with room for the worker to read on, or the gate never opens.
WINDOW = GATE + 65536


def _request(net, url):
    """The response a download reads: streamed by the http worker where the
    transport has it, else the transport's own `open`."""
    stream = getattr(net, "stream", None)
    if stream is not None:
        try:
            return _Streamed(stream(url, WINDOW), WINDOW)
        except OSError as exc:      # no slot, no grant: the transport's own way
            _log("%s: %s; opened instead" % (url, exc))
    return net.open(url)


class Fetch:
    """One small document whole -- an index, a licence text, a cover -- at
    most `limit` bytes and, given `size`/`sha`, exactly those. `step()` is True
    while the answer is still on its way (only a non-blocking transport ever
    says so: one with `get(url, cap)` up to its GET_MAX, whose request
    answers off the VM's thread) and raises an InstallError when it cannot be
    had; then `data` holds the bytes."""

    def __init__(self, net, url, limit, size=None, sha=None):
        self.net = net
        self.url = url
        self.size = size
        self.sha = sha
        self.limit = limit if size is None else size
        self.data = None
        self._out = bytearray()
        self._buf = bytearray(4096)
        get = getattr(net, "get", None)
        try:
            if get is not None and self.limit <= getattr(net, "GET_MAX", 0):
                self._resp = _Whole(get(url, self.limit))
            else:
                self._resp = net.open(url)
        except Exception as exc:  # noqa: BLE001 -- every transport error is "unreachable"
            raise InstallError(net_text(net, UNREACHABLE), "%s: %s" % (url, exc))

    def step(self):
        resp = self._resp
        if resp is None:
            return False
        try:
            if resp.status is None and hasattr(resp, "poll"):
                try:
                    resp.poll()
                except Exception as exc:  # noqa: BLE001 -- no response is "unreachable"
                    raise InstallError(net_text(self.net, UNREACHABLE), "%s: %s"
                                       % (self.url, exc))
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
    The folder goes a store op a step (`dropping`), so a stopped job is
    stepped until `step()` ends like any other, or left to `recover`.

    With an `ask`, `ask(stage)` is called once every file is checked and
    before anything moves into place, inside a store session: None goes on,
    anything else is the install prompt's question (runtime/shell/
    app_perms.py), held in `asking` until `agree()` goes on or `cancel()`
    removes the build; an InstallError it raises refuses the install. A
    denied install leaves the shelf exactly as it was.

    With a `keep` (the module docstring's keeper) the checked build goes to the
    store of record before it moves into place, and moves only once that store
    has it: `keeping` is True meanwhile, and there is no cancelling -- every
    byte is already checked, and the keeper finishes the job when its answer
    comes, whether or not anybody is still stepping it."""

    def __init__(self, cart, p, net, root, session, accepted=(), chunk=None,
                 step_ms=None, archive="ram", supplied=None, keep=None, ask=None):
        self.cart = cart
        self.ask = ask
        self.asking = None
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
        self.target = cart_path(root, self.folder)
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
        self._steps = None                # store ops a step at a time: the staging made, the landing
        self._then = None                 # what follows when they end
        self.landing = False
        self.dropping = False             # a stopped build being deleted, a store op a step
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
        if self.keeping or self.asking is not None:
            return True
        budget = self.step_ms if budget_ms is None else budget_ms
        t = _ticks_ms()
        try:
            if self._started is None:
                self._begin()
            if self._steps is not None:
                if self.in_store(lambda: next(self._steps, _DONE)) is not _DONE:
                    return True
                self._steps = None
                then, self._then = self._then, None
                if then is not None:
                    then()
                return not self.finished
            while True:
                if self._reader is None:
                    if self._opening is None and not self._next():
                        ask, self.ask = self.ask, None
                        if ask is not None:
                            self.asking = self.in_store(lambda: ask(self.stage))
                            if self.asking is not None:
                                return True
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
        return not self.finished

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

    def agree(self):
        """The install prompt accepted: the build moves into place on the
        next step."""
        self.asking = None

    def cancel(self):
        """Stop, keeping nothing: the build is deleted over the next steps
        (`dropping`). False once the build is landing, or the keeper has it:
        past that point the cart lands."""
        if self.keeping or self.landing:
            return False
        if not self.finished and not self.dropping:
            self._fail(None, "cancelled")
        return True

    def finish(self):
        """A landing begun, done now: what a caller that stops stepping
        (Ctrl-C, the app closed) calls, so a cart is never left half moved."""
        while self.landing and not self.finished and self.step():
            pass

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
        self._steps = self._fresh()

    def _fresh(self):
        """The staging folder made empty, a store op a step: what a stopped
        build left deleted an entry at a time, then the folder made."""
        _mkdir(stage_root(self.root))
        if _exists(self.stage):
            yield
            yield from rmtree_steps(self.stage)
        yield
        os.mkdir(self.stage)

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
                    o.resp = _request(self.net, o.url)
                except Exception as exc:  # noqa: BLE001 -- try the next mirror
                    o.why.append("%s: %s" % (o.url, exc))
                    o.text = net_text(self.net, o.text)
                    continue
                finally:
                    self.t_net += _ticks_diff(_ticks_ms(), t)
            resp = o.resp
            if resp.status is None and hasattr(resp, "poll"):
                try:
                    resp.poll()
                except Exception as exc:  # noqa: BLE001 -- no answer: the next mirror
                    o.why.append("%s: %s" % (o.url, exc))
                    o.text = net_text(self.net, o.text)
                    o.close()
                    continue
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
            self.landing = True
            self._steps = self._land(entry)
            self._then = self._landed
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
            self.in_store(lambda: self._swap(entry))
            self.keep.landed(self.folder)
            self._landed()
        except Exception as exc:  # noqa: BLE001 -- an install never takes the shell down
            self._failed(exc)
        finally:
            self.t_all += _ticks_diff(_ticks_ms(), t)
            self._report_once()

    def _land(self, entry):
        """The checked build onto the shelf, a store op a step: the kid's
        config carried from the copy it replaces, then its saves in the op
        that sets that copy aside (so the running cart's last save goes too), the
        build moved in by one rename, the record, then the old copy deleted
        an entry at a time (`recover` finishes what a cut leaves)."""
        old = _exists(self.target)
        aside = self.stage + ".old"
        if old:
            prev = load_record(self.root).get(self.folder)
            yield
            self._carry_config(prev)
            if _exists(aside):
                yield
                yield from rmtree_steps(aside)
            yield
            self._carry_saves()           # with the move aside: a save between is never left behind
            os.rename(self.target, aside)
        yield
        _moy_carts().adopt(self.stage, self.target)
        yield

        def made():
            rec = load_record(self.root)
            rec[self.folder] = entry
            return record_text(rec)
        yield from _record_steps(self.root, made(), made)
        if old:
            yield
            yield from discard_steps(aside, self.stage + ".gone")

    def _swap(self, entry):
        """The keeper's build into the carts folder by one rename (the old
        copy aside first), and the record. Inside one store session."""
        rec = load_record(self.root)
        _moy_carts().adopt(self.stage, self.target)
        rec[self.folder] = entry
        save_record(self.root, rec)

    def _landed(self):
        self.path = self.target
        self._close(ok=True)

    def _carry(self, old):
        """The kid's own files from the copy being replaced: the saves always,
        the config when it is not the one this store wrote."""
        self._carry_saves()
        self._carry_config(old)

    def _carry_saves(self):
        prev = self.target + "/" + SAVES
        if _exists(prev):
            _copy_bytes(prev, self.stage + "/" + SAVES)

    def _carry_config(self, old):
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
        if ok or self.dropping:
            return
        # The build deleted a store op a step, the job unfinished until then: a
        # staging folder is `recover`'s to finish wherever a cut leaves it.
        self.finished = False
        self.dropping = True
        self.asking = None
        self._steps = rmtree_steps(self.stage)
        self._then = self._dropped

    def _dropped(self):
        self.finished = True


def _copy_bytes(src, dst):
    with open(src, "rb") as fi:
        data = fi.read()
    with open(dst, "wb") as fo:
        fo.write(data)


# -- the stream readers ---------------------------------------------------------
# The stream readers (cart_unpack), this module's names too. Imported at the
# foot because cart_unpack takes this module's words and fetch helpers.
try:
    from cart_unpack import _Sink, _ZipReader, _Ranged, _TarGzReader, _Bare, _Local
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.cart_unpack import _Sink, _ZipReader, _Ranged, _TarGzReader, _Bare, _Local

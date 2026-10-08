"""The store on the boards' file systems, under the boards' MicroPython (#224).

The shelf scan reads each cart folder from ONE listing of it, from inside it:
`os.ilistdir` for what is there, `os.chdir` into the folder, names opened
relative to it (`moy_store_base._enter`). CPython runs the same code over a
POSIX directory, which says nothing about FatFS's relative paths, littlefs's
(MicroPython joins them onto the working directory itself) or MicroPython's
VFS working directory. And every board's card is the store's own volume
(native/moy_store/moy_card.c): a FATFS over a C block device that keeps a
cache of sectors, which is only correct if every write the store makes drops
what it covers.

So this mounts a FAT image on that volume, over a card that is a bytearray
behind a fake `moy_sd`, in the desktop MicroPython built in the boards'
model, and pins:

  * the scan of a card is the scan CPython makes of the same files, entry for
    entry, and leaves the working directory where it found it;
  * store writes made through the card -- a code save, a config, a sprite
    sheet, a new cart, a duplicate, a delete -- leave the same shelf
    CPython's store has after the same writes, and every sector the cache
    hands FatFS in the whole session is compared with the card's (a stale
    one fails on the spot, and a cache that skips the drop is caught);
  * the card read back through a device with no cache, after an unmount,
    holds that same shelf.

The same scan and writes run on littlefs, a board's internal flash and the
store a board falls back to with no card, over a plain RAM device -- and on
the kernel's own littlefs instance (native/moy_store/moy_kvfs.c) through its
VFS type, KVfs, over the unix port's RAM medium, whose image is then read back
by VfsLfs2: the instance the kernel owns writes the format the port's reads.
The VFS verbs themselves are held to VfsLfs2's, call for call, below.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from runtime import moy_carts, moy_catalogue
from test_catalogue import _store
from unix_mp import require_unix_mp

ROOT = Path(__file__).resolve().parent.parent
SECTORS = 32768                         # a 16 MB card

FAKE_MOY_SD = '''
SECTOR_SIZE = 512
disk = None
reads = 0
def init(n):
    global disk
    disk = bytearray(n * 512)
    return n
def read(block, buf, n):
    global reads
    reads += 1
    buf[:] = memoryview(disk)[block * 512:(block + n) * 512]
def write(block, buf, n):
    memoryview(disk)[block * 512:(block + n) * 512] = buf
'''

# The writes, as the Editor and the shelf make them. Run by both sides.
WRITES = '''
def writes(moy_carts, cat, root, shelf):
    by = {}
    for e in shelf:
        by[e["title"]] = e
    a = cat.load(by["Assets"]["h"])
    moy_carts.save_code(a, a["src"] + "# saved on the card\\n", force=True)
    a["cfg"]["speed"] = 7
    moy_carts.save_config(a)
    moy_carts.save_sprites(a, ("7" * 128 + "\\n") * 4)
    cat.create("Fresh Cart", root, src="def _draw():\\n    cls(2)\\n")
    cat.duplicate(by["No Sheet"]["h"], root, "No Sheet Twin")
    cat.delete(by["Short Sheet"]["h"])
'''

DRIVER = '''
import sys, os, vfs, json
sys.path[:0] = [@STAGE@, @RUNTIME@, @DEVICE@]
import moy_sd, moy_store
moy_sd.init(@SECTORS@)
moy_store.card_stats(True, @SKIP_DROP@)     # every hit compared with the card


class Ram:
    def __init__(self, n, size=4096):
        self.size = size
        self.data = bytearray(n * size)

    def readblocks(self, block, buf, off=0):
        a = block * self.size + off
        buf[:] = memoryview(self.data)[a:a + len(buf)]

    def writeblocks(self, block, buf, off=None):
        a = block * self.size + (off or 0)
        memoryview(self.data)[a:a + len(buf)] = buf

    def ioctl(self, op, arg):
        if op == 4:
            return len(self.data) // self.size
        return self.size if op == 5 else 0


class Plain:
    def readblocks(self, block, buf):
        moy_sd.read(block, buf, len(buf) // 512)

    def writeblocks(self, block, buf):
        moy_sd.write(block, buf, len(buf) // 512)

    def ioctl(self, op, arg):
        return @SECTORS@ if op == 4 else (512 if op == 5 else 0)


def copy(src, dst):
    try:
        os.mkdir(dst)
    except OSError:
        pass
    for e in os.ilistdir(src):
        s, d = src + "/" + e[0], dst + "/" + e[0]
        if e[1] == 0x4000:
            copy(s, d)
        else:
            with open(s, "rb") as f, open(d, "wb") as g:
                g.write(f.read())


def shelf(cat, root):
    out = []
    for e in cat.catalogue(root):
        del e["h"]
        out.append(e)
    return json.dumps(out)


if @FS@ == "fat":
    vfs.VfsFat.mkfs(Plain())
    vfs.mount(moy_store.card(@SECTORS@, moy_sd), "/sd")
elif @FS@ == "kvfs":
    moy_store.kvol_load(None)
    vfs.mount(moy_store.KVfs(), "/sd")
else:
    bd = Ram(@SECTORS@ // 8)
    vfs.VfsLfs2.mkfs(bd)
    vfs.mount(vfs.VfsLfs2(bd), "/sd")
import moy_carts, moy_catalogue
root = "/sd/moybyte/carts"
moy_carts.ensure_dirs(root)
copy(@SRC@, root)
here = os.getcwd()
print("BEFORE", shelf(moy_catalogue, root))
print("CWD", os.getcwd() == here)
@WRITES@
writes(moy_carts, moy_catalogue, root, moy_catalogue.catalogue(root))
print("AFTER", shelf(moy_catalogue, root))
reads, hits, handed, stale = moy_store.card_stats() if @FS@ == "fat" else (0, 0, 0, 0)
print("HANDED", handed, hits, stale)
vfs.umount("/sd")
if @FS@ == "fat":
    vfs.mount(vfs.VfsFat(Plain()), "/sd")
elif @FS@ == "kvfs":
    bd = Ram(4096)
    bd.data[:] = moy_store.kvol_image()
    vfs.mount(vfs.VfsLfs2(bd), "/sd")
else:
    vfs.mount(vfs.VfsLfs2(bd), "/sd")
print("REMOUNT", shelf(moy_catalogue, root))
'''


def _same(shelf):
    """A shelf as both VMs read it: a broken manifest's note quotes its JSON
    parser, whose words differ between them."""
    for e in shelf:
        if "broken" in e:
            e["broken"] = e["broken"].split(":")[0]
    return shelf


def _host_shelf(root, card_root):
    """CPython's catalogue of `root`, as the card would name it."""
    out = []
    for e in moy_catalogue.catalogue(root):
        del e["h"]
        e["path"] = e["path"].replace(root, card_root, 1)
        out.append(e)
    return _same(json.loads(json.dumps(out)))


def _card_shelf(line):
    return _same(json.loads(line))


def _session(tmp_path, exe, fs, skip_drop=False):
    src = _store(tmp_path)
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "moy_sd.py").write_text(FAKE_MOY_SD)
    script = stage / "driver.py"
    script.write_text(
        DRIVER.replace("@STAGE@", repr(str(stage)))
        .replace("@RUNTIME@", repr(str(ROOT / "runtime")))
        .replace("@DEVICE@", repr(str(ROOT / "device")))
        .replace("@SECTORS@", str(SECTORS))
        .replace("@FS@", repr(fs))
        .replace("@SKIP_DROP@", repr(skip_drop))
        .replace("@SRC@", repr(src))
        .replace("@WRITES@", WRITES))
    return src, subprocess.run([exe, "-X", "heapsize=64m", str(script)],
                               capture_output=True, text=True, timeout=300)


def test_a_cache_that_skips_the_drop_is_caught(tmp_path):
    """The check is only worth its name if a cache that keeps a written
    sector is stale on the next read of it: with the drop skipped, the session
    fails."""
    exe = require_unix_mp(board_model=True, why="the card volume's cache check")
    _src, out = _session(tmp_path, exe, "fat", skip_drop=True)
    assert out.returncode != 0 and "[Errno 5] EIO" in out.stdout + out.stderr, \
        out.stdout[-2000:]


@pytest.mark.parametrize("fs", ["fat", "lfs", "kvfs"])
def test_the_scan_and_the_stores_writes_on_the_boards_file_systems(tmp_path,
                                                                   fs):
    exe = require_unix_mp(
        board_model=True,
        why="The only check of the shelf scan's working-directory reads on "
            "FatFS, and of the T-Deck's sector cache under the store's "
            "writes; CPython has neither.")
    src, out = _session(tmp_path, exe, fs)
    host = str(tmp_path / "host" / "carts")
    shutil.copytree(src, host)
    card = "/sd/moybyte/carts"
    lines = {}
    for ln in out.stdout.splitlines():
        key, _, rest = ln.partition(" ")
        lines[key] = rest
    assert out.returncode == 0 and "REMOUNT" in lines, out.stdout + out.stderr

    before = _host_shelf(host, card)
    assert len(before) > 40
    assert _card_shelf(lines["BEFORE"]) == before
    assert lines["CWD"] == "True"

    ns = {}
    exec(WRITES, ns)
    ns["writes"](moy_carts, moy_catalogue, host,
                 moy_catalogue.catalogue(host))
    after = _host_shelf(host, card)
    assert _card_shelf(lines["AFTER"]) == after
    assert _card_shelf(lines["REMOUNT"]) == after
    assert {"Fresh Cart", "No Sheet Twin"} <= {e["title"] for e in after}
    assert "Short Sheet" not in {e["title"] for e in after}

    if fs == "fat":
        handed, hits, stale = map(int, lines["HANDED"].split())
        assert stale == 0
        assert hits > handed / 2, (handed, hits)


# -- KVfs's verbs against VfsLfs2's --------------------------------------------

VERBS = '''
import os, vfs, moy_store


class Ram:
    def __init__(self, n, size=4096):
        self.size = size
        self.data = bytearray(n * size)

    def readblocks(self, block, buf, off=0):
        a = block * self.size + off
        buf[:] = memoryview(self.data)[a:a + len(buf)]

    def writeblocks(self, block, buf, off=None):
        a = block * self.size + (off or 0)
        memoryview(self.data)[a:a + len(buf)] = buf

    def ioctl(self, op, arg):
        if op == 4:
            return len(self.data) // self.size
        return self.size if op == 5 else 0


def run(m):
    out = []

    def t(name, fn):
        try:
            out.append((name, fn()))
        except OSError as e:
            out.append((name, "OSError", e.errno))
        except ValueError:
            out.append((name, "ValueError"))

    t("mkdir", lambda: os.mkdir(m + "/d"))
    t("mkdir again", lambda: os.mkdir(m + "/d"))
    t("write", lambda: open(m + "/d/a.txt", "w").write("one\\ntwo\\n" * 100))
    t("read", lambda: open(m + "/d/a.txt").read()[:9])
    t("readline", lambda: open(m + "/d/a.txt").readline())
    t("bin", lambda: open(m + "/d/b.bin", "wb").write(bytes(range(256))))

    def seeking():
        with open(m + "/d/b.bin", "r+b") as f:
            f.seek(10)
            f.write(b"XY")
            f.seek(-3, 2)
            tail = f.read()
            return f.tell(), tail
    t("seek", seeking)
    t("bytes", lambda: open(m + "/d/b.bin", "rb").read()[8:14])
    t("append", lambda: open(m + "/d/b.bin", "ab").write(b"Z"))
    t("excl", lambda: open(m + "/d/b.bin", "x"))
    t("missing", lambda: open(m + "/d/none.txt"))
    t("mode", lambda: open(m + "/d/a.txt", "rw"))
    t("list", lambda: sorted(os.listdir(m + "/d")))
    t("ilistdir", lambda: sorted(e[:2] + (e[3],) for e in os.ilistdir(m + "/d")))
    t("stat", lambda: os.stat(m + "/d/b.bin")[:7])
    t("mtime", lambda: os.stat(m + "/d/b.bin")[8] > 0)
    t("stat dir", lambda: os.stat(m + "/d")[0])
    t("rename", lambda: os.rename(m + "/d/b.bin", m + "/d/c.bin"))
    t("rename missing", lambda: os.rename(m + "/d/b.bin", m + "/d/e.bin"))
    t("rmdir full", lambda: os.rmdir(m + "/d"))
    t("chdir", lambda: os.chdir(m + "/d"))
    t("cwd", lambda: os.getcwd())
    t("relative", lambda: open("a.txt").read()[:3])
    t("chdir up", lambda: os.chdir("..//d/./../d"))
    t("cwd 2", lambda: os.getcwd())
    t("chdir bad", lambda: os.chdir(m + "/nope"))
    t("chdir root", lambda: os.chdir("/"))
    t("remove", lambda: [os.remove(m + "/d/" + n) for n in sorted(os.listdir(m + "/d"))])
    t("rmdir", lambda: os.rmdir(m + "/d"))
    t("remove missing", lambda: os.remove(m + "/d"))
    t("statvfs", lambda: os.statvfs(m)[:3] + (os.statvfs(m)[9],))
    f = open(m + "/closed.txt", "w")
    f.close()
    t("closed", lambda: f.write("x"))
    t("import", lambda: __import__("kvmod").X)
    return out


open("/tmp/kvmod_src.py", "w")
moy_store.kvol_load(None)
vfs.mount(moy_store.KVfs(), "/kv")
bd = Ram(4096)
vfs.VfsLfs2.mkfs(bd)
vfs.mount(vfs.VfsLfs2(bd), "/ref")
for m in ("/kv", "/ref"):
    with open(m + "/kvmod.py", "w") as f:
        f.write("X = 42\\n")
import sys
sys.path.insert(0, "/kv")
a = run("/kv")
sys.modules.pop("kvmod", None)
sys.path[0] = "/ref"
b = run("/ref")
print("KV", repr([x for x in a]).replace("/kv", "@"))
print("REF", repr([x for x in b]).replace("/ref", "@"))
'''


def test_kvfs_answers_every_verb_as_vfslfs2_does(tmp_path):
    """The kernel's VFS type, call for call against the port's own littlefs
    VFS on the same operations: results, errnos, the working folder's
    normalisation, a closed file, an import through it."""
    exe = require_unix_mp(board_model=True, why="the kernel's littlefs VFS type")
    script = tmp_path / "verbs.py"
    script.write_text(VERBS)
    out = subprocess.run([exe, "-X", "heapsize=64m", str(script)],
                         capture_output=True, text=True, timeout=120)
    lines = dict(ln.split(" ", 1) for ln in out.stdout.splitlines()
                 if ln.startswith(("KV ", "REF ")))
    assert out.returncode == 0 and "KV" in lines, out.stdout + out.stderr
    assert lines["KV"] == lines["REF"]
    assert "('mtime', True)" in lines["KV"]

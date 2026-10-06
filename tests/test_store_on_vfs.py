"""The store on the boards' file systems, under the boards' MicroPython (#224).

The shelf scan reads each cart folder from ONE listing of it, from inside it:
`os.ilistdir` for what is there, `os.chdir` into the folder, names opened
relative to it (`moy_store_base._enter`). CPython runs the same code over a
POSIX directory, which says nothing about FatFS's relative paths, littlefs's
(MicroPython joins them onto the working directory itself) or MicroPython's
VFS working directory. And the T-Deck's block device keeps a
cache of sectors (`moybyte_sd._NativeSDBlockDev`), which is only correct if
every write the store makes drops what it covers.

So this mounts a FAT image on that block device, over a card that is a
bytearray, in the desktop MicroPython built in the boards' model, and pins:

  * the scan of a card is the scan CPython makes of the same files, entry for
    entry, and leaves the working directory where it found it;
  * store writes made through the cached device -- a code save, a config, a
    sprite sheet, a new cart, a duplicate, a delete -- leave the same shelf
    CPython's store has after the same writes, and every sector the file
    system is handed in the whole session is the card's own (a stale cached
    sector fails on the spot);
  * the card read back through a device with no cache, after an unmount,
    holds that same shelf.

The same scan and writes run on littlefs, a board's internal flash and the
store a board falls back to with no card, over a plain RAM device.
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
import moy_sd, moybyte_sd
moy_sd.init(@SECTORS@)
handed = [0]


class Checked(moybyte_sd._NativeSDBlockDev):
    def readblocks(self, block, buf, off=0):
        super().readblocks(block, buf, off)
        handed[0] += 1
        if buf != moy_sd.disk[block * 512:block * 512 + len(buf)]:
            raise AssertionError("stale sector %d" % block)
        return 0


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
    bd = Checked(@SECTORS@)
    vfs.VfsFat.mkfs(bd)
    vfs.mount(vfs.VfsFat(bd), "/sd")
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
print("HANDED", handed[0], moy_sd.reads)
vfs.umount("/sd")
if @FS@ == "fat":
    vfs.mount(vfs.VfsFat(Plain()), "/sd")
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


@pytest.mark.parametrize("fs", ["fat", "lfs"])
def test_the_scan_and_the_stores_writes_on_the_boards_file_systems(tmp_path,
                                                                   fs):
    exe = require_unix_mp(
        board_model=True,
        why="The only check of the shelf scan's working-directory reads on "
            "FatFS, and of the T-Deck's sector cache under the store's "
            "writes; CPython has neither.")
    src = _store(tmp_path)
    host = str(tmp_path / "host" / "carts")
    shutil.copytree(src, host)
    card = "/sd/moybyte/carts"
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
        .replace("@SRC@", repr(src))
        .replace("@WRITES@", WRITES))
    out = subprocess.run([exe, "-X", "heapsize=64m", str(script)],
                         capture_output=True, text=True, timeout=300)
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
        handed, reads = map(int, lines["HANDED"].split())
        assert reads < handed / 2, (handed, reads)

"""The website's install image (tools/store_image.py): each board's merged
image, then its internal store holding the browser console add-on that fits.

What is pinned: the store sits where each board's table and chip put it (the
"vfs" partition, or MicroPython's tail on the P4s); the bytes between the
image and the store are erased flash; and the store is a LittleFS that the
desktop MicroPython's VfsLfs2 -- the same littlefs, on-disk shape and block
size as every board's internal store (native/moy_store/moy_kvfs.c) -- mounts
at the partition's whole size, with the add-on's pack and manifest read back
whole, junk past the image left as free space, and room to write after."""

import hashlib
import json
import re
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from unix_mp import require_unix_mp

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

pytest.importorskip("littlefs", reason="littlefs-python is the dev extra's (make setup)")

import board_config  # noqa: E402
import gen_web_blob  # noqa: E402
import store_image  # noqa: E402
import web_pack  # noqa: E402

# Each board: its folder, the store's offset and size its table and chip give.
BOARDS = {
    "tdeck": ("lilygo_t_deck_plus_mainline", 0xC40000, 0x3C0000),
    "guition_s3": ("guition_jc3248w535", 0xC40000, 0x3C0000),
    "p4": ("esp32_p4_wifi6_touch_lcd_7b", 0xD30000, (32 << 20) - 0xD30000),
    "guition_p4": ("guition_jc8012p4a1c", 0xD30000, (16 << 20) - 0xD30000),
    "xiao_zero": ("seeed_xiao_esp32s3_zero", 0x420000, 0x3E0000),
    "c3_zero": ("esp32c3_zero", 0x1E0000, 0x220000),
}
SIZES = {1: 0, 2: 1, 4: 2, 8: 3, 16: 4, 32: 5}
TYPES = {"app": 0, "data": 1}
SUBTYPES = {"factory": 0, "ota_0": 0x10, "ota_1": 0x11, "ota": 0, "phy": 1, "nvs": 2,
            "coredump": 3, "fat": 0x81}


def _board(name):
    return ROOT / "firmware" / BOARDS[name][0]


def _csv(board):
    rows = []
    for path in (board / "boards").glob("*/partitions-*.csv"):
        for line in path.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                rows.append([f.strip() for f in line.split(",")])
    return rows


def _flash_mb(board):
    for path in (board / "boards").glob("*/sdkconfig.board"):
        m = re.search(r"^CONFIG_ESPTOOLPY_FLASHSIZE_(\d+)MB=y$", path.read_text(), re.M)
        if m:
            return int(m.group(1))
    return 4                                  # the port's default


def _merged(board, offset, app=b"app" * 1000):
    """A merged image as MicroPython's makeimg writes one: the bootloader's
    header naming the chip size, the table from the board's CSV at 0x8000,
    the app at the first app partition, 0xFF between."""
    rows = _csv(board)
    table = bytearray()
    for name, kind, sub, off, size in (r[:5] for r in rows):
        table += struct.pack("<HBBII16sI", 0x50AA, TYPES[kind], SUBTYPES[sub],
                             int(off, 0), int(size, 0), name.encode(), 0)
    table += b"\xeb\xeb" + b"\xff" * 14 + hashlib.md5(table).digest()
    app_at = min(int(r[3], 0) for r in rows if r[1] == "app")
    img = bytearray(b"\xff" * (app_at - offset))
    img[0:4] = bytes((0xE9, 3, 2, (SIZES[_flash_mb(board)] << 4) | 0xF))
    t = store_image.TABLE_AT - offset
    img[t:t + len(table)] = table
    return bytes(img) + app


def _pack(tag=b"x"):
    return gen_web_blob.pack_bytes([("index.html.gz", b"\x1f\x8b" + tag * 5000),
                                    ("micropython.wasm.gz", bytes(range(256)) * 900)])


@pytest.mark.parametrize("name", sorted(BOARDS))
def test_the_store_is_where_each_board_mounts_it(name):
    board = _board(name)
    offset = int(str(board_config.load(board)["flash"]["offset"]), 0)
    merged = _merged(board, offset)
    assert store_image.store_part(merged, offset) == BOARDS[name][1:]


@pytest.mark.parametrize("name", sorted(BOARDS))
def test_each_board_s_internal_carts_folder_is_the_one_the_image_fills(name):
    """The add-on lands in /moy/carts, the carts folder every board's
    internal store keeps (a console's moy_runtime, a Zero's board.toml)."""
    board = _board(name)
    flash = board_config.load(board)["flash"]
    if "addon" in flash:
        assert flash["addon"] == store_image.CARTS
        return
    text = (board / "modules" / "moy_runtime.py").read_text()
    assert re.search(r'^(FLASH_)?CARTS_ROOT = "%s"$' % store_image.CARTS, text, re.M)


def test_the_add_on_s_folder_names_agree_everywhere():
    from runtime import moy_seed, web_addon
    page_h = (ROOT / "native/moy_net/moy_page.h").read_text()
    removed_h = (ROOT / "native/moy_store/moy_removed.h").read_text()
    prefix = web_pack.CART_ID % ""
    assert '#define MOY_PAGE_CART "%s"' % prefix in page_h
    assert '#define MOY_REMOVED_ADDON_CART "%s"' % prefix in removed_h
    assert '#define MOY_REMOVED_ADDON "%s"' % moy_seed.ADDON_LINE in removed_h
    assert web_addon.PREFIX == moy_seed.ADDON_PREFIX == prefix


@pytest.mark.parametrize("name", ["c3_zero", "p4"])
def test_the_install_is_the_image_then_the_store(name):
    board = _board(name)
    offset = int(str(board_config.load(board)["flash"]["offset"]), 0)
    merged = _merged(board, offset)
    pack, _digest = _pack()
    out = store_image.install_image(merged, offset, pack)
    at = BOARDS[name][1] - offset
    assert out[:len(merged)] == merged
    assert set(out[len(merged):at]) == {0xFF}
    assert len(out) - at < len(pack) + 64 * 4096, "the store's used blocks only"


MOUNT = r'''
import os, vfs, json
img = open(@IMG@, "rb").read()
N = @BLOCKS@


class Flash:
    """The partition: the image's blocks, junk past them (an old store), and
    what the session writes."""
    def __init__(self):
        self.w = {}

    def _block(self, b):
        if b in self.w:
            return self.w[b]
        if b * 4096 < len(img):
            return img[b * 4096:(b + 1) * 4096]
        return bytes([0x5A, b & 0xFF]) * 2048

    def readblocks(self, b, buf, off=0):
        n = len(buf)
        at = 0
        while at < n:
            blk = bytearray(self._block(b + (off + at) // 4096))
            o = (off + at) % 4096
            k = min(n - at, 4096 - o)
            buf[at:at + k] = blk[o:o + k]
            at += k

    def writeblocks(self, b, buf, off=None):
        off = off or 0
        at = 0
        while at < len(buf):
            i = b + (off + at) // 4096
            o = (off + at) % 4096
            k = min(len(buf) - at, 4096 - o)
            blk = bytearray(self._block(i))
            blk[o:o + k] = buf[at:at + k]
            self.w[i] = bytes(blk)
            at += k

    def ioctl(self, op, arg):
        if op == 4:
            return N
        if op == 5:
            return 4096
        if op == 6:
            self.w[arg] = b"\xff" * 4096
        return 0


vfs.mount(vfs.VfsLfs2(Flash()), "/s")
st = os.statvfs("/s")
print("BLOCKS", st[2], st[3])
carts = "/s/moy/carts"
names = os.listdir(carts)
print("CARTS", json.dumps(names))
cart = carts + "/" + names[0]
print("FILES", json.dumps(sorted(os.listdir(cart))))
with open(cart + "/manifest.json") as f:
    print("FITS", json.loads(f.read())["moybyte"]["fits"])
with open(cart + "/moybyte_web.pack", "rb") as f:
    head = f.read(48)
    size = 48
    while True:
        b = f.read(4096)
        if not b:
            break
        size += len(b)
print("HEAD", head[:8].decode(), head[16:48].hex(), size)
with open("/s/moy/written", "wb") as f:
    f.write(b"k" * 100000)
print("WROTE", os.stat("/s/moy/written")[6])
'''


@pytest.mark.parametrize("name", sorted(BOARDS))
def test_each_board_s_store_mounts_in_micropython_with_the_add_on(name, tmp_path):
    exe = require_unix_mp(why="the only check that the store image is the "
                              "LittleFS a board's MicroPython mounts")
    board = _board(name)
    offset = int(str(board_config.load(board)["flash"]["offset"]), 0)
    merged = _merged(board, offset)
    pack, digest = _pack(name.encode())
    out = store_image.install_image(merged, offset, pack)
    at, size = BOARDS[name][1:]
    img = tmp_path / "store.bin"
    img.write_bytes(out[at - offset:])
    script = tmp_path / "mount.py"
    script.write_text(MOUNT.replace("@IMG@", repr(str(img)))
                      .replace("@BLOCKS@", str(size // 4096)))
    run = subprocess.run([exe, "-X", "heapsize=32m", str(script)],
                         capture_output=True, text=True, timeout=300)
    lines = dict(ln.split(" ", 1) for ln in run.stdout.splitlines() if " " in ln)
    assert run.returncode == 0, run.stdout + run.stderr
    total, free = map(int, lines["BLOCKS"].split())
    assert total == size // 4096
    assert free > total - len(pack) // 4096 - 32, "the junk past the image is free space"
    folder = web_pack.cart_id(digest.hex()) + ".moy"
    assert json.loads(lines["CARTS"]) == [folder]
    assert json.loads(lines["FILES"]) == ["LICENSE.md", "manifest.json", "moybyte_web.pack"]
    assert lines["FITS"] == digest.hex()
    assert lines["HEAD"] == "MOYWEB01 %s %d" % (digest.hex(), len(pack))
    assert lines["WROTE"] == "100000"

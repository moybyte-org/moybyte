#!/usr/bin/env python3
# Map (grep -n a name to jump there):
#   partitions      the partition table a merged image carries
#   flash_bytes     the flash size its bootloader's header names
#   store_part      the internal store's offset and size: "vfs", else MicroPython's tail
#   store_image     a LittleFS image of the store holding given files, its used blocks only
#   install_image   bootloader + table + app + the store with the browser console add-on
#   install_name    the install image's file name beside the merged one
"""The website's install of a board: its merged image (bootloader, partition
table, app) and, at the internal store's offset, a store holding the browser
console add-on that fits the app (tools/web_pack.py), and nothing else -- the
image seeds its own carts on its first boot.

    python3 tools/store_image.py firmware/<board>        # dist/<board>/<stem>_install.bin
    python3 tools/store_image.py firmware/<board> --out x.bin

Written at the merged image's offset (board.toml's `[flash] offset`), it
replaces the board's internal store: the store's first blocks become a new
LittleFS, and what lay past them is free space to it. A card is not touched;
a board whose carts live on a card moves the add-on there on its first boot
(moy_seed.bring_addon).

THE STORE is the kernel's LittleFS over the "vfs" partition (native/moy_store/
moy_kvfs.c, the same on-disk shape as MicroPython's VfsLfs2 on every board):
4096-byte blocks, as many as the partition holds, littlefs 2.1 on disk. A
board whose table lists no "vfs" (the two P4s) gets MicroPython's own: the
flash from the end of the last partition to the end of the chip (ports/esp32
main.c), the chip's size being the one its bootloader's header names. The
image holds the used blocks only: it is made at the fewest blocks that fit,
so every block it allocates is in that prefix, and then grown to the
partition's count (lfs_fs_grow), which rewrites only the superblock.

littlefs-python (pyproject's dev extra) writes it; CI's firmware build
installs it beside zopfli. tests/test_store_image.py mounts the result in the
desktop MicroPython and reads the add-on back.
"""

import argparse
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

BLOCK = 4096                    # moy_kvfs.c's block size, and the flash's sector
READ = PROG = LOOKAHEAD = 32    # moy_kvfs.c's KV_READ, KV_PROG, KV_LOOKAHEAD
CACHE = 128                     # its KV_CACHE
CYCLES = 100
TABLE_AT = 0x8000               # CONFIG_PARTITION_TABLE_OFFSET on every board
STORE_LABEL = "vfs"
CARTS = "/moy/carts"            # the internal store's carts folder on every board
INSTALL = "_install"
FLASH_SIZES = {0: 1, 1: 2, 2: 4, 3: 8, 4: 16, 5: 32, 6: 64, 7: 128}   # header nibble -> MB


class ImageError(Exception):
    pass


def partitions(merged, offset):
    """[(label, type, subtype, offset, size)] of the table in `merged` (the
    bytes written at flash `offset`)."""
    at = TABLE_AT - offset
    out = []
    for i in range(at, at + 0xC00, 32):
        magic = struct.unpack_from("<H", merged, i)[0]
        if magic != 0x50AA:
            break
        kind, sub, off, size = struct.unpack_from("<BBII", merged, i + 2)
        label = merged[i + 12:i + 28].split(b"\0", 1)[0].decode()
        out.append((label, kind, sub, off, size))
    if not out:
        raise ImageError("no partition table at 0x%x" % TABLE_AT)
    return out


def flash_bytes(merged):
    """The chip size the bootloader's header names (byte 3's high nibble)."""
    if merged[0] != 0xE9:
        raise ImageError("the image does not start with a bootloader (0x%02x)" % merged[0])
    mb = FLASH_SIZES.get(merged[3] >> 4)
    if mb is None:
        raise ImageError("the header names no flash size (0x%02x)" % merged[3])
    return mb << 20


def store_part(merged, offset):
    """(offset, size) of the internal store: the "vfs" partition, else the
    tail of the chip after the last partition."""
    parts = partitions(merged, offset)
    for label, _kind, _sub, off, size in parts:
        if label == STORE_LABEL:
            return off, size
    end = max(off + size for _l, _k, _s, off, size in parts)
    chip = flash_bytes(merged)
    if chip <= end:
        raise ImageError("no room for a store after 0x%x on a %d MB chip" % (end, chip >> 20))
    return end, chip - end


def _fs(blocks):
    from littlefs import LittleFS
    return LittleFS(block_size=BLOCK, block_count=blocks, read_size=READ, prog_size=PROG,
                    lookahead_size=LOOKAHEAD, cache_size=CACHE, block_cycles=CYCLES)


def store_image(files, blocks):
    """The used blocks of a LittleFS of `blocks` blocks holding `files`
    ({absolute path: bytes})."""
    from littlefs import LittleFSError
    need = sum(-(-len(b) // BLOCK) for b in files.values())
    n = need + 4
    while True:
        if n > blocks:
            raise ImageError("%d B of files do not fit a store of %d blocks"
                             % (sum(map(len, files.values())), blocks))
        fs = _fs(n)
        try:
            for path, data in sorted(files.items()):
                fs.makedirs(path.rsplit("/", 1)[0], exist_ok=True)
                with fs.open(path, "wb") as f:
                    f.write(data)
            break
        except LittleFSError:
            n += max(4, n // 32)
    fs.fs_grow(blocks)
    fs.unmount()
    return bytes(fs.context.buffer[:n * BLOCK])


def addon_files(pack):
    """{path: bytes} of the add-on cart for the pack bytes `pack`, in CARTS."""
    import web_pack
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, web_pack.PACK_FILE)
        with open(path, "wb") as f:
            f.write(pack)
        files = web_pack._cart_files(path)
        folder = web_pack.cart_id(web_pack.digest_of(pack)) + ".moy"
    return {"%s/%s/%s" % (CARTS, folder, name): data for name, data in files.items()}


def install_image(merged, offset, pack):
    """The install: `merged` (written at `offset`), 0xFF to the store, and the
    store holding the add-on for `pack`. Raises ImageError, or web_pack's
    PageError for a pack that is not one."""
    at, size = store_part(merged, offset)
    if at - offset < len(merged):
        raise ImageError("the merged image runs into the store at 0x%x" % at)
    store = store_image(addon_files(pack), size // BLOCK)
    return merged + b"\xff" * (at - offset - len(merged)) + store


def install_name(image):
    """`moybyte_tdeck.bin` -> `moybyte_tdeck_install.bin`."""
    stem, ext = os.path.splitext(image)
    return stem + INSTALL + ext


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("board_dir")
    ap.add_argument("--image", help="the merged image (default: board.toml's [flash] image)")
    ap.add_argument("--out", help="where to write it (default: beside the merged image)")
    a = ap.parse_args()
    import board_config
    import web_pack
    flash = board_config.load(a.board_dir)["flash"]
    image = a.image or os.path.join(ROOT, flash["image"])
    offset = int(str(flash.get("offset", "0x0")), 0)
    pack, _digest = web_pack.page_beside(image, board_config.load(a.board_dir)["board"]["ota"])
    with open(image, "rb") as f:
        merged = f.read()
    with open(pack, "rb") as f:
        body = f.read()
    try:
        out = install_image(merged, offset, body)
    except (ImageError, web_pack.PageError) as exc:
        print("!! %s: %s" % (a.board_dir, exc), file=sys.stderr)
        return 1
    dest = a.out or install_name(image)
    with open(dest + ".part", "wb") as f:
        f.write(out)
    os.replace(dest + ".part", dest)
    at, size = store_part(merged, offset)
    print("OK -> %s (%d B: the image, then the store at 0x%x of %d KB with the "
          "browser console add-on)" % (dest, len(out), at, size >> 10))
    return 0


if __name__ == "__main__":
    sys.exit(main())

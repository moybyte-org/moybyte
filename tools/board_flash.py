#!/usr/bin/env python3
"""Flash / monitor a board from its board.toml [flash]/[monitor] declaration.

Phase A of the board-port kit (#202): the per-board flash facts -- chip, image
path, flash offset, baud, otadata region, esptool reset strategy -- lived as
hand-written Makefile recipes, one per board, and the differences between them
are exactly the kind that bites: the T-Deck's otadata sits at 0x1d000 and the
P4's at 0xd000, and erasing the wrong board's offset on the other board is one
transposed digit away (it happened, the same day this tool was written). Now
the facts live in each board's board.toml and the Makefile targets are two
lines.

    tools/board_flash.py flash   <board_dir> --port /dev/ttyACM0
    tools/board_flash.py monitor <board_dir> --port /dev/ttyACM0

The flash ORDER is fixed here, once: erase otadata FIRST (with --after
no_reset), then write the merged image, so the board leaves the flash running
the slot just written. A board that has taken an OTA is on ota_1, and skipping
the erase makes a cable flash into ota_0 look like a flash that did nothing.

THE STORE FOLLOWS THE TABLE. The merged image carries its partition table, and
the store is wherever that table puts it: a listed `vfs`/`ffat` partition, or
the tail MicroPython builds from the end of the last partition. A filesystem
does not mount at another offset or size, and MicroPython's `inisetup` formats
only a first sector that reads all 0xFF: anything else prints "filesystem
appears to be corrupted" on every boot and the console never starts. So the
flash reads the board's table (0x8000) before it writes, and when the image's
table puts the store somewhere else it erases the new store's first sectors
too, and says so. Same table, same store: nothing extra is erased and the
store survives the flash.

`--before` and `--after` come from the toml, because how a board enters and
leaves the ROM loader is a hardware fact and not a preference. The T-Deck
declares `before = usb_reset` (measured: default_reset write-times-out against
a wedged USB-Serial/JTAG node, usb_reset connects); the P4's CH343 is happy
with the esptool default. `after` defaults to hard_reset, which is what the
console boards want -- and the Zero declares `watchdog_reset`, the verb that
is PROVEN on it: hard_reset left it sitting in the loader when it ran as CDC,
which reads exactly like an image that did not take, and the verb stayed when
the board moved to USB-Serial/JTAG rather than changing two things at once.
That fact lives in the board's own toml, which this file reads.

esptool runs from THIS interpreter (`sys.executable -m esptool`) -- the venv's,
via the Makefile. The fork-era esptool_no_modem wrapper is not needed on either
current board (both of this session's T-Deck flashes ran plain esptool); it
survives for the legacy Makefile variants that still name it.
"""

import argparse
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import board_config  # noqa: E402


def _esptool(chip, port, baud, *args):
    cmd = [sys.executable, "-m", "esptool", "--chip", chip, "--port", port]
    if baud:
        cmd += ["--baud", str(baud)]
    cmd += list(args)
    print("+", " ".join(cmd))
    return subprocess.call(cmd)


PT_OFFSET = 0x8000   # ESP-IDF's default table offset, which every board here keeps
PT_SIZE = 0xC00      # the entries; the rest of the sector is the signature area
_ENTRY = struct.Struct("<2sBBII16sI")   # magic, type, subtype, offset, size, label, flags
_MAGIC = b"\xaa\x50"
_TYPE_DATA = 1
_STORE_LABELS = ("vfs", "ffat")   # the names MicroPython's startup looks for
STORE_ERASE = 0x2000   # the filesystem's first two blocks: LittleFS's root pair


def parse_table(blob):
    """The (label, type, subtype, offset, size) of each entry of a partition
    table sector. The entries end at the first one without the magic (the MD5
    record or erased flash), so a blank sector parses to []."""
    entries = []
    for at in range(0, min(len(blob), PT_SIZE) - _ENTRY.size + 1, _ENTRY.size):
        magic, typ, sub, off, size, label, _flags = _ENTRY.unpack_from(blob, at)
        if magic != _MAGIC:
            break
        entries.append((label.split(b"\0", 1)[0].decode("ascii", "replace"),
                        typ, sub, off, size))
    return entries


def store_of(entries):
    """Where a table puts the store, as (offset, size): the listed vfs/ffat
    data partition, or (end of the last partition, None) for the tail
    MicroPython builds itself. None for an empty table."""
    for label, typ, _sub, off, size in entries:
        if typ == _TYPE_DATA and label in _STORE_LABELS:
            return (off, size)
    if not entries:
        return None
    return (max(off + size for _l, _t, _s, off, size in entries), None)


def moved_store(board_table, image_table):
    """The (offset, size) the image's table gives the store when the board's
    table gives it another place, else None."""
    new = store_of(parse_table(image_table))
    return new if new and new != store_of(parse_table(board_table)) else None


def _describe_store(store):
    if store is None:
        return "none"
    off, size = store
    return "%#x+%s" % (off, "tail" if size is None else "%#x" % size)


def _image_table(image, offset):
    """The partition table sector inside a merged image written at `offset`,
    or None when the image does not reach it (an app-only image)."""
    at = PT_OFFSET - offset
    if at < 0:
        return None
    with open(image, "rb") as f:
        f.seek(at)
        blob = f.read(PT_SIZE)
    return blob if len(blob) == PT_SIZE else None


def _read_board_table(chip, port, baud, before):
    """(esptool's return code, the board's table sector)."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "table.bin"
        args = ["--before", str(before)] if before else []
        rc = _esptool(chip, port, baud, *args, "--after", "no_reset",
                      "read_flash", hex(PT_OFFSET), hex(PT_SIZE), str(out))
        return rc, out.read_bytes() if out.exists() else b""


def _verify_identity(board_dir, port):
    """Best-effort identity check before writing flash. esptool's own chip
    probe already refuses a P4 image on an S3 -- but the two S3 boards are the
    SAME chip behind the SAME usb id (303a:1001), and a T-Deck image booted on
    the Guition is a valid flash of the wrong firmware. Both are attach_only
    (an open never resets them), so asking costs nothing when the console is
    up. A board that does not answer only WARNS: a wedged board is this tool's
    ordinary customer. A POSITIVE mismatch refuses (--no-verify overrides)."""
    ser = board_config.load(board_dir).get("serial", {})
    want = board_config.load(board_dir).get("board", {}).get("ota")
    if not want or not ser.get("attach_only"):
        return  # non-attach boards are chip-guarded by esptool; nothing to ask
    try:
        sys.path.insert(0, str(ROOT / "tools"))
        from p4_autotest import P4Board
        b = P4Board(port, board_dir=board_dir)
    except Exception as exc:  # noqa: BLE001 -- no pyserial / busy port
        print("!! identity unverified (%s) -- proceeding" % exc)
        return
    try:
        b.drain(0.6)
        got = b.identify(timeout=4.0)
    except Exception as exc:  # noqa: BLE001 -- an S3 re-enumerating mid-probe
        # The same wedged board the silent-answer path below already tolerates,
        # arriving as an exception instead of a silence: the probe failing must
        # not be the thing that stops the reflash.
        print("!! identity check failed (%s) -- proceeding as %r" % (exc, want))
        return
    finally:
        b.close()
    if got is None:
        print("!! %s did not answer an identity check (wedged or mid-boot) -- "
              "proceeding as %r" % (port, want))
    elif got != want:
        sys.exit("wrong board on %s: it answers as %r, this image is for %r "
                 "-- the ttyACM numbering has probably shuffled (--no-verify "
                 "to override)" % (port, got, want))
    else:
        print("board on %s confirmed: %s" % (port, got))


def flash(board_dir, port, verify=True):
    if verify:
        _verify_identity(board_dir, port)
    cfg = board_config.load(board_dir)
    chip = cfg["board"]["chip"]
    fl = cfg.get("flash")
    if not fl:
        sys.exit("%s/board.toml has no [flash] section" % board_dir)
    image = ROOT / fl["image"]
    if not image.exists():
        sys.exit("no image at %s -- build it first (make firmware-build-...)"
                 % image)
    baud = fl.get("baud")
    # 0) where the image puts the store, against where the board has it.
    new_store = None
    image_table = _image_table(image, int(str(fl["offset"]), 0))
    if image_table:
        rc, board_table = _read_board_table(chip, port, baud, fl.get("before"))
        if rc:
            return rc
        new_store = moved_store(board_table, image_table)
        if new_store:
            print("!! this image's partition table moves the store (board: %s, "
                  "image: %s): its first %#x bytes are erased so the console "
                  "formats a fresh filesystem; the store re-seeds"
                  % (_describe_store(store_of(parse_table(board_table))),
                     _describe_store(new_store), STORE_ERASE))
    # 1) otadata erase, FIRST and with no reset after -- see the module
    #    docstring for why the order is load-bearing.
    if fl.get("otadata_offset"):
        rc = _esptool(chip, port, baud, "--after", "no_reset", "erase_region",
                      str(fl["otadata_offset"]), str(fl.get("otadata_size", "0x2000")))
        if rc:
            return rc
    if new_store:
        rc = _esptool(chip, port, baud, "--after", "no_reset", "erase_region",
                      hex(new_store[0]), hex(STORE_ERASE))
        if rc:
            return rc
    # 2) the merged image at the board's offset.
    args = []
    if fl.get("before"):
        args += ["--before", str(fl["before"])]
    args += ["--after", str(fl.get("after", "hard_reset")), "write_flash",
             str(fl["offset"]), str(image)]
    return _esptool(chip, port, baud, *args)


def monitor(board_dir, port):
    cfg = board_config.load(board_dir)
    baud = cfg.get("monitor", {}).get("baud", 115200)
    cmd = [sys.executable, "-m", "serial.tools.miniterm", port, str(baud)]
    print("+", " ".join(cmd))
    return subprocess.call(cmd)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("verb", choices=("flash", "monitor"))
    ap.add_argument("board_dir")
    ap.add_argument("--port", required=True)
    ap.add_argument("--no-verify", action="store_true",
                    help="skip the pre-flash identity check")
    a = ap.parse_args(argv[1:])
    if a.verb == "flash":
        return flash(a.board_dir, a.port, verify=not a.no_verify)
    return monitor(a.board_dir, a.port)


if __name__ == "__main__":
    sys.exit(main(sys.argv))

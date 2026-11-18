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
    tools/board_flash.py addon   <board_dir> --port /dev/ttyACM0   (a REPL board)
    tools/board_flash.py carts   <board_dir> --port /dev/ttyACM0   (a REPL board)

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
store survives the flash. The coredump partition is held the same way: when
the image's table puts it where the board's did not, its bytes are whatever
the old table kept there, so the flash erases it and the console reads no dump
until a panic writes one; and so is the pmem log's partition (`pmem`,
native/moy_store/moy_plog.h), erased whole where the image's table places it
anew, so no record of an old store's carts outlives the store.

`--before` and `--after` come from the toml, because how a board enters and
leaves the ROM loader is a hardware fact and not a preference. The T-Deck
declares `before = usb_reset` (measured: default_reset write-times-out against
a wedged USB-Serial/JTAG node, usb_reset connects); the P4's CH343 is happy
with the esptool default. `after` defaults to hard_reset, which every board
takes: an RTS pulse that boots the app and keeps a USB-Serial/JTAG board's
USB device, where a watchdog reset re-enumerates it while it boots.

`stub` picks the flasher stub esptool uploads, and every esptool run of a
flash takes it as `ESPTOOL_STUB_VERSION` (read from 4.12, the variable behind
esptool's hidden `--stub-version`; 5.x defaults to 2). An older esptool ignores
it and runs its own stub, so the flash is slower, never wrong. A board without
the key gets esptool's default. The Waveshare P4 declares 2, and its board.toml
says why.

THE BROWSER CONSOLE ADD-ON. A board that declares `[flash] addon` (the carts
folder, on a board that speaks a bare REPL: the two Zeros) has the add-on cart
that fits the image just written installed into its store after the flash,
over that REPL (`install_addon`), with the add-on folders that fit other
images taken off, and a removal of the console its store recorded dropped.
Either Zero also fetches the one that fits from its release by itself when
it has none and its owner did not remove it, and the XIAO after an update
(zero_host.make_addon); the cable install is how a build of this tree, which
no release publishes, gets its page. The console boards install it through
their dev channel (`tools/board.py BOARD addon`, which a board pass runs).

THE OFFICIAL CARTS. On the same boards the image seeds the carts board.toml's
`[roster] image` names (the XIAO's the core carts, every app and five games;
the C3's the core alone); the flash then installs the other official carts
from this tree's system_carts/, the bytes its release publishes
(`install_carts`): into a NEW store -- one holding nothing but the image's
carts and add-ons -- the set board.toml's `[roster] install` names (empty on
both Zeros today), and into any store every official cart it holds at an older version
that the image does not carry, replaced with the kid's saves and config kept
(the seed's own rule, #47). Each is copied beside the carts folder and moved
in by one rename, and the store's reserve (zero_host.SEED_RESERVE) is kept.
The XIAO brings them to a new release by itself after an update
(runtime/carts/web_addon.py); the C3, with no OTA, by this.

EVERY MPREMOTE CALL HAS A DEADLINE (`_mpremote`): a wedged USB-Serial/JTAG
answers an open with nothing, and on 2026-11-17 one held a flash for 45
minutes. The REPL probe gets PROBE_S, a command EXEC_S, a copy longer by
its size; running out is `NoAnswer`, which names the port and the cure.
And nothing opens the port while a flashed board boots (`_let_boot`), then
the probes come PROBE_GAP_S apart: opened while it booted, a Zero wedged.

esptool runs from THIS interpreter (`sys.executable -m esptool`) -- the venv's,
via the Makefile. The fork-era esptool_no_modem wrapper is not needed on either
current board (both of this session's T-Deck flashes ran plain esptool); it
survives for the legacy Makefile variants that still name it.
"""

import argparse
import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import board_config  # noqa: E402


def _esptool(chip, port, baud, *args, stub=None):
    cmd = [sys.executable, "-m", "esptool", "--chip", chip, "--port", port]
    if baud:
        cmd += ["--baud", str(baud)]
    cmd += list(args)
    if stub is None:
        print("+", " ".join(cmd))
        return subprocess.call(cmd)
    env = dict(os.environ, ESPTOOL_STUB_VERSION=str(stub))
    print("+ ESPTOOL_STUB_VERSION=%s" % stub, " ".join(cmd))
    return subprocess.call(cmd, env=env)


PT_OFFSET = 0x8000   # ESP-IDF's default table offset, which every board here keeps
PT_SIZE = 0xC00      # the entries; the rest of the sector is the signature area
_ENTRY = struct.Struct("<2sBBII16sI")   # magic, type, subtype, offset, size, label, flags
_MAGIC = b"\xaa\x50"
_TYPE_DATA = 1
_SUBTYPE_COREDUMP = 3
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


def coredump_of(entries):
    """(offset, size) of a table's coredump partition, or None."""
    for _label, typ, sub, off, size in entries:
        if typ == _TYPE_DATA and sub == _SUBTYPE_COREDUMP:
            return (off, size)
    return None


def moved_coredump(board_table, image_table):
    """The image's coredump partition when the board's table has none at that
    place, else None."""
    new = coredump_of(parse_table(image_table))
    return new if new and new != coredump_of(parse_table(board_table)) else None


_PMEM_LABEL = "pmem"   # native/moy_store/moy_plog.h's MOY_PLOG_PART


def pmem_of(entries):
    """(offset, size) of a table's pmem log partition, or None."""
    for label, typ, _sub, off, size in entries:
        if typ == _TYPE_DATA and label == _PMEM_LABEL:
            return (off, size)
    return None


def moved_pmem(board_table, image_table):
    """The image's pmem partition when the board's table has none at that
    place, else None."""
    new = pmem_of(parse_table(image_table))
    return new if new and new != pmem_of(parse_table(board_table)) else None


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


def _read_board_table(chip, port, baud, before, stub=None):
    """(esptool's return code, the board's table sector)."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "table.bin"
        args = ["--before", str(before)] if before else []
        rc = _esptool(chip, port, baud, *args, "--after", "no_reset",
                      "read_flash", hex(PT_OFFSET), hex(PT_SIZE), str(out),
                      stub=stub)
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
    stub = fl.get("stub")
    # 0) where the image puts the store, against where the board has it.
    new_store = new_core = new_pmem = None
    image_table = _image_table(image, int(str(fl["offset"]), 0))
    if image_table:
        rc, board_table = _read_board_table(chip, port, baud, fl.get("before"),
                                           stub)
        if rc:
            return rc
        new_store = moved_store(board_table, image_table)
        if new_store:
            print("!! this image's partition table moves the store (board: %s, "
                  "image: %s): its first %#x bytes are erased so the console "
                  "formats a fresh filesystem; the store re-seeds"
                  % (_describe_store(store_of(parse_table(board_table))),
                     _describe_store(new_store), STORE_ERASE))
        new_core = moved_coredump(board_table, image_table)
        if new_core:
            print("!! this image's partition table places the coredump partition at "
                  "%#x+%#x: it is erased" % new_core)
        new_pmem = moved_pmem(board_table, image_table)
        if new_pmem:
            print("!! this image's partition table places the pmem log at "
                  "%#x+%#x: it is erased" % new_pmem)
    # 1) otadata erase, FIRST and with no reset after -- see the module
    #    docstring for why the order is load-bearing.
    if fl.get("otadata_offset"):
        rc = _esptool(chip, port, baud, "--after", "no_reset", "erase_region",
                      str(fl["otadata_offset"]), str(fl.get("otadata_size", "0x2000")),
                      stub=stub)
        if rc:
            return rc
    if new_store:
        rc = _esptool(chip, port, baud, "--after", "no_reset", "erase_region",
                      hex(new_store[0]), hex(STORE_ERASE), stub=stub)
        if rc:
            return rc
    for part in (new_core, new_pmem):
        if not part:
            continue
        rc = _esptool(chip, port, baud, "--after", "no_reset", "erase_region",
                      hex(part[0]), hex(part[1]), stub=stub)
        if rc:
            return rc
    # 2) the merged image at the board's offset.
    args = []
    if fl.get("before"):
        args += ["--before", str(fl["before"])]
    args += ["--after", str(fl.get("after", "hard_reset")), "write_flash",
             str(fl["offset"]), str(image)]
    rc = _esptool(chip, port, baud, *args, stub=stub)
    if rc == 0 and fl.get("addon"):
        _let_boot(port)
        rc = install_addon(board_dir, port, reset=False) or install_carts(board_dir, port)
    return rc


def _let_boot(port):
    """Leave a board esptool just reset alone for FLASHED_BOOT_S: opened and
    closed while it boots out of a watchdog reset, which enumerates its USB
    again, a Zero's USB-Serial/JTAG wedged twice on 2026-11-17 (no web host,
    every open of the port hanging, only a power cycle bringing it back),
    where a board that had finished booting took a whole provisioning over
    the same port."""
    import time
    print("   leaving %s to boot for %d s" % (port, FLASHED_BOOT_S))
    time.sleep(FLASHED_BOOT_S)


ADDON_BOOT_S = 30          # how long a flashed board has to answer its REPL
FLASHED_BOOT_S = 25        # a flashed board's boot, untouched (_let_boot)
PROBE_GAP_S = 5            # between REPL probes: each one opens the port
# Every mpremote call has a deadline: a wedged USB-Serial/JTAG answers an open
# with nothing, never an error, and an mpremote with no deadline waits on it
# for good. The REPL probe is short; a command is given EXEC_S; a copy EXEC_S
# and a second for every COPY_RATE bytes it carries.
PROBE_S = 15
EXEC_S = 120
COPY_RATE = 8 * 1024


class NoAnswer(Exception):
    """An mpremote call that outlived its deadline."""

    def __init__(self, port, seconds):
        Exception.__init__(self, "no answer from %s after %d s: unplug and replug "
                                 "the board" % (port, seconds))


def addon_folder(board_dir, out):
    """The browser console add-on cart that fits the image `board_dir`'s
    build left in dist/ (tools/web_pack.py), unpacked under `out`."""
    import zipfile
    sys.path.insert(0, str(ROOT / "tools"))
    import web_pack
    cfg = board_config.load(board_dir)
    page = web_pack.page_beside(str(ROOT / cfg["flash"]["image"]), cfg["board"]["ota"])
    name, _ = web_pack.publish_cart(page, out)
    with zipfile.ZipFile(os.path.join(out, name)) as z:
        z.extractall(out)
    return os.path.join(out, web_pack.cart_id(page[1]) + ".moy")


def _mpremote(port, *args, timeout=EXEC_S):
    """mpremote's `args` on `port`, given `timeout` seconds; NoAnswer past it."""
    cmd = [sys.executable, "-m", "mpremote", "connect", port] + list(args)
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise NoAnswer(port, timeout)


def _copy_s(path):
    """The deadline of a copy of `path`, a file or a folder."""
    n = 0
    if os.path.isdir(path):
        for base, _dirs, names in os.walk(path):
            n += sum(os.path.getsize(os.path.join(base, f)) for f in names)
    else:
        n = os.path.getsize(path)
    return EXEC_S + n // COPY_RATE


def _await_repl(board_dir, port, what):
    import time
    end = time.monotonic() + ADDON_BOOT_S
    while _mpremote(port, "exec", "pass", timeout=PROBE_S).returncode != 0:
        if time.monotonic() > end:
            print("!! %s: no REPL on %s to install %s" % (board_dir, port, what))
            return False
        time.sleep(PROBE_GAP_S)
    return True


def _answered(fn):
    """`fn`, an install over a board's REPL, with an mpremote call that
    outlived its deadline (NoAnswer) its named failure: 1."""
    def run(board_dir, port, *a, **kw):
        try:
            return fn(board_dir, port, *a, **kw)
        except NoAnswer as exc:
            print("!! %s: %s" % (board_dir, exc))
            return 1
    run.__name__ = fn.__name__
    run.__doc__ = fn.__doc__
    return run


@_answered
def install_addon(board_dir, port, reset=True):
    """The add-on that fits the built image into `[flash] addon`, the carts
    folder, over the board's REPL (mpremote's Ctrl-C stops what the board
    runs), the other add-on folders taken off, the pack's size checked and a
    removal of the console the store recorded dropped, as an install drops
    it (moy_seed.ADDON_LINE); then with `reset` a reset, which starts the
    board again. 0, or 1 with what went wrong."""
    root = board_config.load(board_dir)["flash"]["addon"]
    with tempfile.TemporaryDirectory() as tmp:
        folder = addon_folder(board_dir, tmp)
        name = os.path.basename(folder)
        size = os.path.getsize(os.path.join(folder, "moybyte_web.pack"))
        if not _await_repl(board_dir, port, "the browser console add-on"):
            return 1
        clear = ("import os\n"
                 "p = ''\n"
                 "for d in %r.strip('/').split('/'):\n"
                 "    p += '/' + d\n"
                 "    try:\n"
                 "        os.mkdir(p)\n"
                 "    except OSError:\n"
                 "        pass\n"
                 "def rm(p):\n"
                 "    try:\n"
                 "        for n in os.listdir(p): rm(p + '/' + n)\n"
                 "        os.rmdir(p)\n"
                 "    except OSError:\n"
                 "        os.remove(p)\n"
                 "for n in os.listdir(%r):\n"
                 "    if n.startswith('web_console_') and n != %r:\n"
                 "        rm(%r + '/' + n)\n" % (root, root, name, root))
        print("+ the browser console add-on: %s -> %s" % (name, root))
        steps = (("exec", clear),
                 ("cp", "-r", folder, ":%s/" % root),
                 ("exec", "import os\nassert os.stat(%r)[6] == %d\n"
                  "import moy_store\nmoy_store.removed_drop(%r, 'web_console')\n"
                  "print('MOY' + 'OK')"
                  % ("%s/%s/moybyte_web.pack" % (root, name), size, root)))
        for step in steps:
            r = _mpremote(port, *step, timeout=_copy_s(folder) if step[0] == "cp" else EXEC_S)
            if r.returncode != 0 or (step[0] == "exec" and "Traceback" in r.stdout + r.stderr):
                print("!! the browser console add-on did not install (%s):\n%s%s"
                      % (step[0], r.stdout, r.stderr))
                return 1
        if reset:
            _mpremote(port, "reset")
    return 0


SEED_DIR = ROOT / "system_carts"
CART_SKIP = ("thumbs", "__pycache__")
KEPT = ("pmem.bin", "config.json")        # moy_seed._RESEED_PRESERVE


def offered_carts(seeds=SEED_DIR):
    """{folder: manifest version} of every official cart in `seeds`, in
    order: the seed carts but the test fixtures, which no release publishes
    (gen_device_carts.is_fixture)."""
    import json
    from tools.gen_device_carts import is_fixture
    out = {}
    for d in sorted(Path(seeds).iterdir()):
        man = d / "manifest.json"
        if d.name.endswith(".moy") and man.is_file():
            m = json.loads(man.read_text())
            if not is_fixture(m):
                out[d.name] = int(m.get("version", 0))
    return out


def carts_plan(have, offered, install, image):
    """The official carts to write into a store holding `have` ({folder:
    manifest version}, every cart folder in it), of `offered` (offered_carts),
    leaving out the ones the image carries (`image`, ids), which its own seed
    keeps: each one the store holds at an older version, and into a NEW store
    -- one holding nothing but the image's carts and add-ons -- each of
    `install` (ids) it lacks. In `offered`'s order."""
    image = set(c + ".moy" for c in image)
    want = set(c + ".moy" for c in install)
    new = all(f in image or f.startswith("web_console_") for f in have)
    return [f for f, v in offered.items() if f not in image and (
        (f in have and have[f] < v) or (f not in have and new and f in want))]


def _cart_bytes(src, block):
    """The store blocks the cart folder `src` takes, in bytes."""
    n = 2 * block
    for base, dirs, names in os.walk(src):
        dirs[:] = [d for d in dirs if d not in CART_SKIP]
        n += block * len(dirs)
        for f in names:
            n += (os.path.getsize(os.path.join(base, f)) + block - 1) // block * block
    return n


def _reserve():
    sys.path.insert(0, str(ROOT / "device"))
    import zero_host
    return zero_host.SEED_RESERVE


@_answered
def install_carts(board_dir, port, reset=True, seeds=SEED_DIR):
    """The official carts this tree publishes into `[flash] addon`, the carts
    folder, over the board's REPL (carts_plan: a new store's `[roster]
    install`, and every one the store holds at an older version), each copied
    beside the carts folder and moved in by one rename with its saves and
    config kept, the store's reserve kept free; then with `reset` a reset. 0,
    or 1 with what went wrong."""
    import json
    import shutil
    root = board_config.load(board_dir)["flash"]["addon"]
    stage = root.rsplit("/", 1)[0] + "/install"
    if not _await_repl(board_dir, port, "the official carts"):
        return 1
    look = ("import os, json\n"
            "h = {}\n"
            "for n in os.listdir(%r):\n"
            "    if n.endswith('.moy'):\n"
            "        try:\n"
            "            h[n] = int(json.load(open(%r + '/' + n + '/manifest.json')).get('version', 0))\n"
            "        except Exception:\n"
            "            h[n] = 0\n"
            "s = os.statvfs(%r)\n"
            "print('MOYCARTS', json.dumps({'have': h, 'block': s[0], 'free': s[0] * s[3]}))\n"
            % (root, root, root))
    r = _mpremote(port, "exec", look)
    line = [ln for ln in r.stdout.splitlines() if ln.startswith("MOYCARTS ")]
    if r.returncode != 0 or not line:
        print("!! the official carts: the store did not answer:\n%s%s" % (r.stdout, r.stderr))
        return 1
    got = json.loads(line[-1].split(" ", 1)[1])
    offered = offered_carts(seeds)
    image = board_config.roster_only(board_dir) or tuple(f[:-4] for f in offered)
    plan = carts_plan(got["have"], offered, board_config.roster_install(board_dir), image)
    free, reserve = got["free"], _reserve()
    rm = ("import os\n"
          "def rm(p):\n"
          "    try:\n"
          "        for n in os.listdir(p): rm(p + '/' + n)\n"
          "        os.rmdir(p)\n"
          "    except OSError:\n"
          "        try:\n"
          "            os.remove(p)\n"
          "        except OSError:\n"
          "            pass\n")
    swap = (rm + "st, d = %r, %r\n"
            "for k in %r:\n"
            "    try:\n"
            "        os.stat(d + '/' + k)\n"
            "        rm(st + '/' + k)\n"
            "        os.rename(d + '/' + k, st + '/' + k)\n"
            "    except OSError:\n"
            "        pass\n"
            "rm(d)\n"
            "os.rename(st, d)\n"
            "assert os.stat(d + '/manifest.json')[6] == %d\n"
            "print('MOY' + 'OK')\n")
    left = []
    with tempfile.TemporaryDirectory() as tmp:
        for folder in plan:
            src = Path(seeds) / folder
            need = _cart_bytes(str(src), got["block"])
            if free - need < reserve:
                left.append(folder)
                continue
            clean = os.path.join(tmp, folder)
            shutil.copytree(str(src), clean, ignore=shutil.ignore_patterns(*CART_SKIP))
            print("+ official cart %s -> %s" % (folder, root))
            steps = (("exec", rm + "try:\n    os.mkdir(%r)\nexcept OSError:\n    pass\nrm(%r)\n"
                      % (stage, stage + "/" + folder)),
                     ("cp", "-r", clean, ":%s/" % stage),
                     ("exec", swap % (stage + "/" + folder, root + "/" + folder, KEPT,
                                      os.path.getsize(os.path.join(clean, "manifest.json")))))
            for step in steps:
                r = _mpremote(port, *step,
                              timeout=_copy_s(clean) if step[0] == "cp" else EXEC_S)
                if r.returncode != 0 or (step[0] == "exec" and "Traceback" in r.stdout + r.stderr):
                    print("!! %s did not install (%s):\n%s%s"
                          % (folder, step[0], r.stdout, r.stderr))
                    return 1
            free -= need
    if left:
        print("!! left out, the store's reserve: %s" % ", ".join(left))
    if reset:
        _mpremote(port, "reset")
    return 0


def monitor(board_dir, port):
    cfg = board_config.load(board_dir)
    baud = cfg.get("monitor", {}).get("baud", 115200)
    cmd = [sys.executable, "-m", "serial.tools.miniterm", port, str(baud)]
    print("+", " ".join(cmd))
    return subprocess.call(cmd)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("verb", choices=("flash", "monitor", "addon", "carts"))
    ap.add_argument("board_dir")
    ap.add_argument("--port", required=True)
    ap.add_argument("--no-verify", action="store_true",
                    help="skip the pre-flash identity check")
    a = ap.parse_args(argv[1:])
    if a.verb == "flash":
        return flash(a.board_dir, a.port, verify=not a.no_verify)
    if a.verb == "addon":
        return install_addon(a.board_dir, a.port)
    if a.verb == "carts":
        return install_carts(a.board_dir, a.port)
    return monitor(a.board_dir, a.port)


if __name__ == "__main__":
    sys.exit(main(sys.argv))

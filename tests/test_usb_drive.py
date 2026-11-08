"""USB mass storage (runtime/system/usb_drive.py, docs/os_2026-11/between_apps.md
section 2): the card on a computer while the store is held closed, never both.

Two halves:
  * the hold itself, under the desktop MicroPython in the boards' model, on
    the store's own card volume (native/moy_store/moy_card.c) over a fake
    card: while held, every path under /sd answers EBUSY (Python's opens and
    the C store's writes alike), the card cannot be mounted again, a write
    lands nowhere else, and what the "computer" wrote while it had the card
    is what the reopened store reads;
  * the mode on CPython: the refusals (no card, a game running), the order
    (the store closed before the driver starts, the driver stopped before the
    store opens), a driver that will not start giving the store back, the
    console opening nothing while it is on, and Settings' page holding the
    screen until it is turned off.
"""
import subprocess
from pathlib import Path

import pytest

from runtime import usb_drive
from unix_mp import require_unix_mp, runtime_tree

ROOT = Path(__file__).resolve().parent.parent
SECTORS = 8192                          # a 4 MB card

FAKE_MOY_SD = '''
disk = None
def init(n):
    global disk
    disk = bytearray(n * 512)
def read(block, buf, n):
    buf[:] = memoryview(disk)[block * 512:(block + n) * 512]
def write(block, buf, n):
    memoryview(disk)[block * 512:(block + n) * 512] = buf
'''

HOLD = '''
import sys, os, vfs
sys.path[:0] = [@STAGE@, @RUNTIME@]
import moy_sd, moy_store, usb_drive
moy_sd.init(@SECTORS@)


class Plain:
    """The card as a computer sees it: its sectors, no cache."""
    def readblocks(self, block, buf):
        moy_sd.read(block, buf, len(buf) // 512)
    def writeblocks(self, block, buf):
        moy_sd.write(block, buf, len(buf) // 512)
    def ioctl(self, op, arg):
        return @SECTORS@ if op == 4 else (512 if op == 5 else 0)


def errno(fn):
    try:
        fn()
    except OSError as e:
        return e.errno
    return 0


def card():
    return moy_store.card(@SECTORS@, moy_sd)


vfs.VfsFat.mkfs(Plain())
vfs.mount(card(), "/sd")
os.mkdir("/sd/moybyte")
with open("/sd/moybyte/kept.txt", "w") as f:
    f.write("from the console")
print("HOLD_WHILE_MOUNTED", errno(lambda: moy_store.card_hold(True)))

usb_drive.hold_closed("/sd")
print("HELD", moy_store.card_hold())
print("READ", errno(lambda: open("/sd/moybyte/kept.txt")))
print("WRITE", errno(lambda: open("/sd/moybyte/new.txt", "w")))
print("LIST", errno(lambda: os.listdir("/sd/moybyte")))
print("MKDIR", errno(lambda: os.mkdir("/sd/moybyte/d")))
print("STORE_WRITE", errno(lambda: moy_store.write("/sd/moybyte/c.txt", "x")) != 0)
print("REMOUNT", errno(card))

vfs.mount(vfs.VfsFat(Plain()), "/pc")
with open("/pc/moybyte/from_usb.txt", "w") as f:
    f.write("from the computer")
vfs.umount("/pc")

usb_drive.reopen(card, "/sd")
print("HELD_AFTER", moy_store.card_hold())
print("FILES", ",".join(sorted(os.listdir("/sd/moybyte"))))
print("COMPUTER", open("/sd/moybyte/from_usb.txt").read())
print("CONSOLE", open("/sd/moybyte/kept.txt").read())
moy_store.write("/sd/moybyte/after.txt", "open again")
print("AFTER", open("/sd/moybyte/after.txt").read())
'''


def test_the_store_is_held_closed_while_the_card_is_on_usb(tmp_path):
    exe = require_unix_mp(
        board_model=True,
        why="The only check that a held card refuses every path under /sd, "
            "Python's and the C store's, and refuses a mount; CPython has no "
            "card volume.")
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "moy_sd.py").write_text(FAKE_MOY_SD)
    script = stage / "hold.py"
    script.write_text(HOLD.replace("@STAGE@", repr(str(stage)))
                      .replace("@RUNTIME@", repr(runtime_tree()))
                      .replace("@SECTORS@", str(SECTORS)))
    out = subprocess.run([exe, "-X", "heapsize=16m", str(script)],
                         capture_output=True, text=True, timeout=120)
    lines = dict(ln.partition(" ")[::2] for ln in out.stdout.splitlines())
    assert out.returncode == 0 and "AFTER" in lines, out.stdout + out.stderr
    ebusy = str(usb_drive.EBUSY)
    assert lines["HOLD_WHILE_MOUNTED"] == ebusy     # the volume first, then the hold
    assert lines["HELD"] == "True"
    for verb in ("READ", "WRITE", "LIST", "MKDIR", "REMOUNT"):
        assert lines[verb] == ebusy, (verb, lines)
    assert lines["STORE_WRITE"] == "True"
    assert lines["HELD_AFTER"] == "False"
    assert lines["FILES"] == "from_usb.txt,kept.txt"
    assert lines["COMPUTER"] == "from the computer"
    assert lines["CONSOLE"] == "from the console"
    assert lines["AFTER"] == "open again"


# -- the mode, on CPython ------------------------------------------------------

class _Lever(object):
    def __init__(self, log, fail=False):
        self.log = log
        self.fail = fail
        self.st = usb_drive.STOPPED

    def start(self):
        if self.fail:
            raise OSError(5)
        self.log.append("start")
        self.st = usb_drive.WAITING

    def stop(self):
        self.log.append("stop")
        self.st = usb_drive.STOPPED

    def state(self):
        return self.st


def _drive(ws, log, card=True, fail=False, dev_channel=False):
    d = usb_drive.UsbDrive(_Lever(log, fail),
                           lambda: log.append("close"),
                           lambda: log.append("open"),
                           lambda: card, dev_channel=dev_channel)
    ws.usb_drive = d
    return d


@pytest.fixture
def ws(tmp_path):
    from runtime import host_app
    w = host_app.build_workstation(str(tmp_path / "carts"))
    w.rescans = 0
    was = w.rescan_carts

    def rescan(folders=None):
        w.rescans += 1
        return was(folders)
    w.rescan_carts = rescan
    return w


def test_no_card_and_a_running_game_are_refused(ws):
    log = []
    d = _drive(ws, log, card=False)
    assert not d.turn_on(ws) and d.why == usb_drive.NO_CARD and log == []
    d = _drive(ws, log)
    ws.procs.list.append(object())
    assert not d.turn_on(ws) and d.why == usb_drive.BUSY and log == []
    ws.procs.list.pop()
    assert d.turn_on(ws) and d.on


def test_the_store_closes_before_the_driver_starts_and_opens_after_it_stops(ws):
    log = []
    d = _drive(ws, log)
    assert d.state() == usb_drive.STOPPED
    assert d.turn_on(ws)
    assert log == ["close", "start"] and d.state() == usb_drive.WAITING
    d.turn_off(ws)
    assert log == ["close", "start", "stop", "open"]
    assert not d.on and d.state() == usb_drive.STOPPED and ws.rescans == 1


def test_a_driver_that_will_not_start_gives_the_store_back(ws):
    log = []
    d = _drive(ws, log, fail=True)
    assert not d.turn_on(ws)
    assert log == ["close", "open"] and d.why == usb_drive.FAILED and not d.on


def test_the_console_opens_nothing_while_the_card_is_on_usb(ws):
    log = []
    d = _drive(ws, log)
    d.turn_on(ws)
    cart = ws.carts.all[0]
    ws.open(cart)
    assert ws.cart is not cart and ws._notice[0] == "USB drive is on"
    d.turn_off(ws)
    assert not ws.usb_drive_refuses()


def _settings(ws):
    from runtime.settings_layer import say
    ws.open_settings()
    sl = ws.settings_layer
    sl._draw_page()
    return sl, say


def _keys(*pressed):
    class I(object):
        def pressed(self, name):
            return name in pressed
    return I()


def test_settings_turns_it_on_and_holds_the_page_until_it_is_off(ws):
    sl, say = _settings(ws)
    assert ("usb_drive", "USB DRIVE", "action") not in sl._settings_rows()
    log = []
    d = _drive(ws, log, dev_channel=True)
    assert sl._settings_rows()[-1:] != () and \
        ("usb_drive", "USB DRIVE", "action") in sl._settings_rows()
    sl._activate_settings_action("usb_drive")
    assert sl._page_of() == "usb"
    sl._draw_page()
    d.turn_on(ws)
    sl.handle_input(_keys("b"))
    sl.handle_input(_keys("home"))
    sl.back()
    assert sl._page_of() == "usb" and d.on          # held while it is on
    assert sl.usb_moved()                            # the page follows the drive
    sl._draw_page()
    assert not sl.usb_moved()
    d.lever.st = usb_drive.MOUNTED
    assert sl.usb_moved()
    sl.reset()                                       # Settings opened again
    assert sl._page_of() == "usb"
    d.turn_off(ws)
    sl.back()
    assert sl._page_of() is None and sl._want == say("USB DRIVE")


def test_a_computer_that_ejects_the_card_gives_it_back(ws):
    sl, _say = _settings(ws)
    log = []
    d = _drive(ws, log)
    sl.open_usb()
    d.turn_on(ws)
    sl._draw_page()
    d.lever.st = usb_drive.EJECTED
    assert sl.usb_moved() and not d.on
    assert log == ["close", "start", "stop", "open"]


def test_a_driver_that_gave_the_port_back_by_itself_reopens_the_store(ws):
    """native/moy_usbdisk ends a drive no computer took (or one it left):
    the console finds it stopped and takes the card back."""
    sl, _say = _settings(ws)
    log = []
    d = _drive(ws, log)
    sl.open_usb()
    d.turn_on(ws)
    sl._draw_page()
    d.lever.st = usb_drive.STOPPED
    assert sl.usb_moved() and not d.on and log[-1] == "open"

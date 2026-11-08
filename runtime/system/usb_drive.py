"""USB mass storage (docs/os_2026-11/between_apps.md section 2): the card,
and its `moybyte/` folder, on a computer over USB while the store is held
closed. Never both at once.

THE HOLD (`hold_closed`, `reopen`). Closing the store unmounts the card's
volume (the store's own FATFS, native/moy_store/moy_card.c, f_umounted by its
VfsFat's umount), takes the C's hold (`moy_store.card_hold(True)`: from there
every mount of the card answers EBUSY, the kernel's included), and mounts
`Closed` at the mount point, so every path under it answers EBUSY: a write
made while the computer has the card fails where it is made and never lands
on the internal flash under an empty `/sd`. Only then does the board's USB
driver start (`native/moy_usbdisk`, which refuses a card that is not held).
Reopening is the other way round: the driver stopped first, then the hold
let go and the volume mounted again, its cache dropped (the computer may have
written any sector), and the shelf rescanned.

THE MODE (`UsbDrive`, Settings' USB drive page). Turning it on is refused
while the store is on the internal flash (no card) and while a game or an
app runs: everything the console writes is closed first, and a run would
find its saves failing. While it is on the console opens nothing
(`Workstation.open`), and Settings' page holds the screen until it is turned
off. Where the board's one USB port is also its dev channel (the T-Deck: the
S3's PHY switched from USB-Serial/JTAG to the OTG controller), the page says
so; turning it off switches back, and the BOOT button reaches the ROM loader
either way.

The board's driver (`lever`) answers `start()`, `stop()` and `state()`:
STOPPED, WAITING (on the bus, no computer has it), MOUNTED (a computer
configured it) or EJECTED (the computer let it go). A board with no driver
has no `ws.usb_drive` (None): the row is absent.
"""

STOPPED, WAITING, MOUNTED, EJECTED = 0, 1, 2, 3

EBUSY = 16

# What turning it on answers when it cannot: the page's line.
NO_CARD = "Needs a memory card"
BUSY = "Close games and apps first"
FAILED = "USB didn't start"


class Closed(object):
    """The mount point's stand-in while the computer has the card: a VFS
    whose every verb raises EBUSY."""

    def _no(self, *args):
        raise OSError(EBUSY)

    mount = umount = lambda self, *a: None
    open = ilistdir = stat = statvfs = mkdir = rmdir = remove = rename = _no
    chdir = getcwd = _no


def _vfs():
    try:
        import vfs
    except ImportError:
        import os as vfs
    return vfs


def hold_closed(mount_point="/sd"):
    """The store closed: the card's volume unmounted, held in C, and `Closed`
    mounted in its place."""
    import moy_store
    vfs = _vfs()
    vfs.umount(mount_point)
    try:
        moy_store.card_hold(True)
    finally:
        vfs.mount(Closed(), mount_point)


def reopen(make_volume, mount_point="/sd"):
    """The store open again: `Closed` gone, the hold let go and the volume
    `make_volume()` returns (the card's, mounted afresh) at `mount_point`."""
    import moy_store
    vfs = _vfs()
    try:
        vfs.umount(mount_point)
    except OSError:
        pass
    moy_store.card_hold(False)
    vol = make_volume()
    vfs.mount(vol, mount_point)
    return vol


class UsbDrive(object):
    """The mode. `close_store()` and `open_store()` are the board's hold
    (each run where the board's card sessions run); `on_card()` answers
    whether the store is the card's; `dev_channel` is True where the drive
    takes the board's serial dev channel while it is on."""

    def __init__(self, lever, close_store, open_store, on_card,
                 dev_channel=False):
        self.lever = lever
        self._close = close_store
        self._open = open_store
        self._on_card = on_card
        self.dev_channel = dev_channel
        self.on = False
        self.why = None             # the last refusal, for the page

    def refusal(self, ws):
        """Why it cannot turn on now, or None."""
        if not self._on_card():
            return NO_CARD
        if _busy(ws):
            return BUSY
        return None

    def turn_on(self, ws):
        """Close the store and lend the card out: True when it is on."""
        if self.on:
            return True
        why = self.refusal(ws)
        if why is None:
            ed = getattr(ws, "editor_app", None)
            if ed is not None and getattr(ed, "flush_pending", None) is not None:
                ed.flush_pending()
            try:
                self._close()
            except OSError:
                why = FAILED
        if why is None:
            try:
                self.lever.start()
            except OSError:
                self._open()
                why = FAILED
        self.why = why
        if why is not None:
            return False
        self.on = True
        ws.request_frame()
        return True

    def turn_off(self, ws):
        """Take the card back from USB and open the store on it again."""
        if not self.on:
            return
        self.lever.stop()
        self.on = False
        self.why = None
        self._open()
        ws.rescan_carts()
        ws.request_frame()

    def state(self):
        """STOPPED, WAITING, MOUNTED or EJECTED."""
        if not self.on:
            return STOPPED
        return self.lever.state()


def _busy(ws):
    """A game or an app is running: a desk's running set, or the front run."""
    procs = getattr(ws, "procs", None)
    if procs is None:
        return False
    return bool(procs.list) or procs._front_live()

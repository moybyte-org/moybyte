"""The T-Deck's SD card, on the SPI host the panel already owns.

ONE LIFECYCLE, and the hazard is why. SD and the panel share SPI host 1, so a
`machine.SDCard` -- which re-runs `spi_bus_initialize()` on that host -- and a
per-op teardown -- which corrupts the bus and DMA state `esp_lcd` needs -- both
hang the board on the NEXT panel flush. No panic, no message: gray screen, dead
USB. So the card is ATTACHED to the already-initialized host through the native
`moy_sd` module (ESP-IDF "Sharing the SPI Bus"), mounted ONCE, and kept resident
for the rest of the session. Only the unused LoRa radio CS is parked; the panel's
CS and sdspi's CS are driver-owned and never touched here.

The desktop loop is single-threaded, so `with_sd_live()` runs between frames and
never overlaps a panel flush.
"""

SD_MOUNT = "/sd"
SPI_HOST = 1
RADIO_CS = 9
SD_CS = 39
SD_LIVE_FREQ_KHZ = 20000


# Sectors the block device keeps (`_NativeSDBlockDev`): 16 KB at 512 bytes.
# 32 held the shelf scan's whole working set on an 87-cart card (#224); 16
# thrashed.
CACHE_SECTORS = 32


class _NativeSDBlockDev:
    """MicroPython block device backed by moy_sd (FAT via vfs.mount), with a
    read cache of single sectors.

    FatFS reads directory and FAT sectors one at a time through its one-sector
    window, and reads them again on every path lookup that crosses them: the
    shelf scan read 7,578 sectors of which 642 were distinct (#224). So a
    one-sector read is served from the last CACHE_SECTORS such reads (clock
    eviction), and a longer one -- file data -- goes to the card. Every write
    drops the cached sectors it covers BEFORE it is issued, so neither a write
    nor a failed write leaves a stale sector behind, and a new mount is a new
    device with an empty cache. The slots are one bytearray on the gc heap,
    which is PSRAM on this board."""

    def __init__(self, sectors, cache=CACHE_SECTORS):
        import moy_sd

        self.sectors = sectors
        self._read = moy_sd.read
        self._write = moy_sd.write
        size = self._size = moy_sd.SECTOR_SIZE
        whole = memoryview(bytearray(cache * size))
        self._slots = [whole[i * size:(i + 1) * size] for i in range(cache)]
        self._held = [-1] * cache          # slot -> block, -1 when empty
        self._where = {}                   # block -> slot
        self._ref = bytearray(cache)       # clock bits
        self._hand = 0

    def readblocks(self, block, buf, off=0):
        if off:
            raise OSError(22)  # EINVAL: byte-offset addressing unsupported (FAT uses 512-blocks)
        n = len(buf) // self._size
        if n != 1:
            self._read(block, buf, n)
            return 0
        s = self._where.get(block)
        if s is None:
            s = self._free()
            self._read(block, self._slots[s], 1)
            self._held[s] = block
            self._where[block] = s
        self._ref[s] = 1
        buf[:] = self._slots[s]
        return 0

    def writeblocks(self, block, buf, off=0):
        if off:
            raise OSError(22)
        n = len(buf) // self._size
        end = block + n
        held = self._held
        for s in range(len(held)):
            if block <= held[s] < end:
                self._drop(s)
        self._write(block, buf, n)
        return 0

    def ioctl(self, op, arg):
        if op == 4:        # MP_BLOCKDEV_IOCTL_BLOCK_COUNT
            return self.sectors
        if op == 5:        # MP_BLOCKDEV_IOCTL_BLOCK_SIZE
            return self._size
        if op == 1 or op == 2:             # INIT / DEINIT: a card may have changed
            for s in range(len(self._held)):
                self._drop(s)
        return 0           # SYNC / BLOCK_ERASE: nothing to do

    def _drop(self, s):
        b = self._held[s]
        if b >= 0:
            del self._where[b]
            self._held[s] = -1
        self._ref[s] = 0

    def _free(self):
        """An empty slot: the clock hand's next one not used since it last
        passed, emptied."""
        ref = self._ref
        h = self._hand
        while ref[h]:
            ref[h] = 0
            h += 1
            if h == len(ref):
                h = 0
        self._hand = h + 1 if h + 1 < len(ref) else 0
        self._drop(h)
        return h


def mount_sd_live(host=SPI_HOST, cs=SD_CS, freq_khz=SD_LIVE_FREQ_KHZ):
    """Attach + mount the SD card on the display-shared host via moy_sd."""
    global device
    import moy_sd

    sectors = moy_sd.init(host, cs, freq_khz)
    bd = _NativeSDBlockDev(sectors)
    _mount(bd, SD_MOUNT)
    device = bd
    return bd


_live_mounted = False
# The block device mounted at SD_MOUNT, once one is: what a session reads the
# card through, cache and all.
device = None


def with_sd_live(fn):
    """Run fn() (cart reads/writes under /sd) with SD mounted, then return --
    WITHOUT tearing the card down. The device is mounted once and kept resident
    for the rest of the session; see this module's header for what a teardown
    costs."""
    global _live_mounted
    if not _live_mounted:
        # Imported here, never on the resident path: MicroPython looks for a
        # file overriding a built-in like `os` along the whole path every time
        # the import runs, which costs a session milliseconds on this board --
        # and a running compiled cart's every `read` is a session.
        import os

        try:
            from machine import Pin

            Pin(RADIO_CS, Pin.OUT, value=1)  # park the unused LoRa radio CS only
        except Exception:
            pass
        try:
            os.mkdir(SD_MOUNT)
        except OSError:
            pass
        if not _looks_mounted(os):
            mount_sd_live()
        _live_mounted = True
    return fn()


def _looks_mounted(os_module):
    try:
        return os_module.statvfs(SD_MOUNT) != os_module.statvfs("/")
    except OSError:
        return False
    except AttributeError:
        # A build with no os.statvfs cannot answer; the caller's own residency
        # latch is what keeps a session to one attach either way.
        return False


def _mount(block_device, path):
    try:
        import vfs

        vfs.mount(block_device, path)
        return
    except (ImportError, AttributeError):
        pass

    import os

    os.mount(block_device, path)

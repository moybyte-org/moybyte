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


class _NativeSDBlockDev:
    """MicroPython block device backed by moy_sd (FAT via vfs.mount)."""

    def __init__(self, sectors):
        self.sectors = sectors

    def readblocks(self, block, buf, off=0):
        import moy_sd

        if off:
            raise OSError(22)  # EINVAL: byte-offset addressing unsupported (FAT uses 512-blocks)
        moy_sd.read(block, buf, len(buf) // moy_sd.SECTOR_SIZE)
        return 0

    def writeblocks(self, block, buf, off=0):
        import moy_sd

        if off:
            raise OSError(22)
        moy_sd.write(block, buf, len(buf) // moy_sd.SECTOR_SIZE)
        return 0

    def ioctl(self, op, arg):
        if op == 4:        # MP_BLOCKDEV_IOCTL_BLOCK_COUNT
            return self.sectors
        if op == 5:        # MP_BLOCKDEV_IOCTL_BLOCK_SIZE
            import moy_sd

            return moy_sd.SECTOR_SIZE
        return 0           # INIT / DEINIT / SYNC / BLOCK_ERASE: nothing to do


def mount_sd_live(host=SPI_HOST, cs=SD_CS, freq_khz=SD_LIVE_FREQ_KHZ):
    """Attach + mount the SD card on the display-shared host via moy_sd."""
    import moy_sd

    sectors = moy_sd.init(host, cs, freq_khz)
    bd = _NativeSDBlockDev(sectors)
    _mount(bd, SD_MOUNT)
    return bd


_live_mounted = False


def with_sd_live(fn):
    """Run fn() (cart reads/writes under /sd) with SD mounted, then return --
    WITHOUT tearing the card down. The device is mounted once and kept resident
    for the rest of the session; see this module's header for what a teardown
    costs."""
    global _live_mounted
    import os

    if not _live_mounted:
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

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


def mount_sd_live(host=SPI_HOST, cs=SD_CS, freq_khz=SD_LIVE_FREQ_KHZ):
    """Attach the SD card on the display-shared host via moy_sd and mount the
    store's own volume over it: native/moy_store's card, a FATFS over a C
    block device with the sector cache (moy_card.c), which VfsFat and the
    store share."""
    global device
    import moy_sd
    import moy_store

    sectors = moy_sd.init(host, cs, freq_khz)
    bd = moy_store.card(sectors)
    _mount(bd, SD_MOUNT)
    device = bd
    return bd


_live_mounted = False
# The card volume mounted at SD_MOUNT, once one is.
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

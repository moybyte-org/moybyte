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

THE PINS ARE THE PANEL MODULE'S. `moy_lcd` owns the host and exports it with
the card's CS and the CS to park (`SPI_HOST`, `SD_CS`, `RADIO_CS`, -1 where a
board has none), each its board's define; the constants below are the T-Deck's
and answer only where no `moy_lcd` does. The M5Stack Core2 takes this module
with CS 4 and no radio, and its GPIO9 is a FLASH data line: driving the
T-Deck's radio CS there corrupts the next flash read and hangs the board in its
own panic handler (2026-11-01).
"""

SD_MOUNT = "/sd"
SPI_HOST = 1
RADIO_CS = 9
SD_CS = 39
SD_LIVE_FREQ_KHZ = 20000


def _pins():
    """(host, sd_cs, radio_cs): the panel module's, else this file's."""
    try:
        import moy_lcd
        return (getattr(moy_lcd, "SPI_HOST", SPI_HOST),
                getattr(moy_lcd, "SD_CS", SD_CS),
                getattr(moy_lcd, "RADIO_CS", RADIO_CS))
    except ImportError:
        return SPI_HOST, SD_CS, RADIO_CS


def mount_sd_live(host=None, cs=None, freq_khz=SD_LIVE_FREQ_KHZ):
    """Attach the SD card on the display-shared host via moy_sd and mount the
    store's own volume over it: native/moy_store's card, a FATFS over a C
    block device with the sector cache (moy_card.c), which VfsFat and the
    store share."""
    global device
    import moy_sd
    import moy_store

    if host is None or cs is None:
        p_host, p_cs, _radio = _pins()
        host = p_host if host is None else host
        cs = p_cs if cs is None else cs
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

        radio_cs = _pins()[2]
        if radio_cs >= 0:
            try:
                from machine import Pin

                Pin(radio_cs, Pin.OUT, value=1)  # park the unused radio CS only
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

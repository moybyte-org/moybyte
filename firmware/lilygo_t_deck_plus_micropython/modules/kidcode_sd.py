SD_MOUNT = "/sd"
SD_PROJECT_FILE_PATHS = (
    "/sd/kidcode/project.py",
    "/sd/kidcode/main.py",
    "/sd/project.py",
    "/sd/main.py",
)
SD_FREQ = 800000
SPI_HOST = 1
SPI_MOSI = 41
SPI_MISO = 38
SPI_SCK = 40
RADIO_CS = 9
TFT_CS = 12
SD_CS = 39


def mount_sd(spi_bus=None):
    import os
    from machine import Pin, SDCard

    if _looks_mounted(os):
        return SD_MOUNT

    spi_bus = spi_bus or _display_spi_bus()
    if spi_bus is None:
        spi_bus = _new_spi_bus()

    # Keep all shared SPI devices deselected before adding the SD card.
    _deselect_shared_spi(Pin)

    try:
        os.mkdir(SD_MOUNT)
    except OSError:
        pass

    sd = SDCard(spi_bus=spi_bus, cs=SD_CS, freq=SD_FREQ)
    try:
        _mount(sd, SD_MOUNT)
    except OSError:
        if _looks_mounted(os):
            return SD_MOUNT
        raise
    return SD_MOUNT


def read_first_project_source(spi_bus=None):
    import os

    sd = None
    owned_spi = None
    try:
        spi_bus = spi_bus or _display_spi_bus()
        if spi_bus is None:
            owned_spi = _new_spi_bus()
            spi_bus = owned_spi
        sd = _mount_sd_device(spi_bus)
        for path in SD_PROJECT_FILE_PATHS:
            try:
                with open(path, "r") as handle:
                    return path, handle.read()
            except OSError:
                pass
        return None
    finally:
        _unmount_if_possible(os)
        _deinit_if_possible(sd)
        _deinit_if_possible(owned_spi)
        _deselect_after_sd()


def with_sd(fn, spi_bus=None):
    """Mount the SD card, run fn() (which may read/write under /sd), then always
    unmount + deselect so the display can own the shared SPI bus again. This is
    the same lifecycle as read_first_project_source: leaving an SDCard device on
    the bus collides with esp_lcd flushes and hard-hangs the device."""
    import os

    sd = None
    owned_spi = None
    try:
        spi_bus = spi_bus or _display_spi_bus()
        if spi_bus is None:
            owned_spi = _new_spi_bus()
            spi_bus = owned_spi
        sd = _mount_sd_device(spi_bus)
        return fn()
    finally:
        _unmount_if_possible(os)
        _deinit_if_possible(sd)
        _deinit_if_possible(owned_spi)
        _deselect_after_sd()


# --- live SD sharing (native single-bus, while the panel is running) --------
#
# machine.SDCard re-runs spi_bus_initialize() on the host esp_lcd already owns,
# which hard-hangs the board once the panel is live (see the README SD section
# and CLAUDE.md). The kc_sd native module instead ATTACHES the card to that same,
# already-initialized host (ESP-IDF "Sharing the SPI Bus" guide) -- no bus re-init,
# the panel device is left intact. So reads AND writes work mid-run, as long as
# the caller never flushes the panel during the SD session (the device desktop
# loop is single-threaded, so with_sd_live() runs between frames).
SD_LIVE_FREQ_KHZ = 20000


class _NativeSDBlockDev:
    """MicroPython block device backed by kc_sd (FAT via vfs.mount)."""

    def __init__(self, sectors):
        self.sectors = sectors

    def readblocks(self, block, buf, off=0):
        import kc_sd

        if off:
            raise OSError(22)  # EINVAL: byte-offset addressing unsupported (FAT uses 512-blocks)
        kc_sd.read(block, buf, len(buf) // kc_sd.SECTOR_SIZE)
        return 0

    def writeblocks(self, block, buf, off=0):
        import kc_sd

        if off:
            raise OSError(22)
        kc_sd.write(block, buf, len(buf) // kc_sd.SECTOR_SIZE)
        return 0

    def ioctl(self, op, arg):
        if op == 4:        # MP_BLOCKDEV_IOCTL_BLOCK_COUNT
            return self.sectors
        if op == 5:        # MP_BLOCKDEV_IOCTL_BLOCK_SIZE
            import kc_sd

            return kc_sd.SECTOR_SIZE
        return 0           # INIT / DEINIT / SYNC / BLOCK_ERASE: nothing to do


def mount_sd_live(host=SPI_HOST, cs=SD_CS, freq_khz=SD_LIVE_FREQ_KHZ):
    """Attach + mount the SD card on the display-shared host via kc_sd."""
    import kc_sd

    sectors = kc_sd.init(host, cs, freq_khz)
    bd = _NativeSDBlockDev(sectors)
    _mount(bd, SD_MOUNT)
    return bd


_live_mounted = False


def with_sd_live(fn):
    """Run fn() (cart reads/writes under /sd) with SD mounted via the native
    single-bus path (kc_sd) while the panel is live, then return -- WITHOUT
    tearing the card down. The SD device is mounted once and kept resident for
    the rest of the device session.

    Why persistent: tearing the sdspi device down between ops (sdspi_host_deinit)
    corrupts the shared SPI bus + DMA state esp_lcd needs, and the next panel
    flush hangs the board -- observed as "the write lands on SD, then resume
    hangs." We also leave esp_lcd's TFT_CS and sdspi's SD_CS untouched (both are
    driver-owned); only the unused LoRa radio CS is parked high. The desktop loop
    is single-threaded, so fn() never overlaps a panel flush."""
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


def _mount_sd_device(spi_bus):
    import os
    from machine import Pin, SDCard

    if _looks_mounted(os):
        return None

    _deselect_shared_spi(Pin)
    try:
        os.mkdir(SD_MOUNT)
    except OSError:
        pass

    sd = SDCard(spi_bus=spi_bus, cs=SD_CS, freq=SD_FREQ)
    _mount(sd, SD_MOUNT)
    return sd


def _display_spi_bus():
    try:
        from tdeck_display import get_spi_bus

        return get_spi_bus()
    except Exception:
        return None


def _new_spi_bus():
    import machine

    return machine.SPI.Bus(host=SPI_HOST, mosi=SPI_MOSI, miso=SPI_MISO, sck=SPI_SCK)


def _deselect_shared_spi(Pin):
    Pin(RADIO_CS, Pin.OUT, value=1)
    Pin(TFT_CS, Pin.OUT, value=1)
    Pin(SD_CS, Pin.OUT, value=1)


def _deselect_after_sd():
    try:
        from machine import Pin

        _deselect_shared_spi(Pin)
    except Exception:
        pass


def _unmount_if_possible(os_module):
    try:
        import vfs

        vfs.umount(SD_MOUNT)
        return
    except Exception:
        pass

    try:
        os_module.umount(SD_MOUNT)
    except Exception:
        pass


def _deinit_if_possible(obj):
    if obj is None:
        return
    deinit = getattr(obj, "deinit", None)
    if deinit is None:
        return
    try:
        deinit()
    except Exception:
        pass


def _looks_mounted(os_module):
    try:
        return os_module.statvfs(SD_MOUNT) != os_module.statvfs("/")
    except OSError:
        return False
    except AttributeError:
        return _has_project_file(os_module)


def _has_project_file(os_module):
    for path in SD_PROJECT_FILE_PATHS:
        try:
            with open(path, "r"):
                return True
        except OSError:
            pass
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

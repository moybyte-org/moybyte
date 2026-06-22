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

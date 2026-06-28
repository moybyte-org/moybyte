"""OTA firmware updater for the device (#53): flash a new app image from SD.

The KidCode build now ships a DUAL-APP partition table (otadata + ota_0 + ota_1,
see build.sh --ota), so the device can write a new firmware image to the INACTIVE
slot and ping-pong between ota_0/ota_1 -- the running slot is never touched, so a
failed/half-written update can't brick the device. Rollback is enabled in the
bootloader (CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE): a freshly-flashed app that
never calls mark_valid() is reverted on the next boot, so a bad image self-heals.

Source of the image is the SD card (kid copies / future #38 WiFi downloads a .bin
to /sd/update/). SD shares the panel's SPI host, so every SD touch goes through the
injected `with_sd` wrapper (kid_runtime's _with_sd_synced -> comp.sync() +
kidcode_sd.with_sd_live) exactly like cart saves -- it drains any in-flight panel
DMA, then runs the op on the native single-bus path.

Architecture split (host == device, #17): this module owns ONLY the hardware
(esp32.Partition flash writes + SD reads). ALL pixels -- the confirm screen and
the progress bar -- are drawn by the shared console (Workstation._draw_update), which
drives this backend one chunk per frame so the normal frame/flush loop stays in
charge. The host injects no updater, so the shared "UPDATE FW" Settings row simply
doesn't appear there.

Driven by the console as: find_bin() -> begin(path) -> step()*N -> finish() -> reset().
"""

UPDATE_DIR = "/sd/update"
BLOCK = 4096                 # esp32.Partition native block (erase page); writeblocks erases
IMAGE_MAGIC = 0xE9          # first byte of an ESP32 app image (esp_image_header_t.magic)


class OtaUpdater:
    """Stepwise OTA install from an SD .bin into the inactive app slot.

    `with_sd(fn)` runs fn() with the SD card mounted on the live single-bus path and
    the panel DMA drained first (injected by run_desktop). Flash writes themselves
    don't touch the shared SPI bus, but the SD reads do, so the whole read+write of
    each chunk runs inside one with_sd() call.
    """

    def __init__(self, with_sd):
        self._with_sd = with_sd
        self._buf = bytearray(BLOCK)
        self._mv = memoryview(self._buf)
        self._part = None         # the target (inactive) esp32.Partition
        self._f = None            # the open SD image file (resident across steps)
        self._block = 0           # next block index to write
        self.total = 0            # image size in bytes (for the progress bar)
        self.done = 0             # bytes flashed so far
        self.path = None          # the image being installed
        self.error = None         # last error string (shown by the console)

    # -- capability + status (cheap, no SD) ----------------------------------

    def available(self):
        """True when this build has OTA partitions (running slot is ota_0/ota_1).
        False on a legacy single-`factory` build -- there's no second slot to write,
        so the console hides the UPDATE FW row until a full OTA image is USB-flashed."""
        try:
            import esp32

            return self._running_label() in ("ota_0", "ota_1")
        except Exception:
            return False

    def _running_label(self):
        import esp32

        return esp32.Partition(esp32.Partition.RUNNING).info()[4]

    def slot(self):
        """The running partition label (ota_0 / ota_1 / factory) for display."""
        try:
            return self._running_label()
        except Exception:
            return "?"

    def mark_valid(self):
        """Confirm the running image is healthy so the bootloader cancels its pending
        rollback. Called once at a healthy boot (run_desktop). No-op (swallowed) when
        the image was already marked valid or this isn't an OTA build."""
        try:
            import esp32

            esp32.Partition.mark_app_valid_cancel_rollback()
            return True
        except Exception:
            return False

    # -- discovery -----------------------------------------------------------

    def find_bin(self):
        """The newest *.bin under /sd/update, as (path, size), or None. SD op."""
        def _scan():
            import os

            try:
                names = os.listdir(UPDATE_DIR)
            except OSError:
                return None
            best = None
            for name in names:
                if not name.lower().endswith(".bin"):
                    continue
                p = UPDATE_DIR + "/" + name
                try:
                    size = os.stat(p)[6]
                except OSError:
                    continue
                if best is None or size > best[1]:
                    best = (p, size)
            return best

        try:
            return self._with_sd(_scan)
        except Exception as exc:
            self.error = _short(exc)
            return None

    # -- install (driven one step per frame by the console) ------------------

    def begin(self, path):
        """Open the image + target slot, validate it fits and looks like an app image.
        Returns total bytes on success; raises on a bad/oversized image."""
        import esp32
        import os

        self.error = None
        self._block = 0
        self.done = 0
        self.path = path

        size = os.stat(path)[6]
        part = esp32.Partition(esp32.Partition.RUNNING).get_next_update()
        slot_size = part.info()[3]
        if size <= 0:
            raise ValueError("empty image")
        if size > slot_size:
            raise ValueError("image %dK > slot %dK" % (size // 1024, slot_size // 1024))

        def _open():
            f = open(path, "rb")
            head = f.read(1)
            f.seek(0)
            return f, head

        f, head = self._with_sd(_open)
        if not head or head[0] != IMAGE_MAGIC:
            try:
                f.close()
            except Exception:
                pass
            raise ValueError("not an app image")

        self._f = f
        self._part = part
        self.total = size
        return size

    def step(self, max_blocks=8):
        """Flash up to max_blocks (32K) of the image. Returns True while more remains,
        False once the whole image is written (then call finish()). One SD session per
        step, so the console can repaint the progress bar between steps."""
        if self._f is None or self._part is None:
            return False

        def _do():
            for _ in range(max_blocks):
                n = self._f.readinto(self._buf)
                if not n:
                    return True            # EOF on a block boundary
                if n < BLOCK:              # final partial block: pad with 0xFF (erased)
                    for j in range(n, BLOCK):
                        self._buf[j] = 0xFF
                # writeblocks(idx, buf) with no offset erases the 4K page then writes it.
                self._part.writeblocks(self._block, self._mv)
                self._block += 1
                self.done += n
                if n < BLOCK:
                    return True            # that was the last (partial) block
            return False                   # filled max_blocks, more to go

        try:
            eof = self._with_sd(_do)
        except Exception as exc:
            self.error = _short(exc)
            self.cancel()
            return False
        if eof:
            self._close_file()
        return not eof

    def finish(self):
        """Point the bootloader at the freshly-written slot. The new app boots on the
        next reset and must call mark_valid() to keep it (else rollback)."""
        if self._part is None:
            return False
        try:
            self._part.set_boot()
            return True
        except Exception as exc:
            self.error = _short(exc)
            return False

    def cancel(self):
        self._close_file()
        self._part = None
        self._block = 0
        self.done = 0

    def reset(self):
        import machine

        machine.reset()

    def _close_file(self):
        f = self._f
        self._f = None
        if f is not None:
            try:
                f.close()
            except Exception:
                pass


def _short(exc):
    s = str(exc)
    if not s:
        s = exc.__class__.__name__
    return s[:40]

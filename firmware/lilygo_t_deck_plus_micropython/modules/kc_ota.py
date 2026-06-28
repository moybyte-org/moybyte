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

Phase 3 (#53) adds WiFi download: check_online() fetches a small JSON manifest
({"version", "url", "sha256", "size"}) over HTTP(S) via the injected wifi service,
and if it advertises a newer FIRMWARE_VERSION, download_step()*N streams the .bin
straight to /sd/update (raw socket -> SD, never buffering the whole image in RAM)
while accumulating a SHA-256 to verify before the same Phase-2 install path runs.
The network code is the LIVE counterpart of the host fake -- like DeviceWifi it is
UNVERIFIED on hardware (WiFi + the LCD DMA flush fight for internal RAM, see the
#38 notes in kid_runtime) -- so treat the socket calls as a sketch until a device pass.
"""

UPDATE_DIR = "/sd/update"
BLOCK = 4096                 # esp32.Partition native block (erase page); writeblocks erases
IMAGE_MAGIC = 0xE9          # first byte of an ESP32 app image (esp_image_header_t.magic)

# Released-build identity (mirrors cart versioning). The online check offers an update
# when the manifest is for a DIFFERENT channel than the running build (a deliberate
# stable<->unstable switch -- so a kid can opt into beta and always drop back) OR
# advertises a higher "version" within the SAME channel.
#   FIRMWARE_VERSION -- monotonic build number; bump per stable release. Unstable (beta)
#       builds stamp an auto-incrementing version so every publish reads as newer.
#   FIRMWARE_CHANNEL -- "stable" (master) or "unstable" (dev/beta). The build STAMPS this
#       (and the version) via a generated `_ota_build` module from KIDCODE_OTA_CHANNEL, so
#       the committed default stays "stable" and the channel is a build choice -- clean
#       across merges, not a per-branch source edit.
FIRMWARE_VERSION = 1
FIRMWARE_CHANNEL = "stable"
FIRMWARE_LABEL = None
try:
    import _ota_build                                    # written by build.sh (gitignored)
    FIRMWARE_CHANNEL = getattr(_ota_build, "CHANNEL", FIRMWARE_CHANNEL) or FIRMWARE_CHANNEL
    FIRMWARE_VERSION = int(getattr(_ota_build, "VERSION", FIRMWARE_VERSION))
    FIRMWARE_LABEL = getattr(_ota_build, "LABEL", None) or FIRMWARE_LABEL
except Exception:
    pass

OTA_CFG_NAME = "ota.json"        # /sd/update/ota.json -> {"channels": {"stable": url, ...}}
DOWNLOAD_NAME = "firmware.bin"   # WiFi downloads land here (then the Phase-2 install runs)
DL_CHUNK = 4096                  # socket read / SD write granularity for the streamed image


class OtaUpdater:
    """Stepwise OTA install from an SD .bin into the inactive app slot.

    `with_sd(fn)` runs fn() with the SD card mounted on the live single-bus path and
    the panel DMA drained first (injected by run_desktop). Flash writes themselves
    don't touch the shared SPI bus, but the SD reads do, so the whole read+write of
    each chunk runs inside one with_sd() call.
    """

    def __init__(self, with_sd, wifi=None, go_online=None):
        self._with_sd = with_sd
        self._wifi = wifi         # injected wifi service (DeviceWifi); None -> no online update
        self._go_online = go_online  # callable: best-effort connect from saved creds
        self._buf = bytearray(BLOCK)
        self._mv = memoryview(self._buf)
        self._part = None         # the target (inactive) esp32.Partition
        self._f = None            # the open SD image file (resident across steps)
        self._block = 0           # next block index to write
        self.total = 0            # image size in bytes (for the progress bar)
        self.done = 0             # bytes flashed so far
        self.path = None          # the image being installed
        self.error = None         # last error string (shown by the console)
        # WiFi download (Phase 3) state:
        self._sock = None         # open HTTP(S) socket while a download streams
        self._dl_f = None         # open SD file the download writes to
        self._hash = None         # running sha256 of the downloaded bytes
        self._dl_sha = ""         # expected sha256 (hex) from the manifest
        self.dl_total = 0         # download size in bytes (Content-Length / manifest)
        self.dl_done = 0          # bytes downloaded so far (for the progress bar)

    def set_wifi(self, wifi, go_online=None):
        self._wifi = wifi
        if go_online is not None:
            self._go_online = go_online

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

    def version(self):
        """The running firmware version (compared against the online manifest)."""
        return FIRMWARE_VERSION

    def channel(self):
        """The running release channel ("stable" / "unstable"). A manifest from a
        DIFFERENT channel is always offered (so a kid can switch to beta and back); a
        same-channel manifest is offered only when its version is higher."""
        return FIRMWARE_CHANNEL

    def version_label(self):
        """A human label for the running build: the stamped label ("beta 2026-06-29
        14:30") or "v<n>" for a stable release. Beta versions are an epoch int, so the
        label keeps the update screen readable."""
        return FIRMWARE_LABEL or ("v%d" % FIRMWARE_VERSION)

    def offers(self, manifest, channel=None):
        """Decide whether `manifest` should be offered as an install. True when it's for
        a different channel than the running build (a switch -- including a deliberate
        beta->stable downgrade) OR a newer version within the running channel. `channel`
        is the channel that was checked, used when the manifest omits its own."""
        try:
            mver = int(manifest.get("version", 0) or 0)
        except Exception:
            mver = 0
        mch = manifest.get("channel") or channel or FIRMWARE_CHANNEL
        if mch != FIRMWARE_CHANNEL:
            return True                     # switching channels: always offer
        return mver > FIRMWARE_VERSION      # same channel: only strictly newer

    def online_available(self):
        """True when an online update is possible: OTA-capable build AND a wifi
        service is injected. The console shows the UPDATE ONLINE row only then."""
        return self.available() and self._wifi is not None

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

    # -- WiFi download (Phase 3, #53): manifest check + streamed .bin --------
    #
    # check_online() pulls a small JSON manifest; if it's newer, the console drives
    # begin_download() -> download_step()*N -> download_finish(), which streams the
    # image straight from the socket into /sd/update/firmware.bin (never holding the
    # whole 3MB in RAM) and verifies size + sha256. Then the normal Phase-2 install
    # path takes the downloaded file. UNVERIFIED on hardware (see the class docstring).

    def manifest_url(self, channel=None):
        """The configured manifest URL for `channel` from /sd/update/ota.json, or None.
        Schema: {"channels": {"stable": url, "unstable": url}}; falls back to the running
        channel, then "stable", then any. A legacy {"manifest_url": url} is honoured as
        the single (stable) channel for back-compat."""
        def _read():
            try:
                import json

                with open(UPDATE_DIR + "/" + OTA_CFG_NAME) as f:
                    cfg = json.load(f)
                chans = cfg.get("channels")
                if chans:
                    if channel and chans.get(channel):
                        return chans.get(channel)
                    return (chans.get(FIRMWARE_CHANNEL)
                            or chans.get("stable")
                            or next(iter(chans.values()), None))
                return cfg.get("manifest_url")
            except Exception:
                return None
        try:
            return self._with_sd(_read)
        except Exception:
            return None

    def wifi_online(self):
        if self._wifi is None:
            return False
        try:
            return bool(self._wifi.status()[0])
        except Exception:
            return False

    def ensure_online(self):
        """Best-effort: report connected, else try a saved-credentials autoconnect.
        Never prompts for a password -- the kid joins a network via the WiFi cart;
        this only reuses what's already saved."""
        if self.wifi_online():
            return True
        if self._go_online is not None:
            try:
                self._go_online()
            except Exception:
                pass
        return self.wifi_online()

    def check_online(self, channel=None):
        """Fetch + parse the manifest for `channel` (default the running channel).
        Returns the dict (with at least "version" and "url") or None, setting self.error
        on any failure. Blocking network call -- the console runs it once, between
        frames, behind a CHECKING... screen."""
        self.error = None
        url = self.manifest_url(channel)
        if not url:
            self.error = "no manifest url"
            return None
        if not self.ensure_online():
            self.error = "wifi offline"
            return None
        try:
            txt = self._http_get_text(url)
            if txt is None:
                return None
            import json

            return json.loads(txt)
        except Exception as exc:
            self.error = _short(exc)
            return None

    def begin_download(self, manifest):
        """Open the socket + SD file for the manifest's image. Raises on a bad URL or
        non-200 response; sets dl_total/dl_done for the progress bar."""
        self.error = None
        self.dl_done = 0
        url = manifest.get("url")
        if not url:
            raise ValueError("manifest has no url")
        self.dl_total = int(manifest.get("size", 0) or 0)
        self._dl_sha = (manifest.get("sha256") or "").lower()

        sock, code, clen, rest = self._http_open(url)
        if code != 200:
            try:
                sock.close()
            except Exception:
                pass
            raise ValueError("http %d" % code)
        if not self.dl_total and clen:
            self.dl_total = clen

        import hashlib

        self._hash = hashlib.sha256()
        self._sock = sock

        def _open():
            import os

            try:
                os.mkdir(UPDATE_DIR)
            except OSError:
                pass
            return open(UPDATE_DIR + "/" + DOWNLOAD_NAME, "wb")

        self._dl_f = self._with_sd(_open)
        self.path = UPDATE_DIR + "/" + DOWNLOAD_NAME
        if rest:                               # body bytes already read with the headers
            self._consume(rest)

    def download_step(self, max_bytes=DL_CHUNK):
        """Stream one chunk socket -> SD. Returns True while more remains, False at EOF
        (then call download_finish()). One SD session per step (same as install)."""
        if self._sock is None or self._dl_f is None:
            return False
        try:
            chunk = self._sock.read(max_bytes)
        except Exception as exc:
            self.error = _short(exc)
            self._dl_close()
            return False
        if not chunk:
            return False                       # EOF (server closed the connection)
        try:
            self._consume(chunk)
        except Exception as exc:
            self.error = _short(exc)
            self._dl_close()
            return False
        return True

    def _consume(self, chunk):
        self._hash.update(chunk)

        def _w():
            self._dl_f.write(chunk)

        self._with_sd(_w)
        self.dl_done += len(chunk)

    def download_finish(self):
        """Close the stream and verify size + sha256. Returns the .bin path on success,
        else None with self.error set (and the bad file is left for the kid to inspect)."""
        self._dl_close()
        if self.dl_total and self.dl_done != self.dl_total:
            self.error = "size %d/%d" % (self.dl_done, self.dl_total)
            return None
        if self._dl_sha:
            try:
                import binascii

                got = binascii.hexlify(self._hash.digest()).decode()
            except Exception as exc:
                self.error = _short(exc)
                return None
            if got != self._dl_sha:
                self.error = "sha256 mismatch"
                return None
        return self.path

    def download_cancel(self):
        self._dl_close()
        self.dl_done = 0
        self.dl_total = 0

    def _dl_close(self):
        s = self._sock
        self._sock = None
        if s is not None:
            try:
                s.close()
            except Exception:
                pass
        f = self._dl_f
        self._dl_f = None
        if f is not None:
            def _c():
                f.close()
            try:
                self._with_sd(_c)
            except Exception:
                try:
                    f.close()
                except Exception:
                    pass

    # -- minimal streaming HTTP(S) client (no urequests: it buffers the whole body) --

    def _parse_url(self, url):
        if url.startswith("https://"):
            scheme, rest, port = "https", url[8:], 443
        elif url.startswith("http://"):
            scheme, rest, port = "http", url[7:], 80
        else:
            raise ValueError("bad url")
        slash = rest.find("/")
        if slash < 0:
            hostport, path = rest, "/"
        else:
            hostport, path = rest[:slash], rest[slash:]
        if ":" in hostport:
            host, p = hostport.split(":", 1)
            port = int(p)
        else:
            host = hostport
        return scheme, host, port, path

    def _http_open(self, url):
        """Connect + send GET + read the response headers. Returns
        (sock, status_code, content_length, leftover_body_bytes)."""
        import socket

        scheme, host, port, path = self._parse_url(url)
        ai = socket.getaddrinfo(host, port)[0]
        sock = socket.socket(ai[0], ai[1], ai[2])
        sock.settimeout(15)
        sock.connect(ai[-1])
        if scheme == "https":
            import ssl

            sock = ssl.wrap_socket(sock, server_hostname=host)
        req = ("GET %s HTTP/1.0\r\nHost: %s\r\n"
               "User-Agent: kidcode-ota\r\nConnection: close\r\n\r\n" % (path, host))
        sock.write(req.encode())

        hdr = b""
        while b"\r\n\r\n" not in hdr:
            b = sock.read(1)                   # headers are small; byte-wise keeps body intact
            if not b:
                break
            hdr += b
            if len(hdr) > 4096:
                break
        head, _, rest = hdr.partition(b"\r\n\r\n")
        lines = head.split(b"\r\n")
        code = 0
        if lines and b" " in lines[0]:
            try:
                code = int(lines[0].split(b" ")[1])
            except Exception:
                code = 0
        clen = 0
        for ln in lines[1:]:
            if ln.lower().startswith(b"content-length:"):
                try:
                    clen = int(ln.split(b":", 1)[1].strip())
                except Exception:
                    clen = 0
        return sock, code, clen, rest

    def _http_get_text(self, url, limit=8192):
        """Fetch a small text resource (the manifest) fully into RAM."""
        sock, code, clen, rest = self._http_open(url)
        try:
            if code != 200:
                self.error = "http %d" % code
                return None
            body = rest
            cap = clen if clen else limit
            while len(body) < cap:
                chunk = sock.read(512)
                if not chunk:
                    break
                body += chunk
                if len(body) > limit:
                    break
            return body.decode()
        finally:
            try:
                sock.close()
            except Exception:
                pass


def _short(exc):
    s = str(exc)
    if not s:
        s = exc.__class__.__name__
    return s[:40]

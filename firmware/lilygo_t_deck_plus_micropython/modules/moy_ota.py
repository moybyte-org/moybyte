"""OTA firmware updater for the device (#53): flash a new app image from SD.

The Moybyte build now ships a DUAL-APP partition table (otadata + ota_0 + ota_1,
see build.sh --ota), so the device can write a new firmware image to the INACTIVE
slot and ping-pong between ota_0/ota_1 -- the running slot is never touched, so a
failed/half-written update can't brick the device. Rollback is enabled in the
bootloader (CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE): a freshly-flashed app that
never calls mark_valid() is reverted on the next boot, so a bad image self-heals.

Source of the image is the SD card (kid copies / future #38 WiFi downloads a .bin
to /sd/update/). SD shares the panel's SPI host, so every SD touch goes through the
injected `with_sd` wrapper (moy_runtime's _with_sd_synced -> comp.sync() +
moybyte_sd.with_sd_live) exactly like cart saves -- it drains any in-flight panel
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
#38 notes in moy_runtime) -- so treat the socket calls as a sketch until a device pass.
"""

UPDATE_DIR = "/sd/update"    # the T-Deck default; a board with no SD passes its own
                             # (the P4 stages on its 24MB internal VFS -- see
                             # OtaUpdater(update_dir=...), which every path here reads
                             # off the instance rather than this module constant)
BLOCK = 4096                 # esp32.Partition native block (erase page); writeblocks erases
IMAGE_MAGIC = 0xE9          # first byte of an ESP32 app image (esp_image_header_t.magic)

# What counts as proof that the update worked (confirm_when_healthy). The
# rollback net can only revert an image that never SAID it was fine, so what that
# claim is worth is decided entirely by where it is made -- and "the desktop
# object was constructed" is worth very little. Two conditions, because either
# alone is satisfied by a board nobody would call healthy:
#
#   HEALTHY_PAINTS -- frames that reached the GLASS. This is the one that catches
#       #56, where every boot print appeared and the panel stayed dark. It is 1
#       and cannot be more: the console repaints only when something changes, so
#       an idle desktop paints once and then sits there for as long as you like.
#       (Measured on the P4: 1 painted frame in the first 6 seconds. A threshold
#       of 120 painted frames -- the obvious first guess -- would have left every
#       board on a quiet desktop unconfirmed, i.e. rolled every update back.)
#   HEALTHY_LOOPS -- iterations of the frame loop survived after that. ~2-4s of
#       input polling, timers and compositing on either board; long enough that a
#       crash in the ordinary run of things lands inside it, short enough that
#       nobody power-cycles before it.
HEALTHY_PAINTS = 1
HEALTHY_LOOPS = 120
PENDING_NAME = "pending.json"   # written beside the image at finish(), read at boot
# How long ensure_online() waits for the link AFTER the autoconnect attempt. See
# its docstring: a saved network on the P4 came up 1.5s after connect() had
# already given up and returned False.
ONLINE_WAIT_MS = 12000

# Which board this image is for. An OTA payload is an APP PARTITION image, so it
# is board-specific in the strongest possible way -- handing a P4 an Xtensa S3
# build would write a valid-looking image that cannot boot. The manifest URL
# therefore carries the board (latest-<board>.json), and this is stamped by
# build.sh alongside the channel.
BOARD = "tdeck"

# Released-build identity (mirrors cart versioning). The online check offers an update
# when the manifest is for a DIFFERENT channel than the running build (a deliberate
# stable<->unstable switch -- so a kid can opt into beta and always drop back) OR
# advertises a higher "version" within the SAME channel.
#   FIRMWARE_VERSION -- monotonic build number, bumped per stable release. `make release`
#       (tools/release.py) owns it: the merge of dev into master IS the release, so the
#       bump rides that merge rather than whichever commit happened to land last.
#       Unstable (beta) builds ignore it and stamp a build epoch, so every dev push reads
#       as newer than the last for anyone on the beta channel.
#   FIRMWARE_CHANNEL -- "stable" (master) or "unstable" (dev/beta). The build STAMPS this
#       (and the version) via a generated `_ota_build` module from MOYBYTE_OTA_CHANNEL, so
#       the committed default stays "stable" and the channel is a build choice -- clean
#       across merges, not a per-branch source edit.
FIRMWARE_VERSION = 2            # v2: SD<->display boot fix (#56), Sky Run (#54), the WiFi
                                # OTA online path confirmed on hardware + 4x faster streamed
                                # download (#53). Bump on every stable release.
#   FIRMWARE_NAME -- what a HUMAN calls this release ("0.6"), and the only version anyone
#       outside the code ever reads: the update screen, the manifest label, the git tag.
#       Deliberately separate from FIRMWARE_VERSION above, which exists solely so the
#       device can order two builds with `>` -- it is signed as an int, and betas stamp a
#       build epoch into it, so it can never carry a dotted name. `make release NAME=0.7`
#       sets this; MAJOR.MINOR, with a third component only when a release is purely a fix.
FIRMWARE_NAME = "0.6"
FIRMWARE_CHANNEL = "stable"
FIRMWARE_LABEL = None
try:
    import _ota_build                                    # written by build.sh (gitignored)
    FIRMWARE_CHANNEL = getattr(_ota_build, "CHANNEL", FIRMWARE_CHANNEL) or FIRMWARE_CHANNEL
    FIRMWARE_VERSION = int(getattr(_ota_build, "VERSION", FIRMWARE_VERSION))
    FIRMWARE_LABEL = getattr(_ota_build, "LABEL", None) or FIRMWARE_LABEL
    BOARD = getattr(_ota_build, "BOARD", BOARD) or BOARD
except Exception:
    pass

OTA_CFG_NAME = "ota.json"        # /sd/update/ota.json -> {"channels": {"stable": url, ...}}

# Where each channel lives when the card says nothing. The two branches publish
# one rolling release each (CLAUDE.md -> "Branches and releases"), and CI writes
# `latest.json` beside the app image on both -- so a board straight off the
# flasher can check for updates with no ota.json and no host of the owner's own.
# An /sd/update/ota.json still WINS, which is how a LAN test against
# `make ota-publish-unstable` + `make ota-serve` overrides these.
#   stable   <- master, the tested branch (firmware-latest)
#   unstable <- dev, every push (firmware-beta)
# PER BOARD, because an OTA payload is an app-partition image: the T-Deck's is
# Xtensa and the P4's is RISC-V, and a board handed the other one writes a
# perfectly valid image that cannot boot. One manifest per (channel, board).
_GH = "https://github.com/moybyte-org/moybyte/releases/download"
DEFAULT_CHANNEL_RELEASES = {
    "stable": _GH + "/firmware-latest",
    "unstable": _GH + "/firmware-beta",
}


def default_manifest_url(channel, board=None):
    base = DEFAULT_CHANNEL_RELEASES.get(channel)
    return base and (base + "/latest-%s.json" % (board or BOARD))

# -- manifest signing (the anti-MITM measure) --------------------------------
#
# TLS gets us nothing here on its own: MicroPython's ssl.wrap_socket does no
# certificate verification, so anyone who can answer for github.com on the
# kid's network can serve any firmware they like -- and the manifest's sha256
# cannot help, because the same attacker writes the manifest. So a manifest
# from the BAKED urls above must carry a signature made by the key whose public
# half is here, in the image the owner flashed over a cable.
#
# RSA-2048/SHA-256 PKCS#1 v1.5, chosen for the verifier: pow(sig, 65537, n) is
# ~17 modular squarings of a 2048-bit int, which MicroPython does in C with no
# native module of ours. MEASURED on the P4 (2026-08-02, this exact code run on
# real MicroPython over the serial harness): 35ms for the modexp, 41ms for a
# whole verify_manifest. Expect the T-Deck to be slower -- 240MHz Xtensa against
# the P4's RISC-V -- so budget ~100ms and no more thought than that: it happens
# once per check, behind a CHECKING screen already waiting on the network. (An
# earlier estimate here said "single-digit ms" and was simply wrong; mpz reduces
# by division, which is the cost. Ed25519 remains far worse -- pure-Python
# scalar multiplication runs into seconds.)
# That rests on two build facts, both checked in the tree rather than assumed:
# 3-argument pow is MICROPY_PY_BUILTINS_POW3, which mpconfig.h enables at
# ROM_LEVEL_EXTRA_FEATURES, and the esp32 port sets exactly that level. A port
# built below it would lose modular pow -- and with it, verification.
# (Ed25519 would be the nicer primitive and the wrong one -- pure-Python scalar
# multiplication here runs into seconds.) The signature covers
# channel/version/size/sha256; the image follows from the sha256, which the
# download is checked against. tools/ota_sign.py is the other half.
#
# A TUPLE, not one key, so a compromised key can be rotated by publishing an
# image trusted by the old key and signed by the new one. Empty = unsigned
# builds; see _require_signature.
OTA_PUBLIC_KEYS = (
    (
        'cde3f291071ec24c5c24af208757caf7d06a7f70a42c35435586d3a4d6b20c70'
        'e0f5dadb9b4405eae83e1d86f1410b730d8f59dba0eba47159e6ac60b91c13e9'
        '83da56f5867f8540242bcdb0b9f5c2b9b5bafd1959dddefe7cf42ec75ad92140'
        'fb18eaee715e22eb80754b45f3d4848ed06e8d8d49652da0c3239afced318c69'
        '50b6e55639970340353f32354d4f2537486c89f8129a0553c0c18391be95f73e'
        'e30c0c98decf20ad04abd7c7b74b68bc102502bf9b98f07d22b8fe459ebf2580'
        '2abf721b362b96000eeb8056e8308d45d1d5346cec1434992af3c80abce02366'
        '1aaddd9580585a52a27906314d5d0f71177487a9089e63cf77a79b40b328ec19',
        65537),
)            # ((modulus_hex, exponent), ...) -- see `make ota-keygen`

OTA_SCHEME = "moybyte-ota-v2"    # v2 added `board` -- see _canonical
# The ASN.1 DigestInfo header for SHA-256. Fixed for the algorithm, so it is a
# constant on both sides rather than a parser on either.
_SHA256_DER = b"\x30\x31\x30\x0d\x06\x09\x60\x86\x48\x01\x65" \
              b"\x03\x04\x02\x01\x05\x00\x04\x20"

DOWNLOAD_NAME = "firmware.bin"   # WiFi downloads land here (then the Phase-2 install runs)
DL_CHUNK = 16384                 # bytes streamed (and written to SD in ONE op) per frame.
                                 # The per-frame cost (panel flush + SD sync + repaint) is
                                 # FIXED, so a bigger block amortizes it: 4K/frame crawled
                                 # (~100KB/s), 16K is ~4x. Matches the install step's 32K.


def _ms():
    """A start stamp for _ms_since. ticks_ms on the device, monotonic on the host
    -- moy_ota is imported by the host tests, so every device call in here needs a
    CPython answer too."""
    import time

    try:
        return time.ticks_ms()
    except AttributeError:
        return time.monotonic()


def _ms_since(start):
    """Elapsed ms since a _ms() stamp, wrap-safe on the device (ticks_ms rolls at
    2**30). The two branches are self-consistent: an int start came from ticks_ms,
    a float one from monotonic."""
    import time

    try:
        return time.ticks_diff(time.ticks_ms(), start)
    except AttributeError:
        return int((time.monotonic() - start) * 1000)


class OtaUpdater:
    """Stepwise OTA install from an SD .bin into the inactive app slot.

    `with_sd(fn)` runs fn() with the SD card mounted on the live single-bus path and
    the panel DMA drained first (injected by run_desktop). Flash writes themselves
    don't touch the shared SPI bus, but the SD reads do, so the whole read+write of
    each chunk runs inside one with_sd() call.
    """

    def __init__(self, with_sd, wifi=None, go_online=None, update_dir=None):
        # Where a downloaded/copied image is staged. The T-Deck uses the SD card
        # (its VFS is the card); the P4 has no SD in the console at all and
        # passes a path on its 24MB internal filesystem. Every path in here reads
        # THIS, never the module constant, so two boards' updaters cannot look at
        # each other's directory.
        self.update_dir = update_dir or UPDATE_DIR
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
        self.absent = False       # the channel simply has nothing for this board yet
        self.confirmed = False   # has confirm_when_healthy already fired this boot?
        self._pending_seen = False   # boot_check found a marker -> the confirm clears it
        self._loops = 0           # frame-loop iterations it has been called from
        self.boot_verdict = None  # ("ok"|"rolled_back", text) from the previous install
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
        14:30" / "0.6"), else the release name, else the raw counter. Betas stamp an
        epoch into FIRMWARE_VERSION, so the label is the only readable thing they have
        -- and a stable build's counter is an ordering key nobody should have to read."""
        return FIRMWARE_LABEL or FIRMWARE_NAME or ("v%d" % FIRMWARE_VERSION)

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
        rollback. The raw verb -- callers want confirm_when_healthy, which decides
        WHEN this is honest. No-op (swallowed) when the image was already marked
        valid or this isn't an OTA build."""
        try:
            import esp32

            esp32.Partition.mark_app_valid_cancel_rollback()
            return True
        except Exception:
            return False

    def confirm_when_healthy(self, frames_drawn):
        """The rollback confirm, deferred until the console has actually RUN.

        Marking the image valid where the desktop is CONSTRUCTED confirms firmware
        that has never drawn a pixel -- and a live board showing a black screen is
        exactly the failure this project has already shipped once (#56: every boot
        print appeared, the panel stayed dark). Rollback cannot save a board from a
        fault the firmware promised in advance would not happen.

        So the frame loop calls this every iteration with ws._frames_drawn, and the
        confirm waits for both halves of "it works": something reached the glass
        (HEALTHY_PAINTS) and the loop kept running afterwards (HEALTHY_LOOPS, which
        this counts itself -- one call per iteration is exactly what the loop makes
        it). An image that comes up mute, or comes up and then dies, is never
        confirmed, so the next reset reverts it. Returns True the one frame it
        confirms."""
        if self.confirmed:
            return False
        self._loops += 1
        if frames_drawn < HEALTHY_PAINTS or self._loops < HEALTHY_LOOPS:
            return False
        self.confirmed = True
        ok = self.mark_valid()
        # The pending marker is cleared HERE and not where it was read, so that an
        # image which boots, reports its verdict and then dies still has a marker
        # on the boot after the rollback -- otherwise that second failure would be
        # the silent one. Only when boot_check actually SAW one, though: on the
        # T-Deck this is an SD session on the bus the panel shares, and an
        # ordinary boot (no update pending, which is nearly all of them) should
        # not pay for a delete that can only fail.
        if self._pending_seen:
            self._pending_seen = False
            self._clear_pending()
        return ok

    # -- did the last update actually take? ----------------------------------

    def _pending_path(self):
        return self.update_dir + "/" + PENDING_NAME

    def _arm_pending(self, slot):
        """Record, just before the reboot, which slot the bootloader was pointed at.

        Without this a rollback is SILENT: the board comes back on the old firmware
        and nothing says the update was tried and undone, which reads to a kid as
        "the update did nothing". One small file turns that into a verdict the next
        boot can state out loud. Best-effort -- losing it costs the message, never
        the update."""
        rec = {"slot": slot, "version": self.version(),
               "channel": self.channel(), "label": self.version_label()}

        def _w():
            import json
            import os

            try:
                os.mkdir(self.update_dir)
            except OSError:
                pass                  # already there (the image lives in it)
            f = open(self._pending_path(), "w")
            try:
                f.write(json.dumps(rec))
            finally:
                f.close()

        try:
            self._with_sd(_w)
            return True
        except Exception as exc:
            _log("could not record the pending update:", _short(exc))
            return False

    def _clear_pending(self):
        def _rm():
            import os

            try:
                os.remove(self._pending_path())
            except OSError:
                pass

        try:
            self._with_sd(_rm)
        except Exception:
            pass

    def boot_check(self):
        """Read the marker the previous install left and say what became of it.

        Returns None on an ordinary boot, else ("ok", text) when the slot we were
        pointed at is the one now running, or ("rolled_back", text) when it isn't --
        which means the bootloader gave up on the new image and put the old one
        back. The marker is deliberately NOT deleted here (see
        confirm_when_healthy). Also caches the verdict on `boot_verdict` for the
        update screen."""
        def _read():
            import json

            try:
                f = open(self._pending_path(), "r")
            except OSError:
                return None
            try:
                return json.load(f)
            finally:
                f.close()

        try:
            rec = self._with_sd(_read)
        except Exception:
            return None               # no SD / torn file: no verdict, same as before
        if not isinstance(rec, dict):
            return None
        self._pending_seen = True
        was = rec.get("label") or ("v%s" % rec.get("version", "?"))
        if rec.get("slot") == self.slot():
            self.boot_verdict = ("ok", "%s -> %s" % (was, self.version_label()))
        else:
            self.boot_verdict = ("rolled_back", "put %s back" % self.version_label())
        return self.boot_verdict

    # -- discovery -----------------------------------------------------------

    def find_bin(self):
        """The newest *.bin under /sd/update, as (path, size), or None. SD op."""
        def _scan():
            import os

            try:
                names = os.listdir(self.update_dir)
            except OSError:
                return None
            best = None
            for name in names:
                if not name.lower().endswith(".bin"):
                    continue
                p = self.update_dir + "/" + name
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
        next reset and must confirm itself to keep it (confirm_when_healthy), else
        rollback. Also records WHICH slot, so the next boot can tell whether the
        thing we just installed is the thing now running."""
        if self._part is None:
            return False
        try:
            slot = self._part.info()[4]
            self._part.set_boot()
        except Exception as exc:
            self.error = _short(exc)
            return False
        self._arm_pending(slot)
        return True

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
        """The manifest URL for `channel`: /sd/update/ota.json if it names one, else
        the baked DEFAULT_CHANNEL_URLS (the GitHub release each branch publishes).
        Schema: {"channels": {"stable": url, "unstable": url}}; falls back to the running
        channel, then "stable", then any. A legacy {"manifest_url": url} is honoured as
        the single (stable) channel for back-compat. The card WINS over the default so a
        LAN/offline host stays a one-file override."""
        return self._manifest_source(channel)[0]

    def _manifest_source(self, channel=None):
        """(url, from_card): WHERE the url came from decides whether an unsigned
        manifest is acceptable -- see _require_signature."""
        def _read():
            try:
                import json

                with open(self.update_dir + "/" + OTA_CFG_NAME) as f:
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
            url = self._with_sd(_read)
        except Exception:
            url = None
        if url:
            return url, True
        return default_manifest_url(channel or FIRMWARE_CHANNEL), False

    # -- signature verification ----------------------------------------------

    def _canonical(self, manifest):
        """The bytes a signature covers. MIRRORS tools/ota_sign.canonical -- change
        one and you must change the other (tests/test_ota_signing.py pins that they
        agree). Built by hand rather than as canonical JSON because MicroPython's
        json.dumps has neither sort_keys nor separators, so "serialize the same way
        both sides do" has no meaning over here."""
        return ("%s\n%s\n%s\n%d\n%d\n%s" % (
            OTA_SCHEME,
            manifest.get("board") or "",
            manifest.get("channel") or "",
            int(manifest.get("version") or 0),
            int(manifest.get("size") or 0),
            (manifest.get("sha256") or "").lower(),
        )).encode()

    def verify_manifest(self, manifest, keys=None):
        """True when the manifest carries a signature from a key this image trusts.

        Timed, because the cost of a 2048-bit modexp on this silicon was an
        estimate until a board ran one, and the serial log is the only window this
        board has (its USB RX is dead under the desktop). Once there is a real
        number here, it stops being a question."""
        t0 = _ms()
        ok = self._verify_manifest(manifest, keys)
        _log("verify_manifest ->", ok, "in %dms" % _ms_since(t0))
        return ok

    def _verify_manifest(self, manifest, keys):
        """Whole-block comparison rather than a padding parser: rebuild the PKCS#1
        block that a valid signature must decrypt to and compare it entire. Parsing
        the padding is where the classic signature forgeries live, and there is
        nothing in it worth parsing."""
        sig = manifest.get("sig")
        keys = OTA_PUBLIC_KEYS if keys is None else keys
        if not sig or not keys:
            return False
        try:
            s = int(sig, 16)
        except (TypeError, ValueError):
            return False

        import hashlib

        digest = hashlib.sha256(self._canonical(manifest)).digest()
        for mod_hex, exp in keys:
            try:
                n = int(mod_hex, 16)
            except (TypeError, ValueError):
                continue
            # From the hex, not n.bit_length(): MicroPython has no bit_length.
            k = (len(mod_hex) + 1) // 2
            if not 0 < s < n:
                continue
            tail = _SHA256_DER + digest
            want = b"\x00\x01" + b"\xff" * (k - len(tail) - 3) + b"\x00" + tail
            try:
                got = pow(s, exp, n).to_bytes(k, "big")
            except (OverflowError, ValueError):
                continue
            if got == want:
                return True
        return False

    def _require_signature(self, from_card):
        """Whether an unsigned manifest may be installed.

        A manifest from the BAKED urls must be signed: that is the path an
        attacker on the network gets to answer for. One reached because the owner
        put an ota.json on the card need not be -- choosing a host by physically
        writing to the SD card is an act of consent, and it keeps the LAN dev loop
        (`make ota-publish-unstable`) working with no key to manage. A signature
        that IS present is still checked either way, so a tampered official
        manifest cannot be laundered by copying it to a local host.

        With no keys baked in at all, nothing can be verified, so requiring a
        signature would just brick the update path -- an unsigned build trusts the
        network exactly as it did before signing existed."""
        return bool(OTA_PUBLIC_KEYS) and not from_card

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
        this only reuses what's already saved.

        Then WAIT for the link, because `connect()` returning False does not mean
        the association failed. Measured on the P4 (2026-08-02, saved network,
        from a cold reset): connect() polls isconnected() for 4s and gives up --
        and the link came up 1.5s after it did. The radio is a separate C6 over
        SDIO here, so association from cold simply takes longer than the
        interactive budget, and reporting "wifi offline" on a network that is
        seconds from ready made the online update look broken.

        The wait belongs HERE and not in connect(): this caller has already
        committed to a blocking network round trip behind a CHECKING screen, so
        a few more seconds cost nothing, while lengthening connect() would freeze
        the desktop for every wrong password too."""
        if self.wifi_online():
            return True
        if self._go_online is not None:
            try:
                self._go_online()
            except Exception:
                pass
        import time

        for _ in range(ONLINE_WAIT_MS // 250):
            if self.wifi_online():
                return True
            try:
                time.sleep_ms(250)
            except AttributeError:
                time.sleep(0.25)
        return self.wifi_online()

    def check_online(self, channel=None):
        """Fetch + parse the manifest for `channel` (default the running channel).
        Returns the dict (with at least "version" and "url") or None, setting self.error
        on any failure. Blocking network call -- the console runs it once, between
        frames, behind a CHECKING... screen."""
        self.error = None
        self.absent = False
        _log("check_online channel=%r running=%s/%s" % (
            channel, FIRMWARE_CHANNEL, FIRMWARE_VERSION))
        url, from_card = self._manifest_source(channel)
        _log("manifest_url ->", url, "(from card)" if from_card else "(baked)")
        if not url:
            self.error = "no manifest url"
            _log("ABORT: no /sd/update/ota.json (or no url for channel)")
            return None
        online = self.ensure_online()
        try:
            st = self._wifi.status() if self._wifi is not None else None
        except Exception as exc:
            st = ("status err", exc)
        _log("ensure_online ->", online, "wifi.status=", st)
        if not online:
            self.error = "wifi offline"
            return None
        try:
            txt = self._http_get_text(url)
            _log("manifest body len=", len(txt) if txt is not None else None,
                 "err=", self.error)
            if txt is None:
                # A manifest that isn't there is not a broken update -- it is a
                # channel with nothing published for this board yet, which is
                # the normal state of a channel before its first release. Saying
                # "Update didn't finish, http 404" blames the kid's console for
                # the absence of a file on a server.
                if self.error in ("http 404", "http 410"):
                    _log("no manifest published on this channel for", BOARD)
                    self.error = None
                    self.absent = True
                return None
            import json

            m = json.loads(txt)
            _log("manifest parsed: version=%r channel=%r size=%r" % (
                m.get("version"), m.get("channel"), m.get("size")))

            # Wrong board, wrong silicon. Checked BEFORE the signature so the
            # error says the useful thing: a manifest naming another board is a
            # misconfigured url or a replay, not a tampered file. (A manifest
            # with no board at all is one from before the field existed, and
            # only gets in if it is unsigned-and-allowed anyway.)
            mboard = m.get("board")
            if mboard and mboard != BOARD:
                self.error = "wrong board"
                _log("REJECTED: manifest is for %r, this is %r" % (mboard, BOARD))
                return None

            # The signature gate. A bad one is refused even when a signature was
            # not required: a manifest that carries a signature and fails it has
            # been tampered with, which is worse news than one carrying none.
            signed = self.verify_manifest(m) if m.get("sig") else None
            if signed is False:
                self.error = "bad signature"
                _log("REJECTED: signature present but not from a trusted key")
                return None
            if signed is None and self._require_signature(from_card):
                self.error = "unsigned update"
                _log("REJECTED: unsigned manifest from a baked url")
                return None
            _log("signature:", "verified" if signed else "not required")
            return m
        except Exception as exc:
            self.error = _short(exc)
            _log("manifest fetch/parse FAILED:", self.error)
            return None

    def begin_download(self, manifest):
        """Open the socket + SD file for the manifest's image. Raises on a bad URL or
        non-200 response; sets dl_total/dl_done for the progress bar."""
        self.error = None
        self.dl_done = 0
        url = manifest.get("url")
        _log("begin_download url=", url)
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
        _log("download starting size=%d (rest=%d)" % (self.dl_total, len(rest)))
        self._dl_logged = 0

        import hashlib

        self._hash = hashlib.sha256()
        self._sock = sock

        def _open():
            import os

            try:
                os.mkdir(self.update_dir)
            except OSError:
                pass
            return open(self.update_dir + "/" + DOWNLOAD_NAME, "wb")

        self._dl_f = self._with_sd(_open)
        self.path = self.update_dir + "/" + DOWNLOAD_NAME
        if rest:                               # body bytes already read with the headers
            self._consume(rest)

    def download_step(self, max_bytes=DL_CHUNK):
        """Stream up to max_bytes socket -> SD in ONE write per call. Returns True while
        more remains, False at EOF (then call download_finish()). One SD session per step,
        so the console repaints the bar between steps. Filling a whole block per frame
        (vs the old 4K) is the speed lever -- the per-frame flush/sync/repaint cost is
        fixed, so more bytes per frame divides it down."""
        if self._sock is None or self._dl_f is None:
            return False
        buf = bytearray()
        try:
            while len(buf) < max_bytes:
                chunk = self._sock.read(max_bytes - len(buf))
                if not chunk:
                    break                      # EOF (server closed) -- flush what we have
                buf += chunk
        except Exception as exc:
            self.error = _short(exc)
            _log("download read FAILED at %d/%d:" % (self.dl_done, self.dl_total),
                 self.error)
            self._dl_close()
            return False
        if not buf:
            _log("download EOF at %d/%d" % (self.dl_done, self.dl_total))
            return False                       # clean EOF on a block boundary
        try:
            self._consume(buf)                 # one hash update + one SD write of the block
        except Exception as exc:
            self.error = _short(exc)
            _log("download SD write FAILED at %d/%d:" % (self.dl_done, self.dl_total),
                 self.error)
            self._dl_close()
            return False
        # Progress breadcrumb every ~256K so a stall is visible without spamming serial.
        if self.dl_done - getattr(self, "_dl_logged", 0) >= 262144:
            self._dl_logged = self.dl_done
            _log("download %d/%d" % (self.dl_done, self.dl_total))
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
            _log("download_finish SIZE MISMATCH:", self.error)
            return None
        if self._dl_sha:
            try:
                import binascii

                got = binascii.hexlify(self._hash.digest()).decode()
            except Exception as exc:
                self.error = _short(exc)
                _log("download_finish hash err:", self.error)
                return None
            if got != self._dl_sha:
                self.error = "sha256 mismatch"
                _log("download_finish SHA MISMATCH got=%s want=%s" % (
                    got[:12], self._dl_sha[:12]))
                return None
        _log("download_finish OK bytes=%d ->" % self.dl_done, self.path)
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

    def _http_open(self, url, hops=4):
        """`_http_open_once` + redirect following, which is what makes the
        GitHub-hosted channels (DEFAULT_CHANNEL_URLS) reachable: a release
        download is a 302 to the objects.githubusercontent.com CDN, and the
        manifest beside it redirects the same way. Returns the FINAL response."""
        seen = 0
        while True:
            sock, code, clen, rest, loc = self._http_open_once(url)
            if code not in (301, 302, 303, 307, 308) or not loc or seen >= hops:
                return sock, code, clen, rest
            try:
                sock.close()
            except Exception:
                pass
            seen += 1
            # A relative Location is legal; resolve it against the current host.
            if loc.startswith("/"):
                scheme, host, port, _ = self._parse_url(url)
                dflt = 443 if scheme == "https" else 80
                loc = "%s://%s%s%s" % (scheme, host,
                                       "" if port == dflt else ":%d" % port, loc)
            _log("redirect %d -> %s" % (code, loc))
            url = loc

    def _http_open_once(self, url):
        """Connect + send GET + read the response headers. Returns
        (sock, status_code, content_length, leftover_body_bytes, location)."""
        import socket

        scheme, host, port, path = self._parse_url(url)
        _log("http_open %s host=%s port=%d path=%s" % (scheme, host, port, path))
        ai = socket.getaddrinfo(host, port)[0]
        _log("getaddrinfo ->", ai[-1])
        sock = socket.socket(ai[0], ai[1], ai[2])
        sock.settimeout(15)
        sock.connect(ai[-1])
        _log("connected")
        if scheme == "https":
            import ssl

            sock = ssl.wrap_socket(sock, server_hostname=host)
            _log("tls wrapped")
        req = ("GET %s HTTP/1.0\r\nHost: %s\r\n"
               "User-Agent: moybyte-ota\r\nConnection: close\r\n\r\n" % (path, host))
        sock.write(req.encode())
        _log("request sent, reading headers")

        # Byte-wise on purpose: a chunked read would swallow the first of the
        # body, and this runs twice per update, not per frame.
        #
        # The cap is 16K because GitHub's headers are not small. Its release
        # redirect measured 5147 bytes on 2026-08-02 -- 3626 of them a single
        # Content-Security-Policy header, with the Location we need at byte 95.
        # Under the old 4096 cap that worked only because Location happened to
        # come FIRST; reorder those two headers and the redirect vanishes with
        # no error to show for it. A bytearray + a tail check rather than
        # `hdr += b` and `in`, both of which are O(n^2) over 5K of header.
        t0 = _ms()
        hdr = bytearray()
        while hdr[-4:] != b"\r\n\r\n":
            b = sock.read(1)
            if not b:
                break
            hdr += b
            if len(hdr) > 16384:
                _log("WARNING: header block over 16K, giving up on the rest")
                break
        head, _, rest = bytes(hdr).partition(b"\r\n\r\n")
        lines = head.split(b"\r\n")
        code = 0
        if lines and b" " in lines[0]:
            try:
                code = int(lines[0].split(b" ")[1])
            except Exception:
                code = 0
        clen = 0
        loc = None
        for ln in lines[1:]:
            low = ln.lower()
            if low.startswith(b"content-length:"):
                try:
                    clen = int(ln.split(b":", 1)[1].strip())
                except Exception:
                    clen = 0
            elif low.startswith(b"location:"):
                try:
                    loc = ln.split(b":", 1)[1].strip().decode()
                except Exception:
                    loc = None
        # The header SIZE and the time to read it, because both are guesses until a
        # board reports them: GitHub's redirect measured 5147 bytes from the host,
        # and this reads it one byte at a time through TLS.
        _log("http status=%d content-length=%d hdr=%dB in %dms loc=%s"
             % (code, clen, len(hdr), _ms_since(t0), loc))
        return sock, code, clen, rest, loc

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
    # Keep the errno/class visible: an OSError's str() is often just the bare errno
    # (e.g. "113"), so prefix the class name to make the on-screen + serial text legible.
    s = str(exc)
    cls = exc.__class__.__name__
    if not s:
        s = cls
    elif cls in ("OSError", "ValueError") and s[:1].isdigit():
        s = "%s %s" % (cls, s)
    return s[:48]


def _log(*a):
    # Verbose OTA-online trace to serial (#53). During check_online / download the
    # device is mostly blocked in socket I/O (not the tx_color busy-wait that starves
    # USB), so these prints DO reach a passive `/dev/ttyACM*` reader -- the only window
    # into the WiFi update path, which the native desktop loop otherwise hides. Guarded
    # so a logging hiccup can never break an update.
    try:
        print("Moybyte OTA:", *a)
    except Exception:
        pass

# Map (grep -n a name to jump there):
#   wait_online                      report the link, dialling saved credentials first
#   OtaUpdater                       the Settings screens' updater, over the kernel's
#   C6Updater                        the companion radio's updater (the P4s)
#   verify_sig                       does a signature sign the payload (the reference)
"""The firmware's identity and the updater's Python face.

The updater is the kernel's (native/moy_net/moy_ota.c, docs/kernel_survival_2026-10.md
section 6.3): the streaming HTTP(S) client with redirects, the manifest's
signature under the keys below, the stream into the inactive app slot or the
companion C6, and the image checked whole before anything can boot it. What
stays here is what a screen drives: `OtaUpdater` keeps the surface
`runtime/update_ui.py`, the webhost's `/update` and the Zero's `ZeroUpdate`
pump a step per frame, the boot verdict and the confirm (`moy_ota_health`),
the card's `ota.json` override and the pending marker -- the store's, read
under the board's store gate (`with_sd`).

A board's slot ping-pongs between ota_0 and ota_1 and the bootloader rolls a
never-confirmed image back, so a failed or half-written update cannot brick a
board; the two consents are the download (into an INACTIVE slot, which changes
nothing a board runs) and `finish()`, which makes it the next boot's.
"""

from moy_ota_health import SlotHealth

try:
    import moy_net
except ImportError:              # a host tool reading the identity or verify_sig
    moy_net = None

UPDATE_DIR = "/sd/update"    # the T-Deck default; a board with no SD passes its own

# How long wait_online() waits for the link AFTER the autoconnect attempt. See
# its docstring: a saved network on the P4 came up 1.5s after connect() had
# already given up and returned False.
ONLINE_WAIT_MS = 12000
ONLINE_STEP_MS = 250


def keep_alive():
    """Feed the kernel's task watchdog when the frame loop has armed it: a
    wait that holds the frame (the link wait, a connect's poll) is the console
    waiting on purpose (an update check behind its CHECKING screen, Get Carts'
    connect), and an unfed one past the timeout resets the board. Unarmed, it
    is left alone -- the first feed would subscribe the task."""
    try:
        import moy_kernel
        if moy_kernel.watchdog()[0]:
            moy_kernel.feed()
    except Exception:  # noqa: BLE001 -- a build without the kernel's watchdog
        pass


def wait_online(online, autoconnect=None, wait_ms=ONLINE_WAIT_MS,
                step_ms=ONLINE_STEP_MS):
    """Report the link, dialling saved credentials first and then WAITING.

    The wait is the whole point, and it is measured: `connect()` returning False
    does not mean the association failed. On the P4 (2026-08-02, saved network,
    cold reset) connect() polled isconnected() for 4s and gave up -- and the link
    came up 1.5s AFTER it did, because the radio is a separate C6 over SDIO and
    cold association simply takes longer than the interactive budget. Without the
    wait a perfectly good network reads as "wifi offline", which is what both the
    online update and the WEB CONSOLE row did on their first try.

    It belongs behind a caller that has already committed to a blocking round
    trip behind a CHECKING screen, never inside `connect()`: a few more seconds
    cost that caller nothing, while lengthening connect() would freeze the
    desktop for every wrong password too.

    `online()` answers "is the link up" and `autoconnect()` dials -- both
    zero-argument, so each caller keeps its own idea of what a radio is and what
    an exception from one means. The dial is best-effort: a radio that raises on
    connect may still be associating, and the wait is what finds out.
    """
    if online():
        return True
    keep_alive()
    if autoconnect is not None:
        try:
            autoconnect()
        except Exception:  # noqa: BLE001 -- the wait below decides
            pass
    import time
    sleep_ms = getattr(time, "sleep_ms", None)
    for _ in range(max(1, wait_ms // step_ms)):
        if online():
            return True
        keep_alive()
        if sleep_ms is not None:
            sleep_ms(step_ms)
        else:                              # host / CPython: no sleep_ms
            time.sleep(step_ms / 1000.0)
    return bool(online())


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
FIRMWARE_VERSION = 5            # v5: native tri/sspr/tline kernels on device (#167); manifests declare canvas as the moy-1 string; PICO-8 Lua porter consolidated into moy-spec
#   FIRMWARE_NAME -- what a HUMAN calls this release ("0.6"), and the only version anyone
#       outside the code ever reads: the update screen, the manifest label, the git tag.
#       Deliberately separate from FIRMWARE_VERSION above, which exists solely so the
#       device can order two builds with `>` -- it is signed as an int, and betas stamp a
#       build epoch into it, so it can never carry a dotted name. `make release NAME=0.7`
#       sets this; MAJOR.MINOR, with a third component only when a release is purely a fix.
FIRMWARE_NAME = "0.8"
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

OTA_CFG_NAME = "ota.json"        # <update_dir>/ota.json -> {"channels": {"stable": url, ...}}

# Where each channel lives when the card says nothing. The two branches publish
# one rolling release each (.claude/skills/release/SKILL.md), and CI writes
# `latest-<board>.json` beside the app image on both. An <update_dir>/ota.json
# still WINS, which is how a LAN test against `make ota-publish-unstable` +
# `make ota-serve` overrides these. PER BOARD, because an OTA payload is an
# app-partition image: a board handed another's writes a valid image that
# cannot boot.
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
# TLS verifies no certificate, so anyone who can answer for github.com on the
# kid's network can serve any bytes they like, and the manifest's sha256 cannot
# help when the same attacker writes the manifest. So a manifest from the BAKED
# urls must carry a signature by a key whose public half is here: RSA-2048,
# SHA-256, PKCS#1 v1.5 over the canonical text tools/ota_sign.py defines. The
# board verifies in C (native/moy_net/moy_ota.c, whose KEYS_HEX mirrors this
# tuple; tests/test_ota_signing.py holds the two to each other); `verify_sig`
# below is the same arithmetic in Python, for the host's tools and the wasm
# tier's module signatures.
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

OTA_SCHEME = "moybyte-ota-v2"    # v2 added `board`: tools/ota_sign.canonical
# The ASN.1 DigestInfo header for SHA-256, fixed for the algorithm.
_SHA256_DER = b"\x30\x31\x30\x0d\x06\x09\x60\x86\x48\x01\x65" \
              b"\x03\x04\x02\x01\x05\x00\x04\x20"

# What `download_finish` returns: the bytes are in the sink, not in a file.
SLOT_STAGED = "<slot>"
C6_STAGED = "<c6>"
DL_CHUNK = 16384                 # bytes streamed into the sink per painted frame
INSTALL_CHUNK = 32768            # a copied image's bytes per painted frame
IMAGE_MAGIC = 0xE9               # an ESP32 app image's first byte

# moy_net's sinks and phases (native/moy_net/moy_ota.h).
SINK_SLOT = 1
SINK_C6 = 2
_VERIFIED = 2
AGENT = "moybyte-ota"


class OtaUpdater(SlotHealth):
    """Firmware into the inactive app slot, a step per frame: streamed off the
    wire, or from an image copied into `update_dir`. The kernel holds the
    transfer; this holds what the screen reads.

    `with_sd(fn)` runs fn() inside the board's store session (on the T-Deck:
    the card on the live single-bus path, the panel DMA drained first): every
    read of `update_dir` -- the copied image, ota.json, the pending marker --
    goes through it.
    """

    def __init__(self, with_sd, wifi=None, go_online=None, update_dir=None):
        SlotHealth.__init__(self, with_sd, update_dir or UPDATE_DIR)
        self._wifi = wifi         # the wifi service; None -> no online update
        self._go_online = go_online  # callable: best-effort connect from saved creds
        self._f = None            # a copied image, open across steps
        self._buf = bytearray(INSTALL_CHUNK)
        self.path = None          # the image being installed, or a *_STAGED marker
        self.error = None         # the last failure (what the screen shows)
        self.absent = False       # the channel has nothing for this board yet
        self.from_card = False    # where the last manifest url came from
        self._manifest_text = None

    def set_wifi(self, wifi, go_online=None):
        self._wifi = wifi
        if go_online is not None:
            self._go_online = go_online

    # -- capability, identity, progress -------------------------------------

    def available(self):
        """True when this build has OTA partitions (running slot ota_0/ota_1)."""
        try:
            return self._running_label() in ("ota_0", "ota_1")
        except Exception:
            return False

    def version(self):
        return FIRMWARE_VERSION

    def channel(self):
        return FIRMWARE_CHANNEL

    def version_label(self):
        """The stamped label, else the release name, else the raw counter."""
        return FIRMWARE_LABEL or FIRMWARE_NAME or ("v%d" % FIRMWARE_VERSION)

    def offers(self, manifest, channel=None):
        """Offer a manifest for a DIFFERENT channel (a switch, including a
        deliberate beta->stable downgrade) or a newer version within this one."""
        try:
            mver = int(manifest.get("version", 0) or 0)
        except Exception:
            mver = 0
        mch = manifest.get("channel") or channel or FIRMWARE_CHANNEL
        if mch != FIRMWARE_CHANNEL:
            return True
        return mver > FIRMWARE_VERSION

    def online_available(self):
        return self.available() and self._wifi is not None

    def _state(self):
        return moy_net.ota_state()

    @property
    def dl_done(self):
        return self._state()[2]

    @property
    def dl_total(self):
        return self._state()[3]

    @property
    def done(self):
        return self._state()[4]

    @property
    def total(self):
        return self._state()[5]

    def _failed(self, fallback):
        self.error = self._state()[6] or fallback
        _log(self.error)
        return None

    # -- did the last update take? --------------------------------------------

    def _arm_pending(self, slot):
        """Record, just before the reboot, which slot the bootloader was pointed
        at, so the next boot can say whether the update took or was rolled back
        (a silent rollback reads to a kid as "the update did nothing")."""
        rec = {"slot": slot, "version": self.version(),
               "channel": self.channel(), "label": self.version_label()}

        def _w():
            import json
            import os

            try:
                os.mkdir(self.update_dir)
            except OSError:
                pass
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

    # -- a copied image --------------------------------------------------------

    def find_bin(self):
        """The biggest *.bin under update_dir, as (path, size), or None."""
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

    def begin(self, path):
        """Open a copied image and the slot. Returns its size; raises on an
        empty, oversized or non-app image."""
        import os

        self.error = None
        self.path = path

        def _open():
            size = os.stat(path)[6]
            f = open(path, "rb")
            return f, size

        f, size = self._with_sd(_open)
        try:
            moy_net.ota_slot_begin(size)
        except Exception:
            f.close()
            raise
        self._f = f
        return size

    def step(self, max_bytes=INSTALL_CHUNK):
        """Write the next slice of a copied image. True while more remains,
        False once it is whole (then finish()) or on a failure (error set)."""
        f = self._f
        if f is None:
            return False
        mv = memoryview(self._buf)[:max_bytes]
        try:
            n = self._with_sd(lambda: f.readinto(mv))
        except Exception as exc:
            self.error = _store_short(exc)
            self.cancel()
            return False
        if n and not moy_net.ota_slot_write(mv[:n]):
            self._close_file()
            self._failed("Couldn't save the update.")
            return False
        if n == max_bytes:
            return True
        self._close_file()
        if not moy_net.ota_slot_close():
            self._failed("image refused")
        return False

    def finish(self):
        """Make the verified slot the next boot's, and record which slot, so the
        next boot can tell whether the image now running is the one installed."""
        slot = moy_net.ota_activate()
        if slot is None:
            self._failed("set_boot failed")
            return False
        self._arm_pending(slot)
        return True

    def cancel(self):
        self._close_file()
        moy_net.ota_cancel()

    def reset(self):
        import machine

        machine.reset()

    def _close_file(self):
        f = self._f
        self._f = None
        if f is not None:
            try:
                self._with_sd(f.close)
            except Exception:
                pass

    # -- the online update: manifest, then a stream into the slot ----------

    def manifest_url(self, channel=None):
        """The manifest URL for `channel`: update_dir/ota.json if it names one,
        else the baked release for this board. Schema: {"channels": {"stable":
        url, "unstable": url}}; a legacy {"manifest_url": url} is the one
        channel. The card WINS so a LAN host stays a one-file override."""
        return self._manifest_source(channel)[0]

    def _manifest_source(self, channel=None):
        """(url, from_card): where the url came from decides whether an
        unsigned manifest is acceptable (_require_signature)."""
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

    def _require_signature(self, from_card):
        """Whether an unsigned manifest is refused: one from the BAKED urls must
        be signed; one the owner pointed at by writing ota.json to the card
        need not be (a physical act of consent, and the LAN dev loop). A
        signature that IS present is checked either way. With no keys baked,
        nothing can be verified, and nothing is required."""
        return bool(OTA_PUBLIC_KEYS) and not from_card

    def wifi_online(self):
        if self._wifi is None:
            return False
        try:
            return bool(self._wifi.status()[0])
        except Exception:
            return False

    def ensure_online(self):
        """Connected, else dial saved credentials and wait (`wait_online`)."""
        return wait_online(self.wifi_online, self._go_online)

    def check_online(self, channel=None):
        """The channel's manifest, fetched and judged by the kernel (the board
        it names, then its signature), as a dict; None with `error` set, or
        with `absent` when the channel has nothing for this board yet.
        Blocking: the screen runs it once, behind CHECKING..."""
        self.error = None
        self.absent = False
        self._manifest_text = None
        url, from_card = self._manifest_source(channel)
        self.from_card = from_card
        _log("check channel=%r running=%s/%s url=%s%s" % (
            channel, FIRMWARE_CHANNEL, FIRMWARE_VERSION, url,
            " (from card)" if from_card else ""))
        if not url:
            self.error = "no manifest url"
            return None
        if not self.ensure_online():
            self.error = "wifi offline"
            return None
        rc, text = moy_net.ota_check(url, BOARD, self._require_signature(from_card))
        if rc == 1:
            _log("no manifest published on this channel for", BOARD)
            self.absent = True
            return None
        if rc != 0:
            self.error = text or "no manifest"
            _log("manifest refused:", self.error)
            return None
        import json

        try:
            m = json.loads(text)
        except Exception as exc:
            self.error = _short(exc)
            return None
        self._manifest_text = text
        _log("manifest: version=%r channel=%r size=%r" % (
            m.get("version"), m.get("channel"), m.get("size")))
        return m

    def begin_download(self, manifest, to_slot=True, sink=SINK_SLOT):
        """Open the stream and the sink for the manifest's image (the inactive
        slot, or the C6 for its block). Raises with the reason."""
        self.error = None
        url = manifest.get("url")
        if not url:
            raise ValueError("manifest has no url")
        size = int(manifest.get("size", 0) or 0)
        sha = (manifest.get("sha256") or "").lower()
        _log("download", url)
        try:
            moy_net.ota_dl_begin(url, size, sha, sink)
        except ValueError as exc:
            self.error = str(exc)
            raise
        self.path = C6_STAGED if sink == SINK_C6 else SLOT_STAGED

    def download_step(self, max_bytes=DL_CHUNK):
        """One slice off the wire into the sink: True while more remains."""
        k = moy_net.ota_dl_step(max_bytes)
        if k < 0:
            self._failed("download failed")
        return k > 0

    def download_finish(self):
        """The size and sha256 checked: the staged marker, or None with error."""
        if not moy_net.ota_dl_finish():
            return self._failed("verify failed")
        st = self._state()
        _log("download verified bytes=%d" % st[2])
        return self.path

    def staged_in_slot(self):
        st = self._state()
        return st[0] == _VERIFIED and st[1] == SINK_SLOT

    def download_cancel(self):
        moy_net.ota_cancel()


class C6Updater:
    """The companion C6's firmware (the P4s), pumped by the same screen:
    check -> download, streamed into the radio's own inactive slot as it
    arrives -> commit, which ends the radio's OTA and restarts it. The
    manifest is the board's own (OtaUpdater.check_online fetched and judged
    it); the `c6` block carries its own signature (`c6_sig`, tools/ota_sign.py's
    canonical_c6), required exactly when the manifest's was. After the commit
    the console reboots: its WiFi and BLE hold state against the old radio."""

    def __init__(self, updater):
        self.updater = updater
        self.error = None
        self.offer = None               # the manifest's c6 block, when newer
        self.installed = None           # what the radio said it runs at the check
        self.fl_done = 0
        self.fl_total = 0

    def shim_version(self):
        """What the radio says it runs, or None (a stock slave, an old shim,
        a transport that is down)."""
        return moy_net.ota_c6_version()

    def check(self, channel=None):
        """"offer" (self.offer set), "uptodate", "nopublish" or "error"."""
        self.error = None
        self.offer = None
        u = self.updater
        if u is None:
            self.error = "no updater"
            return "error"
        m = u.check_online(channel)
        if m is None:
            if getattr(u, "absent", False):
                return "nopublish"
            self.error = u.error or "no manifest"
            return "error"
        c6 = m.get("c6")
        if not c6:
            return "nopublish"
        why = moy_net.ota_judge_c6(u._manifest_text,
                                   u._require_signature(u.from_card))
        if why:
            self.error = why
            return "error"
        try:
            want = int(c6.get("version") or 0)
        except (TypeError, ValueError):
            self.error = "bad c6 version"
            return "error"
        self.installed = self.shim_version()
        if self.installed is not None and self.installed >= want:
            return "uptodate"
        self.offer = c6
        return "offer"

    def begin_download(self):
        self.updater.begin_download(self.offer, sink=SINK_C6)

    def download_step(self):
        return self.updater.download_step()

    def download_finish(self):
        path = self.updater.download_finish()
        if path is None:
            self.error = self.updater.error or "verify failed"
        return path

    def download_cancel(self):
        self.updater.download_cancel()

    @property
    def dl_done(self):
        return self.updater.dl_done if self.updater else 0

    @property
    def dl_total(self):
        return self.updater.dl_total if self.updater else 0

    # The image went into the radio as it arrived: "flashing" is the commit.
    def begin_flash(self, path):
        self.error = None
        self.fl_total = self.fl_done = self.updater.dl_done

    def flash_step(self):
        return False

    def finish_flash(self):
        return True

    def activate(self):
        """End the radio's OTA and restart it into the verified image."""
        if moy_net.ota_c6_commit():
            return True
        self.error = moy_net.ota_state()[6] or "c6 activate failed"
        return False

    def cancel(self):
        self.download_cancel()


def _short(exc):
    # An OSError's str() is often the bare errno; prefix the class name.
    s = str(exc)
    cls = exc.__class__.__name__
    if not s:
        s = cls
    elif cls in ("OSError", "ValueError") and s[:1].isdigit():
        s = "%s %s" % (cls, s)
    return s[:48]


ENOSPC = 28
NO_ROOM = "Not enough room for it."
NO_WRITE = "Couldn't save the update."


def _store_short(exc):
    """What the screen says when reading or writing the image fails."""
    if not isinstance(exc, OSError):
        return _short(exc)
    code = getattr(exc, "errno", None)
    if code is None and exc.args:
        code = exc.args[0]
    return NO_ROOM if code == ENOSPC else NO_WRITE


def _log(*a):
    try:
        print("Moybyte OTA:", *a)
    except Exception:
        pass


def verify_sig(payload, sig, keys=None):
    """True when `sig` (hex) validly signs `payload` under a baked key: the
    whole PKCS#1 v1.5 block rebuilt and compared, never a padding parser. The
    kernel's moy_ota_verify on a board; this arithmetic where there is none."""
    if keys is None and moy_net is not None and hasattr(moy_net, "ota_verify"):
        return bool(sig) and moy_net.ota_verify(payload, sig)
    keys = OTA_PUBLIC_KEYS if keys is None else keys
    if not sig or not keys:
        return False
    try:
        s = int(sig, 16)
    except (TypeError, ValueError):
        return False

    import hashlib

    digest = hashlib.sha256(payload).digest()
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

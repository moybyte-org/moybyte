"""The Zero: a pocketable cart-store the browser console pairs with (#41).

This is the re-based Zero (moycore plan 3.2, owner call 2026-08-12): no panel,
no console, no canvas -- the BROWSER runs the whole console (the wasm head),
and this board contributes exactly what a browser cannot: the kid's cart store
on its own flash, served and SYNCED over WiFi, and its PINS (#9). It is
deliberately the smallest possible host of the 3.4 sync RPC:

    boot -> join WiFi (creds in /moy/wifi.json, the console's own shape)
         -> WebHost(/moy/carts, /moy/web) + /gpio   [the SAME class the boards
         -> poll() forever                           inject, one endpoint more]

    ...no joinable network
         -> zero_setup.run()  the SoftAP + form a first boot is configured
                              through, which reboots back into the line above

`moy_webhost` serves the wasm bundle, streams the store as carts.json, and
applies POST /sync batches back into /moy/carts. A cart made in the browser
lands on this flash about a second after its commit, and is still there --
served back -- on the next visit. That is the whole product: a console in your
pocket whose screen is whatever browser is nearby.

Since 2026-08-29 the bundle comes from THIS IMAGE (native/moy_web, the same
627KB .incbin every other board carries), with a pushed copy in /moy/web still
winning if one is there. Until that day this board had no image of its own, so
the baked half was structurally unreachable here and a hand-pushed copy was the
only source -- which is precisely the silent drift baking it was introduced to
end, and this board was the last place it survived. `serve()` prints which of
the two it is about to serve, for the same reason.

`POST /gpio` is the second half of the same idea (zero_gpio): the cart in the
browser calls `pin_write`, the page batches it here, and this board -- which
has the pins -- does the driving.

`GET/POST /update` is the third (ZeroUpdate, below). The ROUTES are
`moy_webhost`'s now, on every board (2026-08-29); what is this board's own is
the BACKEND behind them -- the OTA flow every other board runs off its own
Settings screen, driven here by a state machine in the poll loop because this
board has no screen to put it on. The browser console this board serves is
where a person triggers it and watches it, which is the whole point of the
endpoints being pin-gated JSON rather than a UI.

Runs on the board's own frozen image (`build.sh` -> MOYBYTE_ZERO). STA is the
SERVING mode: the SoftAP lane measured too weak for the old streaming view and
is not re-attempted for serving megabyte bundles -- setup is the one thing it
does, because a board nobody can configure is worse than a board that serves
slowly for a minute.
"""

import json
import os
import time

try:
    import network
except ImportError:                  # host: this module is import-only there
    network = None

import zero_gpio

ROOT = "/moy"
CARTS_DIR = ROOT + "/carts"
WEB_DIR = ROOT + "/web"
WIFI_STORE = ROOT + "/wifi.json"
ZERO_STORE = ROOT + "/zero.json"   # this board's own name + write pin (setup)
# Where an OTA payload is staged. On the internal VFS because there is no card
# slot on this board at all -- moy_ota's default names /sd/update, which every
# path in that module reads off the instance instead, precisely so two boards'
# updaters cannot look at each other's directory.
UPDATE_DIR = ROOT + "/update"
HOSTNAME = "moybyte-zero"          # mDNS: a headless board needs a findable name


def _mkdir(path):
    try:
        os.mkdir(path)
    except OSError:
        pass


def _networks():
    """The known-networks list out of /moy/wifi.json -- the same document the
    console's WiFi panel writes ({"networks": [{"ssid", "password"}]}), so a
    card/creds file moved from a console board just works."""
    try:
        with open(WIFI_STORE) as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return []
    nets = doc.get("networks") if isinstance(doc, dict) else doc
    return [n for n in (nets or []) if isinstance(n, dict) and n.get("ssid")]


def identity():
    """This board's own name and write PIN (/moy/zero.json), or {}.

    Written by first-run setup and by nothing else. Absent on a board whose
    credentials were pushed over USB, which is why every reader defaults: the
    provisioning path is not going away just because the AP path exists.
    """
    try:
        with open(ZERO_STORE) as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


# -- the seed roster, carried by the image (2026-08-30) ----------------------
#
# WHAT THIS REPLACED. Until this landed, a Zero got its carts from
# `provision.sh` over a USB cable and from nowhere else -- the script's own
# header called it "what an image cannot carry: a kid's carts". The plain roster
# the console boards freeze very nearly could not be carried here, and both
# forms were BUILT to find out: with it this image is 2,830,672 B of a
# 2,883,584 B OTA slot and leaves 51 KB -- under the #168 warning floor, one
# cart from a build failure, in a slot paid for twice. Compressed the image is
# 2,399,232 B and leaves 473 KB. `carts_data.CARTS_Z` is the same 35 carts as
# one raw-deflate stream each, and this function inflates them.
#
# It matters beyond the cable, because the website's flasher can write this
# board (site/build.py's BOARDS): a person who flashed a Zero from a web page
# got an empty console and no hint that a second, cabled step existed. There is
# no second step now.
#
# ONLY WHAT IS MISSING, and this board is where that rule differs from the
# console boards'. On a console board the store is a CACHE of the image's
# built-ins, so #47 re-seeds a cart whose baked version is newer and accepts
# that on-device edits to a built-in's code are discarded. Here the store is the
# RECORD -- it is the only copy of a cart a kid made in the browser, it has a
# `moy_journal` history behind it, and `seed_builtins` names a folder by the
# TITLE slug, so a kid's edited "Hop Quest" is exactly what a version bump would
# overwrite. So the gate is PRESENCE, not version.
#
# It was EMPTINESS until 2026-08-30, gating the whole roster on the store having
# no cart at all, and that was too coarse in both directions. A cart that is not
# there has nothing to overwrite, so refusing to write it protects nothing --
# and the two things it cost were real: a new built-in could never reach a Zero
# that had been used once (`Pin Light` shipped and no Zero could receive it),
# and a seed interrupted by a power cut left a part-seeded store that the next
# boot read as "not empty" and never finished. Per-cart presence fixes both and
# gives up nothing: a kid's cart is still never rewritten.


def store_is_empty(root=CARTS_DIR):
    """Is there no cart at all under `root`?

    `.moy` folders only. A store holding just the sidecars a sync leaves behind
    (`.history/`, a stray `journal/`) has no carts in it and should still be
    seeded; a store holding one cart should not.
    """
    try:
        names = os.listdir(root)
    except OSError:                      # no store yet -> nothing to protect
        return True
    for name in names:
        if name.endswith(".moy"):
            return False
    return True


def seed_carts(root=CARTS_DIR):
    """Write any of the image's baked carts that this store is MISSING.

    Returns the count written -- 0 on the ordinary warm boot, where this walks
    the roster doing one directory stat per cart and inflates nothing.

    Best-effort in every direction, because none of the ways this can fail may
    cost the board its job -- which is serving whatever carts it DOES have. An
    image built without a roster (the module is generated, so a hand-assembled
    tree may have none), a full filesystem, a corrupt blob: each prints one
    `ZERO seed:` line and returns, because serial is this board's only display.

    TWO CONSEQUENCES, both said out loud because neither is obvious:

      - a built-in a kid DELETED comes back on the next boot, because "missing"
        is all this can see and a deletion leaves exactly that. The alternative
        is a tombstone file, which is a store format change for a case a reflash
        also undoes.
      - a built-in that ships BROKEN cannot be fixed in place here. The console
        boards take a #47 version bump and replace their copy; this board reads
        presence and will not, so a new image with the fix in it changes
        nothing. That is the price of never overwriting a kid's cart, and it is
        the right way round on the board holding the only copy -- but it means
        the recovery is manual and worth knowing. It needs no cable: the sync
        protocol already has a whole-cart delete, and the pin rides in the BODY
        for a POST (a `?pin=` on this endpoint is refused -- moy_webhost reads
        `doc["pin"]`, which is what the protocol envelope declares):

            curl -X POST http://<board>/sync -H "Content-Type: application/json" \
                 -d '{"v":1,"pin":"NNNN","ops":[{"p":"hop_quest.moy","dc":1}]}'

        That removes it, journal and all -> `{"err": [], "ok": 1}`. The next
        BOOT seeds the image's copy, so it still wants a power cycle; there is
        no reset endpoint. `./provision.sh --carts` forces the push over USB if
        the board is in your hand -- but prefer the POST: an `mpremote` command
        stops the console, and getting back out of that has its own failure mode
        (the README's hardware notes).

    It bit immediately. `Pin Light` shipped crashing on its first frame
    (2026-08-30, colour names where the draw verbs take indices), and the three
    console boards took the fix from a version bump while this one had to have
    the folder removed.
    """
    try:
        from carts_data import CARTS_Z
    except ImportError:
        print("ZERO seed: no roster baked into this image")
        return 0
    try:
        import moy_carts
        # `ticks` and not `time.ticks_ms` directly: this module is import-only
        # on the host and the host suites drive this function, where
        # MicroPython's clock does not exist. One shim, the tree's own
        # (runtime/ticks.py), which this board already freezes for the
        # transport's blocking budgets.
        import ticks

        moy_carts.ensure_dirs(root)
        # SAID BEFORE it starts, not only after. This runs before the radio and
        # writes ~763 KB across 155 files to the internal VFS, so on a first
        # boot it is the longest silence in the log -- and on a board with no
        # screen a silence is what a hang looks like.
        missing = sum(1 for t, _v, _b in CARTS_Z
                      if not moy_carts._exists(root + "/" + moy_carts.slug(t) + ".moy"))
        if missing:
            print("ZERO seed: inflating %d carts from the image" % missing)
        started = ticks._ticks_ms()
        written = moy_carts.seed_packed(CARTS_Z, root, only_new=True)
        elapsed = ticks._ticks_diff(ticks._ticks_ms(), started)
        if not written:
            return 0
    except Exception as exc:             # noqa: BLE001 -- a partial store beats none
        print("ZERO seed: FAILED (the store may be partly seeded):", exc)
        return 0
    print("ZERO seed: %d carts inflated from the image in %d ms"
          % (written, elapsed))
    return written


def connect(wait_ms=15000, hostname=None):
    """Join the first known network that answers. Returns the STA IP or None.
    The wait is per-network and generous for the same reason moy_ota's
    ensure_online waits: cold association routinely outlives a short poll."""
    try:
        network.hostname(hostname or HOSTNAME)
    except Exception:                    # noqa: BLE001 -- older port: no mDNS
        pass
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    if sta.isconnected():
        return sta.ifconfig()[0]
    for net in _networks():
        try:
            sta.connect(net["ssid"], net.get("password") or net.get("key") or "")
        except OSError:
            continue
        for _ in range(wait_ms // 250):
            if sta.isconnected():
                print("ZERO wifi:", net["ssid"], sta.ifconfig()[0])
                return sta.ifconfig()[0]
            time.sleep_ms(250)
        print("ZERO wifi: no answer from", net["ssid"])
    return None


# -- OTA on a board with no screen (#53 / #41, 2026-08-29) -------------------
#
# Every other board triggers and reports an update on its own glass: Settings ->
# UPDATE ONLINE draws a CHECKING screen, a progress bar, and the verdict of the
# last install. This board has no glass, so the SAME OtaUpdater is driven by the
# state machine below and reported as JSON on two pin-gated endpoints.
#
# THE DECISION, stated here because the README points at it: the TRIGGER is a
# request, not a timer. A boot-time CHECK runs (once, after the running image
# has certified itself) and its result is printed on serial and cached for
# `GET /update`; it does not install. Installing is `POST /update` carrying the
# pin -- the same act of consent that gates every other write to this board.
#
# THERE IS NO EXCEPTION, and there was one until 2026-08-29 (owner call): an
# `"ota_auto": true` in /moy/zero.json made the boot check install what it
# found. It is deleted. No other board in this tree has any auto-install
# concept -- a console board takes two deliberate human acts, opening the
# update screen in Settings and confirming -- so this was a one-board
# divergence, on the board holding the only local copy of a kid's carts,
# down a path that had never run on hardware. Nothing wrote the flag either:
# reaching it meant hand-editing a JSON file over the cable.
#
# WHERE THE REQUEST COMES FROM instead (landed 2026-08-29): the browser
# console this board serves. The browser IS this board's screen, so it is
# where "update this board" belongs, and it POSTs the same /update this file
# already gated -- now through moy_webhost, which serves the route on every
# board. On a board WITH glass that POST hands the glass back instead
# (moy_webhost.ConsoleUpdate); this board is the one that genuinely needs the
# browser to display the progress, because it has no other display.
#
# HOW A HUMAN LEARNS IT HAPPENED, all three ways, none of them a UI:
#   * serial -- every transition prints a `ZERO ota:` line, and the boot line
#     names the running label; the cable is how a pin reaches a human on this
#     board anyway.
#   * `GET /update?pin=NNNN` -- the running version/label/channel/slot, the
#     PREVIOUS install's verdict (moy_ota.boot_check: installed, or rolled back
#     by the bootloader), and live progress.
#   * the page itself -- the browser console is baked into the image, so a
#     board that updated is serving a different console the next time it is
#     opened.


class _ZeroWifi:
    """The two-method wifi service `moy_ota` asks for, over this board's own
    `connect()`.

    NOT `device_wifi.DeviceWifi`: that class is the console's WiFi FEATURE --
    scanning, saved-network management, the credentials a Settings panel
    writes -- and it reads its store through `moy_carts`' system store, which
    is not where this board keeps `/moy/wifi.json`. Everything moy_ota needs is
    `status()[0]` (is the link up) and something to call when it is not. Both
    already exist here; this is the adapter, not a second implementation.
    """

    def status(self):
        """(connected, ssid, ip) -- the shape moy_ota.wifi_online() indexes."""
        try:
            sta = network.WLAN(network.STA_IF)
            if not sta.isconnected():
                return (False, None, None)
            return (True, sta.config("essid"), sta.ifconfig()[0])
        except Exception:                # noqa: BLE001 -- a radio mid-reassoc
            return (False, None, None)


class ZeroUpdate:
    """The OTA flow as a state machine driven one slice per poll iteration.

    Why a state machine and not a blocking install: `POST /update` has to
    ANSWER. A 2MB download plus a flash write is tens of seconds, and a socket
    held open across it would make every failure look identical (a browser
    timeout) whether the board died, the manifest was refused, or it worked.
    So the POST queues, returns immediately, and `GET /update` is where the
    answer lives -- which is also the only shape that can report a board that
    is about to reboot out from under the connection.

    States: idle -> checking -> {offer | none | error}
                             -> downloading -> ready -> installing -> reboot

    `ready` is the pause between the two acts of consent (2026-08-29). The
    browser console this board serves runs the SHARED update screen, which asks
    once before the download and again before the flash -- so the board has to
    be able to stop in between, or the second confirm would gate nothing.
    """

    def __init__(self, updater):
        self.ota = updater
        self.state = "idle"
        self.error = None
        self.manifest = None
        # Was the fetched manifest actually OFFERED? Kept apart from `manifest`
        # because a manifest that was judged not-newer is still what the check
        # FOUND, and the screen draws "UP TO DATE" from it.
        self.offered = False
        self.staged = None         # the downloaded image, waiting for `install`
        self._channel = None       # the channel the request asked us to check
        # "nothing is published on this channel for this board yet" -- the
        # normal state of a channel before its first release. Carried
        # separately from `state` because "none" covers it AND "up to date",
        # and those are two different sentences to say to a person.
        self.nothing_published = False
        self._want = None          # queued action, applied by the next step()
        self._checked = False      # has the once-per-boot check run?
        self._reboot_in = 0        # poll iterations left before machine.reset()

    # -- the request side -----------------------------------------------------

    def request(self, action, channel=None):
        """Queue `check`, `download`, `install` or `cancel`. (ok, message).

        THREE VERBS, ONE PER ACT (2026-08-29). `install` used to mean the whole
        job -- check, download and flash off one request -- which is the one
        shape the shared update screen cannot mirror: that screen asks TWICE,
        once before the download and again before the flash, and a board that
        did both off the first tap would leave the second confirm gating
        nothing. So `download` fetches and STOPS at `ready`, and `install`
        flashes what is staged. `download` still checks first when it has to,
        which is the convenience the old chained form existed for.

        THE STATE MOVES HERE, not in step(). The WORK still happens in the poll
        loop and this still answers immediately -- but a status read landing in
        between would otherwise report the state the request has not been
        applied to yet, and a remote screen polling that reads it as an answer.

        `channel` is what the caller asked us to look at (the browser's CHANNEL
        row). None keeps the running build's own channel, which is what every
        boot check and every curl without one means.
        """
        if action not in ("check", "download", "install", "cancel"):
            return False, "action must be check, download, install or cancel"
        if action == "cancel":
            self._cancel()
            return True, "cancelled"
        if self.state in ("checking", "downloading", "installing", "reboot"):
            return False, "busy: " + self.state
        if action == "install":
            if self.state != "ready":
                return False, "nothing downloaded yet -- ask for download first"
            self._want = "install"
            self.state = "installing"
            return True, "queued"
        self._channel = channel or None
        if action == "download" and self.state == "offer":
            self._want = "download"
            self.state = "downloading"
            return True, "queued"
        self._want = "check+download" if action == "download" else "check"
        self.state = "checking"
        return True, "queued"

    def boot_check(self):
        """Read the previous install's verdict and say it out loud. Runs once,
        at boot, before anything is served."""
        try:
            verdict = self.ota.boot_check()
        except Exception as exc:         # noqa: BLE001
            print("ZERO ota: boot check failed:", exc)
            return None
        if verdict:
            print("ZERO ota: last update %s -- %s" % verdict)
        return verdict

    # -- the pumped side ------------------------------------------------------

    def step(self):
        """One slice. Called every poll iteration; returns True if it did work.

        The check and the manifest fetch BLOCK (a socket round trip), which is
        acceptable here and would not be on a console: this loop's only other
        duty is `host.poll()`, and a store request arriving during a 300ms
        manifest fetch is a store request served 300ms late.
        """
        if self._reboot_in:
            self._reboot_in -= 1
            if self._reboot_in == 0:
                print("ZERO ota: rebooting into the new slot")
                self.ota.reset()
            return True
        want, self._want = self._want, None
        if want in ("check", "check+download"):
            found = self._check()
            if found and want == "check+download":
                self._begin_download()
            return True
        if want == "download":
            self._begin_download()
            return True
        if want == "install":
            self._begin_install()
            return True
        if self.state == "downloading":
            return self._download_slice()
        if self.state == "installing":
            return self._install_slice()
        return False

    def boot_check_once(self):
        """Queue the once-per-boot manifest check. It LOOKS and never installs
        -- installing takes a request carrying the pin, and there is no setting
        that changes that (see the ota_auto note in this module's header).

        Deliberately called only after the running image has been CONFIRMED
        (see serve()): looking for the next firmware before the current one has
        certified itself is how a board chases a bad update round a rollback
        loop."""
        if self._checked:
            return False
        self._checked = True
        self._want = "check"
        self.state = "checking"
        return True

    # -- the phases -----------------------------------------------------------

    def _check(self):
        self.state = "checking"
        self.error = None
        self.manifest = None
        self.offered = False
        self.staged = None
        self.nothing_published = False
        m = self.ota.check_online(self._channel)
        if m is None:
            if self.ota.absent:
                self.state = "none"
                self.nothing_published = True
                print("ZERO ota: no build published on this channel yet")
            else:
                self.state = "error"
                self.error = self.ota.error or "check failed"
                print("ZERO ota: check failed --", self.error)
            return False
        # KEPT EITHER WAY. A manifest that was fetched and judged not-newer
        # is still what the check found, and it is what the screen draws "UP TO
        # DATE" from -- `offered` is the verdict, `manifest` is the evidence.
        self.manifest = m
        if not self.ota.offers(m, self._channel):
            self.state = "none"
            print("ZERO ota: up to date (%s)" % self.ota.version_label())
            return False
        self.offered = True
        self.state = "offer"
        print("ZERO ota: %s available (%s, %d bytes)"
              % (m.get("label", "?"), m.get("channel", "?"),
                 int(m.get("size", 0) or 0)))
        return True

    def _begin_download(self):
        if not self.manifest or not self.offered:
            self.state = "error"
            self.error = "nothing to install"
            return
        try:
            self.ota.begin_download(self.manifest)
        except Exception as exc:         # noqa: BLE001 -- bad url / non-200
            self.state = "error"
            self.error = self.ota.error or str(exc)
            print("ZERO ota: download refused --", self.error)
            return
        self.state = "downloading"
        print("ZERO ota: downloading %d bytes" % self.ota.dl_total)

    def _download_slice(self):
        if self.ota.download_step():
            return True
        path = self.ota.download_finish()
        if path is None:
            self.state = "error"
            self.error = self.ota.error or "download failed"
            print("ZERO ota: download failed --", self.error)
            return True
        # STOP HERE. The flash is the SECOND act of consent and it has not been
        # given yet: the image sits verified on the filesystem until an
        # `install` request arrives, and the running slot is untouched until
        # then. This is where the browser's update screen draws its second
        # confirm, exactly as a board's does with an image found on a card.
        self.staged = path
        self.state = "ready"
        print("ZERO ota: downloaded and verified -- waiting for install")
        return True

    def _begin_install(self):
        path = self.staged
        if not path:
            self.state = "error"
            self.error = "nothing downloaded"
            return
        try:
            total = self.ota.begin(path)
        except Exception as exc:         # noqa: BLE001 -- not an app image
            self.state = "error"
            self.error = str(exc)
            print("ZERO ota: image refused --", self.error)
            return
        self.state = "installing"
        print("ZERO ota: writing %d bytes into the inactive slot" % total)

    def _install_slice(self):
        # step() returns True WHILE MORE REMAINS (.claude/rules/ota.md: an
        # inverted read here writes a truncated image whose set_boot is then
        # correctly refused).
        if self.ota.step():
            return True
        if self.ota.error:
            self.state = "error"
            self.error = self.ota.error
            print("ZERO ota: install failed --", self.error)
            return True
        if not self.ota.finish():
            self.state = "error"
            self.error = self.ota.error or "set_boot failed"
            print("ZERO ota: could not point the bootloader --", self.error)
            return True
        self.state = "reboot"
        # A short grace before the reset, in poll iterations rather than a
        # sleep: it keeps serving, so the GET that asked for this can still
        # read `"state": "reboot"` and know the board is not simply gone. The
        # bootloader's rollback is the net on the other side -- the new image
        # has to confirm itself (moy_ota.confirm_when_serving) or the next
        # reset puts this one back.
        self._reboot_in = 200
        print("ZERO ota: installed -- rebooting shortly")
        return True

    def _cancel(self):
        try:
            self.ota.download_cancel()
            self.ota.cancel()
        except Exception:                # noqa: BLE001
            pass
        self.state = "idle"
        self.manifest = None
        self.offered = False
        self.staged = None
        self.error = None
        self.nothing_published = False
        self._channel = None
        self._reboot_in = 0

    # -- what a human reads ---------------------------------------------------

    def status(self):
        """This board's half of the shared /update document.

        Built through `moy_webhost.update_status`, which both backends call:
        the page reads ONE shape, and the fields that mean the same thing on
        every board (the running firmware, the previous install's verdict)
        cannot drift between them. What is this board's own is the state word,
        the progress numbers -- which come from a DIFFERENT pair of counters in
        each phase -- and `screen=False`, the hardware fact that makes this
        document the only progress report that exists here.

        The import is local because importing this module must stay free of
        everything that only works on a board (moy_webhost pulls in the socket
        transport); this runs at most once per poll answering a request.
        """
        from moy_webhost import update_status

        ota = self.ota
        offer = None
        if self.manifest:
            offer = {
                "version": self.manifest.get("version"),
                "label": self.manifest.get("label"),
                "channel": self.manifest.get("channel"),
                "size": self.manifest.get("size"),
            }
        progress = None
        if self.state in ("downloading", "ready"):
            # `ready` reports the FINISHED transfer, not a fresh zero: the
            # screen's second confirm prints how big the thing it is about to
            # install is, and that number is this one.
            progress = {"done": ota.dl_done, "total": ota.dl_total}
        elif self.state in ("installing", "reboot"):
            progress = {"done": ota.done, "total": ota.total}
        return update_status(ota, self.state, False, error=self.error,
                             offer=offer, progress=progress,
                             absent=self.nothing_published,
                             staged=self.staged)


def _frozen_or_pushed(names=("zero_host", "zero_gpio", "zero_setup",
                             "moy_webhost", "moy_sync", "moy_carts")):
    """Which of the image's own modules a PUSHED copy is shadowing.

    MicroPython searches the filesystem root before `.frozen`, so a stale
    `/moy_sync.py` left over from the pre-image provisioning arrangement wins
    silently and forever. That is the same bug the web bundle has one level
    down -- storage wins, so `start()` says which copy it is serving -- and it
    is worth one line at boot for exactly the same reason.
    """
    out = []
    for name in names:
        try:
            os.stat("/" + name + ".py")
            out.append(name)
        except OSError:
            pass
    return out


def zero_host_class():
    """`WebHost` + this board's /gpio, built on first use.

    A function because the subclass cannot exist before its base is imported,
    and `moy_webhost` is imported inside `serve()` on purpose -- importing this
    module must stay free of everything that only works on the board.
    """
    from moy_webhost import WebHost

    class ZeroHost(WebHost):
        """The shared store host plus THIS board's pins.

        A subclass and not an edit to `moy_webhost`, because /gpio is not a
        console-board endpoint: the other three boards spend their GPIOs on a
        panel, a touch controller and an SD card, and would be handing out the
        pins their own screen is drawn through. The Zero has a spare header and
        no screen, which is the whole difference.
        """

        def __init__(self, *a, **kw):
            WebHost.__init__(self, *a, **kw)
            self._pins = None       # built on the first /gpio, not at boot: a
                                    # board nobody wires anything to should not
                                    # import machine.Pin to find that out

        def handle_http(self, method, path, body):
            # `path` is the request TARGET; /gpio's GET reads its pin off the
            # query exactly as carts.json does, so the whole target goes down.
            bare = path.split("?", 1)[0]
            if bare == "/gpio":
                if self._pins is None:
                    self._pins = zero_gpio.pin_factory()
                return zero_gpio.handle(method, body, pin=self.pin,
                                        get_pin=self._pins, query=path)
            # /update is the BASE class's since 2026-08-29 -- every board
            # answers it, and the only thing that differed was the backend
            # behind it, which is what `self.update` already is.
            return WebHost.handle_http(self, method, path, body)

    return ZeroHost


def make_updater(me):
    """This board's OtaUpdater + the ZeroUpdate driving it, or (None, None).

    `with_sd` is a plain call-through: there is no card on this board, so the
    T-Deck's mount-and-drain bracket has nothing to bracket. `update_dir` is on
    the internal VFS for the same reason -- the P4 arrangement, and moy_ota
    reads the instance's directory everywhere rather than its module constant,
    so the two boards' updaters cannot look at each other's staging area.

    A failure here costs the update endpoints and NOTHING ELSE: a board that
    cannot construct an updater must still serve the kid's carts.
    """
    try:
        import moy_ota

        ota = moy_ota.OtaUpdater(lambda fn: fn(), update_dir=UPDATE_DIR)
        ota.set_wifi(_ZeroWifi(),
                     go_online=lambda: connect(hostname=me.get("name")
                                               or HOSTNAME))
        task = ZeroUpdate(ota)
        return ota, task
    except Exception as exc:             # noqa: BLE001
        print("ZERO ota: updater unavailable:", exc)
        return None, None


def serve():
    """Bring the store host up and pump it forever. Ctrl-C drops to the REPL
    (which is how provision.sh and mpremote get the board back)."""
    _mkdir(ROOT)
    _mkdir(CARTS_DIR)
    _mkdir(WEB_DIR)
    # BEFORE the radio, on purpose: seeding is local, and a board that cannot
    # find a network still ends up with a store -- it goes on to host the setup
    # AP, and the first page served after that form is answered has carts behind
    # it rather than an empty shelf.
    seed_carts(CARTS_DIR)
    me = identity()
    name = me.get("name") or HOSTNAME
    ip = connect(hostname=name)
    if ip is None:
        # NO WAY IN: host the setup AP instead of printing and giving up. This
        # is the one branch that never returns -- a filled-in form resets the
        # board, and a board nobody fills in stays reachable until someone does.
        print("ZERO no wifi -- hosting the setup AP")
        import zero_setup
        zero_setup.run(WIFI_STORE, ZERO_STORE)
        return
    host = zero_host_class()(CARTS_DIR, WEB_DIR, pin=me.get("pin"))
    ota, task = make_updater(me)
    host.update = task
    if task is not None:
        # BEFORE the first poll: the marker names the slot the last install
        # pointed the bootloader at, and reading it is how a rollback stops
        # being silent. It is also the one thing a human plugging in a cable
        # after an unattended update wants to see first.
        task.boot_check()
    host.start(ip)
    # The pin is PRINTED, and printed as part of the url. A page reaches this
    # board's write half (POST /sync, POST /gpio, POST /update) by carrying
    # `?pin=`, so a pin nobody is told is a board that silently refuses every
    # edit made on it. Serial is the right channel for it: reading this line
    # takes the cable.
    pin = me.get("pin")
    # Built by `paired_url()` rather than reassembled here: that is the one
    # body that knows a port is spelled only when it is not 80, so this line
    # cannot drift from the url the console's own screens show.
    print("ZERO serving %s  (mDNS %s.local)%s"
          % (host.paired_url(), name,
             "" if pin else "  [no pin -- writes are open]"))
    if ota is not None:
        print("ZERO firmware %s (%s, %s)"
              % (ota.version_label(), ota.channel(), ota.slot()))
    shadowed = _frozen_or_pushed()
    if shadowed:
        # Loud, because it is invisible otherwise and it inverts what the image
        # guarantees: these modules are running from /, not from this build.
        print("ZERO NOTE: pushed copies are SHADOWING the image for:",
              ", ".join(shadowed))
    while True:
        # The console boards poll between frames; this board HAS no frames, so
        # a short sleep is its whole duty cycle. 10ms keeps a page load snappy
        # while idling at ~zero.
        host.poll()
        if task is not None:
            # THE ROLLBACK CONFIRM, on the only evidence this board has: the
            # host is up and the loop is still going round. `serving` is the
            # headless stand-in for "something reached the glass" -- see
            # moy_ota.confirm_when_serving, and note that an image whose host
            # never comes up never confirms, which is exactly the image the
            # bootloader should take back.
            if ota is not None and not ota.confirmed:
                if ota.confirm_when_serving(host.serving):
                    print("ZERO ota: this image confirmed itself -- rollback "
                          "cancelled")
                    # Only NOW go looking for the next one. A board that checks
                    # for firmware before certifying its own can chase a bad
                    # image round a rollback loop forever.
                    task.boot_check_once()
        # ...and `task.step()` is NOT called here: `WebHost.poll()` pumps the
        # injected update backend, which is the one place every board's does
        # its slice. A second call here would double this board's install rate
        # for no reason and put the two boards' pumps in different files again.
        time.sleep_ms(10)

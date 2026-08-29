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

`GET/POST /update` is the third (ZeroUpdate, below): the OTA flow every other
board runs off its own Settings screen, exposed as two pin-gated JSON endpoints
because this board has no screen to put it on.

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
# WHERE THE REQUEST WILL COME FROM instead: the browser console this board
# serves. The browser IS this board's screen, so its Settings menu is where
# "update this board" belongs, and it will POST the same /update this file
# already gates. That is a follow-up, not something this file does today.
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
                             -> downloading -> installing -> reboot
    """

    def __init__(self, updater):
        self.ota = updater
        self.state = "idle"
        self.error = None
        self.manifest = None
        self._want = None          # queued action, applied by the next step()
        self._checked = False      # has the once-per-boot check run?
        self._reboot_in = 0        # poll iterations left before machine.reset()

    # -- the request side -----------------------------------------------------

    def request(self, action):
        """Queue `check`, `install` or `cancel`. Returns (ok, message)."""
        if action not in ("check", "install", "cancel"):
            return False, "action must be check, install or cancel"
        if action == "cancel":
            self._cancel()
            return True, "cancelled"
        if self.state in ("downloading", "installing", "reboot"):
            return False, "busy: " + self.state
        if action == "install" and self.state != "offer":
            # Check first rather than refusing: a caller that knows it wants
            # the newest build should not have to make two requests, and the
            # check is the thing that decides whether there IS an install.
            self._want = "check+install"
        else:
            self._want = action
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
        if want in ("check", "check+install"):
            found = self._check()
            if found and want == "check+install":
                self._begin_install()
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
        return True

    # -- the phases -----------------------------------------------------------

    def _check(self):
        self.state = "checking"
        self.error = None
        self.manifest = None
        m = self.ota.check_online()
        if m is None:
            if self.ota.absent:
                self.state = "none"
                print("ZERO ota: no build published on this channel yet")
            else:
                self.state = "error"
                self.error = self.ota.error or "check failed"
                print("ZERO ota: check failed --", self.error)
            return False
        if not self.ota.offers(m):
            self.state = "none"
            print("ZERO ota: up to date (%s)" % self.ota.version_label())
            return False
        self.manifest = m
        self.state = "offer"
        print("ZERO ota: %s available (%s, %d bytes)"
              % (m.get("label", "?"), m.get("channel", "?"),
                 int(m.get("size", 0) or 0)))
        return True

    def _begin_install(self):
        if not self.manifest:
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
        try:
            total = self.ota.begin(path)
        except Exception as exc:         # noqa: BLE001 -- not an app image
            self.state = "error"
            self.error = str(exc)
            print("ZERO ota: image refused --", self.error)
            return True
        self.state = "installing"
        print("ZERO ota: writing %d bytes into the inactive slot" % total)
        return True

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
        self.error = None
        self._reboot_in = 0

    # -- what a human reads ---------------------------------------------------

    def status(self):
        ota = self.ota
        out = {
            "state": self.state,
            "running": {"version": ota.version(), "label": ota.version_label(),
                        "channel": ota.channel(), "slot": ota.slot(),
                        "board": _ota_board()},
        }
        if self.error:
            out["error"] = self.error
        if ota.boot_verdict:
            # The PREVIOUS install's outcome: "ok" (the slot we pointed at is
            # the one running) or "rolled_back" (the bootloader gave up on it).
            # This is the headless replacement for the notice banner the
            # console boards show, and it is why the marker is cleared at the
            # CONFIRM rather than at the read.
            out["last"] = {"result": ota.boot_verdict[0],
                           "detail": ota.boot_verdict[1]}
        if self.manifest:
            out["available"] = {
                "version": self.manifest.get("version"),
                "label": self.manifest.get("label"),
                "channel": self.manifest.get("channel"),
                "size": self.manifest.get("size"),
            }
        if self.state == "downloading":
            out["progress"] = {"done": ota.dl_done, "total": ota.dl_total}
        elif self.state == "installing":
            out["progress"] = {"done": ota.done, "total": ota.total}
        return out


def _ota_board():
    try:
        import moy_ota

        return moy_ota.BOARD
    except Exception:                    # noqa: BLE001
        return "?"


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
            self.update = None      # a ZeroUpdate, injected by serve()

        def handle_http(self, method, path, body):
            # `path` is the request TARGET; /gpio's GET reads its pin off the
            # query exactly as carts.json does, so the whole target goes down.
            bare = path.split("?", 1)[0]
            if bare == "/gpio":
                if self._pins is None:
                    self._pins = zero_gpio.pin_factory()
                return zero_gpio.handle(method, body, pin=self.pin,
                                        get_pin=self._pins, query=path)
            if bare == "/update":
                return self._update(method, path, body)
            return WebHost.handle_http(self, method, path, body)

        def _update(self, method, target, body):
            """The firmware endpoint. BOTH METHODS ARE GATED, with no read-half
            exemption -- the same call `/gpio`'s GET was brought under on
            2026-08-25. What a GET here reveals is which firmware a specific
            board on somebody's home network is running, which is a shopping
            list for whoever wants to hand it an image; and the write half is
            the strongest verb this board has, since it replaces the board.
            """
            from moy_webserver import http_response

            refused = self.gate(target)
            if refused is not None:
                return refused
            if self.update is None:
                return http_response(503, '{"error":"no updater"}')
            if method == "GET":
                return http_response(200, json.dumps(self.update.status()))
            if method != "POST":
                return http_response(405, '{"error":"GET or POST"}')
            try:
                doc = json.loads(body or "{}")
                action = doc.get("action") or "check"
            except (ValueError, AttributeError):
                return http_response(400, '{"error":"bad json"}')
            ok, msg = self.update.request(action)
            # The POST ANSWERS IMMEDIATELY and the work happens in step(): see
            # ZeroUpdate's docstring for why holding the socket across a 2MB
            # download is the one shape that cannot report anything useful.
            return http_response(200 if ok else 409,
                                 json.dumps({"ok": ok, "message": msg,
                                             "state": self.update.state}))

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
    print("ZERO serving http://%s:%d/%s  (mDNS %s.local)%s"
          % (ip, host.port, ("?pin=" + pin) if pin else "", name,
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
            task.step()
        time.sleep_ms(10)

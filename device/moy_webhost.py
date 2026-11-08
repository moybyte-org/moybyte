# Map (grep -n a name to jump there):
#   pack_store         the whole store as the page's bundle (the C pull)
#   update_status      the one /update document
#   ConsoleUpdate      the /update backend on a board with glass
#   ConsoleSystem      the console's Settings and badges for the page it serves
#   WebHost            the Settings contract over the kernel's webhost
#   WebHost.poll       the C poll, its events, the parked request, the update
#   WebHost.answer_vm  the routes the VM answers: /run, /update, the system root
#   WebHost.browsers   the paired browsers (and WebHost.forget, one or all)
#   ensure_online      connect, wait for the link, report (a port's station)
#   make_webhost       the WebHost every board injects
"""Serve the moybyte web console FROM the console, over the device's own WiFi.

The server is the kernel's (native/moy_net/moy_webhost.c, whose header is the
authority on the routes and the sign-in): its listener, the baked bundle, the
capability marker, the store's two pulls and the sync apply are C, polled once
a frame and calling no Python, and it outlives a soft reset -- a Ctrl-D leaves
it serving and the next VM adopts it. What stays here is what needs the
console behind it:

    POST /run       play a cart on the BOARD's own glass (#197)         [sealed]
    GET  /update    this board's FIRMWARE state (#41/#53)               [sealed]
    POST /update    ask it to update                                    [sealed]
    GET  /console.json  the console's portable Settings and its badges  [sealed]
    POST /sync      a batch for the system root, merged into them       [sealed]

The last two are a host built with `system` (`ConsoleSystem`; the Zero has
none, so its page keeps its Settings in the tab): the board is of record, and
which keys a page may change is `moy_sync.SYSTEM_KEYS`.

The C parks such a request until `poll()` takes it, answers it here, and hands
the response back; one nobody answers within the kernel's window (a VM gone
mid-request) is a 503. A batch that changed the SHELF is an event the poll
turns into `on_sync(folders)` -- the cart folders the batches changed, None
when they did not all fit the kernel's list (the launcher re-reads those, or
every one), and the listener closing after
its goodbye window is the event that releases the radio lease (`on_stop`).

THE SIGN-IN is the C's (moy_webauth.c): it opens a parked request's seal
before the VM sees it and seals the VM's answer, so these handlers read plain
bodies and check nothing. Its folder (`auth`, the store's system folder: the
keys file and the console's password) is read at START, never at
construction: a board builds this before its store is up. A host built with
no folder (a test, the dev server) is open end to end.

FIRMWARE UPDATES ARE TWO BACKENDS behind one route. A board WITH GLASS
(`ConsoleUpdate`) hands the glass back: the request turns the web console off
and opens the update screen (runtime/system/update_ui.py), whose confirms are taps on
the board -- nothing installs unattended. A HEADLESS board
(`zero_host.ZeroUpdate`) drives the install from its own poll. `screen` in the
status document says which, so the page knows whether it is about to stop
being the console.
"""

import json as _json

try:
    import moy_net
except ImportError:                      # host: the C over ctypes
    from runtime import net_binding as moy_net
http_response = moy_net.http_response
query_param = moy_net.query_param

try:
    import moy_sync
except ImportError:                      # host / CPython: the runtime package
    from runtime import moy_sync

DEFAULT_PORT = 80

# The answer a board with no updatable firmware answers /update with: 503
# means "a board, but nothing to update", where a 404 means "not a board".
NO_UPDATER = '{"error":"no updater"}'

# The kernel's events (moy_net.h's MOY_WEB_EV_*).
EV_SHELF = 1
EV_STOPPED = 2
EV_PAIRED = 4


def _kinds():
    kinds = moy_sync.file_folders()
    return None if kinds is None else tuple(kinds)


def pack_store(carts_root, tops=None):
    """The store under `carts_root` as the page's bundle: {"<top>/<rel>":
    text}, and {"<top>/<rel>": {"b": base64}} for a cover -- the kernel's pull,
    parsed. `tops` restricts the top-level folders walked (the files root's
    kinds); the dev server (firmware/web_runner/serve.py) serves this."""
    return _json.loads(moy_net.web_pack(carts_root, tops))


def baked_stamp():
    """The image's own bundle as "<count> <bytes> <digest>", or None.

    WHICH console is this board serving? Reachable from a REPL
    (`py moy_webhost.baked_stamp()`), so the check costs one line and no
    reflash."""
    stamp = getattr(moy_net, "web_stamp", None)
    return stamp() if stamp is not None else None


def _ota_board():
    """Which board this image is for: `moy_ota.BOARD`. An OTA payload is an
    app-partition image, so another board's is a valid image that cannot
    boot, and the page shows the name for the same reason the manifest signs
    it."""
    try:
        import moy_ota

        return moy_ota.BOARD
    except Exception:                    # noqa: BLE001 -- a host/no-OTA build
        return "?"


def update_status(ota, state, screen, error=None, offer=None, progress=None,
                  absent=False, staged=None):
    """The ONE /update document, whichever backend answered.

      state      the backend's own state word, not normalised across the two:
                 they are different machines.
      screen     does this board have glass of its own -- True means
                 triggering an update ENDS this page's job as the console.
      running    the firmware now running.
      last       the PREVIOUS install's verdict, when the boot found a marker.
      available  what the last check found, when it found anything.
      progress   {done, total} while bytes are moving, ABSENT otherwise: a
                 frozen 0/0 is what a broken transfer looks like too.
      absent     the channel has published nothing for this board yet.
      staged     where a downloaded image waits for the second confirm.
      error      the last failure, in words a person can act on.
    """
    out = {
        "state": state,
        "screen": bool(screen),
        "running": {"version": ota.version(), "label": ota.version_label(),
                    "channel": ota.channel(), "slot": ota.slot(),
                    "board": _ota_board()},
    }
    if error:
        out["error"] = error
    verdict = getattr(ota, "boot_verdict", None)
    if verdict:
        out["last"] = {"result": verdict[0], "detail": verdict[1]}
    if offer:
        out["available"] = offer
    if progress:
        out["progress"] = progress
    if absent:
        out["absent"] = True
    if staged:
        out["staged"] = staged
    return out


class ConsoleUpdate:
    """The /update backend on a board WITH GLASS: a HAND-OFF, not a driver.

    This board already owns the update flow -- `runtime/system/update_ui.py` advances
    the flash one chunk per painted frame of its screen and takes two confirms
    on the glass. A browser-driven install would be a second driver running
    while the update screen is not up, which on the T-Deck puts SD reads at
    the frame tail against a flush in flight. So the browser's part is the
    TRIGGER: a request turns the web console off -- which unparks the glass --
    and opens the update screen there, through the console's one funnel
    (`ws.web.stop()`).

    THE POST ANSWERS FIRST; THE HAND-OFF HAPPENS ON THE NEXT POLL: stopping
    the web console closes the socket the request arrived on.

    NOTHING HERE IS UNATTENDED: a request opens a screen, and the confirms
    that download and flash are taps on the board's own glass.
    """

    def __init__(self, ws):
        self.ws = ws
        self.state = "idle"
        self.error = None
        self._want = False       # a queued hand-off, applied by the next step()

    def request(self, action, channel=None):
        """Queue the hand-off: (ok, message), the message saying what happened
        to the CALLER. check, download and install are the same request here;
        `channel` is ignored, since the screen this hands off to shows the
        console's own CHANNEL row."""
        if action not in ("check", "download", "install", "cancel"):
            return False, "action must be check, download, install or cancel"
        if action == "cancel":
            return False, "the console's own screen owns this update"
        if self._updater() is None:
            return False, "this console cannot update itself"
        if self.state == "glass" or self._want:
            return False, "the console already has this on its screen"
        self.error = None
        self.state = "glass"
        self._want = True
        return True, "the console took this onto its own screen"

    def step(self):
        """One slice, from `WebHost.poll()`. True if it did work."""
        if not self._want:
            return False
        self._want = False
        ws = self.ws
        try:
            # The update screen's lease first: the stop below releases "web",
            # and the radio would power down between the two.
            ws.wifi_hold("update")
            web = getattr(ws, "web", None)
            if web is not None:
                # A BARE stop: this path can fail after it (the screen raises
                # and the board stays at its desktop), and the page is where
                # that error is readable -- a host in its goodbye window would
                # answer 503 to the request that was going to say so.
                web.stop(why=None)
            # ...and only now the screen: unparking routes home and would pop
            # an update screen pushed before it.
            ws.update_ui.open_update_online()
        except Exception as exc:         # noqa: BLE001 -- never break a frame
            self.state = "error"
            self.error = "%s" % exc
            print("UPDATE hand-off failed:", exc)
            try:
                ws.wifi_release("update")
            except Exception:                # noqa: BLE001
                pass
        return True

    def status(self):
        """The status document, or None when this board cannot update itself
        (the same 503 a board with no updater gives)."""
        ota = self._updater()
        if ota is None:
            return None
        return update_status(ota, self.state, True, error=self.error)

    def _updater(self):
        """The board's OtaUpdater when this build can take an OTA, else None.
        Read off `ws` at every call: a board constructs the webhost before the
        updater is attached."""
        ota = getattr(self.ws, "updater", None)
        if ota is None:
            return None
        try:
            return ota if self.ws._update_available() else None
        except Exception:                # noqa: BLE001 -- a ws without the query
            return None


class ConsoleSystem:
    """The system root's board half: the pull a page seeds its console from,
    and the merge of what it sends back, over the live console. A changed
    Setting is re-applied through the verbs boot uses (`apply_system_keys`),
    so a theme picked in the page is the board's when its glass comes back;
    a badge earned there is kept here without a second celebration."""

    def __init__(self, ws):
        self.ws = ws
        self._rx = moy_sync.SystemReceiver()

    def pull(self):
        ws = self.ws
        return moy_sync.system_pull(ws.system, ws.ach.doc())

    def apply(self, ops):
        """(applied, [(index, reason)]) of one batch's ops."""
        ws = self.ws
        applied, errors, changed = self._rx.apply(
            ops, ws.system, lambda i, day: ws.ach.award(i, quiet=True, day=day),
            ws.carts_root)
        if changed:
            ws.apply_system_keys(changed)
        return applied, errors


class WebHost:
    """The Settings contract (`.serving`, `.joining`, `.closing`, `.start()`,
    `.stop()`, `.url()`, `.error`) over the kernel's web-console switch
    (native/moy_net/moy_webconsole.c) and its webhost.

    A start never waits: it hands the switch the configuration and returns,
    and the frame's poll brings it on -- serving once the link has an address
    (the kernel dials the network its driver kept; `dial` is this console's
    own fallback), failed once the join ran out. `on_serving` and `on_failed`
    are the switch's two outcomes, said once each. Constructed, it binds
    nothing; a switch the kernel is ALREADY serving (a soft reset under a
    running one) is adopted, holding the radio lease it rides on."""

    # The routes the VM answers. A subclass adds its own (the Zero's /gpio)
    # and answers them in `handle_http`; a host with a `system` adds the
    # system root's pull, which is also what has the C park its batches.
    DEFER = ("/run", "/update")

    # The switch's states (moy_net.h's MOY_WC_*).
    OFF, JOINING, SERVING, CLOSING, FAILED = range(5)

    def __init__(self, carts_root, port=None, with_sd=None,
                 ensure_online=None, auth=None, on_sync=None, auth_source=None,
                 on_run=None, update=None, on_stop=None, files_root=None,
                 kinds=None, dial=None, on_serving=None, on_failed=None,
                 system=None, on_paired=None):
        self.port = DEFAULT_PORT if port is None else port
        self.carts_root = carts_root
        self.files_root = (moy_sync.files_root(carts_root)
                           if files_root is None else files_root)
        self.kinds = _kinds() if kinds is None else tuple(kinds)
        self.auth = auth
        self._auth_source = auth_source
        self.on_paired = on_paired
        self.on_sync = on_sync
        self.on_run = on_run
        self.update = update
        self.system = system
        self.on_stop = on_stop
        self.on_serving = on_serving
        self.on_failed = on_failed
        self._ensure_online = ensure_online
        self._dial = dial
        self._with_sd = with_sd or (lambda fn: fn())
        self._given_ip = None
        self.error = None
        self._why = None
        self._seen = moy_net.wc_phase(self.port)

    def _state(self):
        return moy_net.wc_phase(self.port)  # no allocation: the frame asks it

    @property
    def serving(self):
        return self._state() == self.SERVING

    @property
    def joining(self):
        return self._state() == self.JOINING

    @property
    def ip(self):
        st = moy_net.wc_state()
        mine = st[3] == self.port and st[0] in (self.SERVING, self.CLOSING)
        return (st[4] if mine else None) or self._given_ip

    @ip.setter
    def ip(self, ip):
        self._given_ip = ip

    @property
    def closing(self):
        """The goodbye's reason while the kernel is in its window, else None."""
        if self._why is None:
            return None
        return self._why if moy_net.web_state()[1] else None

    @property
    def requests(self):
        return moy_net.web_state()[4]

    def _defer(self):
        if self.system is None:
            return self.DEFER
        return self.DEFER + (moy_sync.SYSTEM_PULL,)

    def _configure(self, port):
        moy_net.web_start(port, self.carts_root, self.files_root,
                          self.kinds if self.files_root else None,
                          self.auth or None, self._defer())

    def _read_auth(self):
        if self._auth_source is not None:
            try:
                self.auth = self._auth_source() or None
            except Exception as exc:  # noqa: BLE001 -- a board then refuses every sealed route
                print("WEBHOST sign-in folder unavailable:", exc)

    def start(self, ip=None):
        """Turn the switch on, never waiting for the link. `ip` is the address
        to serve on where the kernel has no driver of its own (the Zero, the
        host); a console's comes from its driver. OSError when the bind is
        refused at once (the link was already up)."""
        self._read_auth()
        if ip is None and self._ensure_online is not None:
            ip = self._ensure_online()      # a board whose station is the port's
        self._given_ip = ip
        self.error = None
        self._why = None
        self._with_sd(lambda: None)          # the store's volume is up
        moy_net.wc_on(self.port, self.carts_root, self.files_root,
                      self.kinds if self.files_root else None,
                      self.auth or None, self._defer(), ip)
        st = moy_net.wc_state()
        if st[0] == self.JOINING and not st[2] and self._dial is not None:
            try:
                self._dial()                 # no kept network: this console's own
            except Exception as exc:         # noqa: BLE001 -- the join's timeout decides
                print("WEBHOST dial:", exc)
        self._seen = self._state()
        if self._seen == self.FAILED:
            self.error = self._failure(st[5])
            raise OSError(self.error)
        if self._seen == self.SERVING:
            self._announce()

    def _failure(self, err):
        if err == 110:
            return "no wifi"
        return "port %d busy" % self.port

    def _announce(self):
        try:
            print("WEBHOST", self.source_note())
        except Exception:                # noqa: BLE001 -- a log is never fatal
            pass

    def source_note(self):
        """One line naming the bundle this board serves: the image's, always."""
        stamp = baked_stamp()
        return "serving the bundle baked into this firmware (%s)" % (
            stamp or "NONE -- this image has no web console")

    def adopt(self, ws):
        """A host the kernel kept serving across a soft reset: take the radio
        lease it rides on, and the sign-in over this boot's store."""
        if not self.serving:
            return False
        try:
            ws.wifi_hold("web")
            self._read_auth()
            moy_net.web_auth(self.auth or None)
        except Exception as exc:         # noqa: BLE001 -- the console boots regardless
            print("WEBHOST adopt:", exc)
        return True

    def poll(self):
        """One kernel poll, its events, the parked request, then ONE slice of
        the update backend -- after the transport's, never inside a handler:
        the console board's turns the web console off, which closes the socket
        the request arrived on. Never breaks a frame."""
        moy_net.wc_poll()
        self._transition()
        did = moy_net.web_poll()
        ev = moy_net.web_events()
        if ev & EV_SHELF and self.on_sync is not None:
            try:
                self.on_sync(moy_net.web_shelf())
            except Exception as exc:     # noqa: BLE001
                print("SYNC rescan failed:", exc)
        if ev & EV_STOPPED:
            self._stopped()
        if ev & EV_PAIRED and self.on_paired is not None:
            try:
                self.on_paired()
            except Exception as exc:     # noqa: BLE001 -- a hook is never fatal
                print("WEBHOST on_paired:", exc)
        req = moy_net.web_take()
        if req is not None:
            did = True
            try:
                resp = self.answer_vm(req[0], req[1], req[2])
            except Exception as exc:     # noqa: BLE001 -- never fail the request
                print("WEB ERR %s: %s" % (type(exc).__name__, exc))
                resp = http_response(500, '{"error":"server"}')
            moy_net.web_answer(resp or http_response(
                404, "not found", "text/plain; charset=utf-8"))
        u = self.update
        if u is not None:
            try:
                if u.step():
                    did = True
            except Exception as exc:     # noqa: BLE001 -- never break a frame
                print("UPDATE ERR %s: %s" % (type(exc).__name__, exc))
        return did

    service = poll

    def _transition(self):
        """Say the switch's outcome once: serving, or the failure (whose
        radio lease goes with it)."""
        now = self._state()
        if now == self._seen:
            return
        was, self._seen = self._seen, now
        if now == self.SERVING and was == self.JOINING:
            self._announce()
            hook = self.on_serving
        elif now == self.FAILED:
            self.error = self._failure(moy_net.wc_state()[5])
            moy_net.wc_off()
            self._seen = self.OFF
            hook = self.on_failed
        else:
            return
        if hook is not None:
            try:
                hook()
            except Exception as exc:     # noqa: BLE001 -- a hook is never fatal
                print("WEBHOST hook:", exc)

    def _stopped(self):
        moy_net.wc_poll()               # the switch sees the listener gone: off
        self._seen = self._state()
        self._why = None
        hook = self.on_stop
        if hook is not None:
            try:
                hook()
            except Exception as exc:  # noqa: BLE001 -- a hook is never fatal
                print("WEBHOST on_stop:", exc)

    def stop(self, why=None):
        """Stop serving. With `why`, the kernel SAYS SO for its goodbye window
        before the socket closes, so a page can tell "switched off" from
        "unplugged". `serving` goes False at once either way: the Settings row
        and the glass follow the tap, and the lingering socket is a transport
        detail (`closing`). A join still waiting for the link just ends."""
        state = self._state()
        if why is None or state != self.SERVING:
            moy_net.wc_off()
            moy_net.web_events()
            self._seen = self.OFF
            self._stopped()
            return
        self._why = why
        moy_net.wc_off(why)
        self._seen = self.CLOSING

    def url(self):
        """The address to hand a human; the port is spelled unless it is 80."""
        got = moy_net.wc_url(False) if self._state() == self.SERVING else ""
        if got:
            return got
        host = self.ip or "0.0.0.0"
        if self.port == 80:
            return "http://%s/" % host
        return "http://%s:%d/" % (host, self.port)

    def paired_url(self):
        """The QR's link: `url()` with a FRESH single-use token and the
        board's id after its `#` (moy_net.h, moy_wc_url), so each call spends
        the last one's token. The page reads it and never sends it in a URL."""
        got = moy_net.wc_url(True) if self._state() == self.SERVING else ""
        return got or self.url()

    def browsers(self):
        """The paired browsers, [(kid, created, seen, name)] (Unix seconds,
        moy_net.h's moy_net_time), whether the switch is on or not: the
        sign-in reads its folder's keys first. [] with no folder."""
        self._read_auth()
        if not self.auth:
            return []
        moy_net.web_auth(self.auth)
        return moy_net.web_keys()

    def forget(self, kid):
        """Forget one paired browser by its id, or every one ("all"): how many
        went. Their sessions end, and a web terminal one holds is cut."""
        self._read_auth()
        if not self.auth:
            return 0
        moy_net.web_auth(self.auth)
        return moy_net.web_key_drop(kid)

    def handle_http(self, method, target, body):
        """One request's whole response off a socket (the host's seam: the
        tests and the dev loop): the kernel's router, then the routes the VM
        answers, sealed as the request was. A board's poll answers its parked
        requests through answer_vm alone; its moy_net has no web_handle."""
        if self._state() == self.OFF and self._why is None:
            st = moy_net.web_state()
            if not (st[0] or st[1]):    # never re-point a kernel host that serves
                self._configure(0)      # off a socket: this host's store and sign-in
        resp, plain = moy_net.web_handle(method, target, body or b"")
        if resp is not None:
            return resp
        resp = self.answer_vm(method, target, plain)
        return None if resp is None else moy_net.web_handle_answer(resp)

    def answer_vm(self, method, target, body):
        """A parked request's response from the routes the VM answers (its
        body opened), or None for none of them. The C seals it."""
        path = target.split("?", 1)[0]
        if path == "/run" and method == "POST":
            return self._run(body)
        if path == "/update":
            return self._update(target, body if method == "POST" else None)
        if self.system is not None:
            if path == moy_sync.SYSTEM_PULL and method == "GET":
                return self._console(target)
            if path == "/sync" and method == "POST":
                return self._system(body)
        return None

    def _console(self, target):
        """GET /console.json: the documents a page seeds its console's own
        root from."""
        return http_response(200, _json.dumps(self.system.pull()))

    def _system(self, body):
        """A system batch the C parked, merged into the console's Settings
        and badges inside the storage gate, answered as the C answers a carts
        batch."""
        ops, root_id = moy_sync.parse_batch(body or b"")
        if ops is None or root_id != moy_sync.SYSTEM_ROOT_ID:
            return http_response(400, '{"error":"bad batch"}')
        applied, errors = self._with_sd(lambda: self.system.apply(ops))
        if errors:
            print("SYNC system %d applied, %d refused: %s"
                  % (applied, len(errors), errors[0][1]))
        return http_response(200, _json.dumps(
            {"ok": applied, "err": [list(e) for e in errors]}))

    def _run(self, body):
        """PLAY ON DEVICE (#197): `{"cart": "<title or folder>"}`, a sealed
        route like /sync: it starts code on the kid's console. The launch
        runs inside the storage gate (it reads the cart off the store at the
        frame tail). The answer carries the TITLE that actually started."""
        try:
            if isinstance(body, bytes):
                body = body.decode("utf-8")
            doc = _json.loads(body)
        except Exception:                # noqa: BLE001
            doc = None
        if not isinstance(doc, dict):
            return http_response(400, '{"error":"bad body"}')
        if self.on_run is None:
            return http_response(501, '{"error":"no runner"}')
        name = doc.get("cart")
        # A named launch only: launch_named("") runs the FIRST cart.
        if not name or not str(name).strip():
            return http_response(400, '{"error":"no cart named"}')
        try:
            title = self._with_sd(lambda: self.on_run(name))
        except Exception as exc:         # noqa: BLE001 -- never fail the request
            print("RUN failed:", exc)
            return http_response(500, '{"error":"run failed"}')
        if not title:
            return http_response(404, '{"error":"no cart"}')
        return http_response(200, _json.dumps({"run": title}))

    def _update(self, target, body):
        """The firmware endpoint (#41/#53); `body` is None on the GET. Both
        methods are sealed routes: what a GET reveals is which firmware a
        board on somebody's network runs. A request only QUEUES; the work is
        the backend's step()."""
        if self.update is None:
            return http_response(503, NO_UPDATER)
        if body is None:
            doc = self.update.status()
            if doc is None:
                return http_response(503, NO_UPDATER)
            return http_response(200, _json.dumps(doc))
        try:
            if isinstance(body, bytes):
                body = body.decode("utf-8")
            sent = _json.loads(body or "{}")
            # DEFAULTS TO LOOKING, NEVER TO INSTALLING.
            action = sent.get("action") or "check"
            channel = sent.get("channel") or None
        except (ValueError, AttributeError):
            return http_response(400, '{"error":"bad json"}')
        doc = self.update.status()
        if doc is None:
            return http_response(503, NO_UPDATER)
        ok, msg = self.update.request(action, channel)
        doc = self.update.status() or doc
        doc["ok"] = ok
        doc["message"] = msg
        return http_response(200 if ok else 409, _json.dumps(doc))


def ensure_online(wifi, autoconnect=None, wait_ms=None, step_ms=None):
    """Connect if needed, WAIT for the link (`moy_ota.wait_online`, one body),
    then report the STA IP the WEB CONSOLE row displays; OSError for a board
    with no radio or no link."""
    if wifi is None:
        raise OSError("no wifi service")
    import moy_ota
    moy_ota.wait_online(
        lambda: wifi.status()[0],
        None if autoconnect is None else lambda: autoconnect(wifi),
        moy_ota.ONLINE_WAIT_MS if wait_ms is None else wait_ms,
        moy_ota.ONLINE_STEP_MS if step_ms is None else step_ms)
    st = wifi.status()
    if not st[0]:
        raise OSError("no wifi")
    return st[2]                       # the STA IP: what the row displays


def _system_dir(ws):
    try:
        import ssh_service
    except ImportError:                  # host: the runtime package
        from runtime import ssh_service
    return ssh_service.system_dir(ws)


def make_webhost(ws, carts_root, autoconnect=None, with_sd=None, port=None):
    """The WebHost every board injects -- built once, here, so no board is
    left without the web console by a per-board injection nobody wrote.

    Takes `ws` rather than `ws.wifi`: the wifi service is attached later, and
    the closures read it when the switch is turned. The sign-in's folder is
    the store's system folder, read off the live `ws` at START (where
    `passwd` keeps the password, ssh_service.system_dir), and ConsoleUpdate asks `ws`
    whether this build can take an OTA at every request. The switch joins
    the network the kernel's driver kept; `dial` is the console's own
    fallback when it kept none. `autoconnect` is accepted for the boards'
    call shape and not used: nothing here waits for a link."""
    return WebHost(carts_root, port=port, with_sd=with_sd,
                   dial=lambda: dial_saved(getattr(ws, "wifi", None)),
                   auth_source=lambda: _system_dir(ws),
                   on_sync=lambda folders: ws.rescan_carts(folders),
                   on_run=lambda name: ws.launch_named(name),
                   on_stop=lambda: ws.wifi_release("web"),
                   on_serving=lambda: ws.web.on_serving(),
                   on_failed=lambda: ws.web.on_failed(),
                   on_paired=lambda: ws.web.on_paired(),
                   update=ConsoleUpdate(ws), system=ConsoleSystem(ws))


def dial_saved(wifi):
    """Ask the station for the network the store remembers first, without
    waiting for it (the switch's poll does): what a console dials when its
    driver kept no network yet."""
    if wifi is None:
        return False
    store = getattr(wifi, "_store", None)
    root = getattr(wifi, "_root", None)
    up = getattr(wifi, "_ensure_wlan", None)
    wlan = up() if up is not None else getattr(wifi, "wlan", None)
    if store is None or root is None or wlan is None:
        return False
    for n in store.load_wifi(root):
        wlan.connect(n["ssid"], n.get("password", ""))
        return True
    return False

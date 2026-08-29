"""First-run setup: the AP a Zero hosts when it has no way onto a network (#41).

A headless board has one honest failure mode and this is it -- no screen to
show a DHCP address, no keyboard to type a password, and (until now) a serial
line as the only way to hand it credentials. So when `zero_host.connect()`
finds nothing joinable, the board becomes its own network for as long as it
takes to be told about a real one:

    boot -> connect() finds nothing
         -> SoftAP `moybyte-zero-XXXX`  (open; the suffix is the AP MAC's tail)
         -> http://192.168.4.1/          the setup form
         -> POST /setup                  saved -> reboot -> STA, as normal

    GET  /        the form (one small self-contained page, no dependencies)
    GET  /scan    {"nets": [{"ssid", "rssi", "lock"}, ...]} -- what STA can see
    POST /setup   name + 4-digit pin + ssid + password, urlencoded

THE AP IS OPEN, deliberately. A WPA key would have to be printed on the board
or guessed, and there is nothing behind this AP to protect: it exists only
while the board has no configuration, it serves one form, and the one secret
that crosses it -- the WiFi password being typed in -- is a secret the person
typing it already owns and is standing next to. What it costs is that a
neighbour in range during the setup minute could join and set the board up
first; what it buys is a setup that works with a phone and no instructions.
The moment it succeeds the AP is gone and the board is on STA behind the
router's own security. (A pairing button or a printed key is #41's if this
ever ships beyond a desk.)

STA is still the SERVING mode and that is not re-litigated here: the deleted
streaming port measured SoftAP throughput as the cause of its multi-second
stalls, and this board hands out a ~570KB bundle. The AP is for the form and
nothing else.

Setup serves on port 80, not the console's 8080: what a person does here is
type an address into a phone, and `192.168.4.1` is a shorter thing to get
right than `192.168.4.1:8080`.

No captive-portal DNS hijack. A phone's connectivity probe will 404 and the
phone will say the network has no internet, which is true. The address is on
the AP name and in the serial print; a DNS responder is a second server to
own for a saving of one typed line.
"""

import json

from moy_webserver import WebServer, http_response

try:
    from ticks import _ticks_ms as ticks_ms, _ticks_diff as ticks_diff
except ImportError:                  # host / CPython: the runtime package
    from runtime.ticks import _ticks_ms as ticks_ms, _ticks_diff as ticks_diff

AP_PREFIX = "moybyte-zero-"
SETUP_PORT = 80
DEFAULT_NAME = "moybyte-zero"

# How long after answering the form before the board resets. The response has
# to reach the phone first (the transport closes the conn as it returns, but
# lwIP still has bytes to push), and a person needs to read "saved".
REBOOT_MS = 1200

# A scan blocks the radio for a couple of seconds and briefly disturbs the AP
# the phone is sitting on, so a page reload inside this window reuses the last
# one rather than making the person's connection stutter twice.
SCAN_CACHE_MS = 10000

# Hostname characters. It becomes `network.hostname()`, i.e. the mDNS label the
# whole point of this board is being findable by, so it takes what a DNS label
# takes and nothing else.
_NAME_OK = "abcdefghijklmnopqrstuvwxyz0123456789-"


def ap_ssid(mac):
    """`moybyte-zero-XXXX` from the AP interface's MAC (its last two bytes).

    The tail rather than a counter or a random number: two Zeros on one desk
    must not collide, the MAC is the only per-board fact available before any
    configuration exists, and its last two bytes are what every other consumer
    device puts on the end of its own setup SSID.
    """
    try:
        tail = bytes(mac)[-2:]
    except Exception:                # noqa: BLE001 -- no mac: still name it
        return AP_PREFIX + "0000"
    return AP_PREFIX + "".join("%02x" % b for b in tail)


def _unquote(text):
    """One urlencoded field -> text. `text` arrives LATIN-1 (one char per byte).

    The percent escapes are collected as BYTES and decoded ONCE at the end. Per
    character is the obvious way and it is wrong: a UTF-8 SSID (`Caf%C3%A9`)
    comes out as two mojibake characters, which is a network name the board
    then cannot match -- a setup that appears to succeed and never connects.
    """
    out = bytearray()
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "+":
            out.append(0x20)
            i += 1
            continue
        if c == "%" and i + 2 < n:
            try:
                out.append(int(text[i + 1:i + 3], 16))
                i += 3
                continue
            except ValueError:
                pass
        out.append(ord(c) & 0xFF)
        i += 1
    try:
        return bytes(out).decode("utf-8")
    except Exception:                # noqa: BLE001 -- a stray byte loses the
        return bytes(out).decode("latin-1")   # accent, never the whole field


def parse_form(body):
    """An application/x-www-form-urlencoded body -> {field: value}.

    The form posts as a plain HTML <form>, so this works with the page's
    JavaScript broken or absent -- which on a first-run setup page reached from
    an unknown phone is worth more than any nicety JS would buy.
    """
    if isinstance(body, bytes):
        # latin-1 preserves every byte as one character; _unquote puts them
        # back together and decodes the result as the UTF-8 a form sends.
        body = body.decode("latin-1")
    out = {}
    for part in body.split("&"):
        if not part:
            continue
        k, sep, v = part.partition("=")
        if not sep:
            continue
        out[_unquote(k)] = _unquote(v)
    return out


def clean_name(raw):
    """A form name -> an mDNS label, or "" when nothing usable survives."""
    name = "".join(c for c in str(raw or "").strip().lower() if c in _NAME_OK)
    while name.startswith("-"):
        name = name[1:]
    while name.endswith("-"):
        name = name[:-1]
    return name[:24]


def validate(fields):
    """The form -> (clean, None) or (None, why).

    `why` is a whole sentence because it is shown to the person standing at the
    board, who has no other channel and cannot read a traceback.
    """
    name = clean_name(fields.get("name"))
    if not name:
        return None, ("The name needs at least one letter or digit "
                      "(a-z, 0-9 and dashes).")
    pin = str(fields.get("pin") or "").strip()
    if len(pin) != 4 or not pin.isdigit():
        return None, "The pin has to be exactly 4 digits."
    ssid = str(fields.get("ssid") or "").strip()
    if not ssid:
        return None, "Pick a WiFi network, or type its name."
    if len(ssid) > 32:
        return None, "That network name is too long to be real (32 max)."
    password = str(fields.get("password") or "")
    # "" is a real answer -- an open network. Anything else is WPA, whose key
    # is 8..63 characters by the standard, so a shorter one is a typo that
    # would otherwise cost a reboot to discover.
    if password and not (8 <= len(password) <= 63):
        return None, "A WiFi password is between 8 and 63 characters."
    return {"name": name, "pin": pin,
            "ssid": ssid, "password": password}, None


def merge_network(doc, ssid, password):
    """The saved-networks document with `ssid` FIRST and any old copy gone.

    First because `zero_host.connect()` walks the list in order and the network
    somebody just typed in is the one they are standing in; the rest are kept
    because a board set up at a friend's house should still come up at home.
    """
    nets = []
    if isinstance(doc, dict):
        nets = doc.get("networks") or []
    elif isinstance(doc, list):
        nets = doc
    keep = [n for n in nets
            if isinstance(n, dict) and n.get("ssid") and n.get("ssid") != ssid]
    return {"networks": [{"ssid": ssid, "password": password}] + keep}


def zero_doc(name, pin):
    """The board's own identity file: the mDNS name and the write PIN."""
    return {"name": name, "pin": pin}


def scan_json(nets):
    """`network.WLAN.scan()` tuples -> the page's list.

    Strongest copy of each SSID wins, hidden ones dropped -- a duplicate row
    per mesh repeater is noise on a phone-sized form.
    """
    best = {}
    for net in nets or []:
        try:
            ssid = net[0]
            rssi = net[3]
            auth = net[4]
        except Exception:            # noqa: BLE001 -- an odd row is not fatal
            continue
        if isinstance(ssid, bytes):
            try:
                ssid = ssid.decode("utf-8")
            except Exception:        # noqa: BLE001
                continue
        if not ssid:
            continue
        prev = best.get(ssid)
        if prev is None or rssi > prev[0]:
            best[ssid] = (rssi, 0 if auth in (0, None) else 1)
    rows = [{"ssid": s, "rssi": v[0], "lock": v[1]} for s, v in best.items()]
    rows.sort(key=lambda r: -r["rssi"])
    return json.dumps({"nets": rows[:20]})


# The page. One file, no fetches but /scan, no fonts, no frameworks -- it is
# served by a board with ~20KB of free internal SRAM to a phone that may be the
# only computer in the room.
_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>moybyte zero setup</title><style>
:root{color-scheme:light dark}
body{margin:0;padding:24px 18px;font:16px/1.5 system-ui,sans-serif;
 max-width:26rem;margin-inline:auto}
h1{font-size:1.35rem;margin:0 0 .25rem}
p.sub{margin:0 0 1.5rem;opacity:.7}
label{display:block;margin:0 0 1rem}
span{display:block;font-size:.85rem;opacity:.7;margin-bottom:.25rem}
input{width:100%%;box-sizing:border-box;padding:.6rem;font-size:1rem;
 border:1px solid #8888;border-radius:8px;background:transparent;color:inherit}
button{width:100%%;padding:.75rem;font-size:1rem;border:0;border-radius:8px;
 background:#3b6ea5;color:#fff}
.err{padding:.6rem .8rem;border-radius:8px;background:#c0392b;color:#fff;
 margin-bottom:1rem}
</style></head><body>
<h1>moybyte zero</h1>
<p class="sub">%s</p>
%s<form method="post" action="/setup">
<label><span>name for this board</span>
<input name="name" value="%s" maxlength="24" autocapitalize="off"
 autocorrect="off" spellcheck="false"></label>
<label><span>pin (4 digits, asked for before anything may change this board)</span>
<input name="pin" inputmode="numeric" pattern="[0-9]{4}" maxlength="4"
 required></label>
<label><span>wifi network</span>
<input name="ssid" list="nets" maxlength="32" autocapitalize="off"
 autocorrect="off" spellcheck="false" required><datalist id="nets"></datalist>
</label>
<label><span>wifi password (leave empty for an open network)</span>
<input name="password" type="password" maxlength="63"></label>
<button>save and restart</button></form>
<script>
fetch("scan").then(function(r){return r.json()}).then(function(d){
 var l=document.getElementById("nets");
 (d.nets||[]).forEach(function(n){
  var o=document.createElement("option");o.value=n.ssid;l.appendChild(o)});
}).catch(function(){});
</script></body></html>"""

_DONE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>moybyte zero</title><style>
body{margin:0;padding:24px 18px;font:16px/1.5 system-ui,sans-serif;
 max-width:26rem;margin-inline:auto}</style></head><body>
<h1>saved</h1>
<p>This board is restarting and will join <b>%s</b>.</p>
<p>Find it again at <b>http://%s.local:8080/?pin=%s</b> once it is up.</p>
<p>Keep the whole address: the <b>?pin=</b> part is what lets the console save
your work back onto this board. Without it you can play, but nothing you make
is kept.</p>
<p>This setup network is about to disappear &mdash; rejoin your own WiFi.</p>
</body></html>"""


def page(ap_name, error=None, name=DEFAULT_NAME):
    return _PAGE % (
        "connected to %s &mdash; tell this board where to live" % ap_name,
        ('<p class="err">%s</p>' % error) if error else "",
        name)


class SetupServer(WebServer):
    """The setup form over the same transport everything else here rides.

    `save(clean)` and `scan()` are injected: the persistence and the radio are
    the two things a host test cannot have, and everything between them --
    parsing, validating, what the page says -- is then ordinary testable code.
    """

    def __init__(self, ap_name, save, scan, port=SETUP_PORT):
        WebServer.__init__(self, port=port)
        self.ap_name = ap_name
        self._save = save
        self._scan = scan
        self.saved = None            # the accepted form, once there is one
        self.reboot_at = None        # set by a good POST; the loop watches it
        self._scan_cache = None
        self._scan_at = 0

    def handle_http(self, method, path, body):
        path = path.split("?", 1)[0]
        if method == "GET" and path in ("/", "/index.html"):
            return http_response(200, page(self.ap_name),
                                 "text/html; charset=utf-8")
        if method == "GET" and path == "/scan":
            return http_response(200, self.scan_cached())
        if method == "POST" and path == "/setup":
            return self._setup(body)
        return None                  # -> 404 from the transport

    def scan_cached(self):
        now = ticks_ms()
        if (self._scan_cache is None
                or ticks_diff(now, self._scan_at) >= SCAN_CACHE_MS):
            try:
                self._scan_cache = scan_json(self._scan())
            except Exception as exc:  # noqa: BLE001 -- a failed scan is a form
                print("ZERO SETUP scan failed:", exc)   # you type into by hand
                self._scan_cache = '{"nets":[]}'
            self._scan_at = now
        return self._scan_cache

    def _setup(self, body):
        clean, why = validate(parse_form(body))
        if clean is None:
            return http_response(400, page(self.ap_name, why),
                                 "text/html; charset=utf-8")
        try:
            self._save(clean)
        except Exception as exc:      # noqa: BLE001 -- a full/broken fs
            print("ZERO SETUP save failed:", exc)
            return http_response(500, page(
                self.ap_name, "This board could not write the settings down "
                              "(%s). Nothing was changed." % exc),
                "text/html; charset=utf-8")
        self.saved = clean
        # The reset is DEFERRED, not called here: this returns the response the
        # phone is waiting for, and a machine.reset() on the way out of a
        # handler resets before those bytes leave the board.
        self.reboot_at = ticks_ms() + REBOOT_MS
        print("ZERO SETUP saved: name=%s ssid=%s -- rebooting"
              % (clean["name"], clean["ssid"]))
        return http_response(200, _DONE % (clean["ssid"], clean["name"],
                                           clean["pin"]),
                             "text/html; charset=utf-8")

    def due(self):
        """True once the saved-and-rebooting delay has run out."""
        return (self.reboot_at is not None
                and ticks_diff(ticks_ms(), self.reboot_at) >= 0)


def save_setup(clean, wifi_store, zero_store, read=None, write=None):
    """Persist one accepted form: the network into the console's own wifi.json
    (merged), the name and pin into zero.json.

    `read`/`write` are injected for the host test; on the board they are files.
    """
    _read = read or _read_json
    _write = write or _write_json
    _write(wifi_store, merge_network(_read(wifi_store), clean["ssid"],
                                     clean["password"]))
    _write(zero_store, zero_doc(clean["name"], clean["pin"]))


def _read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(path, doc):
    with open(path, "w") as f:
        f.write(json.dumps(doc))


def run(wifi_store, zero_store, port=SETUP_PORT, sleep_ms=10):
    """Host the setup AP and serve the form until it is filled in. Never
    returns: a good POST resets the board into STA."""
    import machine
    import network
    import time

    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ssid = ap_ssid(ap.config("mac"))
    try:
        ap.config(essid=ssid, authmode=network.AUTH_OPEN)
    except Exception as exc:         # noqa: BLE001 -- older port: essid only
        print("ZERO SETUP ap config:", exc)
        ap.config(essid=ssid)
    # STA active but NOT connecting: this is what /scan reads. Bringing it up
    # beside the AP is supported on the S3 (the two share one radio and the AP
    # follows the STA's channel), and a scan is the only thing it is asked for.
    sta = network.WLAN(network.STA_IF)
    sta.active(True)

    srv = SetupServer(ssid,
                      lambda clean: save_setup(clean, wifi_store, zero_store),
                      sta.scan, port=port)
    ip = ap.ifconfig()[0]
    if not srv.start(ip):
        raise OSError("setup port %d busy" % port)
    print("ZERO SETUP  join '%s' (open) then open http://%s/" % (ssid, ip))
    while True:
        srv.poll()
        if srv.due():
            srv.stop()
            ap.active(False)
            machine.reset()
        time.sleep_ms(sleep_ms)

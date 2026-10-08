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

Setup serves on port 80: what a person does here is type an address into a
phone, and `192.168.4.1` is a shorter thing to get right than
`192.168.4.1:8080`. That argument won on the SERVING side too on 2026-08-29
(moy_webserver.DEFAULT_PORT), so both halves are on 80 now -- which collides
with nothing, because the two are mutually exclusive by construction: a board
with saved WiFi serves and never reaches here, and a board without one hosts
this AP and never builds a WebHost (zero_host.serve returns into run() below).

THE CAPTIVE PORTAL, and the decline it reverses (2026-08-29). This docstring
used to say: "No captive-portal DNS hijack. A phone's connectivity probe will
404 and the phone will say the network has no internet, which is true. The
address is on the AP name and in the serial print; a DNS responder is a second
server to own for a saving of one typed line." Two things in that are wrong.

The first is the price. It is not "one typed line": it is a person holding a
phone that has just said THIS NETWORK HAS NO INTERNET, being expected to
ignore that, leave the notification, open a browser and type four numbers and
three dots -- on the one flow a headless board has no other channel for. What
a portal buys is that the form OPENS BY ITSELF, which is the difference between
a setup with no instructions and a setup that needs some.

The second is that the decline never priced the hijack; it assumed one. The
mechanism was checked before this was reversed, on the sources this board's own
image is built from: ESP-IDF 5.5.1's DHCP server initialises `dhcps_dns = 0x00`
(no DNS option offered) BUT compiles `CONFIG_LWIP_DHCPS_ADD_DNS`, which
defaults to `y` and is `y` in this board's generated sdkconfig -- and its else
branch offers the AP's own address as the DNS server. So a phone joining this
AP is already told to resolve through 192.168.4.1, and answering on :53 is
enough. Nothing has to be configured, which is fortunate, because MicroPython
1.28 exposes no binding for `esp_netif_dhcps_option` at all: neither the DNS
offer flag nor RFC 8910's option 114 (`ESP_NETIF_CAPTIVEPORTAL_URI`, which the
IDF does implement) can be reached from Python. Option 114 would be the honest
modern answer; the responder needs nothing configured.

What it costs, stated so the next person can weigh a reversal back:

  * the kernel's responder (native/moy_net/moy_dns.c, fuzzed and
    host-tested) + `DnsRedirect` + the redirect branch.
  * A SECOND listening socket in the one path that configures the board. So it
    is strictly optional at every step: a bind that fails prints one line and
    setup continues exactly as it did before, a datagram that does not parse is
    dropped, and nothing the responder does can reach the form's own socket.
  * A phone whose captive sheet is a restricted webview rather than its real
    browser. The page is a plain form with no dependencies precisely so that
    the poorest browser on the phone can still render it.
  * Answers carry TTL 0, so nothing this board says about a name outlives the
    phone's stay on this AP. A cached `connectivitycheck.gstatic.com ->
    192.168.4.1` on somebody's home network would be harm we caused.

The HTTP half is one branch: any GET this server does not serve is a 302 to
`http://<ap ip>/`. That is what makes the probes fail in the right direction --
Android's `/generate_204` wants a 204 and Apple's `/hotspot-detect.html` wants
a body reading `Success`, and both read anything else as "there is a portal
here" -- and it is also what a person typing a half-remembered address gets.
"""

import json

import moy_net
from moy_net import http_response

try:
    from ticks import _ticks_ms as ticks_ms, _ticks_diff as ticks_diff
except ImportError:                  # host / CPython: the runtime package
    from runtime.ticks import _ticks_ms as ticks_ms, _ticks_diff as ticks_diff

AP_PREFIX = "moybyte-zero-"
SETUP_PORT = 80
DEFAULT_NAME = "moybyte-zero"

# The SoftAP's own address. `run()` reads the real one off the interface and
# passes it down; this is what the host tests and a `SetupServer` built without
# one use, and it is the address ESP-IDF's SoftAP has had since forever.
AP_IP = "192.168.4.1"

# The captive portal's responder (see the module docstring for why it exists).
DNS_PORT = 53

# How long after answering the form before the board resets. The response has
# to reach the phone first (the transport closes the conn as it returns, but
# lwIP still has bytes to push), and a person needs to read "saved".
REBOOT_MS = 1200

# A scan blocks the radio for a couple of seconds and briefly disturbs the AP
# the phone is sitting on, so a page reload inside this window reuses the last
# one rather than making the person's connection stutter twice.
SCAN_CACHE_MS = 10000

# Hostname characters. It becomes the kernel's mDNS name, i.e. the label the
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


# -- the captive portal ------------------------------------------------------
#
# Two pieces, and the module docstring carries the decision and its cost. The
# HTTP half is `_redirect` plus one branch in `handle_http`; the DNS half is
# the kernel's responder (moy_dns.c: the packet rules and the socket) behind
# `dns_reply` and `DnsRedirect`.


def _redirect(url):
    """A 302 to the setup form.

    Its own builder because `http_response` has no Location header and should
    not grow one for a single caller on one board. A body at all because a
    text browser that does not follow the redirect still has the address.
    """
    body = ('<!doctype html><meta charset="utf-8">'
            '<title>moybyte zero</title>'
            '<p>This board is waiting to be set up at <a href="%s">%s</a>.'
            % (url, url))
    head = ("HTTP/1.1 302 Found\r\n"
            "Location: %s\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            "Content-Length: %d\r\n"
            "Cache-Control: no-store\r\n"
            "Connection: close\r\n\r\n") % (url, len(body))
    return head.encode("utf-8") + body.encode("utf-8")


def dns_reply(query, ip):
    """One DNS query datagram -> the datagram to answer it with, or None: the
    kernel's (native/moy_net/moy_dns.c, whose header has the refusals and
    why every name is answered with `ip`, TTL 0)."""
    if not query:
        return None
    return moy_net.dns_reply(bytes(query), ip)


class DnsRedirect:
    """The responder on :53 (the kernel's, moy_dns.c), and nothing else.
    Optional at every step: `start()` returning False is a supported outcome
    -- the form is still served and the address is still printed -- and
    nothing here may raise into the setup loop."""

    def __init__(self, ip, port=DNS_PORT):
        self.ip = ip
        self.port = port
        self.answered = 0
        self.up = False

    @property
    def bound_port(self):
        return moy_net.dns_port() if self.up else 0

    def start(self):
        self.up = bool(moy_net.dns_start(self.ip, self.port))
        if not self.up:
            print("ZERO SETUP no captive portal (dns: port %s refused) -- the "
                  "form is still at http://%s/" % (self.port, self.ip))
        return self.up

    def poll(self):
        """Answer up to the kernel's per-poll share of queries. Returns how many."""
        if not self.up:
            return 0
        served = moy_net.dns_poll()
        self.answered += served
        return served

    def stop(self):
        if self.up:
            moy_net.dns_stop()
        self.up = False


# The page. One file, no fetches but /scan, no fonts, no frameworks -- it is
# served by a board with ~20KB of free internal SRAM to a phone that may be the
# only computer in the room.
#
# IT IS READ ON A PHONE, and everything below that looks like fussing is one of
# those two facts:
#   * every control is >= 44px tall, which is the tap target both platforms
#     ask for, and inputs are 16px because a smaller one makes iOS ZOOM on
#     focus and a zoomed page needs horizontal scrolling to fill in;
#   * the password field can be REVEALED. A WiFi key typed blind, once, by
#     somebody who cannot see the board is the likeliest way this whole flow
#     fails -- and the failure arrives minutes later as a board that never
#     comes back, with no way to tell a typo from a dead radio.
#
# THE SCRIPT ONLY EVER UPGRADES. Everything it touches has a correct
# no-JavaScript state already in the markup: the network hint reads "type its
# name exactly", the SSID field is an input and not a <select>, and the two
# containers it fills render as nothing when empty. That is the reason the
# reveal checkbox is INJECTED rather than written here -- a checkbox that a
# broken script leaves inert is a control that lies, and this page is reached
# from a phone nobody chose.
_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>moybyte zero setup</title><style>
:root{color-scheme:light dark}
body{margin:0;padding:24px 18px 40px;font:16px/1.5 system-ui,sans-serif;
 max-width:26rem;margin-inline:auto}
h1{font-size:1.35rem;margin:0 0 .25rem}
p.sub{margin:0 0 1.5rem;opacity:.7}
label{display:block;margin:0 0 1.25rem}
span{display:block;font-size:.9rem;opacity:.75;margin-bottom:.35rem}
input{width:100%%;box-sizing:border-box;padding:.7rem;font-size:1rem;
 min-height:44px;border:1px solid #8888;border-radius:8px;
 background:transparent;color:inherit}
button{width:100%%;padding:.85rem;font-size:1.05rem;min-height:48px;border:0;
 border-radius:10px;background:#3b6ea5;color:#fff}
.err{padding:.6rem .8rem;border-radius:8px;background:#c0392b;color:#fff;
 margin-bottom:1rem}
.hint{margin:-.75rem 0 1.25rem;font-size:.9rem;opacity:.75}
#nets{display:flex;flex-wrap:wrap;gap:.5rem;margin:-.75rem 0 1.25rem}
#nets:empty{margin:0}
#nets button{width:auto;min-height:44px;padding:.5rem .9rem;font-size:1rem;
 border:1px solid #8886;background:#8883;color:inherit;border-radius:999px}
#reveal label{display:flex;align-items:center;gap:.5rem;margin:-.75rem 0 1.25rem;
 font-size:.95rem;opacity:.85}
#reveal input{width:auto;min-height:1.4rem;height:1.4rem;flex:0 0 1.4rem}
</style></head><body>
<h1>moybyte zero</h1>
<p class="sub">%s</p>
%s<form method="post" action="/setup">
<label><span>name for this board</span>
<input name="name" value="%s" maxlength="24" autocapitalize="off"
 autocorrect="off" autocomplete="off" spellcheck="false"></label>
<label><span>pin (4 digits, asked for before anything may change this board)</span>
<input name="pin" inputmode="numeric" pattern="[0-9]{4}" maxlength="4"
 autocomplete="off" required></label>
<label><span>wifi network</span>
<input name="ssid" list="netlist" maxlength="32" autocapitalize="off"
 autocorrect="off" autocomplete="off" spellcheck="false" required>
<datalist id="netlist"></datalist></label>
<p class="hint" id="wifihint">Type its name exactly.</p>
<div id="nets"></div>
<label><span>wifi password (leave empty for an open network)</span>
<input name="password" type="password" maxlength="63" autocapitalize="off"
 autocorrect="off" autocomplete="off" spellcheck="false"></label>
<div id="reveal"></div>
<button>save and restart</button></form>
<script>
var f=document.forms[0],pw=f.password;
var sw=document.createElement("label"),cb=document.createElement("input");
cb.type="checkbox";
cb.onchange=function(){pw.type=cb.checked?"text":"password"};
sw.appendChild(cb);sw.appendChild(document.createTextNode("show password"));
document.getElementById("reveal").appendChild(sw);
var hint=document.getElementById("wifihint");
var box=document.getElementById("nets"),list=document.getElementById("netlist");
if(!window.fetch){
 hint.textContent="Cannot list networks here - type its name exactly."}else{
hint.textContent="Looking for networks...";
fetch("scan").then(function(r){return r.json()}).then(function(d){
 var nets=(d&&d.nets)||[];
 if(!nets.length){
  hint.textContent="No networks in range - type its name exactly.";return}
 hint.textContent="Tap yours, or type its name exactly.";
 nets.forEach(function(n){
  var o=document.createElement("option");o.value=n.ssid;list.appendChild(o);
  var b=document.createElement("button");b.type="button";b.textContent=n.ssid;
  b.onclick=function(){f.ssid.value=n.ssid;if(n.lock){pw.focus()}};
  box.appendChild(b)});
}).catch(function(){
 hint.textContent="Could not look for networks - type its name exactly."})}
</script></body></html>"""

_DONE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>moybyte zero</title><style>
body{margin:0;padding:24px 18px 40px;font:16px/1.5 system-ui,sans-serif;
 max-width:26rem;margin-inline:auto}
b{overflow-wrap:anywhere}</style></head><body>
<h1>saved</h1>
<p>This board is restarting and will join <b>%s</b>.</p>
<p>Find it again at <b>http://%s.local/?pin=%s</b> once it is up.</p>
<p>Keep the whole address: the <b>?pin=</b> part is what lets the console save
your work back onto this board. Without it you can play, but nothing you make
is kept.</p>
<p>If your phone cannot find that name, your router's list of connected
devices will show it as <b>%s</b> &mdash; the address there works too.</p>
<p>This setup network is about to disappear &mdash; rejoin your own WiFi.</p>
</body></html>"""


def page(ap_name, error=None, name=DEFAULT_NAME):
    return _PAGE % (
        "connected to %s &mdash; tell this board where to live" % ap_name,
        ('<p class="err">%s</p>' % error) if error else "",
        name)


class SetupServer:
    """The setup form over the kernel's webhost, which parks every request
    (`defer` "*") for `poll()` to answer here.

    `save(clean)` and `scan()` are injected: the persistence and the radio are
    the two things a host test cannot have, and everything between them --
    parsing, validating, what the page says -- is then ordinary testable code.
    """

    def __init__(self, ap_name, save, scan, port=SETUP_PORT, ip=AP_IP):
        self.port = port
        self.serving = False
        self.ap_name = ap_name
        self.ap_ip = ip              # where every unserved GET is sent
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
        if method == "GET":
            # THE CAPTIVE-PORTAL BRANCH, and the reason this is not a 404. A
            # phone that has just joined asks for a URL it knows the answer to
            # (`/generate_204` on Android, `/hotspot-detect.html` on Apple) and
            # reads any other answer as "there is a portal here", which is what
            # opens the form without anybody typing an address. `DnsRedirect`
            # is what brings those requests to this socket at all; this branch
            # is what they get, and it is equally the right answer to a person
            # who typed the address with a path on the end.
            return _redirect("http://%s/" % self.ap_ip)
        return None                  # a POST we do not serve -> 404

    def start(self, ip=None):
        """Bind the kernel's listener with every path parked for this object."""
        try:
            moy_net.web_start(self.port, "/", None, None, None, ("*",))
        except OSError as exc:
            print("ZERO SETUP port %d: %s" % (self.port, exc))
            return False
        self.serving = True
        return True

    def poll(self):
        """One kernel poll, then the parked request answered here."""
        if not self.serving:
            return False
        did = moy_net.web_poll()
        req = moy_net.web_take()
        if req is not None:
            did = True
            try:
                resp = self.handle_http(req[0], req[1], req[2])
            except Exception as exc:      # noqa: BLE001 -- never fail the request
                print("ZERO SETUP %s: %s" % (type(exc).__name__, exc))
                resp = http_response(500, '{"error":"server"}')
            moy_net.web_answer(resp or http_response(
                404, "not found", "text/plain; charset=utf-8"))
        return did

    def stop(self):
        if self.serving:
            moy_net.web_stop()
            moy_net.web_events()
        self.serving = False

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
                                           clean["pin"], clean["name"]),
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


def _scan():
    """What the station sees, in the port's WLAN.scan shape scan_json reads."""
    return [(ssid.encode(), b"", 0, rssi, auth, False)
            for ssid, rssi, auth in moy_net.wifi_scan()]


def run(wifi_store, zero_store, port=SETUP_PORT, sleep_ms=10):
    """Host the setup AP and serve the form until it is filled in. Never
    returns: a good POST resets the board into STA. The access point is the
    kernel's WiFi driver's provisioning mode, the station beside it up for
    the form's scans and never connecting."""
    import machine
    import time

    ssid = ap_ssid(moy_net.wifi_ap_mac())
    ip = moy_net.wifi_ap(ssid) or AP_IP
    srv = SetupServer(ssid,
                      lambda clean: save_setup(clean, wifi_store, zero_store),
                      _scan, port=port, ip=ip)
    if not srv.start(ip):
        raise OSError("setup port %d busy" % port)
    # The portal, second and optional: the form is the thing that must come up.
    dns = DnsRedirect(ip)
    portal = dns.start()
    print("ZERO SETUP  join '%s' (open)%s -- or open http://%s/"
          % (ssid, " and the form opens itself" if portal else "", ip))
    while True:
        srv.poll()
        dns.poll()
        if srv.due():
            srv.stop()
            dns.stop()
            moy_net.wifi_off()
            machine.reset()
        time.sleep_ms(sleep_ms)

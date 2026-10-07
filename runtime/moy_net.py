"""The links as the kernel's native `moy_net` will expose them: the Python
twin (docs/kernel_survival_2026-10.md section 6).

  wifi_password    the password a connect uses: the one given, else the stored
  wifi_remember    whether a connect's credentials are written to the store
  PeerTable        the radio link's peers, rows of kind PEER keyed by MAC
  parse_request    a raw HTTP request -> (method, target, length, header end)
  query_param      one parameter of a request target's query string
  http_response    a complete HTTP/1.1 response, Connection: close
  encode_batch     a sync batch's wire JSON
  decode_batch     a sync batch's fields off the wire, or None when malformed

Pure functions over numbers, strings and bytes, and one table: no socket, no
radio, no file. device/device_wifi.py, device/moy_espnow.py,
device/moy_webserver.py and runtime/moy_sync.py are their callers.
"""

import json


# -- WiFi ---------------------------------------------------------------------

def wifi_password(password, stored):
    """The password a connect to a network uses. An EMPTY one resolves to the
    stored one: the panel's known-network reconnect passes "" (it has no
    credential access), and associating with "" AND remembering it destroyed
    the saved password (the on-glass P4, 2026-07-25)."""
    if not password and stored:
        return stored
    return password


def wifi_remember(ok, password, stored):
    """Whether a connect's credentials are written to the store: when the
    radio ASSOCIATED, or when a non-blank password differs from the stored
    one. Never a blank one the radio did not verify -- "" is both an open
    network and one the store could not tell us about, so remembering it
    unconditionally let one unreadable load rewrite wifi.json as a single
    empty-password entry. A non-blank password is kept even on failure: the
    connect's short poll giving up is the late association moy_ota's
    ensure_online waits for."""
    return bool(ok or (password and password != stored))


# -- the link's peers -----------------------------------------------------------

class PeerTable:
    """The radio link's peers: MAC -> peer object, each a row of kind PEER.
    Reads like the dict it replaces (get, [], del, pop, values, keys, len,
    in, == a dict)."""

    def __init__(self):
        # Imported here: the Zero stages this module for its HTTP and sync
        # halves and carries no spine, and it has no radio link to hold.
        try:
            from moy_spine import KIND_PEER, Table
        except ImportError:                 # host: the runtime package
            from runtime.moy_spine import KIND_PEER, Table
        self._t = Table(KIND_PEER, "peer")
        self._by_mac = {}

    def handle(self, mac):
        """The peer's row handle, or 0."""
        return self._by_mac.get(mac, 0)

    def get(self, mac, default=None):
        h = self._by_mac.get(mac)
        return default if h is None else self._t.get(h)

    def __getitem__(self, mac):
        return self._t.get(self._by_mac[mac])

    def __setitem__(self, mac, peer):
        h = self._by_mac.get(mac)
        if h is None:
            self._by_mac[mac] = self._t.new(peer)
        else:
            self._t.put(h, peer)

    def __delitem__(self, mac):
        self._t.release(self._by_mac.pop(mac))

    def pop(self, mac, default=None):
        h = self._by_mac.pop(mac, None)
        return default if h is None else self._t.release(h)

    def __contains__(self, mac):
        return mac in self._by_mac

    def __len__(self):
        return len(self._by_mac)

    def keys(self):
        return list(self._by_mac.keys())

    def values(self):
        return [self._t.get(h) for h in self._by_mac.values()]

    def __eq__(self, other):
        if isinstance(other, PeerTable):
            other = dict((m, other[m]) for m in other.keys())
        return dict((m, self[m]) for m in self.keys()) == other


# -- HTTP -----------------------------------------------------------------------

def parse_request(raw):
    """Parse a raw HTTP request (bytes or str) into (method, target, content_length,
    header_end). header_end is the index just past the blank line ending the headers (-1 if
    the headers aren't complete yet). A malformed request returns (None, None, 0, -1).

    `target` is the REQUEST TARGET VERBATIM, query string and all. It used to be
    stripped at "?" here, which was the wrong place to do it (2026-08-25): a GET
    carries its pin as `?pin=NNNN` -- the only place a GET can carry anything --
    and the transport was discarding the credential before any handler could see
    it, so gating a read was not expressible. Handlers split it themselves (they
    always did, defensively) and reach the query through `query_param`."""
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8")
        except Exception:  # noqa: BLE001
            text = raw.decode("latin-1")
    else:
        text = raw
    sep = text.find("\r\n\r\n")
    nlen = 4
    if sep < 0:
        sep = text.find("\n\n")
        nlen = 2
    if sep < 0:
        return (None, None, 0, -1)               # headers incomplete
    head = text[:sep]
    lines = head.replace("\r\n", "\n").split("\n")
    if not lines:
        return (None, None, 0, -1)
    parts = lines[0].split(" ")
    if len(parts) < 2:
        return (None, None, 0, -1)
    method = parts[0]
    path = parts[1]
    clen = 0
    for ln in lines[1:]:
        c = ln.find(":")
        if c > 0 and ln[:c].strip().lower() == "content-length":
            try:
                clen = int(ln[c + 1:].strip())
            except Exception:  # noqa: BLE001
                clen = 0
    return (method, path, clen, sep + nlen)


def query_param(target, name):
    """The value of `name` in a request target's query string, or "".

    Deliberately small: no percent-decoding and no `+` handling, because the ONE
    thing that rides a query here is a four-digit pin and a decoder is code that
    can be wrong about a credential. A parameter whose value would need decoding
    is one this does not serve.
    """
    if not target:
        return ""
    q = target.split("?", 1)
    if len(q) < 2:
        return ""
    for pair in q[1].split("&"):
        kv = pair.split("=", 1)
        if kv[0] == name:
            return kv[1] if len(kv) > 1 else ""
    return ""


def http_response(status, body, content_type="application/json"):
    """Build a complete HTTP/1.1 response (bytes). `body` may be str or bytes. The server
    closes the connection after each response (Connection: close), which keeps the
    single-request-per-poll model simple and robust to half-open clients."""
    if isinstance(body, str):
        body = body.encode("utf-8")
    # 403/405/501 joined the table on 2026-08-25: all three were already being
    # SENT (the pin gate, the write-surface refusal, "no runner") and all three
    # went out reading `HTTP/1.1 403 OK`, because an unknown status fell through
    # to "OK". Browsers do not care, but a human reading a capture does, and the
    # page now branches on exactly these.
    reason = {200: "OK", 400: "Bad Request", 403: "Forbidden",
              404: "Not Found", 405: "Method Not Allowed",
              500: "Server Error", 501: "Not Implemented"}.get(status, "OK")
    head = (
        "HTTP/1.1 %d %s\r\n"
        "Content-Type: %s\r\n"
        "Content-Length: %d\r\n"
        "Cache-Control: no-store\r\n"
        "Access-Control-Allow-Origin: *\r\n"
        "Connection: close\r\n\r\n"
    ) % (status, reason, content_type, len(body))
    return head.encode("utf-8") + body


# -- the sync batch -------------------------------------------------------------

def encode_batch(v, root_id, ops, pin=None):
    """A batch's wire JSON: version `v`, its root named only when `root_id` is
    not None (the v1 shape names none), its ops and an optional pin."""
    if root_id is None:
        doc = {"v": v, "ops": ops}
    else:
        doc = {"v": v, "root": root_id, "ops": ops}
    if pin:
        doc["pin"] = pin
    return json.dumps(doc)


def decode_batch(body):
    """(v, root, ops, pin) off the wire, or None when the body is not UTF-8,
    not JSON or not an object. Which root `v` and `root` name, and whether the
    ops are a list, are the receiver's questions (moy_sync.parse_batch).
    Tolerant of bytes (the transport hands bytes)."""
    if isinstance(body, bytes):
        try:
            body = body.decode("utf-8")
        except Exception:  # noqa: BLE001
            return None
    try:
        doc = json.loads(body)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(doc, dict):
        return None
    ops = doc.get("ops")
    return doc.get("v"), doc.get("root"), ops, doc.get("pin")

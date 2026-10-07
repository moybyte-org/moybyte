# Map (grep -n a name to jump there):
#   parse_url                        a URL into scheme, host, port and path
#   http_open                        http_open_once plus redirects
#   http_open_once                   connect, send a GET, read the headers
"""The streaming HTTP(S) client a board fetches through: the OTA updater
(device/moy_ota.py) and Get Carts' transport (device/cart_net.py).

No urequests: it buffers the whole body, and a firmware image does not fit.
A GET here returns the socket with the headers read, and the body stays in
the socket for the caller to stream. Redirects are followed, which is what
makes a GitHub release asset reachable (a 302 to its CDN). `agent` is the
User-Agent and `log` the trace's sink, both the caller's.
"""

AGENT = "moybyte"


def _ms():
    """A start stamp for _ms_since. ticks_ms on the device, monotonic on the host
    -- this module is imported by the host tests, so every device call in here needs
    a CPython answer too."""
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


def _log(*a):
    try:
        print("Moybyte http:", *a)
    except Exception:
        pass


def parse_url(url):
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


def http_open(url, hops=4, agent=AGENT, log=None):
    """`http_open_once` + redirect following, which is what makes the
    GitHub-hosted channels (DEFAULT_CHANNEL_URLS) reachable: a release
    download is a 302 to the objects.githubusercontent.com CDN, and the
    manifest beside it redirects the same way. Returns the FINAL response as
    (sock, status, content_length, leftover_body_bytes); the body is the
    socket's to read. `agent` is the User-Agent, `log` the trace's sink."""
    log = log or _log
    seen = 0
    while True:
        sock, code, clen, rest, loc = http_open_once(url, agent, log)
        if code not in (301, 302, 303, 307, 308) or not loc or seen >= hops:
            return sock, code, clen, rest
        try:
            sock.close()
        except Exception:
            pass
        seen += 1
        # A relative Location is legal; resolve it against the current host.
        if loc.startswith("/"):
            scheme, host, port, _ = parse_url(url)
            dflt = 443 if scheme == "https" else 80
            loc = "%s://%s%s%s" % (scheme, host,
                                   "" if port == dflt else ":%d" % port, loc)
        log("redirect %d -> %s" % (code, loc))
        url = loc


def http_open_once(url, agent=AGENT, log=None):
    """Connect + send GET + read the response headers. Returns
    (sock, status_code, content_length, leftover_body_bytes, location)."""
    import socket

    log = log or _log
    scheme, host, port, path = parse_url(url)
    log("http_open %s host=%s port=%d path=%s" % (scheme, host, port, path))
    ai = socket.getaddrinfo(host, port)[0]
    log("getaddrinfo ->", ai[-1])
    sock = socket.socket(ai[0], ai[1], ai[2])
    sock.settimeout(15)
    sock.connect(ai[-1])
    log("connected")
    if scheme == "https":
        import ssl

        sock = ssl.wrap_socket(sock, server_hostname=host)
        log("tls wrapped")
    req = ("GET %s HTTP/1.0\r\nHost: %s\r\n"
           "User-Agent: %s\r\nConnection: close\r\n\r\n" % (path, host, agent))
    sock.write(req.encode())
    log("request sent, reading headers")

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
            log("WARNING: header block over 16K, giving up on the rest")
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
    log("http status=%d content-length=%d hdr=%dB in %dms loc=%s"
         % (code, clen, len(hdr), _ms_since(t0), loc))
    return sock, code, clen, rest, loc

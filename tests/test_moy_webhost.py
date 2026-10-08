"""Serving the web console from the board (moycore plan 3.4, the pull half).

The server is the kernel's (native/moy_net/moy_webhost.c), run here through
the same C over ctypes; `device/moy_webhost.py` is the Settings contract over
it and the routes the VM answers. The bet this slice makes is that the page
needs NO change to read a board: it boots with `fetch("carts.json")` -- a
relative url -- so a page served from the console fetches the console's
store. That bet is only good if the board emits exactly the bundle shape
`worker.js` consumes, so most of what is worth testing here is the SHAPE, plus
the things that would go wrong on real hardware and nowhere else: a megabyte
held in RAM, a store touched against a band in flight, and a path from the
network reaching a file that is none of the browser's business.
"""

import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

from board_source import runtime_text

ROOT = Path(__file__).resolve().parent.parent
MODULES = ROOT / "device"
sys.path.insert(0, str(MODULES))

import moy_webhost as wh                                       # noqa: E402
from runtime import net_binding as nb                           # noqa: E402


@pytest.fixture(autouse=True)
def _kernel_host():
    """The kernel's webhost is one per process: each test starts it unbaked
    and leaves it stopped."""
    nb._bake({})
    yield
    nb.web_stop()
    nb.web_events()
    nb._bake({})


def _store(tmp_path):
    """A carts root shaped like a real one, thumbs and all."""
    root = tmp_path / "carts"
    (root / "hop.moy").mkdir(parents=True)
    (root / "hop.moy" / "main.py").write_text("def _draw():\n    cls(1)\n")
    (root / "hop.moy" / "manifest.json").write_text('{"title": "Hop"}')
    (root / "hop.moy" / "images").mkdir()
    (root / "hop.moy" / "images" / "bg.moyimg").write_text("0,0,")
    (root / "hop.moy" / "thumbs").mkdir()
    (root / "hop.moy" / "thumbs" / "wp320x240.mct").write_text("CACHE")
    # The durable undo history moy_journal writes after the first commit: the
    # log, the per-file cursor, and one full-file SNAPSHOT per commit.
    (root / "hop.moy" / "journal" / "s").mkdir(parents=True)
    (root / "hop.moy" / "journal" / "journal.jsonl").write_text(
        '{"seq": 1, "file": "main.py", "snap": "s/0001-main.py"}\n')
    (root / "hop.moy" / "journal" / "cursor.json").write_text('{"bytes": 24}')
    (root / "hop.moy" / "journal" / "s" / "0001-main.py").write_text(
        "def _draw():\n    cls(7)\n")
    (root / "hop.moy" / "journal.jsonl").write_text("stray flat log\n")
    # moy_fs._write_atomic's last-known-good rotations, one per file it saves.
    (root / "hop.moy" / "main.py.bak").write_text("def _draw():\n    cls(2)\n")
    (root / "hop.moy" / "manifest.json.bak").write_text('{"title": "Hop"}')
    (root / "hop.moy" / "pmem.json").write_text("[41, 0, 0]")
    (root / "sky.moy").mkdir()
    (root / "sky.moy" / "main.lua").write_text("function _draw() end\n")
    (root / "loose.txt").write_text("not a cart")
    return root


def h_status(resp):
    return resp.split(b"\r\n")[0]


def _head(resp):
    return resp.split(b"\r\n\r\n", 1)[0].decode("latin-1")


def _chunks(resp):
    """A chunked response's chunks, checking the framing as it goes."""
    head, wire = resp.split(b"\r\n\r\n", 1)
    assert b"Transfer-Encoding: chunked" in head
    assert wire.endswith(b"0\r\n\r\n"), wire[-16:]
    out = []
    while True:
        size_s, _, wire = wire.partition(b"\r\n")
        n = int(size_s, 16)
        if n == 0:
            return out
        out.append(wire[:n])
        assert wire[n:n + 2] == b"\r\n"
        wire = wire[n + 2:]


def _body(resp):
    if b"Transfer-Encoding: chunked" in resp.split(b"\r\n\r\n", 1)[0]:
        return b"".join(_chunks(resp))
    return resp.split(b"\r\n\r\n", 1)[1]


def _json(resp):
    return json.loads(_body(resp))


def _pull(root, path="/carts.json"):
    """The pull the page makes, through the whole router."""
    return _json(wh.WebHost(str(root)).handle_http("GET", path, b""))


def test_the_bundle_is_the_shape_the_page_already_consumes(tmp_path):
    """`{"<cart>/<rel>": text}` -- worker.js's writeCarts input, and what the
    dev server (web_runner/serve.py --carts) emits. A different shape here
    means the page needs a branch, and the whole point of this slice is that it
    does not."""
    b = wh.pack_store(str(_store(tmp_path)))
    assert b["hop.moy/main.py"].startswith("def _draw()")
    assert b["hop.moy/manifest.json"] == '{"title": "Hop"}'
    assert b["hop.moy/images/bg.moyimg"] == "0,0,"    # nested dirs keep their path
    assert b["sky.moy/main.lua"].startswith("function")
    assert all("/" in k for k in b), "a key without a cart prefix"
    assert _pull(tmp_path / "carts") == b, "the dev server and the wire differ"


def test_thumbs_do_not_cross_the_wire(tmp_path):
    """`thumbs/` is a REGENERABLE cache keyed to a screen size (#66/#155) --
    sending it costs transfer time for pixels the browser will rebuild at its
    own size anyway. It is also the largest thing in a cart folder."""
    b = _pull(_store(tmp_path))
    assert not any("thumbs" in k for k in b), sorted(b)


def test_the_undo_journal_does_not_cross_the_wire(tmp_path):
    """The bulk of a real store, and inert at the far end: each side keeps its
    own undo history."""
    b = _pull(_store(tmp_path))
    assert not [k for k in b if "journal" in k], sorted(b)
    assert "hop.moy/main.py" in b                    # the cart itself survives


def test_the_atomic_write_backups_do_not_cross_the_wire(tmp_path):
    """`<file>.bak` is moy_fs's crash-recovery rotation: the live file is
    already in the bundle, so every one of these is a second copy of content
    the browser has, on a transport whose size is the whole problem."""
    b = _pull(_store(tmp_path))
    assert not [k for k in b if k.endswith(".bak")], sorted(b)
    assert b["hop.moy/main.py"].startswith("def _draw()")


def test_a_kids_saves_still_cross_the_wire(tmp_path):
    """The contrast that makes the journal cut a judgement and not a diet:
    pmem.json is their score, their pet, where they got to."""
    assert _pull(_store(tmp_path)).get("hop.moy/pmem.json") == "[41, 0, 0]"


def test_a_carts_cover_crosses_the_wire_as_its_bytes(tmp_path):
    """The one binary file a pull carries: a cart's cover.png (SPEC.md 3.6),
    as {"b": base64} -- the page's writeStore writes it as bytes. A compiled
    module beside it stays home. Bigger than one read, so its pieces must join
    into the one value."""
    import base64
    root = _store(tmp_path)
    data = bytes((i * 37 + 11) & 255 for i in range(10000))
    (root / "hop.moy" / "cover.png").write_bytes(data)
    (root / "hop.moy" / "main.wasm").write_bytes(b"\0asm\1\0\0\0\xff\xfe")
    b = _pull(root)
    assert base64.b64decode(b["hop.moy/cover.png"]["b"]) == data
    assert "hop.moy/main.wasm" not in b


def test_a_loose_file_beside_the_carts_is_not_a_cart(tmp_path):
    b = _pull(_store(tmp_path))
    assert not any(k.startswith("loose") for k in b)


def test_an_unreadable_file_is_skipped_not_fatal(tmp_path):
    """A binary asset in a cart folder must not take the whole endpoint down --
    the store is the kid's, and one odd file in it should cost that file. And
    it is ABSENT, never present and empty."""
    root = _store(tmp_path)
    (root / "hop.moy" / "blob.bin").write_bytes(b"\xff\xfe\x00\x01")
    b = _pull(root)
    assert "hop.moy/blob.bin" not in b
    assert "hop.moy/main.py" in b, "one bad file killed the rest"


def test_an_empty_file_crosses_as_an_empty_value(tmp_path):
    root = _store(tmp_path)
    (root / "hop.moy" / "empty.txt").write_text("")
    assert _pull(root)["hop.moy/empty.txt"] == ""


def test_a_file_that_stops_being_text_ends_its_value(tmp_path):
    """Decided on the first piece: past it, bytes that cannot be UTF-8 END the
    value, so the response stays valid JSON holding a short file rather than
    a pull that dies half way."""
    root = _store(tmp_path)
    (root / "hop.moy" / "odd.txt").write_bytes(b"a" * 5000 + b"\xff" + b"b" * 10)
    v = _pull(root)["hop.moy/odd.txt"]
    assert v == "a" * len(v) and 4096 <= len(v) <= 5000


def test_a_big_file_is_streamed_in_pieces_and_never_held(tmp_path):
    """The 2026-09-09 Guition fix, at the wire: no chunk is as big as the file
    it came from. A store with 142KB PICO-8 carts on it used to die every pull
    on copies of a whole value."""
    root = _store(tmp_path)
    text = "-- a line of a cart\n" * 9000                        # 180KB
    (root / "hop.moy" / "big.lua").write_text(text)
    r = wh.WebHost(str(root)).handle_http("GET", "/carts.json", b"")
    sizes = [len(c) for c in _chunks(r)]
    assert max(sizes) <= 8192, max(sizes)
    assert _json(r)["hop.moy/big.lua"] == text


def test_a_value_split_across_pieces_is_still_the_file(tmp_path):
    """A file is read 4 KB at a time and escaped piece by piece. Quotes,
    backslashes, control characters and multi-byte UTF-8 across a piece
    boundary are where that gets tested, so put them there deliberately."""
    nasty = ('a"b\\c\td\n\x01e' * 200 + "héllo 中文 \U0001f600")
    for lead in range(4090, 4097):
        text = ("." * lead) + nasty * 6
        root = tmp_path / ("s%d" % lead) / "carts"
        (root / "odd.moy").mkdir(parents=True)
        (root / "odd.moy" / "nasty.txt").write_text(text)
        assert _pull(root)["odd.moy/nasty.txt"] == text, lead


def test_the_stream_escapes_what_json_requires(tmp_path):
    root = tmp_path / "carts"
    (root / "odd.moy").mkdir(parents=True)
    nasty = 'q = "hi"\\ntab\\there\\n\\u0001 \\\\ backslash\\r\\n\x7f\x1f'
    (root / "odd.moy" / "main.py").write_text(nasty)
    assert _pull(root)["odd.moy/main.py"] == nasty


# -- the router --------------------------------------------------------------


def _host(tmp_path, **kw):
    """A host over the `_store` tree, OPEN (the LAN dev-loop shape) unless a
    test passes a pin. Assets come from the firmware image, so a test that
    wants one installs it with the `baked` fixture. Port 0: a start binds
    nothing, so a test needs no free port to switch it on and off."""
    kw.setdefault("port", 0)
    return wh.WebHost(str(_store(tmp_path)), **kw)


@pytest.fixture
def baked():
    """Install a baked bundle (native/moy_web's table) for one test."""
    return nb._bake


ASSETS = None


def _assets():
    sys.path.insert(0, str(ROOT / "tools"))
    import gen_web_blob
    return gen_web_blob.asset_names()


def test_the_root_serves_the_console_page(tmp_path, baked):
    baked({"index.html.gz": b"\x1f\x8bpage"})
    h = _host(tmp_path)
    for path in ("/", "/index.html"):
        r = h.handle_http("GET", path, b"")
        assert b"200" in h_status(r), path
        assert "Content-Type: text/html" in _head(r)
        assert "Content-Encoding: gzip" in _head(r)
        assert r.endswith(b"\x1f\x8bpage")


def test_carts_json_is_json_and_live(tmp_path):
    h = _host(tmp_path)
    r = h.handle_http("GET", "/carts.json", b"")
    head = _head(r)
    assert "application/json" in head
    assert "no-store" in head, "the store is live; caching it serves stale carts"
    assert "Content-Length" not in head, "chunked and length are exclusive"
    assert _json(r)["hop.moy/main.py"].startswith("def _draw()")


def test_only_the_known_assets_are_reachable(tmp_path, baked):
    """An ALLOWLIST, not a `..` check. The set of files a browser needs is
    fixed at build time, so accepting a path from the network buys nothing and
    risks handing out wifi.json -- and "reject .." is one encoding trick from
    being wrong."""
    baked({"secret.txt": b"wifi password", "index.html.gz": b"\x1f\x8b"})
    h = _host(tmp_path)
    (tmp_path / "secret.txt").write_text("wifi password")
    for path in ("/secret.txt", "/../secret.txt", "/carts/hop.moy/main.py",
                 "/%2e%2e/secret.txt", "/wifi.json", "/index.html.gz"):
        r = h.handle_http("GET", path, b"")
        assert b"404" in h_status(r) and b"wifi password" not in r, path


def test_a_query_string_does_not_hide_an_asset(tmp_path, baked):
    """The page opens itself with `?dev=1`; a served index must survive it."""
    baked({"index.html.gz": b"\x1f\x8bpage"})
    r = _host(tmp_path).handle_http("GET", "/?dev=1", b"")
    assert b"200" in h_status(r) and r.endswith(b"page")


def test_a_board_with_no_bundle_says_how_to_get_one(tmp_path):
    """A firmware built with no web bundle looks exactly like a broken feature
    in a browser. Name the build step, since that is the whole fix."""
    r = _host(tmp_path).handle_http("GET", "/", b"")
    assert b"404" in h_status(r)
    assert b"web_runner/dist" in r and b"reflash" in r


def test_a_raw_baked_asset_is_served_without_an_encoding(tmp_path, baked):
    """A bundle baked without its .gz is served raw, and must not claim to be
    gzip -- a browser handed raw bytes under that header sees garbage."""
    baked({"worker.js": b"// raw"})
    r = _host(tmp_path).handle_http("GET", "/worker.js", b"")
    assert b"200" in h_status(r) and r.endswith(b"// raw")
    assert "Content-Encoding" not in _head(r)
    assert "javascript" in _head(r)


def test_the_baked_stamp_answers_which_console_this_board_serves(baked):
    baked({"index.html.gz": b"x"}, stamp="4 572693 d44b8756bf08")
    assert wh.baked_stamp() == "4 572693 d44b8756bf08"
    assert "4 572693 d44b8756bf08" in wh.WebHost("/x").source_note()
    baked({})
    assert wh.baked_stamp() is None
    assert "NONE" in wh.WebHost("/x").source_note()


@pytest.mark.parametrize("name", _assets())
def test_every_allowlisted_asset_has_a_sane_content_type(tmp_path, baked, name):
    """A wrong type on `micropython.wasm` is the difference between the console
    booting and the browser refusing to instantiate it -- and it fails only in
    a real browser, which is the expensive place to find out."""
    baked({name + ".gz": b"\x1f\x8bx"})
    ctype = _head(_host(tmp_path).handle_http("GET", "/" + name, b""))
    ctype = [l for l in ctype.split("\r\n") if l.startswith("Content-Type")][0]
    if name.endswith(".wasm"):
        assert ctype == "Content-Type: application/wasm"
    if name.endswith(".js") or name.endswith(".mjs"):
        assert "javascript" in ctype
    if name.endswith(".html"):
        assert "text/html" in ctype


def test_an_asset_is_never_cached(tmp_path, baked):
    """The regression that shipped once: a day-long cache on files whose whole
    delivery mechanism is a new build."""
    baked({"worker.js.gz": b"\x1f\x8bx"})
    assert "no-store" in _head(_host(tmp_path).handle_http("GET", "/worker.js", b""))


def test_the_bundle_send_drains_nothing(tmp_path, baked):
    """The T-Deck's drain exists because its card shares the panel's SPI host.
    The baked bundle is in flash and races nothing, so serving it waits on no
    band in flight."""
    baked({"worker.js.gz": b"\x1f\x8bx"})
    h = _host(tmp_path)
    before = nb._drains()
    h.handle_http("GET", "/worker.js", b"")
    h.handle_http("GET", "/sync", b"")
    assert nb._drains() == before


def test_a_write_anywhere_but_sync_is_still_405(tmp_path):
    """The write surface is exactly the named paths. Every other method+path
    pair stays refused with a reason the page can read (405, not a 404 that
    reads as a wrong url)."""
    h = _host(tmp_path)
    r = h.handle_http("POST", "/carts.json", b"{}")
    assert b"405" in h_status(r)
    assert b"/sync" in r
    r = h.handle_http("PUT", "/sync", b"{}")
    assert b"405" in h_status(r)
    r = h.handle_http("PUT", "/update", b"{}")
    assert b"405" in h_status(r), "a wrong verb on a VM route is still 405"


# -- over a real socket --------------------------------------------------------


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _exchange(host, raw, polls=400):
    """Send `raw` to the serving host from a thread, pumping its poll the way
    the frame loop does, and return everything it answered."""
    out = {}

    def client():
        c = socket.create_connection(("127.0.0.1", host.port), timeout=5)
        c.sendall(raw)
        got = b""
        while True:
            k = c.recv(65536)
            if not k:
                break
            got += k
        c.close()
        out["r"] = got

    t = threading.Thread(target=client)
    t.start()
    for _ in range(polls):
        host.poll()
        if not t.is_alive():
            break
        time.sleep(0.005)
    t.join(5)
    assert not t.is_alive(), "the exchange never finished"
    return out["r"]


@pytest.fixture
def serving(tmp_path):
    hosts = []

    def make(**kw):
        h = wh.WebHost(str(_store(tmp_path)), port=_free_port(), **kw)
        h.start(ip="127.0.0.1")
        hosts.append(h)
        return h
    yield make
    for h in hosts:
        h.stop()


def test_the_baked_body_streams_byte_for_byte(serving, baked):
    """The send over a REAL socket: a body whose size is deliberately not a
    multiple of the slice it goes out in, compared byte for byte -- a wrong
    last slice is a wasm binary the browser refuses with nothing to say why."""
    payload = bytes(range(256)) * 17 + b"tail"      # 4356 B
    baked({"micropython.wasm.gz": payload})
    h = serving()
    got = _exchange(h, b"GET /micropython.wasm HTTP/1.1\r\nHost: x\r\n\r\n")
    head, _, body = got.partition(b"\r\n\r\n")
    assert b"Content-Length: %d" % len(payload) in head
    assert body == payload


def test_the_chunked_wire_format_is_well_formed(serving):
    """`<hex>\\r\\n<data>\\r\\n` per chunk, `0\\r\\n\\r\\n` to end, over a real
    socket: a browser is unforgiving here and the failure is a page that hangs
    mid-load with no error."""
    h = serving()
    got = _exchange(h, b"GET /carts.json HTTP/1.1\r\n\r\n")
    assert json.loads(b"".join(_chunks(got)))["hop.moy/pmem.json"] == "[41, 0, 0]"


def test_a_post_body_split_across_packets_is_read_whole(serving):
    h = serving()
    body = json.dumps({"v": 1, "ops": [{"p": "hop.moy/main.py",
                                         "t": "x" * 20000}]}).encode()
    raw = (b"POST /sync HTTP/1.1\r\nContent-Length: %d\r\n\r\n" % len(body)) + body
    got = _exchange(h, raw)
    assert json.loads(got.split(b"\r\n\r\n", 1)[1]) == {"ok": 1, "err": []}


def test_a_request_the_vm_answers_is_parked_and_answered(serving):
    """/run is the console's: the kernel parks it, the poll takes it, the VM
    answers it -- the C itself never calls up."""
    seen = []
    h = serving(on_run=lambda name: (seen.append(name), "Hop")[1])
    got = _exchange(h, b'POST /run HTTP/1.1\r\nContent-Length: 15\r\n\r\n'
                       b'{"cart": "hop"}')
    assert b"200" in h_status(got)
    assert json.loads(got.split(b"\r\n\r\n", 1)[1]) == {"run": "Hop"}
    assert seen == ["hop"]


def test_a_parked_request_nobody_takes_is_a_503(tmp_path):
    """A VM gone mid-request (a soft reset) must not leave the browser hanging:
    the kernel answers it itself once the window is out."""
    h = wh.WebHost(str(_store(tmp_path)), port=_free_port())
    h.start(ip="127.0.0.1")
    try:
        c = socket.create_connection(("127.0.0.1", h.port), timeout=5)
        c.sendall(b"GET /update HTTP/1.1\r\n\r\n")
        for _ in range(100):
            nb.web_poll()                     # the kernel's poll alone: no VM
            if nb.web_state()[2]:
                break
            time.sleep(0.005)
        assert nb.web_state()[2], "never parked"
        nb._skew_clock(3001)
        nb.web_poll()
        got = c.recv(4096)
        c.close()
        assert b"503" in h_status(got) and b"busy" in got
    finally:
        h.stop()


def test_start_refuses_to_report_serving_when_the_bind_failed(tmp_path):
    """Not checking the bind is how a row comes to say ON over a server that is
    serving nothing."""
    s = socket.socket()
    s.bind(("0.0.0.0", 0))
    s.listen(1)
    try:
        h = wh.WebHost(str(tmp_path / "carts"), port=s.getsockname()[1])
        with pytest.raises(OSError):
            h.start(ip="10.0.0.5")
        assert h.serving is False
    finally:
        s.close()


def test_a_host_the_kernel_kept_serving_is_adopted(serving):
    """A soft reset leaves the kernel's webhost serving; the next VM's host is
    serving from construction, and takes the radio lease it rides on."""
    h = serving()
    again = wh.WebHost(h.carts_root, port=h.port)
    assert again.serving is True
    held = []

    class _WS:
        wifi = None

        def wifi_hold(self, tag):
            held.append(tag)
    assert again.adopt(_WS()) is True and held == ["web"]
    other = wh.WebHost(h.carts_root, port=h.port + 1)
    assert other.serving is False, "a host on another port is not this one"


# ---------------------------------------------------------------------------
# POST /sync -- the push half (moycore plan 3.4; moy_sync carries the batch
# semantics and its own suites; what belongs HERE is the endpoint's wiring:
# routing, the pin gate, the feeder drain, and the shelf-refresh event).
# ---------------------------------------------------------------------------

def _batch(*ops, pin=None, root=None):
    doc = ({"v": 1, "ops": list(ops)} if root is None else
           {"v": 2, "root": root, "ops": list(ops)})
    if pin is not None:
        doc["pin"] = pin
    return json.dumps(doc).encode()


def test_sync_applies_into_the_store(tmp_path):
    root = _store(tmp_path)
    h = wh.WebHost(str(root))
    r = h.handle_http("POST", "/sync",
                      _batch({"p": "hop.moy/main.py", "t": "x = 7\n"}))
    assert b"200" in r.split(b"\r\n")[0]
    body = json.loads(r.split(b"\r\n\r\n", 1)[1])
    assert body == {"ok": 1, "err": []}
    assert (root / "hop.moy" / "main.py").read_text() == "x = 7\n"


def test_sync_refuses_a_bad_batch_and_a_bad_path(tmp_path):
    root = _store(tmp_path)
    h = wh.WebHost(str(root))
    assert b"400" in h.handle_http("POST", "/sync", b"junk").split(b"\r\n")[0]
    r = h.handle_http("POST", "/sync",
                      _batch({"p": "../../wifi.json", "t": "stolen"}))
    body = json.loads(r.split(b"\r\n\r\n", 1)[1])
    assert body["ok"] == 0 and body["err"]
    assert not (tmp_path / "wifi.json").exists()


def test_sync_pin_gate(tmp_path):
    """pin=None keeps the desk's open dev loop; a host built WITH a pin
    refuses a batch that does not carry it, before any op is looked at."""
    root = _store(tmp_path)
    h = wh.WebHost(str(root), pin="4321")
    op = {"p": "hop.moy/main.py", "t": "x = 9\n"}
    r = h.handle_http("POST", "/sync", _batch(op))
    assert b"403" in r.split(b"\r\n")[0]
    assert (root / "hop.moy" / "main.py").read_text() != "x = 9\n"
    r = h.handle_http("POST", "/sync", _batch(op, pin="4321"))
    assert b"200" in r.split(b"\r\n")[0]
    assert (root / "hop.moy" / "main.py").read_text() == "x = 9\n"


# ---------------------------------------------------------------------------
# THE PIN GATES EVERYTHING (owner call 2026-08-25). Everything except the boot
# assets and the capability marker, and each of those two is open for its own
# stated reason -- so both the refusals and the exemptions are pinned here.
# ---------------------------------------------------------------------------


def _pinned(tmp_path):
    h = _host(tmp_path)
    h.pin = "4321"
    return h


def test_a_pinned_board_refuses_its_store_to_a_page_with_no_pin(tmp_path):
    """The reversal. This used to be open on the reasoning that a read changes
    nothing -- what it reads is a child's work off their console, to anyone who
    can reach the port."""
    h = _pinned(tmp_path)
    for path in ("/carts.json", "/files.json"):
        r = h.handle_http("GET", path, b"")
        assert isinstance(r, bytes), path
        assert b"403" in r.split(b"\r\n")[0], path
        # A JSON body, and a status the page can tell apart from a 404: the
        # worker branches on exactly this to raise its pin prompt.
        assert json.loads(r.split(b"\r\n\r\n", 1)[1]) == {"error": "pin"}
        assert b"application/json" in r


def test_the_pin_rides_the_query_because_a_get_has_nowhere_else(tmp_path):
    h = _pinned(tmp_path)
    r = h.handle_http("GET", "/carts.json?pin=4321", b"")
    assert _json(r)["hop.moy/main.py"]
    # Wrong, empty and prefix-lookalike are all refusals.
    for q in ("?pin=0000", "?pin=", "?pinned=4321", "?dev=1", "?pin=43210"):
        r = h.handle_http("GET", "/carts.json" + q, b"")
        assert b"403" in h_status(r), q
    # ...and the pin survives company on the query.
    r = h.handle_http("GET", "/carts.json?dev=1&pin=4321", b"")
    assert b"200" in h_status(r)


def test_a_post_wants_its_pin_in_the_body_and_ignores_the_query(tmp_path):
    """The asymmetry, pinned because it cost a wrong instruction in the Zero's
    README: a GET carries the pin on the QUERY (it has nowhere else) and POST
    /sync carries it in the BODY, which is what the protocol envelope declares
    and what `doc["pin"]` reads. Writing `POST /sync?pin=NNNN` looks obviously
    right, is refused, and the refusal says `{"error":"pin"}` -- which reads as
    a wrong pin rather than a misplaced one."""
    h = _pinned(tmp_path)
    op = {"p": "hop.moy/main.py", "t": "x = 1\n"}

    r = h.handle_http("POST", "/sync?pin=4321", _batch(op))
    assert b"403" in r.split(b"\r\n")[0], "a query pin must not authorize a POST"
    assert json.loads(r.split(b"\r\n\r\n", 1)[1]) == {"error": "pin"}

    r = h.handle_http("POST", "/sync", _batch(op, pin="4321"))
    assert b"200" in r.split(b"\r\n")[0], "the body pin is the one that works"


def test_a_whole_cart_delete_takes_the_cart_and_its_journal(tmp_path):
    """`{"p": "<cart>.moy", "dc": 1}` is the documented way to remove a
    built-in that shipped broken from a board whose seed reads PRESENCE and will
    not overwrite it (the Zero). Over HTTP, so the recovery needs no cable and
    cannot wedge that board's USB the way an mpremote session can."""
    root = _store(tmp_path)
    h = wh.WebHost(str(root), pin="4321")
    (root / "hop.moy" / "journal").mkdir(parents=True, exist_ok=True)
    (root / "hop.moy" / "journal" / "journal.jsonl").write_text("{}\n")
    assert (root / "hop.moy").is_dir()

    r = h.handle_http("POST", "/sync",
                      _batch({"p": "hop.moy", "dc": 1}, pin="4321"))
    assert b"200" in r.split(b"\r\n")[0]
    assert not (root / "hop.moy").exists(), (
        "the cart survived -- and a journal left behind is what makes the next "
        "boot's re-seed land on top of stale history")


def test_the_boot_assets_stay_open_or_nothing_can_ask_for_the_pin(tmp_path, baked):
    """The one exemption that is a NECESSITY, not a judgement: the page is what
    shows the pin prompt, so a board that gated its own console behind the pin
    would have nothing left to ask the question with. These are the same bytes
    every build ships and say nothing about this board."""
    baked({n + ".gz": b"\x1f\x8b" + n.encode() for n in _assets()})
    h = _pinned(tmp_path)
    for path in ("/", "/index.html", "/worker.js", "/micropython.wasm"):
        assert b"200" in h_status(h.handle_http("GET", path, b"")), path


def test_the_capability_marker_stays_open_and_says_only_that(tmp_path):
    """GET /sync is the page's MODE decision (board store vs browser store),
    made before it has any pin to offer -- and all it reveals is that a board
    lives here: no cart, no name, no file."""
    h = _pinned(tmp_path)
    r = h.handle_http("GET", "/sync", b"")
    assert b"200" in r.split(b"\r\n")[0]
    assert json.loads(r.split(b"\r\n\r\n", 1)[1]) == {"sync": 1}


def test_an_open_host_is_open_end_to_end(tmp_path):
    """pin=None -- a test, the dev server -- keeps the LAN dev loop free of a
    password."""
    h = _host(tmp_path)
    assert b"200" in h_status(h.handle_http("GET", "/carts.json", b""))
    assert b"200" in h_status(h.handle_http(
        "POST", "/sync", _batch({"p": "hop.moy/main.py", "t": "x\n"})))


def test_a_refused_read_never_touches_the_store(tmp_path):
    """Refusing AFTER the walk would leak its timing and, on the T-Deck, wait
    on the panel feeder to do it -- the gate comes first."""
    h = _pinned(tmp_path)
    before = nb._drains()
    h.handle_http("GET", "/carts.json", b"")
    h.handle_http("GET", "/files.json", b"")
    h.handle_http("POST", "/sync", _batch({"p": "hop.moy/main.py", "t": "x"}))
    assert nb._drains() == before, "a refused request touched the store"


# ---------------------------------------------------------------------------
# The receiver journals what it is handed (2026-08-25): this board is the store
# of record for the page it serves, so a browser commit has to be walkable by
# the console's own UNDO.
# ---------------------------------------------------------------------------


def test_a_push_extends_the_carts_own_journal(tmp_path):
    """A browser-made commit is indistinguishable from a keyboard-made one to
    the Editor's UNDO -- which is the whole point of using `journal_append` and
    not a second history format. `_store` already seeds the one commit an
    on-glass edit would have left, so this also pins that the two interleave in
    ONE timeline rather than the push starting a parallel log."""
    from runtime import moy_journal

    root = _store(tmp_path)
    h = wh.WebHost(str(root))
    for src in ("cls(2)\n", "cls(3)\n"):
        r = h.handle_http("POST", "/sync",
                          _batch({"p": "hop.moy/main.py", "t": src}))
        assert b"200" in r.split(b"\r\n")[0]
    entries = moy_journal._journal_load_entries(
        str(root / "hop.moy" / "journal" / "journal.jsonl"))
    assert [e["seq"] for e in entries] == [1, 2, 3], entries
    # ...and they are real commits: the console's UNDO walks one back.
    assert moy_journal.journal_undo(str(root / "hop.moy"), ("main.py",))
    assert (root / "hop.moy" / "main.py").read_text() == "cls(2)\n"


def test_the_journal_a_push_writes_still_never_travels_back(tmp_path):
    """The receiving side journals; the PULL is unchanged. A board that has
    taken browser commits must not then serve their history to the next page --
    that is the 2026-08-22 decision ("the browser gets carts, not their
    history") and the receiver's own journal is not an exception to it."""
    h = _host(tmp_path)
    h.handle_http("POST", "/sync", _batch({"p": "hop.moy/main.py", "t": "z\n"}))
    body = _json(h.handle_http("GET", "/carts.json", b""))
    assert not any("journal" in k for k in body), sorted(body)


def test_sync_apply_drains_the_feeder_once_first(tmp_path):
    """On the T-Deck every one of these writes lands on the card that shares
    the panel's SPI host: the apply waits out the band in flight ONCE, before
    its first write, and holds the bus for the whole batch."""
    h = _host(tmp_path)
    before = nb._drains()
    h.handle_http("POST", "/sync", _batch(
        {"p": "hop.moy/main.py", "t": "x = 1\n"},
        {"p": "hop.moy/pmem.json", "t": "[1]"}))
    assert nb._drains() == before + 1


def test_sync_shelf_refresh_fires_only_when_the_shelf_changed(tmp_path):
    """The kernel raises an event; the VM's poll turns it into the rescan."""
    root = _store(tmp_path)
    hits = []
    h = wh.WebHost(str(root), on_sync=lambda: hits.append(1))
    h.handle_http("POST", "/sync",
                  _batch({"p": "hop.moy/main.py", "t": "x = 2\n"}))
    h.poll()
    assert not hits, "a code edit repaints nothing on the shelf"
    h.handle_http("POST", "/sync",
                  _batch({"p": "hop.moy/manifest.json", "t": '{"title":"H2"}'}))
    h.poll()
    assert hits == [1]
    h.handle_http("POST", "/sync", _batch({"p": "new.moy/main.py", "t": "y"}))
    h.poll()
    assert hits == [1, 1], "a cart born is a shelf change"
    h.poll()
    assert hits == [1, 1], "an event fires once"


def test_the_pull_drains_the_feeder_once_not_per_file(tmp_path):
    """On the T-Deck the store is on a card sharing the panel's SPI host
    (.claude/rules/boards.md): the pull waits out the band in flight before it
    reads, once -- the card stays resident for the walk."""
    h = _host(tmp_path)
    before = nb._drains()
    _json(h.handle_http("GET", "/carts.json", b""))
    assert nb._drains() == before + 1


# ---------------------------------------------------------------------------
# GET /files.json + the files half of POST /sync -- the #108 user-files layer
# on the same endpoints (2026-08-25). moy_sync owns the batch semantics; what
# belongs here is the board's wiring: which tree a batch reaches, what the pull
# is allowed to hand out, and the storage gate around both.
# ---------------------------------------------------------------------------


def _files(tmp_path):
    """The #108 files root beside the carts one, holding both things that must
    never leave the board."""
    root = Path(wh.moy_sync.files_root(str(tmp_path / "carts")))
    (root / "drawings").mkdir(parents=True)
    (root / "drawings" / "sunset.moyimg").write_text("0,1,")
    (root / "recordings" / "take_1").mkdir(parents=True)
    (root / "recordings" / "take_1" / "part0.json").write_text("[1]")
    (root / ".history" / "drawings").mkdir(parents=True)
    (root / ".history" / "drawings" / "sunset.jsonl").write_text('{"t": "kf"}\n')
    (root / "trash" / "drawings").mkdir(parents=True)
    (root / "trash" / "drawings" / "gone.moyimg").write_text("9,")
    return root


def test_files_json_serves_the_kinds_and_nothing_else(tmp_path):
    """The same bundle shape as carts.json, kind-filtered: `.history/` is each
    side's own undo history and `trash/` is a LOCAL recovery bin, so neither
    crosses in either direction."""
    _files(tmp_path)
    h = _host(tmp_path)
    r = h.handle_http("GET", "/files.json", b"")
    assert "application/json" in _head(r)
    assert _json(r) == {"drawings/sunset.moyimg": "0,1,",
                        "recordings/take_1/part0.json": "[1]"}


def test_files_json_answers_on_a_board_that_has_made_nothing_yet(tmp_path):
    """An empty object, NEVER a 404: the 404 is what tells the browser this
    board predates files sync, and returning it for a store that merely holds
    no drawings would disable the push half for good."""
    h = _host(tmp_path)
    assert _json(h.handle_http("GET", "/files.json", b"")) == {}


def test_a_host_with_no_files_layer_404s_and_says_so_by_being_silent(tmp_path,
                                                                    monkeypatch):
    """A host that cannot resolve a files root (no moy_carts) gives the SAME
    answer a board flashed before files sync gives -- the page then builds no
    files watcher, and nothing retries a batch this host could only refuse."""
    monkeypatch.setattr(wh.moy_sync, "files_root", lambda root: None)
    h = wh.WebHost(str(_store(tmp_path)))
    assert b"404" in h_status(h.handle_http("GET", "/files.json", b""))
    r = h.handle_http("POST", "/sync", _batch(
        {"p": "drawings/x.moyimg", "t": "0,"}, root="files"))
    assert b"400" in h_status(r)


def test_the_files_pull_drains_the_feeder_too(tmp_path):
    _files(tmp_path)
    h = _host(tmp_path)
    before = nb._drains()
    _json(h.handle_http("GET", "/files.json", b""))
    assert nb._drains() == before + 1


def test_sync_routes_a_files_batch_into_the_files_root(tmp_path):
    root = _store(tmp_path)
    files = _files(tmp_path)
    h = wh.WebHost(str(root))
    r = h.handle_http("POST", "/sync", _batch(
        {"p": "drawings/sunset.moyimg", "t": "7,"}, root="files"))
    assert b"200" in r.split(b"\r\n")[0]
    assert json.loads(r.split(b"\r\n\r\n", 1)[1]) == {"ok": 1, "err": []}
    assert (files / "drawings" / "sunset.moyimg").read_text() == "7,"
    assert not (root / "drawings").exists(), "it must not land among the carts"


def test_sync_refuses_a_files_path_that_is_not_a_kind(tmp_path):
    root = _store(tmp_path)
    files = _files(tmp_path)
    h = wh.WebHost(str(root))
    r = h.handle_http("POST", "/sync", _batch(
        {"p": "trash/drawings/gone.moyimg", "t": "stolen"},
        {"p": ".history/drawings/sunset.jsonl", "t": "stolen"}, root="files"))
    body = json.loads(r.split(b"\r\n\r\n", 1)[1])
    assert body["ok"] == 0 and len(body["err"]) == 2
    assert (files / "trash" / "drawings" / "gone.moyimg").read_text() == "9,"


def test_a_files_batch_never_fires_the_shelf_rescan(tmp_path):
    """The launcher renders no drawings, and the Files app scans its kinds when
    it opens -- a rescan here would repaint the shelf for nothing."""
    root = _store(tmp_path)
    _files(tmp_path)
    hits = []
    h = wh.WebHost(str(root),
                   on_sync=lambda: hits.append(1))
    h.handle_http("POST", "/sync", _batch(
        {"p": "drawings/new.moyimg", "t": "0,"}, root="files"))
    assert not hits


def test_the_files_apply_drains_the_feeder_too(tmp_path):
    """Same law as the carts apply: on the T-Deck every one of these writes
    lands on the card that shares the panel's SPI host."""
    root = _store(tmp_path)
    _files(tmp_path)
    h = wh.WebHost(str(root))
    before = nb._drains()
    h.handle_http("POST", "/sync", _batch(
        {"p": "drawings/new.moyimg", "t": "0,"}, root="files"))
    assert nb._drains() == before + 1


def test_an_unknown_root_is_a_bad_batch_not_a_guess(tmp_path):
    root = _store(tmp_path)
    h = wh.WebHost(str(root))
    r = h.handle_http("POST", "/sync", _batch(
        {"p": "hop.moy/main.py", "t": "x"}, root="wifi"))
    assert b"400" in r.split(b"\r\n")[0]
    assert (root / "hop.moy" / "main.py").read_text().endswith("cls(1)\n")




# -- the streamed store ------------------------------------------------------







# -- the Settings row --------------------------------------------------------

class _FakeHost:
    """The four members console.Workstation's webhost verbs use."""

    def __init__(self, fail=None):
        self.serving = False
        self.error = None
        self._fail = fail
        self.stops = 0

    def start(self):
        if self._fail:
            raise OSError(self._fail)
        self.serving = True

    def stop(self):
        self.serving = False
        self.stops += 1

    def url(self):
        return "http://192.168.1.151/"


from ws_helpers import build_ws as _ws


def _row_keys(ws):
    return [r[0] for r in ws.settings_layer._settings_rows()]


def test_the_row_appears_only_where_the_service_is_injected(tmp_path):
    """A Settings row for a thing the tier cannot do is worse than no row --
    the host has no webhost, so it must not offer one."""
    ws = _ws(tmp_path)
    assert ws.webhost is None
    assert "webhost" not in _row_keys(ws)
    ws.webhost = _FakeHost()
    assert "webhost" in _row_keys(ws)


def test_the_row_is_appended_so_existing_row_indices_do_not_move(tmp_path):
    """Settings rows are pinned by index and by frozen 320x240 pixels
    elsewhere. Gating is only safe if it APPENDS."""
    ws = _ws(tmp_path)
    before = _row_keys(ws)
    ws.webhost = _FakeHost()
    after = _row_keys(ws)
    assert after[:len(before)] == before
    assert after[-1] == "webhost"


def test_the_row_shows_the_ADDRESS_not_just_ON(tmp_path):
    """The address IS the feature -- it has to be typed into a browser. A row
    reading "ON" would be telling the kid to go find the IP somewhere else."""
    ws = _ws(tmp_path)
    ws.webhost = _FakeHost()
    assert ws.webhost_label() == "OFF"
    ws.toggle_webhost()
    assert ws.webhost_serving() is True
    assert ws.webhost_label() == "192.168.1.151"   # scheme stripped
    ws.toggle_webhost()
    assert ws.webhost_serving() is False
    assert ws.webhost_label() == "OFF"


def test_a_failed_start_shows_its_reason_and_does_not_raise(tmp_path):
    """Starting touches WiFi, which fails slowly and in ways nobody can act on
    from a Settings screen. A raised exception would take the console down from
    a toggle; the row is this feature's only surface, so it carries the news."""
    ws = _ws(tmp_path)
    ws.webhost = _FakeHost(fail="no wifi")
    ws.toggle_webhost()
    assert ws.webhost_serving() is False
    assert "no wifi" in ws.webhost_label()


def test_the_verbs_are_safe_with_no_service(tmp_path):
    ws = _ws(tmp_path)
    assert ws.webhost_serving() is False
    assert ws.webhost_label() == "OFF"
    ws.toggle_webhost()                     # must not raise




# -- the link wait, and the board parity it exists to protect -----------------


class _Wifi:
    """Just enough of the injected wifi service: status() -> (up, ssid, ip)."""

    def __init__(self, up=False, ip="192.168.1.50"):
        self.up = up
        self.ip = ip
        self.connects = 0

    def status(self):
        return (self.up, "net" if self.up else "", self.ip if self.up else "")


def test_ensure_online_waits_for_a_link_that_arrives_late():
    """The whole reason this helper exists: `connect()` gives up at 4s and a
    saved network measured 1.5s slower than that, so a good network read as
    "no wifi". Here autoconnect brings the link up and the IP comes back."""
    w = _Wifi(up=False)

    def _auto(wifi):
        wifi.connects += 1
        wifi.up = True                    # the link arrives during the wait

    assert wh.ensure_online(w, _auto, wait_ms=50, step_ms=5) == "192.168.1.50"
    assert w.connects == 1


def test_ensure_online_reports_a_link_that_never_arrives():
    w = _Wifi(up=False)
    with pytest.raises(OSError):
        wh.ensure_online(w, lambda wifi: None, wait_ms=10, step_ms=5)


def test_ensure_online_without_a_wifi_service_is_an_error_not_a_crash():
    with pytest.raises(OSError):
        wh.ensure_online(None)


def test_an_already_connected_board_neither_reconnects_nor_sleeps():
    w = _Wifi(up=True)
    called = []
    assert wh.ensure_online(w, lambda wifi: called.append(1)) == "192.168.1.50"
    assert not called, "a connected board was made to reconnect"


def test_the_link_wait_is_moy_otas_one_body_with_moy_otas_bounds(monkeypatch):
    """It was the same 25 lines twice -- here and in `moy_ota.ensure_online` --
    with `12000` and `250` written out in both. One body now, and the bounds
    have one home; what stays local is what each caller needs on top (a bool for
    the updater, the STA IP for the WEB CONSOLE row)."""
    import moy_ota

    seen = {}

    def _spy(online, autoconnect=None, wait_ms=None, step_ms=None):
        seen["bounds"] = (wait_ms, step_ms)
        if autoconnect is not None:
            autoconnect()
        return online()

    monkeypatch.setattr(moy_ota, "wait_online", _spy)
    w = _Wifi(up=False)

    def _auto(wifi):
        wifi.up = True

    assert wh.ensure_online(w, _auto) == "192.168.1.50"
    assert seen["bounds"] == (moy_ota.ONLINE_WAIT_MS, moy_ota.ONLINE_STEP_MS)


def test_make_webhost_reads_the_wifi_service_lazily():
    """`ws.wifi` is attached by wire_workstation_core, which has not run when a
    board builds this -- so binding the service at construction time would
    capture None forever. The dial reads it when the switch turns."""
    dialled = []

    class _Wlan:
        def connect(self, ssid, pw):
            dialled.append(ssid)

    class _Store:
        def load_wifi(self, root):
            return [{"ssid": "home", "password": "x"}, {"ssid": "other"}]

    class _Wifi2:
        _store = _Store()
        _root = "/r"
        wlan = _Wlan()

    class _WS:
        wifi = None

    ws = _WS()
    host = wh.make_webhost(ws, "/moy/carts", "/moy/web")
    ws.wifi = _Wifi2()                    # attached AFTER construction
    assert host._dial() is True and dialled == ["home"]


# The web dir a board passes is named for the STORAGE it lives on, never for
# the board (moy_webhost's constants say why): the T-Deck's copy goes on its
# SD, the P4 has no card, and the Guition -- which does have a slot -- still
# stages the pushed bundle on the internal VFS.
BOARDS = (
    "lilygo_t_deck_plus_mainline",
    "esp32_p4_wifi6_touch_lcd_7b",
    "guition_jc3248w535",
    "guition_jc8012p4a1c",
)


@pytest.mark.parametrize("board", BOARDS)
def test_every_board_injects_the_web_console(board):
    """THE PIN FOR THIS WHOLE CLASS OF BUG.

    The web console shipped on the P4 with every shared piece already in place
    -- moy_webhost, the Settings row, the console verbs, all staged from one
    source -- and the T-Deck still did not have the feature, because the single
    per-board injection was never written for it. Nothing failed: the row is
    capability-gated on `ws.webhost`, so the board just quietly did not offer
    it, and no test could tell the difference between "not wired" and "not
    supported".

    So the assertion is on the INJECTION, per board, by name. A new board that
    stages every shared module and forgets this line fails here instead of
    shipping a console that silently cannot be reached.
    """
    src = runtime_text(ROOT / "firmware" / board / "modules" / "moy_runtime.py")
    assert "make_webhost(" in src, "%s never builds a WebHost" % board
    assert "ws.webhost" in src, "%s never attaches one to the console" % board




def test_the_link_wait_is_shared_and_not_recopied_per_board():
    """It was a 25-line closure in the P4's run_desktop. Writing it per board is
    precisely how the T-Deck went without the feature, so the helper is shared
    and the boards must not grow private copies of it again."""
    for board in BOARDS:
        src = runtime_text(ROOT / "firmware" / board / "modules" / "moy_runtime.py")
        assert "ONLINE_WAIT_MS" not in src.replace("moy_ota's ONLINE_WAIT_MS", "")
        assert "def _web_online" not in src, "%s re-grew a private link wait" % board


# ---------------------------------------------------------------------------
# WASM MODE (#197): the pairing pin, the parked connection screen, and
# PLAY ON DEVICE. A SWITCH and not a session -- see runtime/web_console_ui.py.
# ---------------------------------------------------------------------------


class _ModeHost(_FakeHost):
    """_FakeHost plus the two members wasm mode reads: a paired url, and a pin
    the host resolves at start() the way the real one does."""

    def __init__(self, fail=None, pin_source=None):
        _FakeHost.__init__(self, fail=fail)
        self.pin = None
        self._pin_source = pin_source

    def start(self):
        if self._pin_source is not None:
            self.pin = self._pin_source()
        _FakeHost.start(self)

    def paired_url(self):
        return wh.WebHost.paired_url(self)


def _mode_ws(tmp_path):
    """A workstation wired the way make_webhost wires a board: the pin comes
    off the LIVE ws, at start."""
    ws = _ws(tmp_path)
    ws.webhost = _ModeHost(pin_source=lambda: ws.web_pin())
    return ws


# -- the pin -----------------------------------------------------------------

def test_the_pin_is_minted_once_and_persisted(tmp_path):
    """Four digits, kept in system.json beside every other Settings choice. A
    pin that changed per boot would mean re-scanning after every power cycle,
    and a phone holding the old url would look like a broken board."""
    ws = _ws(tmp_path)
    pin = ws.web_pin()
    assert len(pin) == 4 and pin.isdigit()
    assert ws.web_pin() == pin, "a second call minted a second pin"
    assert ws.system.get("web_pin") == pin
    assert _ws(tmp_path).web_pin() == pin, "it did not survive a fresh console"


def test_a_board_that_never_serves_never_writes_a_pin(tmp_path):
    """Lazy on purpose: a console whose owner never turns the row on has no
    business having written a secret into its store."""
    ws = _ws(tmp_path)
    assert ws.system.text("web_pin") is None
    ws.webhost = _ModeHost(pin_source=lambda: ws.web_pin())
    assert ws.system.text("web_pin") is None, "constructing the host minted a pin"
    ws.toggle_webhost()
    assert ws.system.get("web_pin")


def test_the_pin_is_read_at_START_not_at_construction():
    """THE ORDERING BUG THIS EXISTS TO PREVENT. Boards build the webhost before
    system.json is loaded, so a pin captured at construction is one minted
    against an empty store -- the QR would show a pin the store does not agree
    with, and every batch the page sent would come back 403."""
    seen = []

    class _WS:
        wifi = None

        def web_pin(self):
            seen.append(1)
            return "1234"

        def rescan_carts(self):
            pass

        def launch_named(self, name):
            return None

    # port=0 (the ephemeral-port idiom test_moy_webserver.py already uses),
    # because start() BINDS: since 2026-08-29 the default is 80, which a board's
    # lwIP hands out freely and a CPython test process running as a normal user
    # is refused. The port is not what this test is about.
    host = wh.make_webhost(_WS(), "/moy/carts", "/moy/web", port=0)
    assert not seen, "make_webhost read the pin at construction"
    assert host.pin is None
    try:
        host.start(ip="10.0.0.7")
        assert seen == [1] and host.pin == "1234"
    finally:
        host.stop()


def test_an_explicit_pin_still_wins_over_the_consoles():
    """A host built with its own policy (a test, the dev server) must not have
    it silently replaced."""
    class _WS:
        wifi = None

        def web_pin(self):
            raise AssertionError("the explicit pin was ignored")

        def rescan_carts(self):
            pass

        def launch_named(self, name):
            return None

    host = wh.make_webhost(_WS(), "/moy/carts", "/moy/web", pin="0000", port=0)
    try:
        host.start(ip="10.0.0.7")
        assert host.pin == "0000"
    finally:
        host.stop()


def test_the_paired_url_is_the_address_plus_the_pin(tmp_path):
    """What the QR encodes and SHOW ADDRESS reveals. The page forwards its own
    `?pin=` into every batch it posts, so this string IS the pairing gesture."""
    ws = _mode_ws(tmp_path)
    assert ws.web_console_url() == "", "an address while nothing is serving"
    ws.toggle_webhost()
    assert ws.web_console_url() == (
        "http://192.168.1.151/?pin=" + ws.web_pin())


def test_a_host_without_a_pin_pairs_with_a_bare_url(tmp_path):
    host = wh.WebHost(str(tmp_path / "carts"))
    host.ip = "10.0.0.5"
    assert host.paired_url() == "http://10.0.0.5/"
    host.pin = "4821"
    assert host.paired_url() == "http://10.0.0.5/?pin=4821"


def test_the_url_spells_the_port_unless_it_is_the_default(tmp_path):
    """The 2026-08-29 move to port 80 is a rule about the port's IDENTITY, not
    about whether one was passed.

    Omitting `:80` is the whole point (five characters off an address a kid
    scans, one QR version). Omitting anything else would hand a browser a url
    that goes to port 80 -- so the host dev twin's 8321, and every explicit
    port a test or a deployment picks, must still render."""
    assert wh.DEFAULT_PORT == 80
    host = wh.WebHost(str(tmp_path / "carts"))
    host.ip = "10.0.0.5"
    assert host.port == 80
    assert host.url() == "http://10.0.0.5/"
    other = wh.WebHost(str(tmp_path / "carts"),
                       port=8321)
    other.ip = "10.0.0.5"
    other.pin = "4821"
    assert other.url() == "http://10.0.0.5:8321/"
    assert other.paired_url() == "http://10.0.0.5:8321/?pin=4821"


# -- the switch --------------------------------------------------------------

def test_turning_the_row_on_parks_the_glass_and_off_returns_it(tmp_path):
    """Wasm mode IS the toggle (owner call, 2026-08-25). No heartbeat, no
    session object: while it is on the glass shows how to reach the browser,
    and turning it off gives the console back."""
    ws = _mode_ws(tmp_path)
    assert ws.wm.top_kind() == "launcher"
    ws.toggle_webhost()
    assert ws.wm.top_kind() == "webconsole" and ws.web.parked is True
    ws.toggle_webhost()
    assert ws.wm.top_kind() == "launcher" and ws.web.parked is False
    assert ws.webhost_serving() is False


def test_a_failed_start_does_not_park(tmp_path):
    """The failure's reason is readable in exactly one place -- the Settings row
    -- so a start that could not bring WiFi up must leave the kid looking at
    it, not at a connection screen for a console nobody can reach."""
    ws = _ws(tmp_path)
    ws.webhost = _ModeHost(fail="no wifi")
    ws.open_settings()
    ws.toggle_webhost()
    assert ws.wm.top_kind() == "settings" and ws.web.parked is False
    assert "no wifi" in ws.webhost_label()


def test_wasm_mode_holds_the_radio_and_lets_it_go_with_the_socket(tmp_path):
    """The radio is a LEASE (2026-09-07): off unless something holds it, and
    the web console is the holder this whole feature exists for. The row ON
    takes it before the host binds; the row OFF lets it go -- at once for a
    host that stops at once (this fake, a bare stop), and only when the SOCKET
    closes for one saying goodbye (the test after this one)."""
    ws = _mode_ws(tmp_path)
    assert ws.wifi.radio is False
    ws.toggle_webhost()
    assert ws.webhost_serving() and "web" in ws.leases.holders()
    assert ws.wifi.radio is True
    ws.toggle_webhost()
    assert not ws.webhost_serving()
    assert ws.leases.holders() == [] and ws.wifi.radio is False


def test_a_failed_start_holds_no_radio(tmp_path):
    """A start that could not bring WiFi up leaves the kid in Settings reading
    why -- with the radio OFF, because a lease nobody is using is the bug the
    lease exists to prevent."""
    ws = _ws(tmp_path)
    ws.webhost = _ModeHost(fail="no wifi")
    ws.open_settings()
    ws.toggle_webhost()
    assert "no wifi" in ws.webhost_label()
    assert ws.leases.holders() == [] and ws.wifi.radio is False


def test_the_goodbye_window_keeps_the_radio_until_the_socket_closes(tmp_path):
    """The page's last round trip rides the link. `serving` drops at once when
    the row is switched off, but the socket lingers for the closing window --
    and so must the radio, or the goodbye is said into a dead radio and the
    page gets the vanished-board panel this window was written to prevent.
    `make_webhost` wires the release to the host's own `on_stop`, which the
    kernel's STOPPED event fires."""
    ws = _ws(tmp_path)
    h = wh.WebHost(str(_store(tmp_path)), pin="4321", port=0,
                   on_stop=lambda: ws.wifi_release("web"))
    h.update = None
    ws.webhost = h
    ws.wifi_hold("web")
    h.start(ip="10.0.0.5")
    ws.web.park()

    ws.stop_web_console()                   # the connection screen's TURN OFF
    assert h.serving is False and h.closing == "off"
    assert not ws.web.parked
    assert "web" in ws.leases.holders(), "let go before the goodbye was said"
    assert ws.wifi.radio is True

    nb._skew_clock(4990)
    h.poll()
    assert "web" in ws.leases.holders()
    nb._skew_clock(20)
    h.poll()
    assert h.closing is None
    assert ws.leases.holders() == [] and ws.wifi.radio is False


def test_every_board_wires_the_release_to_the_socket(tmp_path):
    """make_webhost is the ONE injection every board takes, so the lease's
    release lives there and not in four board files."""
    ws = _ws(tmp_path)
    h = wh.make_webhost(ws, str(_store(tmp_path)), port=_free_port())
    ws.wifi_hold("web")
    h.start(ip="127.0.0.1")
    assert h.serving
    h.stop()                                # a bare stop: the socket is gone
    assert ws.leases.holders() == [] and ws.wifi.radio is False


def test_park_sets_the_flag_before_it_hands_the_glass_over(tmp_path):
    """ORDER, not just outcome. `go_home` is what routes to the connection
    screen and it decides by reading the flag, so a park that called it first
    would leave the glass on the launcher with the mode on. The screen's entry
    state (a revealed pin) is cleared here too -- nothing in the router calls a
    Layer's `on_enter`."""
    ws = _mode_ws(tmp_path)
    seen = []
    ws.web.ui.on_enter = lambda: seen.append(("on_enter", ws.web.parked))
    ws.go_home = lambda: seen.append(("go_home", ws.web.parked))
    ws.park_web_console()
    assert seen == [("on_enter", True), ("go_home", True)]


def test_go_home_re_parks_exactly_while_the_flag_is_set(tmp_path):
    """The other half: every door back to the launcher funnels through go_home,
    so the flag is the only thing that has to be right for a browser-launched
    cart, the bar's X, a crash and hold-BACKSPACE to all land back here."""
    ws = _mode_ws(tmp_path)
    ws.web.parked = True
    ws.go_home()
    assert ws.wm.top_kind() == "webconsole"
    ws.web.parked = False
    ws.go_home()
    assert ws.wm.top_kind() == "launcher"


def test_turn_off_stops_the_host_before_it_gives_the_glass_back(tmp_path):
    """ORDER again, and the reason the mode exists: unparking first would put a
    kid on a launcher over a store a browser is still free to write."""
    ws = _mode_ws(tmp_path)
    ws.toggle_webhost()
    seen = []
    unpark = ws.web.unpark

    def _unpark():
        seen.append(ws.web.serving())
        unpark()

    ws.web.unpark = _unpark
    ws.stop_web_console()
    assert seen == [False], "the glass came back while the host still served"
    assert ws.web.parked is False and ws.webhost_serving() is False


def test_the_parked_screen_owns_the_glass_and_claims_every_event(tmp_path):
    """A parked surface that let events through would route them to a launcher
    the kid cannot see."""
    ws = _mode_ws(tmp_path)
    ws.toggle_webhost()
    layer = ws._content_layer()
    assert layer.id == "webconsole" and layer.domain == "system"
    assert layer.handle_pointer(0, 0, False) is True
    assert layer in ws.wm.draw_stack()


def test_the_parked_screen_is_static_so_the_redraw_gate_closes(tmp_path):
    """#44: nothing here animates, so an idle connection screen costs the board
    zero painted frames -- and it may sit on a desk for an hour."""
    ws = _mode_ws(tmp_path)
    ws.toggle_webhost()
    ws.frame(1 / 30.0)
    ws._splash_until = None                  # the boot logo animates on its own
    ws._toast_until = 0
    ws._dirty = False
    ws.pointer.visible = False
    ws._last_ptr = ws._ptr_state()
    assert ws._animating(1 / 30.0) is False, "the connection screen animates"
    assert ws._needs_redraw(1 / 30.0) is False


def test_show_address_toggles_and_resets_on_every_entry(tmp_path):
    """A pin left revealed on the glass is a pin the next person in the room
    reads, so entering the mode always starts hidden."""
    ws = _mode_ws(tmp_path)
    ws.toggle_webhost()
    ui = ws.web.ui
    assert ui.show_address is False
    _qr, _addr, show, off = ui.rects()
    assert show[2] > 0 and off[2] > 0
    ui.handle_pointer(show[0] + 2, show[1] + 2, True)
    assert ui.show_address is True
    ui.handle_pointer(show[0] + 2, show[1] + 2, True)
    assert ui.show_address is False
    ui.show_address = True
    ws.toggle_webhost()                      # off...
    ws.toggle_webhost()                      # ...and on again
    assert ui.show_address is False


def test_the_turn_off_button_turns_it_off(tmp_path):
    """The toggle that got here lives in Settings, and Settings is behind this
    screen -- a mode with no visible way out is a trap."""
    ws = _mode_ws(tmp_path)
    ws.toggle_webhost()
    _qr, _addr, _show, off = ws.web.ui.rects()
    ws.web.ui.handle_pointer(off[0] + 2, off[1] + 2, True)
    assert ws.webhost_serving() is False
    assert ws.wm.top_kind() == "launcher"


def test_turn_off_still_turns_OFF_when_the_host_died_underneath(tmp_path):
    """The one state a plain toggle gets wrong. If the socket stopped from
    somewhere else, `toggle_webhost` reads "not serving" and STARTS it -- a
    button labelled TURN OFF that turns it on, on the one screen whose whole
    job is being the way out."""
    ws = _mode_ws(tmp_path)
    ws.toggle_webhost()
    ws.webhost.stop()                        # the host dies under the screen
    assert ws.webhost_serving() is False and ws.web.parked is True
    _qr, _addr, _show, off = ws.web.ui.rects()
    ws.web.ui.handle_pointer(off[0] + 2, off[1] + 2, True)
    assert ws.webhost_serving() is False, "TURN OFF restarted the host"
    assert ws.wm.top_kind() == "launcher"


def test_the_qr_encodes_the_paired_url(tmp_path):
    """The screen draws what `web_console_url` says -- not a cached or
    reconstructed address -- and re-encodes only when it changes."""
    from runtime import moy_qr
    ws = _mode_ws(tmp_path)
    ws.toggle_webhost()
    url = ws.web_console_url()
    first = ws.web.ui.matrix(url)
    assert first == moy_qr.encode(url)
    assert ws.web.ui.matrix(url) is first, "re-encoded an unchanged url"
    assert ws.web.ui.matrix("http://10.0.0.9/?pin=0000") != first


def test_the_windowed_tier_parks_FULLSCREEN_not_in_a_window(tmp_path):
    """Windows exist only above the desk (#105), so parking leaves the make
    world and the play world presents this fullscreen with no special case. A
    connection screen inside a draggable window would be a QR a kid can hide
    behind another window."""
    from ws_helpers import build_desktop_ws
    ws = build_desktop_ws(tmp_path)
    ws.webhost = _ModeHost(pin_source=lambda: ws.web_pin())
    ws.open_desk()
    assert ws.wm.desk_open() is True
    ws.toggle_webhost()
    assert ws.wm.top_kind() == "webconsole"
    assert ws.wm.desk_open() is False, "the connection screen became a window"
    ws.toggle_webhost()
    assert ws.wm.desk_open() is True, "the desk is home on this tier"
    assert "webconsole" not in ws.wm.stack.kinds(), \
        "the connection screen stayed under the desk"


# -- PLAY ON DEVICE ----------------------------------------------------------

def test_run_plays_the_named_cart_on_the_glass(tmp_path):
    """The whole protocol is a cart NAME. The answer carries the TITLE that
    actually started, because the page sent a name it guessed at."""
    seen = []
    host = wh.WebHost(str(tmp_path / "carts"),
                      on_run=lambda name: (seen.append(name), "Star Catcher")[1])
    r = host.handle_http("POST", "/run", b'{"cart": "star_catcher"}')
    assert b"200" in r.split(b"\r\n")[0]
    assert json.loads(r.split(b"\r\n\r\n", 1)[1]) == {"run": "Star Catcher"}
    assert seen == ["star_catcher"]


def test_run_takes_the_same_pin_gate_as_sync(tmp_path):
    """Starting code on a kid's console is a WRITE by any reading of the word,
    and the gate closes before the name is even looked at."""
    launched = []
    host = wh.WebHost(str(tmp_path / "carts"),
                      pin="4321", on_run=lambda n: (launched.append(n), "X")[1])
    r = host.handle_http("POST", "/run", b'{"cart": "hop"}')
    assert b"403" in r.split(b"\r\n")[0]
    assert not launched, "a pinless request reached the launcher"
    r = host.handle_http("POST", "/run", b'{"cart": "hop", "pin": "4321"}')
    assert b"200" in r.split(b"\r\n")[0]
    assert launched == ["hop"]


def test_run_answers_404_for_a_cart_this_board_does_not_have(tmp_path):
    host = wh.WebHost(str(tmp_path / "carts"),
                      on_run=lambda n: None)
    assert b"404" in h_status(host.handle_http("POST", "/run", b'{"cart":"x"}'))


def test_run_answers_501_where_there_is_no_console_behind_it(tmp_path):
    """The dev server and the CI harness serve the store with no glass. 501 and
    not 404, because a page reads 404 as "no such endpoint" and would stop
    offering the button against a board that simply has no runner."""
    host = wh.WebHost(str(tmp_path / "carts"))
    assert b"501" in h_status(host.handle_http("POST", "/run", b'{"cart":"x"}'))


def test_run_refuses_a_body_that_is_not_a_json_object(tmp_path):
    host = wh.WebHost(str(tmp_path / "carts"),
                      on_run=lambda n: "X")
    for body in (b"junk", b"[1,2]", b'"hop"', b""):
        assert b"400" in h_status(host.handle_http("POST", "/run", body)), body


def test_run_refuses_an_empty_cart_name_without_launching(tmp_path):
    """`{"cart": null}` / "" must NOT reach the console -- launch_named("")
    runs the FIRST cart (the serial `run` with no arg), so a malformed PLAY ON
    DEVICE would otherwise start a random game. Refused before on_run fires."""
    launched = []
    host = wh.WebHost(str(tmp_path / "carts"),
                      on_run=lambda n: (launched.append(n), "X")[1])
    for body in (b'{"cart": null}', b'{"cart": ""}', b'{"cart": "   "}', b'{}'):
        assert b"400" in h_status(host.handle_http("POST", "/run", body)), body
    assert launched == [], "on_run fired for an empty name: %r" % launched


def test_a_crashing_launch_is_a_500_not_a_dead_request(tmp_path):
    """`handle_http` runs inside the frame loop's tail. An exception escaping
    here would take the poll down, and with it the browser's whole session."""
    def boom(name):
        raise RuntimeError("nope")

    host = wh.WebHost(str(tmp_path / "carts"), on_run=boom)
    assert b"500" in h_status(host.handle_http("POST", "/run", b'{"cart":"x"}'))


def test_the_launch_runs_inside_the_storage_gate(tmp_path):
    """Opening a cart reads its manifest, code, sheet and pmem off the store.
    On the T-Deck that is the card sharing the panel's SPI host, and this runs
    at the FRAME TAIL where a painted frame's flush may still be shipping --
    the documented Cache/MMU panic, same law as the sync apply."""
    depth = [0]
    entries = []

    def gate(fn):
        depth[0] += 1
        entries.append(1)
        try:
            return fn()
        finally:
            depth[0] -= 1

    inside = []
    host = wh.WebHost(str(tmp_path / "carts"),
                      with_sd=gate,
                      on_run=lambda n: (inside.append(depth[0]), "X")[1])
    host.handle_http("POST", "/run", b'{"cart": "hop"}')
    assert inside == [1], "the launch ran outside the storage gate"
    assert len(entries) == 1


def test_get_sync_is_the_capability_marker(tmp_path):
    """How a page tells a BOARD from a static host: moybyte.com, an export and
    a plain file server all 404 this, and the page then offers neither PLAY ON
    DEVICE nor a sync loop against a wall."""
    h = _host(tmp_path)
    r = h.handle_http("GET", "/sync", b"")
    assert b"200" in h_status(r)
    assert json.loads(r.split(b"\r\n\r\n", 1)[1]) == {"sync": 1}


def test_the_405_still_names_where_writes_go(tmp_path):
    """Both write endpoints, so a wrong method reads as a wrong verb rather
    than a wrong url."""
    h = _host(tmp_path)
    r = h.handle_http("PUT", "/run", b"{}")
    assert b"405" in h_status(r)
    assert b"/sync" in r and b"/run" in r




# -- the cart-name lookup, and the exit that comes back here -----------------

def test_launch_named_takes_a_title_or_a_folder(tmp_path):
    """The browser knows a cart by its TITLE (that is what rides every frame
    payload); a human at a serial prompt types part of a folder name. Both are
    accepted."""
    ws = _ws(tmp_path)
    for name in ("Star Catcher", "star_catcher", "moybyte.star_catcher.moy",
                 "star catch"):
        assert ws.launch_named(name) == "Star Catcher", name
        ws.go_home()
    assert ws.launch_named("no such cart anywhere") is None


def test_launch_named_prefers_an_exact_match_to_a_partial_one(tmp_path):
    """A cart whose title is a substring of another's must still be reachable
    by its own name."""
    ws = _ws(tmp_path)
    titles = [c["title"] for c in ws.launcher.items if c.get("path")]
    for t in titles:
        assert ws.launch_named(t) == t, titles
        ws.go_home()


def test_launch_named_survives_a_name_that_is_not_a_string(tmp_path):
    """The name comes off a JSON body a browser wrote, so it is a string only
    by convention -- and this runs at the frame loop's tail, where a TypeError
    takes the poll down with the whole session."""
    ws = _ws(tmp_path)
    for junk in (7, 3.5, True, {"a": 1}, ["x"]):
        assert ws.launch_named(junk) is None, junk
        assert ws.wm.top_kind() == "launcher", junk
    # None is not junk -- it is the dev channel's `run` with no argument, which
    # plays the first real cart.
    assert ws.launch_named(None) is not None


def test_launch_named_never_picks_a_pseudo_tile(tmp_path):
    """The pinned Make/+New cards carry no store path; running one would open
    an editor at a browser's request."""
    ws = _ws(tmp_path)
    title = ws.launch_named("")
    assert title and any(c.get("path") and c["title"] == title
                         for c in ws.launcher.items)


def test_a_cart_launched_from_the_browser_exits_BACK_to_the_screen(tmp_path):
    """The other half of the switch. Every return-to-the-launcher path funnels
    through go_home, so the exit gesture, the bar's X and a crash all land back
    on the connection screen rather than on a shelf the browser is rewriting."""
    ws = _mode_ws(tmp_path)
    ws.toggle_webhost()
    assert ws.launch_named("Star Catcher") == "Star Catcher"
    assert ws.wm.top_kind() == "desktop", "the cart did not start"
    ws._exit_to_caller()
    assert ws.wm.top_kind() == "webconsole"
    assert ws.webhost_serving() is True, "the exit stopped the host"


def test_leaving_the_mode_from_inside_a_cart_gives_the_console_back(tmp_path):
    """The switch stays authoritative mid-run: turning the row off while a
    browser-launched cart plays must clear the mode, not leave the flag set for
    the next exit to trip over."""
    ws = _mode_ws(tmp_path)
    ws.toggle_webhost()
    ws.launch_named("Star Catcher")
    ws.toggle_webhost()
    assert ws.web.parked is False
    ws._exit_to_caller()
    assert ws.wm.top_kind() == "launcher"


def test_the_dev_channel_run_uses_the_same_lookup():
    """ONE author for the cart lookup: a name that works over serial works from
    the page, and neither can grow its own idea of what a cart is called."""
    src = (ROOT / "runtime" / "dev_channel.py").read_text()
    assert "ws.launch_named(" in src
    assert "ws.launch_selected()" not in src, "the dev channel re-grew a lookup"


# -- the goodbye ------------------------------------------------------------
#
# A board that is going away ON PURPOSE -- WEB CONSOLE switched off, or an
# update about to reboot -- looks exactly like an unplugged one from the far
# end, because stop() just closed the socket. The page has carried an
# "expected" kind for this since the surface was written and NOTHING in the
# system could produce it, so every deliberate shutdown reached a reader as the
# vanished-board panel with its data-loss warning. Reported from a P4 session,
# 2026-08-30.


def test_a_stop_with_a_reason_keeps_answering_to_give_it(tmp_path):
    h = _host(tmp_path)
    h.start(ip="10.0.0.5")
    h.stop(why="off")
    assert h.serving is False, (
        "`serving` is the console's notion of the feature being ON -- the row "
        "reads it and web_console unparks the glass on it, so holding it true "
        "would leave a kid staring at a parked screen for the whole window")
    assert h.closing == "off", "the socket has to outlive `serving` to say why"
    r = h.handle_http("GET", "/carts.json", b"")
    assert b"503" in r.split(b"\r\n")[0]
    assert json.loads(r.split(b"\r\n\r\n", 1)[1]) == {"error": "closing",
                                                     "why": "off"}


def test_the_goodbye_outranks_the_pin(tmp_path):
    """Deliberately ahead of the gate. A page that was never given a pin still
    deserves to know the console was switched off rather than be left to decide
    it vanished -- and this says nothing except that somebody turned it off."""
    h = _pinned(tmp_path)
    h.start(ip="10.0.0.5")
    h.stop(why="off")
    r = h.handle_http("GET", "/carts.json", b"")
    assert json.loads(r.split(b"\r\n\r\n", 1)[1])["error"] == "closing"


def test_the_window_closes_the_socket_when_it_expires(tmp_path):
    stopped = []
    h = _host(tmp_path, on_stop=lambda: stopped.append(1))
    h.start(ip="10.0.0.5")
    h.stop(why="off")
    nb._skew_clock(4990)
    h.poll()
    assert h.closing == "off", "closed early -- the page may not have asked yet"
    assert not stopped
    nb._skew_clock(20)
    h.poll()
    assert h.closing is None and h.serving is False
    assert stopped == [1], "the socket closed and nobody let the radio go"


def test_a_bare_stop_still_closes_at_once(tmp_path):
    """What a teardown and an error path want: no window, no waiting. The
    goodbye is opt-in, so nothing that used to stop instantly now lingers."""
    h = _host(tmp_path)
    h.start(ip="10.0.0.5")
    h.stop()
    assert h.serving is False and h.closing is None
    assert b"200" in h_status(h.handle_http("GET", "/sync", b""))


def test_starting_again_cancels_the_goodbye(tmp_path):
    """Toggled off and straight back on inside the window. A host left
    `closing` would answer 503 to everything while its Settings row said ON."""
    h = _host(tmp_path)
    h.start(ip="10.0.0.5")
    h.stop(why="off")
    assert h.closing == "off"
    h.start(ip="10.0.0.5")
    assert h.closing is None and h.serving is True
    r = h.handle_http("GET", "/carts.json", b"")
    assert b"503" not in h_status(r)


def test_the_frame_loop_keeps_polling_a_host_that_is_saying_goodbye():
    """The half that makes the window real. `serving` goes false at once so the
    row and the glass follow the tap; the socket outlives it by a few seconds
    purely to answer. Polling only on `serving` would leave nobody to answer,
    which is the bug the window exists to fix: the kernel loop's service bit
    for the webhost is up while the switch is not OFF OR the socket still
    listens (its goodbye window)."""
    board = (ROOT / "native" / "moy_kernel" / "moy_loop_board.c").read_text(
        encoding="utf-8")
    svc = board[board.index("static uint32_t b_services(void) {"):]
    svc = svc[:svc.index("\n}\n")]
    assert "if (wc.state != MOY_WC_OFF || web.listening) {" in svc
    assert "bits |= MOY_SVC_WEB;" in svc


def test_turning_the_row_off_still_stops_a_host_that_cannot_take_a_reason():
    """`toggle` turns any failure into the row's label, so a host with the older
    no-argument `stop()` would raise TypeError, be swallowed, and NOT STOP --
    a row saying ON over a console the kid just switched off. The stop is
    unconditional; the goodbye is the optional part."""
    from runtime.web_console import WebConsole

    class Old:
        stopped = False

        def stop(self):                  # no `why`
            Old.stopped = True

    WebConsole._stop_saying_why(Old(), "off")
    assert Old.stopped, "an older host was left running"

    seen = {}

    class New:
        def stop(self, why=None):
            seen["why"] = why

    WebConsole._stop_saying_why(New(), "off")
    assert seen["why"] == "off", "a host that CAN take a reason must get one"


def test_update_wants_its_pin_on_the_query_for_BOTH_methods(tmp_path):
    """The mirror of the /sync rule above, and the pair is the whole trap.

    /sync and /run read the pin from the BODY. /update reads it from the QUERY
    on both methods -- deliberately, because a GET has nowhere else and one
    endpoint spending the credential in two places is a rule nobody holds. Two
    endpoints, two rules, and the page has to know which is which.

    It did not. The worker put the update POST's pin in the body only, so on
    every pinned board -- which is every real one -- the first POST came back
    403; the worker's 403 branch calls update_off(), which latches the link
    dead; and UPDATE ONLINE reported "the console stopped answering" instantly,
    before any offer, on a board answering perfectly.
    """
    h = _pinned(tmp_path)
    h.update = _StubUpdate()

    # Body-only: refused, exactly as it was in the wild.
    r = h.handle_http("POST", "/update", b'{"action":"check","pin":"4321"}')
    assert b"403" in r.split(b"\r\n")[0], (
        "a body pin authorized /update -- then the page's bug would be "
        "invisible here too")
    assert json.loads(r.split(b"\r\n\r\n", 1)[1]) == {"error": "pin"}

    # On the query: accepted, both methods.
    r = h.handle_http("POST", "/update?pin=4321", b'{"action":"check"}')
    assert b"200" in r.split(b"\r\n")[0], r[:80]
    r = h.handle_http("GET", "/update?pin=4321", b"")
    assert b"200" in r.split(b"\r\n")[0], r[:80]


class _StubUpdate:
    """The smallest thing /update will talk to: it only has to be present and
    answer, since what is under test is the GATE in front of it."""

    def status(self):
        return {"running": {"board": "test", "version": 1}, "state": "none"}

    def request(self, action, channel=None):
        return True, "queued"

    def step(self):
        return False


def test_the_link_wait_feeds_an_armed_watchdog_and_leaves_an_unarmed_one(
        monkeypatch):
    """A WEB CONSOLE start with no reachable network holds the frame for the
    whole link wait; unfed, the kernel's task watchdog reset the Guition S3.
    Fed only when the frame loop armed it: the first feed would subscribe."""
    import types
    sys.path.insert(0, str(MODULES))
    import moy_ota
    for armed in (True, False):
        fed = []
        k = types.SimpleNamespace(watchdog=lambda: (armed, 5000, 0, 0),
                                  feed=lambda: fed.append(1))
        monkeypatch.setitem(sys.modules, "moy_kernel", k)
        moy_ota.wait_online(lambda: False, None, wait_ms=40, step_ms=10)
        assert (len(fed) >= 4) is armed, (armed, fed)


# -- the switch never waits (native/moy_net/moy_webconsole.c) -------------------


def test_a_start_with_no_link_returns_at_once_and_the_poll_brings_it_on(tmp_path):
    """ensure_online used to hold the frame until the link came, past the task
    watchdog on a board with no reachable network. The switch returns JOINING,
    and the frame's poll binds once the kernel's link has an address."""
    seen = []
    nb._link(None)
    h = wh.WebHost(str(_store(tmp_path)), port=_free_port(),
                   on_serving=lambda: seen.append("up"))
    try:
        t0 = time.time()
        h.start()
        assert time.time() - t0 < 0.5
        assert h.joining and not h.serving
        h.poll()
        assert h.joining and seen == []
        nb._link("192.168.4.9")
        h.poll()
        assert h.serving and seen == ["up"]
        assert h.url() == "http://192.168.4.9:%d/" % h.port
        assert nb.wc_url(True).startswith("http://192.168.4.9:")
    finally:
        h.stop()
        nb._link(None)


def test_a_join_that_never_gets_a_link_fails_and_says_so(tmp_path):
    failed = []
    nb._link(None)
    h = wh.WebHost(str(_store(tmp_path)), port=_free_port(),
                   on_failed=lambda: failed.append(h.error))
    h.start()
    nb._skew_clock(20001)
    h.poll()
    assert failed == ["no wifi"]
    assert not h.joining and not h.serving


def test_turning_a_join_off_ends_it_and_lets_the_radio_go(tmp_path):
    stopped = []
    nb._link(None)
    h = wh.WebHost(str(_store(tmp_path)), port=_free_port(),
                   on_stop=lambda: stopped.append(1))
    h.start()
    h.stop("off")
    assert stopped == [1] and not h.joining
    nb._link("10.1.1.1")
    h.poll()
    assert not h.serving, "a join that was turned off came on anyway"
    nb._link(None)


def test_the_console_parks_when_the_join_arrives_and_not_before(tmp_path):
    ws = _ws(tmp_path)
    nb._link(None)
    ws.webhost = wh.make_webhost(ws, str(_store(tmp_path)), port=_free_port())
    ws.toggle_webhost()
    assert ws.webhost.joining and not ws.web.parked
    assert ws.webhost_label() == "JOINING..."
    nb._link("10.2.0.4")
    ws.webhost.poll()
    try:
        assert ws.web.parked and ws.webhost_serving()
        assert nb.wc_state()[1] is True, "the park is the kernel's flag"
    finally:
        ws.toggle_webhost()
        nb._link(None)
    assert not ws.web.parked and ws.webhost_label() == "OFF"


def test_the_goodbye_ends_with_the_switch_off(tmp_path):
    h = wh.WebHost(str(_store(tmp_path)), port=_free_port())
    h.start(ip="127.0.0.1")
    h.stop("off")
    assert nb.wc_state()[0] == wh.WebHost.CLOSING
    nb._skew_clock(5001)
    for _ in range(5):                  # the frame loop polls only while closing
        if h.closing or h.serving or h.joining:
            h.poll()
    assert nb.wc_state()[0] == wh.WebHost.OFF

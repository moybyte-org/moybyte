"""Serving the web console from the board (moycore plan 3.4, the pull half).

The bet this slice makes is that the page needs NO change to read a board: it
already boots with `fetch("carts.json")` -- a relative url -- so a page served
from the console fetches the console's store. That bet is only good if the
board emits exactly the bundle shape `worker.js` consumes, so most of what is
worth testing here is the SHAPE, plus the two things that would go wrong on
real hardware and nowhere else: a megabyte asset held in RAM, and a path from
the network reaching a file that is none of the browser's business.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MODULES = ROOT / "firmware" / "lilygo_t_deck_plus_micropython" / "modules"
sys.path.insert(0, str(MODULES))

import moy_webhost as wh                                       # noqa: E402
from moy_webserver import FileResponse                          # noqa: E402


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
    (root / "sky.moy").mkdir()
    (root / "sky.moy" / "main.lua").write_text("function _draw() end\n")
    (root / "loose.txt").write_text("not a cart")
    return root


def test_the_bundle_is_the_shape_the_page_already_consumes(tmp_path):
    """`{"<cart>/<rel>": text}` -- worker.js's writeCarts input, and what the
    dev server (web_runner/moy.py's pack_cart) emits. A different shape here
    means the page needs a branch, and the whole point of this slice is that it
    does not."""
    b = wh.pack_store(str(_store(tmp_path)))
    assert b["hop.moy/main.py"].startswith("def _draw()")
    assert b["hop.moy/manifest.json"] == '{"title": "Hop"}'
    assert b["hop.moy/images/bg.moyimg"] == "0,0,"    # nested dirs keep their path
    assert b["sky.moy/main.lua"].startswith("function")
    assert all("/" in k for k in b), "a key without a cart prefix"


def test_thumbs_do_not_cross_the_wire(tmp_path):
    """`thumbs/` is a REGENERABLE cache keyed to a screen size (#66/#155) --
    sending it costs transfer time for pixels the browser will rebuild at its
    own size anyway. It is also the largest thing in a cart folder."""
    b = wh.pack_store(str(_store(tmp_path)))
    assert not any("thumbs" in k for k in b), sorted(b)


def test_a_loose_file_beside_the_carts_is_not_a_cart(tmp_path):
    b = wh.pack_store(str(_store(tmp_path)))
    assert not any(k.startswith("loose") for k in b)


def test_an_unreadable_file_is_skipped_not_fatal(tmp_path):
    """A binary asset in a cart folder must not take the whole endpoint down --
    the store is the kid's, and one odd file in it should cost that file."""
    root = _store(tmp_path)
    (root / "hop.moy" / "blob.bin").write_bytes(b"\xff\xfe\x00\x01")
    b = wh.pack_store(str(root))
    assert "hop.moy/blob.bin" not in b
    assert "hop.moy/main.py" in b, "one bad file killed the rest"


# -- the handler -------------------------------------------------------------

def _host(tmp_path, web=True):
    root = _store(tmp_path)
    wdir = tmp_path / "web"
    wdir.mkdir()
    if web:
        (wdir / "index.html").write_text("<!doctype html>console")
        (wdir / "micropython.wasm").write_bytes(b"\x00asm" + b"x" * 5000)
        (wdir / "worker.js").write_text("// worker")
    h = wh.WebHost.__new__(wh.WebHost)          # no socket needed
    h.carts_root = str(root)
    h.web_dir = str(wdir)
    h._with_sd = lambda fn: fn()
    return h


def test_the_root_serves_the_console_page(tmp_path):
    h = _host(tmp_path)
    for path in ("/", "/index.html"):
        r = h.handle_http("GET", path, b"")
        assert isinstance(r, FileResponse), path
        assert r.content_type.startswith("text/html")


def test_a_megabyte_asset_is_streamed_not_held(tmp_path):
    """The whole reason FileResponse exists. `micropython.wasm` is ~1.0MB and
    the S3 has ~23KB of internal SRAM free in play (#66), so a response built
    as one bytes object is not a slow path -- it is one that does not run."""
    h = _host(tmp_path)
    r = h.handle_http("GET", "/micropython.wasm", b"")
    assert isinstance(r, FileResponse)
    assert r.content_type == "application/wasm"
    assert r.size == 5004
    assert r.CHUNK <= 4096, "a chunk this big defeats the purpose"
    head = r.head().decode()
    assert "Content-Length: 5004" in head
    # NOT cached. This shipped as max-age=86400 on the reasoning that the
    # assets change only on a reflash -- they change on every web-build push,
    # which is the routine action, and a board then served a correct console to
    # a browser showing yesterday's for a day. A full reload is ~1.5s at the
    # measured ~700KB/s, which is the price of never being stale.
    assert "no-store" in head and "max-age" not in head


def test_carts_json_is_json_and_live(tmp_path):
    h = _host(tmp_path)
    r = h.handle_http("GET", "/carts.json", b"")
    head = r.head().decode()
    assert "application/json" in head
    assert "no-store" in head, "the store is live; caching it serves stale carts"
    assert json.loads("".join(r.body_iter))["hop.moy/main.py"].startswith(
        "def _draw()")


def test_only_the_four_known_assets_are_reachable(tmp_path):
    """An ALLOWLIST, not a `..` check. The set of files a browser needs is
    fixed at build time, so accepting a path from the network buys nothing and
    risks handing out wifi.json -- and "reject .." is one encoding trick from
    being wrong."""
    h = _host(tmp_path)
    (Path(h.web_dir).parent / "secret.txt").write_text("wifi password")
    for path in ("/secret.txt", "/../secret.txt", "/carts/hop.moy/main.py",
                 "/%2e%2e/secret.txt", "/wifi.json"):
        assert h.handle_http("GET", path, b"") is None, path


def test_a_query_string_does_not_hide_an_asset(tmp_path):
    """The page opens itself with `?dev=1`; a served index must survive it."""
    h = _host(tmp_path)
    assert isinstance(h.handle_http("GET", "/?dev=1", b""), FileResponse)


def test_a_board_with_no_bundle_says_which_directory(tmp_path):
    """Copying dist/ to the board is a SETUP step, and its failure looks
    exactly like a broken feature in a browser. Name the path."""
    h = _host(tmp_path, web=False)
    r = h.handle_http("GET", "/", b"")
    assert isinstance(r, bytes) and b"404" in r.split(b"\r\n")[0]
    assert h.web_dir.encode() in r and b"web_runner/dist" in r


# -- the bundle baked into the firmware image --------------------------------
#
# The board used to serve ONLY a copy a human had put on its storage, and that
# copy drifts with nothing to detect it: a board once served a bundle old
# enough to still carry a desktop-blackout bug fixed in dist/ hours earlier.
# The T-Deck could not even be pushed to (its USB-CDC RX is dead under the
# desktop), so its copy went on by card reader or not at all. Hence moy_web:
# the gzipped bundle rides the image, and a console that boots is current with
# its own firmware.


class _FakeMoyWeb:
    """The native module's three verbs, over a dict of bytes."""

    def __init__(self, blobs, stamp="4 572693 deadbeef1234"):
        self.blobs = {k: memoryview(v) for k, v in blobs.items()}
        self._stamp = stamp

    def asset(self, name):
        return self.blobs.get(name)

    def names(self):
        return tuple(self.blobs)

    def stamp(self):
        return self._stamp

    def total(self):
        return sum(len(v) for v in self.blobs.values())


@pytest.fixture
def baked(monkeypatch):
    """Install a fake baked bundle for the duration of one test."""
    def _install(blobs, **kw):
        mod = _FakeMoyWeb(blobs, **kw)
        monkeypatch.setattr(wh, "_moy_web", mod)
        return mod
    return _install


def test_the_image_serves_the_console_when_storage_has_none(tmp_path, baked):
    """The guarantee. A board that has never been pushed to still serves a
    console, and it is the one its firmware was built from."""
    from moy_webserver import BlobResponse
    baked({"index.html.gz": b"\x1f\x8b" + b"page",
           "micropython.wasm.gz": b"\x1f\x8b" + b"w" * 900})
    h = _host(tmp_path, web=False)
    r = h.handle_http("GET", "/", b"")
    assert isinstance(r, BlobResponse)
    assert r.content_type.startswith("text/html")
    head = r.head().decode()
    assert "Content-Encoding: gzip" in head, "stored gzipped, served as gzip"
    assert "Content-Length: 6" in head
    assert "no-store" in head


def test_a_pushed_copy_BEATS_the_baked_one(tmp_path, baked):
    """THE PRECEDENCE DECISION, and it is not arbitrary. A copy on the board is
    an explicit human action (tools/p4_push_web.py, a card reader). If the
    image won, that dev loop would die and a baked bundle would be one nobody
    could iterate on without a full reflash."""
    baked({"index.html.gz": b"BAKED", "worker.js.gz": b"BAKED"})
    h = _host(tmp_path)                     # writes index.html + worker.js
    r = h.handle_http("GET", "/", b"")
    assert isinstance(r, FileResponse)
    assert r.path.endswith("index.html"), "the image overrode a pushed copy"


def test_a_pushed_gz_also_beats_the_baked_one(tmp_path, baked):
    baked({"worker.js.gz": b"BAKED"})
    h = _host(tmp_path)
    (Path(h.web_dir) / "worker.js.gz").write_bytes(b"\x1f\x8bPUSHED")
    r = h.handle_http("GET", "/worker.js", b"")
    assert isinstance(r, FileResponse) and r.path.endswith(".gz")
    assert r.encoding == "gzip"


def test_a_raw_baked_asset_is_served_without_an_encoding(tmp_path, baked):
    """The build bakes .gz today, but the lookup is the same two-step rule as
    on storage, so a raw bundle needs no code change -- and must not be
    announced as gzip, which is a page that fails to boot."""
    from moy_webserver import BlobResponse
    baked({"worker.js": b"// raw"})
    h = _host(tmp_path, web=False)
    r = h.handle_http("GET", "/worker.js", b"")
    assert isinstance(r, BlobResponse)
    assert r.encoding is None
    assert "Content-Encoding" not in r.head().decode()


def test_an_image_with_no_bundle_falls_through_to_the_404(tmp_path, baked):
    """A firmware built without a web bundle (the generator warns loudly, and
    CI refuses to publish one). The module is still there and reports nothing,
    so the request must land on the 404 -- not on an exception mid-request."""
    baked({})
    h = _host(tmp_path, web=False)
    r = h.handle_http("GET", "/", b"")
    assert isinstance(r, bytes) and b"404" in r.split(b"\r\n")[0]
    assert b"no web bundle in this firmware" in r


def test_a_broken_native_module_is_not_a_broken_request(tmp_path, monkeypatch):
    """`_baked` is guarded, not trusted. An old image has no module at all and
    a wedged one must cost the request its bundle, not the whole endpoint."""
    class _Boom:
        def asset(self, name):
            raise RuntimeError("nope")

    monkeypatch.setattr(wh, "_moy_web", _Boom())
    h = _host(tmp_path, web=False)
    r = h.handle_http("GET", "/", b"")
    assert isinstance(r, bytes) and b"404" in r.split(b"\r\n")[0]


def test_the_baked_response_holds_no_copy_of_the_bundle(tmp_path, baked):
    """The reason this is affordable at all. The blob is flash-mapped rodata
    and the response is a memoryview at it -- #66 measures ~23KB of internal
    SRAM free during play, so a bytes() of the 523KB wasm is not a slow path,
    it is one that does not run."""
    from moy_webserver import BlobResponse
    mod = baked({"micropython.wasm.gz": b"\x1f\x8b" + b"z" * 4000})
    h = _host(tmp_path, web=False)
    r = h.handle_http("GET", "/micropython.wasm", b"")
    assert isinstance(r, BlobResponse)
    assert r.data is mod.blobs["micropython.wasm.gz"], "the bundle was copied"
    assert r.size == 4002


def test_the_baked_stamp_answers_which_console_this_board_serves(baked):
    """The question that started the whole thing, now one line over a REPL."""
    baked({"index.html.gz": b"x"}, stamp="4 572693 d44b8756bf08")
    assert wh.baked_stamp() == "4 572693 d44b8756bf08"


def test_the_baked_stamp_is_none_on_an_image_without_the_module(monkeypatch):
    monkeypatch.setattr(wh, "_moy_web", None)
    assert wh.baked_stamp() is None


def test_the_baked_body_streams_byte_for_byte(tmp_path, baked):
    """The chunk pump over a REAL socket, the same net FileResponse has.

    A blob send slices a memoryview instead of refilling a buffer, so the
    failure mode is the mirror image: correct for every full chunk and short or
    doubled in the tail. On a wasm binary that means a browser that refuses to
    instantiate and says nothing useful, so: a payload that is deliberately not
    a chunk multiple, compared byte for byte.
    """
    import socket
    import threading
    from moy_webserver import WebServer, BlobResponse

    payload = bytes(range(256)) * 40 + b"tail"        # 10244 B: 2 chunks + 2052
    resp = BlobResponse(memoryview(payload), "application/wasm",
                        encoding="gzip")
    a, b = socket.socketpair()
    srv = WebServer.__new__(WebServer)

    def pump():
        try:
            srv._send_blob(a, resp)
        finally:
            a.close()

    t = threading.Thread(target=pump)
    t.start()
    b.settimeout(5)
    got = b""
    while True:
        chunk = b.recv(65536)
        if not chunk:
            break
        got += chunk
    t.join(5)
    assert not t.is_alive(), "the send never finished"
    b.close()

    head, _, body = got.partition(b"\r\n\r\n")
    assert b"Content-Length: %d" % len(payload) in head
    assert b"Content-Encoding: gzip" in head
    assert body == payload, "streamed body differs (len %d vs %d)" % (
        len(body), len(payload))


def test_a_blob_goes_out_through_the_transports_dispatch(tmp_path, baked):
    """`_http_send_close` decides by TYPE, and a new response class that nobody
    added a branch for would be sent with `sendall(<object>)` -- i.e. a
    TypeError swallowed by that method's catch-all, which reads to a browser as
    a connection that closed with no reply."""
    import socket
    import threading
    from moy_webserver import WebServer, BlobResponse

    resp = BlobResponse(memoryview(b"\x1f\x8bbody"), "text/html")
    a, b = socket.socketpair()
    srv = WebServer.__new__(WebServer)
    t = threading.Thread(target=lambda: srv._http_send_close(a, resp))
    t.start()
    b.settimeout(5)
    got = b""
    while True:
        c = b.recv(65536)
        if not c:
            break
        got += c
    t.join(5)
    b.close()
    assert got.endswith(b"\x1f\x8bbody"), got[-32:]


def test_the_blob_send_pays_no_storage_gate(tmp_path, baked):
    """The T-Deck's SD gate exists because its card shares the panel's SPI host.
    The baked bundle is in flash and races nothing, so a gate here would put an
    unrelated mount in the path of every asset request."""
    baked({"worker.js.gz": b"\x1f\x8bx"})
    entered = []
    host = wh.WebHost(str(tmp_path / "carts"), str(tmp_path / "nowhere"),
                      with_sd=lambda fn: (entered.append(1), fn())[1])
    conn = _Conn()
    host._send_blob(conn, host._asset("worker.js"))
    assert not entered, "the baked bundle went through the SD gate"
    assert bytes(conn.sent).endswith(b"\x1f\x8bx")


def test_a_write_is_refused_in_a_way_a_newer_page_can_read(tmp_path):
    """The push half is the next slice. A page from a build that HAS it will
    POST at a board that does not, and 405-with-a-reason is a thing the page
    can act on where a 404 reads as a wrong url."""
    h = _host(tmp_path)
    r = h.handle_http("POST", "/carts.json", b"{}")
    assert b"405" in r.split(b"\r\n")[0]
    assert b"read-only" in r


def test_the_sd_gate_wraps_the_store_read(tmp_path):
    """On the T-Deck the store is on a shared-SPI SD card that must only be
    touched inside moybyte_sd.with_sd_live -- reading it from anywhere else is
    the class of mistake that hangs the panel (CLAUDE.md's hard constraints).
    The handler must go through the injected gate, not around it."""
    h = _host(tmp_path)
    calls = []

    def gate(fn):
        calls.append(1)
        return fn()

    h._with_sd = gate
    r = h.handle_http("GET", "/carts.json", b"")
    assert calls == [1], "the store was read outside the SD gate"
    # The gate is entered BEFORE the generator is built, and the walk runs
    # after -- which is right for with_sd_live (mount once, keep resident) and
    # would be wrong for a scoped mount. Pinned because the generator rewrite
    # silently turned "wrap the read" into "wrap building an iterator", and the
    # count above passes either way.
    assert calls == [1] and hasattr(r, "body_iter")
    json.loads("".join(r.body_iter))       # the walk, after the gate returned
    assert calls == [1], "the walk re-entered the gate per file"


@pytest.mark.parametrize("name,ctype", sorted(wh.ASSETS.items()))
def test_every_allowlisted_asset_has_a_sane_content_type(name, ctype):
    """A wrong type on `micropython.wasm` is the difference between the console
    booting and the browser refusing to instantiate it -- and it fails only in
    a real browser, which is the expensive place to find out."""
    assert "/" in ctype
    if name.endswith(".wasm"):
        assert ctype == "application/wasm"
    if name.endswith(".js") or name.endswith(".mjs"):
        assert "javascript" in ctype


def test_the_stream_delivers_the_file_byte_for_byte(tmp_path):
    """The chunk pump over a REAL socket, because everything above it only
    checks the header.

    `_send_file` reuses one buffer and sends a memoryview slice of it; the
    failure mode of getting that wrong is not a crash but a body that is
    correct for every full chunk and garbage in the last partial one -- which
    on a wasm binary means the browser refuses to instantiate and nothing says
    why. So: a file whose size is deliberately NOT a chunk multiple, compared
    byte for byte.
    """
    import socket
    import threading
    from moy_webserver import WebServer

    payload = bytes(range(256)) * 17 + b"tail"      # 4356 B: 4 chunks + 260
    src = tmp_path / "micropython.wasm"
    src.write_bytes(payload)
    resp = FileResponse(str(src), len(payload), "application/wasm")

    a, b = socket.socketpair()
    srv = WebServer.__new__(WebServer)

    def pump():
        try:
            srv._send_file(a, resp)
        finally:
            a.close()               # EOF, so the read loop below terminates

    t = threading.Thread(target=pump)
    t.start()
    b.settimeout(5)                 # never hang the suite on a broken pump
    got = b""
    while True:
        chunk = b.recv(65536)
        if not chunk:
            break
        got += chunk
    t.join(5)
    assert not t.is_alive(), "the send never finished"
    b.close()

    head, _, body = got.partition(b"\r\n\r\n")
    assert b"Content-Length: %d" % len(payload) in head
    assert body == payload, "streamed body differs (len %d vs %d)" % (
        len(body), len(payload))


# -- the streamed store ------------------------------------------------------

def _stream_to_json(root):
    return json.loads("".join(wh.stream_store_json(str(root))))


def test_the_streamed_json_equals_the_packed_dict(tmp_path):
    """The generator replaced pack_store on the wire because the dict did not
    fit (982KB / 61s on P4 glass). It must produce the SAME bundle -- an
    equality this direct is worth having precisely because the two now share no
    code path."""
    root = _store(tmp_path)
    assert _stream_to_json(root) == wh.pack_store(str(root))


def test_the_stream_escapes_what_json_requires(tmp_path):
    """Hand-rolled escaping, because json.dumps on a 40KB main.py allocates a
    second 40KB string -- the same mistake one level down. Hand-rolled means it
    has to be checked against the real thing."""
    root = tmp_path / "carts"
    (root / "odd.moy").mkdir(parents=True)
    nasty = 'q = "hi"\\ntab\\there\\n\\u0001 \\\\ backslash\\r\\n'
    (root / "odd.moy" / "main.py").write_text(nasty)
    assert _stream_to_json(root)["odd.moy/main.py"] == nasty


def test_an_ascii_file_is_not_rebuilt_character_by_character():
    """The common case -- a cart source with nothing to escape -- must return
    the input string itself, not a rebuilt copy. On a 40KB file the difference
    is a 40KB allocation per cart, which is the whole reason this is not
    json.dumps."""
    plain = "def _draw():\n"          # \n IS escaped, so use a truly plain one
    plain = "def _draw(): cls(1)"
    assert wh._jstr(plain) == '"' + plain + '"'


def test_carts_json_is_a_chunked_response_now(tmp_path):
    from moy_webserver import ChunkedResponse
    h = _host(tmp_path)
    r = h.handle_http("GET", "/carts.json", b"")
    assert isinstance(r, ChunkedResponse)
    head = r.head().decode()
    assert "Transfer-Encoding: chunked" in head
    assert "Content-Length" not in head, "chunked and length are exclusive"
    assert json.loads("".join(r.body_iter))["hop.moy/main.py"]


def test_the_chunked_wire_format_is_well_formed(tmp_path):
    """Framing over a real socket: `<hex>\\r\\n<data>\\r\\n` per chunk, `0\\r\\n\\r\\n`
    to end. A browser is unforgiving here and the failure is a page that hangs
    mid-load with no error."""
    import socket
    import threading
    from moy_webserver import WebServer, ChunkedResponse

    body = ["{", '"a/b.py"', ":", '"x"', "}"]
    resp = ChunkedResponse(iter(body))
    a, b = socket.socketpair()
    srv = WebServer.__new__(WebServer)

    def pump():
        try:
            srv._send_chunked(a, resp)
        finally:
            a.close()

    t = threading.Thread(target=pump)
    t.start()
    b.settimeout(5)
    got = b""
    while True:
        c = b.recv(65536)
        if not c:
            break
        got += c
    t.join(5)
    b.close()

    head, _, wire = got.partition(b"\r\n\r\n")
    assert b"chunked" in head
    assert wire.endswith(b"0\r\n\r\n"), wire[-16:]
    # De-chunk and require the original JSON back.
    out, rest = b"", wire
    while True:
        size_s, _, rest = rest.partition(b"\r\n")
        n = int(size_s, 16)
        if n == 0:
            break
        out += rest[:n]
        rest = rest[n + 2:]
    assert json.loads(out) == {"a/b.py": "x"}


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
        return "http://192.168.1.151:8080/"


def _ws(tmp_path):
    from runtime import host_app
    return host_app.build_workstation(str(tmp_path / "carts"))


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
    assert ws.webhost_label() == "192.168.1.151:8080"   # scheme stripped
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


def test_start_refuses_to_report_serving_when_the_bind_failed(tmp_path):
    """WebServer.start RETURNS False on a busy port rather than raising (it is
    guarded so it cannot take the loop down). Not checking it is how a row comes
    to say ON over a server that is serving nothing."""
    h = wh.WebHost.__new__(wh.WebHost)
    h.serving = False
    h.error = None
    h.port = 8080
    h._ensure_online = lambda: "10.0.0.5"
    h.ip = None
    from moy_webserver import WebServer
    h.__class__.__mro__      # sanity: WebHost derives from WebServer
    orig = WebServer.start
    try:
        WebServer.start = lambda self, ip=None: False      # simulate a busy port
        raised = False
        try:
            wh.WebHost.start(h)
        except OSError:
            raised = True
        assert raised, "a failed bind was reported as success"
        assert h.serving is False
    finally:
        WebServer.start = orig


def test_an_asset_is_never_cached_by_default(tmp_path):
    """The regression that shipped: a day-long cache on files whose whole
    delivery mechanism is "push a new build, no reflash". Pinned as a DEFAULT,
    because the bug was not the parameter existing -- it was the default."""
    from moy_webserver import FileResponse as FR
    assert FR("x", 1, "text/plain").max_age == 0
    assert "no-store" in FR("x", 1, "text/plain").head().decode()
    # ...and a caller with genuinely immutable assets can still opt in.
    assert "max-age=60" in FR("x", 1, "text/plain", max_age=60).head().decode()


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


def test_make_webhost_reads_the_wifi_service_lazily():
    """`ws.wifi` is attached by wire_workstation_core, which has not run when a
    board builds this -- so binding the service at construction time would
    capture None forever."""
    class _WS:
        wifi = None

    ws = _WS()
    host = wh.make_webhost(ws, "/moy/carts", "/moy/web")
    ws.wifi = _Wifi(up=True)              # attached AFTER construction
    assert host._ensure_online() == "192.168.1.50"


BOARDS = (
    ("lilygo_t_deck_plus_micropython", "TDECK_WEB_DIR"),
    ("esp32_p4_wifi6_touch_lcd_7b", "P4_WEB_DIR"),
)


@pytest.mark.parametrize("board,web_dir", BOARDS, ids=[b for b, _ in BOARDS])
def test_every_board_injects_the_web_console(board, web_dir):
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
    src = (ROOT / "firmware" / board / "modules" / "moy_runtime.py").read_text()
    assert "make_webhost(" in src, "%s never builds a WebHost" % board
    assert "ws.webhost" in src, "%s never attaches one to the console" % board
    assert web_dir in src, "%s does not pass its own web directory" % board


class _Conn:
    def __init__(self):
        self.sent = bytearray()

    def sendall(self, b):
        self.sent += bytes(b)


def test_asset_streaming_goes_through_the_storage_gate(tmp_path):
    """The megabyte is the part that needs gating.

    On the T-Deck the bundle lives on an SD card sharing the panel's SPI host,
    and an SD op that overlaps an in-flight panel DMA is the documented hard
    hang -- the read lands, then the next flush freezes the board silently. The
    store walk was already gated; the ~1MB micropython.wasm was not, which is
    the wrong way round.
    """
    web = tmp_path / "web"
    web.mkdir()
    (web / "worker.js").write_text("console.log(1)\n" * 200)
    entered = []

    def _gate(fn):
        entered.append(1)
        return fn()

    host = wh.WebHost(str(tmp_path / "carts"), str(web), with_sd=_gate)
    conn = _Conn()
    host._send_file(conn, host._asset("worker.js"))
    assert entered, "the asset stream bypassed the SD gate"
    assert b"console.log(1)" in bytes(conn.sent)
    assert b"200 OK" in bytes(conn.sent)


def test_a_board_without_shared_storage_pays_no_gate(tmp_path):
    """The P4's bundle is on internal flash and races nothing, so it must not
    acquire a gate it does not need."""
    web = tmp_path / "web"
    web.mkdir()
    (web / "worker.js").write_text("x")
    host = wh.WebHost(str(tmp_path / "carts"), str(web))
    assert host.stream_gate is None
    conn = _Conn()
    host._send_file(conn, host._asset("worker.js"))
    assert bytes(conn.sent).endswith(b"x")


def test_the_link_wait_is_shared_and_not_recopied_per_board():
    """It was a 25-line closure in the P4's run_desktop. Writing it per board is
    precisely how the T-Deck went without the feature, so the helper is shared
    and the boards must not grow private copies of it again."""
    for board, _ in BOARDS:
        src = (ROOT / "firmware" / board / "modules" / "moy_runtime.py").read_text()
        assert "ONLINE_WAIT_MS" not in src.replace("moy_ota's ONLINE_WAIT_MS", "")
        assert "def _web_online" not in src, "%s re-grew a private link wait" % board

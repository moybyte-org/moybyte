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
    # Cached, unlike the JSON: these change only when the board is reflashed,
    # and re-sending 1.1MB on every page open is a ~9s tax at the T-Deck's
    # measured ~137KB/s.
    assert "max-age=" in head and "no-store" not in head


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

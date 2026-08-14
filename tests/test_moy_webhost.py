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
    assert isinstance(r, bytes)
    head, _, body = r.partition(b"\r\n\r\n")
    assert b"application/json" in head
    assert b"no-store" in head, "the store is live; caching it serves stale carts"
    assert json.loads(body)["hop.moy/main.py"].startswith("def _draw()")


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
    h.handle_http("GET", "/carts.json", b"")
    assert calls == [1], "the store was read outside the SD gate"


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

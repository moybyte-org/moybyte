"""Compiled carts in the HOSTED console, in real Chrome.

The browser runs a "runtime": "wasm" cart's main.wasm as a SIBLING module on
its own WebAssembly engine -- native/moy_wasm_web behind the session surface
the boards' engine implements, worker.js's cart engine holding the instance
and its adapters over libmoy's import table -- and the console drives it as a
board does: the same moycore, WasmRun, Player and canvas.

What it proves, in real Chrome against the built dist/:

  * the tier cart (tests/fixtures/wasm/tier.moy) -- par's items, blit565, its
    own file through `read`, a config value through `cfg`, verbs over the
    frame -- shows the frame the host shows, pixel for pixel
    (tests/tier_frame.py's golden); its `snd` samples are drained by the
    page's audio pull and reach the page in the frames; and button A's trap
    ends it on the console's own error panel;
  * Jet Teapot and ESP 88, built by tools/jet_cart.py as mit-carts' recipe
    builds them, play;
  * Get Carts offers a compiled cart from a local index and installs it --
    reading only the members this console keeps, by range, so not one byte
    of a chip's module crosses -- and the installed cart plays on the next
    load, out of OPFS, showing the same golden frame.

    MOYBYTE_WEB_E2E=1 .venv/bin/python -m pytest tests/test_web_wasm_e2e.py

Gated like its siblings (`tests/web_e2e.py`). The Jet carts need wasi-sdk 24
(tools/jet_cart.py fetches it by sha256); without it that test skips on a
bench and fails under CI.
"""

import base64
import io
import json
import os
import struct
import sys
import zlib

import pytest

import tier_frame
import web_e2e
from cart_store_fixtures import sha, stored_zip
from web_e2e import ROOT, DIST

pytestmark = pytest.mark.skipif(
    not os.environ.get("MOYBYTE_WEB_E2E"),
    reason="MOYBYTE_WEB_E2E not set (spawns headless Chrome for ~2 min)")

TIER = ROOT / "tests" / "fixtures" / "wasm" / "tier.moy"
BINARY = (".wasm", ".bin", ".png", ".obj", ".aot", ".wad")


def _built(cart_src, out):
    """A compiled fixture cart, its main.wasm assembled (tools/wasm_cart.py)."""
    sys.path.insert(0, str(ROOT))
    from tools import wasm_cart
    wasm_cart.build(str(cart_src), str(out))
    return out


def _files(folder):
    """{relative path: bytes} of a built cart, its sources left behind."""
    out = {}
    for dp, dn, fn in os.walk(folder):
        dn[:] = [d for d in dn if d != "src"]
        for f in fn:
            p = os.path.join(dp, f)
            out[os.path.relpath(p, folder).replace(os.sep, "/")] = open(p, "rb").read()
    return out


def _site(tmp_path, carts=(), index=None):
    """dist/ as a static site whose served shelf also carries `carts` (built
    cart folders), each file as the bundle carries it -- text, or a binary
    file's bytes -- and, given `index`, the shelves Get Carts lists."""
    site = tmp_path / "site"
    site.mkdir()
    for name in os.listdir(DIST):
        if name != "carts.json":
            os.symlink(DIST / name, site / name)
    bundle = json.loads((DIST / "carts.json").read_text())
    for folder in carts:
        for rel, data in _files(folder).items():
            key = os.path.basename(str(folder)) + "/" + rel
            if rel.endswith(BINARY):
                bundle[key] = {"b": base64.b64encode(data).decode()}
            else:
                bundle[key] = data.decode()
    (site / "carts.json").write_text(json.dumps(bundle))
    if index is not None:
        (site / "indexes.json").write_text(json.dumps({"indexes": [index]}))
    return site


# The console's `state`, and what the page's audio path saw: a spy on the
# worker's frames, counting the ones that carry PCM and the loudest sample.
_STATE = ("__moyState().then(s => JSON.stringify({cart: s.cart, err: s.cart_error, "
          "notice: s.notice, snd: s.snd}))")
_SPY = """(() => {
  const was = onWorker; window.__pcm = {frames: 0, peak: 0};
  onWorker = function (m) {
    if (m.t === 'frame' && m.s) {
      const a = JSON.parse(m.s).audio;
      if (a) {
        const b = atob(a); window.__pcm.frames++;
        for (let i = 0; i + 1 < b.length; i += 2) {
          let v = b.charCodeAt(i) | (b.charCodeAt(i + 1) << 8);
          if (v >= 32768) v -= 65536;
          window.__pcm.peak = Math.max(window.__pcm.peak, Math.abs(v));
        }
      }
    }
    return was(m);
  };
  return 'spying';
})()"""
_PCM = "JSON.stringify(window.__pcm)"
_CANVAS = """(() => {
  const c = document.getElementById('cv');
  const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
  let s = '';
  for (let i = 0; i < d.length; i += 32768) s += String.fromCharCode.apply(null, d.subarray(i, i + 32768));
  return c.width + 'x' + c.height + ':' + btoa(s);
})()"""
# How many colours the canvas shows, sampling every 97th pixel.
_COLOURS = """(() => {
  const c = document.getElementById('cv');
  const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
  const seen = new Set();
  for (let i = 0; i < d.length; i += 4 * 97) seen.add((d[i] << 16) | (d[i + 1] << 8) | d[i + 2]);
  return seen.size;
})()"""
# Button A (the page maps `z` to it) on the canvas, held long enough to be a
# press the cart's tick sees.
_PRESS_A = """(async () => {
  const c = document.getElementById('cv');
  c.dispatchEvent(new KeyboardEvent('keydown', {key: 'z', bubbles: true}));
  await new Promise(r => setTimeout(r, 250));
  c.dispatchEvent(new KeyboardEvent('keyup', {key: 'z', bubbles: true}));
  return 'pressed';
})()"""


def _digest(shot):
    size, b64 = shot.split(":", 1)
    assert size == "%dx%d" % (tier_frame.W, tier_frame.H), size
    return tier_frame.digest(tier_frame.from_rgba(base64.b64decode(b64)))


def _play(tmp_path, site, name, query, steps):
    server, base = web_e2e.serve(site, web_e2e.free_port())
    try:
        return web_e2e.run(tmp_path, name, query, steps, base, tmp_path / ("chrome_" + name))
    finally:
        server.terminate()
        server.wait(timeout=10)


def test_a_compiled_cart_runs_in_the_browser_as_it_runs_on_the_host(tmp_path):
    web_e2e.require("store")
    tier = _built(TIER, tmp_path / "carts" / "tier.moy")
    out, js = _play(tmp_path, _site(tmp_path, [tier]), "tier",
                    "?handheld=1&dev=1&cart=tier.moy", [
        {"js": _SPY},
        {"note": "the cart plays a while: frames, samples", "wait": 3000},
        {"js": _STATE},
        {"js": _CANVAS},
        {"js": _PCM},
        {"shot": "tier"},
        {"note": "button A traps on purpose", "js": _PRESS_A},
        {"wait": 1000},
        {"js": _STATE},
        {"shot": "trapped"},
    ])
    assert len(js) == 6, "the scenario did not run to its end:\n%s" % out[-4000:]
    before, shot, pcm, after = json.loads(js[1]), js[2], json.loads(js[3]), json.loads(js[5])
    assert before["cart"] == "Tier Wasm", out[-3000:]
    assert before["err"] is None and before["notice"] is None, before
    assert _digest(shot) == tier_frame.golden(), \
        "the browser's frame is not the one the host shows (tests/tier_frame.py)"
    queued, played, _starved, room, live = before["snd"]
    assert live and queued > 0 and played > 0 and queued - played + room == 2048, before
    assert pcm["frames"] > 0 and pcm["peak"] > 1000, \
        "the cart's samples did not reach the page's audio: %r" % pcm
    assert "unreachable" in (after["err"] or ""), after
    assert after["notice"] is None, after


def test_the_jet_carts_play_in_the_browser(tmp_path):
    web_e2e.require("store")
    sys.path.insert(0, str(ROOT))
    from tools import jet_cart
    if jet_cart.wasi_sdk(fetch=bool(os.environ.get("CI"))) is None:
        why = "no wasi-sdk 24 (python3 tools/jet_cart.py --toolchain fetches it)"
        if os.environ.get("CI") or os.environ.get("MOYBYTE_REQUIRE_WEB_E2E"):
            pytest.fail(why)
        pytest.skip(why)
    carts = tmp_path / "carts"
    for cart in ("teapot", "esp88"):
        jet_cart.build(str(carts), cart=cart)
    site = _site(tmp_path, [carts / "teapot.moy", carts / "esp88.moy"])
    for folder, title in (("teapot.moy", "Jet Teapot"), ("esp88.moy", "ESP 88")):
        out, js = _play(tmp_path, site, folder.split(".")[0],
                        "?handheld=1&dev=1&cart=" + folder, [
            {"wait": 4000}, {"js": _STATE}, {"js": _COLOURS}, {"shot": "playing"},
        ])
        assert len(js) == 2, out[-3000:]
        st = json.loads(js[0])
        assert st["cart"] == title and st["err"] is None and st["notice"] is None, st
        assert js[1] > 16, "%s drew next to nothing (%d colours)" % (title, js[1])


def _png(w, h):
    """A plain RGB PNG: a cover a shelf can decode."""
    raw = b"".join(b"\0" + bytes(v for x in range(w)
                                  for v in ((x * 2) & 255, (y * 2) & 255, 96))
                   for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _shelf(pages, releases, tier):
    """One compiled cart in a carts repository's shape: its release asset a
    STORED zip carrying main.wasm, its files, its cover and a module for each
    of two chips, mirrored on the repository's Pages site."""
    files = _files(tier)
    files["cover.png"] = cover = _png(128, 128)
    files["LICENSES.txt"] = b"MIT, by the test.\n"
    for chip in ("esp32s3", "esp32p4"):
        files["main.%s.f2.aot" % chip] = os.urandom(200000)
    z = stored_zip("tier.moy", files)
    rel = "releases/tier-v1/tier.moy.zip"
    pages.files["/" + rel] = z
    releases.files["/o/carts/releases/download/tier-v1/tier.moy.zip"] = z
    lic = b"The MIT licence of the test carts.\n"
    pages.files["/carts/LICENCE.txt"] = lic
    pages.files["/carts/tier/cover.png"] = cover
    entry = {
        "id": "tier", "name": "Tier Wasm", "version": 1, "folder": "tier.moy",
        "runtime": "wasm", "memory": 4, "chips": ["esp32s3", "esp32p4"],
        "licence": {"spdx": "MIT", "name": "MIT License", "url": "carts/LICENCE.txt",
                    "size": len(lic), "sha256": sha(lic)},
        "cover": {"url": "carts/tier/cover.png", "size": len(cover),
                  "sha256": sha(cover), "w": 128, "h": 128},
        "assets": [{"name": "tier.moy.zip",
                    "url": "%s/o/carts/releases/download/tier-v1/tier.moy.zip"
                           % releases.base,
                    "mirror": rel, "size": len(z), "sha256": sha(z),
                    "files": dict((fn, {"size": len(b), "sha256": sha(b)})
                                  for fn, b in files.items())}],
        "external": [],
    }
    pages.files["/index.json"] = json.dumps(
        {"version": 1, "name": "Test shelf", "home": releases.base + "/o/carts",
         "carts": [entry]}).encode()
    return z, files


def _zip_spans(z):
    """{member name: (header start, data end)} of a stored zip."""
    import zipfile
    out = {}
    with zipfile.ZipFile(io.BytesIO(z)) as zf:
        for info in zf.infolist():
            h = info.header_offset
            n, x = struct.unpack("<HH", z[h + 26:h + 30])
            out[info.filename] = (h, h + 30 + n + x + info.compress_size)
    return out


def test_get_carts_installs_a_compiled_cart_without_its_modules_and_it_plays(tmp_path):
    web_e2e.require("store", "carts")
    pages, releases = web_e2e.Host(cors=True), web_e2e.Host(cors=False)
    tier = _built(TIER, tmp_path / "src" / "tier.moy")
    z, files = _shelf(pages, releases, tier)
    site = _site(tmp_path, index=pages.base + "/index.json")
    server, base = web_e2e.serve(site, web_e2e.free_port())
    profile = tmp_path / "chrome"
    sys.path.insert(0, str(ROOT))
    from runtime.getcarts_app import GetCartsLayout
    lay = GetCartsLayout(320, 240, 1, False, 1)
    row = lay.row_rect(0)
    one = lay.buttons(1)[0]
    try:
        out, js = web_e2e.run(tmp_path, "install", "?handheld=1&dev=1&cart=get_carts.moy", [
            {"note": "Get Carts lists the compiled cart as one to GET", "wait": 4000},
            {"shot": "list"},
            {"click": [row[0] + row[2] // 2, row[1] + row[3] // 2]},
            {"wait": 800}, {"shot": "cart"},
            {"click": [one[0] + one[2] // 2, one[1] + one[3] // 2]},
            {"wait": 6000}, {"shot": "ready"},
            {"js": "window.__moyInstalled || ''"},
        ], base, profile)
        assert js == ["tier.moy"], "the compiled cart did not install:\n%s" % out[-4000:]
        mirror = "/releases/tier-v1/tier.moy.zip"
        spans = [(a, b) for p, a, b in pages.ranges if p == mirror]
        assert spans and len(spans) == pages.asked.count(mirror), \
            "the asset was read whole, not by range: %r" % pages.asked
        assert pages.preflights == [], "a range the page needed a preflight for"
        assert not [p for p in releases.asked if p.endswith(".zip")], releases.asked
        members = _zip_spans(z)
        for a, b in spans[1:]:
            for name, (h, e) in members.items():
                if name.endswith(".aot"):
                    assert b < h or a >= e, "a chip's module crossed: %s" % name
        fetched = sum(b - a + 1 for a, b in spans)
        assert fetched < len(z) // 2, (fetched, len(z))

        out2, js2 = web_e2e.run(tmp_path, "play", "?handheld=1&dev=1&cart=tier.moy", [
            {"wait": 3000}, {"js": _STATE}, {"js": _CANVAS}, {"shot": "plays"},
        ], base, profile)
        st = json.loads(js2[0])
        assert st["cart"] == "Tier Wasm" and st["err"] is None and st["notice"] is None, \
            "the installed cart did not play:\n%s" % out2[-3000:]
        assert _digest(js2[1]) == tier_frame.golden()
        print("\nwasm store e2e: %d of %d asset bytes fetched by range" % (fetched, len(z)))
    finally:
        server.terminate()
        server.wait(timeout=10)
        pages.stop()
        releases.stop()

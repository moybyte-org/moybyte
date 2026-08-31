"""The p8 import runs on a REAL MicroPython, not just on CPython (#194).

The browser converts a dropped PICO-8 cart by running the SAME THREE files the
desktop CLI runs -- moy-spec's vendored asset converter, moy-spec's vendored Lua
porter, and our guards/report file -- inside the wasm VM. Everything else in the
tree checks those files under CPython, where `os.path`, `zlib`, `str.isalnum`,
f-strings, `json.dumps(indent=...)` and a full regex engine all exist and none
of them do in MicroPython. So this suite drives the whole import under `make
unix-micropython`'s desktop MicroPython, which is the same interpreter (v1.28,
same stdlib surface) the wasm build is.

THIS IS THE CHECK THAT PAID FOR ITSELF, on 2026-08-29. `p8_lua_port.py` was
about to be vendored on the strength of "it is stdlib-only Python"; it was, and
it did not run. `localization_lua`'s two patterns used a LOOKBEHIND, a
non-capturing group, an inline `(?m)` and a negative lookahead, and MicroPython's
`re` rejects all four at COMPILE time ("regex too complex") -- so the browser's
import would have died on the first cart, at a line that reads fine. Three more
followed: `str.isalnum` (absent, four call sites), `json.dump(indent=2)` (no
`indent` keyword) and `os.makedirs`/`os.path.join`. All six were fixed UPSTREAM
in moy-spec and re-vendored, because a regex engine and a str method cannot be
injected from out here the way `os.path.basename` is.

WHAT IT IS GUARDING NOW. Four things, each of which would fail SILENTLY in the
browser and nowhere else:

  * a CPython-only construct creeping into `tools/p8_writer.py`, whose whole
    reason to exist as a separate file is that it is shared with the wasm
    console;
  * the same, in a RE-VENDORED `tools/p8_lua_port.py` -- a static scan of that
    file lives in tests/test_p8_import_vendor.py and knows only the six
    constructs already found; this lane is what would find the seventh;
  * `firmware/web_runner/shims/zlib.py` -- four lines over the built-in
    `deflate` -- ceasing to inflate a real PNG IDAT, which is the ONLY thing
    standing between the browser and a second, JavaScript reader of the
    `.p8.png` format;
  * a re-vendored converter reaching for more of `os` than the one
    `os.path.basename` that `_ensure_os_path` injects (tests/
    test_p8_import_vendor.py pins the converter's side of that; this proves the
    injection actually works).

The `.p8.png` case is the one worth the seconds: the PNG unfilter is the
heaviest interpreted loop in the feature, and every filter type is exercised
because tests/p8_fixture.py rotates through all five.

MEMORY, measured here 2026-08-29: the fixture imports inside MicroPython's
2MB unix default; a real BBS cart (Celeste, ~8000 lines) does NOT and needs
about 8MB. The browser gives the VM 16MB (worker.js), so that is headroom
rather than a limit -- but it is the number a future DEVICE leg has to clear,
and it is why this suite drives the small fixture rather than a real cart.
"""

import json
import os
import subprocess
import sys

import pytest

import unix_mp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

SHIM = os.path.join(ROOT, "firmware", "web_runner", "shims", "zlib.py")


def _stage(tmp_path):
    """The frozen set the wasm build stages, as a flat directory on disk.

    Deliberately assembled by NAME rather than by running build.sh: this is
    checking the FILES, and a copy here that drifts from build.sh's is caught by
    tests/test_staging_closure.py, which derives the web frozen set from the
    same declarations."""
    import p8_fixture
    import import_p8

    for rel in ("tools/p8_import.py", "tools/p8_lua_port.py",
                "tools/p8_writer.py"):
        src = os.path.join(ROOT, rel)
        with open(src, encoding="utf-8") as f:
            body = f.read()
        with open(os.path.join(str(tmp_path), os.path.basename(rel)), "w",
                  encoding="utf-8") as f:
            f.write(body)
    with open(SHIM, encoding="utf-8") as f:
        shim = f.read()
    with open(os.path.join(str(tmp_path), "zlib.py"), "w", encoding="utf-8") as f:
        f.write(shim)
    return p8_fixture.write_pair(str(tmp_path), import_p8.parse_p8)


DRIVER = """
import sys
sys.path.insert(0, ".")
import json, p8_import, p8_writer

path = sys.argv[1]
f = open(path, "rb")
blob = f.read()
f.close()
out = {"png_problem": p8_writer.png_problem(blob)
       if p8_writer.looks_like_png(blob) else "not-a-png"}
sections = p8_import.read_p8(path)
out["sections_problem"] = p8_writer.sections_problem(sections)
title = p8_writer.cart_title(sections, path)
summary = p8_writer.write_cart(sections, "out.moy", title)
out["title"] = title
out["report"] = p8_writer.report_lines(summary)
out["unsupported"] = summary["unsupported"]
out["differs"] = summary["differs"]
out["files"] = sorted(__import__("os").listdir("out.moy"))
f = open("out.moy/manifest.json")
out["manifest_text"] = f.read()
out["manifest"] = json.loads(out["manifest_text"])
f.close()
f = open("out.moy/main.lua")
main = f.read()
f.close()
out["main_len"] = len(main)
out["main_sha"] = _sha(main)
for probe in ("PICO-8 compatibility shim", "local P8_VH = 120",
              "function p8_draw()", "__p8_gff", "-- Localized p8 API"):
    out["probe_" + probe.split()[-1]] = probe in main
# The map and the sheet are OPTIONAL outputs -- a cart with no __map__ gets no
# map.moymap, and the anon-title fixture is exactly that cart.
for name, key in (("sprites.moygfx", "gfx"), ("map.moymap", "map")):
    try:
        f = open("out.moy/" + name)
    except OSError:
        continue
    text = f.read()
    f.close()
    out[key + "_sha"] = _sha(text)
    if key == "gfx":
        out["gfx0"] = text.split("\\n")[0]
print("RESULT " + json.dumps(out))
"""

# `_sha` rather than shipping whole files back through one JSON line: the point
# of the cross-tier comparison below is byte identity, and a hash says that in
# 64 characters. binascii + hashlib are both MicroPython builtins.
_SHA = """
import hashlib, binascii
def _sha(text):
    return binascii.hexlify(hashlib.sha256(text.encode()).digest()).decode()
"""


def _drive(tmp_path, cart, name="run.py"):
    exe = unix_mp.require_unix_mp(
        why="This is the only lane that proves the p8 import -- the vendored\n"
            "converter, the .moy writer and the zlib shim -- actually runs on\n"
            "MicroPython. The browser runs those exact files.")
    with open(os.path.join(str(tmp_path), name), "w", encoding="utf-8") as f:
        f.write(_SHA + DRIVER)
    r = subprocess.run([exe, name, cart], cwd=str(tmp_path),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, "MicroPython refused the import:\n%s\n%s" % (
        r.stdout[-3000:], r.stderr[-3000:])
    line = [l for l in r.stdout.split("\n") if l.startswith("RESULT ")]
    assert line, r.stdout[-3000:]
    return json.loads(line[0][len("RESULT "):])


@pytest.mark.parametrize("form", ["p8", "p8.png"])
def test_the_whole_import_runs_on_micropython(tmp_path, form):
    p8, png = _stage(tmp_path)
    got = _drive(tmp_path, p8 if form == "p8" else png)

    assert got["sections_problem"] is None
    assert got["title"] == "tiny dash"
    assert got["files"] == ["main.lua", "manifest.json", "map.moymap",
                            "sounds.json", "sprites.moygfx"]
    assert got["manifest"]["canvas"] == "128x128"
    assert got["manifest"]["main"] == "main.lua"
    assert got["manifest"]["safe_to_share"] is False
    # The whole POINT, on the tier that nearly could not do it: the shim, the
    # zoom hint, the renamed lifecycle, the flag table and the localization
    # block -- the last of which is `localization_lua`, the function whose two
    # regexes MicroPython refused to compile at all.
    assert got["probe_shim"] and got["probe_120"] and got["probe_p8_draw()"]
    assert got["probe___p8_gff"] and got["probe_API"]
    # ...and the ART is really there, not an empty grid from a failed inflate.
    assert got["gfx0"].startswith("0123456789abcdef")
    # ...and the compatibility report names the cart and says what the code is.
    text = " ".join(got["report"])
    assert "imported." in text
    assert "cart's own Lua" in text
    assert "CODE did NOT" not in text
    assert any("dset" in u for u in got["unsupported"])
    # `differs` is EMPTY, and that is the assertion: as of 2026-08-30 every verb
    # that used to mean something else here graduated to a real shim. The
    # CPython twin (tests/test_import_p8.py) guards the reporting MECHANISM with
    # a synthetic entry, so this side only has to prove the census is honest --
    # naming a verb here rots the moment that verb is shimmed, which is exactly
    # how this line came to assert a shipped sspr() was still broken.
    assert got["differs"] == []


def test_micropython_and_cpython_write_the_same_cart(tmp_path):
    """ONE WRITER, and here is what that has to MEAN: the browser and the CLI
    produce the same bytes.

    Cheap to say and easy to lose -- MicroPython's dicts are not
    insertion-ordered, which is why the porter declares its manifest field order
    instead of taking the dict's. The four files this compares are the ones the
    two tiers both fully control; `sounds.json` is deliberately not among them,
    because its nested per-step dicts come out of the vendored converter in a
    tier-dependent key order (same content, same length -- checked by
    tests/test_import_p8.py through the audio model, which is the level that
    matters for a bank)."""
    import hashlib

    p8, _png = _stage(tmp_path)
    got = _drive(tmp_path, p8)

    import import_p8
    out = os.path.join(str(tmp_path), "cpython.moy")
    import_p8.import_p8(p8, out)

    def sha(name):
        with open(os.path.join(out, name), encoding="utf-8") as f:
            return hashlib.sha256(f.read().encode()).hexdigest()

    assert got["main_sha"] == sha("main.lua"), \
        "main.lua differs between MicroPython and CPython"
    assert got["gfx_sha"] == sha("sprites.moygfx")
    assert got["map_sha"] == sha("map.moymap")
    assert got["manifest_text"] == open(
        os.path.join(out, "manifest.json"), encoding="utf-8").read()


def test_a_cart_with_no_title_comment_is_named_from_its_filename(tmp_path):
    """The `os.path.basename` branch, which is the ONE thing MicroPython cannot
    give the vendored converter and the one `p8_writer._ensure_os_path` injects.

    It only fires for a cart whose Lua opens with no `--` comment, so the main
    fixture never reaches it -- and a shim that is never exercised is a shim
    that will be broken the first time somebody drops a cart without a header.
    """
    _stage(tmp_path)
    anon = os.path.join(str(tmp_path), "space-blaster_2.p8")
    with open(anon, "w", encoding="utf-8") as f:
        f.write("pico-8 cartridge\nversion 41\n__lua__\nx = 1\n"
                "__gfx__\n" + "1" * 128 + "\n")
    got = _drive(tmp_path, anon, name="anon.py")
    assert got["title"] == "space blaster 2", got["title"]


def test_the_png_guard_names_a_picture_on_micropython(tmp_path):
    """The guard matters MOST here: the browser freezes at opt=3, which strips
    the converter's own assert-based validation, so this is the only thing left
    between a dropped holiday photo and a struct unpack deep in the PNG walk."""
    import zlib as host_zlib
    import struct

    _stage(tmp_path)
    w, h = 8, 8
    raw = b"".join(b"\x00" + b"\x00" * (w * 4) for _ in range(h))

    def chunk(t, b):
        return (struct.pack(">I", len(b)) + t + b
                + struct.pack(">I", host_zlib.crc32(t + b) & 0xFFFFFFFF))

    photo = os.path.join(str(tmp_path), "photo.p8.png")
    with open(photo, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", host_zlib.compress(raw)) + chunk(b"IEND", b""))

    exe = unix_mp.require_unix_mp()
    with open(os.path.join(str(tmp_path), "guard.py"), "w",
              encoding="utf-8") as f:
        f.write("import sys\nsys.path.insert(0, '.')\nimport p8_writer\n"
                "f = open(sys.argv[1], 'rb')\nb = f.read()\nf.close()\n"
                "print('PROBLEM ' + str(p8_writer.png_problem(b)))\n")
    r = subprocess.run([exe, "guard.py", photo], cwd=str(tmp_path),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
    assert "160x205" in r.stdout, r.stdout + r.stderr


def test_the_zlib_shim_inflates_what_cpython_deflated(tmp_path):
    """`shims/zlib.py` in isolation. It is four lines, it is the only thing the
    `.p8.png` path needs that MicroPython does not ship, and if it ever stops
    working the tempting fix is a second reader in JavaScript."""
    import zlib as host_zlib

    _stage(tmp_path)
    payload = (b"pico-8 cartridge // http://www.pico-8.com\n" * 40)
    with open(os.path.join(str(tmp_path), "blob.bin"), "wb") as f:
        f.write(host_zlib.compress(payload, 9))
    exe = unix_mp.require_unix_mp()
    with open(os.path.join(str(tmp_path), "inflate.py"), "w",
              encoding="utf-8") as f:
        f.write("import sys\nsys.path.insert(0, '.')\nimport zlib\n"
                "f = open('blob.bin', 'rb')\nb = f.read()\nf.close()\n"
                "out = zlib.decompress(b)\n"
                "print('LEN %d %s' % (len(out), out[:16]))\n")
    r = subprocess.run([exe, "inflate.py"], cwd=str(tmp_path),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
    assert "LEN %d" % len(payload) in r.stdout, r.stdout + r.stderr
    assert "pico-8 cartridge" in r.stdout

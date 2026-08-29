"""The p8 import runs on a REAL MicroPython, not just on CPython (#194).

The browser converts a dropped PICO-8 cart by running the SAME two files the
desktop CLI runs -- moy-spec's vendored converter and our `.moy` writer --
inside the wasm VM. Everything else in the tree checks those files under
CPython, where `os.path`, `zlib`, f-strings and `json.dumps(indent=...)` all
exist and none of them do in MicroPython. So this suite drives the whole import
under `make unix-micropython`'s desktop MicroPython, which is the same
interpreter (v1.28, same stdlib surface) the wasm build is.

WHAT IT IS ACTUALLY GUARDING. Three separate things, each of which would fail
SILENTLY in the browser and nowhere else:

  * a CPython-only construct creeping into `tools/p8_writer.py`, whose whole
    reason to exist as a separate file is that it is shared with the wasm
    console;
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

    for rel in ("tools/p8_import.py", "tools/p8_writer.py"):
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
out["files"] = sorted(__import__("os").listdir("out.moy"))
f = open("out.moy/manifest.json")
out["manifest"] = json.loads(f.read())
f.close()
f = open("out.moy/main.py")
out["main_head"] = f.read()[:2000]
f.close()
f = open("out.moy/sprites.moygfx")
out["gfx0"] = f.read().split("\\n")[0]
f.close()
print("RESULT " + json.dumps(out))
"""


def _drive(tmp_path, cart, name="run.py"):
    exe = unix_mp.require_unix_mp(
        why="This is the only lane that proves the p8 import -- the vendored\n"
            "converter, the .moy writer and the zlib shim -- actually runs on\n"
            "MicroPython. The browser runs those exact files.")
    with open(os.path.join(str(tmp_path), name), "w", encoding="utf-8") as f:
        f.write(DRIVER)
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
    assert got["files"] == ["config.json", "main.py", "manifest.json",
                            "sounds.json", "sprites.moygfx"]
    # The zoom hint (#194's guaranteed footgun) survives the tier change.
    assert got["manifest"]["canvas"] == "128x128"
    assert got["manifest"]["safe_to_share"] is False
    assert "view(128, 120)" in got["main_head"]
    # ...and the ART is really there, not an empty grid from a failed inflate.
    assert got["gfx0"].startswith("0123456789abcdef")
    # ...and the compatibility report says the two things it must.
    text = " ".join(got["report"])
    assert "CODE did NOT" in text
    assert any("sspr" in u for u in got["unsupported"])


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

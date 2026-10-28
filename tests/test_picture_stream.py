"""The desk's picture read from its file a row at a time: the C reader
(native/moy_store/moy_img.h's streamed reader, `moy_ufiles.cover_image`) held
to its reference (`moyimg.cover_stream`) over every shape, window and way a
file is not one; the Wallpaper reading a 1024x600 picture into one
screen-sized buffer and keeping the screen it made beside the picture; and a
picture cart's one-picture-per-glass-size field."""

import binascii
import glob
import io
import json
import os
import random
import zlib

import pytest

from runtime import host_app, moyimg, moy_carts
from runtime.file_widgets import cover_indices
from runtime.moy_image import load_picture_bake, save_picture_bake
from runtime.wallpaper import Wallpaper, pick_picture, picture_sizes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREENS = ((320, 240), (480, 320), (1024, 600), (1280, 800), (37, 91))


def _uf():
    from tools import moy_ufiles_binding
    try:
        moy_ufiles_binding.library()
    except ImportError as exc:
        pytest.skip(str(exc))
    return moy_ufiles_binding.binding()


class _Trickle(io.RawIOBase):
    """A stream that answers every read with a few bytes, so the readers'
    seams fall everywhere."""

    def __init__(self, data, seed):
        self.data = data
        self.at = 0
        self.rnd = random.Random(seed)

    def readable(self):
        return True

    def read(self, n=-1):
        if n is None or n < 0:
            n = len(self.data)
        k = min(n, self.rnd.randrange(1, 8))
        got = self.data[self.at:self.at + k]
        self.at += len(got)
        return got


def _both(blob, dw, dh, mw=4096, mh=4096, trickle=None):
    uf = _uf()
    a, b = bytearray(dw * dh), bytearray(dw * dh)
    src = _Trickle(blob, trickle) if trickle is not None else io.BytesIO(blob)
    got_c = uf.cover_image(src, a, dw, dh, mw, mh)
    got_py = moyimg.cover_stream(io.BytesIO(blob), b, dw, dh, mw, mh)
    assert got_c == got_py, (blob[:80], got_c, got_py)
    if got_c is not None:
        assert a == b
    return got_c, a


def _art(w, h, seed=7):
    rnd = random.Random(seed)
    return bytes(rnd.randrange(64) if rnd.random() < 0.2 else (x // 9 + y // 5) % 64
                 for y in range(h) for x in range(w))


def _blob(w, h, pix, wbits=12, level=9, head=None, tail=""):
    comp = zlib.compressobj(level, zlib.DEFLATED, wbits)
    z = comp.compress(bytes(pix)) + comp.flush()
    data = binascii.b2a_base64(z).decode().strip()
    if head is None:
        head = '{"format": "moyimg-v1", "w": %d, "h": %d, ' % (w, h)
    return (head + '"data": "' + data + '"' + tail + "}").encode()


def test_every_shipped_image_reads_as_the_reference_on_every_screen():
    files = [f for f in glob.glob(os.path.join(ROOT, "system_carts", "**", "*.moyimg"),
                                  recursive=True)]
    assert files
    for f in files:
        with open(f, "rb") as fh:
            blob = fh.read()
        w, h, pix = moyimg.decode_moyimg(blob.decode())
        for dw, dh in SCREENS:
            got, out = _both(blob, dw, dh)
            assert got == (w, h), f
            assert out == cover_indices(pix, w, h, dw, dh), (f, dw, dh)


def test_pictures_of_every_shape_and_window_through_any_seam():
    rnd = random.Random(11)
    for w, h in ((1, 1), (7, 300), (300, 7), (320, 240), (512, 300), (1024, 600)):
        pix = _art(w, h, rnd.randrange(99))
        for wbits in (9, 12, 15):
            blob = _blob(w, h, pix, wbits)
            dw, dh = rnd.choice(SCREENS)
            got, out = _both(blob, dw, dh, trickle=rnd.randrange(99))
            assert got == (w, h)
            assert out == cover_indices(pix, w, h, dw, dh)


def test_a_copy_carries_its_provenance_after_the_picture():
    """The wallpaper copy WALL writes is the picture with `src` and `sig`
    after `data`: it is what a theme's My Art picture is."""
    pix = _art(64, 48)
    text = json.loads(moyimg.encode_moyimg(64, 48, pix))
    text["src"] = "drawings/sunset"
    text["sig"] = 3911139466
    blob = json.dumps(text).encode()
    assert _both(blob, 32, 24)[0] == (64, 48)
    for tail, ok in ((', "src": "a"', True), (' , "src": {"x": [1]}', True),
                     (', "src": ', False), (', ', False), (' "src": 1', False),
                     (', "pad": "' + "x" * 300 + '"', False)):
        assert (_both(_blob(64, 48, pix, tail=tail), 32, 24)[0] is not None) == ok, tail


def test_what_is_not_a_picture_in_the_streamed_form():
    pix = _art(16, 8)
    good = _blob(16, 8, pix).decode()
    data = good[good.index('"data": "') + 9:good.rindex('"')]
    z = binascii.a2b_base64(data)
    wrong = [
        "", "{}", "[]", "not json",
        good[:-1],                                          # no closing brace
        good + "x",                                         # bytes after the end
        good.replace('"data": "', '"data":"\\u0041'),       # an escape
        good.replace(data, data[:10] + "\n" + data[10:]),   # whitespace in base64
        good.replace(data, data + "="),                     # padding it does not ask for
        good.replace(data, data.rstrip("=")),               # padding it asks for, gone
        good.replace(data, data[:-5]),                      # a truncated stream
        good.replace('"w": 16', '"w": 17'),                 # another size
        good.replace('"w": 16', '"w": 0'),
        good.replace('"w": 16', '"w": "x"'),
        good.replace('"h": 8, ', ""),                       # no h
        '{"data": "%s", "w": 16, "h": 8}' % data,          # data before the size
        '{"pad": "%s", "w": 16, "h": 8, "data": "%s"}' % ("y" * 250, data),
        '{"w": 16 "h": 8, "data": "%s"}' % data,            # a head that is not JSON
        good.replace(data, binascii.b2a_base64(zlib.compress(bytes(pix) + b"\0"))
                     .decode().strip()),                    # a stream one longer
        good.replace(data, binascii.b2a_base64(z[:-4] + b"\0\0\0\0").decode().strip()),
        good.replace(data, binascii.b2a_base64(
            zlib.compress(bytes(pix))[2:]).decode().strip()),  # no ZLIB header
    ]
    for text in wrong:
        assert _both(text.encode(), 4, 4)[0] is None, text[:90]
    assert _both(good.encode(), 4, 4)[0] == (16, 8)
    # Whitespace around the punctuation is JSON's, and read.
    spaced = good.replace('"data": "', '"data" :\n "').replace('"}', '" \n}\n')
    assert _both(spaced.encode(), 4, 4)[0] == (16, 8)


def test_a_picture_larger_than_the_reader_takes_is_none_before_it_is_read():
    blob = _blob(64, 32, _art(64, 32))
    assert _both(blob, 8, 8, 63, 32)[0] is None
    assert _both(blob, 8, 8, 64, 31)[0] is None
    assert _both(blob, 8, 8, 64, 32)[0] == (64, 32)


def test_a_source_that_fails_is_its_error_not_a_missing_picture():
    uf = _uf()

    class Broken(io.RawIOBase):
        def readable(self):
            return True

        def read(self, n=-1):
            raise OSError(5, "the card went away")

    with pytest.raises(OSError):
        uf.cover_image(Broken(), bytearray(16), 4, 4, 64, 64)
    with pytest.raises(ValueError):
        uf.cover_image(io.BytesIO(b"{}"), bytearray(15), 4, 4, 64, 64)


def test_the_file_stamp_is_the_text_stamp():
    for text in ("", "x", "a" * 63, "b" * 64, "c" * 65, moyimg.encode_moyimg(9, 9, _art(9, 9))):
        raw = text.encode()
        assert moyimg.bytes_sig(raw[:64], raw[-64:], len(raw)) == moyimg.text_sig(text)
        assert moyimg.file_sig(io.BytesIO(raw)) == moyimg.text_sig(text)


def test_a_sized_inflate_refuses_another_count():
    z = zlib.compress(b"abc")
    assert moyimg._inflate(z, 3) == b"abc"
    assert moyimg._inflate(z, 4) is None and moyimg._inflate(z, 2) is None


# -- the desk ----------------------------------------------------------------

def _desk(tmp_path, size, monkeypatch, w=1024, h=600, seed=7):
    """A workstation on a `size` glass whose theme picture is a w x h file in
    a package folder of its own."""
    ws = host_app.build_workstation(str(tmp_path / "carts"), sys_size=size)
    pkg = tmp_path / "pkg"
    pkg.mkdir(exist_ok=True)
    pix = _art(w, h, seed)
    (pkg / "desk.moyimg").write_text(moyimg.encode_moyimg(w, h, pix))
    ref = [(str(pkg), "desk.moyimg", None)]
    monkeypatch.setattr(ws.look, "picture_ref", lambda: ref[0])
    ws.look.wallpaper_id = ws.look.PICTURE
    ws.look.picture_gen += 1
    return ws, pix, pkg


@pytest.mark.parametrize("size", [(320, 240), (480, 320), (1024, 600)])
def test_a_1024x600_picture_is_read_into_one_screen(tmp_path, monkeypatch, size):
    ws, pix, pkg = _desk(tmp_path, size, monkeypatch)
    wall = ws.wallpaper
    cv = ws.sys_canvas
    assert wall.draw_picture(cv)
    cw, ch = size
    want = cover_indices(pix, 1024, 600, cw, ch)
    got = wall._pic_data
    assert got[:2] == (cw, ch) and len(got[2]) == cw * ch
    assert bytes(got[2]) == bytes(want)
    for x, y in ((0, 0), (cw - 1, ch - 1), (cw // 2, ch // 3)):
        assert cv.pix(x, y) == want[y * cw + x]
    # Nothing of the source is kept: the screen is the only raster.
    assert wall._pic_pix is got[2]
    # ...and the screen is kept beside the picture.
    assert (pkg / "thumbs" / ("pic%dx%d.mct" % size)).is_file()


def test_the_next_read_is_the_kept_screen(tmp_path, monkeypatch):
    ws, pix, pkg = _desk(tmp_path, (480, 320), monkeypatch)
    assert ws.wallpaper.draw_picture(ws.sys_canvas)
    first = bytes(ws.wallpaper._pic_data[2])

    import runtime.moyimg as rm
    monkeypatch.setattr(ws.carts_store, "cover_image", None, raising=False)
    calls = []
    monkeypatch.setattr(rm, "cover_stream", lambda *a: calls.append(a))
    wall = Wallpaper(ws, ws.wallpaper._NAMES)
    assert wall.draw_picture(ws.sys_canvas)
    assert bytes(wall._pic_data[2]) == first and calls == []

    # Another picture in the same file: the kept screen is stale, and read anew.
    (pkg / "desk.moyimg").write_text(moyimg.encode_moyimg(1024, 600, _art(1024, 600, 3)))
    ws.look.picture_gen += 1
    assert wall.draw_picture(ws.sys_canvas)
    assert bytes(wall._pic_data[2]) == bytes(
        cover_indices(_art(1024, 600, 3), 1024, 600, 480, 320))


def test_a_kept_screen_that_does_not_read_is_absent(tmp_path):
    out = bytearray(12)
    save_picture_bake(str(tmp_path), 4, 3, 99, bytes(range(12)))
    assert load_picture_bake(str(tmp_path), 4, 3, 99, out) and out == bytes(range(12))
    assert not load_picture_bake(str(tmp_path), 4, 3, 98, out)        # stale
    assert not load_picture_bake(str(tmp_path), 3, 4, 99, out)        # another screen
    f = tmp_path / "thumbs" / "pic4x3.mct"
    f.write_bytes(f.read_bytes()[:-1])
    assert not load_picture_bake(str(tmp_path), 4, 3, 99, out)        # short
    f.write_bytes(f.read_bytes() + b"xy")
    assert not load_picture_bake(str(tmp_path), 4, 3, 99, out)        # long


def test_a_picture_past_the_largest_draws_the_fill(tmp_path, monkeypatch):
    ws, _pix, _pkg = _desk(tmp_path, (320, 240), monkeypatch, w=1025, h=600)
    assert not ws.wallpaper.draw_picture(ws.sys_canvas)
    assert ws.wallpaper._pic_pix is None
    # ...once: the answer is kept until the picture changes.
    assert ws.wallpaper._pic_key is not None


def test_a_package_file_is_read_at_its_own_size(tmp_path):
    """read(cap + 1) asks MicroPython for the cap whatever the file holds."""
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    look = ws.look
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "f.bin").write_bytes(b"12345")
    look._pkg_paths["x"] = str(pkg)
    asked = []
    import builtins
    real = builtins.open

    class Rec:
        def __init__(self, f):
            self.f = f

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.f.close()

        def seek(self, *a):
            return self.f.seek(*a)

        def read(self, n=-1):
            asked.append(n)
            return self.f.read(n)

    def fake(p, mode="r", *a, **k):
        f = real(p, mode, *a, **k)
        return Rec(f) if str(p).endswith("f.bin") else f

    builtins.open = fake
    try:
        assert look._package_file("x", "f.bin", 1 << 20) == b"12345"
        assert look._package_file("x", "f.bin", 3) == b"1234"
    finally:
        builtins.open = real
    assert asked == [5, 4]


# -- a picture cart ------------------------------------------------------------

def test_the_pictures_field_reads_strictly():
    assert picture_sizes({"320x240": "a", "1024x600": "b"}) == [(320, 240, "a"),
                                                               (1024, 600, "b")]
    for bad in (None, {}, [], "a", {"320x240": ""}, {"320x240": 3}, {"320": "a"},
                {"0x240": "a"}, {"x240": "a"}, {"320x240x1": "a"}, {"320X240": "a"},
                {"320x240": "../a"}, {"320x240": "a/b"}, {"320x240": ".a"},
                {"320x240": "a", "big": "b"}):
        assert picture_sizes(bad) is None, bad


def test_a_glass_takes_its_own_size_else_the_next_larger_else_the_largest():
    sizes = picture_sizes({"320x240": "s", "480x320": "m", "1024x600": "l",
                           "1280x800": "xl"})
    assert pick_picture(sizes, 320, 240) == "s"
    assert pick_picture(sizes, 480, 320) == "m"
    assert pick_picture(sizes, 400, 300) == "m"
    assert pick_picture(sizes, 800, 480) == "l"
    assert pick_picture(sizes, 1280, 720) == "xl"
    assert pick_picture(sizes, 1920, 1080) == "xl"
    assert pick_picture(sizes, 330, 100) == "m"


def _picture_cart(root, pictures, images):
    d = os.path.join(root, "local.pics.moy")
    os.makedirs(os.path.join(d, "images"))
    with open(os.path.join(d, "manifest.json"), "w") as f:
        json.dump({"format": "moybyte-cart-v1", "title": "Pics", "id": "local.pics",
                   "runtime": "python", "main": "main.py",
                   "moybyte": {"type": "wallpaper", "edit": [], "pictures": pictures}}, f)
    with open(os.path.join(d, "main.py"), "w") as f:
        f.write("RAN = 1\ndef _draw():\n    cls(3)\n")
    for name, (w, h, pix) in images.items():
        with open(os.path.join(d, "images", name + ".moyimg"), "w") as f:
            f.write(moyimg.encode_moyimg(w, h, pix))
    return d


def test_a_picture_cart_draws_its_glass_picture_and_runs_nothing(tmp_path):
    root = str(tmp_path / "carts")
    small, big = _art(320, 240, 1), _art(1024, 600, 2)
    d = _picture_cart(root, {"320x240": "small", "1024x600": "big"},
                      {"small": (320, 240, small), "big": (1024, 600, big)})
    for size, want in (((320, 240), small),
                       ((480, 320), cover_indices(big, 1024, 600, 480, 320))):
        ws = host_app.build_workstation(root, sys_size=size)
        ws.look.select_wallpaper("local.pics", persist=False)
        wall = ws.wallpaper
        assert wall._wp_pics is not None and wall._wp_ns is None
        wall.draw(0)
        assert bytes(wall._pic_data[2]) == bytes(want), size
        assert os.path.isfile(os.path.join(d, "thumbs", "pic%dx%d.mct" % size))


def test_a_copy_of_a_picture_cart_is_one(tmp_path):
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    d = _picture_cart(root, {"320x240": "small"}, {"small": (320, 240, _art(320, 240))})
    cart = moy_carts.load(d)
    assert cart["pictures"] == {"320x240": "small"}
    dup = moy_carts.duplicate(cart, root)
    assert dup["pictures"] == {"320x240": "small"}
    assert os.path.isfile(os.path.join(dup["path"], "images", "small.moyimg"))


def test_a_seeded_picture_cart_keeps_its_pictures(tmp_path):
    import sys
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import gen_device_carts
    src = str(tmp_path / "src")
    _picture_cart(src, {"320x240": "small"}, {"small": (8, 6, _art(8, 6))})
    os.rename(os.path.join(src, "local.pics.moy"), os.path.join(src, "moybyte.pics.moy"))
    with open(os.path.join(src, "moybyte.pics.moy", "manifest.json")) as f:
        man = json.load(f)
    man["id"] = "moybyte.pics"
    man["moybyte"].update(system=True, order=99)
    with open(os.path.join(src, "moybyte.pics.moy", "manifest.json"), "w") as f:
        json.dump(man, f)
    packed = gen_device_carts.build_packed(src)
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    moy_carts.seed_any([(t, v, b) for t, v, b in packed], root, {})
    cart = moy_carts.load(os.path.join(root, "moybyte.pics.moy"))
    assert cart["pictures"] == {"320x240": "small"}


MP_DRIVER = r'''
import sys, json, hashlib, binascii, io
sys.path.insert(0, @RUNTIME@)
import moyimg, moy_ufiles
out = []
for path, dw, dh in json.loads(@JOBS@):
    rows = []
    for how in ("file", "bytes", "twin"):
        buf = bytearray(dw * dh)
        with open(path, "rb") as f:
            if how == "file":
                got = moy_ufiles.cover_image(f, buf, dw, dh, 1024, 600)
            elif how == "bytes":
                got = moy_ufiles.cover_image(io.BytesIO(f.read()), buf, dw, dh, 1024, 600)
            else:
                got = moyimg.cover_stream(f, buf, dw, dh, 1024, 600)
        rows.append(None if got is None else
                    [got[0], got[1], binascii.hexlify(hashlib.sha256(buf).digest()).decode()])
    out.append(rows)
print(json.dumps(out))
'''


@pytest.mark.parametrize("board_model", (False, True))
def test_the_boards_reader_is_the_reference(tmp_path, board_model):
    """`moy_ufiles.cover_image` on the desktop MicroPython, over a file and a
    BytesIO through the stream protocol, and the reference run there too."""
    import hashlib
    import subprocess

    from tests.unix_mp import require_unix_mp
    exe = require_unix_mp("moy_ufiles", board_model=board_model)
    pix = _art(1024, 600)
    good = _blob(1024, 600, pix)
    blobs = [good, _blob(512, 300, _art(512, 300), 15), _blob(1025, 600, _art(1025, 600)),
             good[:-3], good.replace(b'"data": "', b'"data": "\\/')]
    jobs = []
    for i, b in enumerate(blobs):
        p = tmp_path / ("p%d.moyimg" % i)
        p.write_bytes(b)
        for dw, dh in ((320, 240), (480, 320)):
            jobs.append((str(p), dw, dh))
    script = (MP_DRIVER.replace("@RUNTIME@", repr(os.path.join(ROOT, "runtime")))
              .replace("@JOBS@", repr(json.dumps(jobs))))
    run = subprocess.run([exe, "-X", "heapsize=24M", "-c", script], capture_output=True,
                         text=True, timeout=600)
    assert run.returncode == 0, (run.stdout + run.stderr)[-3000:]
    got = json.loads(run.stdout.splitlines()[-1])
    for (path, dw, dh), rows in zip(jobs, got):
        buf = bytearray(dw * dh)
        with open(path, "rb") as f:
            want = moyimg.cover_stream(f, buf, dw, dh, 1024, 600)
        want = None if want is None else [want[0], want[1], hashlib.sha256(buf).hexdigest()]
        assert rows == [want, want, want], (path, dw, dh)
    assert got[0][0] is not None and got[-1][0] is None

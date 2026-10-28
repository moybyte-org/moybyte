"""The moyimg-v1 decoder in C (native/moy_store/moy_img.c), the reader a
VM-free run takes a cart's images with, held to runtime/moyimg.py's
`decode_moyimg` -- every image this repo ships, pictures of every shape and
window the writers make, and the ways a blob is not a picture
(docs/kernel_cartpath_2026-10.md §1)."""

import binascii
import glob
import json
import os
import random
import zlib

import pytest

from runtime import moyimg, native_build

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if native_build.cc() is None:
    pytest.skip("no C compiler: the decoder is C", allow_module_level=True)

from runtime import moy_play  # noqa: E402

try:
    moy_play._img_lib()
except ImportError as exc:      # no desktop MicroPython tree: no inflater to build
    pytest.skip(str(exc), allow_module_level=True)


def _same(text):
    want = moyimg.decode_moyimg(text)
    got = moy_play.image_decode(text)
    assert got == want, (text[:120], got and got[:2], want and want[:2])
    return got


def _blob(w, h, pix, wbits=12, level=9, meta=None):
    comp = zlib.compressobj(level, zlib.DEFLATED, wbits)
    z = comp.compress(bytes(pix)) + comp.flush()
    m = {"format": "moyimg-v1", "w": w, "h": h,
         "data": binascii.b2a_base64(z).decode().strip()}
    m.update(meta or {})
    return json.dumps(m)


def test_every_shipped_image_decodes_as_the_reference_does():
    files = sorted(set(glob.glob(os.path.join(ROOT, "**", "*.moyimg"), recursive=True))
                   - set(glob.glob(os.path.join(ROOT, ".build", "**"), recursive=True)))
    files = [f for f in files if "/firmware/" not in f or "/.build/" not in f]
    assert files
    for f in files:
        with open(f) as fh:
            assert _same(fh.read()) is not None, f


def test_pictures_of_every_shape_and_window():
    rnd = random.Random(7)
    for w, h in ((1, 1), (8, 8), (16, 3), (128, 128), (320, 240), (7, 300)):
        for wbits in (9, 12, 15):
            for kind in ("flat", "noise", "stripes"):
                if kind == "flat":
                    pix = bytes([rnd.randrange(64)]) * (w * h)
                elif kind == "noise":
                    pix = bytes(rnd.randrange(64) for _ in range(w * h))
                else:
                    pix = bytes((x // 3) % 64 for x in range(w * h))
                got = _same(_blob(w, h, pix, wbits))
                assert got == (w, h, pix)
    # The writer's own encoder, both tiers' path.
    pix = bytes(rnd.randrange(64) for _ in range(40 * 30))
    assert _same(moyimg.encode_moyimg(40, 30, pix)) == (40, 30, pix)


def test_what_is_not_a_picture_is_refused_as_the_reference_refuses_it():
    pix = bytes(range(64)) * 4                       # 16 x 16
    good = _blob(16, 16, pix)
    m = json.loads(good)
    z = binascii.a2b_base64(m["data"])

    def with_data(raw, **kw):
        d = dict(m, data=binascii.b2a_base64(raw).decode().strip())
        d.update(kw)
        return json.dumps(d)

    cases = [
        good,
        with_data(z[:-1]),                           # truncated
        with_data(z[:-4] + b"\0\0\0\0"),             # bad Adler-32
        with_data(z + b"trailing"),                  # bytes past the stream
        with_data(zlib.compress(pix + b"x")),        # one pixel too many
        with_data(zlib.compress(pix[:-1])),          # one too few
        with_data(zlib.compress(pix), w=17),         # the size is not the picture's
        with_data(zlib.compress(pix), w=0),
        with_data(zlib.compress(pix), h=-16),
        with_data(zlib.compress(pix), w="16"),       # int() of a string
        with_data(zlib.compress(pix), w=16.9),       # int() truncates
        with_data(zlib.compress(pix), w=None),
        with_data(zlib.compress(pix), data=None),
        with_data(zlib.compress(pix), data=[1]),
        with_data(b"not zlib at all"),
        with_data(b""),
        json.dumps(dict(m, data=m["data"][:-2])),    # missing padding
        json.dumps(dict(m, data=m["data"][:-1])),
        json.dumps(dict(m, data="\n".join(m["data"][i:i + 76]
                                          for i in range(0, len(m["data"]), 76)))),
        json.dumps(dict(m, data=m["data"] + "====")),
        json.dumps({k: v for k, v in m.items() if k != "data"}),
        json.dumps([m]),
        good.replace("/", "\\/").replace("A", "\\u0041"),   # escapes json.loads decodes
        good.replace('"data": "', '"data": "\\u00e9\\t'),     # ...to characters it skips
        json.dumps(dict(m, data=m["data"] + "=\u00e9")),          # past the padding, too
        good[:-1],
        good + " ",
        "",
        "null",
    ]
    for text in cases:
        _same(text)


def test_a_buffer_short_of_the_picture_is_named():
    lib = moy_play._img_lib()
    import ctypes
    text = _blob(8, 8, bytes(64)).encode()
    w, h = ctypes.c_uint32(), ctypes.c_uint32()
    out = ctypes.create_string_buffer(64)            # w*h, not w*h + 1
    assert lib.moy_img_host_decode(text, len(text), out, 64, ctypes.byref(w),
                                   ctypes.byref(h)) == -2
    out = ctypes.create_string_buffer(65)
    assert lib.moy_img_host_decode(text, len(text), out, 65, ctypes.byref(w),
                                   ctypes.byref(h)) == 0

"""native/moy_ui's NUL scan (`moy_ui_nul`, a word at a time) and the field
count over it, against Python's own find, for a NUL at every byte of a word,
every alignment of the start and every length -- the real C, through the
host's one library."""

import ctypes
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from runtime import glass_binding  # noqa: E402


def _fns():
    lib = glass_binding._lib()
    nul = lib.moy_ui_nul
    nul.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    nul.restype = ctypes.c_void_p
    fields = lib.moy_ui_fields
    fields.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    fields.restype = ctypes.c_int
    return nul, fields


def _want_fields(b):
    return 0 if not b else b.count(b"\0") + (0 if b.endswith(b"\0") else 1)


def test_the_first_nul_and_the_fields_whatever_the_alignment():
    nul, fields = _fns()
    # bytes either side of a NUL that look like one to a careless word test
    fill = bytes([0x80, 0x01, 0xff, 0x7f, 0x81, 0x41, 0x00 + 1, 0xfe])
    for start in range(4):
        for n in range(0, 21):
            for z in [None] + list(range(n)):
                body = bytearray(fill[i % len(fill)] for i in range(n))
                if z is not None:
                    body[z] = 0
                buf = ctypes.create_string_buffer(bytes(start) + bytes(body) + b"\x00" * 8)
                base = ctypes.addressof(buf) + start
                got = nul(base, base + n) - base
                assert got == (n if z is None else z), (start, n, z)
                assert fields(base, n) == _want_fields(bytes(body)), (start, n, z)


def test_fields_of_a_table_of_cells():
    _nul, fields = _fns()
    cells = b"\0".join(b"cart %d\x1fTitle %d\x1fgame" % (i, i) for i in range(80))
    buf = ctypes.create_string_buffer(cells)
    assert fields(ctypes.addressof(buf), len(cells)) == 80
    assert fields(ctypes.addressof(buf), len(cells) + 1) == 80   # a trailing NUL ends the last

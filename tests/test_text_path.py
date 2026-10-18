"""The one UI text path (native/moy_gfx/moy_text.h, docs/kernel_toolkit_2026-10.md
§4, §10 "one text path").

* Text is measured in UTF-8 bytes and a truncation NEVER cuts a sequence: it
  backs up to the last whole codepoint (the fit, both wraps, moy_chrome's
  capped prints), fuzzed with multi-byte labels.
* The panels' wrap is moy_chrome's old `panel_lines`, line for line, on ASCII
  (its transcription below is the oracle), and the toolkit's is the twin's.
* A literal never reaches the raster.
* A host golden pins a non-ASCII heading and wrap.
* The ratchet: the rasterizer's two lanes (petme128's cells, a loaded font's
  rows) entered from moy_text alone, no second wrap, no draw_scaled.
"""

import ctypes
import hashlib
import os
import random
import re

import pytest

import ui_twin as twin
from runtime import chrome, glass_binding, moy_play, ui_draw
from runtime.chrome import theme_colors
from runtime.host_canvas import indices_of
from runtime.host_canvas import make_system_canvas as SystemCanvas
from ws_helpers import boot_on_fixture, fixture_colors

@pytest.fixture(autouse=True)
def _on_the_night_fixture(monkeypatch):
    """These pin the lookless pixel bar and its coordinates: every
    workstation here boots on the `night` fixture (ws_helpers)."""
    boot_on_fixture(monkeypatch)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIECES = ("a", "B", "z", "0", " ", " ", "  ", "\t", "\n", "-", "é", "ü",
          "€", "日", "\U0001f600", "Ж", "word", "longerword")


def _text(r, n):
    return "".join(r.choice(PIECES) for _ in range(n))


class _Line(ctypes.Structure):
    _fields_ = [("off", ctypes.c_uint16), ("len", ctypes.c_uint16),
                ("collapse", ctypes.c_uint8)]


def _lib():
    lib = glass_binding._lib()
    lib.moy_text_fit.restype = ctypes.c_size_t
    lib.moy_text_fit.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_size_t]
    lib.moy_text_wrap.argtypes = [ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                                  ctypes.c_int]
    lib.moy_text.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint8,
                             ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int,
                             ctypes.c_int, ctypes.c_uint16, ctypes.c_int,
                             ctypes.c_int, ctypes.c_int]
    return lib


def _panel(lib, b, cols):
    n = lib.moy_text_wrap(0, b, len(b), cols, 1, None, 0)
    lines = (_Line * max(1, n))()
    lib.moy_text_wrap(0, b, len(b), cols, 1, lines, n)
    return [b[ln.off:ln.off + ln.len] for ln in lines[:n]]


def _old_panel_lines(text, cols):
    """moy_chrome's panel_lines as it was, transcribed: the oracle on ASCII."""
    out = []
    for para in text.split("\n"):
        line = None
        words = para.split(" ")
        for w in words:
            while len(w) > cols:
                if line:
                    out.append(line)
                    line = None
                out.append(w[:cols])
                w = w[cols:]
            if not line:
                line = w
            elif len(line) + 1 + len(w) <= cols:
                line = line + " " + w
            else:
                out.append(line)
                line = w
        out.append(line or "")
    return out


def test_a_fit_never_cuts_a_codepoint():
    lib = _lib()
    r = random.Random("fit")
    for _ in range(400):
        b = _text(r, r.randint(0, 12)).encode()
        for cells in range(len(b) + 2):
            k = lib.moy_text_fit(b, len(b), cells)
            assert k <= max(cells, 0) or k == len(b) <= cells
            b[:k].decode()                         # whole codepoints only
            if k < len(b):
                # maximal: one more codepoint would not fit
                rest = b[k:].decode()
                assert k + len(rest[0].encode()) > cells


def test_the_toolkit_wrap_is_the_twins_and_cuts_no_codepoint():
    r = random.Random("wrap")
    for _ in range(600):
        t = _text(r, r.randint(0, 20))
        cols = r.randint(1, 14)
        got = ui_draw.wrap_words(t, cols)
        assert got == twin.wrap_words(t, cols), (t, cols)
        for line in got:
            assert len(line.encode()) <= cols, (t, cols, line)


def test_the_panel_wrap_is_panel_lines_on_ascii():
    lib = _lib()
    r = random.Random("panel-ascii")
    pieces = ("a", "bb", "word", "a-very-long-hyphenated-token", " ", "  ", "\n")
    for _ in range(600):
        t = "".join(r.choice(pieces) for _ in range(r.randint(0, 14)))
        cols = r.randint(1, 20)
        got = [x.decode() for x in _panel(lib, t.encode(), cols)]
        assert got == _old_panel_lines(t, cols), (t, cols)


def test_the_panel_wrap_breaks_long_words_at_codepoints():
    lib = _lib()
    r = random.Random("panel-utf8")
    for _ in range(600):
        b = _text(r, r.randint(0, 20)).encode()
        cols = r.randint(1, 12)
        for line in _panel(lib, b, cols):
            s = line.decode()                      # never a cut sequence
            assert len(line) <= cols or len(s) == 1, (b, cols, line)


def test_moy_chromes_capped_prints_cut_no_codepoint():
    try:
        moy_play._clib()
    except RuntimeError:
        pytest.skip("no host Player (a C compiler builds it)")
    title = "Été à Montréal — 日本語のタイトル"
    ops = moy_play.chrome_toast(title, "paint")
    texts = [o[7] for o in ops if o[0] == 3]
    assert any(t and title.startswith(t) and t != title for t in texts), texts


def test_a_literal_never_reaches_the_raster():
    lib = _lib()
    buf = (ctypes.c_uint16 * (64 * 16))()

    class _T(ctypes.Structure):
        _fields_ = [("px", ctypes.c_void_p), ("cap", ctypes.c_size_t),
                    ("dw", ctypes.c_int)] + [(n, ctypes.c_int) for n in (
                        "cam_x", "cam_y", "cx0", "cy0", "cx1", "cy1")]

    t = _T(ctypes.addressof(buf), 64 * 16, 64, 0, 0, 0, 0, 64, 16)
    for source, want in ((0, 16), (1, 16), (2, 16), (3, -1)):
        r = lib.moy_text(ctypes.byref(t), 0, source, b"HI", 2, 0, 0, 0xFFFF, 1, 0, -1)
        assert r == want, source
    assert any(buf), "nothing drew for TABLE/PY/DATA"
    before = bytes(buf)
    lib.moy_text(ctypes.byref(t), 0, 3, b"LIT", 3, 20, 0, 0x1234, 1, 0, -1)
    assert bytes(buf) == before, "a LIT source drew"


# A host golden with non-ASCII text: a heading through the display type, the
# toolkit's wrap and its print. Byte semantics: each non-ASCII byte is a blank
# cell, and every cut lands on a codepoint.
NON_ASCII_GOLDEN = "bfc397099784c84b"


def _non_ascii_frame():
    cv = SystemCanvas(320, 240, font_scale=2)
    th = fixture_colors("machine")
    cv.cls(th["panel"])
    chrome._print_scaled(cv, "Café 日本", 4, 4, th["accent"], 2)
    y = 40
    for line in ui_draw.wrap_words("Ünïcödé läbëls "
                              "wräp by býtes \U0001f600 ok", 9):
        cv.print(line, 4, y, th["ink"])
        y += 18
    return hashlib.sha256(indices_of(cv)).hexdigest()[:16]


def test_a_non_ascii_frame_is_pinned():
    assert _non_ascii_frame() == NON_ASCII_GOLDEN


def test_a_heading_is_one_text_op():
    cv = SystemCanvas(320, 240, font_scale=2)
    cv.gate_counts_reset()
    chrome._print_scaled(cv, "LIBRARY", 4, 4, 7, 2)
    assert cv.gate_counts()[:2] == (0, 1)


# --- the ratchet ------------------------------------------------------------------

def _sources(dirs, exts):
    for d in dirs:
        for base, _sub, files in os.walk(os.path.join(ROOT, d)):
            for f in files:
                if f.endswith(exts):
                    p = os.path.join(base, f)
                    with open(p, encoding="utf-8", errors="replace") as fh:
                        yield os.path.relpath(p, ROOT), fh.read()


def test_one_rasterizer_entry_and_no_second_wrap():
    calls = []
    for rel, src in _sources(("native", "runtime"), (".c", ".h", ".py")):
        src = re.sub(r"//[^\n]*|/\*.*?\*/", "", src, flags=re.S)
        for m in re.finditer(r"\b(mg_text_raw|mg_text_font)\s*\(", src):
            calls.append((m.group(1), rel))
    # each lane's declaration, its definition, and moy_text's one call
    assert sorted(calls) == sorted(
        (lane, rel) for lane in ("mg_text_font", "mg_text_raw")
        for rel in ("native/moy_gfx/moy_gfx_kernels.c", "native/moy_gfx/moy_gfx_kernels.h",
                    "native/moy_gfx/moy_text.c")), calls
    for rel, src in _sources(("native", "runtime", "device"), (".c", ".h", ".py")):
        assert "draw_scaled" not in src, rel
    chrome_c = open(os.path.join(ROOT, "native/moy_play/moy_chrome.c")).read()
    body = chrome_c[chrome_c.index("static void panel_lines("):]
    body = body[:body.index("\n}\n")]
    assert "moy_text_wrap" in body and "memcpy" not in body
    ui_src = open(os.path.join(ROOT, "runtime/ui_draw.py")).read()
    body = ui_src[ui_src.index("def wrap_words("):]
    body = body[:body.index("\ndef ")]
    assert "_C.wrap(" in body and "split(" not in body

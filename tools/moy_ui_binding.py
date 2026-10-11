# Map (grep -n a name to jump there):
#   binding          the module: moy_ui's names over the glass's host library
"""The toolkit's C (native/moy_ui) on the CPython host, by ctypes: the module
`moy_ui` as the boards and the browser import it, name for name. Its C is
compiled into the glass's host library (runtime/glass_binding.py), because
its primitives draw into the canvas rows that library holds; runtime/__init__
registers this as `moy_ui`, so the host's toolkit is the C every image runs.
"""

import ctypes
import os
import re
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.dirname(HERE) not in sys.path:
    sys.path.insert(0, os.path.dirname(HERE))

from runtime import glass_binding  # noqa: E402

c = ctypes
_I = c.c_int
_P = c.c_char_p
_Z = c.c_size_t
_I16x3 = c.c_int16 * 3
OK, BAD, STALE, NOMEM = range(4)

_SIGS = (
    ("moy_ui_kind", [_P, _Z], _I),
    ("moy_ui_kind_known", [_P, _Z], _I),
    ("moy_ui_button_kind", [_P, _Z], _I),
    ("moy_ui_state", [_P, _Z], _I),
    ("moy_ui_skin_use", [_P, _Z], _I),
    ("moy_ui_skin", [], _I),
    ("moy_ui_gen", [], c.c_uint32),
    ("moy_ui_look_set", [_I, c.c_void_p], _I),
    ("moy_ui_colors", [_I, _I, _I, c.POINTER(c.c_int16)], _I),
    ("moy_ui_metrics", [_I, c.POINTER(_I)], c.POINTER(c.c_int16)),
    ("moy_text_width", [c.c_uint32, _P, _Z, _I], _I),
    ("moy_ui_glyph_id", [_P, _Z], _I),
    ("moy_ui_row_glyph", [c.c_uint32, _I, _I, _I, _I, _I, _I, _I], _I),
    ("moy_text_wrap", [c.c_uint32, _P, _Z, _I, _I, c.c_void_p, _I], _I),
)


class _Box(c.Structure):
    _fields_ = [("x", c.c_int16), ("y", c.c_int16), ("w", c.c_int16), ("h", c.c_int16)]


class _Span(c.Structure):
    _fields_ = [("p", c.c_void_p), ("n", c.c_uint16)]


class _Sink(c.Structure):
    _fields_ = [("kind", c.c_uint8), ("fs", c.c_uint8), ("look", c.c_uint8),
                ("dst", c.c_void_p)]


_I16 = c.c_int16


class _Row(c.Structure):
    _fields_ = [("label", _Span), ("value", _Span)] + [
        (n, _I16) for n in ("glyph", "glyph_size", "glyph_ink", "value_ink", "pad",
                            "pad_right", "text_dy", "scale", "fs")] + [
        ("colors", _I16 * 3)]


class _Cell(c.Structure):
    _fields_ = [("label", _Span)] + [
        (n, _I16) for n in ("glyph", "pad", "caption_h", "band_fill", "band_ink",
                            "text_dx", "text_dy", "fs")] + [("colors", _I16 * 3)]


DEFAULT, FROM_STYLE = -32768, -2

_DRAW_SIGS = (
    ("moy_ui_sink_row", [c.POINTER(_Sink), c.c_uint32, _I], _I),
    ("moy_ui_calls", [], c.c_uint32),
    ("moy_ui_row", [c.POINTER(_Sink), _Box, _I, _I, _I, c.POINTER(_Row), _I], _I),
    ("moy_ui_cell", [c.POINTER(_Sink), _Box, _I, _I, _I, c.POINTER(_Cell), _I,
                     c.POINTER(_Box)], _I),
    ("moy_ui_label", [c.POINTER(_Sink), _Box, _Span, _I, _I], _I),
    ("moy_ui_bar_rect", [_Box, _I, _I, _I, _I, _I, c.POINTER(_Box)], _I),
    ("moy_ui_scrollbar", [c.POINTER(_Sink), _Box, _I, _I, _I, _I, _I], _I),
    ("moy_ui_scroll_cues", [c.POINTER(_Sink), _I, _I, _I, _I, _I, _I, _I], _I),
    ("moy_ui_fill_uncovered", [c.POINTER(_Sink), _Box, _Box, _I], _I),
    ("moy_ui_list", [c.POINTER(_Sink), _Box, _I, c.c_char_p, _Z, _I, _I, _I, _I, _I,
                     _I], _I),
)


class _Line(c.Structure):
    """moy_text.h's moy_text_line_t."""
    _fields_ = [("off", c.c_uint16), ("len", c.c_uint16), ("collapse", c.c_uint8)]


_SPACE = re.compile(rb"[ \t\n\r\x0b\x0c\x1c-\x1f]+")


def _names(lib, sym, n):
    arr = (c.c_char_p * n).in_dll(lib, sym)
    return tuple(arr[i].decode() for i in range(n))


def _check(rc):
    if rc == NOMEM:
        raise MemoryError("moy_ui: no memory for the looks")
    if rc != OK:
        raise ValueError("moy_ui: bad argument")


def binding():
    """The module, or None where there is no C compiler."""
    try:
        lib = glass_binding._lib()
    except ImportError:
        return None
    for name, args, res in _SIGS + _DRAW_SIGS:
        fn = getattr(lib, name)
        fn.argtypes = args
        fn.restype = res
    counts = {}
    for key in ("TOKENS", "STATES", "KINDS", "COLOR_KINDS", "SKINS", "GLYPHS"):
        counts[key] = c.c_int.in_dll(lib, "moy_ui_n_" + key.lower()).value
    tokens = _names(lib, "moy_ui_token_names", counts["TOKENS"])
    states = _names(lib, "moy_ui_state_names", counts["STATES"])
    kinds = _names(lib, "moy_ui_kind_names", counts["KINDS"])
    skins = _names(lib, "moy_ui_skin_names", counts["SKINS"])
    glyphs = _names(lib, "moy_ui_glyph_names", counts["GLYPHS"])
    m = types.ModuleType("moy_ui")

    def _b(s):
        return s.encode("utf-8") if isinstance(s, str) else bytes(s)

    def kind(name):
        b = _b(name)
        return lib.moy_ui_kind(b, len(b))

    def button_kind(name):
        b = _b(name)
        return lib.moy_ui_button_kind(b, len(b))

    def skin(*name):
        if name:
            b = _b(name[0])
            return skins[lib.moy_ui_skin_use(b, len(b))]
        return skins[lib.moy_ui_skin()]

    def look(slot, row):
        if len(row) != len(tokens):
            raise ValueError("moy_ui: a token row is %d int32" % len(tokens))
        arr = (c.c_int32 * len(tokens))(*row)
        _check(lib.moy_ui_look_set(int(slot), arr))

    def colors(look_, kind_, state):
        out = _I16x3()
        _check(lib.moy_ui_colors(int(look_), int(kind_), int(state), out))
        return tuple(None if v < 0 else v for v in out)

    def metrics(name):
        b = _b(name)
        k = lib.moy_ui_kind_known(b, len(b))
        n = _I()
        p = lib.moy_ui_metrics(k, c.byref(n))
        if not p:
            return None
        return tuple(p[i] for i in range(n.value))

    def text_width(text, scale=1):
        b = _b(text)
        return lib.moy_text_width(0, b, len(b), int(scale))

    def wrap(text, cols):
        b = _b(text)
        n = lib.moy_text_wrap(0, b, len(b), int(cols), 0, None, 0)
        lines = (_Line * max(1, n))()
        lib.moy_text_wrap(0, b, len(b), int(cols), 0, lines, n)
        out = []
        for ln in lines[:n]:
            seg = b[ln.off:ln.off + ln.len]
            if ln.collapse:
                seg = _SPACE.sub(b" ", seg).strip(b" ")
            out.append(seg.decode("utf-8"))
        return out

    def glyph_id(name):
        b = _b(name)
        return lib.moy_ui_glyph_id(b, len(b))

    def glyph(canvas, g, x, y, w, h, ci, scale):
        if lib.moy_ui_row_glyph(int(canvas), int(g), int(x), int(y), int(w), int(h),
                                int(ci), int(scale)) == STALE:
            raise ValueError("moy_ui: stale canvas")

    def sink(canvas, look):
        s = _Sink()
        rc = lib.moy_ui_sink_row(c.byref(s), int(canvas), int(look))
        if rc == STALE:
            raise ValueError("moy_ui: stale canvas")
        _check(rc)
        return s

    def span(text):
        if text is None:
            return _Span(None, 0), None
        b = _b(text)
        buf = c.create_string_buffer(b, len(b))
        return _Span(c.addressof(buf), len(b)), buf

    def opt(v, none):
        return none if v is None else int(v)

    def colors3(cols, out):
        for i in range(3):
            out[i] = FROM_STYLE if cols is None else opt(cols[i], -1)

    def box(x, y, w, h):
        return _Box(int(x), int(y), int(w), int(h))

    def row(canvas, look, x, y, w, h, kind, flags, interact, label, value, glyph,
            glyph_size, glyph_ink, value_ink, pad, pad_right, text_dy, scale, fs,
            colors, phase):
        s = sink(canvas, look)
        r = _Row()
        r.label, keep1 = span(label)
        r.value, keep2 = span(value)
        r.glyph = int(glyph)
        r.glyph_size = opt(glyph_size, DEFAULT)
        r.glyph_ink = opt(glyph_ink, FROM_STYLE)
        r.value_ink = opt(value_ink, FROM_STYLE)
        r.pad, r.pad_right = opt(pad, DEFAULT), opt(pad_right, DEFAULT)
        r.text_dy = opt(text_dy, DEFAULT)
        r.scale, r.fs = int(scale), int(fs)
        colors3(colors, r.colors)
        _check(lib.moy_ui_row(c.byref(s), box(x, y, w, h), int(kind), int(flags),
                              int(interact), c.byref(r), int(phase)))

    def cell(canvas, look, x, y, w, h, kind, flags, interact, label, glyph, pad,
             caption_h, band_fill, band_ink, text_dx, text_dy, fs, colors, phase):
        s = sink(canvas, look)
        k = _Cell()
        k.label, keep = span(label)
        k.glyph = int(glyph)
        k.pad, k.caption_h = opt(pad, DEFAULT), opt(caption_h, DEFAULT)
        k.band_fill, k.band_ink = opt(band_fill, FROM_STYLE), opt(band_ink, FROM_STYLE)
        k.text_dx, k.text_dy = opt(text_dx, DEFAULT), opt(text_dy, DEFAULT)
        k.fs = int(fs)
        colors3(colors, k.colors)
        art = _Box()
        _check(lib.moy_ui_cell(c.byref(s), box(x, y, w, h), int(kind), int(flags),
                               int(interact), c.byref(k), int(phase), c.byref(art)))
        return (art.x, art.y, art.w, art.h)

    def label(canvas, look, x, y, w, h, text, ink, align):
        sp, keep = span(text)
        return lib.moy_ui_label(c.byref(sink(canvas, look)), box(x, y, w, h), sp,
                                int(ink), int(align))

    def bar_rect(x, y, w, h, content, offset, horizontal, fs, bar_w):
        out = _Box()
        if not lib.moy_ui_bar_rect(box(x, y, w, h), int(content), int(offset),
                                   1 if horizontal else 0, int(fs), int(bar_w),
                                   c.byref(out)):
            return None
        return (out.x, out.y, out.w, out.h)

    def scrollbar(canvas, look, x, y, w, h, content, offset, horizontal, fs, bar_w):
        _check(lib.moy_ui_scrollbar(c.byref(sink(canvas, look)), box(x, y, w, h),
                                    int(content), int(offset), 1 if horizontal else 0,
                                    int(fs), int(bar_w)))

    def scroll_cues(canvas, ux, uy, dx, dy, can_up, can_dn, ci):
        _check(lib.moy_ui_scroll_cues(c.byref(sink(canvas, 0)), int(ux), int(uy),
                                      int(dx), int(dy), 1 if can_up else 0,
                                      1 if can_dn else 0, int(ci)))

    def fill_uncovered(canvas, ix, iy, iw, ih, ox, oy, ow, oh, ci):
        _check(lib.moy_ui_fill_uncovered(c.byref(sink(canvas, 0)), box(ix, iy, iw, ih),
                                         box(ox, oy, ow, oh), int(ci)))

    def list_(canvas, look, x, y, w, h, kind, items, count, row_h, offset, sel, fs,
              bar_w):
        b = bytes(items)
        _check(lib.moy_ui_list(c.byref(sink(canvas, look)), box(x, y, w, h), int(kind),
                               b, len(b), int(count), int(row_h), int(offset), int(sel),
                               int(fs), int(bar_w)))

    m.calls = lambda: lib.moy_ui_calls()
    m.row = row
    m.cell = cell
    m.label = label
    m.bar_rect = bar_rect
    m.scrollbar = scrollbar
    m.scroll_cues = scroll_cues
    m.fill_uncovered = fill_uncovered
    m.list = list_
    m.glyph_names = lambda: glyphs
    m.glyph_id = glyph_id
    m.glyph = glyph
    m.text_width = text_width
    m.wrap = wrap
    m.tokens = lambda: tokens
    m.states = lambda: states
    m.kinds = lambda: kinds[:counts["COLOR_KINDS"]]
    m.metric_kinds = lambda: tuple(k for k in kinds if metrics(k) is not None)
    m.kind = kind
    m.button_kind = button_kind
    m.skins = lambda: skins
    m.skin = skin
    m.gen = lambda: lib.moy_ui_gen()
    m.look = look
    m.colors = colors
    m.metrics = metrics
    m.lib = lib
    return m

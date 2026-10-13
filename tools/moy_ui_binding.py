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
    ("moy_theme_resolve", [_P, _Z, _I, c.c_void_p, c.POINTER(c.c_uint32)], _I),
    ("moy_theme_install", [_P, _Z, c.POINTER(c.c_uint8)], _I),
    ("moy_theme_shape", [_P, _Z, c.c_void_p], _I),
    ("moy_theme_surface", [_P, _Z, c.c_void_p, c.POINTER(c.c_uint8)], _I),
    ("moy_ui_shape_use", [c.c_void_p], _I),
    ("moy_ui_shape", [], c.c_void_p),
    ("moy_ui_pattern_unreadable", [_I, c.c_void_p, c.c_void_p, _I], _I),
    ("moy_theme_installed", [], c.c_char_p),
    ("moy_ui_look_set", [_I, c.c_void_p], _I),
    ("moy_ui_colors", [_I, _I, _I, c.POINTER(c.c_int16)], _I),
    ("moy_ui_metrics", [_I, c.POINTER(_I)], c.POINTER(c.c_int16)),
    ("moy_text_width", [c.c_uint32, _P, _Z, _I], _I),
    ("moy_font_load", [_P, _Z, c.POINTER(c.c_uint32)], _I),
    ("moy_font_free", [c.c_uint32], _I),
    ("moy_font_info", [c.c_uint32, c.c_void_p], _I),
    ("moy_theme_fonts", [_P, _Z, c.c_void_p], _I),
    ("moy_ui_glyph_id", [_P, _Z], _I),
    ("moy_text_wrap", [c.c_uint32, _P, _Z, _I, _I, c.c_void_p, _I], _I),
    ("moy_str", [_I], c.c_char_p),
    ("moy_str_id", [_P, _Z], _I),
    ("moy_str_set", [_I, c.c_char_p, _Z], _I),
    ("moy_str_reset", [], None),
)


class _Box(c.Structure):
    _fields_ = [("x", c.c_int16), ("y", c.c_int16), ("w", c.c_int16), ("h", c.c_int16)]


class _Surface(c.Structure):
    _fields_ = [("edge", c.c_uint8), ("fill", c.c_uint8), ("flags", c.c_uint8),
                ("a", c.c_int8), ("b", c.c_int8), ("line", c.c_int8)]


def _shape_type(metrics, parts, states, stids):
    class _Shape(c.Structure):
        _fields_ = [("metric", c.c_int8 * metrics), ("n_surf", c.c_uint8),
                    ("n_fx", c.c_uint8),
                    ("effect", ((c.c_uint8 * 2) * states) * parts),
                    ("stid", (c.c_uint8 * states) * parts),
                    ("surf", _Surface * stids),
                    ("frame", c.c_uint8 * 6),
                    ("name", c.c_char * 25)]
    return _Shape


class _Span(c.Structure):
    _fields_ = [("p", c.c_void_p), ("n", c.c_uint16)]


class _FrameButton(c.Structure):
    _fields_ = [("box", _Box), ("id", c.c_uint8), ("right", c.c_uint8)]


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


class _Button(c.Structure):
    _fields_ = [("label", _Span), ("glyph", _I16), ("fs", _I16), ("colors", _I16 * 3)]


_R4 = c.c_int32 * 4
DEFAULT, FROM_STYLE = -32768, -2

_DRAW_SIGS = (
    ("moy_ui_sink_row", [c.POINTER(_Sink), c.c_uint32, _I], _I),
    ("moy_ui_sink_glyph", [c.POINTER(_Sink), _I, _I, _I, _I, _I, _I, _I], _I),
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
    ("moy_ui_button", [c.POINTER(_Sink), _Box, _I, _I, _I, c.POINTER(_Button), _I], _I),
    ("moy_ui_button_x", [_Box, _I, _Span, _I], _I),
    ("moy_ui_chip", [c.POINTER(_Sink), _Box, _I, _I, _I, c.POINTER(_Button), _I], _I),
    ("moy_ui_tab_rects", [_Box, c.c_char_p, _Z, _I, _I, _I, c.POINTER(_Box),
                          c.POINTER(_I)], _I),
    ("moy_ui_tab", [c.POINTER(_Sink), _Box, _I, _I, _Span, _I, _I, _I], _I),
    ("moy_ui_status", [c.POINTER(_Sink), _Box, c.c_char_p, _Z, _I, _I], _I),
    ("moy_ui_game_btn", [c.POINTER(_Sink), _Box, _Span, _I, _I], _I),
    ("moy_ui_game_icon_btn", [c.POINTER(_Sink), _Box, _I, _Span, _I, _I, _I], _I),
    ("moy_ui_mini_btn", [c.POINTER(_Sink), _Box, _Span, _I, _I, _I], _I),
    ("moy_ui_toolbar", [c.POINTER(_Sink), _Box], _I),
    ("moy_ui_paint", [c.POINTER(_Sink), _Box, _I, _I, c.POINTER(_I)], _I),
    ("moy_ui_frame", [c.POINTER(_Sink), _Box, _I, _Span, _I, _I, _I], _I),
    ("moy_ui_frame_buttons", [_Box, _I, _Span, _I, _I, c.POINTER(_FrameButton)], _I),
    ("moy_ui_frame_band", [c.POINTER(_Sink), _Box, _I], _I),
    ("moy_ui_dialog", [c.POINTER(_Sink), _Box, _I, _I], _I),
    ("moy_ui_text_field", [c.POINTER(_Sink), _Box, _Span, _Span, c.c_void_p, _I], _I),
    ("moy_ui_panel", [c.POINTER(_Sink), _Box, _Span, _I, c.POINTER(_Box)], _I),
    ("moy_ui_focus_ring", [c.POINTER(_Sink), _Box, _I], _I),
    ("moy_ui_inset32", [_R4, c.c_int32, c.c_int32, _R4], None),
    ("moy_ui_cut", [_R4, _I, c.c_int32, _R4, _R4], None),
    ("moy_ui_split", [_R4, _I, c.c_int32, _I, c.c_void_p, _I], _I),
    ("moy_ui_row_content", [_R4, _I, c.c_int32, c.c_int32, _I, _I, c.c_int32, _R4], None),
    ("moy_ui_cell_art", [_R4, _I, c.c_int32, c.c_int32, _R4], None),
    ("moy_ui_hits_clear", [c.c_void_p], None),
    ("moy_ui_hits_add", [c.c_void_p, _Z, c.c_int32, c.c_int32, c.c_int32, c.c_int32,
                         c.c_int32], _I),
    ("moy_ui_hits_at", [c.c_void_p, c.c_int32, c.c_int32], c.c_int32),
    ("moy_ui_hits_pump", [c.c_void_p, c.c_int32, c.c_int32, _I, _I, c.c_void_p], None),
    ("moy_ui_scroll_stop", [c.c_void_p], None),
    ("moy_ui_scroll_drag_start", [c.c_void_p, c.c_int32], None),
    ("moy_ui_scroll_drag_move", [c.c_void_p, c.c_int32, c.c_float, c.c_void_p, c.c_int32,
                                 c.c_int32], _I),
    ("moy_ui_scroll_drag_end", [c.c_void_p, c.c_int32, c.c_int32, c.c_int32], None),
    ("moy_ui_scroll_tick", [c.c_void_p, c.c_float, c.c_void_p, c.c_int32, c.c_int32], _I),
    ("moy_ui_dragtap_frame", [c.c_void_p, c.c_void_p, _R4, _I, c.c_int32, c.c_int32, _I, _I,
                              c.c_int32, c.c_float, c.c_void_p, c.c_int32, c.c_void_p,
                              c.c_void_p], _I),
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
    for key in ("TOKENS", "STATES", "KINDS", "COLOR_KINDS", "GLYPHS"):
        counts[key] = c.c_int.in_dll(lib, "moy_ui_n_" + key.lower()).value
    tokens = _names(lib, "moy_ui_token_names", counts["TOKENS"])
    states = _names(lib, "moy_ui_state_names", counts["STATES"])
    kinds = _names(lib, "moy_ui_kind_names", counts["KINDS"])
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

    def text_width(text, scale=1, font=0):
        b = _b(text)
        return lib.moy_text_width(int(font) & 0xFFFFFFFF, b, len(b), int(scale))

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

    def sink(cv, look):
        """The sink of canvas `cv` (its `_ui`: the CANVAS handle and the sprite
        queue), once a pending sprite batch has landed."""
        h, queue = cv._ui
        if queue[0] > 4:
            cv.flush_batch()
        s = _Sink()
        rc = lib.moy_ui_sink_row(c.byref(s), int(h), 0 if look is None else int(look))
        if rc == STALE:
            raise ValueError("moy_ui: stale canvas")
        _check(rc)
        return s

    def num(v):
        return int(v)

    def box_of(r):
        return _Box(int(r[0]), int(r[1]), int(r[2]), int(r[3]))

    def r4(r):
        return _R4(int(r[0]), int(r[1]), int(r[2]), int(r[3]))

    def span(text):
        """Text as the C takes it: a str as it is, anything else as str()
        makes it, None as nothing."""
        if text is None:
            return _Span(None, 0), None
        b = _b(text if isinstance(text, (str, bytes)) else str(text))
        buf = c.create_string_buffer(b, len(b))
        return _Span(c.addressof(buf), len(b)), buf

    def span_if(text):
        return span(text if text else "")

    def opt(v, none):
        return none if v is None else int(v)

    def col(v, none):
        return none if v is None else int(v) & 63

    def colors3(cols, out):
        for i in range(3):
            out[i] = FROM_STYLE if cols is None else col(cols[i], -1)

    def fs_or(v, s):
        return s.fs if v is None else int(v)

    def glyph(cv, g, rect, ci, scale):
        s = sink(cv, None)
        b = box_of(rect)
        _check(lib.moy_ui_sink_glyph(c.byref(s), int(g), b.x, b.y, b.w, b.h, int(ci),
                                     fs_or(scale, s)))

    def row(cv, look, rect, kind, flags, interact, label, value, glyph_, glyph_size,
            glyph_ink, value_ink, pad, pad_right, text_dy, scale, fs, colors, phase):
        s = sink(cv, look)
        r = _Row()
        r.label, keep1 = span_if(label)
        r.value, keep2 = span(value)
        r.glyph = int(glyph_)
        r.glyph_size = opt(glyph_size, DEFAULT)
        r.glyph_ink = col(glyph_ink, FROM_STYLE)
        r.value_ink = col(value_ink, FROM_STYLE)
        r.pad, r.pad_right = opt(pad, DEFAULT), opt(pad_right, DEFAULT)
        r.text_dy = opt(text_dy, DEFAULT)
        r.scale, r.fs = int(scale), fs_or(fs, s)
        colors3(colors, r.colors)
        _check(lib.moy_ui_row(c.byref(s), box_of(rect), int(kind), int(flags),
                              int(interact), c.byref(r), int(phase)))

    def cell(cv, look, rect, kind, flags, interact, label, glyph_, pad, caption_h,
             band_fill, band_ink, text_dx, text_dy, fs, colors, phase):
        s = sink(cv, look)
        k = _Cell()
        k.label, keep = span(label)
        k.glyph = int(glyph_)
        k.pad, k.caption_h = opt(pad, DEFAULT), opt(caption_h, DEFAULT)
        k.band_fill, k.band_ink = col(band_fill, FROM_STYLE), col(band_ink, FROM_STYLE)
        k.text_dx, k.text_dy = opt(text_dx, DEFAULT), opt(text_dy, DEFAULT)
        k.fs = fs_or(fs, s)
        colors3(colors, k.colors)
        art = _Box()
        _check(lib.moy_ui_cell(c.byref(s), box_of(rect), int(kind), int(flags),
                               int(interact), c.byref(k), int(phase), c.byref(art)))
        return (art.x, art.y, art.w, art.h)

    def label(cv, rect, text, ink, align):
        sp, keep = span_if(text)
        return lib.moy_ui_label(c.byref(sink(cv, None)), box_of(rect), sp, int(ink),
                                int(align))

    def bar_rect(view, content, offset, horizontal, fs, bar_w):
        out = _Box()
        if not lib.moy_ui_bar_rect(box_of(view), int(content), int(offset),
                                   1 if horizontal else 0, int(fs), int(bar_w),
                                   c.byref(out)):
            return None
        return (out.x, out.y, out.w, out.h)

    def scrollbar(cv, look, view, content, offset, horizontal, bar_w):
        s = sink(cv, look)
        _check(lib.moy_ui_scrollbar(c.byref(s), box_of(view), int(content), int(offset),
                                    1 if horizontal else 0, s.fs, int(bar_w)))

    def scroll_cues(cv, up, dn, can_up, can_dn, ci):
        if not (can_up or can_dn):
            return
        _check(lib.moy_ui_scroll_cues(c.byref(sink(cv, None)), int(up[0]), int(up[1]),
                                      int(dn[0]), int(dn[1]), 1 if can_up else 0,
                                      1 if can_dn else 0, int(ci)))

    def fill_uncovered(cv, inner, outer, ci):
        _check(lib.moy_ui_fill_uncovered(c.byref(sink(cv, None)), box_of(inner),
                                         box_of(outer), int(ci)))

    def list_(cv, look, rect, kind, items, count, row_h, offset, sel, bar_w):
        s = sink(cv, look)
        b = bytes(items)
        _check(lib.moy_ui_list(c.byref(s), box_of(rect), int(kind), b, len(b), int(count),
                               int(row_h), int(offset), int(sel), s.fs, int(bar_w)))

    def _btn(label, glyph_, fs, colors=None):
        k = _Button()
        k.label, keep = span(label)
        k.glyph, k.fs = int(glyph_), int(fs)
        colors3(colors, k.colors)
        return k, keep

    def button(cv, look, rect, kind, flags, interact, label, glyph_, phase):
        s = sink(cv, look)
        k, keep = _btn(label, glyph_, s.fs)
        _check(lib.moy_ui_button(c.byref(s), box_of(rect), int(kind), int(flags),
                                 int(interact), c.byref(k), int(phase)))

    def button_x(rect, flags, label, fs):
        sp, keep = span(label)
        return lib.moy_ui_button_x(box_of(rect), int(flags), sp, int(fs))

    def chip(cv, look, rect, kind, flags, interact, label, glyph_, fs, colors, phase):
        s = sink(cv, look)
        k, keep = _btn(label, glyph_, fs_or(fs, s), colors)
        _check(lib.moy_ui_chip(c.byref(s), box_of(rect), int(kind), int(flags),
                               int(interact), c.byref(k), int(phase)))

    def tab_rects(rect, labels, count, fs, gap):
        b = bytes(labels)
        out = (_Box * max(1, count))()
        on = _I()
        n = lib.moy_ui_tab_rects(box_of(rect), b, len(b), int(count), int(fs),
                                 opt(gap, DEFAULT), out, c.byref(on))
        return (bool(on.value), [(r.x, r.y, r.w, r.h) for r in out[:n]])

    def tab(cv, look, rect, active, labels_on, label, ink, phase):
        s = sink(cv, look)
        sp, keep = span(label)
        _check(lib.moy_ui_tab(c.byref(s), box_of(rect), 1 if active else 0,
                              1 if labels_on else 0, sp, col(ink, FROM_STYLE), s.fs,
                              int(phase)))

    def status(cv, look, rect, items, count):
        s = sink(cv, look)
        b = bytes(items)
        _check(lib.moy_ui_status(c.byref(s), box_of(rect), b, len(b), int(count), s.fs))

    def game_btn(cv, rect, label, fill):
        s = sink(cv, None)
        sp, keep = span(label)
        _check(lib.moy_ui_game_btn(c.byref(s), box_of(rect), sp, int(fill), s.fs))

    def game_icon_btn(cv, rect, glyph_, label, fill, phase):
        s = sink(cv, None)
        sp, keep = span_if(label)
        _check(lib.moy_ui_game_icon_btn(c.byref(s), box_of(rect), int(glyph_), sp,
                                        int(fill), s.fs, int(phase)))

    def mini_btn(cv, rect, label, fill, label_rect):
        s = sink(cv, None)
        sp, keep = span(label)
        lr = rect if label_rect is None else label_rect
        _check(lib.moy_ui_mini_btn(c.byref(s), box_of(rect), sp, int(fill), int(lr[0]),
                                   int(lr[1])))

    def toolbar(cv, look, rect):
        _check(lib.moy_ui_toolbar(c.byref(sink(cv, look)), box_of(rect)))

    def paint(cv, look, rect, part, state):
        painted = _I()
        _check(lib.moy_ui_paint(c.byref(sink(cv, look)), box_of(rect), int(part), int(state),
                                c.byref(painted)))
        return bool(painted.value)

    def frame(cv, look, rect, title_h, title, active, has_min, fs):
        s = sink(cv, look)
        sp, keep = span_if(title)
        _check(lib.moy_ui_frame(c.byref(s), box_of(rect), int(title_h), sp,
                                1 if active else 0, 1 if has_min else 0, int(fs)))

    def frame_buttons(rect, title_h, title, has_min, fs):
        sp, keep = span_if(title)
        out = (_FrameButton * 3)()
        n = lib.moy_ui_frame_buttons(box_of(rect), int(title_h), sp, 1 if has_min else 0,
                                     int(fs), out)
        return tuple((b.id, b.box.x, b.box.y, b.box.w, b.box.h, b.right) for b in out[:n])

    def frame_band(cv, look, rect, light):
        _check(lib.moy_ui_frame_band(c.byref(sink(cv, look)), box_of(rect),
                                     1 if light else 0))

    def dialog(cv, rect, ring, fill):
        _check(lib.moy_ui_dialog(c.byref(sink(cv, None)), box_of(rect),
                                 col(ring, FROM_STYLE), col(fill, FROM_STYLE)))

    def text_field(cv, rect, text, hint, colors, fs):
        s = sink(cv, None)
        t, k1 = span_if(text)
        p, k2 = span_if(hint)
        cs = None
        if colors is not None:
            cs = (c.c_int16 * 5)(*[col(v, -1) for v in colors])
        _check(lib.moy_ui_text_field(c.byref(s), box_of(rect), t, p,
                                     None if cs is None else c.addressof(cs),
                                     1 if fs is None else int(fs)))

    def panel(cv, look, rect, title, fs):
        s = sink(cv, look)
        sp, keep = span_if(title)
        out = _Box()
        _check(lib.moy_ui_panel(c.byref(s), box_of(rect), sp, fs_or(fs, s),
                                c.byref(out)))
        return (out.x, out.y, out.w, out.h)

    def focus_ring(cv, look, rect, fs):
        s = sink(cv, look)
        _check(lib.moy_ui_focus_ring(c.byref(s), box_of(rect), fs_or(fs, s)))

    def inset(rect, dx, dy):
        out = _R4()
        lib.moy_ui_inset32(r4(rect), int(dx), int(dy), out)
        return tuple(out)

    def cut(rect, side, n):
        band, rest = _R4(), _R4()
        lib.moy_ui_cut(r4(rect), int(side), int(n), band, rest)
        return (tuple(band), tuple(rest))

    def split(rect, n, gap, horizontal):
        out = (_R4 * max(1, n))()
        got = lib.moy_ui_split(r4(rect), int(n), int(gap), 1 if horizontal else 0,
                               c.addressof(out), max(1, n))
        return [tuple(r) for r in out[:got]]

    def row_content(rect, fs, pad, pad_right, icon, glyph_, glyph_size):
        out = _R4()
        lib.moy_ui_row_content(r4(rect), int(fs), opt(pad, DEFAULT),
                               opt(pad_right, DEFAULT), 1 if icon else 0,
                               1 if glyph_ else 0, opt(glyph_size, DEFAULT), out)
        return tuple(out)

    def cell_art(rect, fs, pad, caption_h):
        out = _R4()
        lib.moy_ui_cell_art(r4(rect), int(fs), opt(pad, DEFAULT), int(caption_h), out)
        return tuple(out)

    for f in (row, cell, label, bar_rect, scrollbar, scroll_cues, fill_uncovered,
              button, button_x, chip, tab_rects, tab, status, game_btn, game_icon_btn,
              mini_btn, toolbar, paint, frame, frame_buttons, frame_band, dialog, text_field, panel, focus_ring, inset, cut,
              split, row_content, cell_art):
        setattr(m, f.__name__, f)
    m.list = list_
    # -- interaction over caller-owned buffers --------------------------------

    def _buf(b):
        """A writable caller buffer's address (array or bytearray)."""
        return (c.c_char * (len(b) * b.itemsize if hasattr(b, "itemsize") else len(b))) \
            .from_buffer(b)

    def hits_clear(buf):
        lib.moy_ui_hits_clear(_buf(buf))

    def hits_add(buf, rect, id_):
        b = _buf(buf)
        return lib.moy_ui_hits_add(b, len(b) // 4, int(rect[0]), int(rect[1]),
                                   int(rect[2]), int(rect[3]), int(id_)) == OK

    def hits_at(buf, px, py):
        return lib.moy_ui_hits_at(_buf(buf), int(px), int(py))

    def hits_pump(buf, px, py, down, pointing, pump):
        lib.moy_ui_hits_pump(_buf(buf), int(px), int(py), 1 if down else 0,
                             1 if pointing else 0, _buf(pump))

    def _dt(v):
        return 0.0 if v is None else float(v)

    def scroll_stop(st):
        lib.moy_ui_scroll_stop(_buf(st))

    def scroll_drag_start(st, p):
        lib.moy_ui_scroll_drag_start(_buf(st), int(p))

    def scroll_drag_move(st, p, dt, offset, content, extent):
        off = c.c_int32(int(offset))
        if not lib.moy_ui_scroll_drag_move(_buf(st), int(p), _dt(dt), c.byref(off),
                                           int(content), int(extent)):
            return -1
        return off.value

    def scroll_drag_end(st, offset, content, extent):
        lib.moy_ui_scroll_drag_end(_buf(st), int(offset), int(content), int(extent))

    def scroll_tick(st, dt, offset, content, extent):
        off = c.c_int32(int(offset))
        if not lib.moy_ui_scroll_tick(_buf(st), _dt(dt), c.byref(off), int(content),
                                      int(extent)):
            return -1
        return off.value

    def dragtap(tap, st, view, horizontal, px, py, click, down, slop, dt, offset,
                content, out):
        o = (c.c_int32 * 3)(int(offset), 0, 0)
        tapped = lib.moy_ui_dragtap_frame(
            _buf(tap), _buf(st), r4(view), 1 if horizontal else 0, int(px), int(py),
            1 if click else 0, 1 if down else 0, int(slop), _dt(dt), c.byref(o, 0),
            int(content), c.byref(o, 4), c.byref(o, 8))
        out[0], out[1], out[2] = o[0], o[1], o[2]
        return bool(tapped)

    for f in (hits_clear, hits_add, hits_at, hits_pump, scroll_stop, scroll_drag_start,
              scroll_drag_move, scroll_drag_end, scroll_tick, dragtap):
        setattr(m, f.__name__, f)
    m.hits_cap = lambda: c.c_int.in_dll(lib, "moy_ui_hits_cap").value
    m.calls = lambda: lib.moy_ui_calls()
    m.glyph_names = lambda: glyphs
    m.glyph_id = glyph_id
    m.glyph = glyph

    def s(name):
        b = _b(name)
        i = lib.moy_str_id(b, len(b))
        if i < 0:
            raise KeyError(name)
        return lib.moy_str(i).decode("utf-8")

    def str_set(name, text):
        b = _b(name)
        i = lib.moy_str_id(b, len(b))
        if i < 0:
            raise KeyError(name)
        t = None if text is None else _b(text)
        rc = lib.moy_str_set(i, t, 0 if t is None else len(t))
        if rc == 3:
            raise MemoryError("moy_ui: no room for the pack")
        return rc == 0

    m.s = s
    m.str_set = str_set
    m.str_reset = lambda: lib.moy_str_reset()
    m.text_width = text_width
    m.wrap = wrap
    m.tokens = lambda: tokens
    m.states = lambda: states
    m.kinds = lambda: kinds[:counts["COLOR_KINDS"]]
    m.metric_kinds = lambda: tuple(k for k in kinds if metrics(k) is not None)
    m.kind = kind
    m.button_kind = button_kind
    seeds = _names(lib, "moy_theme_seed_names", c.c_int.in_dll(lib, "moy_ui_n_themes").value)
    m.themes = lambda: seeds
    n_roles = c.c_int.in_dll(lib, "moy_theme_n_roles").value
    roles = _names(lib, "moy_theme_role_names", n_roles)
    metric_n = c.c_int.in_dll(lib, "moy_theme_n_metrics").value
    effect_names = _names(lib, "moy_theme_effect_names",
                          c.c_int.in_dll(lib, "moy_theme_n_effects").value)
    m.theme_roles = lambda: roles

    parts = _names(lib, "moy_theme_part_names", c.c_int.in_dll(lib, "moy_theme_n_parts").value)
    th_states = _names(lib, "moy_theme_state_names",
                       c.c_int.in_dll(lib, "moy_theme_n_states").value)
    Shape = _shape_type(metric_n, len(parts), len(th_states), 64)
    assert c.sizeof(Shape) == c.c_int.in_dll(lib, "moy_ui_shape_size").value
    m.parts = lambda: parts
    m.theme_states = lambda: th_states

    def rec(r):
        return (r.edge, r.fill, r.flags, r.a, r.b, r.line)

    def theme_shape(name):
        b = _b(name)
        sh = Shape()
        lib.moy_theme_shape(b, len(b), c.byref(sh))
        fx, sf = [], []
        for p in range(len(parts)):
            for st in range(len(th_states)):
                e = sh.effect[p][st]
                if e[0]:
                    fx.append((parts[p], th_states[st], effect_names[e[0]], roles[e[1]]))
                if sh.stid[p][st]:
                    sf.append((parts[p], th_states[st], rec(sh.surf[sh.stid[p][st] - 1])))
        return tuple(sh.metric), tuple(fx), tuple(sf)

    def surface(text):
        b = _b(text)
        r = _Surface()
        notes = c.c_uint8()
        if lib.moy_theme_surface(b, len(b), c.byref(r), c.byref(notes)) != 0:
            return None
        return rec(r), notes.value

    def use_shape(name):
        if name is None:
            lib.moy_ui_shape_use(None)
            return 0
        b = _b(name)
        sh = Shape()
        rc = lib.moy_theme_shape(b, len(b), c.byref(sh))
        lib.moy_ui_shape_use(c.byref(sh))
        return rc

    def pattern_unreadable(look):
        bad = ((c.c_uint8 * 2) * 16)()
        n = min(16, lib.moy_ui_pattern_unreadable(look, lib.moy_ui_shape(), bad, 16))
        return [(parts[bad[i][0]], th_states[bad[i][1]]) for i in range(n)]

    m.pattern_unreadable = pattern_unreadable
    m.theme_shape = theme_shape
    m.surface = surface
    m.use_shape = use_shape

    def theme(name, light):
        b = _b(name)
        row = (c.c_int32 * n_roles)()
        bad = c.c_uint32()
        rc = lib.moy_theme_resolve(b, len(b), 1 if light else 0, row, c.byref(bad))
        return tuple(row), bad.value, rc

    def theme_install(src):
        b = bytes(src)
        notes = c.c_uint8()
        rc = lib.moy_theme_install(b, len(b), c.byref(notes))
        return rc, notes.value

    def theme_installed():
        s = lib.moy_theme_installed()
        return None if s is None else s.decode()

    font_roles = _names(lib, "moy_theme_font_roles",
                        c.c_int.in_dll(lib, "moy_theme_n_font_roles").value)

    def theme_fonts(name):
        b = _b(name)
        files = ((c.c_char * 25) * len(font_roles))()
        lib.moy_theme_fonts(b, len(b), files)
        return tuple((r, f.value.decode()) for r, f in zip(font_roles, files) if f.value)

    def font_load(src):
        b = bytes(src)
        fid = c.c_uint32()
        rc = lib.moy_font_load(b, len(b), c.byref(fid))
        return rc, (fid.value if rc == 0 else None)

    def font_free(font):
        return bool(lib.moy_font_free(int(font) & 0xFFFFFFFF))

    def font_info(font):
        out = (c.c_int * 6)()
        if not lib.moy_font_info(int(font) & 0xFFFFFFFF, out):
            return None
        return tuple(out)

    m.theme = theme
    m.theme_install = theme_install
    m.theme_installed = theme_installed
    m.theme_fonts = theme_fonts
    m.font_load = font_load
    m.font_free = font_free
    m.font_info = font_info
    m.look = look
    m.colors = colors
    m.metrics = metrics
    m.lib = lib
    return m

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
    ("moy_theme_probe", [_P, _Z, c.c_void_p, c.POINTER(c.c_uint8)], _I),
    ("moy_theme_probe_end", [], None),
    ("moy_theme_probe_name", [], c.c_char_p),
    ("moy_theme_probe_font", [_I], c.c_char_p),
    ("moy_theme_probe_sheet", [_I], c.c_char_p),
    ("moy_theme_sheets", [_P, _Z, c.c_void_p], _I),
    ("moy_theme_sheet_check", [_P, _Z, _I, c.POINTER(_I)], _I),
    ("moy_theme_sheet_load", [_P, _Z], _I),
    ("moy_theme_sheet_clear", [], None),
    ("moy_theme_sheet_cell", [_I, _I, _I], _I),
    ("moy_theme_uninstall", [], None),
    ("moy_theme_custom", [_P, _Z, _I, _I, c.c_void_p, c.c_void_p, _Z, c.POINTER(_Z)], _I),
    ("moy_ui_look_set", [_I, c.c_void_p], _I),
    ("moy_ui_colors", [_I, _I, _I, c.POINTER(c.c_int16)], _I),
    ("moy_ui_button_style", [_I, _I, _I, _I, c.c_void_p], _I),
    ("moy_ui_buttons_set", [_P, _Z, _I, c.c_void_p], _I),
    ("moy_ui_buttons_clear", [], None),
    ("moy_ui_buttons_app", [_P, _Z], _I),
    ("moy_theme_reads", [c.c_int32, c.c_int32], _I),
    ("moy_ui_metrics", [_I, c.POINTER(_I)], c.POINTER(c.c_int16)),
    ("moy_text_width", [c.c_uint32, _P, _Z, _I], _I),
    ("moy_font_load", [_P, _Z, c.POINTER(c.c_uint32)], _I),
    ("moy_font_free", [c.c_uint32], _I),
    ("moy_font_info", [c.c_uint32, c.c_void_p], _I),
    ("moy_ui_font_use", [_I, c.c_uint32], _I),
    ("moy_ui_font", [_I, c.c_void_p], c.c_uint32),
    ("moy_theme_fonts", [_P, _Z, c.c_void_p], _I),
    ("moy_theme_layout", [_P, _Z, c.c_void_p], _I),
    ("moy_theme_font_file", [_P, _Z, c.c_void_p], c.c_void_p),
    ("moy_text_print_font", [], c.c_uint32),
    ("moy_font_reset", [], None),
    ("moy_text_wire", [_I], None),
    ("moy_text_wire_swapped", [], _I),
    ("moy_text_lanes", [c.c_void_p], None),
    ("moy_text_print_font_use", [c.c_uint32], c.c_int64),
    ("moy_ui_cart_text", [_I], _I),
    ("moy_ui_cmask", [], _I),
    ("moy_canvas_cart", [_I], None),
    ("moy_theme_look", [_P, _Z, _I, _I, c.c_void_p], _I),
    ("moy_theme_backdrop", [_P, _Z, _I, _I, c.c_void_p, _Z], _I),
    ("moy_theme_place", [c.c_void_p, _I, c.c_void_p, c.c_void_p, c.c_void_p], _I),
    ("moy_theme_block_use", [c.c_void_p, _I], None),
    ("moy_theme_rgb565", [c.c_int32], c.c_uint16),
    ("moy_theme_lum_of", [c.c_int32], c.c_int32),
    ("moy_theme_nearest", [c.c_int32], c.c_int32),
    ("moy_ui_print_font", [c.c_uint32], c.c_int64),
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


def _layout_type(bars, slots, slot_cap, widget_cap, text_max):
    class _Bar(c.Structure):
        _fields_ = [("edge", c.c_uint8), ("size", c.c_uint8), ("n", c.c_uint8 * slots),
                    ("w", (c.c_uint8 * slot_cap) * slots),
                    ("text0", c.c_uint8), ("n_text", c.c_uint8)]

    class _Layout(c.Structure):
        _fields_ = [("set", c.c_uint8), ("n_bars", c.c_uint8), ("bar", _Bar * bars),
                    ("n_text", c.c_uint8), ("text", (c.c_char * (text_max + 1)) * widget_cap),
                    ("home", c.c_uint8)]
    return _Layout


class _Span(c.Structure):
    _fields_ = [("p", c.c_void_p), ("n", c.c_uint16)]


class _Depth(c.Structure):
    _fields_ = ([(n, c.c_uint8) for n in ("on", "win_r", "card_r", "key_r", "corners",
                                          "stepped", "shadow", "edges", "frames")]
                + [("shadow_565", c.c_uint16)]
                + [(n, c.c_int16) for n in ("alpha", "blur", "dx", "dy", "back", "line",
                                            "line_back")]
                + [(n, c.c_int32) for n in ("line_c", "line_back_c", "body")])


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
    _fields_ = [("label", _Span), ("glyph", _I16), ("fs", _I16), ("colors", _I16 * 3),
                ("app", _I16)]


class _BtnStyle(c.Structure):
    _fields_ = [("c", _I16 * 3), ("edge_w", c.c_int8), ("shape", c.c_int8),
                ("r", c.c_int8), ("font", c.c_int8), ("bevel", c.c_int8)]


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
    ("moy_ui_label_paint", [c.POINTER(_Sink), _Box, _Span, _I, _I, c.c_void_p], _I),
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
    ("moy_ui_toolbar", [c.POINTER(_Sink), _Box, c.POINTER(_I)], _I),
    ("moy_ui_paint", [c.POINTER(_Sink), _Box, _I, _I, c.POINTER(_I)], _I),
    ("moy_ui_frame", [c.POINTER(_Sink), _Box, _I, _Span, _I, _I, _I], _I),
    ("moy_ui_frame_buttons", [_Box, _I, _Span, _I, _I, c.POINTER(_FrameButton)], _I),
    ("moy_ui_frame_band", [c.POINTER(_Sink), _Box, _I], _I),
    ("moy_ui_frame_icon", [_Box, _I, c.POINTER(_Box)], _I),
    ("moy_ui_depth_use", [c.c_char_p, c.c_size_t, _I], _I),
    ("moy_ui_depth", [], c.c_void_p),
    ("moy_ui_frame_geom", [_I, _I, _I, _I, _I, c.POINTER(_I)], None),
    ("moy_ui_frame_under", [c.POINTER(_Sink), _Box, _I, _I, _I], _I),
    ("moy_ui_cast", [c.POINTER(_Sink), _Box, _I, _I], _I),
    ("moy_ui_focus", [c.POINTER(_Sink), _Box, _I, _I, _I, _I, c.POINTER(_Box),
                      c.POINTER(_I)], _I),
    ("moy_ui_current", [c.POINTER(_Sink), _Box, _I, _I, _I, c.POINTER(_I)], _I),
    ("moy_ui_marks_active", [_I], None),
    ("moy_ui_marks_drawn", [_I], c.c_uint32),
    ("moy_ui_marks", [], c.c_void_p),
    ("moy_ui_mark_reach", [_I], _I),
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
    # the public UI API (native/moy_ui/moy_ui_api.h)
    ("moy_ui_api_size", [], c.c_size_t),
    ("moy_ui_api_ctx", [c.c_void_p, c.c_size_t], c.c_void_p),
    ("moy_ui_over_block", [c.c_void_p, c.c_char_p, c.c_size_t], _I),
    ("moy_ui_api_begin", [c.c_void_p, c.c_void_p, _Box, _I, _I], _I),
    ("moy_ui_api_page", [c.c_void_p, c.POINTER(_Sink), _Span, c.c_void_p, c.POINTER(_Box)],
     None),
    ("moy_ui_api_label", [c.c_void_p, c.POINTER(_Sink), _Span, c.c_void_p], None),
    ("moy_ui_api_button", [c.c_void_p, c.POINTER(_Sink), _Span, _I, c.c_void_p], _I),
    ("moy_ui_api_toggle", [c.c_void_p, c.POINTER(_Sink), _Span, _I, c.c_void_p], _I),
    ("moy_ui_api_slider", [c.c_void_p, c.POINTER(_Sink), _Span, c.c_int32, c.c_int32,
                           c.c_int32, c.c_void_p], c.c_int32),
    ("moy_ui_api_list", [c.c_void_p, c.POINTER(_Sink), _Span, c.c_char_p, _Z, c.c_int32,
                         c.c_void_p], c.c_int32),
    ("moy_ui_api_part", [c.c_void_p, c.POINTER(_Sink), _Span, _I, _I, c.c_void_p,
                         c.c_void_p], None),
    ("moy_ui_api_focus", [c.c_void_p, _Span, c.c_char_p, c.c_size_t], _I),
    ("moy_ui_api_keys_taken", [c.c_void_p], _I),
    ("moy_ui_api_at", [c.c_void_p, _I, _I, _I, _I], None),
    ("moy_ui_api_dup", [c.c_void_p], c.c_char_p),
)


class _Over(c.Structure):
    """moy_ui_over_t (native/moy_ui/moy_ui_api.h)."""
    _fields_ = [("fill", c.c_int32), ("ink", c.c_int32), ("border", c.c_int32),
                ("id", _Span), ("current", c.c_int8), ("disabled", c.c_int8),
                ("nav", c.c_int8), ("mark", c.c_int8)]


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

    def button_style(look_, kind_, state, app):
        bs = _BtnStyle()
        _check(lib.moy_ui_button_style(int(look_), int(kind_), int(state), int(app),
                                       c.byref(bs)))
        return tuple(None if v < 0 else v for v in bs.c) + (
            bs.edge_w, bs.shape, bs.r, bs.font, bs.bevel)

    def buttons_set(app, kind_, leaves):
        b = None if app is None else _b(app)
        arr = (c.c_int32 * len(leaves))(*[int(v) for v in leaves])
        _check(lib.moy_ui_buttons_set(b, 0 if b is None else len(b), int(kind_), arr))

    def buttons_app(name):
        b = _b(name)
        return lib.moy_ui_buttons_app(b, len(b))

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

    def wrap(text, cols, font=None):
        b = _b(text)
        fid, mode = (0, 0) if font is None else (int(font) & 0xFFFFFFFF, 2)
        n = lib.moy_text_wrap(fid, b, len(b), int(cols), mode, None, 0)
        lines = (_Line * max(1, n))()
        lib.moy_text_wrap(fid, b, len(b), int(cols), mode, lines, n)
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
        return none if v is None else int(v) & lib.moy_ui_cmask()

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

    def label(cv, rect, text, ink, align, bg=None, shadow=None):
        sp, keep = span_if(text)
        lp = (c.c_int16 * 5)(-1 if bg is None else int(bg),
                             *((-1, 0, 0, 0) if shadow is None else (int(v) for v in shadow)))
        return lib.moy_ui_label_paint(c.byref(sink(cv, None)), box_of(rect), sp, int(ink),
                                      int(align), c.addressof(lp))

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

    def _btn(label, glyph_, fs, colors=None, app=-1):
        k = _Button()
        k.label, keep = span(label)
        k.glyph, k.fs, k.app = int(glyph_), int(fs), int(app)
        colors3(colors, k.colors)
        return k, keep

    def button(cv, look, rect, kind, flags, interact, label, glyph_, phase, app):
        s = sink(cv, look)
        k, keep = _btn(label, glyph_, s.fs, app=app)
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
        ink = _I()
        _check(lib.moy_ui_toolbar(c.byref(sink(cv, look)), box_of(rect), c.byref(ink)))
        return ink.value

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

    def frame_icon(rect, title_h):
        out = _Box()
        if not lib.moy_ui_frame_icon(box_of(rect), int(title_h), c.byref(out)):
            return None
        return (out.x, out.y, out.w, out.h)

    def frame_geom(w, h, title_h, active, mode):
        out = (_I * 6)()
        lib.moy_ui_frame_geom(int(w), int(h), int(title_h), 1 if active else 0, int(mode), out)
        return tuple(out)

    def frame_under(cv, look, rect, title_h, active, mode):
        _check(lib.moy_ui_frame_under(c.byref(sink(cv, look)), box_of(rect), int(title_h),
                                      1 if active else 0, int(mode)))

    def cast(cv, look, rect, part, mode):
        return bool(lib.moy_ui_cast(c.byref(sink(cv, look)), box_of(rect), int(part),
                                    int(mode)))

    def frame_band(cv, look, rect, light, side):
        _check(lib.moy_ui_frame_band(c.byref(sink(cv, look)), box_of(rect),
                                     1 if light else 0, int(side)))

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

    def focus(cv, look, rect, cls, r, flags, fs):
        s = sink(cv, look)
        out, ink = _Box(), _I()
        _check(lib.moy_ui_focus(c.byref(s), box_of(rect), int(cls), int(r), int(flags),
                                fs_or(fs, s), c.byref(out), c.byref(ink)))
        return (None if ink.value < 0 else ink.value, (out.x, out.y, out.w, out.h))

    def current(cv, look, rect, r, flags, fs):
        s = sink(cv, look)
        ink = _I()
        _check(lib.moy_ui_current(c.byref(s), box_of(rect), int(r), int(flags),
                                  fs_or(fs, s), c.byref(ink)))
        return None if ink.value < 0 else ink.value

    def marks(active, reset):
        if active is not None:
            lib.moy_ui_marks_active(1 if active else 0)
        on, act = c.cast(lib.moy_ui_marks(), c.POINTER(c.c_uint8 * 2)).contents
        return (bool(on), bool(act), lib.moy_ui_marks_drawn(1 if reset else 0),
                lib.moy_ui_mark_reach(1))

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
              mini_btn, toolbar, paint, frame, frame_buttons, frame_band, frame_geom, frame_icon,
              frame_under, cast, dialog, text_field, panel, focus_ring, focus, current,
              marks, inset, cut,
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

    # -- the public UI API -----------------------------------------------------

    def _ctx(ctx):
        b = _buf(ctx)
        p = lib.moy_ui_api_ctx(b, len(b))
        if not p:
            raise ValueError("moy_ui: not a ui context")
        return p, b

    def _over(o):
        """The override block (bytes) as the C's struct, and what keeps it."""
        if o is None:
            return None, None
        raw = c.create_string_buffer(bytes(o), len(o))
        ov = _Over()
        _check(lib.moy_ui_over_block(c.byref(ov), raw, len(o)))
        return c.byref(ov), (ov, raw)

    def api_size():
        return int(lib.moy_ui_api_size())

    def api_begin(ctx, inp, rect, cls, fs):
        p, _k = _ctx(ctx)
        ib = None if inp is None else _buf(inp)
        return bool(lib.moy_ui_api_begin(p, ib, box_of(rect), int(cls), int(fs)))

    def api_page(ctx, cv, look, title, o):
        p, _k = _ctx(ctx)
        s = sink(cv, look)
        t, keep = span(title)
        ov, ok = _over(o)
        out = _Box()
        lib.moy_ui_api_page(p, c.byref(s), t, ov, c.byref(out))
        return (out.x, out.y, out.w, out.h)

    def api_label(ctx, cv, look, text, o):
        p, _k = _ctx(ctx)
        s = sink(cv, look)
        t, keep = span(text)
        ov, ok = _over(o)
        lib.moy_ui_api_label(p, c.byref(s), t, ov)

    def api_button(ctx, cv, look, label, kind_, o):
        p, _k = _ctx(ctx)
        s = sink(cv, look)
        t, keep = span(label)
        ov, ok = _over(o)
        return bool(lib.moy_ui_api_button(p, c.byref(s), t, int(kind_), ov))

    def api_toggle(ctx, cv, look, label, on, o):
        p, _k = _ctx(ctx)
        s = sink(cv, look)
        t, keep = span(label)
        ov, ok = _over(o)
        return bool(lib.moy_ui_api_toggle(p, c.byref(s), t, 1 if on else 0, ov))

    def api_slider(ctx, cv, look, label, v, lo, hi, o):
        p, _k = _ctx(ctx)
        s = sink(cv, look)
        t, keep = span(label)
        ov, ok = _over(o)
        return int(lib.moy_ui_api_slider(p, c.byref(s), t, int(v), int(lo), int(hi), ov))

    def api_list(ctx, cv, look, label, items, cur, o):
        p, _k = _ctx(ctx)
        s = sink(cv, look)
        t, keep = span(label)
        ov, ok = _over(o)
        b = bytes(items)
        return int(lib.moy_ui_api_list(p, c.byref(s), t, b, len(b), int(cur), ov))

    def api_part(ctx, cv, look, id_, w, h, o):
        p, _k = _ctx(ctx)
        s = sink(cv, look)
        t, keep = span(id_)
        ov, ok = _over(o)
        out = (c.c_int32 * 5)()
        lib.moy_ui_api_part(p, c.byref(s), t, int(w), int(h), ov, out)
        return tuple(out)

    def api_focus(ctx, id_=None):
        p, _k = _ctx(ctx)
        if id_ is None:
            t, keep = _Span(None, 0), None
        else:
            t, keep = span(id_)
            if not t.n:
                keep = c.create_string_buffer(b"", 1)
                t = _Span(c.addressof(keep), 0)
        buf = c.create_string_buffer(64)
        n = lib.moy_ui_api_focus(p, t, buf, 64)
        return None if n < 0 else buf.value.decode("utf-8", "replace")

    def api_keys_taken(ctx):
        p, _k = _ctx(ctx)
        return bool(lib.moy_ui_api_keys_taken(p))

    def api_at(ctx, x, y, w, h):
        p, _k = _ctx(ctx)
        lib.moy_ui_api_at(p, int(x), int(y), int(w), int(h))

    def api_dup(ctx):
        p, _k = _ctx(ctx)
        d = lib.moy_ui_api_dup(p)
        return None if d is None else d.decode("utf-8", "replace")

    for f in (api_size, api_begin, api_page, api_label, api_button, api_toggle, api_slider,
              api_list, api_part, api_focus, api_keys_taken, api_at, api_dup):
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
    m.button_style = button_style
    m.buttons_set = buttons_set
    m.buttons_clear = lib.moy_ui_buttons_clear
    m.buttons_app = buttons_app
    m.theme_reads = lambda a, b: bool(lib.moy_theme_reads(int(a), int(b)))
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

    def use_depth(name, light):
        if name is None:
            lib.moy_ui_depth_use(None, 0, 0)
        else:
            b = _b(name)
            lib.moy_ui_depth_use(b, len(b), 1 if light else 0)
        d = _Depth.from_address(lib.moy_ui_depth())
        return (d.on, d.win_r, d.card_r, d.key_r, d.corners, d.stepped, d.shadow, d.edges,
                d.alpha, d.blur, d.dx, d.dy, d.back, d.frames)

    m.use_depth = use_depth

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

    sdims = tuple((c.c_int * 6).in_dll(lib, "moy_theme_sheet_dims"))

    class _CellRec(c.Structure):
        _fields_ = [(n, c.c_uint8) for n in ("part", "state", "sub", "x", "y", "w", "h",
                                             "nine")]

    cell_recs = (_CellRec * sdims[4]).in_dll(lib, "moy_theme_cells")
    cells = tuple((parts[r.part], th_states[r.state], r.sub, r.x, r.y, r.w, r.h, bool(r.nine))
                  for r in cell_recs)

    def theme_sheets(name):
        b = _b(name)
        files = ((c.c_char * 25) * sdims[5])()
        lib.moy_theme_sheets(b, len(b), files)
        return tuple(f.value.decode() if f.value else None for f in files)

    def sheet_check(src, rows):
        b = bytes(src)
        got = _I()
        rc = lib.moy_theme_sheet_check(b, len(b), int(rows), c.byref(got))
        return rc, (got.value if rc == OK else 0)

    def sheet_load(src):
        b = bytes(src)
        return lib.moy_theme_sheet_load(b, len(b))

    def sheet_set():
        return tuple(lib.moy_theme_sheet_cell(parts.index(cl[0]), th_states.index(cl[1]),
                                              cl[2]) == i for i, cl in enumerate(cells))

    w_, sr, ir = sdims[0], sdims[1], sdims[2]
    m.theme_sheets = theme_sheets
    m.sheet_check = sheet_check
    m.sheet_load = sheet_load
    m.sheet_clear = lib.moy_theme_sheet_clear
    m.sheet_cells = lambda: cells
    m.sheet_set = sheet_set
    m.sheet_caps = lambda: (w_, sr, ir, sr * (w_ + 2), ir * (w_ + 2))

    dims = (c.c_int * 7).in_dll(lib, "moy_theme_layout_dims")
    Layout = _layout_type(*dims[:5])
    assert c.sizeof(Layout) == dims[6]
    bar_edges = _names(lib, "moy_theme_bar_edges", dims[0])
    widgets = _names(lib, "moy_theme_widget_names", dims[5])

    def theme_layout(name):
        b = _b(name)
        lay = Layout()
        lib.moy_theme_layout(b, len(b), c.byref(lay))
        bars = []
        for i in range(lay.n_bars):
            bar = lay.bar[i]
            slots = tuple(tuple(widgets[bar.w[s][k]] for k in range(bar.n[s]))
                          for s in range(dims[1]))
            texts = tuple(lay.text[bar.text0 + k].value.decode() for k in range(bar.n_text))
            bars.append((bar_edges[bar.edge], bar.size) + slots + (texts,))
        return lay.set == 2, tuple(bars)

    homes = _names(lib, "moy_theme_home_names",
                   c.c_int.in_dll(lib, "moy_theme_n_homes").value)

    def theme_home(name):
        b = _b(name)
        lay = Layout()
        lib.moy_theme_layout(b, len(b), c.byref(lay))
        return homes[lay.home]

    pkg_bytes = c.c_size_t.in_dll(lib, "moy_theme_pkg_bytes").value

    ld = tuple((c.c_int * 10).in_dll(lib, "moy_theme_look_dims"))
    n_leaves, stops, paints_cap, tiles_cap, ab_cap, btn_n, map_max, text_max, n_texts, btn0 = ld

    class _Paint(c.Structure):
        _fields_ = [("n", c.c_uint8), ("mode", c.c_uint8), ("steps", c.c_uint8),
                    ("pad", c.c_uint8), ("t", c.c_int16 * stops), ("c", c.c_int32 * stops)]

    class _Map(c.Structure):
        _fields_ = [("name", c.c_char * (map_max + 1)), ("section", c.c_uint8),
                    ("kind", c.c_uint8), ("v", c.c_int32 * btn_n)]

    class _Look(c.Structure):
        _fields_ = [("v", c.c_int32 * n_leaves), ("n_paints", c.c_uint8),
                    ("n_tiles", c.c_uint8), ("n_appbtn", c.c_uint8), ("n_text", c.c_uint8),
                    ("paint", _Paint * paints_cap), ("tile", _Map * tiles_cap),
                    ("appbtn", _Map * ab_cap), ("text", (c.c_char * (text_max + 1)) * n_texts)]

    if c.sizeof(_Look) != c.c_size_t.in_dll(lib, "moy_theme_look_bytes").value:
        raise RuntimeError("moy_ui_binding: moy_theme_look_t does not match")

    class _Leaf(c.Structure):
        _fields_ = [("kind", c.c_uint8), ("lo", c.c_int32), ("hi", c.c_int32),
                    ("def_", c.c_int32)]

    leaf_rows = (_Leaf * n_leaves).in_dll(lib, "moy_theme_leaves")
    leaf_names = _names(lib, "moy_theme_leaf_names", n_leaves)
    enum_arr = (c.c_char_p * max(r.lo + r.hi for r in leaf_rows if r.kind == 2)).in_dll(
        lib, "moy_theme_enum_names")
    _KINDS = ("colour", "paint", "enum", "int", "num", "bool", "text", "corners")
    pmodes = ("smooth", "steps", "dither")
    btn_kinds = _names(lib, "moy_theme_btn_kinds", 5)

    def theme_look_fields():
        kinds = tuple((_KINDS[r.kind], tuple(enum_arr[r.lo + j].decode() for j in range(r.hi)))
                      if r.kind == 2 else _KINDS[r.kind] for r in leaf_rows)
        btn = tuple(n.split(".", 2)[2] for n in leaf_names[btn0:btn0 + btn_n])
        return leaf_names, kinds, btn

    def theme_look(name, light):
        b = _b(name)
        lk = _Look()
        rc = lib.moy_theme_look(b, len(b), 1 if light else 0, 1, c.byref(lk))
        paints = tuple((pmodes[p.mode], p.steps, tuple((p.t[k], p.c[k]) for k in range(p.n)))
                       for p in lk.paint[:lk.n_paints])
        tiles = tuple((t.name.decode(), t.v[0]) for t in lk.tile[:lk.n_tiles])
        ab = tuple((e.name.decode(), btn_kinds[e.kind], tuple(e.v)) for e in lk.appbtn[:lk.n_appbtn])
        texts = tuple(t.value.decode() for t in lk.text[:lk.n_text])
        return tuple(lk.v), paints, tiles, ab, texts, rc

    bd_surfaces = _names(lib, "moy_theme_bd_surfaces", 2)

    def theme_backdrop(name, light, surface):
        b = _b(name)
        out = (c.c_uint8 * 4096)()
        n = lib.moy_theme_backdrop(b, len(b), 1 if light else 0,
                                   bd_surfaces.index(surface), out, 4096)
        return bytes(out[:max(0, n)])

    def theme_place(values):
        n = len(values)
        arr = (c.c_int32 * max(1, n))(*[int(v) for v in values])
        out = (c.c_int32 * max(1, n))()
        near = (c.c_int32 * max(1, n))()
        block = (c.c_uint16 * 64)()
        used = lib.moy_theme_place(arr, n, out, near, block)
        return tuple(out[:n]), tuple(near[:n]), tuple(block), used

    def theme_block(block, used):
        arr = (c.c_uint16 * 64)(*[int(w) & 0xFFFF for w in list(block)[:64]])
        lib.moy_theme_block_use(arr, int(used))

    def theme_rgb(v):
        v = int(v)
        return lib.moy_theme_rgb565(v), lib.moy_theme_lum_of(v), lib.moy_theme_nearest(v)

    def theme_resolve(src):
        b = bytes(src)
        buf = c.create_string_buffer(pkg_bytes)
        notes = c.c_uint8()
        rc = lib.moy_theme_probe(b, len(b), buf, c.byref(notes))
        if rc != OK:
            return (rc, notes.value, None, None, None, None, None, None, None, None)
        try:
            name = lib.moy_theme_probe_name().decode()
            fonts = tuple((r, lib.moy_theme_probe_font(i).decode())
                          for i, r in enumerate(font_roles)
                          if lib.moy_theme_probe_font(i))
            sheets = tuple(None if not lib.moy_theme_probe_sheet(i)
                           else lib.moy_theme_probe_sheet(i).decode()
                           for i in range(sdims[5]))
            return (rc, notes.value, name, theme(name, False), theme(name, True),
                    fonts, theme_layout(name) + (theme_home(name),), sheets,
                    (theme_look(name, False), theme_look(name, True)),
                    tuple(tuple(theme_backdrop(name, lt, s) for s in bd_surfaces)
                          for lt in (False, True)))
        finally:
            lib.moy_theme_probe_end()

    rdims = (c.c_int * 4).in_dll(lib, "moy_theme_custom_dims")
    theme_bytes = rdims[3]
    acc = ((c.c_uint8 * 2) * rdims[0]).in_dll(lib, "moy_theme_accents")
    accents = tuple((n, acc[i][0], acc[i][1]) for i, n in
                    enumerate(_names(lib, "moy_theme_accent_names", rdims[0])))
    ramps = ((c.c_uint8 * (rdims[2] + 1)) * rdims[1]).in_dll(lib, "moy_theme_tints")
    tints = tuple((n, tuple(ramps[i][1:1 + ramps[i][0]])) for i, n in
                  enumerate(_names(lib, "moy_theme_tint_names", rdims[1])))
    seed_src = (c.c_char_p * len(seeds)).in_dll(lib, "moy_theme_seed_src")

    def theme_custom(source, accent, tint):
        if isinstance(source, str):
            if source not in seeds:
                return 6, None
            b = seed_src[seeds.index(source)]
        else:
            b = bytes(source)
        ai = -1 if accent is None else next(
            (i for i, a in enumerate(accents) if a[1] == accent), None)
        ti = -1 if tint is None else next(
            (i for i, t in enumerate(tints) if t[0] == tint), None)
        if ai is None or ti is None:
            raise ValueError("theme_custom: no such accent or tint")
        scratch = c.create_string_buffer(2 * pkg_bytes)
        out = c.create_string_buffer(theme_bytes)
        n = c.c_size_t()
        rc = lib.moy_theme_custom(b, len(b), ai, ti, scratch, out, theme_bytes, c.byref(n))
        return rc, (out.raw[:n.value] if rc == OK else None)

    def font_file(name):
        b = _b(name)
        n = c.c_size_t()
        p = lib.moy_theme_font_file(b, len(b), c.byref(n))
        return c.string_at(p, n.value) if p else None

    def print_font(font=None):
        if font is None:
            return lib.moy_text_print_font()
        was = lib.moy_ui_print_font(int(font) & 0xFFFFFFFF)
        return None if was < 0 else was

    def cart_text(on):
        lib.moy_canvas_cart(1 if on else 0)     # the canvases' colour mask with it
        return bool(lib.moy_ui_cart_text(1 if on else 0))

    def font_load(src):
        b = bytes(src)
        fid = c.c_uint32()
        rc = lib.moy_font_load(b, len(b), c.byref(fid))
        return rc, (fid.value if rc == 0 else None)

    def font_free(font):
        return bool(lib.moy_font_free(int(font) & 0xFFFFFFFF))

    def font_info(font):
        out = (c.c_int * 8)()           # moy_font_info_t
        if not lib.moy_font_info(int(font) & 0xFFFFFFFF, out):
            return None
        return tuple(out[:6])

    def font_role(role):
        if role not in font_roles:
            raise ValueError("font role")
        return font_roles.index(role)

    def use_font(role, font):
        return bool(lib.moy_ui_font_use(font_role(role), int(font) & 0xFFFFFFFF))

    def font(role):
        out = (c.c_int * 8)()
        fid = lib.moy_ui_font(font_role(role), out)
        return (fid, out[0], out[2], out[3])

    m.theme = theme
    m.theme_install = theme_install
    m.theme_installed = theme_installed
    m.theme_resolve = theme_resolve
    m.theme_look = theme_look
    m.theme_backdrop = theme_backdrop
    m.theme_look_fields = theme_look_fields
    m.theme_place = theme_place
    m.theme_block = theme_block
    m.theme_rgb = theme_rgb
    m.theme_custom = theme_custom
    m.theme_accents = lambda: accents
    m.theme_tints = lambda: tints
    m.theme_uninstall = lib.moy_theme_uninstall
    m.theme_fonts = theme_fonts
    m.theme_layout = theme_layout
    m.theme_home = theme_home
    m.font_load = font_load
    m.font_file = font_file
    m.font_reset = lib.moy_font_reset

    def text_wire(*swapped):
        was = bool(lib.moy_text_wire_swapped())
        if swapped:
            lib.moy_text_wire(1 if swapped[0] else 0)
        return was

    m.text_wire = text_wire

    def text_lanes():
        n = (c.c_uint32 * 2)()
        lib.moy_text_lanes(c.addressof(n))
        return (n[0], n[1])

    m.text_lanes = text_lanes
    m.print_font = print_font
    m.cart_text = cart_text
    m.font_free = font_free
    m.font_info = font_info
    m.use_font = use_font
    m.font = font
    m.look = look
    m.colors = colors
    m.metrics = metrics
    m.lib = lib
    return m

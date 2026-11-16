# Map (grep -n a name to jump there):
#   binding          the module: moy_ui's names over the host's one library
"""The toolkit's C (native/moy_ui) on the CPython host, by ctypes: the module
`moy_ui` as the boards and the browser import it, name for name. Its C is
in the host's one library (runtime/host/host_native.py) with the glass whose canvas
rows its primitives draw into and the Lua and compiled runs whose `ui` reads
the same state; runtime/__init__ registers this as `moy_ui`, so the host's
toolkit is the C every image runs.

This file holds the signatures, the ctypes structures and `binding()`, which
loads the library and sets the module's names through three installers
beside it: the draw verbs (moy_ui_draw_binding), the interaction over a
caller's buffers (moy_ui_scroll_binding) and the public UI API with the
rest of the module (moy_ui_api_binding), each a closure over `lib` as the
one function was, handed the helpers the one before returned.
"""

import ctypes
import os
import re
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from runtime import glass_binding  # noqa: E402
# The mirrors the api and draw bindings take from here too.
from moy_ui_face import (  # noqa: E402,F401
    moy_theme_layout_t, moy_ui_box_t as _Box, moy_ui_btn_style_t as _BtnStyle,
    moy_ui_button_t as _Button, moy_ui_cell_t as _Cell, moy_ui_depth_t as _Depth,
    moy_ui_frame_button_t as _FrameButton, moy_ui_lines_t as _Lines, moy_ui_shape_t, moy_ui_over_t as _Over,
    moy_ui_row_t as _Row, moy_ui_sink_t as _Sink, moy_ui_span_t as _Span, moy_ui_surface_t as _Surface)
from moy_gfx_face import moy_text_line_t as _Line  # noqa: E402,F401

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
    ("moy_theme_modes", [_P, _Z], _I),
    ("moy_theme_marks", [_P, _Z, _I, c.c_void_p, c.c_void_p, c.POINTER(_I)], c.c_uint32),
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
    ("moy_ui_set_disabled", [c.c_int32], None),
    ("moy_ui_set_danger_ink", [c.c_int32], None),
    ("moy_ui_buttons_app", [_P, _Z], _I),
    ("moy_theme_reads", [c.c_int32, c.c_int32], _I),
    ("moy_ui_metrics", [_I, c.POINTER(_I)], c.POINTER(c.c_int16)),
    ("moy_text_width", [c.c_uint32, _P, _Z, _I], _I),
    ("moy_text_cut_px", [c.c_uint32, _P, _Z, _I, _I, c.POINTER(_I)], _Z),
    ("moy_text_covers", [c.c_uint32, _P, _Z], _I),
    ("moy_font_load", [_P, _Z, c.POINTER(c.c_uint32)], _I),
    ("moy_font_free", [c.c_uint32], _I),
    ("moy_ui_font_free", [c.c_uint32], _I),
    ("moy_font_info", [c.c_uint32, c.c_void_p], _I),
    ("moy_ui_font_use", [_I, c.c_uint32], _I),
    ("moy_ui_font", [_I, _I, c.c_void_p], c.c_uint32),
    ("moy_ui_cart_on", [], _I),
    ("moy_theme_fonts", [_P, _Z, c.c_void_p], _I),
    ("moy_theme_layout", [_P, _Z, c.c_void_p], _I),
    ("moy_theme_font_file", [_P, _Z, c.c_void_p], c.c_void_p),
    ("moy_text_print_font", [], c.c_uint32),
    ("moy_font_reset", [], None),
    ("moy_ui_font_reset", [], None),
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


# moy_ui_api.h's MOY_UI_TXO_LEN and MOY_UI_TXO_DEFAULTS (tests/test_text_view.py pins both).
_TXO_LEN = 19
_TXO_DEFAULTS = (0, 0, 4, 0, 0, -1, -1, -1, 0, -1, 0, -1, 0, 0, 0, 0, -1, 0, -1)


_I16 = c.c_int16


_R4 = c.c_int32 * 4
DEFAULT, FROM_STYLE = -32768, -2

_DRAW_SIGS = (
    ("moy_ui_sink_row", [c.POINTER(_Sink), c.c_uint32, _I], _I),
    ("moy_ui_sink_glyph", [c.POINTER(_Sink), _I, _I, _I, _I, _I, _I, _I], _I),
    ("moy_ui_sink_mask", [c.POINTER(_Sink), c.c_uint32, _I, _I, _I, _I], _I),
    ("moy_ui_icons_use", [c.c_uint32, c.c_uint32, c.POINTER(c.c_uint8 * 3)], None),
    ("moy_ui_moy_use", [c.c_void_p, _I], None),
    ("moy_ui_calls", [], c.c_uint32),
    ("moy_ui_row", [c.POINTER(_Sink), _Box, _I, _I, _I, c.POINTER(_Row), _I], _I),
    ("moy_ui_cell", [c.POINTER(_Sink), _Box, _I, _I, _I, c.POINTER(_Cell), _I,
                     c.POINTER(_Box)], _I),
    ("moy_ui_label", [c.POINTER(_Sink), _Box, _Span, _I, _I], _I),
    ("moy_ui_label_paint", [c.POINTER(_Sink), _Box, _Span, _I, _I, c.c_void_p], _I),
    ("moy_ui_bar_rect", [_Box, _I, _I, _I, _I, _I, c.POINTER(_Box)], _I),
    ("moy_ui_scrollbar", [c.POINTER(_Sink), _Box, _I, _I, _I, _I, _I], _I),
    ("moy_ui_fill_uncovered", [c.POINTER(_Sink), _Box, _Box, _I], _I),
    ("moy_ui_fade", [c.POINTER(_Sink), _Box, _I, _I, _I, _I], _I),
    ("moy_ui_list", [c.POINTER(_Sink), _Box, _I, c.c_char_p, _Z, _I, _I, _I, _I, _I,
                     _I], _I),
    ("moy_ui_button", [c.POINTER(_Sink), _Box, _I, _I, _I, c.POINTER(_Button), _I], _I),
    ("moy_ui_button_x", [_Box, _I, _Span, _I], _I),
    ("moy_ui_chip", [c.POINTER(_Sink), _Box, _I, _I, _I, c.POINTER(_Button), _I], _I),
    ("moy_ui_tab_rects", [_Box, c.c_char_p, _Z, _I, _I, _I, c.POINTER(_Box),
                          c.POINTER(_I)], _I),
    ("moy_ui_tab", [c.POINTER(_Sink), _Box, _I, _I, _Span, _I, _I, _I], _I),
    ("moy_ui_status", [c.POINTER(_Sink), _Box, c.c_char_p, _Z, _I, _I], _I),
    ("moy_ui_hints_at", [c.POINTER(_Sink), _Box, c.c_char_p, _Z, _I, _I, _I, c.c_void_p, _I],
     _I),
    ("moy_ui_set_input", [_I], None),
    ("moy_ui_input", [], _I),
    ("moy_ui_set_hint_input", [_I], None),
    ("moy_ui_hint_input", [], _I),
    ("moy_ui_game_btn", [c.POINTER(_Sink), _Box, _Span, _I, _I], _I),
    ("moy_ui_game_icon_btn", [c.POINTER(_Sink), _Box, _I, _Span, _I, _I, _I], _I),
    ("moy_ui_mini_btn", [c.POINTER(_Sink), _Box, _Span, _I, _I, _I], _I),
    ("moy_ui_toolbar", [c.POINTER(_Sink), _Box, c.POINTER(_I)], _I),
    ("moy_ui_paint", [c.POINTER(_Sink), _Box, _I, _I, c.POINTER(_I)], _I),
    ("moy_ui_frame", [c.POINTER(_Sink), _Box, _I, _Span, _I, _I, _I], _I),
    ("moy_ui_frame_buttons", [_Box, _I, _Span, _I, _I, c.POINTER(_FrameButton)], _I),
    ("moy_ui_frame_band", [c.POINTER(_Sink), _Box, _I], _I),
    ("moy_ui_frame_icon", [_Box, _I, _I, c.POINTER(_Box)], _I),
    ("moy_ui_bar_part", [c.POINTER(_Sink), _I, _Box, _I], _I),
    ("moy_ui_bar_tile", [c.POINTER(_Sink), _Box, c.c_int32], _I),
    ("moy_ui_scrim", [c.POINTER(_Sink), _Box], _I),
    ("moy_ui_scrim_alpha", [_I], _I),
    ("moy_ui_scrim_io", [c.POINTER(_Sink), _Box, c.c_void_p, c.c_void_p, c.c_size_t], _I),
    ("moy_ui_tile", [c.POINTER(_Sink), _Box, _I, _I, _I, _I, _I, _I], _I),
    ("moy_ui_unlock", [c.POINTER(_Sink), _I, _I, _Span, _Span, _I, _I, _I, _I, _I,
                       c.POINTER(_Box)], _I),
    ("moy_ui_rec", [c.POINTER(_Sink), _I, _I, _I, _I, c.POINTER(_Box)], _I),
    ("moy_ui_month_h", [_I, _I, _I], _I),
    ("moy_ui_modal_band", [_I, _I], _I),
    ("moy_ui_modal", [c.POINTER(_Sink), _Box, _Span, _I, _I, _I], _I),
    ("moy_ui_perf", [c.POINTER(_Sink), _I, _I, _I, c.POINTER(c.c_int16), _I, _I, _Span, _I,
                     _I, _I, c.POINTER(_Box)], _I),
    ("moy_ui_icon_slot", [c.c_char_p, _Z], _I),
    ("moy_ui_icon_glyph", [c.c_char_p, _Z], _I),
    ("moy_ui_depth_use", [c.c_char_p, c.c_size_t, _I], None),
    ("moy_ui_depth", [], c.c_void_p),
    ("moy_ui_frame_geom", [_I, _I, _I, _I, _I, c.POINTER(_I)], None),
    ("moy_ui_frame_under", [c.POINTER(_Sink), _Box, _I, _I, _I], _I),
    ("moy_ui_frame_under_onto", [c.POINTER(_Sink), _Box, _I, _I, _I, _Box, _I, _I], _I),
    ("moy_ui_cast", [c.POINTER(_Sink), _Box, _I, _I], _I),
    ("moy_ui_focus", [c.POINTER(_Sink), _Box, _I, _I, _I, _I, c.POINTER(_Box),
                      c.POINTER(_I)], _I),
    ("moy_ui_current", [c.POINTER(_Sink), _Box, _I, _I, _I, _I, c.POINTER(_I)], _I),
    ("moy_ui_marks_active", [_I], None),
    ("moy_ui_marks_shown", [], _I),
    ("moy_ui_marks_drawn", [_I], c.c_uint32),
    ("moy_ui_marks", [], c.c_void_p),
    ("moy_ui_mark_reach", [_I], _I),
    ("moy_ui_mark_solid", [_I], _I),
    ("moy_ui_dialog", [c.POINTER(_Sink), _Box, _I, _I], _I),
    ("moy_ui_text_field", [c.POINTER(_Sink), _Box, _Span, _Span, c.c_void_p, _I], _I),
    ("moy_ui_panel", [c.POINTER(_Sink), _Box, _Span, _I, c.POINTER(_Box)], _I),
    ("moy_ui_inset32", [_R4, c.c_int32, c.c_int32, _R4], None),
    ("moy_ui_cut", [_R4, _I, c.c_int32, _R4, _R4], None),
    ("moy_ui_split", [_R4, _I, c.c_int32, _I, c.c_void_p, _I], _I),
    ("moy_ui_row_content", [_R4, _I, c.c_int32, c.c_int32, _I, _I, c.c_int32, _R4], None),
    ("moy_ui_cell_art", [_R4, _I, c.c_int32, c.c_int32, _R4], None),
    ("moy_ui_scroll_stop", [c.c_void_p], None),
    ("moy_ui_scroll_drag_start", [c.c_void_p, c.c_int32], None),
    ("moy_ui_scroll_drag_move", [c.c_void_p, c.c_int32, c.c_float, c.c_void_p, c.c_int32,
                                 c.c_int32], _I),
    ("moy_ui_scroll_drag_end", [c.c_void_p, c.c_int32, c.c_int32, c.c_int32], None),
    ("moy_ui_scroll_tick", [c.c_void_p, c.c_float, c.c_void_p, c.c_int32, c.c_int32], _I),
    ("moy_ui_scroll_glide_to", [c.c_void_p, c.c_int32, c.c_int32, _I, c.c_int32, c.c_int32],
     None),
    ("moy_ui_scroll_fling", [c.c_void_p, c.c_int32, c.c_float], None),
    ("moy_ui_scroll_step", [c.c_void_p, c.c_int32, c.c_int32, c.c_int32, c.c_int32,
                            c.c_int32], c.c_int32),
    ("moy_ui_scroll_land", [c.c_void_p, c.c_void_p], _I),
    ("moy_ui_dragtap_frame", [c.c_void_p, c.c_void_p, _R4, _I, c.c_int32, c.c_int32, _I, _I,
                              c.c_int32, c.c_float, c.c_void_p, c.c_int32, c.c_void_p,
                              c.c_void_p], _I),
    # the public UI API (native/moy_ui/moy_ui_api.h)
    ("moy_ui_api_ctx", [c.c_void_p, c.c_size_t], c.c_void_p),
    ("moy_ui_api_ctx_in", [c.c_void_p, c.c_void_p, c.c_size_t], c.c_void_p),
    ("moy_ui_inst_size", [], c.c_size_t),
    ("moy_ui_inst_init", [c.c_void_p], None),
    ("moy_ui_inst_free", [c.c_void_p], None),
    ("moy_ui_inst_pull", [c.c_void_p, c.c_void_p, c.c_void_p, c.c_size_t, c.c_void_p], None),
    ("moy_ui_theme_gen", [], c.c_uint32),
    ("moy_ui_theme_size", [], c.c_size_t),
    ("moy_ui_theme_pull", [c.c_uint32, c.c_void_p, c.c_size_t], c.c_uint32),
    ("moy_ui_theme_put", [c.c_void_p, c.c_size_t], _I),
    ("moy_ui_theme_pal", [c.c_void_p], None),
    ("moy_ui_theme_font", [c.c_uint32, c.c_uint32, c.c_void_p, c.c_size_t], c.c_uint32),
    ("moy_ui_theme_font_ids", [c.c_void_p], _I),
    ("moy_ui_sink_marks", [c.c_void_p], _I),
    ("moy_ui_over_block", [c.c_void_p, c.c_char_p, c.c_size_t], _I),
    ("moy_ui_api_begin", [c.c_void_p, c.c_void_p, _Box, _I, _I], _I),
    ("moy_ui_api_split", [c.c_void_p, c.POINTER(_Sink), _Span, c.c_void_p, _I, c.c_void_p,
                          c.c_void_p, c.c_void_p], _I),
    ("moy_ui_api_split_frame", [c.c_void_p, c.c_int32, _I, _I, _I, c.c_void_p, c.c_void_p], _I),
    ("moy_ui_api_table", [c.c_void_p, c.POINTER(_Sink), _Span, c.c_char_p, _Z, c.c_char_p, _Z,
                          c.c_int32, c.c_void_p, c.c_void_p], None),
    ("moy_ui_api_shift_drawn", [c.c_void_p, c.POINTER(_I)], _I),
    ("moy_ui_api_band", [c.c_void_p, c.c_void_p], _I),
    ("moy_ui_api_card_clip", [c.c_void_p, c.c_void_p], _I),
    ("moy_ui_api_lag_note", [c.c_void_p, c.c_void_p, _I, _I, _I, _I], None),
    ("moy_ui_api_lag", [c.c_void_p, _I, _I, _I, _I, c.c_void_p, _I], _I),
    ("moy_ui_api_host_begin", [c.c_void_p, c.c_void_p, _Box, _I, _I, c.c_void_p, c.c_void_p],
     _I),
    ("moy_ui_api_text_view", [c.c_void_p, c.POINTER(_Sink), _Span, c.c_void_p, c.c_void_p,
                              c.c_void_p, c.c_void_p, c.c_void_p], c.c_int32),
    ("moy_ui_api_text_reveal", [c.c_void_p, _Span, c.c_void_p, _I, _I], _I),
    ("moy_ui_text_tones", [c.c_void_p], None),
    ("moy_ui_text_words", [_I, c.c_char_p, c.c_size_t, _I], _I),
    ("moy_ui_text_words_reset", [], None),
    ("moy_ui_text_runs", [c.c_char_p, c.c_size_t, _I, c.c_void_p, _I], _I),
    ("moy_ui_api_table_rows", [c.c_void_p, c.c_void_p], _I),
    ("moy_ui_api_bar", [c.c_void_p, c.POINTER(_Sink), _Span, c.c_char_p, _Z, c.c_uint32],
     c.c_int32),
    ("moy_ui_api_bar_lent", [c.c_void_p, c.POINTER(c.c_char_p), c.POINTER(c.c_int16),
                             c.POINTER(_I), c.POINTER(c.c_uint32)], _I),
    ("moy_ui_run_bar_lent", [c.POINTER(c.c_char_p), c.POINTER(c.c_int16), c.POINTER(_I),
                             c.POINTER(c.c_uint32), c.POINTER(c.c_char_p)], _I),
    ("moy_ui_api_bar_chip", [c.c_void_p, c.POINTER(_I)], c.c_char_p),
    ("moy_ui_api_focus", [c.c_void_p, _Span, c.c_char_p, c.c_size_t], _I),
    ("moy_ui_over_size", [], c.c_size_t),
)


_SPACE = re.compile(rb"[ \t\n\r\x0b\x0c\x1c-\x1f]+")


def _names(lib, sym, n):
    arr = (c.c_char_p * n).in_dll(lib, sym)
    return tuple(arr[i].decode() for i in range(n))


def _check(rc):
    if rc == NOMEM:
        raise MemoryError("moy_ui: no memory for the looks")
    if rc != OK:
        raise ValueError("moy_ui: bad argument")


DEEP = -(1 << 31)                       # moy_ui_api.h's MOY_UI_API_DEEP


def _nested(v):
    """A verb that opens a nest: its answer, or RuntimeError where
    MOY_UI_API_NEST were open already (modmoy_ui.c's `nested`)."""
    if v == DEEP:
        raise RuntimeError("moy_ui: nested too deep")
    return v


def binding():
    """The module, or None where there is no C compiler."""
    try:
        lib = glass_binding._lib()
    except ImportError:
        return None
    from runtime import moy_ui_face
    for name, args, res in _SIGS + _DRAW_SIGS + moy_ui_face.SIGS:
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

    from runtime import moy_ui_api_binding, moy_ui_draw_binding, moy_ui_scroll_binding
    h = moy_ui_draw_binding.install(m, lib, tokens)
    h.update(moy_ui_scroll_binding.install(m, lib, h))
    moy_ui_api_binding.install(m, lib, h, counts, glyphs, kinds, states, tokens)
    m.lib = lib
    return m

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


# moy_ui_api.h's MOY_UI_TXO_LEN and MOY_UI_TXO_DEFAULTS (tests/test_text_view.py pins both).
_TXO_LEN = 19
_TXO_DEFAULTS = (0, 0, 4, 0, 0, -1, -1, -1, 0, -1, 0, -1, 0, 0, 0, 0, -1, 0, -1)


_LINE_FN = c.CFUNCTYPE(c.c_void_p, c.c_void_p, c.c_int32, c.POINTER(c.c_size_t))


class _Lines(c.Structure):
    _fields_ = [("src", c.c_void_p), ("n", c.c_int32), ("get", _LINE_FN)]


class _Depth(c.Structure):
    _fields_ = ([(n, c.c_uint8) for n in ("win_r", "card_r", "key_r", "corners",
                                          "stepped", "shadow", "edges", "frames")]
                + [("shadow_565", c.c_uint16)]
                + [(n, c.c_int16) for n in ("alpha", "blur", "dx", "dy", "back", "line",
                                            "line_back")]
                + [(n, c.c_int32) for n in ("line_c", "line_back_c", "body")]
                + [(n, c.c_uint8) for n in ("header", "align", "controls", "icon", "rule")]
                + [(n, c.c_int32) for n in ("fill", "fill_back", "grad", "grad_back")]
                + [(n, c.c_uint8) for n in ("bar_style", "hh_style", "bar_h", "hh_h")])


class _FrameButton(c.Structure):
    _fields_ = [("box", _Box), ("id", c.c_uint8), ("right", c.c_uint8)]


class _Sink(c.Structure):
    _fields_ = [("kind", c.c_uint8), ("fs", c.c_uint8), ("look", c.c_uint8),
                ("dst", c.c_void_p), ("cart", c.c_uint8),
                ("shown", c.c_uint8)]


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
                ("app", _I16), ("icon", _I16)]


class _BtnStyle(c.Structure):
    _fields_ = [("c", _I16 * 3), ("edge_w", c.c_int8), ("shape", c.c_int8),
                ("r", c.c_int8), ("font", c.c_int8), ("bevel", c.c_int8)]


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
    ("moy_ui_api_size", [], c.c_size_t),
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
    ("moy_ui_api_page", [c.c_void_p, c.POINTER(_Sink), _Span, c.c_void_p, c.POINTER(_Box)],
     None),
    ("moy_ui_api_label", [c.c_void_p, c.POINTER(_Sink), _Span, c.c_void_p], None),
    ("moy_ui_api_button", [c.c_void_p, c.POINTER(_Sink), _Span, _I, c.c_void_p], _I),
    ("moy_ui_api_toggle", [c.c_void_p, c.POINTER(_Sink), _Span, _I, c.c_void_p], _I),
    ("moy_ui_api_slider", [c.c_void_p, c.POINTER(_Sink), _Span, c.c_int32, c.c_int32,
                           c.c_int32, c.c_void_p], c.c_int32),
    ("moy_ui_api_list", [c.c_void_p, c.POINTER(_Sink), _Span, c.c_char_p, _Z, c.c_int32,
                         c.c_void_p], c.c_int32),
    ("moy_ui_api_group", [c.c_void_p, c.POINTER(_Sink), _Span, c.c_void_p], None),
    ("moy_ui_api_value", [c.c_void_p, c.POINTER(_Sink), _I, _Span, _Span, c.c_void_p], _I),
    ("moy_ui_api_choice", [c.c_void_p, c.POINTER(_Sink), _I, _Span, c.c_char_p, _Z, c.c_int32,
                           c.c_void_p], c.c_int32),
    ("moy_ui_api_hints", [c.c_void_p, c.POINTER(_Sink), c.c_char_p, _Z, c.c_void_p], None),
    ("moy_ui_api_hints_w", [c.c_void_p, c.POINTER(_Sink), c.c_char_p, _Z], _I),
    ("moy_ui_api_icon", [c.c_void_p, _Span], _I),
    ("moy_ui_api_box", [c.c_void_p, _Span, c.POINTER(c.c_int32)], _I),
    ("moy_ui_api_progress", [c.c_void_p, c.POINTER(_Sink), _Span, c.c_int32, c.c_int32,
                             c.c_void_p], None),
    ("moy_ui_api_part", [c.c_void_p, c.POINTER(_Sink), _Span, _I, _I, c.c_void_p,
                         c.c_void_p], None),
    ("moy_ui_api_part_at", [c.c_void_p, c.POINTER(c.c_int32)], _I),
    ("moy_ui_api_split", [c.c_void_p, c.POINTER(_Sink), _Span, c.c_void_p, _I, c.c_void_p,
                          c.c_void_p, c.c_void_p], _I),
    ("moy_ui_api_split_at", [c.c_void_p, _I, _I], _I),
    ("moy_ui_api_split_frame", [c.c_void_p, c.c_int32, _I, _I, _I, c.c_void_p, c.c_void_p], _I),
    ("moy_ui_api_split_toward", [c.c_void_p, _I, _I, _I, _I, c.c_void_p], _I),
    ("moy_ui_api_table", [c.c_void_p, c.POINTER(_Sink), _Span, c.c_char_p, _Z, c.c_char_p, _Z,
                          c.c_int32, c.c_void_p, c.c_void_p], None),
    ("moy_ui_api_scroll", [c.c_void_p, c.POINTER(_Sink), _Span, c.c_void_p], c.c_int32),
    ("moy_ui_api_field", [c.c_void_p, c.POINTER(_Sink), _Span, c.c_void_p, _Z, _Z, c.c_void_p],
     c.c_int32),
    ("moy_ui_api_dialog", [c.c_void_p, c.POINTER(_Sink), _Span, c.c_char_p, _Z, _I, c.c_void_p],
     c.c_int32),
    ("moy_ui_api_wash", [c.c_void_p, c.POINTER(_Sink)], None),
    ("moy_ui_api_wash_box", [c.c_void_p], _Box),
    ("moy_ui_api_owes", [c.c_void_p], _I),
    ("moy_ui_api_moved", [c.c_void_p], _I),
    ("moy_ui_api_moving", [c.c_void_p], _I),
    ("moy_ui_api_offset", [c.c_void_p, _I, _I], c.c_int32),
    ("moy_ui_api_shift_plan", [c.c_void_p, c.c_void_p], _I),
    ("moy_ui_api_shift_break", [c.c_void_p], None),
    ("moy_ui_api_shift_drawn", [c.c_void_p, c.POINTER(_I)], _I),
    ("moy_ui_api_band", [c.c_void_p, c.c_void_p], _I),
    ("moy_ui_api_card_clip", [c.c_void_p, c.c_void_p], _I),
    ("moy_ui_api_floor", [c.c_void_p, c.c_void_p, _I], _I),
    ("moy_ui_api_floor_box", [c.c_void_p, c.c_void_p], _I),
    ("moy_ui_api_change_box", [c.c_void_p, c.c_void_p], _I),
    ("moy_ui_api_lag_note", [c.c_void_p, c.c_void_p, _I, _I, _I, _I], None),
    ("moy_ui_api_lag", [c.c_void_p, _I, _I, _I, _I, c.c_void_p, _I], _I),
    ("moy_ui_api_host_begin", [c.c_void_p, c.c_void_p, _Box, _I, _I, c.c_void_p, c.c_void_p],
     _I),
    ("moy_ui_api_host_arm", [c.c_void_p], _I),
    ("moy_ui_api_host_whole", [c.c_void_p], None),
    ("moy_ui_api_keys_back", [c.c_void_p], None),
    ("moy_ui_api_pop_box", [c.c_void_p, c.c_void_p], _I),
    ("moy_ui_api_text_view", [c.c_void_p, c.POINTER(_Sink), _Span, c.c_void_p, c.c_void_p,
                              c.c_void_p, c.c_void_p, c.c_void_p], c.c_int32),
    ("moy_ui_api_text_at", [c.c_void_p, _Span, _I, _I, c.c_void_p], _I),
    ("moy_ui_api_text_cover", [c.c_void_p, _Span, _I, _I], None),
    ("moy_ui_api_text_aim", [c.c_void_p, _Span, _I, _I], _I),
    ("moy_ui_api_text_reveal", [c.c_void_p, _Span, c.c_void_p, _I, _I], _I),
    ("moy_ui_text_tones", [c.c_void_p], None),
    ("moy_ui_text_words", [_I, c.c_char_p, c.c_size_t, _I], _I),
    ("moy_ui_text_words_reset", [], None),
    ("moy_ui_text_runs", [c.c_char_p, c.c_size_t, _I, c.c_void_p, _I], _I),
    ("moy_ui_api_keep", [c.c_void_p, c.POINTER(_Sink), _Span, _I], _I),
    ("moy_ui_api_audit", [c.c_void_p, _I], c.c_uint32),
    ("moy_ui_api_fades", [c.c_void_p, _I], c.c_uint32),
    ("moy_ui_api_end", [c.c_void_p, c.POINTER(_Sink)], None),
    ("moy_ui_api_table_pics", [c.c_void_p, c.POINTER(_Sink)], None),
    ("moy_ui_api_table_rows", [c.c_void_p, c.c_void_p], _I),
    ("moy_ui_api_table_dist", [c.c_void_p], c.c_int32),
    ("moy_ui_api_table_part", [c.c_void_p, c.POINTER(_Sink), c.c_void_p], c.c_int32),
    ("moy_ui_api_table_floor", [c.c_void_p, c.c_void_p], c.c_int32),
    ("moy_ui_api_table_copy", [c.c_void_p, c.c_void_p, c.c_int], c.c_int32),
    ("moy_ui_api_table_shift", [c.c_void_p, c.c_void_p, c.c_int], c.c_int32),
    ("moy_ui_api_held", [c.c_void_p, _Span, c.c_int32], _I),
    ("moy_ui_api_held_last", [c.c_void_p, _Span, c.c_int32], _I),
    ("moy_ui_api_held_all", [c.c_void_p], _I),
    ("moy_ui_api_slots", [c.c_void_p, c.c_int32, c.c_int32], c.c_int32),
    ("moy_ui_api_slot", [c.c_void_p, _Span, c.c_int32, _I], c.c_int32),
    ("moy_ui_api_slot_next", [c.c_void_p], c.c_int32),
    ("moy_ui_api_block_set", [c.c_void_p, _Span, c.c_int, c.c_uint32], c.c_int),
    ("moy_ui_api_block_gen", [c.c_void_p, _Span], c.c_int32),
    ("moy_ui_api_block_since", [c.c_void_p, _Span, c.c_int32], c.c_uint32),
    ("moy_ui_api_block_key", [c.c_void_p, _Span], c.c_int32),
    ("moy_ui_api_popover", [c.c_void_p, c.POINTER(_Sink), _Span, _I, c.c_void_p], _I),
    ("moy_ui_api_menu", [c.c_void_p, c.POINTER(_Sink), _Span, c.c_char_p, _Z, c.c_char_p, _Z,
                         c.c_void_p], c.c_int32),
    ("moy_ui_api_menu_danger", [c.c_void_p, c.c_uint32], None),
    ("moy_ui_api_text", [c.c_void_p, _I, _I, _I, _I], None),
    ("moy_ui_api_card_mark", [c.c_void_p, _Span, _I, _Span], None),
    ("moy_ui_api_grid_spread", [c.c_void_p, _I, _I, _I, _I, _I], None),
    ("moy_ui_api_group_card", [c.c_void_p, c.POINTER(_Sink)], None),
    ("moy_ui_api_empty", [c.c_void_p, c.POINTER(_Sink), _Span, _Span, c.c_char_p, _Z], _I),
    ("moy_ui_api_note", [c.c_void_p, c.POINTER(_Sink), _Span, c.c_char_p, _Z], _I),
    ("moy_ui_api_bubble", [c.c_void_p, c.POINTER(_Sink), _Span, _Span, _Span, c.c_void_p, _I,
                           c.c_void_p, c.c_void_p], None),
    ("moy_ui_api_month", [c.c_void_p, c.POINTER(_Sink), _Span, _Span, _I, _I, _I], _I),
    ("moy_ui_api_toast", [c.c_void_p, c.POINTER(_Sink), _Span, _Span, c.c_int,
                          c.POINTER(c.c_int32)], None),
    ("moy_ui_api_tool", [c.c_void_p, c.POINTER(_Sink), _Span, _Span, c.c_void_p], _I),
    ("moy_ui_api_bar", [c.c_void_p, c.POINTER(_Sink), _Span, c.c_char_p, _Z, c.c_uint32],
     c.c_int32),
    ("moy_ui_api_bar_lent", [c.c_void_p, c.POINTER(c.c_char_p), c.POINTER(c.c_int16),
                             c.POINTER(_I), c.POINTER(c.c_uint32)], _I),
    ("moy_ui_api_bar_tap", [c.c_void_p, _I], None),
    ("moy_ui_api_back", [c.c_void_p, _I], _I),
    ("moy_ui_api_cancel", [c.c_void_p, _I], _I),
    ("moy_ui_run_back", [_I], _I),
    ("moy_ui_run_bar_lent", [c.POINTER(c.c_char_p), c.POINTER(c.c_int16), c.POINTER(_I),
                             c.POINTER(c.c_uint32), c.POINTER(c.c_char_p)], _I),
    ("moy_ui_run_bar_tap", [_I], None),
    ("moy_ui_api_paste", [c.c_void_p, c.c_char_p, c.c_size_t], None),
    ("moy_ui_run_paste", [c.c_char_p, c.c_size_t], None),
    ("moy_ui_run_steps", [c.c_int32, c.c_int32], None),
    ("moy_ui_run_typing", [], _I),
    ("moy_ui_run_cancel", [_I], _I),
    ("moy_ui_api_bar_chip", [c.c_void_p, c.POINTER(_I)], c.c_char_p),
    ("moy_ui_api_rule", [c.c_void_p, c.POINTER(_Sink)], None),
    ("moy_ui_api_grid", [c.c_void_p, c.POINTER(_Sink), _Span, _I, _I, c.c_void_p], None),
    ("moy_ui_api_card", [c.c_void_p, c.POINTER(_Sink), _Span, _Span, c.c_void_p, c.c_void_p],
     None),
    ("moy_ui_api_focus", [c.c_void_p, _Span, c.c_char_p, c.c_size_t], _I),
    ("moy_ui_api_focus_ahead", [c.c_void_p, _Span], None),
    ("moy_ui_api_keys_taken", [c.c_void_p], _I),
    ("moy_ui_api_typing", [c.c_void_p], _I),
    ("moy_ui_api_edge", [c.c_void_p], _I),
    ("moy_ui_api_tap_armed", [c.c_void_p], _I),
    ("moy_ui_api_at", [c.c_void_p, _I, _I, _I, _I], None),
    ("moy_ui_api_dup", [c.c_void_p], c.c_char_p),
    ("moy_ui_api_lost", [c.c_void_p], _I),
    ("moy_ui_over_size", [], c.c_size_t),
)


class _Over(c.Structure):
    """moy_ui_over_t (native/moy_ui/moy_ui_api.h)."""
    _fields_ = [("fill", c.c_int32), ("ink", c.c_int32), ("border", c.c_int32),
                ("id", _Span), ("current", c.c_int8), ("disabled", c.c_int8),
                ("nav", c.c_int8), ("mark", c.c_int8), ("steps", c.c_int8),
                ("hint", _Span), ("wash", c.c_int8), ("lead", c.c_int8),
                ("has_gen", c.c_int8), ("gen", c.c_int32), ("has_part", c.c_int8),
                ("part_w", c.c_int16), ("part_h", c.c_int16), ("bar", c.c_int8),
                ("axis_x", c.c_int8), ("snap", c.c_int8), ("has_offset", c.c_int8),
                ("offset", c.c_int32), ("spill", c.c_int16), ("has_keep", c.c_int8), ("whole", c.c_int8),
                ("keep", c.c_int32), ("over_x", c.c_int16), ("over_y", c.c_int16),
                ("over_w", c.c_int16), ("over_h", c.c_int16), ("shift", c.c_int8),
                ("fade", c.c_int16), ("has_across", c.c_int8),
                ("across_first", c.c_int16), ("across_pitch", c.c_int16), ("at", c.c_int16),
                ("signal", c.c_int8), ("values", _Span), ("check", c.c_int8),
                ("subs", _Span), ("icons", _Span), ("counts", _Span), ("tags", _Span),
                ("marks", _Span), ("card", c.c_int8), ("tag", _Span),
                ("focus", _Span), ("flags2", c.c_int32)]


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

    from runtime import moy_ui_api_binding, moy_ui_draw_binding, moy_ui_scroll_binding
    h = moy_ui_draw_binding.install(m, lib, tokens)
    h.update(moy_ui_scroll_binding.install(m, lib, h))
    moy_ui_api_binding.install(m, lib, h, counts, glyphs, kinds, states, tokens)
    m.lib = lib
    return m

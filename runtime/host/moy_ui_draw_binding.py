# Map (grep -n a name to jump there):
#   install          the draw verbs set on the moy_ui module, and the helpers the rest reads
"""The draw verbs of the host's `moy_ui` (runtime/host/moy_ui_binding.py):
the toolkit's kinds, looks and metrics, the text path's measures, and every
rect-taking widget -- rows, cells, buttons, frames, panels, the marks -- each
one call into native/moy_ui over ctypes. `binding()` calls `install` once,
and the helpers it returns are the ones the interaction and the public UI
API's verbs read. Host only.
"""

import ctypes as c

from runtime.moy_ui_binding import (DEFAULT, FROM_STYLE, STALE, _Box, _BtnStyle,
                                    _Button, _Cell, _FrameButton, _I, _I16x3, _Line,
                                    _R4, _Row, _SPACE, _Sink, _Span, _check)


def install(m, lib, tokens):
    """Set the draw verbs on module `m` over `lib`; the helpers the rest reads."""
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

    def text_cut(text, maxw, scale=1, font=0):
        b = _b(text)
        ell = _I(0)
        k = lib.moy_text_cut_px(int(font) & 0xFFFFFFFF, b, len(b), max(0, int(maxw)),
                                int(scale), c.byref(ell))
        if k == len(b) and not ell.value:
            return text
        return b[:k].decode("utf-8") + ("\u2026" if ell.value else "")

    def text_covers(text, font=0):
        b = _b(text)
        return bool(lib.moy_text_covers(int(font) & 0xFFFFFFFF, b, len(b)))

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

    def icons_use(f16, f24, close, maximize, minimize):
        codes = (c.c_uint8 * 3)(int(close), int(maximize), int(minimize))
        lib.moy_ui_icons_use(int(f16), int(f24), c.byref(codes))

    def moy_use(px, clear):
        if px is None:
            lib.moy_ui_moy_use(None, 0)
            return
        b = bytes(px)
        if len(b) < 256:
            raise ValueError("moy_use: 256 pixels")
        buf = (c.c_uint8 * 256).from_buffer_copy(b[:256])
        lib.moy_ui_moy_use(c.byref(buf), int(clear))

    def icon_mask(cv, font, code, x, y, ci):
        s = sink(cv, None)
        _check(lib.moy_ui_sink_mask(c.byref(s), int(font), int(code), int(x), int(y),
                                    int(ci)))

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

    def fill_uncovered(cv, inner, outer, ci):
        _check(lib.moy_ui_fill_uncovered(c.byref(sink(cv, None)), box_of(inner),
                                         box_of(outer), int(ci)))

    def fade(cv, rect, ci, up, look=None):
        s = sink(cv, look)
        _check(lib.moy_ui_fade(c.byref(s), box_of(rect), int(ci), 1 if up else 0, s.fs, 0))

    def list_(cv, look, rect, kind, items, count, row_h, offset, sel, bar_w):
        s = sink(cv, look)
        b = bytes(items)
        _check(lib.moy_ui_list(c.byref(s), box_of(rect), int(kind), b, len(b), int(count),
                               int(row_h), int(offset), int(sel), s.fs, int(bar_w)))

    def _btn(label, glyph_, fs, colors=None, app=-1):
        k = _Button()
        k.label, keep = span(label)
        k.glyph, k.fs, k.app, k.icon = int(glyph_), int(fs), int(app), -1
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

    def hints(cv, look, rect, items, desk, draw):
        s = sink(cv, look)
        b = bytes(items)
        spans = (c.c_int16 * 32)()
        n = lib.moy_ui_hints_at(c.byref(s), box_of(rect), b, len(b), s.fs, int(desk),
                                int(bool(draw)), spans, 16)
        return tuple((spans[2 * k], spans[2 * k + 1]) for k in range(min(n, 16)))

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

    def frame(cv, look, rect, title_h, title, active, btns, fs):
        s = sink(cv, look)
        sp, keep = span_if(title)
        _check(lib.moy_ui_frame(c.byref(s), box_of(rect), int(title_h), sp,
                                1 if active else 0, int(btns), int(fs)))

    def frame_buttons(rect, title_h, title, btns, fs):
        sp, keep = span_if(title)
        out = (_FrameButton * 4)()                  # MOY_UI_FRAME_BUTTONS
        n = lib.moy_ui_frame_buttons(box_of(rect), int(title_h), sp, int(btns), int(fs), out)
        return tuple((b.id, b.box.x, b.box.y, b.box.w, b.box.h, b.right) for b in out[:n])

    def frame_icon(rect, title_h, btns):
        out = _Box()
        if not lib.moy_ui_frame_icon(box_of(rect), int(title_h), int(btns), c.byref(out)):
            return None
        return (out.x, out.y, out.w, out.h)

    def bar_part(cv, look, part, rect, on, fill=None):
        if part == 5 and fill is not None:          # MOY_UI_BARP_TILE
            return bool(lib.moy_ui_bar_tile(c.byref(sink(cv, look)), box_of(rect), int(fill)))
        return bool(lib.moy_ui_bar_part(c.byref(sink(cv, look)), int(part), box_of(rect),
                                        int(on)))

    def _pixels(b, write):
        """(pointer, bytes, what keeps it) of a buffer the C reads or writes."""
        if isinstance(b, bytes):
            if write:
                raise TypeError("moy_ui: a read-only buffer")
            keep = c.c_char_p(b)
            return c.cast(keep, c.c_void_p), len(b), keep
        mv = memoryview(b).cast("B")
        keep = (c.c_char * mv.nbytes).from_buffer(mv)
        return c.cast(keep, c.c_void_p), mv.nbytes, keep

    def scrim(cv, look, rect, src=None, also=None):
        s = sink(cv, look)
        if src is None and also is None:
            _check(lib.moy_ui_scrim(c.byref(s), box_of(rect)))
            return
        n = -1 & 0xFFFFFFFFFFFFFFFF
        ps = pa = None
        if src is not None:
            ps, k, _ks = _pixels(src, False)
            n = min(n, k)
        if also is not None:
            pa, k, _ka = _pixels(also, True)
            n = min(n, k)
        _check(lib.moy_ui_scrim_io(c.byref(s), box_of(rect), ps, pa, n))

    def scrim_alpha(a):
        return lib.moy_ui_scrim_alpha(int(a))

    def _icon(name):
        b = _b(name or "")
        return lib.moy_ui_icon_slot(b, len(b)), lib.moy_ui_icon_glyph(b, len(b))

    def tile(cv, look, rect, icon, tone, r, fs, desk):
        sl, gl = _icon(icon)
        _check(lib.moy_ui_tile(c.byref(sink(cv, look)), box_of(rect), sl, gl, int(tone), int(r),
                               int(fs), int(desk)))

    def unlock(cv, look, cx, y, head, name, icon, burst, fs, desk):
        sl, gl = _icon(icon)
        h, keep = span(head)
        n, keep2 = span(name)
        out = _Box()
        _check(lib.moy_ui_unlock(c.byref(sink(cv, look)), int(cx), int(y), h, n, sl, gl,
                                 int(burst), int(fs), int(desk), c.byref(out)))
        return (out.x, out.y, out.w, out.h)

    def rec(cv, look, right, top, fs, desk):
        out = _Box()
        _check(lib.moy_ui_rec(c.byref(sink(cv, look)), int(right), int(top), int(fs), int(desk),
                              c.byref(out)))
        return (out.x, out.y, out.w, out.h)

    def month_h(weeks, fs, desk):
        return int(lib.moy_ui_month_h(int(weeks), int(fs), int(desk)))

    def modal_band(fs, desk):
        return int(lib.moy_ui_modal_band(int(fs), 1 if desk else 0))

    def modal(cv, look, rect, fs, desk):
        t, keep = span("")
        _check(lib.moy_ui_modal(c.byref(sink(cv, look)), box_of(rect), t, int(fs), int(desk), 0))

    def perf(cv, look, args, words, out):
        v = [int(x) for x in args[:12]]
        pa = (c.c_int16 * 4)(*v[8:12])
        t, keep = span(words)
        b = _Box()
        _check(lib.moy_ui_perf(c.byref(sink(cv, look)), v[0], v[1], v[2], pa, v[3], v[4], t,
                               v[5], v[6], v[7], c.byref(b)))
        out[0], out[1], out[2], out[3] = b.x, b.y, b.w, b.h

    def frame_geom(w, h, title_h, active, mode):
        out = (_I * 6)()
        lib.moy_ui_frame_geom(int(w), int(h), int(title_h), 1 if active else 0, int(mode), out)
        return tuple(out)

    def frame_under(cv, look, rect, title_h, active, mode):
        _check(lib.moy_ui_frame_under(c.byref(sink(cv, look)), box_of(rect), int(title_h),
                                      1 if active else 0, int(mode)))

    def frame_under_onto(cv, look, rect, title_h, active, mode, onto, onto_th, onto_active):
        _check(lib.moy_ui_frame_under_onto(c.byref(sink(cv, look)), box_of(rect), int(title_h),
                                           1 if active else 0, int(mode), box_of(onto),
                                           int(onto_th), 1 if onto_active else 0))

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

    def focus(cv, look, rect, kind, r, flags, fs):
        s = sink(cv, look)
        out, ink = _Box(), _I()
        _check(lib.moy_ui_focus(c.byref(s), box_of(rect), int(kind), int(r), int(flags),
                                fs_or(fs, s), c.byref(out), c.byref(ink)))
        return (None if ink.value < 0 else ink.value, (out.x, out.y, out.w, out.h))

    def current(cv, look, rect, kind, r, flags, fs):
        s = sink(cv, look)
        ink = _I()
        _check(lib.moy_ui_current(c.byref(s), box_of(rect), int(kind), int(r), int(flags),
                                  fs_or(fs, s), c.byref(ink)))
        return None if ink.value < 0 else ink.value

    def marks(active, reset):
        if active is not None:
            lib.moy_ui_marks_active(1 if active else 0)
        return (bool(lib.moy_ui_marks_shown()), lib.moy_ui_marks_drawn(1 if reset else 0),
                lib.moy_ui_mark_reach(2), bool(lib.moy_ui_mark_solid(1)))

    def mark_reach(kind):
        return lib.moy_ui_mark_reach(int(kind))

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

    for f in (row, cell, label, bar_rect, scrollbar, fill_uncovered, fade,
              button, button_x, chip, tab_rects, tab, status, hints, game_btn, game_icon_btn,
              mini_btn, toolbar, paint, frame, frame_buttons, frame_band, frame_geom, frame_icon, bar_part,
              scrim, scrim_alpha, tile, unlock, rec, modal, modal_band, month_h, perf,
              frame_under, frame_under_onto, cast, dialog, text_field, panel, focus, current,
              marks, mark_reach, inset, cut,
              split, row_content, cell_art):
        setattr(m, f.__name__, f)
    m.list = list_
    return {'_b': _b, 'box_of': box_of, 'button_kind': button_kind, 'button_style': button_style, 'buttons_app': buttons_app, 'buttons_set': buttons_set, 'colors': colors, 'glyph': glyph, 'glyph_id': glyph_id, 'icon_mask': icon_mask, 'icons_use': icons_use, 'kind': kind, 'look': look, 'metrics': metrics, 'moy_use': moy_use, 'r4': r4, 'sink': sink, 'span': span, 'text_covers': text_covers, 'text_cut': text_cut, 'text_width': text_width, 'wrap': wrap}

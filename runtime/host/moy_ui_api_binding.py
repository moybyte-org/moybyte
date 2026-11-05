# Map (grep -n a name to jump there):
#   install          the public UI API and the rest of the moy_ui module, set on it
"""The public UI API's verbs of the host's `moy_ui`
(runtime/host/moy_ui_binding.py; native/moy_ui/moy_ui_api.h) -- a context,
its pages, rows, scrolls and text views -- and the rest of the module: the
glyph and string tables, the themes and their shapes, the fonts and the
input kind. `binding()` calls `install` once, with the helpers the draw and
interaction verbs returned. Host only.
"""

import ctypes as c

from runtime.moy_ui_binding import (OK, _Box, _Depth, _I, _LINE_FN, _Lines, _Over,
                                    _Span, _Surface, _TXO_DEFAULTS, _TXO_LEN, _check,
                                    _layout_type, _names, _nested, _shape_type)


def install(m, lib, h, counts, glyphs, kinds, states, tokens):
    """Set the public UI API and the rest on module `m` over `lib`."""
    _b = h['_b']
    box_of = h['box_of']
    button_kind = h['button_kind']
    button_style = h['button_style']
    buttons_app = h['buttons_app']
    buttons_set = h['buttons_set']
    colors = h['colors']
    glyph = h['glyph']
    glyph_id = h['glyph_id']
    icon_mask = h['icon_mask']
    icons_use = h['icons_use']
    kind = h['kind']
    look = h['look']
    metrics = h['metrics']
    moy_use = h['moy_use']
    sink = h['sink']
    span = h['span']
    text_covers = h['text_covers']
    text_cut = h['text_cut']
    text_width = h['text_width']
    wrap = h['wrap']
    _buf = h['_buf']
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

    def api_begin(ctx, inp, rect, flags, fs):
        p, _k = _ctx(ctx)
        ib = None if inp is None else _buf(inp)
        return bool(lib.moy_ui_api_begin(p, ib, box_of(rect), int(flags), int(fs)))

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

    def api_icon(ctx, name):
        p, _k = _ctx(ctx)
        t, keep = span(name)
        return bool(lib.moy_ui_api_icon(p, t))

    def api_box(ctx, id_):
        p, _k = _ctx(ctx)
        t, keep = span(id_)
        v = (c.c_int32 * 4)()
        if not lib.moy_ui_api_box(p, t, v):
            return None
        return tuple(v)

    def api_progress(ctx, cv, look, label, v, hi, o):
        p, _k = _ctx(ctx)
        s = sink(cv, look)
        t, keep = span(label)
        ov, ok = _over(o)
        lib.moy_ui_api_progress(p, c.byref(s), t, int(v), int(hi), ov)

    def api_list(ctx, cv, look, label, items, cur, o):
        p, _k = _ctx(ctx)
        s = sink(cv, look)
        t, keep = span(label)
        ov, ok = _over(o)
        b = bytes(items)
        return int(lib.moy_ui_api_list(p, c.byref(s), t, b, len(b), int(cur), ov))

    def api_group(ctx, cv, look, caption, o):
        p, _k = _ctx(ctx)
        s = sink(cv, look)
        t, keep = span(caption)
        ov, ok = _over(o)
        lib.moy_ui_api_group(p, c.byref(s), t, ov)

    def api_value(ctx, cv, look, kind_, label, value, o):
        p, _k = _ctx(ctx)
        s = sink(cv, look)
        t, keep = span(label)
        v, keep2 = span(value)
        ov, ok = _over(o)
        return bool(lib.moy_ui_api_value(p, c.byref(s), int(kind_), t, v, ov))

    def api_choice(ctx, cv, look, kind_, label, opts, cur, o):
        p, _k = _ctx(ctx)
        s = sink(cv, look)
        t, keep = span(label)
        ov, ok = _over(o)
        b = bytes(opts)
        return int(lib.moy_ui_api_choice(p, c.byref(s), int(kind_), t, b, len(b), int(cur), ov))

    def api_hints(ctx, cv, look, items, o):
        p, _k = _ctx(ctx)
        s = sink(cv, look)
        ov, ok = _over(o)
        b = bytes(items)
        lib.moy_ui_api_hints(p, c.byref(s), b, len(b), ov)

    def api_hints_w(ctx, cv, look, items):
        p, _k = _ctx(ctx)
        s = sink(cv, look)
        b = bytes(items)
        return int(lib.moy_ui_api_hints_w(p, c.byref(s), b, len(b)))

    def api_table(ctx, cv, look, label, cols, cells, cur, o):
        p, _k = _ctx(ctx)
        s = sink(cv, look)
        t, keep = span(label)
        ov, ok = _over(o)
        cb, eb = bytes(cols), bytes(cells)
        out = (c.c_int32 * 2)()
        lib.moy_ui_api_table(p, c.byref(s), t, cb, len(cb), eb, len(eb), int(cur), ov, out)
        return int(out[0]), bool(out[1])

    def api_scroll(ctx, cv, look, id_, o):
        p, _k = _ctx(ctx)
        s = sink(cv, look)
        t, keep = span(id_)
        if not t.n:
            keep = c.create_string_buffer(b"", 1)
            t = _Span(c.addressof(keep), 0)
        ov, ok = _over(o)
        _nested(lib.moy_ui_api_scroll(p, c.byref(s), t, ov))

    def api_field(ctx, cv, look, label, buf, n, o):
        p, _k = _ctx(ctx)
        s = sink(cv, look)
        t, keep = span(label)
        ov, ok = _over(o)
        raw = (c.c_uint8 * max(1, len(buf))).from_buffer(buf) if len(buf) else (c.c_uint8 * 1)()
        return int(lib.moy_ui_api_field(p, c.byref(s), t, raw, len(buf), int(n), ov))

    def api_dialog(ctx, cv, look, title, buttons, kind_, o):
        p, _k = _ctx(ctx)
        s = sink(cv, look)
        t, keep = span(title)
        if not t.n:
            keep = c.create_string_buffer(b"", 1)
            t = _Span(c.addressof(keep), 0)
        ov, ok = _over(o)
        b = bytes(buttons)
        return _nested(int(lib.moy_ui_api_dialog(p, c.byref(s), t, b, len(b), int(kind_), ov)))

    def api_wash(ctx, cv, look):
        p, _k = _ctx(ctx)
        s = sink(cv, look)
        lib.moy_ui_api_wash(p, c.byref(s))

    def api_wash_box(ctx):
        p, _k = _ctx(ctx)
        b = lib.moy_ui_api_wash_box(p)
        return (b.x, b.y, b.w, b.h)

    def api_popover(ctx, cv, look, id_, w, o):
        p, _k = _ctx(ctx)
        s = sink(cv, look)
        t, keep = span(id_)
        if not t.n:
            keep = c.create_string_buffer(b"", 1)
            t = _Span(c.addressof(keep), 0)
        ov, ok = _over(o)
        return _nested(int(lib.moy_ui_api_popover(p, c.byref(s), t, int(w), ov)))

    def api_menu(ctx, cv, look, label, items, icons, o):
        p, _k = _ctx(ctx)
        s = sink(cv, look)
        t, keep = span(label)
        if not t.n:
            keep = c.create_string_buffer(b"", 1)
            t = _Span(c.addressof(keep), 0)
        ov, ok = _over(o)
        b, ib = bytes(items), bytes(icons)
        return int(lib.moy_ui_api_menu(p, c.byref(s), t, b, len(b), ib, len(ib), ov))

    def api_menu_danger(ctx, rows):
        p, _k = _ctx(ctx)
        lib.moy_ui_api_menu_danger(p, int(rows) & 0xFFFFFFFF)

    def api_text(ctx, lines, centre, tone, role):
        p, _k = _ctx(ctx)
        lib.moy_ui_api_text(p, int(lines), int(centre), int(tone), int(role))

    def _span0(text):
        t, keep = span(text)
        if not t.n:
            keep = c.create_string_buffer(b"", 1)
            t = _Span(c.addressof(keep), 0)
        return t, keep

    def api_card_mark(ctx, badge, tone, blank):
        p, _k = _ctx(ctx)
        t, keep = _span0(badge)
        u, keep2 = _span0(blank)
        lib.moy_ui_api_card_mark(p, t, int(tone), u)

    def api_grid_spread(ctx, cols, foot, top, margin, wrap=0):
        p, _k = _ctx(ctx)
        lib.moy_ui_api_grid_spread(p, int(cols), int(foot), int(top), int(margin), int(wrap))

    def api_group_card(ctx, cv, look):
        p, _k = _ctx(ctx)
        s = sink(cv, look)
        lib.moy_ui_api_group_card(p, c.byref(s))

    def api_empty(ctx, cv, look, icon, title, lines):
        p, _k = _ctx(ctx)
        s = sink(cv, look)
        t, keep = _span0(icon)
        u, keep2 = _span0(title)
        b = bytes(lines)
        return int(lib.moy_ui_api_empty(p, c.byref(s), t, u, b, len(b)))

    def api_month(ctx, cv, look, title, days, first, n, today):
        p, _k = _ctx(ctx)
        s = sink(cv, look)
        t, keep = _span0(title)
        u, keep2 = _span0(days)
        return int(lib.moy_ui_api_month(p, c.byref(s), t, u, int(first), int(n), int(today)))

    def api_toast(ctx, cv, look, text, kind, flat):
        p, _k = _ctx(ctx)
        s = sink(cv, look)
        t, keep = _span0(text)
        u, keep2 = _span0(kind)
        out = (c.c_int32 * 4)()
        lib.moy_ui_api_toast(p, c.byref(s), t, u, 1 if flat else 0, out)
        return tuple(out)

    def api_tool(ctx, cv, look, icon, label, o):
        p, _k = _ctx(ctx)
        s = sink(cv, look)
        i, keep = span(icon)
        t, keep2 = span(label)
        ov, ok = _over(o)
        return bool(lib.moy_ui_api_tool(p, c.byref(s), i, t, ov))

    def api_bar(ctx, cv, look, title, tools, off, back=False):
        p, _k = _ctx(ctx)
        s = sink(cv, look)
        t, keep = span(title)
        if not t.n:
            keep = c.create_string_buffer(b"", 1)
            t = _Span(c.addressof(keep), 0)
        b = bytes(tools)
        off = (int(off) | (0x80000000 if back else 0)) & 0xFFFFFFFF
        return int(lib.moy_ui_api_bar(p, c.byref(s), t, b, len(b), off))

    def api_bar_lent(ctx):
        p, _k = _ctx(ctx)
        title = c.c_char_p()
        slots = (c.c_int16 * 6)()
        n = _I()
        off = c.c_uint32()
        if not lib.moy_ui_api_bar_lent(p, c.byref(title), slots, c.byref(n), c.byref(off)):
            return None
        at = _I()
        chip = lib.moy_ui_api_bar_chip(p, c.byref(at))
        return ((title.value or b"").decode("utf-8", "replace"),
                tuple(slots[i] for i in range(n.value)), off.value,
                (chip or b"").decode("utf-8", "replace"))

    def api_rule(ctx, cv, look):
        p, _k = _ctx(ctx)
        lib.moy_ui_api_rule(p, c.byref(sink(cv, look)))

    def api_bar_tap(ctx, i):
        p, _k = _ctx(ctx)
        lib.moy_ui_api_bar_tap(p, int(i))

    def api_owes(ctx):
        p, _k = _ctx(ctx)
        return bool(lib.moy_ui_api_owes(p))

    def api_moved(ctx):
        p, _k = _ctx(ctx)
        return bool(lib.moy_ui_api_moved(p))

    def api_moving(ctx):
        p, _k = _ctx(ctx)
        return bool(lib.moy_ui_api_moving(p))

    def api_busy(ctx):
        p, _k = _ctx(ctx)
        return bool(lib.moy_ui_api_owes(p) or lib.moy_ui_api_moving(p))

    def api_offset(ctx, x, y):
        p, _k = _ctx(ctx)
        return int(lib.moy_ui_api_offset(p, int(x), int(y)))

    def api_fades(reset):
        return int(lib.moy_ui_api_fades(1 if reset else 0))

    def api_shift_plan(ctx, out):
        p, _k = _ctx(ctx)
        w = (c.c_int32 * 8).from_buffer(out)
        return int(lib.moy_ui_api_shift_plan(p, c.addressof(w)))

    def api_shift_break(ctx):
        p, _k = _ctx(ctx)
        lib.moy_ui_api_shift_break(p)

    def api_shift_drawn(ctx):
        p, _k = _ctx(ctx)
        clean = _I(0)
        k = int(lib.moy_ui_api_shift_drawn(p, c.byref(clean)))
        return k | (4 if clean.value else 0)

    def api_band(ctx, r=None):
        p, _k = _ctx(ctx)
        v = (c.c_int32 * 4)() if r is None else (c.c_int32 * 4).from_buffer(r)
        if not lib.moy_ui_api_band(p, v):
            return None
        return tuple(v) if r is None else r

    def api_floor(ctx):
        p, _k = _ctx(ctx)
        v = (c.c_int32 * 96)()
        n = int(lib.moy_ui_api_floor(p, v, 24))
        if n < 0:
            return None
        return tuple(tuple(v[4 * i:4 * i + 4]) for i in range(n))

    def api_floor_box(ctx, out):
        p, _k = _ctx(ctx)
        return bool(lib.moy_ui_api_floor_box(p, (c.c_int32 * 4).from_buffer(out)))

    def api_change_box(ctx, out):
        p, _k = _ctx(ctx)
        return bool(lib.moy_ui_api_change_box(p, (c.c_int32 * 8).from_buffer(out)))

    def api_lag_note(ctx, hist, bands, f, kind):
        p, _k = _ctx(ctx)
        slots = len(hist) // (2 + 2 * bands)
        lib.moy_ui_api_lag_note(p, (c.c_int32 * len(hist)).from_buffer(hist), slots,
                                int(bands), int(f), int(kind))

    def api_lag(hist, bands, f, n, out):
        slots = len(hist) // (2 + 2 * bands)
        return int(lib.moy_ui_api_lag((c.c_int32 * len(hist)).from_buffer(hist), slots,
                                      int(bands), int(f), int(n),
                                      (c.c_int32 * len(out)).from_buffer(out), len(out) // 2))

    def api_host_begin(ctx, inp, rect, flags, fs, pf, hp):
        p, _k = _ctx(ctx)
        return int(lib.moy_ui_api_host_begin(p, (c.c_int32 * len(inp)).from_buffer(inp),
                                             box_of(rect), int(flags), int(fs),
                                             (c.c_int32 * len(pf)).from_buffer(pf),
                                             (c.c_int32 * len(hp)).from_buffer(hp)))

    def api_host_arm(ctx):
        p, _k = _ctx(ctx)
        return int(lib.moy_ui_api_host_arm(p))

    def api_host_whole(ctx):
        p, _k = _ctx(ctx)
        lib.moy_ui_api_host_whole(p)

    def api_keys_back(ctx):
        p, _k = _ctx(ctx)
        lib.moy_ui_api_keys_back(p)

    def api_pop_box(ctx, out):
        p, _k = _ctx(ctx)
        return bool(lib.moy_ui_api_pop_box(p, (c.c_int32 * 4).from_buffer(out)))

    def api_card_clip(ctx, r):
        p, _k = _ctx(ctx)
        v = (c.c_int32 * 4).from_buffer(r)
        lib.moy_ui_api_card_clip(p, c.addressof(v))
        return r

    def api_keep(ctx, cv, look, id_, h):
        p, _k = _ctx(ctx)
        t, keep = span(id_)
        return int(lib.moy_ui_api_keep(p, c.byref(sink(cv, look)), t, int(h)))

    def api_audit(on):
        return int(lib.moy_ui_api_audit(int(on)))

    def api_end(ctx, cv, look):
        p, _k = _ctx(ctx)
        lib.moy_ui_api_end(p, c.byref(sink(cv, look)))

    def api_table_pics(ctx, cv, look):
        p, _k = _ctx(ctx)
        lib.moy_ui_api_table_pics(p, c.byref(sink(cv, look)))

    def api_table_dist(ctx):
        p, _k = _ctx(ctx)
        return int(lib.moy_ui_api_table_dist(p))

    def api_table_part(ctx, cv, look, r):
        p, _k = _ctx(ctx)
        v = (c.c_int32 * 8).from_buffer(r)
        return int(lib.moy_ui_api_table_part(p, c.byref(sink(cv, look)), c.addressof(v)))

    def api_table_floor(ctx, r):
        p, _k = _ctx(ctx)
        v = (c.c_int32 * 4).from_buffer(r)
        return int(lib.moy_ui_api_table_floor(p, c.addressof(v)))

    def api_table_copy(ctx, r, back):
        p, _k = _ctx(ctx)
        v = (c.c_int32 * 4).from_buffer(r)
        return int(lib.moy_ui_api_table_copy(p, c.addressof(v), 1 if back else 0))

    def api_table_shift(ctx, r, back):
        p, _k = _ctx(ctx)
        v = (c.c_int32 * 5).from_buffer(r)
        return int(lib.moy_ui_api_table_shift(p, c.addressof(v), 1 if back else 0))

    def api_held(ctx, id_, key):
        p, _k = _ctx(ctx)
        t, keep = span(id_)
        if not t.n:
            keep = c.create_string_buffer(b"", 1)
            t = _Span(c.addressof(keep), 0)
        return bool(lib.moy_ui_api_held(p, t, int(key)))

    def api_held_last(ctx, id_, key):
        p, _k = _ctx(ctx)
        t, keep = span(id_)
        if not t.n:
            keep = c.create_string_buffer(b"", 1)
            t = _Span(c.addressof(keep), 0)
        return bool(lib.moy_ui_api_held_last(p, t, int(key)))

    def api_held_all(ctx):
        p, _k = _ctx(ctx)
        return bool(lib.moy_ui_api_held_all(p))

    def api_slots(ctx, cap, key):
        p, _k = _ctx(ctx)
        return int(lib.moy_ui_api_slots(p, int(cap), int(key)))

    def api_slot(ctx, name, gen, mint):
        p, _k = _ctx(ctx)
        t, keep = span(name)
        if not t.n:
            keep = c.create_string_buffer(b"", 1)
            t = _Span(c.addressof(keep), 0)
        return int(lib.moy_ui_api_slot(p, t, int(gen), 1 if mint else 0))

    def api_slot_next(ctx):
        p, _k = _ctx(ctx)
        return int(lib.moy_ui_api_slot_next(p))

    def _block(name):
        t, keep = span(name)
        if not t.n:
            keep = c.create_string_buffer(b"", 1)
            t = _Span(c.addressof(keep), 0)
        return t, keep

    def api_block_set(name, field, value):
        t, _keep = _block(name)
        return bool(lib.moy_ui_api_block_set(t, int(field), int(value) & 0xffffffff))

    def api_block_gen(name):
        t, _keep = _block(name)
        return int(lib.moy_ui_api_block_gen(t))

    def api_block_since(name, gen):
        t, _keep = _block(name)
        return int(lib.moy_ui_api_block_since(t, int(gen)))

    def api_block_key(name):
        t, _keep = _block(name)
        return int(lib.moy_ui_api_block_key(t))

    def api_table_rows(ctx):
        p, _k = _ctx(ctx)
        v = (c.c_int32 * 8)()
        if not lib.moy_ui_api_table_rows(p, v):
            return None
        return (v[0], v[1], (v[2], v[3], v[4], v[5]), v[6], v[7])

    def api_grid(ctx, cv, look, id_, w, h, o):
        p, _k = _ctx(ctx)
        s = sink(cv, look)
        t, keep = span(id_)
        if not t.n:
            keep = c.create_string_buffer(b"", 1)
            t = _Span(c.addressof(keep), 0)
        ov, ok = _over(o)
        lib.moy_ui_api_grid(p, c.byref(s), t, int(w), int(h), ov)

    def api_card(ctx, cv, look, title, sub, o):
        p, _k = _ctx(ctx)
        s = sink(cv, look)
        t, keep = span(title)
        u, keep2 = span(sub)
        ov, ok = _over(o)
        out = (c.c_int32 * 5)()
        lib.moy_ui_api_card(p, c.byref(s), t, u, ov, out)
        return tuple(out)

    def api_part(ctx, cv, look, id_, w, h, o):
        p, _k = _ctx(ctx)
        s = sink(cv, look)
        t, keep = span(id_)
        ov, ok = _over(o)
        out = (c.c_int32 * 5)()
        lib.moy_ui_api_part(p, c.byref(s), t, int(w), int(h), ov, out)
        return tuple(out)

    def api_split(ctx, cv, look, id_, sizes, n, mins, opt, out):
        p, _k = _ctx(ctx)
        t, keep = span(id_)
        sv = (c.c_int32 * len(sizes)).from_buffer(sizes)
        mv = None if mins is None else (c.c_int32 * len(mins)).from_buffer(mins)
        ov = (c.c_int32 * len(opt)).from_buffer(opt)
        wv = (c.c_int32 * len(out)).from_buffer(out)
        return int(lib.moy_ui_api_split(p, c.byref(sink(cv, look)), t, c.addressof(sv), int(n),
                                        None if mv is None else c.addressof(mv),
                                        c.addressof(ov), c.addressof(wv)))

    def api_split_at(ctx, x, y):
        p, _k = _ctx(ctx)
        return bool(lib.moy_ui_api_split_at(p, int(x), int(y)))

    def api_split_frame(ctx, words, out):
        p, _k = _ctx(ctx)
        v = (c.c_int32 * 8).from_buffer(words)
        return int(lib.moy_ui_api_split_frame(p, v[0], v[1], v[2], v[3],
                                              c.addressof(v) + 16,
                                              (c.c_int32 * 4).from_buffer(out)))

    def api_split_toward(ctx, x, y, dx, dy, out):
        p, _k = _ctx(ctx)
        return bool(lib.moy_ui_api_split_toward(p, int(x), int(y), int(dx), int(dy),
                                                (c.c_int32 * 4).from_buffer(out)))

    def api_part_at(ctx):
        p, _k = _ctx(ctx)
        v = (c.c_int32 * 2)()
        if not lib.moy_ui_api_part_at(p, v):
            return None
        return (v[0], v[1])

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

    def api_focus_ahead(ctx, id_):
        p, _k = _ctx(ctx)
        t, keep = span(id_)
        if not t.n:
            keep = c.create_string_buffer(b"", 1)
            t = _Span(c.addressof(keep), 0)
        lib.moy_ui_api_focus_ahead(p, t)

    def api_keys_taken(ctx):
        p, _k = _ctx(ctx)
        return bool(lib.moy_ui_api_keys_taken(p))

    def api_edge(ctx):
        p, _k = _ctx(ctx)
        return bool(lib.moy_ui_api_edge(p))

    def api_tap_armed(ctx):
        p, _k = _ctx(ctx)
        return bool(lib.moy_ui_api_tap_armed(p))

    def api_at(ctx, x, y, w, h):
        p, _k = _ctx(ctx)
        lib.moy_ui_api_at(p, int(x), int(y), int(w), int(h))

    def api_lost(ctx):
        p, _k = _ctx(ctx)
        return int(lib.moy_ui_api_lost(p))

    def api_dup(ctx):
        p, _k = _ctx(ctx)
        d = lib.moy_ui_api_dup(p)
        return None if d is None else d.decode("utf-8", "replace")


    # -- text views (native/moy_ui/moy_ui_text.h, ui.text_view) ---------------------
    def text_words(syn, words, tone):
        b = bytes(words)
        _check(lib.moy_ui_text_words(int(syn), b, len(b), int(tone)))

    def api_text_view(ctx, cv, look, id, lines, opt, o, err=None, find=None, into=None):
        p, _k = _ctx(ctx)
        s = sink(cv, look)
        sp, keep = span(id)
        bufs = {}

        def get(_src, i, n):
            if not 0 <= i < len(lines):
                n[0] = 0
                return None
            b = bufs.get(i)
            if b is None:
                raw = _b(lines[i])
                b = bufs[i] = (c.create_string_buffer(raw, len(raw)), len(raw))
            n[0] = b[1]
            return c.addressof(b[0])
        fn = _LINE_FN(get)
        L = _Lines(None, len(lines), fn)
        w = (c.c_int32 * _TXO_LEN)(*_TXO_DEFAULTS)
        for i, v in enumerate(list(opt)[:_TXO_LEN]):
            w[i] = int(v)
        ob, okeep = _over(o)
        e, ek = span(err or "")
        f, fk = span(find or "")
        marks = (_Span * 2)(e, f)
        out = (c.c_int32 * 2)()
        _nested(lib.moy_ui_api_text_view(p, c.byref(s), sp, c.byref(L), c.addressof(w),
                                         c.addressof(marks), ob, c.addressof(out)))
        if into is not None:
            into[0] = out[0]
            into[1] = out[1]
            return into
        return (out[0], out[1])

    def api_text_at(ctx, id_, px, py):
        p, _k = _ctx(ctx)
        t, keep = span(id_)
        out = (c.c_int32 * 2)()
        if not lib.moy_ui_api_text_at(p, t, int(px), int(py), c.addressof(out)):
            return None
        return (out[0], out[1])

    def api_text_cover(ctx, id_, y0, y1):
        p, _k = _ctx(ctx)
        t, keep = span(id_)
        lib.moy_ui_api_text_cover(p, t, int(y0), int(y1))

    def api_text_aim(ctx, id_, line, lines):
        p, _k = _ctx(ctx)
        t, keep = span(id_)
        return bool(lib.moy_ui_api_text_aim(p, t, int(line), int(lines)))

    def api_text_reveal(ctx, id_, lines, line, col):
        p, _k = _ctx(ctx)
        t, keep = span(id_)
        bufs = {}

        def get(_src, i, n):
            if not 0 <= i < len(lines):
                n[0] = 0
                return None
            b = bufs.get(i)
            if b is None:
                raw = _b(lines[i])
                b = bufs[i] = (c.create_string_buffer(raw, len(raw)), len(raw))
            n[0] = b[1]
            return c.addressof(b[0])
        fn = _LINE_FN(get)
        L = _Lines(None, len(lines), fn)
        return bool(lib.moy_ui_api_text_reveal(p, t, c.byref(L), int(line), int(col)))

    def text_tones(tones):
        t = (c.c_int16 * 6)(*[-1 if v is None else int(v) for v in list(tones)[:6]])
        lib.moy_ui_text_tones(c.addressof(t))

    def text_runs(line, syn):
        b = _b(line)
        k = int(lib.moy_ui_text_runs(b, len(b), int(syn), None, 0))
        w = (c.c_int32 * max(1, 4 * k))()
        lib.moy_ui_text_runs(b, len(b), int(syn), c.addressof(w), 4 * k)
        return list(w[:4 * k])

    for f in (api_text_view, api_text_at, api_text_cover, api_text_aim, api_text_reveal,
              text_tones, text_words, text_runs):
        setattr(m, f.__name__, f)

    for f in (api_size, api_begin, api_page, api_label, api_button, api_toggle, api_slider,
              api_list, api_group, api_value, api_choice, api_hints, api_hints_w, api_icon,
              api_progress,
              api_box, api_table, api_scroll, api_field, api_dialog, api_wash, api_wash_box,
              api_owes, api_moved, api_moving, api_busy, api_offset, api_fades,
              api_shift_plan, api_shift_break,
              api_shift_drawn, api_band, api_floor, api_floor_box, api_change_box,
              api_lag_note, api_lag, api_host_begin, api_host_arm,
              api_host_whole, api_keys_back,
              api_pop_box,
              api_card_clip, api_keep, api_audit,
              api_end,
              api_table_rows, api_table_pics, api_table_part, api_table_dist,
              api_table_floor, api_table_copy, api_table_shift, api_held, api_held_last,
              api_held_all,
              api_slots, api_slot, api_slot_next,
              api_block_set, api_block_gen, api_block_since, api_block_key,
              api_grid,
              api_card, api_part, api_part_at, api_split, api_split_at, api_split_frame,
              api_split_toward, api_popover, api_menu, api_menu_danger,
              api_text, api_card_mark, api_grid_spread, api_group_card, api_empty, api_month, api_toast,
              api_tool, api_bar, api_bar_lent, api_bar_tap, api_rule,
              api_focus, api_focus_ahead, api_keys_taken, api_edge, api_tap_armed, api_at,
              api_dup, api_lost):
        setattr(m, f.__name__, f)
    m.calls = lambda: lib.moy_ui_calls()
    m.glyph_names = lambda: glyphs
    m.glyph_id = glyph_id
    m.icon_mask = icon_mask
    m.icons_use = icons_use
    m.moy_use = moy_use
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
    m.text_cut = text_cut
    m.text_covers = text_covers
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
    m.disabled = lambda v: lib.moy_ui_set_disabled(-1 if v is None else int(v))
    m.danger_ink = lambda v: lib.moy_ui_set_danger_ink(-1 if v is None else int(v))
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
        return (d.win_r, d.card_r, d.key_r, d.corners, d.stepped, d.shadow, d.edges,
                d.alpha, d.blur, d.dx, d.dy, d.back, d.frames, d.bar_h, d.hh_h)

    m.use_depth = use_depth

    def theme(name, light):
        b = _b(name)
        row = (c.c_int32 * n_roles)()
        bad = c.c_uint32()
        rc = lib.moy_theme_resolve(b, len(b), 1 if light else 0, row, c.byref(bad))
        return tuple(row), bad.value, rc

    def theme_modes(name):
        b = _b(name)
        return int(lib.moy_theme_modes(b, len(b)))

    class _Mark(c.Structure):
        _fields_ = [("mark", c.c_int8), ("w", c.c_int8), ("gap", c.c_int8),
                    ("key_w", c.c_int8), ("c", c.c_int16), ("ink", c.c_int16),
                    ("alpha", c.c_uint8), ("declared", c.c_uint8), ("key_c", c.c_int32)]

    def theme_marks(name, light):
        b = _b(name)
        row = (c.c_int32 * n_roles)()
        lib.moy_theme_resolve(b, len(b), 1 if light else 0, row, None)
        out = (_Mark * 9)()
        warn = c.c_int()
        failed = lib.moy_theme_marks(b, len(b), 1 if light else 0, row, out, c.byref(warn))
        return (int(failed), bool(warn.value),
                tuple((m.mark, m.c, m.ink, m.w, m.gap, m.alpha, m.key_w, m.key_c) for m in out))

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
    _KINDS = ("colour", "paint", "enum", "int", "num", "bool", "text", "corners", "bits")
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
            return (rc, notes.value, None, None, None, None, None, None, None, None, None)
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
                          for lt in (False, True)),
                    theme_modes(name))
        finally:
            lib.moy_theme_probe_end()

    rdims = (c.c_int * 5).in_dll(lib, "moy_theme_custom_dims")
    theme_bytes = rdims[3]
    acc = ((c.c_uint8 * 2) * rdims[0]).in_dll(lib, "moy_theme_accents")
    accents = tuple((n, acc[i][0], acc[i][1]) for i, n in
                    enumerate(_names(lib, "moy_theme_accent_names", rdims[0])))
    pre = ((c.c_uint8 * 2) * rdims[4]).in_dll(lib, "moy_theme_presets")
    presets = tuple((n, pre[i][0], pre[i][1]) for i, n in
                    enumerate(_names(lib, "moy_theme_preset_names", rdims[4])))
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
            (i for i, a in enumerate(accents + presets) if a[1] == accent), None)
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
    m.theme_modes = theme_modes
    m.theme_marks = theme_marks
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
    m.theme_presets = lambda: presets
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

    def input_(*kind):
        if kind:
            lib.moy_ui_set_input(int(kind[0]))
        return lib.moy_ui_input()

    m.input = input_

    def hint_input(*kind):
        if kind:
            lib.moy_ui_set_hint_input(int(kind[0]))
        return lib.moy_ui_hint_input()

    m.hint_input = hint_input
    m.print_font = print_font
    m.cart_text = cart_text
    m.font_free = font_free
    m.font_info = font_info
    m.use_font = use_font
    m.font = font
    m.look = look
    m.colors = colors
    m.metrics = metrics

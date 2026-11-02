# Map (grep -n a name to jump there):
#   install          the interaction verbs set on the moy_ui module
"""The interaction verbs of the host's `moy_ui` (runtime/host/moy_ui_binding.py):
the scroll model's motion and the drag-or-tap machine, over buffers the
caller owns (ui_scroll's ScrollRegion and DragTap). `binding()` calls
`install` once. Host only.
"""

import ctypes as c



def install(m, lib, h):
    """Set the interaction verbs on module `m` over `lib`; the buffer helper."""
    r4 = h['r4']
    # -- interaction over caller-owned buffers --------------------------------

    def _buf(b):
        """A writable caller buffer's address (array or bytearray)."""
        return (c.c_char * (len(b) * b.itemsize if hasattr(b, "itemsize") else len(b))) \
            .from_buffer(b)

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

    def scroll_glide_to(st, offset, target, slide, content, extent):
        lib.moy_ui_scroll_glide_to(_buf(st), int(offset), int(target), 1 if slide else 0,
                                   int(content), int(extent))

    def scroll_fling(st, offset, vel):
        lib.moy_ui_scroll_fling(_buf(st), int(offset), float(vel))

    def scroll_step(st, offset, n, size, content, extent):
        return int(lib.moy_ui_scroll_step(_buf(st), int(offset), int(n), int(size),
                                          int(content), int(extent)))

    def scroll_land(st, offset):
        off = c.c_int32(int(offset))
        if not lib.moy_ui_scroll_land(_buf(st), c.byref(off)):
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

    for f in (scroll_stop, scroll_drag_start, scroll_drag_move, scroll_drag_end,
              scroll_tick, scroll_glide_to, scroll_fling, scroll_step, scroll_land, dragtap):
        setattr(m, f.__name__, f)
    return {'_buf': _buf}

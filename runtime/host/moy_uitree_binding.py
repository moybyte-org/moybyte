"""The UI engine (native/moy_uitree/moy_uitree.h) on the host: its share of
the host's one library (runtime/host/host_native.py), compiled beside the
toolkit whose verbs its walk calls, and its functions bound by ctypes.
`runtime/uitree/uitree_c.py` is the front end that drives it."""

import ctypes as c
import os

import face_ctypes
import native_build

_DIR = os.path.join(native_build.ROOT, "native", "moy_uitree")

PART = native_build.Part("uitree", ("moy_uitree.h", "moy_uitree_int.h", "moy_uitree.c",
                                    "moy_uitree_lang.c", "moy_uitree_walk.c", "moy_uitree_aot.c"),
                          (_DIR,))

_P, _I, _S, _Z = c.c_void_p, c.c_int, c.c_char_p, c.c_size_t
_I32 = c.POINTER(c.c_int32)

_SIGS = (
    ("uit_new", [], _P),
    ("uit_free", [_P], None),
    ("uit_error", [_P], _S),
    ("uit_error_code", [_P], _S),
    ("uit_state", [_P, _S, _I, _S, _Z], _I),
    ("uit_model", [_P, _I, _S, _Z, _S, _I, _I], _I),
    ("uit_row_of", [_P, _I, _I], _I),
    ("uit_action_row", [_P, _I, _I], _I),
    ("uit_action", [_P, _S, _S, _S, _S, _I, _I, _S, _S], _I),
    ("uit_letter", [_P, _I], _I),
    ("uit_labels", [_P, _S, _S, _Z, _S, _Z], _I),
    ("uit_focused", [_P, c.c_char_p, _Z], _I),
    ("uit_draft", [_P, _S, c.POINTER(_Z)], c.POINTER(c.c_char)),
    ("uit_stop", [_P, _I, c.c_char_p, _Z], _I),
    ("uit_leave", [_P], _I),
    ("uit_set_int", [_P, _I, c.c_int32], _I),
    ("uit_set_num", [_P, _I, c.c_double], _I),
    ("uit_set_absent", [_P, _I], _I),
    ("uit_set_text", [_P, _I, _S, _Z], _I),
    ("uit_set_model", [_P, _I, _I, _S, _Z], _I),
    ("uit_get_int", [_P, _I], c.c_int32),
    ("uit_get_text", [_P, _I, c.POINTER(_Z)], c.POINTER(c.c_char)),
    ("uit_get_cell", [_P, _I, _I, _I], c.c_char_p),
    ("uit_build_begin", [_P, _S], None),
    ("uit_node", [_P, _I, _S, _I], _I),
    ("uit_prop", [_P, _I, _S, _S, _S], _I),
    ("uit_node_action", [_P, _I, _I], _I),
    ("uit_on_draw", [_P, _P, _P], None),
    ("uit_build_end", [_P], _I),
    ("uit_open", [_P], None),
    ("uit_close", [_P], None),
    ("uit_want_focus", [_P, _S], None),
    ("uit_set_placed", [_P, _I], None),
    ("uit_set_ground", [_P, _I, _I, _I, _I], None),
    ("uit_place", [_P, _I, _I, _I, _I, _I, _I], _I),
    ("uit_rect", [_P, _S, _I32], _I),
    ("uit_cond_of", [_P, _I, _S], _I),
    ("uit_anchor", [_P, _S, _I, _I, _I, _I], None),
    ("uit_take_focused", [_P], _I),
    ("uit_back_closer", [_P, c.c_char_p, _Z], _I),
    ("uit_lent", [_P, _S, _I32, _I, c.c_char_p, _Z], _I),
    ("uit_lent_place", [_I32, _I, _I, _I, _I, _I, _I, _I32, c.POINTER(_I), _I32], _I),
    ("uit_frame", [_P, _P, _P, _I, _I, _I, _I, _I, _I, _I], _I),
    ("uit_panes_set", [_P, _I, _S, _I, c.c_uint32], None),
    ("uit_panes", [_P, _I32, _I], _I),
    ("uit_splits", [_P, _P, _P, _I, _I32], _I),
    ("uit_absent", [_P, _I], _I),
    ("uit_pane_ask", [_P, _I, _I, _I], _I),
    ("uit_next", [_P, _I32, c.c_char_p, _Z], _I),
    ("uit_trace_next", [_P, c.c_char_p, _Z], _I),
    ("uit_can", [_P, _I, _I], _I),
    ("uit_dialog", [_P], _I),
    ("uit_back_dialog", [_P, c.c_char_p, _Z], _I),
    ("uit_back_popover", [_P, c.c_char_p, _Z], _I),
    ("uit_unfocused", [_P], _I),
    ("uit_still", [_P, _P, _I, _I, _I, _I, _I, _I, _I], _I),
    ("uit_dump", [_P, c.c_char_p, _Z], _I),
    ("uit_stats", [_P, _I32], _I),
    ("uit_layout_hash", [], c.c_uint32),
    ("uit_save", [_P, c.c_char_p, _Z, _S, c.c_uint32, c.c_char_p, _Z], _I),
    ("uit_load", [_P, c.c_char_p, _Z, _S, _S], _I),
    ("uit_mem", [c.POINTER(c.c_uint32)], None),
    ("uit_mem_peak_reset", [], None),
    ("moy_ui_api_ctx", [_P, _Z], _P),
)

# the header's numbers
BOOL, INT, NUM, TEXT, ENUM, ROWOF, MODEL = range(1, 8)
PAGE, GROUP, LABEL, INFO, LINK, BUTTON, TOGGLE, ROWS, DIALOG, SEG, SLIDER, FIELD = range(1, 13)
EV_NONE, EV_ACTION, EV_WRITE = range(3)
F_DESK, F_ON_BAR, F_BAND, F_BARE, F_TRACE, F_UP, F_DOWN, F_GEN = 1, 2, 4, 8, 16, 32, 64, 128
F_PAGES_SHIFT = 8
OWE = 1 << 16
A_HOOK, A_ROLE_CONFIRM, A_ROLE_BACK, A_ROW, A_SHIFT_ENTER = 1, 2, 4, 8, 16
INPUTS = ("keys", "tdeck", "pad", "touch")


def lib():
    return face_ctypes.lib(_SIGS)


def b(s):
    return s.encode("utf-8") if isinstance(s, str) else s


def layout():
    """This engine's layout hash (uit_layout_hash)."""
    return lib().uit_layout_hash()


def _format():
    import re
    with open(os.path.join(_DIR, "moy_uitree.h")) as f:
        return int(re.search(r"#define UIT_FORMAT (\d+)", f.read()).group(1))


FORMAT = _format()              # UIT_FORMAT, read from the header the library is built from


def mem():
    """(bytes live, the most live since peak_reset(), blocks live): every
    engine's together, as the MicroPython module's `mem()`."""
    out = (c.c_uint32 * 3)()
    lib().uit_mem(out)
    return tuple(out)


def peak_reset():
    lib().uit_mem_peak_reset()


_DRAW = c.CFUNCTYPE(None, c.c_void_p, c.c_int, c.c_int, c.c_int, c.c_int, c.c_int)


class Engine:
    """One engine (`uit_t`), with the methods the MicroPython face's
    `moy_uitree.Engine` has, name for name: the front end
    (runtime/uitree/uitree_c.py) drives either."""

    def __init__(self):
        from runtime.moy_ui_face import moy_ui_sink_t
        self._L = L = lib()
        if not getattr(L, "_uit_sink", False):
            L.moy_ui_sink_row.argtypes = [c.POINTER(moy_ui_sink_t), c.c_uint32, c.c_int]
            L.moy_ui_sink_row.restype = c.c_int
            L._uit_sink = True
        self._Sink = moy_ui_sink_t
        self._e = L.uit_new()
        self._ev = (c.c_int32 * 3)()
        self._buf = c.create_string_buffer(512)
        self._lent = None
        self._pn = None
        self._draw = self._cb = self._raised = None

    def __del__(self):
        e, self._e = getattr(self, "_e", None), None
        if e:
            self._L.uit_free(e)

    free = __del__

    def close(self):
        self._L.uit_close(self._e)

    def error(self):
        return self._L.uit_error(self._e).decode("utf-8", "replace")

    def error_code(self):
        return self._L.uit_error_code(self._e).decode("ascii")

    def state(self, name, ty, values):
        return self._L.uit_state(self._e, b(name), ty, values, len(values) if values else 0)

    def model(self, slot, names, types, key):
        return self._L.uit_model(self._e, slot, names, len(names), types, len(types), key)

    def row_of(self, slot, model):
        return self._L.uit_row_of(self._e, slot, model)

    def action_row(self, a, slot):
        return self._L.uit_action_row(self._e, a, slot)

    def action(self, name, label, hint, when, off, flags, letters, icon):
        return self._L.uit_action(self._e, b(name), b(label), b(hint), b(when) if when else None,
                                  off, flags, b(letters), b(icon))

    def letter(self, ch):
        return self._L.uit_letter(self._e, ch)

    def labels(self, name, keys, words):
        return self._L.uit_labels(self._e, b(name), keys, len(keys), words, len(words))

    def focused(self):
        n = self._L.uit_focused(self._e, self._buf, 512)
        return self._buf.value.decode("utf-8") if n else ""

    def draft(self, key):
        n = c.c_size_t()
        p = self._L.uit_draft(self._e, b(key), c.byref(n))
        return c.string_at(p, n.value).decode("utf-8") if p else None

    def leave(self):
        return self._L.uit_leave(self._e)

    def stop(self, i):
        """The last frame's stop `i`: (kind, id), None past the last."""
        k = self._L.uit_stop(self._e, i, self._buf, 512)
        return None if k < 0 else (k, self._buf.value.decode("utf-8"))

    def set_int(self, slot, v):
        return self._L.uit_set_int(self._e, slot, v)

    def set_num(self, slot, v):
        return self._L.uit_set_num(self._e, slot, v)

    def set_absent(self, slot):
        return self._L.uit_set_absent(self._e, slot)

    def set_text(self, slot, v):
        raw = b(v)
        return self._L.uit_set_text(self._e, slot, raw, len(raw))

    def set_model(self, slot, n, raw):
        return self._L.uit_set_model(self._e, slot, n, raw, len(raw))

    def get_int(self, slot):
        return self._L.uit_get_int(self._e, slot)

    def get_text(self, slot):
        n = c.c_size_t()
        p = self._L.uit_get_text(self._e, slot, c.byref(n))
        return c.string_at(p, n.value).decode("utf-8") if p else ""

    def get_cell(self, slot, row, f):
        p = self._L.uit_get_cell(self._e, slot, row, f)
        return p.decode("utf-8") if p is not None else None

    def build_begin(self, page_id):
        self._L.uit_build_begin(self._e, b(page_id))

    def node(self, kind, key, parent):
        return self._L.uit_node(self._e, kind, b(key), parent)

    def prop(self, node, name, src, rep):
        return self._L.uit_prop(self._e, node, b(name), b(src), b(rep) if rep is not None else None)

    def node_action(self, node, a):
        return self._L.uit_node_action(self._e, node, a)

    def build_end(self):
        return self._L.uit_build_end(self._e)

    def open(self):
        self._L.uit_open(self._e)

    def want_focus(self, key):
        self._L.uit_want_focus(self._e, b(key) if key is not None else None)

    def frame(self, ctx, cv, look, x, y, w, h, flags, fs, inp):
        L = self._L
        buf = (c.c_char * len(ctx)).from_buffer(ctx)
        p = L.moy_ui_api_ctx(c.addressof(buf), len(ctx))
        hd, queue = cv._ui
        if queue[0] > 4:
            cv.flush_batch()
        sink = self._Sink()
        L.moy_ui_sink_row(c.byref(sink), int(hd), 0 if look is None else int(look))
        r = L.uit_frame(self._e, p, c.byref(sink), x, y, w, h, flags, fs, inp)
        del buf
        if self._raised is not None:
            x, self._raised = self._raised, None
            raise x
        return r

    def panes_set(self, du, pane_cur, mouse, clock):
        self._L.uit_panes_set(self._e, du, b(pane_cur or ""), 1 if mouse else 0, clock & 0xffffffff)

    _PANES = 2048       # the words of the panes' answers read at most

    def panes(self):
        """The last pass's panes (uit_panes), as a list of ints."""
        if self._pn is None:
            self._pn = (c.c_int32 * self._PANES)()
        n = self._L.uit_panes(self._e, self._pn, self._PANES)
        return self._pn[:n]

    def splits(self, ctx, cv, look, flags):
        """The last pass's dividers drawn into the pass open on `ctx`
        (uit_splits): its UIT_SP_* words."""
        L = self._L
        buf = (c.c_char * len(ctx)).from_buffer(ctx)
        p = L.moy_ui_api_ctx(c.addressof(buf), len(ctx))
        hd, queue = cv._ui
        if queue[0] > 4:
            cv.flush_batch()
        sink = self._Sink()
        L.moy_ui_sink_row(c.byref(sink), int(hd), 0 if look is None else int(look))
        out = (c.c_int32 * 9)()
        L.uit_splits(self._e, p, c.byref(sink), flags, out)
        del buf
        return list(out)

    def pane_ask(self, node, what, arg):
        return self._L.uit_pane_ask(self._e, node, what, arg)

    def absent(self, slot):
        return bool(self._L.uit_absent(self._e, slot))

    def still(self, ctx, x, y, w, h, flags, fs, inp):
        L = self._L
        buf = (c.c_char * len(ctx)).from_buffer(ctx)
        p = L.moy_ui_api_ctx(c.addressof(buf), len(ctx))
        r = L.uit_still(self._e, p, x, y, w, h, flags, fs, inp)
        del buf
        return bool(r)

    def on_draw(self, fn):
        """A canvas's draw, `fn(node, x, y, w, h)`, called inside the frame;
        what it raises is raised by the frame's call."""
        self._draw = fn
        self._cb = None if fn is None else _DRAW(self._tramp)
        self._L.uit_on_draw(self._e, self._cb, None)

    def _tramp(self, _ud, node, x, y, w, h):
        if self._raised is None:
            try:
                self._draw(node, x, y, w, h)
            except BaseException as e:      # noqa: B036 -- ctypes would print and drop it
                self._raised = e

    def next(self):
        """The next event: (kind, a, b, c, text), or None."""
        k = self._L.uit_next(self._e, self._ev, self._buf, 512)
        if k == EV_NONE:
            return None
        ev = self._ev
        return k, ev[0], ev[1], ev[2], self._buf.value.decode("utf-8", "replace")

    def trace_next(self):
        if self._L.uit_trace_next(self._e, self._buf, 512) < 0:
            return None
        return self._buf.value.decode("utf-8", "replace")

    def can(self, a, row):
        return self._L.uit_can(self._e, a, row)

    def dialog(self):
        return self._L.uit_dialog(self._e)

    def back_dialog(self):
        """Back on a shown dialog: (its first button's action, its key), or -1."""
        a = self._L.uit_back_dialog(self._e, self._buf, 512)
        return a if a < 0 else (a, self._buf.value.decode("utf-8"))

    def back_popover(self):
        """Back on a shown popover: (its close= action, its key), -1 where it
        names none, -2 where none shows."""
        a = self._L.uit_back_popover(self._e, self._buf, 512)
        return a if a < 0 else (a, self._buf.value.decode("utf-8"))

    def back_closer(self):
        """Back on a shown closer row (close=): (its action, its key), -1
        where its action is off, -2 where none shows."""
        a = self._L.uit_back_closer(self._e, self._buf, 512)
        return a if a < 0 else (a, self._buf.value.decode("utf-8"))

    def unfocused(self):
        return self._L.uit_unfocused(self._e)

    def set_placed(self, on):
        self._L.uit_set_placed(self._e, 1 if on else 0)

    def ground(self, x, y, w, h):
        self._L.uit_set_ground(self._e, x, y, w, h)

    def place(self, x, y, w, h, flags, fs):
        return self._L.uit_place(self._e, x, y, w, h, flags, fs)

    def rect(self, key):
        """Where node `key` was placed last, (x, y, w, h), else None."""
        out = (c.c_int32 * 4)()
        return tuple(out) if self._L.uit_rect(self._e, b(key), out) else None

    def take_focused(self):
        return self._L.uit_take_focused(self._e)

    def anchor(self, name, x, y, w, h):
        self._L.uit_anchor(self._e, b(name), x, y, w, h)

    def cond_of(self, node, name):
        """Node `node`'s condition `name` now: 1, 0, or -1 where it has none."""
        return self._L.uit_cond_of(self._e, node, b(name))

    _LENT = 32          # the cells of a lent row read at most

    def lent(self, site):
        """The row lent to `site`: [(kind, node, value index, flags, action,
        key, value, label, icon)] a cell, None where none is lent, a
        negative answer a refusal."""
        if self._lent is None:
            self._lent = ((c.c_int32 * (5 * self._LENT))(), c.create_string_buffer(4096))
        cells, text = self._lent
        n = self._L.uit_lent(self._e, b(site), cells, self._LENT, text, 4096)
        if n < 0:
            return None if n == -1 else n
        words = text.raw.split(b"\0")
        return [tuple(cells[5 * i:5 * i + 5]) + tuple(w.decode("utf-8") for w in words[4 * i:4 * i + 4])
                for i in range(n)]

    def lent_place(self, cells, x0, w, h, fs, pad):
        """The lent row's layout (uit_lent_place): `cells` (kind, flags,
        width named) each; (density, [(x, w, labelled)], hairlines' x, need)."""
        n = len(cells)
        io = (c.c_int32 * (6 * n + 1))()
        for i, (k, f, named) in enumerate(cells):
            io[6 * i:6 * i + 3] = [k, f, named]
        hair = (c.c_int32 * (n + 1))()
        nh, need = c.c_int(0), (c.c_int32 * 1)()
        d = self._L.uit_lent_place(io, n, x0, w, h, fs, pad, hair, c.byref(nh), need)
        return d, [tuple(io[6 * i + 3:6 * i + 6]) for i in range(n)], list(hair[:nh.value]), need[0]

    def dump(self):
        n = self._L.uit_dump(self._e, None, 0)
        buf = c.create_string_buffer(n + 1)
        self._L.uit_dump(self._e, buf, n + 1)
        return buf.value.decode("utf-8")

    def stats(self):
        out = (c.c_int32 * 8)()
        self._L.uit_stats(self._e, out)
        return tuple(out)

    def save(self, variant, source, front):
        """The store as a compiled page (uit_save): bytes, or an int below 0."""
        n = self._L.uit_save(self._e, None, 0, b(variant), source, front, len(front))
        if n < 0:
            return n
        buf = c.create_string_buffer(n)
        self._L.uit_save(self._e, buf, n, b(variant), source, front, len(front))
        return buf.raw

    def load(self, blob, page_id, variant):
        return self._L.uit_load(self._e, blob, len(blob), b(page_id), b(variant))

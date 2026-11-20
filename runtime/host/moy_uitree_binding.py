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
                                    "moy_uitree_lang.c", "moy_uitree_walk.c"), (_DIR,))

_P, _I, _S, _Z = c.c_void_p, c.c_int, c.c_char_p, c.c_size_t
_I32 = c.POINTER(c.c_int32)

_SIGS = (
    ("uit_new", [], _P),
    ("uit_free", [_P], None),
    ("uit_error", [_P], _S),
    ("uit_state", [_P, _S, _I, _S, _Z], _I),
    ("uit_model", [_P, _I, _S, _Z, _S, _I, _I], _I),
    ("uit_row_of", [_P, _I, _I], _I),
    ("uit_action", [_P, _S, _S, _S, _S, _I, _I, _S], _I),
    ("uit_letter", [_P, _I], _I),
    ("uit_labels", [_P, _S, _S, _Z, _S, _Z], _I),
    ("uit_focused", [_P, c.c_char_p, _Z], _I),
    ("uit_draft", [_P, _S, c.POINTER(_Z)], c.POINTER(c.c_char)),
    ("uit_stop", [_P, _I, c.c_char_p, _Z], _I),
    ("uit_leave", [_P], _I),
    ("uit_set_int", [_P, _I, c.c_int32], _I),
    ("uit_set_num", [_P, _I, c.c_double], _I),
    ("uit_set_text", [_P, _I, _S, _Z], _I),
    ("uit_set_model", [_P, _I, _I, _S, _Z], _I),
    ("uit_get_int", [_P, _I], c.c_int32),
    ("uit_get_text", [_P, _I, c.POINTER(_Z)], c.POINTER(c.c_char)),
    ("uit_build_begin", [_P, _S], None),
    ("uit_node", [_P, _I, _S, _I], _I),
    ("uit_prop", [_P, _I, _S, _S, _S], _I),
    ("uit_node_action", [_P, _I, _I], _I),
    ("uit_build_end", [_P], _I),
    ("uit_open", [_P], None),
    ("uit_want_focus", [_P, _S], None),
    ("uit_frame", [_P, _P, _P, _I, _I, _I, _I, _I, _I, _I], _I),
    ("uit_next", [_P, _I32, c.c_char_p, _Z], _I),
    ("uit_trace_next", [_P, c.c_char_p, _Z], _I),
    ("uit_can", [_P, _I, _I], _I),
    ("uit_dialog", [_P], _I),
    ("uit_back_dialog", [_P, c.c_char_p, _Z], _I),
    ("uit_unfocused", [_P], _I),
    ("uit_dump", [_P, c.c_char_p, _Z], _I),
    ("uit_stats", [_P, _I32], _I),
    ("moy_ui_api_ctx", [_P, _Z], _P),
)

# the header's numbers
BOOL, INT, NUM, TEXT, ENUM, ROWOF, MODEL = range(1, 8)
PAGE, GROUP, LABEL, INFO, LINK, BUTTON, TOGGLE, ROWS, DIALOG, SEG, SLIDER, FIELD = range(1, 13)
EV_NONE, EV_ACTION, EV_WRITE = range(3)
F_DESK, F_ON_BAR, F_BAND, F_BARE, F_TRACE, F_UP, F_DOWN, F_GEN = 1, 2, 4, 8, 16, 32, 64, 128
F_PAGES_SHIFT = 8
OWE = 1 << 16
A_HOOK, A_ROLE_CONFIRM, A_ROLE_BACK, A_ROW = 1, 2, 4, 8
INPUTS = ("keys", "tdeck", "pad", "touch")


def lib():
    return face_ctypes.lib(_SIGS)


def b(s):
    return s.encode("utf-8") if isinstance(s, str) else s


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

    def __del__(self):
        e, self._e = getattr(self, "_e", None), None
        if e:
            self._L.uit_free(e)

    def error(self):
        return self._L.uit_error(self._e).decode("utf-8", "replace")

    def state(self, name, ty, values):
        return self._L.uit_state(self._e, b(name), ty, values, len(values) if values else 0)

    def model(self, slot, names, types, key):
        return self._L.uit_model(self._e, slot, names, len(names), types, len(types), key)

    def row_of(self, slot, model):
        return self._L.uit_row_of(self._e, slot, model)

    def action(self, name, label, hint, when, off, flags, letters):
        return self._L.uit_action(self._e, b(name), b(label), b(hint), b(when) if when else None,
                                  off, flags, b(letters))

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
        return r

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

    def unfocused(self):
        return self._L.uit_unfocused(self._e)

    def dump(self):
        n = self._L.uit_dump(self._e, None, 0)
        buf = c.create_string_buffer(n + 1)
        self._L.uit_dump(self._e, buf, n + 1)
        return buf.value.decode("utf-8")

    def stats(self):
        out = (c.c_int32 * 8)()
        self._L.uit_stats(self._e, out)
        return tuple(out)

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
    ("uit_action", [_P, _S, _S, _S, _S, _I, _I, _I], _I),
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
PAGE, GROUP, LABEL, INFO, LINK, BUTTON, TOGGLE, ROWS, DIALOG = range(1, 10)
EV_NONE, EV_ACTION, EV_WRITE = range(3)
F_DESK, F_ON_BAR, F_BAND, F_BARE, F_TRACE, F_UP, F_DOWN = 1, 2, 4, 8, 16, 32, 64
F_PAGES_SHIFT = 8
OWE = 1 << 30
A_HOOK, A_ROLE_CONFIRM, A_ROLE_BACK, A_ROW = 1, 2, 4, 8
INPUTS = ("keys", "tdeck", "pad", "touch")


def lib():
    return face_ctypes.lib(_SIGS)


def b(s):
    return s.encode("utf-8") if isinstance(s, str) else s


def ctx_ptr(lib_, ctx):
    """The toolkit context's C address (a bytearray the host's AppHost holds)."""
    buf = (c.c_char * len(ctx)).from_buffer(ctx)
    p = lib_.moy_ui_api_ctx(c.addressof(buf), len(ctx))
    return p, buf

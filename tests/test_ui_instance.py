"""The toolkit's instances (native/moy_ui/moy_ui_api.h's moy_ui_inst_t).

What a frame writes outside its context -- the kept rings and faces, the
text views' records and scratch rows, the blocks of fields, the engine's
counts -- is an instance's, so a program that runs the toolkit on a task of
its own, with an instance of its own, never writes the bytes the shell's
frames write. The shell, its Python apps and the console's run share the
main one.

  * the engine's files keep no state at file scope but the main instance and
    the console's word table and tones (a scan of the C: a static that
    creeps back in is one two tasks' frames would share);
  * an instance's blocks are its own, and freeing it gives them back;
  * a context made into an instance counts its frames' fades there, not in
    the main instance.
"""

import ctypes as c
import os
import re
import tempfile
from array import array
from pathlib import Path

from runtime import ui, ui_draw
from runtime.host_canvas import make_system_canvas
from ws_helpers import build_ws, use_theme

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_DIR = os.path.join(ROOT, "native", "moy_ui")

# The engine's files: every one a frame of the public UI API runs through.
ENGINE = ("moy_ui_api.c", "moy_ui_api_kept.c", "moy_ui_api_text.c", "moy_ui_api_table.c",
          "moy_ui_api_pop.c", "moy_ui_api_host.c", "moy_ui_text.c")

# The state at file scope each may keep, and why it is no instance's.
SHARED = {
    ("moy_ui_api.c", "s_main"): "the main instance itself",
    ("moy_ui_api_text.c", "s_tones"): "the console's tones, the shell installs them",
    ("moy_ui_text.c", "s_words"): "the console's word table, the shell installs it",
}

_DECL = re.compile(r"^(?:static\s+)?(?!return\b|typedef\b|extern\b|const\b|static\s+const\b)"
                   r"[A-Za-z_][\w\s\*]*?\b(\w+)\s*(?:\[[^\]]*\])*\s*(?:=[^;]*)?;")


def _state(path):
    """The names a file declares as variables at file scope, and its
    function-local statics, that are not const."""
    out = []
    depth = 0
    for line in open(path, encoding="utf-8"):
        s = line.split("//")[0].rstrip()
        if depth == 0 and "(" not in s:
            m = _DECL.match(s)
            if m:
                out.append(m.group(1))
        elif depth > 0:
            m = re.match(r"^\s+static\s+(?!const\b)[\w\s\*]*?\b(\w+)\s*(?:\[[^\]]*\])*\s*(?:=|;)",
                         s)
            if m:
                out.append(m.group(1))
        depth += s.count("{") - s.count("}")
    return out


def test_the_engine_keeps_no_state_outside_its_instances():
    found = []
    for f in ENGINE:
        for name in _state(os.path.join(UI_DIR, f)):
            if (f, name) not in SHARED:
                found.append("%s: %s" % (f, name))
    assert not found, ("state at file scope in the engine, which every task's frames "
                       "would share: move it into moy_ui_inst_t (or the context), or "
                       "list it in SHARED with why it is the console's: %s" % found)
    for (f, name) in SHARED:
        assert name in _state(os.path.join(UI_DIR, f)), "SHARED names %s in %s, gone" % (name, f)


def _lib():
    return ui_draw._C.lib


def _inst():
    lib = _lib()
    b = (c.c_uint8 * int(lib.moy_ui_inst_size()))()
    lib.moy_ui_inst_init(b)
    return b


def test_an_instances_blocks_are_its_own():
    lib = _lib()
    a = _inst()
    try:
        ui.block_set("inst.blk", 0, 7)          # the main instance's
        g_main = ui.block_gen("inst.blk")
        assert g_main > 0
        t = _lib_span("inst.blk")
        assert lib.moy_ui_api_block_gen(a, t) == 0, "a fresh instance holds the main's block"
        assert lib.moy_ui_api_block_set(a, t, 0, 9) == 1
        assert lib.moy_ui_api_block_set(a, t, 0, 9) == 0, "a set to the same value is no change"
        assert lib.moy_ui_api_block_gen(a, t) > 0
        assert ui.block_gen("inst.blk") == g_main, "a set in another instance moved the main's"
        assert ui.block_since("inst.blk", 0) == 1
    finally:
        lib.moy_ui_inst_free(a)
    assert lib.moy_ui_api_block_gen(a, _lib_span("inst.blk")) == 0, "free kept the blocks"


def _lib_span(s):
    """moy_ui_span_t over the bytes of `s` (kept alive on the function)."""
    span = _lib().moy_ui_api_block_gen.argtypes[1]
    raw = s.encode()
    _lib_span.keep = c.create_string_buffer(raw, len(raw))
    return span(c.cast(_lib_span.keep, c.c_void_p).value, len(raw))


_TH = []


def _theme():
    if not _TH:
        _TH.append(build_ws(Path(tempfile.mkdtemp())))
    use_theme(_TH[0], "night", "dark")
    return _TH[0].theme_colors


def test_a_context_of_an_instance_counts_its_fades_there():
    lib = _lib()
    inst = _inst()
    try:
        ctx = ui_draw.ui_context()
        cb = (c.c_uint8 * len(ctx)).from_buffer(ctx)
        assert lib.moy_ui_api_ctx_in(inst, cb, len(ctx))
        del cb
        cv = make_system_canvas(320, 240)
        th = _theme()
        inp = array("i", [0] * ui_draw.UI_IN_LEN)
        ui_draw._C.api_fades(True)
        lib.moy_ui_api_fades(inst, 1)
        for _ in range(3):
            ui_draw.ui_frame(ctx, cv, th, None, inp)
            ui.page("Fades")
            for i in range(30):
                ui.button("Row %d" % i)
            ui.end()
        assert lib.moy_ui_api_fades(inst, 0) > 0, "the instance's frames drew no fade"
        assert ui_draw._C.api_fades(True) == 0, "an instance's fades were counted in the main one"
    finally:
        lib.moy_ui_inst_free(inst)

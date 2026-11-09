"""The toolkit's instances and its theme block (native/moy_ui/moy_ui_api.h's
moy_ui_inst_t, native/moy_ui/moy_ui.h's theme block).

What a frame writes outside its context -- the kept rings and faces, the
text views' records and scratch rows, the blocks of fields, the engine's
counts -- is an instance's, so a program that runs the toolkit on a task of
its own, with an instance of its own, never writes the bytes the shell's
frames write. The shell, its Python apps and the console's run share the
main one. What every instance reads and only the console writes -- the
theme -- is one block a copy of the toolkit linked into a program pulls and
installs at a begin, when the theme's generation has moved.

  * the toolkit's files keep no state at file scope that a task's frame
    writes, and every name they keep says why (a scan of the C: a static
    that creeps back in is one two tasks' frames would share); each name the
    theme block owns is one of its records;
  * an instance's blocks are its own, and freeing it gives them back;
  * a context made into an instance counts its frames' fades there;
  * an instance whose theme is pulled draws the main instance's pixels from
    the block alone, and installs one again only when the generation moves.
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

# The toolkit's C a program links: every file but the MicroPython face and
# the generated tables (constants).
NOT_LINKED = {"modmoy_ui.c": "the MicroPython face: one VM's, never linked into a program",
              "moy_ui_tables.c": "generated from style.json, constants only"}

BLOCK = "the theme block's: the console writes it between frames, a copy pulls it"
SHARED = {
    ("moy_ui_api.c", "s_main"): "the main instance itself",
    ("moy_ui.c", "s_ui"): BLOCK,
    ("moy_ui.c", "s_disabled"): BLOCK,
    ("moy_ui.c", "s_danger_ink"): BLOCK,
    ("moy_ui.c", "s_font"): BLOCK,
    ("moy_ui.c", "s_icon_font"): BLOCK,
    ("moy_ui.c", "s_icon_code"): BLOCK,
    ("moy_ui.c", "s_moy"): BLOCK,
    ("moy_ui.c", "s_moy_clear"): BLOCK,
    ("moy_ui.c", "s_calls"): "a count, a relaxed atomic add",
    ("moy_ui.c", "s_cart"): "the console's boundary, stamped on the sinks it builds; "
                            "a draw reads its sink's",
    ("moy_ui.c", "s_cart_print"): "the console's print font, put aside inside the boundary",
    ("moy_ui.c", "s_gen"): "the theme's generation, a seqlock word",
    ("moy_ui.c", "s_writing"): "the console's setters' nesting, its task's alone",
    ("moy_ui_depth.c", "s_depth"): BLOCK,
    ("moy_ui_depth.c", "s_paint"): BLOCK,
    ("moy_ui_depth.c", "s_paints"): BLOCK,
    ("moy_ui_depth.c", "s_marks"): BLOCK,
    ("moy_ui_depth.c", "s_km"): BLOCK,
    ("moy_ui_depth.c", "s_keycap"): BLOCK,
    ("moy_ui_depth.c", "s_display"): BLOCK,
    ("moy_ui_depth.c", "s_scrim_c"): BLOCK,
    ("moy_ui_depth.c", "s_scrim_a"): BLOCK,
    ("moy_ui_depth.c", "s_shown"): "the console's focus word (a window behind, a menu "
                                   "holding the keys)",
    ("moy_ui_depth.c", "s_marks_drawn"): "a count, a relaxed atomic add",
    ("moy_ui_parts.c", "s_input"): BLOCK,
    ("moy_ui_parts.c", "s_keys"): BLOCK,
    ("moy_ui_parts.c", "s_ink_top"): "a cache, one word a font slot, read and written whole",
    ("moy_ui_text.c", "s_words"): BLOCK,
    ("moy_ui_api_text.c", "s_tones"): BLOCK,
    ("moy_theme.c", "s_live"): BLOCK,
    ("moy_theme.c", "s_live_n"): BLOCK,
    ("moy_theme.c", "s_live_near"): BLOCK,
    ("moy_theme.c", "s_sheet"): BLOCK,
    ("moy_theme.c", "s_sheet_on"): BLOCK,
    ("moy_theme.c", "s_cell_set"): BLOCK,
    ("moy_theme.c", "s_slot"): "the loader's: the console's calls alone",
    ("moy_theme.c", "s_scratch"): "the loader's: the console's calls alone",
    ("moy_theme.c", "s_have"): "the loader's: the console's calls alone",
    ("moy_theme.c", "s_probe"): "the loader's: the console's calls alone",
    ("moy_theme.c", "s_placed"): "the loader's: the console's calls alone",
    ("moy_theme.c", "s_placed_near"): "the loader's: the console's calls alone",
    ("moy_theme.c", "sh"): "the backdrop's scratch: the console's calls alone",
    ("moy_theme.c", "looks"): "the loader's scratch: the console's calls alone",
}
for _n in ("s_gen", "s_gen_serial", "s_shape", "s_ctx", "s_tick", "s_serial", "s_live",
           "s_tp_on", "s_tp_clip", "s_wl"):
    SHARED[("moy_ui_wasm.c", _n)] = "the console's one compiled run, on the main instance"

_DECL = re.compile(r"^(?!(?:return|typedef|extern|enum|union|goto|break|continue|case|default)\b)"
                   r"(?!struct\s+\w+\s*\{)[A-Za-z_][^;()]*;$")


# A comment, a string or a character, left to right: what a brace in one is not.
_TOKENS = re.compile(r"//[^\n]*|/\*.*?\*/|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])+'", re.S)


def _names(decl):
    """The names a declaration (`T a = 1, *b[2];`) declares."""
    decl = re.sub(r"=\s*\{[^}]*\}", "", decl[:-1])
    decl = re.sub(r"=[^,]*", "", decl)
    decl = re.sub(r"\[[^\]]*\]", "", decl)
    pieces = decl.split(",")
    out = re.findall(r"[A-Za-z_]\w*", pieces[0])[-1:]
    for piece in pieces[1:]:
        out += re.findall(r"[A-Za-z_]\w*", piece)[:1]
    return [n for n in out if not n.isupper()]   # (a macro's line)


def _state(path):
    """The names a file declares as variables at file scope, and its
    function-local statics, that are not const."""
    src = open(path, encoding="utf-8").read()
    src = _TOKENS.sub(lambda m: "0" if m.group(0)[0] in "\"'" else
                      "\n" * m.group(0).count("\n"), src)
    out = []
    depth = 0
    pp = False                          # inside a directive's continued lines
    for line in src.split("\n"):
        s = line.strip()
        if pp or s.startswith("#"):
            pp = s.endswith("\\")
            continue
        if s.startswith(("static const ", "const ")) and not re.search(r"\*\s*(?!const)\w+\s*;", s):
            pass                        # (a constant; a pointer to const is state)
        elif s.startswith("static inline "):
            pass
        elif "(" not in s and ")" not in s:
            if depth == 0:
                m = _DECL.match(s)
                if m:
                    out += _names(s)
            elif s.startswith("static ") and not s.startswith("static const"):
                m = _DECL.match(s)
                if m:
                    out += _names(s)
        depth += s.count("{") - s.count("}")
    return out


def _sources():
    return sorted(f for f in os.listdir(UI_DIR) if f.endswith(".c") and f not in NOT_LINKED)


def test_the_toolkit_keeps_no_state_a_frame_writes_outside_its_instances():
    found = []
    for f in _sources():
        for name in _state(os.path.join(UI_DIR, f)):
            if (f, name) not in SHARED:
                found.append("%s: %s" % (f, name))
    assert not found, ("state at file scope in the toolkit, which every task's frames "
                       "would share: move it into moy_ui_inst_t (or the context, or the "
                       "sink), or list it in SHARED with why it is not a frame's: %s" % found)
    for (f, name) in SHARED:
        assert name in _state(os.path.join(UI_DIR, f)), "SHARED names %s in %s, gone" % (name, f)


def test_each_name_the_block_owns_is_one_of_its_records():
    for (f, name), why in SHARED.items():
        if why != BLOCK:
            continue
        src = open(os.path.join(UI_DIR, f), encoding="utf-8").read()
        m = re.search(r"\nint moy_ui_recs_\w+\(moy_ui_rec_t \*r\) \{\n(.*?)\n\}\n", src, re.S)
        assert m, "%s keeps the theme block's %s and has no records function" % (f, name)
        assert re.search(r"\b%s\b" % name, m.group(1)), (
            "%s's %s is the theme block's but no record of it: a copy would never "
            "install it" % (f, name))


def _lib():
    return ui_draw._C.lib


def _inst():
    lib = _lib()
    b = (c.c_uint8 * int(lib.moy_ui_inst_size()))()
    lib.moy_ui_inst_init(b)
    return b


def _lib_span(s):
    """moy_ui_span_t over the bytes of `s` (kept alive on the function)."""
    span = _lib().moy_ui_api_block_gen.argtypes[1]
    raw = s.encode()
    _lib_span.keep = c.create_string_buffer(raw, len(raw))
    return span(c.cast(_lib_span.keep, c.c_void_p).value, len(raw))


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


_TH = []


def _ws():
    if not _TH:
        _TH.append(build_ws(Path(tempfile.mkdtemp())))
    return _TH[0]


def _theme(name="night", variant="dark"):
    use_theme(_ws(), name, variant)
    return _ws().theme_colors


def _ctx_in(inst):
    ctx = ui_draw.ui_context()
    cb = (c.c_uint8 * len(ctx)).from_buffer(ctx)
    assert _lib().moy_ui_api_ctx_in(inst, cb, len(ctx))
    del cb
    return ctx


def test_a_context_of_an_instance_counts_its_fades_there():
    lib = _lib()
    inst = _inst()
    try:
        ctx = _ctx_in(inst)
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


# -- the theme block ------------------------------------------------------------------

ITEMS = ["One", "Two", "Three"]


def _resolve(th):
    """The console resolves `th` into look 0, the look the pages below draw
    in (a theme's look slot is the Python face's choice, not the block's)."""
    ui_draw._C.look(0, ui_draw._pack(th))


def _page(ctx, cv, inp):
    ui_draw.ui_frame(ctx, cv, None, None, inp)
    ui.page("Block")
    ui.group("Values")
    ui.toggle("Sound", True)
    ui.slider("Volume", 4, 0, 10)
    ui.list("Mode", ITEMS, 1)
    ui.cycle("Theme", ITEMS, 0)
    ui.progress("Disk", 30, 100)
    ui.button("Save", "primary")
    ui.button("Delete", "danger")
    ui.text_view("tv", ["def f(x):", "    return x + 1"])
    ui.end()


def _pixels(cv):
    return bytes(cv._buf)


def _block_colours():
    pal = (c.c_uint16 * 64)()
    _lib().moy_ui_theme_pal(pal)
    return list(pal)


def test_a_pulled_theme_draws_the_main_instances_pixels_and_moves_with_its_generation():
    from runtime.host import glass_binding
    lib = _lib()
    inp = array("i", [0] * ui_draw.UI_IN_LEN)
    # slate and berry load the same font files, so the ids the block names
    # stay loaded across the switch (a copy loads its own: the font row)
    _resolve(_theme("slate", "dark"))
    cv = make_system_canvas(320, 240)
    _page(ui_draw.ui_context(), cv, inp)
    _page(ui_draw.ui_context(), cv, inp)
    want = _pixels(cv)
    size = int(lib.moy_ui_theme_size())
    block = (c.c_uint8 * size)()
    g = lib.moy_ui_theme_pull(0, block, size)
    assert g != 0 and g % 2 == 0, "a settled theme's pull wrote no block"
    assert lib.moy_ui_theme_pull(g, block, size) == 0, "an unmoved theme pulled again"
    assert lib.moy_ui_theme_pull(0, block, size - 4) == 0, "a short buffer took a block"
    slate_pal = _block_colours()

    _resolve(_theme("berry", "dark"))
    assert lib.moy_ui_theme_gen() != g, "a theme change left the generation where it was"
    cv2 = make_system_canvas(320, 240)
    _page(ui_draw.ui_context(), cv2, inp)
    assert _pixels(cv2) != want, "the other theme draws slate's pixels: the test proves nothing"

    served = {"gen": g, "asks": [], "gave": 0}

    @c.CFUNCTYPE(c.c_uint32, c.c_uint32, c.c_void_p, c.c_size_t)
    def pull(have, buf, cap):
        served["asks"].append(have)
        if have == served["gen"] or cap < size:
            return 0
        c.memmove(buf, block, size)
        served["gave"] += 1
        return served["gen"]

    inst = _inst()
    mine = (c.c_uint8 * size)()
    try:
        lib.moy_ui_inst_pull(inst, c.cast(pull, c.c_void_p), mine, size)
        ctx = _ctx_in(inst)
        cv3 = make_system_canvas(320, 240)
        # a copy's own surface row takes the block's colours (moy_ui_theme_pal)
        _page(ctx, cv3, inp)
        assert served["gave"] == 1 and served["asks"] == [0]
        assert _block_colours() == slate_pal, "the block did not carry the theme's colours"
        glass_binding.block(_block_colours())
        _page(ctx, cv3, inp)
        _page(ctx, cv3, inp)
        assert _pixels(cv3) == want, "the pulled theme drew other pixels than the main instance"
        assert served["asks"] == [0, g, g] and served["gave"] == 1, (
            "an instance pulled again with its generation unmoved: %r" % served)
        served["gen"] = g + 2
        _page(ctx, cv3, inp)
        assert served["gave"] == 2 and served["asks"][-1] == g, "a moved generation went unpulled"
        _page(ctx, cv3, inp)
        assert served["asks"][-1] == g + 2 and served["gave"] == 2
    finally:
        lib.moy_ui_inst_free(inst)
        _theme("night", "dark")


def test_a_block_of_another_layout_installs_nothing():
    lib = _lib()
    _theme("night", "dark")
    size = int(lib.moy_ui_theme_size())
    block = (c.c_uint8 * size)()
    assert lib.moy_ui_theme_pull(0, block, size) != 0
    g = lib.moy_ui_theme_gen()
    bad = bytearray(block)
    bad[4] ^= 0xFF                              # the layout word
    raw = (c.c_uint8 * size).from_buffer(bad)
    assert lib.moy_ui_theme_put(raw, size) != 0
    assert lib.moy_ui_theme_put(block, size - 1) != 0
    assert lib.moy_ui_theme_gen() == g, "a refused block moved the generation"
    assert lib.moy_ui_theme_put(block, size) == 0

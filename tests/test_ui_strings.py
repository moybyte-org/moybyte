"""The strings C draws are data (docs/kernel_toolkit_2026-10.md §2.3, §10
"strings are data"): native/moy_spine's table, read through moy_str.

* A language pack is untrusted: an entry whose conversions differ from
  English's is refused and the id reads English.
* Under a pseudo-locale every piece moy_chrome builds and every recovery and
  web-console view the kernel draws carries the pack's words, never English:
  no LIT op, no English literal left in C.
* The source ratchet: no string literal reaches the raster in native/, no
  call site passes MOY_TEXT_LIT, and the MOY_TEXT_PY call sites -- text a
  runtime passed in, which moves with the Python strings under the owner's
  2026-10-11 ratchet -- only ever come down.
"""

import ctypes
import json
import os
import re

import pytest

from runtime import moy_play

import test_moy_kernel as tk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STRINGS = os.path.join(ROOT, "native", "moy_spine", "strings", "en.json")
TABLE, PY, DATA, LIT = range(4)
CONV = re.compile(r"(%(?:08x|[sdu]))")


def _en():
    with open(STRINGS) as f:
        return json.load(f)["strings"]


def pseudo(text):
    """Every letter's case swapped and the whole bracketed, conversions kept."""
    parts = CONV.split(text)
    return "<" + "".join(p if CONV.fullmatch(p) else p.swapcase() for p in parts) + ">"


def _fragments():
    """English's words as they would show in a drawn op: each run between
    conversions, four bytes or more, that the pseudo-locale changes."""
    out = set()
    for text in _en().values():
        for p in CONV.split(text):
            if not CONV.fullmatch(p) and len(p.strip()) >= 4 and p.swapcase() != p:
                out.add(p.strip())
    return out


def _set_pack(lib, pack):
    lib.moy_str_set.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_size_t]
    lib.moy_str_id.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
    lib.moy_str.restype = ctypes.c_char_p
    rcs = {}
    for sid, text in pack.items():
        b = sid.encode()
        i = lib.moy_str_id(b, len(b))
        assert i >= 0, sid
        t = text.encode()
        rcs[sid] = lib.moy_str_set(i, t, len(t))
    return rcs


def _str(lib, sid):
    b = sid.encode()
    return lib.moy_str(lib.moy_str_id(b, len(b))).decode()


@pytest.fixture
def chrome():
    lib = moy_play._clib()
    yield lib
    lib.moy_str_reset()


@pytest.fixture
def kernel():
    lib = tk._lib()
    yield lib
    lib.moy_str_reset()


# --- the pack ---------------------------------------------------------------------

def test_a_pack_with_other_conversions_reads_english(chrome):
    en = _en()
    rcs = _set_pack(chrome, {
        "say.crash_line": "Line %s stopped your game.",       # %d -> %s
        "recovery.pc": "PC %08x",                            # one conversion short
        "fit.needs": "%s %s %s %s %u",                       # one too many
        "say.stuck_line": "Stuck at %d.",                    # the same: taken
        "chrome.hint_home": "HALTEN",                        # none either side
        "stuck.text": "100%% stuck after %u s",              # a literal % is no conversion
    })
    assert rcs == {"say.crash_line": 1, "recovery.pc": 1, "fit.needs": 1,
                   "say.stuck_line": 0, "chrome.hint_home": 0, "stuck.text": 0}
    for sid in ("say.crash_line", "recovery.pc", "fit.needs"):
        assert _str(chrome, sid) == en[sid]
    assert moy_play.chrome_say_title(0, 12) == en["say.crash_line"] % 12
    assert moy_play.chrome_say_title(1, 12) == "Stuck at 12."
    hint = moy_play.chrome_panel(320, 240, False, "", "", True)
    assert "HALTEN" in [o[7] for o in hint if o[0] == moy_play.CH_TEXT]
    # a refused entry replaces nothing a pack had taken before it
    assert _set_pack(chrome, {"say.stuck_line": "Stuck %s"}) == {"say.stuck_line": 1}
    assert _str(chrome, "say.stuck_line") == en["say.stuck_line"]
    chrome.moy_str_reset()
    assert _str(chrome, "chrome.hint_home") == en["chrome.hint_home"]


def test_the_face_reads_the_table_and_refuses_a_broken_entry():
    from runtime import ui_draw
    en = _en()
    assert ui_draw.s("recovery.title") == en["recovery.title"]
    try:
        assert ui_draw._C.str_set("web.pin", "PIN") is False
        assert ui_draw.s("web.pin") == en["web.pin"]
        assert ui_draw._C.str_set("web.pin", "CODE %s") is True
        assert ui_draw.s("web.pin") == "CODE %s"
        with pytest.raises(KeyError):
            ui_draw.s("no.such_id")
    finally:
        ui_draw._C.str_reset()
    assert ui_draw.s("web.pin") == en["web.pin"]


# --- the pseudo-locale ------------------------------------------------------------

def _chrome_screens():
    """Every piece moy_chrome builds, each text path it has: (name, ops)."""
    out = []
    for notice in (False, True):
        for compiled in (False, True):
            out.append(("panel", moy_play.chrome_panel(320, 240, notice, "", "", compiled)))
    out.append(("panel_py", moy_play.chrome_panel(320, 240, False, "zz", "qq", False)))
    out.append(("toast", moy_play.chrome_toast("qq", "gear")))
    out.append(("banner", moy_play._chrome("moy_chrome_banner", 320, 1, 18, b"qq", b"zz", True)))
    out.append(("about", moy_play._chrome("moy_chrome_about", 320, 240, 1, None)))
    out.append(("about_ver", moy_play._chrome("moy_chrome_about", 320, 240, 1, b"9.9")))
    out.append(("strip", moy_play._chrome("moy_chrome_strip_crash", 320, 1, b"12:00", b"wifi3")))
    return out


def _srcs(name, *args):
    """The (src, text) of each TEXT op a piece builds."""
    d = moy_play._clib()
    lst = moy_play._ChList()
    getattr(d, name)(ctypes.cast(ctypes.byref(lst), ctypes.c_void_p), *args)
    return [(lst.op[i].src, lst.text[lst.op[i].s:lst.op[i].s + lst.op[i].n].decode())
            for i in range(lst.n) if lst.op[i].op == moy_play.CH_TEXT]


def test_under_a_pseudo_locale_the_chrome_draws_no_english(chrome):
    en = _en()
    assert set(_set_pack(chrome, {k: pseudo(v) for k, v in en.items()}).values()) == {0}
    frags = _fragments()
    for name, ops in _chrome_screens():
        texts = [o[7] for o in ops if o[0] == moy_play.CH_TEXT]
        for t in texts:
            hit = [f for f in frags if f in t]
            assert not hit, "%s draws English %r in %r" % (name, hit, t)
    # each text the chrome forms is the pack's
    assert moy_play.chrome_say_title(0, 7) == pseudo(en["say.crash_line"]) % 7
    assert moy_play.chrome_fit_text("", 5 << 20, 0, 1 << 20, 0) == (
        pseudo(en["fit.needs"]) % (pseudo(en["fit.this_game"]),
                                   pseudo(en["fit.megabytes"]) % (5, 0),
                                   pseudo(en["fit.megabytes"]) % (1, 0),
                                   pseudo(en["fit.restart"])))
    assert moy_play.chrome_newer_text("", "x") == pseudo(en["newer.text"]) % (
        pseudo(en["fit.this_game"]), "x")
    stuck = moy_play._say("moy_chrome_stuck_text", 3000)
    assert stuck == pseudo(en["stuck.text"]) % 3
    # the runaway watch's words are recognised in the pack's spelling
    assert moy_play.chrome_crash_title(stuck, 0) == pseudo(en["say.stuck"])


def test_no_chrome_op_is_a_literal_and_the_sources_are_the_callers(chrome):
    srcs = (_srcs("moy_chrome_panel", 320, 240, False, b"", b"", False)
            + _srcs("moy_chrome_panel", 320, 240, False, b"zz", b"qq", True)
            + _srcs("moy_chrome_panel", 320, 240, False, b"", b"", True)
            + _srcs("moy_chrome_toast", b"qq", b"gear")
            + _srcs("moy_chrome_about", 320, 240, 1, b"9.9"))
    assert all(s != LIT for s, _ in srcs)
    by = {t: s for s, t in srcs}
    en = _en()
    assert by[en["say.crash"]] == TABLE and by[en["chrome.no_text"]] == TABLE
    assert by[en["chrome.hint_home"]] == TABLE and by[en["about.close"]] == TABLE
    assert by["zz"] == PY and by["qq"] == PY and by["9.9"] == PY


def test_a_literal_op_is_never_rastered(chrome):
    """moy_chrome's raster draws through moy_text, which refuses a LIT op."""
    lst = moy_play._ChList()
    p = ctypes.cast(ctypes.byref(lst), ctypes.c_void_p)
    chrome.moy_chrome_about(p, 320, 240, 1, b"9.9")
    pal = (ctypes.c_uint16 * 64)(*range(1, 65))
    for src, drawn in ((TABLE, True), (LIT, False)):
        for i in range(lst.n):
            if lst.op[i].op == moy_play.CH_TEXT:
                lst.op[i].src = src
            else:
                lst.op[i].op = 0                       # only the text
        buf = (ctypes.c_uint16 * (320 * 240))()
        chrome.moy_chrome_raster(p, ctypes.cast(buf, ctypes.c_void_p), 320, 240,
                                 ctypes.cast(pal, ctypes.c_void_p), 1)
        assert any(buf) == drawn


def _lines(v):
    return [v.line[i].value.decode() for i in range(v.nlines)] + [v.hint.decode()]


def test_under_a_pseudo_locale_the_kernels_screens_draw_no_english(kernel):
    en = _en()
    _set_pack(kernel, {k: pseudo(v) for k, v in en.items()})
    frags = _fragments()
    views = []
    for why in (tk.WHY_VM_START, tk.WHY_BOOT_LOOP, tk.WHY_HEAP):
        v = tk.View()
        rec = tk._record(task=b"", what=b"")
        kernel.moy_recovery_view(ctypes.byref(v), why, ctypes.byref(rec), None, b"")
        views.append(v)
    v = tk.View()
    kernel.moy_web_screen_view(ctypes.byref(v), None, b"1234", b"9.9")
    views.append(v)
    for v in views:
        for t in _lines(v):
            assert not [f for f in frags if f in t], t
    assert _lines(views[0])[:2] == [pseudo(en["recovery.not_started"]),
                                    pseudo(en["recovery.last"]) % (
                                        "fault", pseudo(en["recovery.unknown"]))]
    # the serial protocol's names stay English whatever the pack draws
    kernel.moy_recovery_choice_name.restype = ctypes.c_char_p
    assert [kernel.moy_recovery_choice_name(i).decode() for i in range(3)] == [
        en["recovery.retry"], en["recovery.safe"], en["recovery.repl"]]


# --- the source ratchet -----------------------------------------------------------

# Where text reaches a raster in native/: moy_chrome's display list, the
# recovery raster and the lines it draws, and the one text path.
_RASTER_CALL = re.compile(
    r"\b(?:print\(l,\s*MOY_TEXT_\w+,|text\(fb,\s*g,[^;]*?|moy_text\([^;]*?|add\(v,"
    r"|moy_crash_strcpy\(v->hint,[^,]*,|render_plain\([^,]*,[^,]*,)\s*\"")
_SKIP = ("moy_wasm", "libmoy", "moy_lua", "moycore")


def _native_sources():
    for d, dirs, files in os.walk(os.path.join(ROOT, "native")):
        dirs[:] = [x for x in dirs if x not in _SKIP and not x.startswith(".")]
        for f in files:
            if f.endswith((".c", ".h")):
                p = os.path.join(d, f)
                with open(p) as fh:
                    yield os.path.relpath(p, ROOT), re.sub(r"//[^\n]*", "", fh.read())


def test_no_string_literal_reaches_the_raster():
    for rel, src in _native_sources():
        hit = _RASTER_CALL.search(src)
        assert hit is None, "%s passes a literal to the raster: %r" % (rel, hit.group(0))


def test_no_call_site_passes_a_literal_source():
    for rel, src in _native_sources():
        if rel == "native/moy_gfx/moy_text.h":
            continue
        assert "MOY_TEXT_LIT" not in src, rel


# Each file's MOY_TEXT_PY sites: text a runtime passed in. A count only comes
# down, as its strings move to the table.
PY_SITES = {
    "native/moy_gfx/modmoy_gfx.c": 2,           # the system canvases' print
    "native/moy_gfx/moy_gfx_kernels.c": 1,      # the `text` kernel
    "native/moy_ui/moy_ui_draw.c": 1,           # every primitive's label
    "native/moy_play/moy_chrome.c": 9,          # titles, texts, the clock, menu rows
}


def test_the_py_sites_only_come_down():
    got = {}
    for rel, src in _native_sources():
        n = src.count("MOY_TEXT_PY")
        if n and rel != "native/moy_gfx/moy_text.h":
            got[rel] = n
    for rel, n in got.items():
        assert n <= PY_SITES.get(rel, 0), (
            "%s: %d MOY_TEXT_PY sites, the ratchet holds %d" % (rel, n, PY_SITES.get(rel, 0)))
    assert got == PY_SITES, "a PY site moved to the table: lower PY_SITES to %r" % got


# The Python-fed words that moved to the table: the sysmenu's rows and the
# notices the runtime raises. A literal back in its file is a string the
# language pack cannot reach.
PY_FED_MOVED = {
    "runtime/console_notices.py": ["MOYBYTE UPDATED", "UPDATE UNDONE", '"now %s"',
                                   '"still on %s"', '"CART"', "RESTART MOYBYTE",
                                   "DELETE CART", '"SYSTEM"', "CLEAR SEARCH",
                                   '"SEARCH"', '"SETTINGS"', '"ABOUT"', '"SLEEP"'],
    "runtime/device_boot.py": ["IT CRASHED LAST TIME"],
    "runtime/cart_manager.py": ["the store is full"],
}


def test_the_python_fed_words_read_the_table():
    for rel, words in PY_FED_MOVED.items():
        with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
            src = f.read()
        for w in words:
            q = w if w.startswith('"') else '"%s' % w
            assert q not in src, "%s holds the literal %s: use ui_draw.s(id)" % (rel, w)

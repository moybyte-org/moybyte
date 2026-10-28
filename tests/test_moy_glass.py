"""The glass (native/moy_glass, docs/kernel_survival_2026-10.md section 3):
its tables through the host's ctypes binding, the fuzz walk under the
sanitizers, and the draw gates on the VM a board runs -- C objects that check
their canvas's row by slot and generation before every op.

The table is one per image, so a test here names owners of its own and
compares counts before and after rather than absolute ones.
"""

import os
import shutil
import subprocess

import pytest

from runtime import glass_binding as g
from unix_mp import require_unix_mp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GLASS = os.path.join(ROOT, "native", "moy_glass")
SPINE = os.path.join(ROOT, "native", "moy_spine")


def _pooled(n):
    return sum(1 for h in g.rows(-1) if g.row(h)[0] == n)


def test_an_owner_ends_with_every_loan_and_its_generation_refuses_the_old():
    o = g.owner("t_owner")
    a = g.buf(4000, g.ROLE_LAYER, o)
    b = g.buf(1000, g.ROLE_BAKE, o)
    assert sorted(g.rows(o)) == sorted([a.h, b.h])
    assert g.row(a.h)[3] == o and g.row(a.h)[5] == g.CLASS_CART
    g.owner_end(o)
    assert not a.live and not b.live
    with pytest.raises(ValueError):
        g.owner_end(o)                  # a recycled lifetime returns nothing
    o2 = g.owner("t_owner")
    assert o2 != o
    with pytest.raises(ValueError):
        g.buf(16, g.ROLE_LAYER, o)      # nor lends to a dead one
    g.owner_end(o2)


def test_a_lent_layer_waits_in_the_pool_and_comes_out_zeroed():
    n = 4096 + 64
    o = g.owner("t_pool")
    a = g.buf(n, g.ROLE_LAYER, o)
    a.view[:4] = b"\xff\xff\xff\xff"
    before = _pooled(n)
    g.reclaim(o)
    assert _pooled(n) == before + 1
    b = g.buf(n, g.ROLE_LAYER, o)
    assert b.origin == g.ORIGIN_POOL and bytes(b.view[:4]) == b"\0\0\0\0"
    assert _pooled(n) == before
    g.owner_end(o)


def test_an_unlent_buffer_is_freed_never_pooled():
    n = 4096 + 128
    before = _pooled(n)
    live = g.live_bytes()
    a = g.buf(n, g.ROLE_LAYER)
    a.release()
    assert _pooled(n) == before and g.live_bytes() == live


def test_the_pool_is_bounded_and_the_bound_evicts():
    st = g.stats()
    try:
        g.set_pool_bound(0)
        assert g.stats()[3] == 0 and g.stats()[2] == 0
        o = g.owner("t_bound")
        held = g.buf(2048, g.ROLE_LAYER, o)
        g.owner_end(o)
        assert not held.live
        assert g.stats()[2] == 0, "above the bound a released row is freed"
        g.set_pool_bound(4096)
        o = g.owner("t_bound")
        held = [g.buf(2048, g.ROLE_LAYER, o) for _ in range(3)]
        g.owner_end(o)
        assert not any(b.live for b in held)
        assert g.stats()[3] == 4096 and g.stats()[2] == 2
    finally:
        g.set_pool_bound(st[4])


def test_the_census_split_counts_heap_rows_and_owner_classes():
    k0, c0 = g.stats()[7], g.stats()[8]
    o = g.owner("t_split")
    a = g.buf(300, g.ROLE_SCRATCH)
    b = g.buf(500, g.ROLE_LAYER, o)
    assert g.stats()[7] == k0 + 300 and g.stats()[8] == c0 + 500
    assert g.stats()[5] == 0, "the host never falls back to the heap"
    a.release()
    g.owner_end(o)
    assert not b.live


def test_a_canvas_row_holds_the_draw_state_the_gates_read():
    cv = g.Canvas(40, 30)
    assert list(cv.state)[:9] == [0, 0, 0, 0, 40, 30, 40, 30, 1]
    cv.state[0] = 7
    cv.pal[3] = 0xBEEF
    again = g.Canvas(8, 8)
    assert cv.state[0] == 7 and cv.pal[3] == 0xBEEF, "a row never moves"
    h = cv.h
    cv.release()
    assert again.h != h
    again.release()


def test_the_fuzz_walk_holds_under_the_sanitizers(tmp_path):
    cc = shutil.which(os.environ.get("CC", "cc"))
    if cc is None:
        pytest.skip("no C compiler")
    exe = str(tmp_path / "fuzz_glass")
    build = subprocess.run(
        [cc, "-std=c99", "-g", "-O1", "-fsanitize=address,undefined",
         "-fno-sanitize-recover=all", "-I", GLASS, "-I", SPINE,
         os.path.join(GLASS, "fuzz_glass.c"), os.path.join(GLASS, "moy_glass.c"),
         os.path.join(SPINE, "moy_htab.c"), "-o", exe],
        capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    out = subprocess.run([exe, "1", "4000", "12"], capture_output=True,
                         text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]


GATES = r'''import sys
sys.path.insert(0, @RUNTIME@)
sys.path.insert(0, @DEVICE@)
sys.path.insert(0, @STAGE@)
import moy_gfx
import moy_glass
import device_canvas as dc


class Comp:
    def __init__(self, w, h):
        self._w, self._h = w, h
        self._buf = bytearray(w * h * 2)

    def size(self):
        return (self._w, self._h)

    def framebuffer(self):
        return self._buf

    def gfx(self):
        return moy_gfx


cv = dc.DeviceCanvas(Comp(32, 16))
for name in ("rect", "rectb", "print", "pix"):
    print("verb", name, type(getattr(cv, name)).__name__)
lay = cv.new_layer(16, 8)
lay.rect(0, 0, 4, 4, 8)
lay._crow.release()
try:
    lay.rect(0, 0, 4, 4, 8)
    print("stale drew")
except ValueError as e:
    print("stale refused")
print("DONE")
'''


def test_the_four_hot_verbs_are_c_gates_over_a_checked_row(tmp_path):
    """No Python frame per draw verb on a gated canvas: rect, rectb, print
    and pix are moy_gfx's C gate objects, and a gate whose canvas row has
    been released refuses instead of drawing (an index and a generation
    compare in the draw context)."""
    exe = require_unix_mp("moy_gfx", "moy_glass",
                          why="the draw gates over canvas rows (section 3.1)")
    stage = tmp_path / "stage"
    stage.mkdir()
    shutil.copy(os.path.join(ROOT, "runtime", "font.py"), stage / "moy_font.py")
    shutil.copy(os.path.join(ROOT, "runtime", "moy_image.py"), stage / "moy_image.py")
    src = (GATES.replace("@DEVICE@", repr(os.path.join(ROOT, "device")))
           .replace("@RUNTIME@", repr(os.path.join(ROOT, "runtime")))
           .replace("@STAGE@", repr(str(stage))))
    script = tmp_path / "gates.py"
    script.write_text(src)
    out = subprocess.run([exe, str(script)], capture_output=True, text=True,
                         timeout=120)
    assert out.returncode == 0, out.stderr or out.stdout
    lines = out.stdout.split("\n")
    for name in ("rect", "rectb", "print", "pix"):
        assert "verb %s draw_gate" % name in lines, out.stdout
    assert "stale refused" in lines and lines[-2] == "DONE"


def test_device_canvas_defines_no_python_twin_of_the_glass():
    import re
    src = open(os.path.join(ROOT, "device", "device_canvas.py")).read()
    for gone in ("_LAYER_POOL", "(?<!MAX)_LENT_BAKES", r"\b_GLASS\b",
                 "MAP_AUTO_CACHE", "_MaskedRegion", "malloc_dma", "set_pump",
                 r"set_buf\("):
        assert not re.search(gone, src), gone


def test_a_canvas_attribute_map_is_never_left_near_full():
    """MicroPython grows an attribute map only when it is FULL, and every
    method call misses the instance map before it reaches the class: a canvas
    whose map sits full probes the whole map per verb (the T-Deck's circ went
    82 -> 107 us/op at 71 of 73 slots). _room keeps it at most 4/5 full."""
    from runtime import host_canvas
    import device_canvas as dc
    for cv in (host_canvas.make_canvas(64, 32),
               host_canvas.make_system_canvas(64, 32),
               host_canvas.make_canvas(64, 32).new_layer(8, 8)):
        n = len(cv.__dict__)
        size = next(s for s in dc._MAP_SIZES if s >= n)
        assert n * 5 <= size * 4, (type(cv).__name__, n, size)
    # The classes an instance store looks through first (the T-Deck's fillp
    # went 1x -> 1.2x per oval_p at 73 of 73 slots in DeviceCanvas's map).
    for k in (dc.DeviceCanvas, dc.SystemCanvas, dc._LayerComp):
        n = dc._map_len(k)
        size = next(s for s in dc._MAP_SIZES if s >= n)
        assert n * 5 <= size * 4, (k.__name__, n, size)

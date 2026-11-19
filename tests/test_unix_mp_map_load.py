"""The board-model MicroPython grows a hash map at three quarters full.

Every console board's MicroPython takes tools/patch_map_load.py: a hash table
above twelve slots grows while a quarter of it is still empty, so a lookup that
misses (each method call on an instance, each builtin a module names) meets an
empty slot within a few probes instead of walking most of the table. The board
model is where the console's frames are measured, so it must grow its maps the
same way, and `make unix-micropython` patches its tree.

What a map holds is read from the heap: a dict built by inserting keys one at a
time, its outgrown tables collected, holds the table it ended on.
"""

import subprocess

from unix_mp import require_unix_mp

PROBE = r"""
import gc
def held(n):
    gc.collect()
    a = gc.mem_alloc()
    d = {}
    for i in range(n):
        d[i] = i
    gc.collect()
    b = gc.mem_alloc()
    return b - a
empty = held(0)
print(held(12) - empty, held(46) - empty)
"""


def _table_bytes():
    exe = require_unix_mp(
        board_model=True,
        why="The console boards' map growth is modelled only on the board "
            "model's MicroPython.")
    out = subprocess.run([exe, "-c", PROBE], capture_output=True, text=True,
                         timeout=60)
    assert out.returncode == 0, out.stderr
    return [int(v) for v in out.stdout.split()]


def test_a_map_above_twelve_slots_keeps_a_quarter_of_them_empty():
    _small, big = _table_bytes()
    # 8 bytes a slot on the 32-bit model: stock ends 46 keys in 47 slots.
    assert big >= 46 * 8 * 4 // 3, "a 46-key dict holds %d bytes of table" % big


def test_a_map_of_twelve_slots_or_fewer_grows_as_stock_does():
    small, _big = _table_bytes()
    assert small <= 12 * 8, "a 12-key dict holds %d bytes of table" % small

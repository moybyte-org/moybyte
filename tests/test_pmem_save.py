"""A cart's pmem saves never stall play (owner, 2026-11-18).

`pmem(i, v)` only moves the RAM copy; the frame's tail hands the cells to the
store's saver (native/moy_store/moy_save.h) at most once per Pmem.SAVE_MS
while they moved, and exit, pause and a crash save synchronously. The policy is
`widgets.Pmem`; the record is native/moy_store/moy_pmem.h's two slots, whose
torn-write half is the power-cut matrix's cases 16 and 17
(native/moy_store/fuzz_fs.c, tests/test_moy_store.py), which cut its writes
at every block write."""

import os
import re
import time

from runtime.toolkit.widgets import Pmem
from ws_helpers import build_ws_with_cart

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Saver:
    """Pmem's store side, recorded."""

    def __init__(self):
        self.later_calls = []
        self.now_calls = []
        self.waits = 0

    def later(self, cells):
        self.later_calls.append(list(cells))

    def now(self, cells):
        self.now_calls.append(list(cells))

    def wait(self):
        self.waits += 1


def test_a_cart_writing_every_frame_hands_over_at_most_one_save_a_period():
    saver = _Saver()
    pm = Pmem(saver=saver)
    t = 1000
    frames = 30 * 3 * Pmem.SAVE_MS // 1000      # three periods at 30 fps
    for frame in range(1, frames + 1):
        pm.cell(0, frame)
        pm.tick(t)
        t += 33
    assert len(saver.later_calls) == 3
    assert saver.now_calls == []
    # Each save carries the frame it was handed over on: the latest, collapsed.
    assert saver.later_calls[0][0] == 1
    gaps = [b[0] - a[0] for a, b in zip(saver.later_calls, saver.later_calls[1:])]
    assert all(g * 33 >= Pmem.SAVE_MS for g in gaps), gaps


def test_the_period_is_ten_seconds_on_every_tier():
    """The Python Player's and a Lua or compiled run's own (moycore_run.h)."""
    assert Pmem.SAVE_MS == 10000
    with open(os.path.join(ROOT, "native", "moycore", "moycore_run.h")) as f:
        got = re.search(r"#define MOYCORE_PMEM_SAVE_MS (\d+)u", f.read())
    assert got and int(got.group(1)) == Pmem.SAVE_MS


def test_a_clean_pmem_hands_over_nothing():
    saver = _Saver()
    pm = Pmem([5] * 256, saver=saver)
    for t in range(0, 5000, 33):
        pm.cell(0, 5)                           # the same value: no change
        pm.tick(t)
    assert saver.later_calls == []


def test_exit_persists_the_last_value_and_waits_for_the_one_in_flight():
    saver = _Saver()
    pm = Pmem(saver=saver)
    pm.cell(3, 7)
    assert pm.tick(0)
    pm.cell(3, 8)
    assert not pm.tick(500)                     # inside the period: no save
    assert pm.flush()
    assert saver.now_calls[-1][3] == 8
    waits = saver.waits
    assert not pm.flush()                       # clean: only the wait
    assert saver.waits == waits + 1


def test_the_record_reads_back_its_newest_whole_slot(tmp_path):
    """Two slots written in turn: a slot whose checksum fails -- a write the
    power cut -- reads as the other, the cells before it."""
    import moy_store
    path = str(tmp_path / "pmem.bin")
    assert moy_store.pmem_read(path) == [0] * 256
    moy_store.pmem_save(path, [1, -1, 2 ** 31 - 1, -2 ** 31])
    moy_store.pmem_save(path, [2])
    assert moy_store.pmem_read(path)[:5] == [2, 0, 0, 0, 0]
    assert os.path.getsize(path) == 2 * 1536
    raw = bytearray(open(path, "rb").read())
    raw[1536 + 12] ^= 0xFF                      # the newer slot, torn
    open(path, "wb").write(bytes(raw))
    assert moy_store.pmem_read(path)[:5] == [1, -1, 2 ** 31 - 1, -2 ** 31, 0]
    moy_store.pmem_save(path, [3])              # takes the torn slot
    assert moy_store.pmem_read(path)[0] == 3
    raw = bytearray(open(path, "rb").read())
    assert raw[1536 + 12] == 3 and raw[12] == 1


_SPAM = """
n = [0]

def _update(dt):
    n[0] += 1
    pmem(0, n[0])

def _draw():
    cls(1)
"""


def test_the_player_saves_a_spamming_cart_once_a_period_and_its_last_value_at_exit(
        tmp_path):
    from runtime import moy_carts
    ws = build_ws_with_cart(tmp_path, _SPAM, title="Spam", type="game")
    pm = ws.run.project.pmem
    pm.SAVE_MS = 1000                           # the period, shortened for the test
    real = pm._saver
    stamps = []

    class Spy:
        def later(self, cells):
            stamps.append(time.monotonic())
            real.later(cells)

        def now(self, cells):
            real.now(cells)

        def wait(self):
            real.wait()

    pm._saver = Spy()
    t0 = time.monotonic()
    frames = 0
    while time.monotonic() - t0 < 2.5:
        ws.input.begin_frame()
        ws.frame(1 / 30)
        frames += 1
    assert frames > 30
    assert 1 <= len(stamps) <= 4, stamps
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert all(g >= 0.99 for g in gaps), gaps
    last = ws.ns["n"][0]
    folder = ws.run.project.cart["path"]
    ws.spine.exit_to_caller()
    assert moy_carts.load_pmem(folder)[0] == last


def test_a_lua_cart_is_saved_while_it_plays_and_at_exit(tmp_path):
    """A Lua run's image is read every Pmem.SAVE_MS where its run does not
    save itself (the host's: moycore.pmem_to answers False), and its close
    hands the last image over before the exit's save."""
    from runtime import lua_binding, moy_carts
    if not lua_binding.HostLuaRun.available():
        import pytest
        pytest.skip("host lua binding not built (needs a C compiler)")
    from ws_helpers import build_ws
    ws = build_ws(tmp_path)
    cart = moy_carts.create("Spam Lua", str(tmp_path / "carts"),
                            src="n = 0\nfunction _update(dt) n = n + 1 pmem(0, n) end\n",
                            runtime="lua", main="main.lua")
    ws.launcher.items.append(cart)
    ws.launcher.sel = len(ws.launcher.items) - 1
    ws.open()
    assert ws.run.player.cart_error is None
    pm = ws.run.project.pmem
    pm.SAVE_MS = 500                            # the period, shortened for the test
    t0 = time.monotonic()
    while time.monotonic() - t0 < 1.5:
        ws.input.begin_frame()
        ws.frame(1 / 30)
    mid = moy_carts.load_pmem(cart["path"])[0]
    assert mid > 0, "nothing saved while it played"
    ws.spine.exit_to_caller()
    last = moy_carts.load_pmem(cart["path"])[0]
    assert last >= mid and last == pm.cells[0]


_MP_SAVER = """
import vfs, moy_store
vfs.mount(moy_store.card_ram(16384), "/sd")
moy_store.kvol_load(None)
vfs.mount(moy_store.KVfs(), "/v")
for root in ("/sd/carts", "/v/carts"):
    moy_store.mkdir(root)
    moy_store.mkdir(root + "/a.moy")
moy_store.save_stats(True)
for i in range(50):
    moy_store.pmem_save("/sd/carts/a.moy/pmem.bin", [i, -i])
moy_store.save_wait()
print("card", moy_store.pmem_read("/sd/carts/a.moy/pmem.bin")[:3])
moy_store.pmem_save("/v/carts/a.moy/pmem.bin", [7])
print("flash", moy_store.pmem_read("/v/carts/a.moy/pmem.bin")[:2])
print("none", moy_store.pmem_read("/v/carts/b.bin") == [0] * 256)
saves, errors, replaced, last_us, max_us, now, busy = moy_store.save_stats()
print("stats", saves, errors, now, busy)
"""


def test_the_saver_writes_the_card_and_internal_flash(tmp_path):
    """The saver on the desktop MicroPython, which has no saver task: every
    record is written at once, on the card and on internal flash (KVfs)."""
    import subprocess
    from unix_mp import require_unix_mp
    exe = require_unix_mp("moy_store", why="the store's saver")
    script = tmp_path / "saver.py"
    script.write_text(_MP_SAVER)
    done = subprocess.run([str(exe), "-X", "heapsize=32M", str(script)],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    out = done.stdout.splitlines()
    assert out == ["card [49, -49, 0]", "flash [7, 0]", "none True",
                   "stats 51 0 51 0"], out

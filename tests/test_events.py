"""Clock and events, the Python half (docs/os_2026-11.md section 7, phase 1
step 4): the event record and the per-source rings of native/moy_input
(moy_event.h), the writers that stamp them, their readers, and the wake-source
registry of runtime/system/moybyte.py -- on CPython through the host's binding
and on the desktop MicroPython through the module the boards import."""

import array
import ctypes
import subprocess

import pytest

import runtime  # noqa: F401
from runtime import moy_input as mi
from runtime import moybyte
from runtime.ticks import _ticks_ms, _ticks_add
from unix_mp import find_unix_mp, require_unix_mp, runtime_tree

F = mi.EV_FIELDS


@pytest.fixture(autouse=True)
def _clock():
    yield
    mi.pin_clock(0)


def read(t, r, cap=64):
    buf = array.array("i", [0] * (F * cap))
    n = t.events(buf, r)
    return [tuple(buf[i * F:(i + 1) * F]) for i in range(n)]


def kinds(recs):
    return [(e[mi.EV_KIND], e[mi.EV_A], e[mi.EV_B]) for e in recs]


# -- the record ----------------------------------------------------------------------

def test_the_record_is_sixteen_bytes_in_the_designs_order():
    assert ctypes.sizeof(mi._Event) == 16
    assert [f[0] for f in mi._Event._fields_] == ["t_us", "src", "kind", "prov", "a", "id", "b"]
    assert mi.EV_VERSION == 1


def test_a_record_carries_its_sources_slot_and_generation():
    t = mi.InputTable()
    k = t.source("kbd")
    r = t.reader()
    k.set_button("a", True)
    (e,) = read(t, r)
    slot = k.h & 0xFF
    assert e[mi.EV_SRC] == (1 << 8) | slot == k.ring()[5]


# -- the writers ---------------------------------------------------------------------

def test_a_button_that_changes_is_a_record_and_one_reasserted_is_none():
    t = mi.InputTable()
    s = t.source("pad")
    r = t.reader()
    s.set_button("a", True)
    s.set_button("a", True)
    s.set_button("x", True)
    s.release_all()
    a, x = mi.NAMES.index("a"), mi.NAMES.index("x")
    assert kinds(read(t, r)) == [(mi.EV_BTN, a, 1), (mi.EV_BTN, x, 1), (mi.EV_BTN, a, 0), (mi.EV_BTN, x, 0)]


def test_the_tables_release_all_lets_every_held_button_go_as_records():
    t = mi.InputTable()
    s = t.source("pad")
    s.set_button("left", True)
    r = t.reader()
    t.release_all()
    assert kinds(read(t, r)) == [(mi.EV_BTN, 0, 0)]


def test_a_one_shot_key_is_down_text_and_up_with_its_hid_usage():
    t = mi.InputTable()
    k = t.source("kbd")
    r = t.reader()
    k.key(ord("A"))
    usage = 0x04 | (mi.SHIFT << 8)
    assert kinds(read(t, r)) == [(mi.EV_KEY_DOWN, usage, ord("A")), (mi.EV_TEXT, 0, ord("A")),
                                 (mi.EV_KEY_UP, usage, ord("A"))]


def test_a_key_level_is_down_when_it_starts_and_up_when_it_changes():
    t = mi.InputTable()
    k = t.source("kbd")
    r = t.reader()
    mi._lib().moy_input_set_key(t._t, k.h, ord("w"))
    mi._lib().moy_input_set_key(t._t, k.h, ord("w"))
    mi._lib().moy_input_set_key(t._t, k.h, mi.event(ord("z"), mi.CTRL))
    mi._lib().moy_input_set_key(t._t, k.h, 0)
    w, z = 0x1A, 0x1D | (mi.CTRL << 8)
    ctrl_z = mi.event(ord("z"), mi.CTRL)
    assert kinds(read(t, r)) == [
        (mi.EV_KEY_DOWN, w, ord("w")), (mi.EV_TEXT, 0, ord("w")),
        (mi.EV_KEY_UP, w, ord("w")), (mi.EV_KEY_DOWN, z, ctrl_z), (mi.EV_TEXT, 0, 0x1A),
        (mi.EV_KEY_UP, z, ctrl_z)]


@pytest.mark.parametrize("ev,usage", [
    (ord("a"), 0x04), (ord("Z"), 0x1D | 0x200), (ord("1"), 0x1E), (ord("!"), 0x1E | 0x200),
    (ord("0"), 0x27), (ord("#"), 0x20 | 0x200), (ord("~"), 0x35 | 0x200), (13, 0x28),
    (10, 0x28), (27, 0x29), (8, 0x2A), (9, 0x2B), (32, 0x2C), (0x7F, 0x4C),
    (mi.KEY_F1, 0x3A), (mi.KEY_F1 + 11, 0x45), (mi.KEY_RIGHT, 0x4F), (mi.KEY_LEFT, 0x50),
    (mi.KEY_DOWN, 0x51), (mi.KEY_UP, 0x52), (mi.KEY_PGUP, 0x4B), (mi.KEY_PGDN, 0x4E),
    (mi.event(ord("c"), mi.CTRL | mi.ALT), 0x06 | 0x500), (3, 0x06 | 0x100), (0xE9, 0),
])
def test_a_key_event_maps_to_its_us_layout_usage_and_modifiers(ev, usage):
    assert mi.hid_usage(ev) == usage


def test_the_usage_inverts_the_ble_keyboards_table():
    hid = mi._lib()
    hid.moy_hid_event.argtypes = [ctypes.c_uint8, ctypes.c_uint8, ctypes.c_bool]
    hid.moy_hid_event.restype = ctypes.c_int32
    for u in list(range(0x04, 0x32)) + list(range(0x33, 0x4F)):
        for shift in (0, 2):
            ev = hid.moy_hid_event(u, shift, False)
            if ev == 0:
                continue
            back = mi.hid_usage(ev)
            assert hid.moy_hid_event(back & 0xFF, 2 if back & 0x200 else 0, False) == ev, hex(u)


def test_a_touch_is_down_then_moves_then_up_and_a_still_finger_writes_nothing():
    t = mi.InputTable()
    s = t.source("touch")
    r = t.reader()
    s.point(10, 20, True, True)
    s.point(10, 20, True)
    s.point(12, 20, True)
    s.point(12, 20, False)
    s.point(12, 20, False)
    recs = read(t, r)
    assert kinds(recs) == [(mi.EV_TOUCH_DOWN, 0, 10 << 16 | 20), (mi.EV_TOUCH_MOVE, 0, 12 << 16 | 20),
                           (mi.EV_TOUCH_UP, 0, 12 << 16 | 20)]
    assert {e[mi.EV_ID] for e in recs} == {0}


def test_a_press_edge_while_down_is_a_lift_and_a_new_touch():
    t = mi.InputTable()
    s = t.source("touch")
    s.point(1, 1, True, True)
    r = t.reader()
    s.point(5, 5, True, True)
    assert kinds(read(t, r)) == [(mi.EV_TOUCH_UP, 0, 1 << 16 | 1), (mi.EV_TOUCH_DOWN, 0, 5 << 16 | 5)]


def test_the_merge_reads_what_it_read_before_the_records():
    """The latches drive the frame unchanged: a reader changes nothing."""
    t = mi.InputTable()
    k = t.source("kbd")
    t.reader()
    for c in (0x61, 0x61, 0x62):
        k.key(c)
    seen = []
    for _ in range(6):
        t.begin_frame()
        seen.append(t.last_key)
    assert seen == [0x61, 0, 0x61, 0x62, 0, 0]


# -- provenance and placement --------------------------------------------------------

def test_a_source_is_polled_unless_it_names_another_provenance():
    t = mi.InputTable()
    s = t.source("touch")
    assert s.ring() == (mi.EV_DEPTH, 0, 0, mi.PROV_POLL, False, s.ring()[5])
    b = t.source("ble")
    b.set_events(mi.PROV_FIFO, 16)
    i = t.source("ball")
    i.set_events(mi.PROV_ISR, 5)
    assert b.ring()[:5] == (16, 0, 0, mi.PROV_FIFO, False)
    assert i.ring()[:5] == (8, 0, 0, mi.PROV_ISR, True)    # internal, a power of two
    r = t.reader()
    s.point(0, 0, True, True)
    b.set_button("a", True)
    assert [e[mi.EV_PROV] for e in read(t, r)] == [mi.PROV_POLL, mi.PROV_FIFO]
    with pytest.raises(ValueError):
        s.set_events(7, 8)


# -- rings, drops and readers --------------------------------------------------------

def test_a_full_ring_drops_its_oldest_and_the_reader_reads_the_gap():
    t = mi.InputTable()
    s = t.source("pad")
    s.set_events(mi.PROV_POLL, 4)
    r = t.reader()
    for i in range(10):
        s.post(mi.EV_EDGE, i, 0, 1)
    assert s.ring()[1:3] == (4, 6)
    recs = read(t, r)
    assert kinds(recs) == [(mi.EV_LOST, 0, 6)] + [(mi.EV_EDGE, i, 1) for i in range(6, 10)]
    assert read(t, r) == []


def test_drops_count_only_against_a_reader_that_had_not_read():
    t = mi.InputTable()
    s = t.source("pad")
    s.set_events(mi.PROV_POLL, 4)
    for i in range(10):                         # nobody reads: nothing is lost
        s.post(mi.EV_EDGE, i)
    assert s.ring()[2] == 0
    r = t.reader()
    for i in range(4):
        s.post(mi.EV_EDGE, i)
        assert len(read(t, r)) == 1             # a reader that keeps up loses nothing
    for i in range(4):
        s.post(mi.EV_EDGE, i)
    assert s.ring()[2] == 0


def test_a_reader_of_keys_does_not_lose_by_touches_it_never_wanted():
    t = mi.InputTable()
    k = t.source("kbd")
    k.set_events(mi.PROV_POLL, 4)
    keys = t.reader(mi.EV_KEYS)
    for i in range(10):
        k.post(mi.EV_EDGE, i)
    assert k.ring()[2] == 0
    assert t.pending(keys) == 0
    k.key(ord("q"))
    assert t.pending(keys) == 3
    assert [e[mi.EV_KIND] for e in read(t, keys)] == [mi.EV_KEY_DOWN, mi.EV_TEXT, mi.EV_KEY_UP]


def test_records_merge_oldest_first_across_sources_through_the_clocks_wrap():
    t = mi.InputTable()
    a, b = t.source("one"), t.source("two")
    r = t.reader()
    mi.pin_clock(0xFFFFFFF0)
    a.post(mi.EV_EDGE, 1)
    mi.pin_clock(0x00000010)                    # past the wrap
    a.post(mi.EV_EDGE, 3)
    mi.pin_clock(0xFFFFFFF8)
    b.post(mi.EV_EDGE, 2)
    mi.pin_clock(0x00000020)
    recs = read(t, r)
    assert [e[mi.EV_A] for e in recs] == [1, 2, 3]
    ts = [e[mi.EV_T] for e in recs]
    assert all(0 <= x < (1 << 30) for x in ts)
    ages = [(ts[-1] - x) & ((1 << 30) - 1) for x in ts]
    assert ages == [0x20, 0x18, 0]


def test_a_reader_starts_at_the_records_written_after_it():
    t = mi.InputTable()
    s = t.source("pad")
    s.post(mi.EV_EDGE, 1)
    r = t.reader()
    s.post(mi.EV_EDGE, 2)
    assert [e[mi.EV_A] for e in read(t, r)] == [2]


def test_a_buffer_takes_as_many_records_as_fit_and_the_rest_wait():
    t = mi.InputTable()
    s = t.source("pad")
    r = t.reader()
    for i in range(5):
        s.post(mi.EV_EDGE, i)
    assert [e[mi.EV_A] for e in read(t, r, cap=3)] == [0, 1, 2]
    assert t.pending(r) == 2
    assert [e[mi.EV_A] for e in read(t, r, cap=3)] == [3, 4]


def test_the_readers_are_counted_and_a_freed_one_is_taken_again():
    t = mi.InputTable()
    got = [t.reader() for _ in range(8)]
    with pytest.raises(OSError):
        t.reader()
    t.reader_free(got[3])
    assert t.reader() == got[3]
    with pytest.raises(ValueError):
        t.reader_free(99)


def test_a_post_refuses_the_readers_own_kind():
    t = mi.InputTable()
    with pytest.raises(ValueError):
        t.source("pad").post(mi.EV_LOST)


# -- the wake-source registry --------------------------------------------------------

@pytest.fixture
def wakes():
    w = moybyte.Wakes()
    w.bind(mi.InputTable())
    return w


def test_timers_wake_no_earlier_than_asked(wakes):
    hits = []
    now = _ticks_ms()
    e = wakes.every(100, lambda: hits.append("e"))
    a = wakes.after(250, lambda: hits.append("a"))
    out = [0] * 4
    assert wakes.due(_ticks_add(now, 50), out) == 0
    assert wakes.due(_ticks_add(now, 101), out) == 1 and out[0] == e
    assert wakes.due(_ticks_add(now, 150), out) == 0
    n = wakes.due(_ticks_add(now, 260), out)
    assert sorted(out[:n]) == sorted([e, a])
    for wid in out[:n]:
        wakes.call(wid)
    assert sorted(hits) == ["a", "e"]
    assert a not in wakes.by_id                 # an `after` is spent by its call


def test_a_late_every_keeps_its_period_and_does_not_burst(wakes):
    now = _ticks_ms()
    wakes.every(10, lambda: None)
    out = [0] * 4
    assert wakes.due(_ticks_add(now, 1000), out) == 1
    assert wakes.due(_ticks_add(now, 1005), out) == 0
    assert wakes.due(_ticks_add(now, 1011), out) == 1


def test_an_on_wake_is_due_while_its_source_has_records_and_reads_them(wakes):
    t = wakes.table
    k = t.source("kbd")
    p = t.source("pad")
    got = []
    buf = array.array("i", [0] * (F * 8))

    def on_key():
        n = wakes.events(buf)
        got.extend(buf[i * F + mi.EV_KIND] for i in range(n))

    wid = wakes.on("keys", on_key)
    out = [0] * 4
    p.set_button("a", True)
    assert wakes.due(_ticks_ms(), out) == 0
    k.key(ord("x"))
    assert wakes.due(_ticks_ms(), out) == 1 and out[0] == wid
    wakes.call(wid)
    assert got == [mi.EV_KEY_DOWN, mi.EV_TEXT, mi.EV_KEY_UP]
    assert wakes.due(_ticks_ms(), out) == 0
    with pytest.raises(ValueError):
        wakes.on("lora", on_key)


def test_a_process_ending_takes_its_wakes_and_readers(wakes):
    wakes.every(5, lambda: None)
    wakes.on("touch", lambda: None)
    pid = wakes.list[0][1]
    buf = array.array("i", [0] * F)
    wakes.events(buf)                           # its own reader
    assert wakes.readers
    wakes.end(pid)
    assert wakes.list == [] and wakes.by_id == {} and wakes.readers == {}
    assert len([wakes.table.reader() for _ in range(8)]) == 8   # every reader free again


def test_the_workstation_binds_the_registry_to_its_input():
    from runtime.host_app import build_workstation
    ws = build_workstation()
    assert moybyte.WAKES.table is ws.input
    buf = array.array("i", [0] * (F * 4))
    moybyte.events(buf)
    ws.input.source("local").set_button("a", True)
    n = moybyte.events(buf)
    assert n == 1 and buf[mi.EV_KIND] == mi.EV_BTN


# -- the desktop MicroPython ---------------------------------------------------------

VM_SCRIPT = r'''
import sys
sys.path[:0] = [@TREE@]
import gc, array, time
import moy_input as I
t = I.HostInputTable()
s = t.source("pad")
r = t.reader()
buf = array.array("i", [0] * (I.EV_FIELDS * 8))
def frame(n):
    while n:
        s.set_button("a", n & 1)
        s.point(n & 63, 5, True, n == 8)
        t.events(buf, r)
        t.pending(r)
        n -= 1
before = 0                  # the global's slot made before the count
frame(4)
gc.collect()
before = gc.mem_alloc()
frame(400)
print("ALLOC", gc.mem_alloc() - before)
s.post(I.EV_EDGE, 7, 0, 1)
n = t.events(buf, r)
age = (time.ticks_us() - buf[I.EV_T]) & ((1 << 30) - 1)
print("REC", n, buf[I.EV_KIND], buf[I.EV_A], buf[I.EV_PROV], 0 <= age < 1000000)
import moybyte
moybyte.bind(t)
moybyte.events(buf)
gc.collect()
before = gc.mem_alloc()
for _ in range(200):
    moybyte.events(buf)
print("MOYBYTE", gc.mem_alloc() - before)
out = [0] * 4
moybyte.every(1, lambda: None)
w = moybyte.WAKES
gc.collect()
before = gc.mem_alloc()
for i in range(200):
    w.due(time.ticks_ms(), out)
print("DUE", gc.mem_alloc() - before)
'''


def _vm(exe, tmp_path, tag):
    script = tmp_path / ("events_%s.py" % tag)
    script.write_text(VM_SCRIPT.replace("@TREE@", repr(runtime_tree())))
    done = subprocess.run([str(exe), str(script)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout


def test_on_the_vm_reading_events_and_the_registry_allocate_nothing(tmp_path):
    exe = require_unix_mp("moy_input", why="the event rings on the VM a board runs")
    exes = [("desktop", exe)]
    board = find_unix_mp("moy_input", board_model=True)
    if board is not None:
        exes.append(("board", board))
    for tag, x in exes:
        out = _vm(x, tmp_path, tag)
        assert "ALLOC 0\n" in out, (tag, out)
        assert "MOYBYTE 0\n" in out, (tag, out)
        assert "DUE 0\n" in out, (tag, out)
        assert "REC 1 %d 7 %d True" % (mi.EV_EDGE, mi.PROV_POLL) in out, (tag, out)

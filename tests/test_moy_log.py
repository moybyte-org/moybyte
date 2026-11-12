"""The OS log (native/moy_kernel/moy_log.h; docs/os_2026-11/processes.md
section 9): the ring on CPython through its ctypes binding and on the desktop
MicroPython through the module the boards import, its writers (the kernel's
diag lines, a run's start, exit and crash, a script's print, `logging`) and
its readers (the dev channel's `log` word and Settings' System log page)."""

import json
import os
import re
from pathlib import Path

import pytest

import runtime  # noqa: F401  (registers moy_log)
import moy_log as L
from runtime import moy_loop as M
from runtime import moy_logging
from runtime import log_ui, os_log
from ws_helpers import select_on_home
from unix_mp import find_unix_mp, require_unix_mp, runtime_tree

ROOT = Path(__file__).resolve().parent.parent
KERNEL = ROOT / "native" / "moy_kernel"
DT = 1 / 30


@pytest.fixture(autouse=True)
def ring():
    L.init(16384)
    L.current(L.SHELL)
    yield
    L.pin_clock(0)
    L.current(L.SHELL)


def texts():
    return [r[4] for r in L.read(0, 1000)]


# -- the record --------------------------------------------------------------------

def test_a_record_is_its_time_level_process_and_text():
    L.pin_clock(1234567)
    L.write(L.WARNING, L.KERNEL, "HITCH ms=96")
    (seq, t_us, level, pid, text), = L.read()
    assert (t_us, level, pid, text) == (1234567, L.WARNING, L.KERNEL, "HITCH ms=96")
    assert seq == L.stats()[0] and L.next() == seq + 1


def test_the_levels_are_syslogs_eight():
    names = [L.level_name(i) for i in range(8)]
    assert names == ["emerg", "alert", "crit", "err", "warning", "notice", "info", "debug"]
    assert [L.level_of(n) for n in names] == list(range(8))
    assert L.level_of("verbose") == -1
    L.write(99, None, "past debug")
    L.write(-3, None, "past emerg")
    assert [r[2] for r in L.read()] == [L.DEBUG, L.EMERG]


def test_parts_join_with_spaces_and_text_keeps_its_newlines():
    L.write(L.INFO, None, "SERIAL", "rx=%d" % 4, 7)
    L.write(L.CRIT, None, "Traceback\n  line 2\nValueError")
    assert texts() == ["SERIAL rx=4 7", "Traceback\n  line 2\nValueError"]


def test_a_text_past_the_most_keeps_its_first_bytes():
    L.write(L.INFO, None, "x" * (L.TEXT_MAX + 100))
    assert texts() == ["x" * L.TEXT_MAX]


# -- process ids -------------------------------------------------------------------

def test_the_kernel_and_the_shell_have_the_first_two_pids():
    assert (L.name(L.KERNEL), L.name(L.SHELL)) == ("kernel", "shell")


def test_a_run_is_a_new_pid_and_a_write_with_none_goes_under_current():
    a = L.spawn("moybyte.brick_siege")
    b = L.spawn("moybyte.brick_siege")
    assert b == a + 1 and L.name(a) == L.name(b) == "moybyte.brick_siege"
    L.write(L.INFO, None, "shell's")
    L.current(b)
    L.write(L.INFO, None, "the game's")
    L.write(L.INFO, L.KERNEL, "named")
    assert [r[3] for r in L.read()] == [L.SHELL, b, L.KERNEL]


def test_a_pid_whose_name_slot_was_reused_has_no_name():
    old = L.spawn("first")
    for i in range(64):
        L.spawn("run%d" % i)
    assert L.name(old) == "" and L.name(old + 64) == "run63"


# -- the ring ----------------------------------------------------------------------

def test_a_full_ring_drops_its_oldest_records():
    L.init(256)
    for i in range(40):
        L.write(L.INFO, None, "line %02d" % i)
    first, nxt, used, cap = L.stats()
    assert cap == 256 and used <= cap and nxt - first == len(L.read(0, 100))
    got = texts()
    assert got[-1] == "line 39" and got[0] == "line %02d" % (40 - len(got))
    assert [r[0] for r in L.read(0, 100)] == list(range(first, nxt))


def test_a_reader_resumes_and_a_lapped_reader_restarts_at_the_oldest():
    L.init(256)
    for i in range(3):
        L.write(L.INFO, None, "r%d" % i)
    seq = L.read(0, 1)[0][0]
    assert [r[4] for r in L.read(seq + 1)] == ["r1", "r2"]
    for i in range(40):
        L.write(L.INFO, None, "s%02d" % i)
    oldest = L.stats()[0]
    assert oldest > seq + 1
    assert L.read(seq + 1, 1)[0][0] == oldest
    assert L.read(L.next()) == []


def test_a_record_split_by_the_ring_end_reads_whole():
    L.init(200)
    for i in range(30):
        L.write(L.INFO, None, "%d:" % i + "y" * (i % 23))
        r = L.read(L.next() - 1)
        assert r[0][4] == "%d:" % i + "y" * (i % 23)


# -- the dev channel's word ----------------------------------------------------------

def _word(line):
    M.trace_init(20, True, 1000)
    M.devch(line)
    return [t[4:-1].replace("_", " ") for t in M.trace_log().split()
            if t.startswith("say[")]


def test_the_log_word_prints_a_line_a_line_then_the_end():
    L.pin_clock(15103300)
    pid = L.spawn("moybyte.brick_siege")
    L.write(L.INFO, L.SHELL, "wifi up")
    L.write(L.CRIT, pid, "Traceback\n  line 88\nTypeError")
    first, nxt = L.stats()[:2]
    out = _word("log")
    assert out == [
        "LOG %d 15.103 info shell/1 wifi up" % first,
        "LOG %d 15.103 crit moybyte.brick siege/%d Traceback" % (first + 1, pid),
        "LOG %d 15.103 crit moybyte.brick siege/%d   line 88" % (first + 1, pid),
        "LOG %d 15.103 crit moybyte.brick siege/%d TypeError" % (first + 1, pid),
        "LOG end n=2 next=%d first=%d" % (nxt, first),
    ]


def test_the_log_word_filters_by_level_process_and_seq():
    pid = L.spawn("tap_game")
    L.write(L.DEBUG, L.KERNEL, "PERF fps=30")
    L.write(L.ERR, pid, "lives ran out")
    L.write(L.INFO, pid, "start")
    first = L.stats()[0]
    assert len(_word("log err")) == 2
    assert [l.split()[-1] for l in _word("log tap")] == ["out", "start", "first=%d" % first]
    assert _word("log +%d" % (first + 2))[0].endswith("start")
    assert _word("log warning kernel")[0].startswith("LOG end n=0")


def test_hitch_loop_and_perf_reach_the_ring_from_the_loop():
    src = (KERNEL / "moy_loop.c").read_text()
    for what, level in (("h->hitch", "WARNING"), ("h->loop", "DEBUG"),
                        ("L.heavy->line", "DEBUG")):
        assert ("moy_log_write(MOY_LOG_%s, MOY_LOG_KERNEL, %s, strlen(%s))"
                % (level, what, what)) in src


def test_the_ring_is_psram_and_made_before_the_vm():
    """native/moy_kernel/moy_kernel.h: kernel data is PSRAM. The ring allocates
    only through log_psram, one moy_kalloc asking for PSRAM first (moy_kmem.h),
    and the kernel makes it in app_main, before the VM task's first heap area."""
    src = (KERNEL / "moy_log.c").read_text()
    alloc = re.search(r"static void \*log_psram\(size_t n\) \{.*?\n\}", src, re.S).group(0)
    assert "moy_kalloc(" in alloc and "MOY_KMEM_PSRAM" in alloc
    rest = src.replace(alloc, "")
    assert not re.findall(r"\b(malloc|calloc|heap_caps_malloc|heap_caps_calloc)\s*\(", rest)
    kernel = (KERNEL / "moy_kernel.c").read_text()
    main = kernel[kernel.index("void app_main(void)"):]
    assert main.index("moy_log_reserve();") < main.index("xTaskCreatePinnedToCore")


# -- the VM: what the boards import --------------------------------------------------

VM_SCRIPT = '''
import gc, sys
sys.path[:] = [@TREE@]
import moy_log as L
assert not hasattr(L, "__file__")
import os_log
import logging
assert logging.__name__ == "moy_logging"
L.init(4096)
g = logging.getLogger("score")
def frame(n):
    w = L.write
    while n:
        w(6, None, "frame text")
        w(7, 1, "tag", "msg", 7)
        g.debug("below the level")
        g.warning("a warning")
        L.next()
        n -= 1
before = 0                  # the global's slot made before the count
frame(2)
gc.collect()
before = gc.mem_alloc()
frame(300)
print("ALLOC", gc.mem_alloc() - before)
L.init(4096)
p = L.spawn("game")
L.current(p)
logging.basicConfig(level=logging.DEBUG)
logging.info("root")
g.error("lives %d", 0)
print("RECS", [(r[2], r[3] == p, r[4]) for r in L.read()])
'''


def _vm(exe, tmp_path, tag):
    script = tmp_path / ("log_%s.py" % tag)
    script.write_text(VM_SCRIPT.replace("@TREE@", repr(runtime_tree())))
    import subprocess
    done = subprocess.run([str(exe), str(script)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout


def test_on_the_vm_a_write_and_a_log_call_allocate_nothing(tmp_path):
    exe = require_unix_mp("moy_log", why="the OS log's module on the VM a board runs")
    exes = [("desktop", exe)]
    board = find_unix_mp("moy_log", board_model=True)
    if board is not None:
        exes.append(("board", board))
    for tag, x in exes:
        out = _vm(x, tmp_path, tag)
        assert "ALLOC 0" in out, (tag, out)
        assert ("RECS [(6, True, 'root'), (3, True, 'score: lives 0')]") in out, (tag, out)


# -- logging ------------------------------------------------------------------------

def test_logging_maps_its_levels_onto_syslogs_and_starts_at_warning(capsys):
    g = moy_logging.getLogger("t_levels")
    was = moy_logging.root.level
    try:
        g.debug("dropped")
        g.info("dropped")
        g.warning("w %s", "x")
        g.error("e")
        g.critical("c")
        moy_logging.basicConfig(level=moy_logging.DEBUG)
        g.debug("d")
        g.log(moy_logging.INFO, "i")
    finally:
        moy_logging.root.setLevel(was)
    assert [(r[2], r[4]) for r in L.read()] == [
        (L.WARNING, "t_levels: w x"), (L.ERR, "t_levels: e"), (L.CRIT, "t_levels: c"),
        (L.DEBUG, "t_levels: d"), (L.INFO, "t_levels: i")]
    assert "WARNING:t_levels:w x" in capsys.readouterr().out


def test_logging_exception_writes_the_traceback():
    try:
        {}["missing"]
    except KeyError as exc:
        moy_logging.getLogger("t_exc").exception("lookup", exc_info=exc)
    got = texts()
    assert got[0] == "t_exc: lookup"
    assert got[1].startswith("Traceback") and "KeyError" in got[1]


# -- a run: start, crash, exit ---------------------------------------------------------

CRASH_SRC = """
def _update(dt):
    pass

def _draw():
    cls(0)
    boom()
"""


def _open(tmp_path, name, src):
    from runtime import host_app
    d = tmp_path / "carts" / (name + ".moy")
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({"title": name, "id": "test." + name,
                                                 "moybyte": {"type": "game"}}))
    (d / "main.py").write_text(src)
    (d / "config.json").write_text("{}")
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    drv = host_app.ConsoleDriver(ws)
    select_on_home(ws, name)
    ws.open()
    return ws, drv


def test_a_crash_is_its_runs_crit_record_with_the_traceback(tmp_path):
    ws, drv = _open(tmp_path, "crashy", CRASH_SRC)
    pid = ws.run.player.pid
    assert L.name(pid) == "test.crashy" and L.current() == pid
    for _ in range(3):
        drv.frame(DT)
    assert ws.cart_error is not None
    seq, text = os_log.crash_of(pid)
    assert seq == ws.run.player.crash_seq
    assert text.startswith("Traceback") and "boom" in text and '"<cart>"' in text
    ws.run.player.release_world()
    recs = [(r[2], r[4].split("\n")[0]) for r in L.read(0, 100) if r[3] == pid]
    assert recs[0] == (L.INFO, "start test.crashy")
    assert recs[-1] == (L.INFO, "exit")
    assert L.current() == L.SHELL


def test_a_script_prints_into_the_ring_and_to_serial(capsys):
    from runtime.text_console import TextConsole
    con = TextConsole.__new__(TextConsole)
    said = []
    con.write = said.append
    pid = L.spawn("script")
    L.current(pid)
    con.api()["print"]("hello", 3)
    assert said == ["hello 3\n"]
    assert L.read()[-1][3:] == (pid, "hello 3")
    assert capsys.readouterr().out == "hello 3\n"


# -- the viewer ----------------------------------------------------------------------------

def test_a_record_reads_as_its_lines():
    out = []
    log_ui.record_lines((7, 15103300, L.CRIT, L.SHELL, "Traceback\n  line 88"), out)
    log_ui.record_lines((8, 2000000, L.INFO, L.KERNEL, "up"), out)
    assert out == ["15.103 crit shell: Traceback", "    line 88", "2.000 info kernel: up"]


def test_the_page_follows_the_ring_and_opens_on_one_process():
    page = log_ui.LogPage()
    pid = L.spawn("game")
    L.write(L.INFO, L.SHELL, "a")
    L.write(L.ERR, pid, "b")
    page.open()
    assert page.moved()
    page._read()
    assert not page.moved() and len(page.lines) == 2
    L.write(L.INFO, L.SHELL, "c")
    assert page.moved()
    page._read()
    assert len(page.lines) == 3
    page.open(pid)
    page._read()
    assert [l.split(": ", 1)[1] for l in page.lines] == ["b"]
    assert page.title() == "Log: game"

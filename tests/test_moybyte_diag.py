"""Host-testable unit tests for the device-only moybyte_diag module's PURE logic:
the bounded ring buffer, the line/perf formatting, and the dump markers. The SD
persistence + boot-dump paths touch hardware (machine.SDCard / moybyte_sd) and are
covered by the grep tests in test_micropython_spike.py instead.

moybyte_diag lives in the firmware modules/ tree (device-only) but its top-level
imports are host-safe (only `time`; all hardware imports are lazy inside the SD
helpers), so we can load + exercise the pure pieces directly here."""

import importlib.util
from pathlib import Path

import pytest


DIAG_SRC = Path("device/moybyte_diag.py")


@pytest.fixture
def diag():
    # Load a FRESH module instance per test so the module-level ring + ENABLED flag
    # don't leak across tests.
    spec = importlib.util.spec_from_file_location("moybyte_diag_test", DIAG_SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.reset()
    return mod


def test_format_line_shape(diag):
    # "<ticks_ms> <tag> <msg>" with a supplied timestamp for determinism.
    assert diag.format_line("boot", "hello", t=1234) == "1234 boot hello"


def test_format_line_collapses_newlines(diag):
    # An entry must stay exactly one line (newlines would corrupt the ring's byte
    # accounting and the dump's per-line markers).
    line = diag.format_line("err", "a\nb\rc", t=0)
    assert "\n" not in line and "\r" not in line
    assert line == "0 err a b c"


def test_format_line_coerces_non_str(diag):
    line = diag.format_line("n", 42, t=0)
    assert line == "0 n 42"
    # A value whose str() raises must not blow up formatting.
    class Bad(object):
        def __str__(self):
            raise ValueError("nope")
    line = diag.format_line("n", Bad(), t=0)
    assert line.startswith("0 n ")


def test_ring_keeps_recent_drops_oldest_by_count(diag):
    diag.reset(max_lines=3, max_bytes=10000)
    for i in range(5):
        diag.log("t", str(i))
    lines = diag.lines()
    assert len(lines) == 3
    # Oldest (0, 1) dropped; newest (2, 3, 4) kept, in order.
    assert lines[0].endswith("t 2")
    assert lines[-1].endswith("t 4")


def test_ring_bounds_by_bytes(diag):
    # A tight byte cap drops oldest until under budget, regardless of line count.
    diag.reset(max_lines=1000, max_bytes=40)
    for i in range(50):
        diag.log("x", "0123456789")   # each formatted line is well over a few bytes
    total = sum(len(l) + 1 for l in diag.lines())
    assert total <= 40
    assert len(diag.lines()) >= 1     # never empties below one line


def test_ring_keeps_at_least_one_overlong_line(diag):
    diag.reset(max_lines=1000, max_bytes=8)
    diag.log("x", "this single line is way over the byte cap")
    assert len(diag.lines()) == 1     # one over-long line still records


def test_disabled_is_a_noop(diag):
    diag.ENABLED = False
    diag.log("t", "ignored")
    assert diag.lines() == []
    # flush is also a guarded no-op when disabled (and never calls the wrapper).
    called = []
    assert diag.flush_to_sd(lambda fn: called.append(True)) is False
    assert called == []


def test_format_perf_shape(diag):
    s = diag.format_perf("star catcher", 29.6, 12.4, 18.9)
    # Cart name spaces -> '_', numbers rounded to int.
    assert s == "PERF cart=star_catcher fps=30 flush=12 draw=19"


def test_format_perf_handles_none_cart(diag):
    s = diag.format_perf(None, 0, 0, 0)
    assert s == "PERF cart=? fps=0 flush=0 draw=0"


def test_log_perf_appends_perf_line(diag):
    diag.log_perf("game", 30, 10, 20)
    lines = diag.lines()
    assert len(lines) == 1
    # "<ts> PERF cart=game fps=30 flush=10 draw=20"
    assert "PERF cart=game fps=30 flush=10 draw=20" in lines[0]


def test_log_persists_and_echoes_live(diag, capsys):
    # log() both persists to the ring AND echoes the formatted line to stdout live
    # (the empirical loop-serial test). logp is an alias of log.
    diag.log("audio", "I2S ready")
    out = capsys.readouterr().out
    assert "Moybyte" in out and "audio I2S ready" in out         # echoed live
    assert any("audio I2S ready" in l for l in diag.lines())     # and persisted


def test_logp_is_alias_of_log(diag, capsys):
    diag.logp("audio", "ready")
    out = capsys.readouterr().out
    assert "audio ready" in out
    assert any("audio ready" in l for l in diag.lines())


def test_echo_live_can_be_disabled(diag, capsys):
    diag.ECHO_LIVE = False
    diag.log("t", "quiet")
    out = capsys.readouterr().out
    assert "quiet" not in out                                    # no live echo
    assert any("quiet" in l for l in diag.lines())               # still persisted


def test_dump_markers_are_stable(diag):
    # The boot-dump markers the owner greps for must stay exactly these strings.
    assert diag.DUMP_HEADER == "===== Moybyte diag dump (previous session) ====="
    assert diag.DUMP_FOOTER == "===== end diag dump ====="


def test_flush_to_sd_writes_whole_ring_via_wrapper(diag):
    # flush_to_sd must run its writer INSIDE the supplied SD-session wrapper and
    # write the WHOLE current ring (overwrite semantics: one session per file).
    diag.log("a", "one")
    diag.log("b", "two")
    captured = {}

    # Stand in for moybyte_sd.with_sd_live: just runs fn() (host has no SD).
    def fake_with_sd(fn):
        captured["ran_inside"] = True
        return fn()

    # Patch the file writer so we can assert the body without touching a real FS.
    def fake_write(body):
        captured["body"] = body
    diag._write_log_file = fake_write

    assert diag.flush_to_sd(fake_with_sd) is True
    assert captured.get("ran_inside") is True
    assert "a one" in captured["body"]
    assert "b two" in captured["body"]


def test_flush_to_sd_degrades_on_writer_error(diag):
    diag.log("a", "one")

    def boom(body):
        raise OSError("no card")
    diag._write_log_file = boom
    # A write failure must degrade to False, never raise.
    assert diag.flush_to_sd(lambda fn: fn()) is False


def test_flush_to_sd_none_wrapper_is_noop(diag):
    diag.log("a", "one")
    assert diag.flush_to_sd(None) is False

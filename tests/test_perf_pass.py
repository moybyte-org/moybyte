"""The pass's perf stage with no board: the record, the history, the noise
band and the comparison a pass prints (tools/perf_pass.py)."""

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import perf_pass as pp                                          # noqa: E402


@pytest.fixture(autouse=True)
def history_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MOYBYTE_PERF_DIR", str(tmp_path / "perf"))
    return tmp_path / "perf"


def rec(commit, **metrics):
    return pp.record("tdeck", metrics, commit, tree="t",
                     date="2026-11-15T10:00:00", secs=1.0)


def fps(v, samples=None):
    return pp.metric(v, "fps", pp.HIGHER, samples or [v, v, v])


def ms(v, samples=None):
    return pp.metric(v, "ms", pp.LOWER, samples or [v, v])


# -- the record ----------------------------------------------------------------


def test_a_record_names_the_run_and_each_metric_carries_unit_and_samples():
    r = pp.record("p4", {"Brick Siege fps": fps(61.04, [60.5, 61.04, 61.5])},
                  "c2d35a0c+", image={"stamp": "c2d35a0c+", "sha256": "ab",
                                      "flashed": True},
                  secs=170.04, conditions={"wifi_held": []}, tree="t")
    assert r["format"] == pp.FORMAT and r["board"] == "p4"
    assert r["commit"] == "c2d35a0c+" and r["image"]["flashed"] is True
    assert r["secs"] == 170.0 and r["date"][:4].isdigit() and "T" in r["date"]
    m = r["metrics"]["Brick Siege fps"]
    assert m == {"value": 61.04, "unit": "fps", "better": "higher", "n": 3,
                 "samples": [60.5, 61.04, 61.5]}
    assert json.loads(json.dumps(r)) == r          # one JSON line a run


def test_a_metric_the_board_could_not_take_is_none_never_zero():
    m = pp.metric(None, "us", pp.LOWER)
    assert m["value"] is None and m["n"] == 0


def test_the_history_is_one_line_a_run_under_the_board_and_skips_garbage(
        history_dir):
    path = pp.append(rec("aaaaaaaa", x=fps(60)))
    assert path == str(history_dir / "tdeck.jsonl")
    with open(path, "a") as f:
        f.write("not json\n")
    pp.append(rec("bbbbbbbb", x=fps(59)))
    got = pp.history("tdeck")
    assert [r["commit"] for r in got] == ["aaaaaaaa", "bbbbbbbb"]
    assert pp.history("p4") == []


def test_the_previous_run_is_the_newest_at_another_commit():
    a, b, c = rec("aaaaaaaa"), rec("bbbbbbbb"), rec("bbbbbbbb")
    c["date"] = "later"
    # c reruns b's commit, so it compares with a, before both.
    assert pp.previous([a, b, c], c) is a
    assert pp.previous([a], rec("aaaaaaaa")) is None
    assert pp.previous([], rec("x")) is None


# -- the band and the comparison -----------------------------------------------


def test_the_band_is_the_units_default_until_the_samples_spread_wider():
    assert pp.band(fps(60)) == pp.DEFAULT_BAND["fps"]
    assert pp.band(ms(8)) == pp.DEFAULT_BAND["ms"]
    # 50, 60, 70: a MAD of 10 on 60 is 16.7%, times three.
    noisy = fps(60, [50, 60, 70])
    assert pp.band(noisy) == pytest.approx(50.0)
    # Either run's spread widens it.
    assert pp.band(fps(60), noisy) == pytest.approx(50.0)
    # Two samples say nothing about spread.
    assert pp.band(ms(8, [4, 12])) == pp.DEFAULT_BAND["ms"]


@pytest.mark.parametrize("old, new, flag", [
    (fps(60), fps(45), "WORSE"),          # the drop a floor never sees
    (fps(45), fps(60), "better"),
    (fps(60), fps(58), ""),               # inside the 5% band
    (ms(8), ms(12), "WORSE"),             # 8 ms to 12 ms
    (ms(12), ms(8), "better"),
    (ms(3.0), ms(3.4), ""),               # 13% but under the 1 ms floor
    (ms(4.9), ms(5.6), ""),               # an idle loop's 0.1 ms steps
    (fps(60, [40, 60, 80]), fps(45, [40, 45, 50]), ""),   # inside its noise
])
def test_a_move_is_flagged_past_its_band_by_the_metrics_direction(old, new, flag):
    rows = pp.compare(rec("b", x=new), rec("a", x=old))
    assert [r[-1] for r in rows] == [flag]


def test_the_change_is_in_percent_of_the_previous_value():
    (name, m, prev, change, band, flag), = pp.compare(
        rec("b", x=ms(12)), rec("a", x=ms(8)))
    assert (name, prev, change, band) == ("x", 8.0, 50.0, 10.0)


def test_a_metric_new_absent_or_from_zero_is_never_divided_by():
    old = rec("a", idle=fps(0), gone=fps(None))
    new = rec("b", idle=fps(30), gone=fps(None), fresh=ms(4))
    rows = {r[0]: r for r in pp.compare(new, old)}
    assert rows["idle"][3] is None and rows["idle"][5] == "better"
    assert rows["gone"][2] is None and rows["gone"][5] == ""
    assert rows["fresh"][2] is None and rows["fresh"][5] == ""
    rows = pp.compare(new, None)
    assert all(r[2] is None and r[5] == "" for r in rows)


def test_the_block_marks_worse_rows_and_counts_them():
    old = rec("aaaaaaaa", **{"Brick Siege fps": fps(60), "desk idle loop": ms(8)})
    new = rec("bbbbbbbb", **{"Brick Siege fps": fps(45), "desk idle loop": ms(8.1)})
    new["errors"] = {"Jet Teapot": "no OTA signing key"}
    out = pp.block(new, old).splitlines()
    assert out[0].startswith("tdeck perf  (") and "vs aaaaaaaa" in out[0]
    assert out[1].split()[:7] == ["metric", "unit", "value", "n", "prev",
                                  "change", "band"]
    bs = next(ln for ln in out if "Brick Siege fps" in ln)
    assert bs.split()[-7:] == ["45.0", "3", "60.0", "-25.0%", "5%", "<<<",
                               "WORSE"]
    assert next(ln for ln in out if "desk idle loop" in ln).endswith("10%")
    assert "  ! Jet Teapot: no OTA signing key" in out
    assert out[-1] == "  1 metric moved WORSE past its band"


def test_the_first_run_says_it_is_the_baseline():
    out = pp.block(rec("aaaaaaaa", x=fps(60)), None).splitlines()
    assert "this is the baseline" in out[0]
    assert next(ln for ln in out if ln.lstrip().startswith("x ")).split()[4] == "-"


# -- the meters' readings --------------------------------------------------------


def test_idle_reads_give_drawn_fps_and_the_collectors_stops():
    # (ticks_ms, drawn, (collections, pause us, longest since last read))
    reads = [(1000, 100, (5, 900, 0)), (3000, 160, (6, 1400, 500)),
             (5000, 220, (6, 1400, 0)), (7000, 280, (8, 2600, 700))]
    m = pp.idle_metrics(reads)
    assert m["desk idle fps"]["value"] == 30.0 and m["desk idle fps"]["n"] == 3
    # Three collections over six seconds.
    assert m["desk idle gc collections"]["value"] == 30.0
    assert m["desk idle gc collections"]["unit"] == "/min"
    assert m["desk idle gc longest pause"]["value"] == 700


def test_a_build_without_gc_pauses_reports_absent_not_zero():
    reads = [(1000, 100, None), (3000, 160, None), (5000, 220, None)]
    m = pp.idle_metrics(reads)
    assert m["desk idle gc collections"]["value"] is None
    assert m["desk idle gc longest pause"]["value"] is None
    assert m["desk idle fps"]["value"] == 30.0


def test_heapcaps_reads_as_kib_and_an_absent_heap_stays_absent():
    line = ("HEAPCAPS psram=8388608/4194304/2097152/1048576 "
            "sram=307200/102400/40960/20480 dma=-  gc=2097152/1048576/3")
    m = pp.heap_metrics(line)
    assert m["heap sram free"]["value"] == 100.0
    assert m["heap sram largest"]["value"] == 40.0
    assert m["heap psram free"]["value"] == 4096.0
    assert m["heap gc held"]["value"] == 2048.0
    assert m["heap gc live"]["better"] == pp.LOWER
    m = pp.heap_metrics("HEAPCAPS psram=- sram=- dma=- gc=-")
    assert all(v["value"] is None for v in m.values())


def test_each_perf_line_takes_the_loop_line_of_its_period():
    lines = ["PERF cart=Sakura fps=30/60 busy=2ms",
             "LOOP n=120 ms=2.4 inputs=0.1 sleep=14.0",
             "Moybyte 1234 PERF cart=Sakura fps=30/60 busy=2ms",
             "PERF cart=Sakura fps=30/60 busy=3ms",
             "LOOP n=120 ms=2.6 inputs=0.1 sleep=14.0",
             "noise"]
    got = pp.pair_loop(lines)
    assert [g["loop_ms"] for g in got] == [2.4, None, 2.6]
    assert [g["cart"] for g in got] == ["Sakura"] * 3


def test_a_frame_is_the_loop_work_behind_one_drawn_frame():
    # A paced cart: two loop frames of 2.4 ms each per drawn frame.
    assert pp.frame_ms({"fps": (30.0, 60.0), "loop_ms": 2.4}) == pytest.approx(4.8)
    # No LOOP line: PERF's whole-ms busy= stands in.
    assert pp.frame_ms({"fps": (60.0, 60.0), "busy": 9.0, "loop_ms": None}) == 9.0
    # Nothing drawn: no frame to cost.
    assert pp.frame_ms({"fps": (0.0, 60.0), "loop_ms": 1.0}) is None


def test_a_loop_that_spins_between_frames_has_no_frame_cost():
    # A run in the kernel's front: 1619 loop frames a second, 30 drawn, no
    # sleep -- LOOP's 0.6 ms is the spin's, and 0.6 * 1619 / 30 is the period.
    s = {"fps": (30.0, 1619.0), "loop_ms": 0.6}
    assert pp.spins(s) and pp.frame_ms(s) is None
    assert not pp.spins({"fps": (30.0, 60.0)})


def test_perf_samples_give_drawn_fps_and_frame_ms():
    samples = [{"fps": (60.0, 60.0), "loop_ms": 9.0},
               {"fps": (58.0, 58.0), "loop_ms": 11.0},
               {"fps": (59.0, 59.0), "loop_ms": None, "busy": None}]
    m = pp.perf_metrics(samples, "window drag")
    assert m["window drag fps"]["value"] == 59.0
    assert m["window drag frame"]["value"] == 10.0
    assert m["window drag frame"]["n"] == 2


def test_the_history_keeps_the_metrics_in_the_order_they_were_taken():
    names = ["desk idle fps", "heap sram free", "Brick Siege fps", "Brick Siege frame"]
    pp.append(rec("aaaaaaaa", **{n: fps(1) for n in names}))
    assert list(pp.history("tdeck")[0]["metrics"]) == names

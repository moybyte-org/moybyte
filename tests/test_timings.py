"""tools/timings.py: the shared timing log's writer and its summary."""

import csv
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import timings                                                  # noqa: E402


def test_a_run_appends_one_line_a_lane_under_one_header(tmp_path):
    log = str(tmp_path / "sub" / "timings.csv")
    timings.record("build", [("tdeck", 312.44), ("tdeck/compile", 201.0)],
                   ccache=(900, 12), path=log)
    timings.record("preflight", [("preflight", 870.0)], "fail", path=log)
    with open(log, newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == timings.FIELDS
    assert len(rows) == 4
    whole, part, pre = (dict(zip(timings.FIELDS, r)) for r in rows[1:])
    assert (whole["tool"], whole["lane"], whole["seconds"]) == ("build", "tdeck", "312.4")
    assert (whole["ccache_hits"], whole["ccache_misses"]) == ("900", "12")
    assert part["ccache_hits"] == "" and part["lane"] == "tdeck/compile"
    assert pre["status"] == "fail"
    checkout, rev = timings.checkout_and_rev(ROOT)
    assert whole["checkout"] == checkout and whole["rev"] == rev != "?"


def test_the_checkout_is_main_or_the_worktrees_name():
    checkout, _ = timings.checkout_and_rev(ROOT)
    top = subprocess.run(["git", "-C", ROOT, "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True).stdout.strip()
    assert checkout in ("main", os.path.basename(top))


def test_the_command_line_writes_where_moybyte_timings_says(tmp_path, monkeypatch):
    log = tmp_path / "t.csv"
    monkeypatch.setenv("MOYBYTE_TIMINGS", str(log))
    assert timings.main(["add", "--tool", "pass", "--ccache", "5,6",
                         "pass=100.5", "tdeck/suite=40"]) == 0
    rows = timings.read()
    assert [(r["lane"], r["seconds"]) for r in rows] == [("pass", 100.5),
                                                          ("tdeck/suite", 40.0)]
    assert rows[0]["ccache_hits"] == "5"


def test_the_summary_shows_recent_wholes_and_flags_a_slow_lane(tmp_path):
    log = str(tmp_path / "t.csv")
    for secs in (100, 110, 90, 105, 200):
        timings.record("build", [("tdeck", secs), ("tdeck/compile", secs / 2)],
                       path=log)
    timings.record("build", [("tdeck", 999)], "fail", path=log)
    out = timings.summary(timings.read(log))
    recent, medians = out.split("median of the last")
    assert recent.count(" tdeck ") == 6 and "tdeck/compile" not in recent
    lines = {ln.split()[1]: ln for ln in medians.splitlines()[2:]}
    assert "1m45s" in lines["tdeck"]                # median of the passing five
    assert "3m20s" in lines["tdeck"] and "slow: 1.9x" in lines["tdeck"]
    assert " 5 " in lines["tdeck"]                  # the failed run is not a sample
    assert list(lines) == ["tdeck", "tdeck/compile"]
    assert "no timed runs" in timings.summary([])
    assert timings.summary(timings.read(log), tool="pass").startswith("no timed")


def test_durations_read_as_a_person_says_them():
    assert timings.dur(3.24) == "3.2s"
    assert timings.dur(872) == "14m32s"
    assert timings.dur(3720) == "1h02m"


def test_a_board_build_times_its_phases_and_keeps_its_exit_status(tmp_path):
    """The lib times the build.sh that sources it, passing or failing."""
    sh = tmp_path / "build.sh"
    sh.write_text(
        "set -euo pipefail\n"
        "SCRIPT_DIR='%s/firmware/lilygo_t_deck_plus_mainline' REPO_ROOT='%s'\n"
        "source \"$REPO_ROOT/tools/esp32_build_lib.sh\"\n"
        "moybyte_phase patches\n"
        "printf 'Executing \"ninja all\"...\\n[0/1] Re-running CMake...\\n"
        "[1/3] Generating binary image from built executable\\n"
        "[2/3] Linking CXX executable micropython.elf\\n"
        "[3/3] cd ... check_sizes.py\\n' | moybyte_phase_watch m\n"
        "while read -r n at; do moybyte_phase \"$n\" \"$at\"; done < m\n"
        "[ \"${FAIL:-0}\" = 1 ] && false\n"
        "true\n" % (ROOT, ROOT))
    log = tmp_path / "t.csv"
    env = dict(os.environ, MOYBYTE_TIMINGS=str(log))
    ok = subprocess.run(["bash", str(sh)], cwd=tmp_path, env=env,
                        capture_output=True, text=True)
    assert ok.returncode == 0, ok.stderr
    assert "== time: toolchain" in ok.stdout and "== build times (tdeck, ok)" in ok.stdout
    bad = subprocess.run(["bash", str(sh)], cwd=tmp_path, capture_output=True,
                         text=True, env=dict(env, FAIL="1"))
    assert bad.returncode == 1
    rows = timings.read(str(log))
    assert [r["lane"] for r in rows if r["status"] == "ok"] == [
        "tdeck", "tdeck/toolchain", "tdeck/patches", "tdeck/compile",
        "tdeck/link", "tdeck/image"]
    assert any(r["lane"] == "tdeck" and r["status"] == "fail" for r in rows)


def test_a_script_sourcing_the_lib_for_one_function_is_not_timed(tmp_path):
    log = tmp_path / "t.csv"
    out = subprocess.run(
        ["bash", "-c", "set -eu; REPO_ROOT='%s'; source tools/esp32_build_lib.sh; "
         "moybyte_phase x; echo fine" % ROOT],
        cwd=ROOT, env=dict(os.environ, MOYBYTE_TIMINGS=str(log)),
        capture_output=True, text=True)
    assert out.stdout.strip() == "fine" and not log.exists()

"""`tools/board.py pass` with no board attached: the plan and its refusals,
the P4 builds' one-at-a-time slot, the table, read from fake step runs.

The steps are commands handed to a runner; here the runner is a fake that
records them, writes the junit XML a suite would, and holds a build long
enough to see what overlaps. The board dirs and suites are the tree's own.
"""

import os
import sys
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import board                                                    # noqa: E402
import board_pass                                               # noqa: E402

DIRS = board.boards()
CONSOLES = sorted(n for n in DIRS if board.is_console(DIRS[n]))


def args(*argv):
    return board_pass.parser().parse_args(list(argv))


# -- the plan ----------------------------------------------------------------


def test_every_console_board_has_a_suite_and_the_zero_has_none():
    s = board_pass.suites(ROOT, DIRS)
    assert sorted(s) == CONSOLES
    for name, (path, var) in s.items():
        assert os.path.isfile(os.path.join(ROOT, path))
        assert var.startswith("MOYBYTE_") and var.endswith("_PORT")
        with open(os.path.join(ROOT, path)) as f:
            assert var in f.read()
    assert "xiao_zero" not in s


@pytest.mark.parametrize("argv, says", [
    ((), "which boards"),
    (("tdeck", "--all"), "not both"),
    (("nope",), "no board 'nope'"),
    (("xiao_zero",), "never passed"),
])
def test_a_pass_that_cannot_start_says_why(argv, says):
    with pytest.raises(board_pass.PassError, match=says):
        board_pass.plan(args(*argv), DIRS, main_checkout=False)


def test_all_is_every_console_board_and_never_the_zero():
    assert board_pass.plan(args("--all"), DIRS, False) == CONSOLES


def test_the_main_checkout_is_refused_for_a_build_and_says_why():
    with pytest.raises(board_pass.PassError) as e:
        board_pass.plan(args("tdeck"), DIRS, main_checkout=True)
    assert "main checkout" in str(e.value)
    assert "overwrites" in str(e.value)
    assert "--main" in str(e.value)


@pytest.mark.parametrize("flag", ["--main", "--skip-build", "--no-flash"])
def test_the_main_checkout_runs_what_builds_nothing_or_was_asked(flag):
    assert board_pass.plan(args("tdeck", flag), DIRS, main_checkout=True) == ["tdeck"]


def test_the_refusal_exits_2_before_touching_anything(capsys):
    calls = []
    rc = board_pass.main(["tdeck"], run=lambda *a, **k: calls.append(a),
                         dirs=DIRS, main_checkout=True)
    assert rc == 2 and calls == []
    assert "main checkout" in capsys.readouterr().err


def test_this_checkout_knows_whether_it_is_a_worktree():
    assert board_pass.in_main_checkout(ROOT) in (True, False)


def test_board_py_hands_pass_to_the_pass(monkeypatch):
    got = []
    monkeypatch.setattr(board_pass, "main", lambda argv: got.append(argv) or 7)
    assert board.main(["pass", "tdeck", "-k", "wasm"]) == 7
    assert got == [["tdeck", "-k", "wasm"]]


# -- a pass, on fake steps ---------------------------------------------------


class FakeSteps:
    """The runner: records each command, holds builds, writes junit XML."""

    def __init__(self, fail=(), build_s=0.15, s3_barrier=None, build_rc=None):
        self.cmds = []
        self.fail = dict(fail)
        self.build_s = build_s
        self.barrier = s3_barrier
        self.build_rc = build_rc or {}
        self.web_rc = 0
        self.live = {}
        self.peak = {}
        self.lock = threading.Lock()

    def builds(self):
        return [c[1] for c, _e in self.cmds if c[0] == "bash"]

    def chip_of(self, build_sh):
        for name, d in DIRS.items():
            if build_sh == os.path.join(d, "build.sh"):
                return name, board.board_file(d)["board"]["chip"]

    def __call__(self, cmd, log, env=None):
        with self.lock:
            self.cmds.append((list(cmd), env))
        with open(log, "a") as f:
            f.write("$ %s\n" % " ".join(cmd))
        if cmd[:2] == ["bash", board_pass.WEB_BUILD]:
            return self.web_rc
        if cmd[0] == "bash":
            name, chip = self.chip_of(cmd[1])
            with self.lock:
                self.live[chip] = self.live.get(chip, 0) + 1
                self.peak[chip] = max(self.peak.get(chip, 0), self.live[chip])
            if chip == "esp32s3" and self.barrier is not None:
                self.barrier.wait()
            time.sleep(self.build_s)
            with self.lock:
                self.live[chip] -= 1
            with open(log, "a") as f:
                f.write("App image: 3000000 bytes of a 4128768-byte ota_0 slot "
                        "-- 1128768 bytes headroom (1102 KB)\n")
            return self.build_rc.get(name, 0)
        if "pytest" in cmd:
            xml = cmd[cmd.index("--junitxml") + 1]
            failing = self.fail.get(env and next(
                (n for n, (_p, v) in board_pass.suites(ROOT, DIRS).items()
                 if v in env), None), ())
            cases = ['<testcase classname="t" name="test_ok_%d"/>' % i
                     for i in range(3)]
            cases.append('<testcase classname="t" name="test_skip">'
                         '<skipped message="no"/></testcase>')
            cases += ['<testcase classname="t" name="%s"><failure message="x"/>'
                      '</testcase>' % n for n in failing]
            with open(xml, "w") as f:
                f.write("<testsuites><testsuite>%s</testsuite></testsuites>"
                        % "".join(cases))
        return 0


@pytest.fixture
def bench(tmp_path, monkeypatch):
    """Every board on a fake port nobody holds, an image in dist, the build
    lock in tmp."""
    monkeypatch.setenv("MOYBYTE_LOCK_DIR", str(tmp_path / "locks"))
    image = tmp_path / "image.bin"
    image.write_bytes(b"\0")
    monkeypatch.setattr(board_pass, "image_path", lambda d: str(image))
    monkeypatch.setattr(board_pass, "image_stamp", lambda d: "c2d35a0c")
    monkeypatch.setattr(board, "resolve", lambda n, dirs: ("/dev/fake-" + n, "t"))
    monkeypatch.setattr(board, "stable", lambda p: p)
    monkeypatch.setattr(board, "holders", lambda p: [])
    return tmp_path


def run_pass(bench, steps, *argv):
    return board_pass.main(list(argv) + ["--logs", str(bench / "logs")],
                           run=steps, dirs=DIRS, main_checkout=False)


def test_the_p4s_build_one_at_a_time_and_the_s3s_together(bench):
    # The barrier breaks unless both S3 builds are in it at once.
    steps = FakeSteps(s3_barrier=threading.Barrier(2, timeout=5))
    assert run_pass(bench, steps, "--all") == 0
    assert steps.peak == {"esp32p4": 1, "esp32s3": 2}


def test_each_step_gets_the_resolved_port_and_the_suite_its_variable(bench):
    steps = FakeSteps()
    assert run_pass(bench, steps, "tdeck", "-k", "wasm") == 0
    verbs = [c[3] for c, _e in steps.cmds if c[1:2] == [board_pass.BOARD_PY]]
    assert verbs[0] == "flash" and "desk" in verbs
    for c, _e in steps.cmds:
        if c[1:2] == [board_pass.BOARD_PY]:
            assert c[-2:] == ["--port", "/dev/fake-tdeck"]
    suite = [(c, e) for c, e in steps.cmds if "pytest" in c]
    assert len(suite) == 1
    cmd, env = suite[0]
    assert "tests/test_tdeck_on_glass.py" in cmd and cmd[-2:] == ["-k", "wasm"]
    assert env["MOYBYTE_TDECK_PORT"] == "/dev/fake-tdeck"


def test_the_tdeck_is_put_at_volume_0_around_its_suite(bench):
    steps = FakeSteps()
    run_pass(bench, steps, "tdeck", "guition_s3")
    vols = [c[2] for c, _e in steps.cmds if "vol 0" in c]
    assert vols == ["tdeck", "tdeck"]


def test_the_browser_console_is_built_once_before_any_image(bench):
    steps = FakeSteps()
    assert run_pass(bench, steps, "tdeck", "guition_s3") == 0
    assert steps.builds()[0] == board_pass.WEB_BUILD
    assert steps.builds().count(board_pass.WEB_BUILD) == 1
    assert len(steps.builds()) == 3


def test_a_failed_browser_build_stops_every_board_that_builds(bench, capsys):
    steps = FakeSteps()
    steps.web_rc = 2
    assert run_pass(bench, steps, "tdeck", "p4") == 1
    assert steps.builds() == [board_pass.WEB_BUILD]
    out = capsys.readouterr().out
    assert out.count("web build failed (web.build.log)") == 2


def test_skip_build_flashes_and_no_flash_only_runs_the_suite(bench):
    steps = FakeSteps()
    run_pass(bench, steps, "tdeck", "--skip-build")
    assert not [c for c, _e in steps.cmds if c[0] == "bash"]
    assert [c for c, _e in steps.cmds if "flash" in c]
    steps = FakeSteps()
    run_pass(bench, steps, "tdeck", "--no-flash")
    assert not [c for c, _e in steps.cmds if c[0] == "bash" or "flash" in c]
    assert [c for c, _e in steps.cmds if "pytest" in c]


def test_shot_takes_one_picture_a_board_at_the_end(bench):
    steps = FakeSteps()
    run_pass(bench, steps, "tdeck", "--shot")
    shots = [c for c, _e in steps.cmds if "shot" in c]
    assert len(shots) == 1 and shots[0][4].endswith("tdeck.png")
    assert steps.cmds[-1][0] == shots[0]


def test_the_table_has_one_row_a_board_with_the_failures_by_name(bench, capsys):
    steps = FakeSteps(fail={"p4": ["test_wasm_runs"]})
    assert run_pass(bench, steps, "tdeck", "p4") == 1
    out = capsys.readouterr().out.splitlines()
    head = out.index(next(ln for ln in out if ln.startswith("board  ")))
    assert out[head].split()[:6] == ["board", "image", "headroom", "passed",
                                     "skipped", "failed"]
    rows = {ln.split()[0]: ln.split() for ln in out[head + 1:head + 3]}
    assert rows["tdeck"][1:6] == ["c2d35a0c", "1102", "KB", "3", "1"]
    assert rows["p4"][5:8] == ["1", "1", "test_wasm_runs"]
    assert any(ln.startswith("p4: ") and ln.endswith("p4.suite.log")
               for ln in out)


def test_a_failed_build_stops_its_board_and_names_its_log(bench, capsys):
    steps = FakeSteps(build_rc={"tdeck": 2})
    assert run_pass(bench, steps, "tdeck", "guition_s3") == 1
    out = capsys.readouterr().out
    assert "build failed (tdeck.build.log)" in out
    assert not [c for c, _e in steps.cmds if c[2:3] == ["tdeck"]]
    assert [c for c, _e in steps.cmds if c[2:3] == ["guition_s3"]]


def test_a_held_port_skips_its_board_and_names_the_holder(bench, monkeypatch,
                                                          capsys):
    monkeypatch.setattr(board, "holders", lambda p: (
        [(4242, "python -m pytest tests/test_p4_on_glass.py")]
        if p.endswith("p4") else []))
    steps = FakeSteps()
    assert run_pass(bench, steps, "tdeck", "p4") == 1
    assert "port held by pid 4242" in capsys.readouterr().out
    assert not [c for c, _e in steps.cmds if "p4" in c[2:3]]


def test_a_suite_that_never_ran_says_so(bench, capsys):
    class NoXml(FakeSteps):
        def __call__(self, cmd, log, env=None):
            return 1 if "pytest" in cmd else super().__call__(cmd, log, env)
    assert run_pass(bench, NoXml(), "tdeck") == 1
    assert "suite did not run" in capsys.readouterr().out


# -- what a pass reads --------------------------------------------------------


def test_headroom_is_the_last_one_the_build_printed(tmp_path):
    log = tmp_path / "b.log"
    log.write_text("App image: 1 bytes of a 2-byte ota_0 slot -- 2048 bytes "
                   "headroom (2 KB)\nApp image: 1 bytes of a 2-byte ota_0 slot "
                   "-- 1536000 bytes headroom (1500 KB)\n")
    assert board_pass.headroom_of(str(log)) == "1500 KB"
    assert board_pass.headroom_of(str(tmp_path / "none.log")) == "-"


def test_junit_counts_errors_as_failures(tmp_path):
    xml = tmp_path / "j.xml"
    xml.write_text('<testsuites><testsuite><testcase name="a"/>'
                   '<testcase name="b"><error message="setup"/></testcase>'
                   '<testcase name="c"><skipped/></testcase>'
                   '</testsuite></testsuites>')
    assert board_pass.junit_counts(str(xml)) == (1, 1, 1, ["b"])
    assert board_pass.junit_counts(str(tmp_path / "missing.xml")) is None


class RaceOnce(FakeSteps):
    """The first build of `name` loses the component cache's lock (or fails
    for another reason, `why`); every later one passes."""

    def __init__(self, name, why=board_pass.LOST_RACE):
        super().__init__()
        self.name, self.why, self.builds = name, why, 0

    def __call__(self, cmd, log, env=None):
        if (cmd[0] == "bash" and cmd[1] != board_pass.WEB_BUILD
                and self.chip_of(cmd[1])[0] == self.name):
            self.builds += 1
            if self.builds == 1:
                with open(log, "a") as f:
                    f.write("fatal: Unable to create '/x/%s\n" % self.why)
                return 2
        return super().__call__(cmd, log, env)


def test_a_build_that_lost_the_cache_lock_runs_again(bench, monkeypatch):
    monkeypatch.setattr(board_pass, "RETRY_PAUSE_S", (0, 0))
    steps = RaceOnce("p4")
    assert run_pass(bench, steps, "p4", "tdeck") == 0
    assert steps.builds == 2


def test_a_build_that_failed_for_itself_is_not_run_again(bench, monkeypatch):
    monkeypatch.setattr(board_pass, "RETRY_PAUSE_S", (0, 0))
    steps = RaceOnce("p4", why="error: 'x' undeclared")
    assert run_pass(bench, steps, "p4") == 1
    assert steps.builds == 1

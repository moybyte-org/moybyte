"""tools/ci_shard.py: CI's test shards cover the suite, every file once, and
the workflow asks for the shards its matrix runs."""

import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import ci_shard  # noqa: E402

CI = os.path.join(ROOT, ".github", "workflows", "ci.yml")


def _workflow_shards():
    with open(CI, encoding="utf-8") as f:
        wf = f.read()
    m = re.search(r"^\s+shard: \[([\d, ]+)\]$", wf, re.M)
    assert m, "ci.yml's test matrix has no shard axis"
    ks = [int(x) for x in m.group(1).split(",")]
    calls = set(re.findall(r"tools/ci_shard\.py (\d+) \$\{\{ matrix\.shard \}\}", wf))
    return wf, ks, calls


def test_every_test_file_lands_in_exactly_one_shard():
    every = sorted(os.path.relpath(p, ROOT).replace(os.sep, "/")
                   for p in glob.glob(os.path.join(ROOT, "tests", "test_*.py")))
    _wf, ks, _calls = _workflow_shards()
    for n in (1, 2, len(ks), 7):
        placed = [f for _s, fs in ci_shard.shards(n) for f in fs]
        assert len(placed) == len(set(placed)), "a file in two shards"
        assert sorted(placed + list(ci_shard.EXCLUDED)) == every, "a file in no shard"


def test_the_suite_has_no_test_file_the_shards_cannot_see():
    nested = [p for p in glob.glob(os.path.join(ROOT, "tests", "**", "test_*.py"),
                                   recursive=True)
              if os.path.dirname(p) != os.path.join(ROOT, "tests")]
    nested += glob.glob(os.path.join(ROOT, "tests", "**", "*_test.py"), recursive=True)
    assert nested == []


def test_the_workflow_runs_every_shard_and_the_excluded_file_alone():
    wf, ks, calls = _workflow_shards()
    assert ks == list(range(1, len(ks) + 1))
    assert calls == {str(len(ks))}
    for f in ci_shard.EXCLUDED:
        assert ".venv/bin/python -m pytest %s" % f in wf


def test_shards_take_the_heaviest_first_and_an_unweighed_file_weighs_the_median():
    w = {"a": 10.0, "b": 6.0, "c": 5.0, "d": 1.0}
    assert ci_shard.shards(2, ["a", "b", "c", "d"], w) == [(11.0, ["a", "d"]),
                                                           (11.0, ["b", "c"])]
    # the median of the known weights (5.5) for the new file
    assert ci_shard.shards(2, ["a", "b", "c", "d", "new"], w) == [(15.0, ["a", "c"]),
                                                                  (12.5, ["b", "d", "new"])]


def test_weights_are_summed_per_file_from_a_durations_log():
    log = ["12.50s call     tests/test_a.py::test_one",
           "0.50s setup    tests/test_a.py::test_one",
           "3.00s call     tests/test_b.py::T::test_two[x]",
           "(1234 durations < 0.005s hidden.)"]
    assert ci_shard.weigh(log) == {"tests/test_a.py": 13.0, "tests/test_b.py": 3.0}

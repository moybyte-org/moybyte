"""tools/shell_bench.py --report: a row sits beside the same row of the
earlier run, and a name a run repeats pairs by its occurrence."""

import json

from tools import shell_bench


def _run(d, medians):
    rows = [{"name": "meta"}]
    for name, median in medians:
        rows.append({"name": name, "layers": {}, "perf": None,
                     "stats": {"n": 2, "median": median, "p90": median, "max": median}})
    (d / "p4_shell.json").write_text(json.dumps(rows))


def _line(text, name, nth=0):
    return [ln for ln in text.splitlines() if ln.startswith("| %s |" % name)][nth]


def test_a_repeated_row_pairs_by_its_occurrence(tmp_path):
    base, cand = tmp_path / "base", tmp_path / "cand"
    base.mkdir()
    cand.mkdir()
    rep = "editor workspace: settings"
    _run(base, [(rep, 85), ("other", 10), (rep, 40)])
    _run(cand, [(rep, 86), ("other", 10), (rep, 41)])
    text = shell_bench.report(str(cand), "p4", str(base))
    assert "| 85 → 86 | +1 |" in _line(text, rep, 0)
    assert "| 40 → 41 | +1 |" in _line(text, rep, 1)
    assert "In the earlier run only" not in text


def test_an_occurrence_the_earlier_run_lacks_is_new(tmp_path):
    base, cand = tmp_path / "base", tmp_path / "cand"
    base.mkdir()
    cand.mkdir()
    _run(base, [("row", 5), ("gone", 7)])
    _run(cand, [("row", 5), ("row", 6)])
    text = shell_bench.report(str(cand), "p4", str(base))
    assert "| new |" in _line(text, "row", 1)
    assert "In the earlier run only: gone" in text

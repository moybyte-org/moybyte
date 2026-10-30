"""tools/input_gate.py: the input gate's verdicts, its like-for-like column,
its baseline store and sentinel, and one board's gate played against saved
bench rows with the flash and the bench faked.

The fixtures are four T-Deck runs cut from a real gate (dev against a
candidate, two runs each): six rows, the Text console's and Code fling's
frames kept whole."""

import json
import os
import shutil
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import input_bench as ib  # noqa: E402
import input_gate as g  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures", "input_gate")


def _runs(label):
    return [g.load_run(os.path.join(FIX, "%s_tdeck_%d" % (label, n)), "tdeck") for n in (1, 2)]


def _rows():
    return dict(((r["surface"], r["input"]), r) for r in g.compare(_runs("dev"), _runs("ui1")))


# -- the verdicts ------------------------------------------------------------------

def test_the_line_is_devs_median_and_near_is_a_millisecond_over_it():
    v = g.verdict([30.0, 30.2, 30.1], [28.0, 28.1, 28.2])
    assert (v["verdict"], v["base"], v["cand"], v["delta"]) == ("pass", 30.1, 28.1, -2.0)
    assert g.verdict([30.0] * 3, [31.5] * 3)["verdict"] == "fail"
    assert g.verdict([30.0] * 3, [30.9] * 3)["verdict"] == "near"      # over, within 1 ms
    assert g.verdict([30.0] * 3, [29.1] * 3)["verdict"] == "pass"      # under: holds
    assert g.verdict([30.0] * 3, [30.0] * 3)["verdict"] == "pass"      # level: holds
    assert g.verdict([30.0] * 3, [31.0] * 3)["verdict"] == "near"      # on the edge
    # --tol moves the line, never the medians
    v = g.verdict([30.0] * 3, [31.5] * 3, tol=2.0)
    assert (v["verdict"], v["base"], v["delta"]) == ("pass", 30.0, -0.5)
    assert g.verdict([30.0] * 3, [32.5] * 3, tol=2.0)["verdict"] == "near"
    assert g.verdict([30.0] * 3, [33.5] * 3, tol=1.0)["verdict"] == "fail"
    assert g.verdict([30.0] * 3, [27.5] * 3, tol=1.0)["verdict"] == "pass"


def test_a_side_under_three_runs_that_spreads_is_noisy_until_a_third_settles_it():
    v = g.verdict([20.3, 20.3], [21.2, 26.2])
    assert v["verdict"] == "near" and v["noisy"] == ["cand"]
    # a third run: the median decides, the spread no longer does
    v = g.verdict([20.3, 20.3, 20.4], [21.2, 26.2, 21.4])
    assert v["noisy"] == [] and v["cand"] == 21.4 and v["verdict"] == "fail"
    v = g.verdict([35.7, 54.0, 35.7], [34.4, 52.1, 34.4])
    assert v["verdict"] == "pass" and v["unstable"] == ["base", "cand"]
    # three runs that still spread past twice --noise: the median decides, marked
    v = g.verdict([44.0, 47.7, 44.1], [44.5, 75.5, 81.7])
    assert v["verdict"] == "fail" and v["unstable"] == ["cand"]


def test_a_row_without_a_number_on_a_side_is_missing():
    assert g.verdict([], [20.0])["verdict"] == "missing"
    assert g.verdict([20.0, None], [None])["verdict"] == "missing"
    assert g.verdict([20.0, None], [19.0, 18.0])["base"] == 20.0


def test_the_fixture_rows_verdicts_and_their_surfaces():
    rows = _rows()
    assert rows[("Code", "fling")]["verdict"] == "pass"
    assert rows[("Code", "key_held")]["verdict"] == "fail"
    assert rows[("Code", "type_line")]["verdict"] == "pass"          # 0.4 under: holds
    assert rows[("Code", "ball_fast")]["noisy"] == ["cand"]
    assert rows[("Theme Config text", "drag")]["verdict"] == "pass"
    gs = g.groups(list(rows.values()))
    assert gs == {"Code": "fail", "Theme Config text": "pass", "Text console": "fail"}
    assert g.near_rows(list(rows.values())) == [("Code", "ball_fast")]


def test_the_table_and_the_gate_over_boards():
    rows = list(_rows().values())
    text = g.render("tdeck", rows, "dev", "ui1")
    assert "ui1 (2 runs) against dev (2 runs)" in text
    line = next(x for x in text.splitlines() if "key_held" in x)
    assert "29.9 29.8" in line and "31.1 30.9" in line and "+1.1" in line and "fail" in line
    assert "surfaces: Code fail | Theme Config text pass | Text console fail" in text
    p4 = [dict(r, verdict="pass") for r in rows if r["surface"] != "Text console"]
    s = g.summary({"tdeck": rows, "p4": p4}).splitlines()
    theme = next(x for x in s if x.strip().startswith("Theme"))
    assert theme.split()[-1] == "pass"
    console = next(x for x in s if x.strip().startswith("Text console"))
    assert console.split()[-3:] == ["fail", "-", "fail"]
    assert g._status({"tdeck": rows}) == 1
    assert g._status({"p4": p4[:1]}) == 0


# -- like-for-like -----------------------------------------------------------------

def test_a_probe_on_one_side_only_gets_a_like_for_like_column_beside_the_gated_number():
    rows = _rows()
    con = rows[("Text console", "fling")]
    assert con["probe"] == {"base": False, "cand": True}
    # the gated number and verdict stay the bench's
    assert (con["base"], con["cand"], con["verdict"]) == (24.4, 28.6, "fail")
    assert con["lfl"]["verdict"] == "pass" and con["lfl"]["cand"] == 22.8
    # an unprobed run's like-for-like number is its gate
    assert con["lfl"]["base_runs"] == con["base_runs"]
    # a probe on both sides: no column
    assert "probe" not in rows[("Code", "fling")]
    assert g.probed(_runs("dev")[0][("Code", "fling")]) is True
    assert g.probed({"gate": {"p90": 1}}) is None
    text = g.render("tdeck", list(rows.values()), "dev", "ui1")
    assert "probe reads on ui1 only; like-for-like 24.4 -> 22.8 (-1.6 pass)" in text


def test_like_for_like_classes_every_painted_frame_after_a_release_as_coast():
    def rec(t0, probe, late=0):
        f = dict((k, 0) for k in ib.FIELDS)
        f.update(t0=t0 * 1000, t1=t0 * 1000 + 15000, show=(t0 + 16 + late) * 1000,
                 total=15000, content=1000, probe=probe, flushes=-1, span=-1,
                 prev=t0 * 1000 - 5000)
        return [f[k] for k in ib.FIELDS]
    # one touch, released at 100 ms; the view stops at 120 ms and the two
    # paints after it present late
    probes = [0, 10, 20, 30, 30, 30]
    raw = sum((rec(60 + 20 * i, p, 30 if i > 3 else 0) for i, p in enumerate(probes)), [])
    row = {"raw": raw, "tail": None, "plan": {"marks": [[50, "touch", 100]]}}
    probed = ib.analyse(raw, row["plan"])
    assert probed["coast"]["n"] == 1 and probed["settle"]["n"] == 2
    assert probed["gate"]["p90"] < 25
    blind = list(raw)
    k, n = ib.FIELDS.index("probe"), len(ib.FIELDS)
    for i in range(len(blind) // n):
        blind[i * n + k] = ib.NONE_PROBE
    ignored = ib.analyse(blind, row["plan"])
    assert ignored["coast"]["n"] == 3 and ignored["settle"] is None
    assert g.lfl_p90(row) == ignored["gate"]["p90"] == 50.0
    assert g.probed(row) is True and g.probed({"raw": blind}) is False


# -- the baselines -----------------------------------------------------------------

def test_the_store_keys_runs_by_commit_board_and_day(tmp_path):
    st = g.Store(str(tmp_path))
    for run in ("dev_tdeck_1", "dev_tdeck_2"):
        st.add("abc123", "tdeck", "2026-10-30", os.path.join(FIX, run))
    assert [os.path.basename(p) for p in st.runs("abc123", "tdeck", "2026-10-30")] == [
        "dev_tdeck_1", "dev_tdeck_2"]
    assert st.runs("abc123", "tdeck", "2026-10-31") == []
    assert st.runs("abc123", "p4", "2026-10-30") == []
    assert st.keys("tdeck", "2026-10-30") == ["abc123"]
    cov = g.coverage(st.runs("abc123", "tdeck", "2026-10-30"), "tdeck")
    assert cov[("Code", "fling")] == 2 and len(cov) == 6
    assert g.windowed_of(st.runs("abc123", "tdeck", "2026-10-30"), "tdeck") is False
    assert st.retire("abc123", "tdeck", "2026-10-30") == 2
    assert st.runs("abc123", "tdeck", "2026-10-30") == []
    assert st.keys("tdeck", "2026-10-30") == []


def test_the_store_defaults_outside_the_repo_and_takes_an_override(monkeypatch, tmp_path):
    monkeypatch.delenv(g.STORE_ENV, raising=False)
    assert not g.Store().root.startswith(ROOT)
    monkeypatch.setenv(g.STORE_ENV, str(tmp_path))
    assert g.Store().root == str(tmp_path)
    assert g.Store("/x").root == "/x"


def test_the_sentinel_takes_the_steadiest_row_of_each_surface_and_medians_the_drift():
    base = _runs("dev")
    keys = g.sentinel_rows(base, list(base[0]))
    assert len(keys) == 3 and len(set(k[0] for k in keys)) == 3
    rows, med = g.drift(base, base[0], keys)
    assert abs(med) <= 0.2
    slow = dict((k, {"gate": {"p90": g.value(r) + 3.0}}) for k, r in base[0].items())
    rows, med = g.drift(base, slow, keys)
    assert med == pytest.approx(3.0, abs=0.2) and len(rows) == 3


# -- the selection -----------------------------------------------------------------

def test_rows_name_exact_surfaces_and_plan_what_a_board_plays():
    rows = ib.parse_rows("Code:fling,Text console:drag,Code:ball_fast")
    assert rows == {"Code": ["fling", "ball_fast"], "Text console": ["drag"]}
    assert ib.planned("p4", True, rows=rows) == [("Code", "fling"), ("Text console", "drag")]
    assert ib.planned("tdeck", False, rows=rows) == [
        ("Code", "fling"), ("Code", "ball_fast"), ("Text console", "drag")]
    with pytest.raises(SystemExit):
        ib.parse_rows("Code")
    # --only is a substring: "code" is every Code surface, a full screen only windowed
    only = ["code"]
    assert {s for s, _ in ib.planned("p4", True, only)} >= {"Code", "Code find",
                                                            "Code full screen"}
    assert "Code full screen" not in {s for s, _ in ib.planned("tdeck", False, only)}
    sel = g.Sel(rows={"Code": ["fling"]})
    assert sel.args() == ["--rows", "Code:fling"]
    assert g.Sel.of_rows([("Code", "fling"), ("Code", "type")]).args() == [
        "--rows", "Code:fling,Code:type"]


def test_a_round_dir_finds_its_boards_and_labels(tmp_path):
    assert g.found_boards(FIX) == ["tdeck"]
    found = g.discover(FIX, "tdeck")
    assert sorted(found) == ["dev", "ui1"]
    label, dirs = g._pick(found, "dev:2", "base")
    assert label == "dev" and [os.path.basename(d) for d in dirs] == ["dev_tdeck_2"]
    with pytest.raises(SystemExit):
        g._pick(found, "nope", "base")
    assert g.next_n(FIX, "dev", "tdeck") == 3
    assert g.next_n(str(tmp_path / "none"), "dev", "tdeck") == 1


def test_compare_prints_the_table_and_writes_the_verdicts(tmp_path, capsys):
    out = tmp_path / "v.json"
    rc = g.main(["compare", FIX, "--json", str(out)])
    assert rc == 1
    text = capsys.readouterr().out
    assert "Text console       fling" in text and "gate: a surface moves" in text
    data = json.loads(out.read_text())
    assert {r["verdict"] for r in data["tdeck"]} == {"pass", "fail", "near"}


# -- one board's gate, the flash and the bench faked ---------------------------------

class _Img:
    def __init__(self, label, wt, board, need=True):
        self.label, self.wt, self.board = label, wt, board
        self.commit = "c0ffee" * 7 if label == "dev" else "beef" * 10
        self.key = self.commit[:12]
        self.sha256 = "0" * 64
        self.warnings = []

    def meta(self):
        return {"label": self.label, "commit": self.commit, "key": self.key}


@pytest.fixture
def fake(monkeypatch):
    calls = []
    state = {"slow": 0.0, "n": {}}

    def flash(board, wt, log):
        calls.append(os.path.basename(os.path.dirname(log)))
        return 0

    def bench(board, out, sel, root, log):
        name = os.path.basename(out)
        label = "ui1" if name.startswith("ui1") else "dev"
        i = state["n"].get(label, 0)
        state["n"][label] = i + 1
        src = g.load_run(os.path.join(FIX, "%s_tdeck_%d" % (label, i % 2 + 1)), board)
        want = set(sel.planned(board, False))
        rows = []
        for k, r in src.items():
            if k in want:
                r = dict(r)
                if label == "dev" and state["slow"]:
                    r["gate"] = dict(r["gate"], p90=round(r["gate"]["p90"] + state["slow"], 1))
                rows.append(r)
        with open(os.path.join(out, "%s_input.json" % board), "w") as f:
            json.dump(rows, f)
        return 0

    monkeypatch.setattr(g, "Image", _Img)
    monkeypatch.setattr(g, "flash", flash)
    monkeypatch.setattr(g, "bench", bench)
    monkeypatch.setattr(g, "say", lambda b, m: None)
    return types.SimpleNamespace(calls=calls, state=state)


def _ctx(tmp_path, out, **kw):
    a = dict(store=str(tmp_path / "store"), only="Code,Theme Config text,Text console",
             inputs="", rows="", bench=ROOT, dev="/wt/dev", cand="/wt/ui1", cand_label=None,
             out=str(tmp_path / out), runs=2, fresh_baseline=False, no_third=False,
             drift=g.DRIFT_MS, sentinel_rows="", tol=0.0, near=g.NEAR_MS, noise=g.NOISE_MS)
    a.update(kw)
    return g.Ctx(types.SimpleNamespace(**a))


def test_a_first_gate_alternates_dev_and_the_candidate_then_reruns_the_near_rows(
        tmp_path, fake):
    res = g.gate_board(_ctx(tmp_path, "r1"), "tdeck")
    assert fake.calls == ["dev_tdeck_1", "ui1_tdeck_1", "dev_tdeck_2", "ui1_tdeck_2",
                          "dev_tdeck_3", "ui1_tdeck_3"]
    # the third run plays the near rows alone, on both images
    meta = g.read_meta(str(tmp_path / "r1" / "ui1_tdeck_3"))
    assert meta["requested"] == [["Code", "ball_fast"]]
    rows = dict(((r["surface"], r["input"]), r) for r in res["rows"])
    assert len(rows[("Code", "ball_fast")]["cand_runs"]) == 3
    assert len(rows[("Code", "fling")]["cand_runs"]) == 2
    assert rows[("Code", "ball_fast")]["noisy"] == []
    # dev's runs are the day's baseline, the third included
    st = g.Store(str(tmp_path / "store"))
    assert len(st.runs("c0ffeec0ffee", "tdeck", g.today())) == 3


def test_a_later_gate_the_same_day_reuses_the_baseline_after_a_sentinel(tmp_path, fake):
    g.gate_board(_ctx(tmp_path, "r1", no_third=True), "tdeck")
    del fake.calls[:]
    res = g.gate_board(_ctx(tmp_path, "r2", no_third=True), "tdeck")
    assert fake.calls == ["sentinel_tdeck_1", "ui1_tdeck_1", "ui1_tdeck_2"]
    assert len(g.read_meta(str(tmp_path / "r2" / "sentinel_tdeck_1"))["requested"]) == 3
    assert any("sentinel: holds" in h for h in res["head"])
    assert len(res["rows"][0]["base_runs"]) == 2


def test_a_drifted_sentinel_stops_the_gate_and_asks_for_a_fresh_baseline(tmp_path, fake):
    g.gate_board(_ctx(tmp_path, "r1", no_third=True), "tdeck")
    fake.state["slow"] = 3.0
    del fake.calls[:]
    with pytest.raises(g.StepFailed, match="--fresh-baseline"):
        g.gate_board(_ctx(tmp_path, "r2", no_third=True), "tdeck")
    assert fake.calls == ["sentinel_tdeck_1"]
    # a fresh baseline retires the stored runs and benches dev again, alternating
    del fake.calls[:]
    g.gate_board(_ctx(tmp_path, "r3", no_third=True, fresh_baseline=True), "tdeck")
    assert fake.calls == ["dev_tdeck_1", "ui1_tdeck_1", "dev_tdeck_2", "ui1_tdeck_2"]


def test_a_gate_asking_for_rows_the_baseline_lacks_benches_dev_for_those_only(tmp_path, fake):
    g.gate_board(_ctx(tmp_path, "r1", no_third=True, only="Theme Config text"), "tdeck")
    del fake.calls[:]
    g.gate_board(_ctx(tmp_path, "r2", no_third=True, only="Theme Config text,Code"), "tdeck")
    assert fake.calls == ["sentinel_tdeck_1", "dev_tdeck_1", "ui1_tdeck_1", "dev_tdeck_2",
                          "ui1_tdeck_2"]
    meta = g.read_meta(str(tmp_path / "r2" / "dev_tdeck_1"))
    assert {s for s, _ in map(tuple, meta["requested"])} == {"Code", "Code find",
                                                              "Code long line"}


def test_iter_is_one_run_of_the_rows_against_the_stored_baseline(tmp_path, fake, capsys):
    g.gate_board(_ctx(tmp_path, "r1", no_third=True), "tdeck")
    del fake.calls[:]
    rc = g.main(["iter", "--board", "tdeck", "--cand", "/wt/ui1", "--rows",
                 "Code:fling,Theme Config text:drag", "--store", str(tmp_path / "store"),
                 "--out", str(tmp_path / "it")])
    assert fake.calls == ["ui1_tdeck_1"]
    text = capsys.readouterr().out
    assert "Code               fling" in text and "key_held" not in text
    assert rc == 0
    shutil.rmtree(str(tmp_path / "it"))

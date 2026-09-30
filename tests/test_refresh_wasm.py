"""tools/refresh_wasm.py: which cart folders a refresh visits, and that it
keeps going past one push_cart refuses (docs/wasm_tier_plan_2026-09.md, "A
cart survives its firmware")."""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import refresh_wasm  # noqa: E402


def _cart(root, name, runtime="wasm"):
    d = os.path.join(root, name)
    os.makedirs(d)
    man = {"title": name, "runtime": runtime}
    with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(man, f)
    return d


def test_only_compiled_carts_are_found(tmp_path):
    wasm1 = _cart(tmp_path, "a.moy")
    _cart(tmp_path, "b.moy", runtime="lua")
    (tmp_path / "not_a_cart.txt").write_text("x")
    wasm2 = _cart(tmp_path, "sub" + os.sep + "c.moy")
    found = refresh_wasm.find_compiled_carts([str(tmp_path)])
    assert found == sorted([wasm1, wasm2])


def test_a_folder_that_is_not_a_cart_at_all_is_skipped(tmp_path):
    os.makedirs(tmp_path / "empty.moy")
    assert refresh_wasm.find_compiled_carts([str(tmp_path)]) == []


def test_a_missing_path_is_skipped_not_an_error(tmp_path):
    assert refresh_wasm.find_compiled_carts([str(tmp_path / "nope")]) == []


def test_refresh_pushes_every_cart_and_counts_successes(tmp_path, monkeypatch, capsys):
    a = _cart(tmp_path, "a.moy")
    b = _cart(tmp_path, "b.moy")
    calls = []

    def fake_main(argv):
        calls.append(argv)
        return 0 if argv[0] == a else 1
    import push_cart
    monkeypatch.setattr(push_cart, "main", fake_main)
    done = refresh_wasm.refresh([str(tmp_path)], "tdeck")
    assert done == 1
    assert [c[0] for c in calls] == [a, b]
    assert all(c[1:3] == ["--board", "tdeck"] for c in calls)
    assert "refreshed 1 of 2" in capsys.readouterr().out


def test_a_refused_cart_does_not_stop_the_run(tmp_path, monkeypatch, capsys):
    a = _cart(tmp_path, "a.moy")
    b = _cart(tmp_path, "b.moy")

    def fake_main(argv):
        if argv[0] == a:
            raise SystemExit("no signing key")
        return 0
    import push_cart
    monkeypatch.setattr(push_cart, "main", fake_main)
    done = refresh_wasm.refresh([str(tmp_path)], "tdeck")
    assert done == 1              # b still got pushed
    out = capsys.readouterr().out
    assert "refused: no signing key" in out

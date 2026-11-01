"""runtime/ is one flat module namespace kept in folders.

The stager (`tools/board_config.py`) collects every module under runtime/,
subfolders included, and stages it FLAT into a board's `modules/`, so `import
console` names one module wherever its file sits; a `host/` folder anywhere
under runtime/ never stages. On the host, `runtime/__init__.py` spans the
package path over every subfolder, so `runtime.<name>` resolves the same way.
A basename may exist once in the whole tree, device/ included, or two files
would race for one name.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools import board_config

ROOT = Path(__file__).resolve().parent.parent

DENY_BOARD = """
[board]
ota = "probe"
[modules]
dest = "modules"
[modules.shared]
source = "runtime"
strategy = "denylist"
[[modules.shared.deny]]
file = "denied.py"
kind = "host-only"
why = "the probe's denial"
[modules.device]
source = "device"
[[modules.device.group]]
files = ["dev_only.py"]
"""


def _tree(tmp_path, runtime, device=("dev_only.py",), board=DENY_BOARD):
    for rel in runtime:
        p = tmp_path / "runtime" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("X = %r\n" % rel, encoding="utf-8")
    (tmp_path / "device").mkdir()
    for name in device:
        (tmp_path / "device" / name).write_text("Y = 1\n", encoding="utf-8")
    bd = tmp_path / "firmware" / "probe"
    bd.mkdir(parents=True)
    (bd / "board.toml").write_text(board, encoding="utf-8")
    return bd


def test_subfolder_modules_stage_flat_and_host_never_stages(tmp_path):
    bd = _tree(tmp_path, ["__init__.py", "top.py", "denied.py",
                          "shell/console_probe.py", "shell/deep/leaf.py",
                          "host/host_probe.py", "shell/host/nested_host.py"])
    staged = board_config.staged_modules(bd, root=tmp_path)
    assert sorted(staged) == ["console_probe.py", "dev_only.py", "leaf.py",
                              "top.py"]
    assert staged["leaf.py"] == tmp_path / "runtime" / "shell" / "deep" / "leaf.py"


def test_a_basename_twice_under_runtime_is_refused(tmp_path):
    bd = _tree(tmp_path, ["top.py", "shell/top.py"])
    with pytest.raises(SystemExit, match="top.py"):
        board_config.staged_modules(bd, root=tmp_path)


def test_a_host_module_may_not_share_a_name_with_a_staged_one(tmp_path):
    # The host imports it through the same flat namespace.
    bd = _tree(tmp_path, ["top.py", "host/top.py"])
    with pytest.raises(SystemExit, match="top.py"):
        board_config.staged_modules(bd, root=tmp_path)


def test_a_runtime_module_may_not_share_a_name_with_a_device_one(tmp_path):
    bd = _tree(tmp_path, ["shell/dev_only.py"])
    with pytest.raises(SystemExit, match="dev_only.py"):
        board_config.staged_modules(bd, root=tmp_path)


def test_an_allowlist_finds_a_file_in_a_subfolder_and_refuses_host(tmp_path):
    board = DENY_BOARD.replace('strategy = "denylist"', 'strategy = "allowlist"')
    board = board.replace(
        '[[modules.shared.deny]]\nfile = "denied.py"\nkind = "host-only"\n'
        'why = "the probe\'s denial"\n',
        '[[modules.shared.group]]\nfiles = ["leaf.py"]\nwhy = "probe"\n')
    bd = _tree(tmp_path, ["shell/leaf.py", "host/host_probe.py"], board=board)
    staged = board_config.staged_modules(bd, root=tmp_path)
    assert staged["leaf.py"] == tmp_path / "runtime" / "shell" / "leaf.py"
    (bd / "board.toml").write_text(board.replace('"leaf.py"', '"host_probe.py"'),
                                   encoding="utf-8")
    with pytest.raises(SystemExit, match="host_probe.py"):
        board_config.staged_modules(bd, root=tmp_path)


def test_the_real_tree_has_unique_basenames():
    names = {}
    for p in (ROOT / "runtime").rglob("*.py"):
        if "__pycache__" not in p.parts and p.name != "__init__.py":
            names.setdefault(p.name, []).append(p)
    for p in (ROOT / "device").glob("*.py"):
        names.setdefault(p.name, []).append(p)
    assert {n: v for n, v in names.items() if len(v) > 1} == {}


def test_the_package_path_spans_every_runtime_subfolder():
    r = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, 'tests'); import import_guard; "
         "sys.path.pop(0); import_guard.drop_foreign_editable_finders('.'); "
         "import runtime; print('\\n'.join(runtime.__path__))"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    got = {os.path.realpath(p) for p in r.stdout.split("\n") if p}
    want = {os.path.realpath(str(ROOT / "runtime"))} | {
        os.path.realpath(base) for base, dirs, _ in os.walk(ROOT / "runtime")
        if "__pycache__" not in base}
    assert got == want


def test_no_board_stages_a_module_from_runtime_host():
    host = {p.name for p in (ROOT / "runtime" / "host").rglob("*.py")}
    assert host, "runtime/host/ holds the host-only modules"
    for toml in sorted((ROOT / "firmware").glob("*/board.toml")):
        staged = board_config.staged_modules(toml.parent, root=ROOT)
        assert not host & set(staged), (toml.parent.name, host & set(staged))
        assert not host & set(board_config.denials(toml.parent)), (
            "%s/board.toml denies a module runtime/host/ already keeps off "
            "every board" % toml.parent.name)

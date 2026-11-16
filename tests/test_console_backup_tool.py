"""tools/console_backup.py mirrors a console's folder over `py`: every
expression and snippet it sends runs on a board's MicroPython, so this runs
them on the unix MicroPython, one long-lived VM standing in for the console,
whose store is a copy of the trial's (tests/fixtures/store_trial/)."""

import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import console_backup                                            # noqa: E402
from unix_mp import require_unix_mp                              # noqa: E402

from runtime import moy_carts, moy_seed                          # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "store_trial")

CONSOLE = """
import sys
class Workstation:
    pass
ws = Workstation()
ws.carts_root = sys.argv[1]
ws._g = {'ws': ws}
while True:
    line = sys.stdin.readline()
    if not line:
        break
    try:
        if line.startswith('EXEC '):
            exec(eval(line[5:]), ws._g)
            print('PY 1')
        else:
            print('PY ' + repr(eval(line)))
    except Exception as exc:
        print('PY ERR ' + repr(exc))
"""


class VmBoard:
    """`pyval` and `pyexec` as P4Board answers them, run on a MicroPython
    VM whose `ws.carts_root` is `root`."""

    def __init__(self, exe, script, root):
        self.vm = subprocess.Popen([str(exe), str(script), root], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, text=True)
        self.reads = 0

    def _ask(self, line):
        self.vm.stdin.write(line + "\n")
        self.vm.stdin.flush()
        return self.vm.stdout.readline()

    def pyval(self, expr, timeout=30.0, strict=False):
        if "_bk_read" in expr:
            self.reads += 1
        line = self._ask(expr)
        if "PY ERR" in line:
            if strict:
                raise RuntimeError(line)
            return None
        return eval(line.split("PY ", 1)[1])                    # noqa: S307

    def pyexec(self, code, timeout=30.0, strict=False):
        return "PY ERR" not in self._ask("EXEC " + repr(code))

    def close(self):
        self.vm.stdin.close()
        self.vm.wait(10)


@pytest.fixture
def console(tmp_path):
    """A console folder: the trial's store, with what a pull leaves home
    (thumbnails, an install stage, a stale backup) and a save in flight
    (the publish marker naming a file whose backup is whole)."""
    exe = require_unix_mp("moy_store", why="the expressions the backup tool sends a console")
    base = str(tmp_path / "console" / "moybyte")
    shutil.copytree(FIXTURE, base)
    os.makedirs(base + "/carts/local.my_game.moy/thumbs")
    with open(base + "/carts/local.my_game.moy/thumbs/t.mct", "wb") as f:
        f.write(b"thumb")
    os.makedirs(base + "/install/local.half.moy")
    with open(base + "/install/local.half.moy/main.py", "w") as f:
        f.write("half")
    moy_carts.save_system({"theme": "night", "app_guard": {"broken": []}}, base + "/carts")
    big = bytes((k * 131 + k // 7) & 255 for k in range(console_backup.PIECE * 3 + 5))
    with open(base + "/files/big.bin", "wb") as f:
        f.write(big)
    with open(base + "/.publish", "w") as f:
        f.write("#moyfs1 5 1 %s/system/settings\n" % base)
    with open(base + "/system/settings.bak", "w") as f:
        f.write("#moyfs1 5 1\nwhole")
    script = tmp_path / "console.py"
    script.write_text(CONSOLE)
    b = VmBoard(exe, script, base + "/carts")
    yield base, b
    b.close()


def _files(base):
    out = {}
    for d, _dirs, files in os.walk(base):
        for n in files:
            p = os.path.join(d, n)
            with open(p, "rb") as f:
                out[os.path.relpath(p, base)] = f.read()
    return out


def _quiet(_m):
    pass


def test_read_back_reads_a_file_whole(console, tmp_path):
    base, b = console
    out = str(tmp_path / "big.bin")
    console_backup.install(b)
    got = console_backup.read_back(b, base + "/files/big.bin", out, log=_quiet)
    with open(base + "/files/big.bin", "rb") as f:
        assert got == len(f.read())
    assert _files(str(tmp_path))["big.bin"] == _files(base)["files/big.bin"]


def test_a_pull_mirrors_the_folder_and_a_second_copies_nothing(console, tmp_path):
    base, b = console
    out = str(tmp_path / "mirror")
    n, gone = console_backup.mirror(b, out, log=_quiet)
    want = {k: v for k, v in _files(base).items()
            if "/thumbs/" not in k and not k.startswith("install/")
            and (not k.endswith(".bak") or k == "system/settings.bak")}
    assert _files(out) == want and n == len(want) and gone == 0
    assert "system/settings" in want and ".publish" in want and "store.ver" in want

    reads = b.reads
    assert console_backup.mirror(b, out, log=_quiet) == (0, 0)
    assert b.reads == reads, "a second pull reads no file back"

    with open(base + "/carts/local.my_game.moy/main.py", "rb") as f:
        src = f.read()
    with open(base + "/carts/local.my_game.moy/main.py", "wb") as f:
        f.write(src[:-1] + (b"#" if src[-1:] != b"#" else b"!"))     # same size
    os.remove(base + "/files/big.bin")
    assert console_backup.mirror(b, out, log=_quiet) == (1, 1)
    got = _files(out)
    assert "files/big.bin" not in got
    assert got["carts/local.my_game.moy/main.py"] == _files(base)["carts/local.my_game.moy/main.py"]


def test_a_pull_will_not_mirror_over_a_folder_it_did_not_make(console, tmp_path):
    _base, b = console
    out = tmp_path / "home"
    out.mkdir()
    (out / "notes.txt").write_text("mine")
    with pytest.raises(SystemExit):
        console_backup.mirror(b, str(out), log=_quiet)
    assert (out / "notes.txt").read_text() == "mine"


def test_a_pushed_older_folder_boots_through_the_door(console, tmp_path, monkeypatch):
    """A folder pulled from another board, in an older format, pushed onto
    a console and booted: the door's export, then its step, and the rows
    bound to the board it came from dropped."""
    base, b = console
    tree = str(tmp_path / "from_another_board")
    shutil.copytree(FIXTURE, tree)
    moy_carts.save_system({"theme": "day", "wallpaper_guard": {"open": "w"}}, tree + "/carts")
    with open(tree + "/system/ssh_host_key", "w") as f:
        f.write("theirs")
    with open(moy_seed.board_stamp_path(tree + "/carts"), "w") as f:
        f.write("moyboard 0f1e2d3c4b5a\n")
    shutil.rmtree(base + "/carts/local.my_game.moy")

    def put(board, src, dst, link, verbose=False):
        shutil.copyfile(src, dst + ".new")
        board.pyval("ws._g['_put'](%r, %r)" % (dst + ".new", dst), strict=True)
        return True
    monkeypatch.setattr(console_backup.push_cart, "push_file_raw", put)
    sent = console_backup.put_tree(b, tree, None, log=_quiet)
    assert sent > 0
    assert console_backup.put_tree(b, tree, None, log=_quiet) == 0, "nothing differs now"

    root = base + "/carts"
    seen = []

    def step(r):
        seen.append(sorted(os.listdir(r + "/../files/Backups")))
    monkeypatch.setattr(moy_seed, "STORE_FORMAT", 2)
    monkeypatch.setitem(moy_seed.FORMAT_STEPS, 1, step)
    moy_carts.ensure_dirs(root)
    moy_seed.sweep_store(root, [], board="a0b1c2d3e4f5")
    assert seen == [["moybyte-format-1.zip"]]
    assert moy_seed.load_store_version(root)[0] == 2
    assert moy_carts.load_system(root) == {"theme": "day"}
    assert not os.path.exists(base + "/system/ssh_host_key")
    assert os.path.isfile(root + "/local.my_game.moy/main.py")
    with open(root + "/local.my_game.moy/journal/journal.jsonl") as f:
        assert f.read() == open(FIXTURE + "/carts/local.my_game.moy/journal/journal.jsonl").read()

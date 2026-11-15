"""tools/console_backup.py's pull reads an archive back over `py`: every
expression it sends runs on a board's MicroPython, so this runs them on the
unix MicroPython, one long-lived VM standing in for the console."""

import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import console_backup                                            # noqa: E402
from unix_mp import require_unix_mp                              # noqa: E402

CONSOLE = """
import sys
class Workstation:
    pass
ws = Workstation()
while True:
    line = sys.stdin.readline()
    if not line:
        break
    try:
        print("PY " + repr(eval(line)))
    except Exception as exc:
        print("PY ERR " + repr(exc))
"""


class VmBoard:
    """`pyval` as P4Board answers it, evaluated on a MicroPython VM."""

    def __init__(self, exe, script):
        self.vm = subprocess.Popen([str(exe), str(script)], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, text=True)

    def pyval(self, expr, timeout=30.0, strict=False):
        self.vm.stdin.write(expr + "\n")
        self.vm.stdin.flush()
        line = self.vm.stdout.readline()
        if "PY ERR" in line:
            if strict:
                raise RuntimeError(line)
            return None
        return eval(line.split("PY ", 1)[1])                    # noqa: S307

    def close(self):
        self.vm.stdin.close()
        self.vm.wait(10)


def test_pull_reads_an_archive_back_on_the_vm_a_board_runs(tmp_path):
    exe = require_unix_mp(why="the expressions the pull sends a console")
    script = tmp_path / "console.py"
    script.write_text(CONSOLE)
    data = bytes((k * 131 + k // 7) & 255 for k in range(console_backup.PIECE * 70 + 5))
    src = tmp_path / "moybyte-backup.zip"
    src.write_bytes(data)
    out = tmp_path / "pulled.zip"
    b = VmBoard(exe, script)
    try:
        got = console_backup.read_back(b, str(src), str(out), log=lambda _m: None)
    finally:
        b.close()
    assert got == len(data)
    assert out.read_bytes() == data


class PushBoard:
    """A console for push: the upload helpers `push_file_raw` calls exist only
    once the tool has installed them, as on a board."""

    def __init__(self):
        self.helpers = False
        self.words = []

    def pyval(self, expr, timeout=30.0, strict=False):
        if "backups_dir" in expr:
            return "/sd/moybyte/files/Backups"
        return 1

    def pyexec(self, code, timeout=30.0, strict=False):
        self.helpers = code == console_backup.push_cart.HELPERS
        return True

    def cmd(self, word, wait_for=None, timeout=None, retry=True):
        self.words.append(word)
        return "REMOTE restore 600"

    def close(self):
        pass


def test_push_installs_the_upload_helpers_and_restores_an_archive_already_there(
        tmp_path, monkeypatch):
    pc = console_backup.push_cart
    b = PushBoard()
    sent = []

    def push_file_raw(board, src, dst, link, verbose=False):
        assert board.helpers, "the raw push renames through ws._g's helpers"
        sent.append(dst)
        return False                     # already current on the console

    monkeypatch.setattr(pc, "connect", lambda *a: b)
    monkeypatch.setattr(pc, "raw_link", lambda *a: None)
    monkeypatch.setattr(pc, "serial_cfg", lambda *a: None)
    monkeypatch.setattr(pc, "push_file_raw", push_file_raw)
    archive = tmp_path / "moybyte-backup.zip"
    archive.write_bytes(b"PK")
    console_backup.main(["push", str(archive), "--board", "p4"])
    assert sent == ["/sd/moybyte/files/Backups/moybyte-backup.zip"]
    assert b.words == ["restore /sd/moybyte/files/Backups/moybyte-backup.zip"]

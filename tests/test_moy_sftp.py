"""SFTP into the console's files tree (native/moy_tty/moy_tty_sftp.c) on the
host: the SSH server's harness (native/moy_ssh/host/ssh_main.c, the board's
server, face and shell over a folder) against OpenSSH's own `sftp -b` and
`scp -s` (the SFTP protocol, scp's default since OpenSSH 9.0).

What it holds (docs/os_2026-11/system.md section 4): an upload and a download
of a megabyte arrive byte-equal; a session starts in the home, the user's
files folder; `ls`, mkdir, rmdir, rename and remove change the store as the
shell would; /etc is the store's system folder; the root lists the shell's
root; a change in a cart folder tells the shelf to re-read that folder once
the client goes quiet; and the SFTP server is the session's one face, so a
shell running in the same connection refuses it until the shell ends.
"""

import os
import shutil
import subprocess
import time

import pytest

import test_moy_ssh as SSH

pytestmark = pytest.mark.skipif(
    shutil.which("cc") is None or shutil.which("sftp") is None
    or shutil.which("ssh-keygen") is None,
    reason="needs a C compiler and OpenSSH's client")

STORE = "sd/moybyte"


@pytest.fixture(scope="module")
def h(tmp_path_factory):
    out = tmp_path_factory.mktemp("moy_sftp")
    exe = SSH._build(out)
    h = SSH.serve(exe, out / "main")
    pub = (h["key"].parent / "id_ed25519.pub").read_text()
    (h["system"] / "authorized_keys").write_text(pub)
    h["tree"] = out / "main" / "tree"
    h["files"] = h["tree"] / STORE / "files"
    yield h
    SSH.end(h)


def _auth(h):
    return ["-i", str(h["key"]), "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR", "-F", "/dev/null", "-P", str(h["port"])]


def sftp(h, *lines, timeout=60, extra=()):
    r = subprocess.run(["sftp"] + _auth(h) + list(extra) + ["-b", "-", "moy@127.0.0.1"],
                       input="\n".join(lines).encode() + b"\n", capture_output=True,
                       timeout=timeout, env=SSH.env_of(h, ""))
    return r


def scp(h, *args, timeout=60):
    return subprocess.run(["scp", "-s", "-q"] + _auth(h) + list(args), capture_output=True,
                          timeout=timeout, env=SSH.env_of(h, ""), stdin=subprocess.DEVNULL)


def rescans(h):
    h["log"].seek(0)
    return [l[len("RESCAN "):] for l in h["log"].read().splitlines()
            if l.startswith("RESCAN")]


def test_a_megabyte_goes_up_and_comes_back_byte_equal(h, tmp_path):
    data = os.urandom(1 << 20)
    src = tmp_path / "big.bin"
    src.write_bytes(data)
    t0 = time.time()
    r = sftp(h, "put %s" % src)
    up = time.time() - t0
    assert r.returncode == 0, r.stderr
    assert (h["files"] / "big.bin").read_bytes() == data
    back = tmp_path / "back.bin"
    t0 = time.time()
    r = sftp(h, "get big.bin %s" % back)
    down = time.time() - t0
    assert r.returncode == 0, r.stderr
    assert back.read_bytes() == data
    print("SFTP 1 MB over loopback: up %.2f MB/s, down %.2f MB/s (whole sessions)"
          % (1 / up, 1 / down))
    (h["files"] / "big.bin").unlink()


def test_scp_copies_into_the_home_and_back(h, tmp_path):
    data = os.urandom(200000)
    src = tmp_path / "s.bin"
    src.write_bytes(data)
    r = scp(h, str(src), "moy@127.0.0.1:")
    assert r.returncode == 0, r.stderr
    assert (h["files"] / "s.bin").read_bytes() == data
    r = scp(h, "moy@127.0.0.1:s.bin", str(tmp_path / "t.bin"))
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "t.bin").read_bytes() == data
    (h["files"] / "s.bin").unlink()


def test_a_session_starts_in_the_home(h):
    r = sftp(h, "pwd")
    assert r.returncode == 0, r.stderr
    assert b"Remote working directory: /" + STORE.encode() + b"/files" in r.stdout


def test_ls_mkdir_rename_remove_and_rmdir_change_the_store(h, tmp_path):
    (tmp_path / "a.txt").write_text("alpha\n")
    r = sftp(h, "mkdir d", "put %s d/a.txt" % (tmp_path / "a.txt"), "ls -l d",
             "rename d/a.txt d/b.txt", "ls d")
    assert r.returncode == 0, r.stderr
    out = r.stdout.decode()
    assert "-rw-r--r--" in out and " 6 a.txt" in out, out
    assert (h["files"] / "d" / "b.txt").read_text() == "alpha\n"
    assert not (h["files"] / "d" / "a.txt").exists()
    # a plain rename does not replace; posix-rename (OpenSSH's `rename`) does
    (h["files"] / "d" / "c.txt").write_text("gamma\n")
    r = sftp(h, "rename d/b.txt d/c.txt")
    assert r.returncode == 0, r.stderr
    assert (h["files"] / "d" / "c.txt").read_text() == "alpha\n"
    r = sftp(h, "rmdir d")
    assert r.returncode != 0
    r = sftp(h, "rm d/c.txt", "rmdir d")
    assert r.returncode == 0, r.stderr
    assert not (h["files"] / "d").exists()
    r = sftp(h, "rm nothing-here")
    assert r.returncode != 0 and b"No such file" in r.stderr


def test_etc_is_the_system_folder_and_the_root_is_the_shell_s(h, tmp_path):
    (tmp_path / "note").write_text("hello\n")
    r = sftp(h, "put %s /etc/note" % (tmp_path / "note"), "ls /", "ls /etc", "cd /dev", "ls")
    assert r.returncode == 0, r.stderr
    assert (h["system"] / "note").read_text() == "hello\n"
    out = r.stdout.decode()
    for name in ("/bin", "/dev", "/etc", "/proc", "/sd"):
        assert name in out, out
    assert "/etc/note" in out and "/etc/password" in out, out
    assert "null" in out, out
    (h["system"] / "note").unlink()
    r = sftp(h, "put %s /proc/x" % (tmp_path / "note"))
    assert r.returncode != 0


def test_a_cart_folder_s_changes_tell_the_shelf_once_the_client_is_quiet(h, tmp_path):
    cart = tmp_path / "hello.moy"
    cart.mkdir()
    (cart / "main.py").write_text("print('hi')\n")
    (cart / "manifest.json").write_text("{}\n")
    seen = len(rescans(h))
    r = sftp(h, "put -r %s /%s/carts/" % (cart, STORE))
    assert r.returncode == 0, r.stderr
    assert (h["tree"] / STORE / "carts" / "hello.moy" / "main.py").exists()
    deadline = time.time() + 5
    while len(rescans(h)) == seen and time.time() < deadline:
        time.sleep(0.05)
    assert rescans(h)[seen:] == ["hello.moy"]
    seen = len(rescans(h))
    r = sftp(h, "rename /%s/carts/hello.moy /%s/carts/bye.moy" % (STORE, STORE))
    assert r.returncode == 0, r.stderr
    deadline = time.time() + 5
    while len(rescans(h)) == seen and time.time() < deadline:
        time.sleep(0.05)
    assert rescans(h)[seen:] == ["hello.moy bye.moy"]
    shutil.rmtree(h["tree"] / STORE / "carts" / "bye.moy")
    # a file outside the carts tells the shelf nothing
    seen = len(rescans(h))
    r = sftp(h, "put %s /%s/files/x.txt" % (cart / "main.py", STORE))
    assert r.returncode == 0
    time.sleep(0.5)
    assert len(rescans(h)) == seen
    (h["files"] / "x.txt").unlink()


def test_sftp_is_the_one_face_a_shell_in_the_same_connection_holds(h, tmp_path):
    """One face at a time (native/moy_tty/README.md): a shell running on a
    multiplexed connection refuses SFTP's channel until the shell ends."""
    sock = str(tmp_path / "mux")
    mux = ["-o", "ControlPath=" + sock]
    master = subprocess.Popen(["ssh"] + _auth(h)[:-2] + ["-p", str(h["port"]), "-M", "-N"]
                              + mux + ["moy@127.0.0.1"], env=SSH.env_of(h, ""),
                              stdin=subprocess.DEVNULL)
    try:
        deadline = time.time() + 10
        while not os.path.exists(sock) and time.time() < deadline:
            time.sleep(0.05)
        shell = subprocess.Popen(["ssh"] + _auth(h)[:-2] + ["-p", str(h["port"])] + mux
                                 + ["moy@127.0.0.1", "sleep 2"], env=SSH.env_of(h, ""),
                                 stdin=subprocess.DEVNULL)
        time.sleep(0.8)
        r = sftp(h, "pwd", extra=mux)
        assert r.returncode != 0
        assert shell.wait(timeout=10) == 0
        r = sftp(h, "pwd", extra=mux)
        assert r.returncode == 0, r.stderr
    finally:
        master.kill()
        master.wait()

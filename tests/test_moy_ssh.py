"""The SSH server (native/moy_ssh) on the host: the board's server, face and
shell (moy_ssh_sh.c over native/moy_sh's board table, the VM stopped) built
with the host's compiler (host/ssh_main.c), and a real OpenSSH client and
ssh-copy-id against it.

What it holds (docs/os_2026-11.md phase 4, item 1): a session that signs in
by neither a password nor a key is refused, and so is every session while no
password is set; a password signs in and a wrong one is refused; a key that
ssh-copy-id installed signs in; `password_auth = no` in system/ssh refuses
the password and still takes the key. Past the sign-in: an `exec` runs on the
shell with the channel as its stdin and ends with its status; a terminal
session is the face's line discipline; `top` and `edit` run on the
alternate screen; and a session's every allocation goes when it ends.
"""

import os
import shutil
import socket
import subprocess
import time

import pytest

import test_moy_sh as SH

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOD = os.path.join(ROOT, "native", "moy_ssh")
DB = os.path.join(MOD, "dropbear")
PASSWORD = "moybyte-test"
SALT = "00112233445566778899aabbccddeeff"
SYSTEM = "sd/moybyte/system"

pytestmark = pytest.mark.skipif(
    shutil.which("cc") is None or shutil.which("ssh") is None
    or shutil.which("ssh-keygen") is None,
    reason="needs a C compiler and OpenSSH's client")


def _build(out):
    cc = shutil.which("cc")
    shell = [o for o in SH._objects(cc, str(out))
             if os.path.basename(o) not in ("sh_main.o", "board_main.o")]
    srcs = [os.path.join(d, f) for top in ("src", "libtommath", "libtomcrypt")
            for d, _dirs, files in os.walk(os.path.join(DB, top))
            for f in files if f.endswith(".c")]
    srcs += [os.path.join(MOD, f) for f in ("moy_ssh.c", "moy_ssh_face.c", "moy_ssh_sh.c",
                                             "host/ssh_main.c")]
    native = os.path.join(ROOT, "native")
    flags = ["-Os", "-ffunction-sections", "-fdata-sections", "-w", "-g",
             "-include", os.path.join(MOD, "port", "moy_ssh_names.h"),
             "-I" + os.path.join(MOD, "port"), "-I" + MOD,
             "-I" + os.path.join(DB, "src"),
             "-I" + os.path.join(DB, "libtomcrypt", "src", "headers"),
             "-I" + os.path.join(DB, "libtommath"),
             "-I" + os.path.join(native, "moy_sh"), "-I" + os.path.join(native, "moy_store"),
             "-I" + os.path.join(native, "moy_kernel"),
             "-DDROPBEAR_SERVER", "-DLOCALOPTIONS_H_EXISTS", "-DLTC_SOURCE",
             "-D_GNU_SOURCE", "-U_FORTIFY_SOURCE", "-D_FORTIFY_SOURCE=0"]
    objs, procs = [], []
    for i, src in enumerate(srcs):
        obj = str(out / ("ssh%d.o" % i))
        f = list(flags)
        if "/libtomcrypt/" not in src:
            f.remove("-DLTC_SOURCE")
        procs.append(subprocess.Popen([cc] + f + ["-c", src, "-o", obj]))
        objs.append(obj)
    assert all(p.wait() == 0 for p in procs)
    exe = str(out / "ssh_main")
    subprocess.run([cc] + SH.CFLAGS + ["-Wl,--gc-sections", "-o", exe] + objs + shell
                   + ["-lpthread"], check=True)
    return exe


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    out = tmp_path_factory.mktemp("moy_ssh")
    exe = _build(out)
    tree = out / "tree"
    system = tree / SYSTEM
    system.mkdir(parents=True)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    log = open(out / "server.log", "w+")
    env = dict(os.environ, MOY_SH_ROOT=str(tree), ASAN_OPTIONS="detect_leaks=0")
    server = subprocess.Popen([exe, str(port), str(system)], stdout=log,
                              stderr=subprocess.STDOUT, env=env)
    for _ in range(200):
        log.seek(0)
        if "listening" in log.read():
            break
        time.sleep(0.05)
    home = out / "home"
    (home / ".ssh").mkdir(parents=True)
    askpass = out / "askpass.sh"
    askpass.write_text("#!/bin/sh\necho \"$MOY_SSH_PW\"\n")
    askpass.chmod(0o700)
    key = home / ".ssh" / "id_ed25519"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    h = {"port": port, "askpass": askpass, "log": log, "system": system, "home": home,
         "key": key, "out": out}
    set_password(h, PASSWORD)
    yield h
    server.kill()
    server.wait()
    log.close()


def hashed(password):
    """moy_ssh_hash's `$moy1$SALT$HEX`, SHA-256 iterated (moy_ssh.h)."""
    import hashlib
    d = hashlib.sha256((SALT + password).encode()).digest()
    for _ in range(4095):
        d = hashlib.sha256(d + password.encode()).digest()
    return "$moy1$%s$%s" % (SALT, d.hex())


def set_password(h, password):
    p = h["system"] / "password"
    if password is None:
        if p.exists():
            p.unlink()
    else:
        p.write_text(hashed(password) + "\n")


def options(h):
    return ["-p", str(h["port"]), "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
            "-o", "NumberOfPasswordPrompts=1", "-F", "/dev/null"]


def env_of(h, password):
    return dict(os.environ, SSH_ASKPASS=str(h["askpass"]), SSH_ASKPASS_REQUIRE="force",
                DISPLAY="moy", MOY_SSH_PW=password or "", HOME=str(h["home"]))


def ssh(h, password, *args, key=False, methods=None, stdin=None, timeout=30):
    auth = ["-o", "PubkeyAuthentication=no", "-o", "PreferredAuthentications=password"]
    if key:
        auth = ["-i", str(h["key"]), "-o", "IdentitiesOnly=yes",
                "-o", "PreferredAuthentications=publickey"]
    if methods is not None:
        auth = ["-o", "PubkeyAuthentication=no", "-o", "PreferredAuthentications=" + methods]
    cmd = ["ssh"] + options(h) + auth + list(args)
    return subprocess.run(cmd, env=env_of(h, password), input=stdin, capture_output=True,
                          timeout=timeout,
                          stdin=None if stdin is not None else subprocess.DEVNULL)


def stats(h):
    h["log"].seek(0)
    lines = [l for l in h["log"].read().splitlines() if l.startswith("STATS ")]
    return dict(kv.split("=") for kv in lines[-1].split()[1:]) if lines else {}


def test_a_session_that_does_not_sign_in_is_refused(harness):
    r = ssh(harness, None, "moy@127.0.0.1", "ls", methods="none,keyboard-interactive")
    assert r.returncode == 255
    assert b"Permission denied" in r.stderr
    r = ssh(harness, "not-it", "moy@127.0.0.1", "ls")
    assert r.returncode == 255
    assert b"Permission denied" in r.stderr


def test_with_no_password_set_there_is_no_ssh(harness):
    set_password(harness, None)
    try:
        r = ssh(harness, PASSWORD, "moy@127.0.0.1", "echo in")
        assert r.returncode == 255
        assert b"in" not in r.stdout
    finally:
        set_password(harness, PASSWORD)


def test_a_password_signs_in_and_an_exec_runs_on_the_shell(harness):
    r = ssh(harness, PASSWORD, "moy@127.0.0.1", "ls /")
    assert r.returncode == 0, r.stderr
    assert b"etc/" in r.stdout.split()
    r = ssh(harness, PASSWORD, "moy@127.0.0.1", "exit 3")
    assert r.returncode == 3


def test_an_exec_reads_the_channel_as_its_stdin(harness):
    r = ssh(harness, PASSWORD, "moy@127.0.0.1", "cat > /etc/note && wc -l /etc/note",
            stdin=b"one\ntwo\n")
    assert r.returncode == 0, r.stderr
    assert (harness["system"] / "note").read_bytes() == b"one\ntwo\n"
    assert b"2" in r.stdout


def test_ssh_copy_id_installs_a_key_that_signs_in(harness):
    keys = harness["system"] / "authorized_keys"
    if keys.exists():
        keys.unlink()
    assert ssh(harness, "", "moy@127.0.0.1", "echo in", key=True).returncode == 255
    r = subprocess.run(["ssh-copy-id", "-i", str(harness["key"]) + ".pub"] + options(harness)
                       + ["moy@127.0.0.1"], env=env_of(harness, PASSWORD),
                       capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stdout + r.stderr
    pub = (harness["key"].parent / "id_ed25519.pub").read_text().strip()
    assert keys.read_text().strip() == pub
    r = ssh(harness, "", "moy@127.0.0.1", "echo in", key=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout == b"in\n"


def test_password_auth_no_refuses_the_password_and_keeps_the_key(harness):
    keys = harness["system"] / "authorized_keys"
    keys.write_text((harness["key"].parent / "id_ed25519.pub").read_text())
    conf = harness["system"] / "ssh"
    conf.write_text("# sshd_config-like\npassword_auth = no\n")
    try:
        r = ssh(harness, PASSWORD, "moy@127.0.0.1", "echo in")
        assert r.returncode == 255
        assert b"Permission denied" in r.stderr
        r = ssh(harness, "", "moy@127.0.0.1", "echo in", key=True)
        assert r.returncode == 0, r.stderr
    finally:
        conf.write_text("password_auth = yes\n")
    assert ssh(harness, PASSWORD, "moy@127.0.0.1", "echo in").returncode == 0


def terminal(h, chunks):
    """A `-tt` session fed `chunks`, each (bytes, seconds to wait after)."""
    r = subprocess.Popen(["ssh"] + options(h) + [
        "-o", "PubkeyAuthentication=no", "-o", "PreferredAuthentications=password",
        "-tt", "moy@127.0.0.1"], env=env_of(h, PASSWORD), stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    time.sleep(1.0)
    for chunk, wait in chunks:
        r.stdin.write(chunk)
        r.stdin.flush()
        time.sleep(wait)
    out, _err = r.communicate(timeout=20)
    return out


def test_a_terminal_session_is_the_face_s_line_discipline(harness):
    out = terminal(harness, ((b"echo hi\r", 0.4), (b"ech\x7fho abd\r", 0.4),
                             (b"sleep 5\r", 0.4), (b"\x03", 0.4), (b"exit\r", 0.3)))
    assert b"echo hi\r\nhi\r\n" in out, out
    assert b"ech\b \bho abd\r\nabd\r\n" in out, out
    assert b"^C\r\n" in out, out


def test_top_and_edit_run_on_the_alternate_screen(harness):
    (harness["system"] / "memo").write_text("hello\n")
    out = terminal(harness, ((b"top\r", 1.5), (b"q", 0.8), (b"edit /etc/memo\r", 1.0),
                             (b"X", 0.3), (b"\x13", 0.8), (b"\x18", 0.5), (b"exit\r", 0.5)))
    assert out.count(b"\x1b[?1049h") >= 2, out
    assert out.count(b"\x1b[?1049l") >= 2, out
    assert b"PID" in out or b"pid" in out, out
    assert (harness["system"] / "memo").read_text() == "Xhello\n"


def test_every_session_gives_back_what_it_took(harness):
    for i in range(3):
        assert ssh(harness, PASSWORD, "moy@127.0.0.1", "echo %d" % i).returncode == 0
    time.sleep(0.3)
    held = int(stats(harness)["heap_now"])
    for i in range(3):
        assert ssh(harness, PASSWORD, "moy@127.0.0.1", "ls / | wc -l").returncode == 0
    time.sleep(0.3)
    s = stats(harness)
    assert int(s["heap_now"]) == held
    assert int(s["session_peak"]) > 0

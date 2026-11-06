"""The SSH server (native/moy_ssh) on the host: the board's server and face
built with the host's compiler (host/ssh_main.c, a stand-in shell that answers
each line with what it ran), and a real OpenSSH client against it.

What it holds: a password signs in and a wrong one is refused; an `exec`
runs and the session ends with the client's exit status 0; a terminal
session is the face's line discipline (echo, erase, Ctrl-C, CR LF); and a
session's every allocation goes when it ends (the epoch), across several.
"""

import os
import shutil
import socket
import subprocess
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOD = os.path.join(ROOT, "native", "moy_ssh")
DB = os.path.join(MOD, "dropbear")
PASSWORD = "moybyte-test"

pytestmark = pytest.mark.skipif(
    shutil.which("cc") is None or shutil.which("ssh") is None,
    reason="needs a C compiler and an OpenSSH client")


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    out = tmp_path_factory.mktemp("moy_ssh")
    srcs = [os.path.join(d, f) for top in ("src", "libtommath", "libtomcrypt")
            for d, _dirs, files in os.walk(os.path.join(DB, top))
            for f in files if f.endswith(".c")]
    srcs += [os.path.join(MOD, f) for f in ("moy_ssh.c", "moy_ssh_face.c", "host/ssh_main.c")]
    flags = ["-Os", "-ffunction-sections", "-fdata-sections", "-w",
             "-include", os.path.join(MOD, "port", "moy_ssh_names.h"),
             "-I" + os.path.join(MOD, "port"), "-I" + MOD,
             "-I" + os.path.join(DB, "src"),
             "-I" + os.path.join(DB, "libtomcrypt", "src", "headers"),
             "-I" + os.path.join(DB, "libtommath"),
             "-DDROPBEAR_SERVER", "-DLOCALOPTIONS_H_EXISTS", "-DLTC_SOURCE",
             "-D_GNU_SOURCE", "-U_FORTIFY_SOURCE", "-D_FORTIFY_SOURCE=0"]
    objs = []
    procs = []
    for i, src in enumerate(srcs):
        obj = str(out / ("%d.o" % i))
        f = list(flags)
        if "/libtomcrypt/" not in src:
            f.remove("-DLTC_SOURCE")
        procs.append(subprocess.Popen(["cc"] + f + ["-c", src, "-o", obj]))
        objs.append(obj)
    assert all(p.wait() == 0 for p in procs)
    exe = str(out / "ssh_main")
    subprocess.run(["cc", "-Wl,--gc-sections", "-o", exe] + objs + ["-lpthread"], check=True)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    log = open(out / "server.log", "w+")
    server = subprocess.Popen([exe, str(port), str(out / "hostkey"), PASSWORD],
                              stdout=log, stderr=subprocess.STDOUT)
    for _ in range(100):
        log.seek(0)
        if "listening" in log.read():
            break
        time.sleep(0.05)
    askpass = out / "askpass.sh"
    askpass.write_text("#!/bin/sh\necho \"$MOY_SSH_PW\"\n")
    askpass.chmod(0o700)
    yield port, askpass, log
    server.kill()
    server.wait()
    log.close()


def ssh(harness, password, *args, stdin=None, timeout=20):
    port, askpass, _log = harness
    env = dict(os.environ, SSH_ASKPASS=str(askpass), SSH_ASKPASS_REQUIRE="force",
               DISPLAY="moy", MOY_SSH_PW=password)
    cmd = ["ssh", "-p", str(port), "-o", "StrictHostKeyChecking=no",
           "-o", "UserKnownHostsFile=/dev/null", "-o", "PubkeyAuthentication=no",
           "-o", "PreferredAuthentications=password", "-o", "NumberOfPasswordPrompts=1",
           "-o", "LogLevel=ERROR", "-F", "/dev/null"] + list(args)
    return subprocess.run(cmd, env=env, input=stdin, capture_output=True,
                          timeout=timeout, stdin=None if stdin is not None else subprocess.DEVNULL)


def stats(harness):
    _port, _askpass, log = harness
    log.seek(0)
    lines = [l for l in log.read().splitlines() if l.startswith("STATS ")]
    return dict(kv.split("=") for kv in lines[-1].split()[1:]) if lines else {}


def test_a_password_signs_in_and_an_exec_runs(harness):
    r = ssh(harness, PASSWORD, "moy@127.0.0.1", "ls /")
    assert r.returncode == 0, r.stderr
    assert r.stdout == b"ran: ls /\n"


def test_a_wrong_password_is_refused(harness):
    r = ssh(harness, "not-it", "moy@127.0.0.1", "ls")
    assert r.returncode == 255
    assert b"Permission denied" in r.stderr
    assert b"ran:" not in r.stdout


def test_a_terminal_session_is_the_face_s_line_discipline(harness):
    keys = b"echo hi\rabc\x7fd\r\x03exit\r"
    r = ssh(harness, PASSWORD, "-tt", "moy@127.0.0.1", stdin=keys)
    out = r.stdout
    assert b"$ echo hi\r\nran: echo hi\r\n$ " in out, out
    assert b"abc\b \bd\r\nran: abd\r\n" in out, out
    assert b"^C\r\n$ " in out, out


def test_every_session_gives_back_what_it_took(harness):
    for i in range(3):
        assert ssh(harness, PASSWORD, "moy@127.0.0.1", "echo %d" % i).returncode == 0
    time.sleep(0.2)
    s = stats(harness)
    held = int(s["heap_now"])
    for i in range(3):
        assert ssh(harness, PASSWORD, "moy@127.0.0.1", "echo %d" % i).returncode == 0
    time.sleep(0.2)
    assert int(stats(harness)["heap_now"]) == held
    assert int(stats(harness)["session_peak"]) > 0

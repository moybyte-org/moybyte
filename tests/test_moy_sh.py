"""The C shell (native/moy_sh/) on the host, under the sanitizers.

native/moy_sh/host/sh_main.c is a session over a temporary folder, fed a
script line by line as a face feeds it, printing each prompt and line before
what it ran (a transcript). tests/moy_sh/ holds a script per feature and per
C built-in, each beside the transcript it must produce, and the session's
exit status closes it. MOYBYTE_UPDATE_GOLDENS=1 rewrites the transcripts;
read the diff before committing one.

The harness's OS is a stand-in (its header lists what it serves): installed
commands, a background program, the service roles, /dev and /proc, the log.
"""

import fcntl
import glob
import hashlib
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SH = os.path.join(ROOT, "native", "moy_sh")
DASH = os.path.join(SH, "dash")
STORE = os.path.join(ROOT, "native", "moy_store")
KERNEL = os.path.join(ROOT, "native", "moy_kernel")
SCRIPTS = os.path.join(ROOT, "tests", "moy_sh")

CFLAGS = ["-std=gnu99", "-g", "-O1", "-fsanitize=address,undefined",
          "-fno-sanitize-recover=all", "-Wall", "-Werror"]


def shell_sources():
    """What the shell compiles: dash's sources, its built-ins, the session."""
    srcs = sorted(glob.glob(os.path.join(DASH, "*.c")))
    srcs += [os.path.join(DASH, "bltin", "printf.c"), os.path.join(DASH, "bltin", "test.c")]
    srcs += [os.path.join(SH, n) for n in ("moy_sh.c", "moy_sh_sys.c", "moy_sh_cmds.c")]
    return srcs


def _objects(cc, out):
    dash_flags = CFLAGS + ["-include", os.path.join(SH, "dash_config.h"),
                           "-I", SH, "-I", DASH, "-I", STORE]
    jobs = [(src, dash_flags) for src in shell_sources()]
    jobs.append((os.path.join(SH, "moy_sh_arena.c"), CFLAGS + ["-I", SH]))
    jobs.append((os.path.join(SH, "host", "sh_main.c"), CFLAGS + ["-I", SH, "-I", KERNEL]))
    jobs.append((os.path.join(STORE, "moy_vol.c"), CFLAGS + ["-I", STORE]))
    jobs.append((os.path.join(STORE, "moy_store_host.c"), CFLAGS + ["-I", STORE]))
    jobs.append((os.path.join(KERNEL, "moy_log.c"), CFLAGS + ["-I", KERNEL]))

    def one(job):
        src, flags = job
        obj = os.path.join(out, os.path.basename(src)[:-2] + ".o")
        r = subprocess.run([cc] + flags + ["-c", src, "-o", obj],
                           capture_output=True, text=True)
        return obj, r

    with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
        done = list(pool.map(one, jobs))
    for _obj, r in done:
        assert r.returncode == 0, r.stderr[-4000:]
    return [obj for obj, _r in done]


@pytest.fixture(scope="session")
def harness(tmp_path_factory):
    """sh_main, built once per source state and shared by xdist's workers."""
    cc = shutil.which(os.environ.get("CC", "cc"))
    if cc is None:
        pytest.skip("no C compiler")
    h = hashlib.sha256()
    for d in (SH, STORE, KERNEL):
        for path in sorted(glob.glob(os.path.join(d, "**", "*.[ch]"), recursive=True)):
            h.update(path.encode())
            with open(path, "rb") as f:
                h.update(f.read())
    base = tmp_path_factory.getbasetemp().parent / "moy_sh_host" / h.hexdigest()[:16]
    base.mkdir(parents=True, exist_ok=True)
    exe = str(base / "sh_main")
    with open(base / "lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not os.path.exists(exe):
            objs = _objects(cc, str(base))
            r = subprocess.run([cc] + CFLAGS + objs + ["-o", exe + ".tmp"],
                               capture_output=True, text=True)
            assert r.returncode == 0, r.stderr[-4000:]
            os.replace(exe + ".tmp", exe)
    return exe


def run(harness, script, root):
    # one stream, as a terminal shows them: the harness writes each unbuffered
    r = subprocess.run([harness, "-t", str(root), script], stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, timeout=60, env=dict(os.environ, TZ="UTC"))
    return r.stdout.decode("utf-8", "replace") + "[exit %d]\n" % r.returncode


SCRIPT_NAMES = sorted(os.path.basename(p)[:-3] for p in glob.glob(os.path.join(SCRIPTS, "*.sh")))


@pytest.mark.parametrize("name", SCRIPT_NAMES)
def test_the_script_runs_as_its_transcript_says(harness, tmp_path, name):
    script = os.path.join(SCRIPTS, name + ".sh")
    golden = os.path.join(SCRIPTS, name + ".out")
    root = tmp_path / "root"
    root.mkdir()
    got = run(harness, script, root)
    assert "Sanitizer" not in got and "runtime error" not in got, got
    if os.environ.get("MOYBYTE_UPDATE_GOLDENS"):
        with open(golden, "w", encoding="utf-8") as f:
            f.write(got)
    assert os.path.isfile(golden), "no transcript for %s: run with MOYBYTE_UPDATE_GOLDENS=1" % name
    with open(golden, encoding="utf-8") as f:
        assert got == f.read()


def test_every_core_builtin_has_its_script():
    """A C built-in is tested by tests/moy_sh/builtin_<name>.sh."""
    with open(os.path.join(SH, "moy_sh_cmds.c"), encoding="utf-8") as f:
        table = f.read().split("static const struct builtincmd core[] = {")[1].split("};")[0]
    names = re.findall(r'\{ "([a-z]+)",', table)
    assert names == sorted(names), "the core table is searched by bisection: keep it sorted"
    aliases = {"dmesg": "log"}
    missing = [n for n in names if aliases.get(n, n) not in
               {s[len("builtin_"):] for s in SCRIPT_NAMES if s.startswith("builtin_")}]
    assert not missing, "built-ins with no script: %s" % missing


def test_the_shell_keeps_no_writable_data_but_its_state_pointer(tmp_path):
    """Every dash global is a field of the session's record (moy_sh_g.h), in
    the arena: compiled as a board compiles it (no PIC), the shell's objects
    hold one writable word, moy_sh_g, and nothing in .data or .bss besides."""
    cc = shutil.which(os.environ.get("CC", "cc"))
    nm = shutil.which("nm")
    if cc is None or nm is None:
        pytest.skip("no C compiler or nm")
    flags = ["-std=gnu99", "-Os", "-fno-pic", "-fno-pie", "-fno-common",
             "-include", os.path.join(SH, "dash_config.h"), "-I", SH, "-I", DASH, "-I", STORE]
    writable = []
    for src in shell_sources() + [os.path.join(SH, "moy_sh_arena.c")]:
        obj = str(tmp_path / (os.path.basename(src)[:-2] + ".o"))
        r = subprocess.run([cc] + flags + ["-c", src, "-o", obj], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr[-2000:]
        for line in subprocess.check_output([nm, obj], text=True).splitlines():
            parts = line.split()
            if len(parts) == 3 and parts[1] in "bBdDcC":
                writable.append("%s: %s" % (os.path.basename(src), parts[2]))
    assert writable == ["moy_sh.c: moy_sh_g"], writable

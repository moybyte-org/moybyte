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
SPINE = os.path.join(ROOT, "native", "moy_spine")
SCRIPTS = os.path.join(ROOT, "tests", "moy_sh")

CFLAGS = ["-std=gnu99", "-g", "-O1", "-fsanitize=address,undefined",
          "-fno-sanitize-recover=all", "-Wall", "-Werror"]


def shell_sources():
    """What the shell compiles: dash's sources, its built-ins, the session."""
    srcs = sorted(glob.glob(os.path.join(DASH, "*.c")))
    srcs += [os.path.join(DASH, "bltin", "printf.c"), os.path.join(DASH, "bltin", "test.c")]
    srcs += [os.path.join(SH, n) for n in ("moy_sh.c", "moy_sh_sys.c", "moy_sh_cmds.c")]
    return srcs


# The board's side, compiled without dash's shim: its host table and the
# serial face, the screen face and its grid.
BOARD_SOURCES = ("moy_sh_board.c", "moy_sh_screen.c", "moy_sh_store.c", "moy_term.c",
                 "moy_sh_app.c", "moy_sh_edit.c", "moy_sh_top.c")
# The Terminal's face and the recovery floor's screen over it draw through the
# toolkit's engine, so they compile against native/moy_ui and the harnesses,
# which have no toolkit, leave them out.
FACE_SOURCES = ("moy_sh_face.c", "moy_sh_floor.c")
UI_INCLUDES = [a for n in ("moy_ui", "moy_gfx", "moy_glass")
               for a in ("-I", os.path.join(ROOT, "native", n))]


def _objects(cc, out):
    dash_flags = CFLAGS + ["-include", os.path.join(SH, "dash_config.h"),
                           "-I", SH, "-I", DASH, "-I", STORE]
    jobs = [(src, dash_flags) for src in shell_sources()]
    jobs.append((os.path.join(SH, "moy_sh_arena.c"), CFLAGS + ["-I", SH]))
    jobs.append((os.path.join(SH, "moy_sh_fmt.c"), CFLAGS + ["-I", SH]))
    jobs.append((os.path.join(SH, "host", "sh_main.c"), CFLAGS + ["-I", SH, "-I", KERNEL]))
    board = CFLAGS + ["-I", SH, "-I", KERNEL, "-I", STORE, "-I", SPINE]
    for name in BOARD_SOURCES:
        jobs.append((os.path.join(SH, name), board))
    jobs.append((os.path.join(SH, "host", "board_main.c"), board))
    jobs.append((os.path.join(STORE, "moy_vol.c"), CFLAGS + ["-I", STORE]))
    jobs.append((os.path.join(STORE, "moy_store_host.c"), CFLAGS + ["-I", STORE]))
    jobs.append((os.path.join(STORE, "moy_fs.c"), CFLAGS + ["-I", STORE]))
    for name in ("moy_shelf.c", "moy_cat.c", "moy_files.c"):
        jobs.append((os.path.join(STORE, name), CFLAGS + ["-I", STORE, "-I", SPINE]))
    jobs.append((os.path.join(SPINE, "moy_settings.c"), CFLAGS + ["-I", SPINE]))
    jobs.append((os.path.join(SPINE, "moy_json.c"), CFLAGS + ["-I", SPINE]))
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
    for d in (SH, STORE, KERNEL, SPINE):
        for path in sorted(glob.glob(os.path.join(d, "**", "*.[ch]"), recursive=True)):
            h.update(path.encode())
            with open(path, "rb") as f:
                h.update(f.read())
    base = tmp_path_factory.getbasetemp().parent / "moy_sh_host" / h.hexdigest()[:16]
    base.mkdir(parents=True, exist_ok=True)
    exes = {name: str(base / name) for name in ("sh_main", "board_main")}
    with open(base / "lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not all(os.path.exists(e) for e in exes.values()):
            objs = _objects(cc, str(base))
            mains = {name: os.path.join(str(base), name + ".o") for name in exes}
            board = [os.path.join(str(base), n[:-2] + ".o") for n in BOARD_SOURCES]
            shared = [o for o in objs if o not in mains.values() and o not in board]
            for name, exe in exes.items():
                own = [mains[name]] + (board if name == "board_main" else [])
                r = subprocess.run([cc] + CFLAGS + shared + own + ["-o", exe + ".tmp"],
                                   capture_output=True, text=True)
                assert r.returncode == 0, r.stderr[-4000:]
                os.replace(exe + ".tmp", exe)
    return exes


def run(harness, script, root):
    # one stream, as a terminal shows them: the harness writes each unbuffered
    r = subprocess.run([harness["sh_main"], "-t", str(root), script], stdout=subprocess.PIPE,
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


SERIAL = os.path.join(SCRIPTS, "serial")
SERIAL_NAMES = sorted(os.path.basename(p)[:-3] for p in glob.glob(os.path.join(SERIAL, "*.sh")))


@pytest.mark.parametrize("name", SERIAL_NAMES)
def test_the_serial_face_answers_as_its_transcript_says(harness, tmp_path, name):
    """The board's host table (moy_sh_board.c) and the serial face, each
    script line fed as the dev channel's `sh` word feeds it, each line the
    face says printed as the channel carries it. A `vm_` script runs with a
    stand-in console behind the ROLE door; the others with the VM stopped,
    where `ps`, `kill` and `log` answer from the kernel and the console's
    commands say "needs the console"."""
    script = os.path.join(SERIAL, name + ".sh")
    golden = os.path.join(SERIAL, name + ".out")
    root = tmp_path / "root"
    root.mkdir()
    _seed_home(root, name)
    args = [harness["board_main"]] + (["-vm"] if name.startswith("vm_") else []) + [script]
    r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60,
                       env=dict(os.environ, TZ="UTC", MOY_SH_ROOT=str(root)))
    got = r.stdout.decode("utf-8", "replace") + "[exit %d]\n" % r.returncode
    assert "Sanitizer" not in got and "runtime error" not in got, got
    if os.environ.get("MOYBYTE_UPDATE_GOLDENS"):
        with open(golden, "w", encoding="utf-8") as f:
            f.write(got)
    assert os.path.isfile(golden), "no transcript for %s: run with MOYBYTE_UPDATE_GOLDENS=1" % name
    with open(golden, encoding="utf-8") as f:
        assert got == f.read()


def _seed_home(root, name):
    """A `home_` script's tree holds a console's store on its card, a
    `nocard_` script's on the internal flash (/moy): its carts and its files
    folder, where the session starts (moy_sh_board_go_home)."""
    store = ("sd/moybyte" if name.startswith("home_")
             else "moy" if name.startswith("nocard_") else None)
    for sub in ("carts", "files") if store else ():
        (root / store / sub).mkdir(parents=True)


def _face(harness, path, root, screen):
    name = os.path.basename(path)
    args = ([harness["board_main"]] + (["-vm"] if name.startswith("vm_") else [])
            + (["-screen"] if screen else []) + [path])
    r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60,
                       env=dict(os.environ, TZ="UTC", MOY_SH_ROOT=str(root)))
    return r.stdout.decode("utf-8", "replace")


# interrupt.sh's Ctrl-C is sh_main's stand-in (/.interrupt), which no face polls;
# neofetch's rows are sh_main's stand-in console's, and the screen face draws
# them in colour where the serial face (TERM=dumb) writes them plain;
# an `app_` script's text app shows on the screen face's own screen, which
# tests/test_terminal.py holds.
REPLAYS = ([os.path.join(SERIAL, n + ".sh") for n in SERIAL_NAMES if not n.startswith("app_")]
           + [os.path.join(SCRIPTS, n + ".sh") for n in SCRIPT_NAMES
              if n not in ("interrupt", "builtin_neofetch")])


@pytest.mark.parametrize("path", REPLAYS, ids=[os.path.relpath(p, SCRIPTS) for p in REPLAYS])
def test_the_screen_face_replays_the_serial_transcript(harness, tmp_path, path):
    """One transcript, both faces: each serial script's lines fed to the
    screen face (moy_sh_screen.c) give, line for line, what its grid shows,
    the serial face's output with its tabs set at the grid's stops and
    its trailing blanks, which a grid does not hold, dropped. A
    transcript that writes escapes (`clear`) says them raw on serial and
    moves the grid, so it is the grid's own test's. Every script of the
    suite replays so too, over the board's table in place of sh_main's."""
    for side in ("a", "b"):
        (tmp_path / side).mkdir()
        _seed_home(tmp_path / side, os.path.basename(path)[:-3])
    serial = _face(harness, path, tmp_path / "a", False)
    if "\x1b" in serial:
        pytest.skip("escapes: the grid's test reads them")
    screen = _face(harness, path, tmp_path / "b", True)
    assert "Sanitizer" not in screen and "runtime error" not in screen, screen
    want = ["SH " + ln[3:].expandtabs(8).rstrip(" ") if ln.startswith("SH ") else ln
            for ln in serial.split("\n")]
    assert screen.split("\n") == want


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
    hold one writable word, moy_sh_g, and nothing in .data or .bss besides.
    The board's host table and the serial face (moy_sh_board.c) keep none:
    the serial session is parked in that word (moy_sh_park), and the
    Terminal's face (moy_sh_face.c) keeps everything in its record."""
    cc = shutil.which(os.environ.get("CC", "cc"))
    nm = shutil.which("nm")
    if cc is None or nm is None:
        pytest.skip("no C compiler or nm")
    flags = ["-std=gnu99", "-Os", "-fno-pic", "-fno-pie", "-fno-common",
             "-include", os.path.join(SH, "dash_config.h"), "-I", SH, "-I", DASH, "-I", STORE]
    plain = ["-std=gnu99", "-Os", "-fno-pic", "-fno-pie", "-fno-common",
             "-I", SH, "-I", KERNEL, "-I", STORE, "-I", SPINE]
    writable = []
    for src in shell_sources() + [os.path.join(SH, n) for n in ("moy_sh_arena.c", "moy_sh_fmt.c")
                                  + BOARD_SOURCES + FACE_SOURCES]:
        obj = str(tmp_path / (os.path.basename(src)[:-2] + ".o"))
        own = flags if src in shell_sources() else plain + UI_INCLUDES
        r = subprocess.run([cc] + own + ["-c", src, "-o", obj], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr[-2000:]
        for line in subprocess.check_output([nm, obj], text=True).splitlines():
            parts = line.split()
            if len(parts) == 3 and parts[1] in "bBdDcC":
                writable.append("%s: %s" % (os.path.basename(src), parts[2]))
    assert writable == ["moy_sh.c: moy_sh_g"], writable


def test_no_shell_object_calls_the_c_library_s_descriptors(tmp_path):
    """dash's file calls are the shell's own (moy_sh_posix.h's names over
    moy_sh_sys.c's descriptors): a source that missed the shim called the
    C library's close() on the process's real descriptors -- mkinit's init.c
    did, closing an SSH session's sockets at a command substitution's end."""
    cc = shutil.which(os.environ.get("CC", "cc"))
    nm = shutil.which("nm")
    if cc is None or nm is None:
        pytest.skip("no C compiler or nm")
    flags = ["-std=gnu99", "-Os", "-include", os.path.join(SH, "dash_config.h"),
             "-I", SH, "-I", DASH, "-I", STORE]
    raw = {"open", "read", "write", "close", "dup", "dup2", "fcntl", "pipe", "lseek",
           "open64", "stat", "fstat", "lstat"}
    calls = []
    for src in shell_sources():
        obj = str(tmp_path / (os.path.basename(src)[:-2] + ".o"))
        r = subprocess.run([cc] + flags + ["-c", src, "-o", obj], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr[-2000:]
        for line in subprocess.check_output([nm, "-u", obj], text=True).splitlines():
            if line.split()[-1] in raw:
                calls.append("%s: %s" % (os.path.basename(src), line.split()[-1]))
    assert calls == [], calls


def test_every_source_keeps_the_const_a_c23_library_preserves(tmp_path):
    """A newer C library's C23 bsearch, strchr, strchrnul ... answer a const
    pointer for a const argument, and CI's compiler refused dash's implicit
    drops of it (exec.c's bsearch over the const builtin table). Every shell
    source compiles here with tests/moy_sh/c23_const.h force-included, which
    defines them so, under -Werror: this machine refuses what CI refuses, and
    a re-vendor of dash cannot bring the error back unseen. Syntax only, in
    parallel: it reads, never generates code."""
    cc = shutil.which(os.environ.get("CC", "cc"))
    if cc is None:
        pytest.skip("no C compiler")
    guard = ["-include", os.path.join(SCRIPTS, "c23_const.h")]
    base = ["-std=gnu99", "-fsyntax-only", "-Wall", "-Werror"] + guard
    dash = base + ["-include", os.path.join(SH, "dash_config.h"), "-I", SH, "-I", DASH,
                   "-I", STORE]
    plain = base + ["-I", SH, "-I", KERNEL, "-I", STORE, "-I", SPINE]
    jobs = [(src, dash) for src in shell_sources()]
    jobs += [(os.path.join(SH, n), plain) for n in ("moy_sh_arena.c", "moy_sh_fmt.c",
                                                     "host/sh_main.c", "host/board_main.c")
             + BOARD_SOURCES]
    jobs += [(os.path.join(SH, n), plain + UI_INCLUDES) for n in FACE_SOURCES]

    def one(job):
        src, flags = job
        r = subprocess.run([cc] + flags + [src], capture_output=True, text=True)
        return src, r

    with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
        bad = [(src, r.stderr[-1500:]) for src, r in pool.map(one, jobs) if r.returncode]
    assert not bad, bad

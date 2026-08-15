""""No C compiler" has to READ like a missing compiler (#28).

Since runtime/canvas.py was deleted the host has no Python raster to fall back
on: it draws on device_canvas.DeviceCanvas over runtime/gfx_binding.py, which
ctypes-loads a library runtime/native_build.py compiles. So "no C compiler = no
host console" stopped being a caveat and became the plain truth -- and what the
user actually got was

    AttributeError: 'NoneType' object has no attribute 'hg_fill'

four frames inside a ctypes wrapper, naming nothing they could act on.

Nothing here tests a FALLBACK, because there deliberately isn't one. These pin
that the failure is STATED: that both flavours of absence (nothing on PATH, and
a $CC pointing at nothing) reach the same explanation instead of an OSError
traceback, and that `make setup` asks the question while the user is still at
the terminal.
"""

import pathlib
import subprocess
import sys

import pytest

from runtime import native_build


ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _fresh_warning():
    """warn_no_cc is once-per-process; each test wants its own first time."""
    native_build._WARNED[0] = False
    yield
    native_build._WARNED[0] = False


# -- what the sentence has to contain ----------------------------------------

def test_the_explanation_answers_what_why_and_what_to_type():
    text = native_build.explain_no_cc()
    assert "NO C COMPILER" in text                       # what
    assert "host console cannot run" in text             # why it matters now
    assert "$CC" in text and "gcc" in text               # where it looked
    # ...and a command, not just a diagnosis. One of the shipped hints for this
    # platform has to be in there.
    assert any(hint in text for _os, hint in native_build.INSTALL_HINTS), text
    # Firmware work is NOT blocked by this, and a message that let someone
    # believe otherwise would send them off installing the wrong toolchain.
    assert "Firmware builds are unaffected" in text


def test_the_explanation_names_a_CC_that_points_nowhere(monkeypatch):
    monkeypatch.setenv("CC", "/no/such/cc")
    assert "/no/such/cc" in native_build.explain_no_cc()


def test_the_warning_is_printed_once_per_process(capsys):
    native_build.warn_no_cc()
    native_build.warn_no_cc()
    err = capsys.readouterr().err
    assert err.count("NO C COMPILER") == 1, \
        "three host bindings ask independently; three paragraphs read as " \
        "three problems"


def test_the_warning_goes_to_stderr_not_the_console(capsys):
    native_build.warn_no_cc()
    cap = capsys.readouterr()
    assert "NO C COMPILER" in cap.err
    assert "NO C COMPILER" not in cap.out


# -- both flavours of absence reach it ---------------------------------------

def _build_gfx(**kw):
    """Ask for the real host gfx build, whatever the environment allows."""
    from runtime import gfx_binding
    return native_build.build("moyhost_gfx", gfx_binding._SHIM,
                              gfx_binding._LIBMOY, str(ROOT / ".build" / "t"),
                              cflags=gfx_binding._CFLAGS, **kw)


def test_nothing_on_path_returns_None_and_explains(monkeypatch, capsys):
    monkeypatch.delenv("CC", raising=False)
    monkeypatch.setattr(native_build.shutil, "which", lambda _n: None)
    assert _build_gfx() is None
    assert "NO C COMPILER" in capsys.readouterr().err


def test_a_CC_that_cannot_be_run_explains_instead_of_raising(monkeypatch,
                                                             capsys):
    """The case that used to escape as a bare FileNotFoundError from subprocess.

    A $CC pointing at a path that is not there is an ABSENT toolchain, not a
    broken tree -- the tree compiles fine for anyone whose setting points
    somewhere real -- so it takes the None lane, not the RuntimeError one.
    """
    monkeypatch.setenv("CC", str(ROOT / "no" / "such" / "cc"))
    assert _build_gfx() is None
    err = capsys.readouterr().err
    assert "NO C COMPILER" in err
    assert "no/such/cc" in err                # ...and names the setting at fault


def test_a_real_compile_error_still_raises(tmp_path):
    """The other half of the doctrine, unchanged: a compiler that RUNS and
    fails is a broken tree and must be loud, not degraded to None."""
    if native_build.cc() is None:
        pytest.skip("no compiler here to fail with")
    shim = tmp_path / "broken_shim.c"
    shim.write_text("this is not C;\n", encoding="utf-8")
    with pytest.raises(RuntimeError) as exc:
        native_build.build("broken", str(shim), [], str(tmp_path / "cache"))
    assert "build failed" in str(exc.value)


# -- the check `make setup` runs ---------------------------------------------

def test_check_reports_the_compiler_when_there_is_one(capsys):
    if native_build.cc() is None:
        pytest.skip("no compiler here to report")
    assert native_build.check() is not None
    assert "C compiler" in capsys.readouterr().out


def test_check_exits_nonzero_with_no_compiler(monkeypatch):
    """`python -m runtime.native_build` is what the Makefile branches on."""
    env = {k: v for k, v in __import__("os").environ.items() if k != "CC"}
    env["PATH"] = str(ROOT / "no" / "such" / "dir")
    proc = subprocess.run([sys.executable, "-m", "runtime.native_build"],
                          cwd=str(ROOT), env=env, capture_output=True,
                          text=True)
    assert proc.returncode == 1
    assert "NO C COMPILER" in proc.stderr


def test_make_setup_asks_before_the_user_walks_away():
    """The message is worth little if it first appears an hour later, out of a
    ctypes wrapper. `make setup` runs the check itself and says what an exit
    code of 1 means."""
    mk = (ROOT / "Makefile").read_text(encoding="utf-8")
    setup = mk.split("\nsetup:", 1)[1].split("\ncheck-venv:", 1)[0]
    assert "-m runtime.native_build" in setup
    assert "C compiler" in setup

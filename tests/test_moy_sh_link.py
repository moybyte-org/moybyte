"""A link's face (native/moy_sh/moy_sh_link.h): the Terminal's screen with no
shell behind it and its `remote` app, as the board-served page's console
builds them, driven the way the page's transport drives them
(native/moy_sh/host/link_main.c). The grid after each step, every message the
app says among them, is held to tests/moy_sh_link/messages.out
(MOYBYTE_UPDATE_GOLDENS=1 writes it)."""

import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SH = os.path.join(ROOT, "native", "moy_sh")
GOLDEN = os.path.join(ROOT, "tests", "moy_sh_link", "messages.out")

pytestmark = pytest.mark.skipif(shutil.which("cc") is None, reason="needs a C compiler")


def test_the_remote_app_s_grid_and_messages(tmp_path):
    exe = str(tmp_path / "link_main")
    srcs = [os.path.join(SH, n) for n in ("moy_sh_link.c", "moy_sh_app.c", "moy_term.c",
                                          "host/link_main.c")] + [os.path.join(ROOT, "native", "moy_ui", "moy_ui_tables.c")]
    subprocess.run([shutil.which("cc"), "-std=gnu99", "-g", "-O1", "-Wall", "-Werror",
                    "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                    "-DMOY_SH_LINK=1", "-I", SH, "-o", exe] + srcs, check=True)
    out = subprocess.run([exe], check=True, capture_output=True, text=True,
                         env=dict(os.environ, ASAN_OPTIONS="detect_leaks=1")).stdout
    if os.environ.get("MOYBYTE_UPDATE_GOLDENS"):
        with open(GOLDEN, "w", encoding="utf-8") as f:
            f.write(out)
    with open(GOLDEN, encoding="utf-8") as f:
        assert out == f.read()

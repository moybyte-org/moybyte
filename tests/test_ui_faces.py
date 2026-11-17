"""The toolkit's generated verb faces (tools/gen_os/ui.py, from
native/moy_ui/face.json's `api` rows and native/os_calls.json's `ui` rows)
are one set of names and one set of answers on every face:

  * names: every `api` row is the board module's `api_<verb>` and the
    host's, and every `ui` row a Lua cart's `ui.<verb>`;
  * answers: one probe, generated from the rows (`probe_script`), calls each
    generated context-form row three frames running, and its answers on
    CPython over the ctypes face equal its answers on the desktop
    MicroPython in the boards' object model, over the module's C.

The files themselves are tools/gen_os_table.py's outputs, which
tests/test_os_table.py regenerates and compares; the RUN form and a
compiled app's rows are held by the pages every tier draws alike
(tests/test_ui_public.py, tests/test_app_ui.py)."""

import os
import re
import subprocess
import sys

from tools.gen_os import ui as G
from tools.gen_os.table import load
from unix_mp import require_unix_mp, runtime_tree

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERBS = G.verbs()

HEAD_MP = '''
import sys
from array import array
sys.path.insert(0, %(rt)r)
sys.path.insert(0, %(root)r + "/device")
import moy_gfx
import moy_ui as M
import device_canvas, ui_draw, ui


class Comp:
    buf_h = 200

    def __init__(self):
        self.fb = bytearray(240 * 200 * 2)

    def size(self):
        return (240, 200)

    def framebuffer(self):
        return self.fb

    def gfx(self):
        return moy_gfx


CV = device_canvas.SystemCanvas(Comp())
'''

HEAD_HOST = '''
import sys
from array import array
sys.path.insert(0, %(root)r)
import runtime
from runtime import moy_ui as M, ui_draw, ui
from runtime.host_canvas import make_system_canvas
CV = make_system_canvas(240, 200)
'''

COMMON = '''
CTX = ui_draw.ui_context()
INP = array("i", [0] * ui_draw.UI_IN_LEN)
OV = ui._block({"id": "pid", "current": True, "hint": "h", "steps": True})


def begin():
    ui_draw.ui_frame(CTX, CV, None, None, INP)

'''


def test_every_api_row_is_a_name_on_both_faces():
    from runtime import moy_ui
    with open(G.HAND_MP) as f:
        hand = f.read()
    with open(G.MP_C) as f:
        gen = f.read()
    table = re.search(r"moy_ui_globals_table\[\] = \{(.*?)\n\};", hand, re.S).group(1)
    assert "MOY_FACE_MOY_UI_ROWS" in table
    mp = set(re.findall(r"MP_QSTR_(api_\w+)\)", table + gen))
    for v in VERBS:
        assert v.name in mp, v.name
        assert callable(getattr(moy_ui, v.name, None)), v.name


def test_every_ui_row_is_a_lua_verb():
    with open(G.LUA_H) as f:
        reg = re.search(r"#define MOYCORE_UI_VERBS(.*?)\n\n", f.read(), re.S).group(1)
    verbs = set(re.findall(r'\{"(\w+)", moycore_ui_\1\}', reg))
    assert verbs == {r["name"][3:] for r in load()["rows"] if r["name"].startswith("ui.")}


def test_the_context_form_answers_alike_on_cpython_and_the_boards_micropython():
    exe = require_unix_mp("moy_ui", board_model=True,
                          why="the generated context form of moy_ui")
    script = G.probe_script(VERBS)
    host = subprocess.run([sys.executable, "-c", HEAD_HOST % {"root": ROOT} + COMMON + script],
                          cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert host.returncode == 0, host.stderr[-2000:]
    mp = subprocess.run([exe, "-c", HEAD_MP % {"root": ROOT, "rt": runtime_tree()} + COMMON
                         + script], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert mp.returncode == 0, mp.stdout[-1000:] + mp.stderr[-2000:]
    got, want = mp.stdout.splitlines(), host.stdout.splitlines()
    assert want[-1] == "DONE" and len(want) == 3 * len(G.probe_calls(VERBS)) + 1
    for a, b in zip(got, want):
        assert a == b, "MicroPython %r, CPython %r" % (a, b)
    assert len(got) == len(want)

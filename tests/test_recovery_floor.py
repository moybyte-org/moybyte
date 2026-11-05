"""The recovery console's screen (native/moy_sh/moy_sh_floor.h): the Terminal's
face under the reason and the choices, rendered by the same C the boards'
floors run, on the host's one library.

The floor installs its look process-wide (the kernel's look, the shape, the
depth, the glyph lane's and the shapes' word order, the input in use), as a
board's floor does with no console beside it, so every render here runs in a
process of its own: `render` runs this file as a script.

MOY_RECOVERY_PNG=<dir> writes each console's golden as a PNG to look at.
tools/kernel_gate.py holds a board's floor to `render` of the lines it
printed.
"""

import ctypes
import json
import os
import struct
import subprocess
import sys
import zlib

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Each console's floor as moy_kernel.c's k_floor_up makes it: the logical
# screen, the desk's metrics and font scale, the framebuffer's word order and
# the input it reads (moy_ui.h's MOY_UI_INPUT_TDECK 1, MOY_UI_INPUT_TOUCH 3).
CONSOLES = {
    "tdeck": (320, 240, 0, 1, 1, 1),
    "guition_s3": (480, 320, 0, 1, 1, 3),
    "p4": (1024, 600, 1, 2, 0, 3),
    "guition_p4": (1280, 800, 1, 2, 0, 3),
}
# The hint each floor's head shows (native/moy_spine/strings/en.json).
HINTS = {
    "tdeck": "Type retry, safe or reboot.",
    "guition_s3": "Tap a choice, or type retry, safe or repl on serial.",
    "p4": "Tap a choice, or type retry, safe or repl on serial.",
    "guition_p4": "Tap a choice, or type retry, safe or repl on serial.",
}
# What moy_recovery_view says for a fault the console crashed with three
# boots running.
LINES = ["The console kept crashing.", "Last: fault in mp_task", "app: paint",
         "StoreProhibited", "PC 42012345 at 00000000", "Up 81 s, boot 7",
         "Firmware 1.2.3-test"]
# The crc32 of each console's settled first screen for LINES and its hint, the
# words in the framebuffer's order.
GOLDEN = {
    "tdeck": 0xDBE43829,
    "guition_s3": 0xF2AF152B,
    "p4": 0x4D20CC15,
    "guition_p4": 0x7C6E4F93,
}
NONE, RETRY, SAFE, REBOOT, REPL = -1, 0, 1, 2, 3


def render(board, lines=LINES, hint=None, steps=(), png=None, root=None):
    """{"crc", "choices", "rows", "screen"} of `board`'s floor after `steps`
    (each a frame's input: {"key", "x", "y", "ptr", ...}, moy_sh_floor_in_t's
    fields), its first frame drawn before them."""
    job = {"board": board, "lines": list(lines),
           "hint": HINTS[board] if hint is None else hint, "steps": list(steps),
           "png": png, "root": root}
    r = subprocess.run([sys.executable, os.path.abspath(__file__), json.dumps(job)],
                       capture_output=True, text=True, cwd=ROOT, timeout=300)
    if r.returncode:
        raise RuntimeError("the floor's render failed:\n%s%s" % (r.stdout, r.stderr))
    return json.loads(r.stdout.strip().splitlines()[-1])


def _type(text):
    """A frame per byte typed and a frame between, as a keyboard's one-shot
    keys arrive."""
    out = []
    for ch in text:
        out += [{"key": 13 if ch == "\n" else ord(ch)}, {}]
    return out


def _tap(x, y):
    return [{"x": x, "y": y, "ptr": 7}, {"x": x, "y": y, "ptr": 3}, {"x": x, "y": y, "ptr": 1}, {}]


@pytest.fixture(scope="module")
def built():
    sys.path.insert(0, ROOT)
    from runtime import host_native
    if host_native.build() is None:
        pytest.skip("no C compiler")


@pytest.mark.parametrize("board", sorted(CONSOLES))
def test_each_consoles_floor_is_pinned(built, board):
    png = os.environ.get("MOY_RECOVERY_PNG")
    got = render(board, png=os.path.join(png, "floor_%s.png" % board) if png else None)
    assert got["crc"] == GOLDEN[board], "%s: %08x" % (board, got["crc"])
    assert [r[0] for r in got["rows"]] == ["retry", "safe", "reboot"]


@pytest.mark.parametrize("board", ["tdeck", "p4"])
def test_a_command_runs_on_screen_and_a_word_at_the_prompt_is_a_choice(built, board, tmp_path):
    got = render(board, steps=_type("echo floor-$((6 * 7))\n") + _type("safe\n"),
                 root=str(tmp_path))
    assert "floor-42" in got["screen"]
    assert got["choices"] == [SAFE]
    assert got["line"] == ""


@pytest.mark.parametrize("board", sorted(CONSOLES))
def test_a_tap_on_a_button_is_its_choice(built, board):
    first = render(board)
    want = []
    steps = []
    for name, x, y, w, h in first["rows"]:
        steps += _tap(x + w // 2, y + h // 2)
        want.append({"retry": RETRY, "safe": SAFE, "reboot": REBOOT}[name])
    assert render(board, steps=steps)["choices"] == want


def test_the_floors_words_are_the_kernels():
    """The prompt's words and the serial line's are one list in two files."""
    src = open(os.path.join(ROOT, "native", "moy_kernel", "moy_kernel.c")).read()
    floor = open(os.path.join(ROOT, "native", "moy_sh", "moy_sh_floor.c")).read()
    words = '{"retry", "safe", "reboot", "repl"}'
    assert "K_WORDS[] = %s;" % words in src and "WORDS[] = %s;" % words in floor


# ---- the render, in a process of its own ----------------------------------------------

def _png(path, px, w, h, swap):
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        for x in range(w):
            v = px[y * w + x]
            if swap:
                v = ((v >> 8) | (v << 8)) & 0xFFFF
            raw += bytes(((v >> 11) * 255 // 31, ((v >> 5) & 63) * 255 // 63,
                          (v & 31) * 255 // 31))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b""))


def _main(job):
    sys.path.insert(0, ROOT)
    from runtime import host_native
    lib = ctypes.CDLL(host_native.build())
    i32 = ctypes.c_int32

    class Geom(ctypes.Structure):
        _fields_ = [(n, i32) for n in ("w", "h", "desk", "fs", "swap", "input")]

    class In(ctypes.Structure):
        _fields_ = [(n, i32) for n in ("key", "repeats", "repeat_key", "left", "right", "up",
                                       "down", "x", "y", "ptr", "dt")]

    vp = ctypes.c_void_p
    lib.moy_sh_floor_new.restype = vp
    lib.moy_sh_floor_new.argtypes = [ctypes.POINTER(Geom), ctypes.c_char_p]
    lib.moy_sh_floor_say.argtypes = [vp, ctypes.POINTER(ctypes.c_char_p), ctypes.c_int,
                                     ctypes.c_char_p]
    lib.moy_sh_floor_frame.argtypes = [vp, ctypes.POINTER(In), ctypes.POINTER(ctypes.c_int)]
    lib.moy_sh_floor_pixels.restype = vp
    lib.moy_sh_floor_pixels.argtypes = [vp]
    lib.moy_sh_floor_row.argtypes = [vp, ctypes.c_int, ctypes.POINTER(i32)]
    lib.moy_sh_floor_word.restype = ctypes.c_char_p
    lib.moy_sh_floor_face.restype = vp
    lib.moy_sh_floor_face.argtypes = [vp]
    lib.moy_sh_face_lines.argtypes = [vp]
    lib.moy_sh_face_log_line.argtypes = [vp, i32, ctypes.c_char_p, ctypes.c_size_t]
    lib.moy_sh_face_line.restype = ctypes.c_char_p
    lib.moy_sh_face_line.argtypes = [vp, ctypes.POINTER(i32)]
    w, h, desk, fs, swap, inp = CONSOLES[job["board"]]
    root = job.get("root")
    if not root:                        # an empty files tree, as a board's floor sees no store
        import tempfile
        root = tempfile.mkdtemp(prefix="moyfloor")
    fl = lib.moy_sh_floor_new(ctypes.byref(Geom(w, h, desk, fs, swap, inp)), root.encode())
    lines = [s.encode() for s in job["lines"]]
    lib.moy_sh_floor_say(fl, (ctypes.c_char_p * len(lines))(*lines), len(lines),
                         job["hint"].encode())
    ch = ctypes.c_int()
    choices = []
    steps = [{}] + job["steps"]
    for st in steps:
        lib.moy_sh_floor_frame(fl, ctypes.byref(In(**st)), ctypes.byref(ch))
        if ch.value != NONE:
            choices.append(ch.value)
    for _ in range(20):                 # the frames a board draws until the floor settles
        if not lib.moy_sh_floor_frame(fl, ctypes.byref(In(dt=20)), ctypes.byref(ch)):
            break
    px = ctypes.string_at(lib.moy_sh_floor_pixels(fl), w * h * 2)
    if job.get("png"):
        _png(job["png"], (ctypes.c_uint16 * (w * h)).from_buffer_copy(px), w, h, swap)
    rows, b = [], (i32 * 4)()
    i = 0
    while lib.moy_sh_floor_row(fl, i, b):
        rows.append([lib.moy_sh_floor_word(i).decode()] + list(b))
        i += 1
    face = lib.moy_sh_floor_face(fl)
    buf = ctypes.create_string_buffer(512)
    screen = []
    for k in range(lib.moy_sh_face_lines(face)):
        lib.moy_sh_face_log_line(face, k, buf, 512)
        screen.append(buf.value.decode("utf-8", "replace"))
    col = i32()
    line = lib.moy_sh_face_line(face, ctypes.byref(col)).decode()
    print(json.dumps({"crc": zlib.crc32(px), "choices": choices, "rows": rows,
                      "screen": "\n".join(screen), "line": line}))


if __name__ == "__main__":
    _main(json.loads(sys.argv[1]))

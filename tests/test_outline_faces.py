"""The outline faces (native/moy_gfx/moy_path.c): the committed table is the
generator's output, and the faces draw and measure as a story needs them."""

import array
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from runtime import gfx_binding as g, host_canvas  # noqa: E402
from tools import make_font  # noqa: E402


def test_the_committed_faces_are_the_generators():
    if not all(os.path.exists(make_font.ttf_path(f)) for f in ("atkinson", "jbmono")):
        pytest.skip("the pinned TrueType files are not fetched (tools/make_font.py fetch)")
    out = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "make_outline_faces.py"),
                          "check"], capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr


def test_a_width_scales_with_the_size_and_kerns():
    one = g.path_width(1, 64 * 64, b"AVATAR")
    two = g.path_width(1, 128 * 64, b"AVATAR")
    assert abs(two - 2 * one) <= 6 * 2
    assert g.path_width(1, 64 * 64, b"AV") < g.path_width(1, 64 * 64, b"A") + \
        g.path_width(1, 64 * 64, b"V")
    assert g.path_width(2, 64 * 64, b"iii") == g.path_width(2, 64 * 64, b"WWW")
    assert g.path_width(3, 64 * 64, b"a") == -1 and g.path_width(0, 0, b"a") == -1


def test_a_codepoint_past_latin1_draws_as_a_question_mark():
    q = g.path_width(0, 32 * 64, b"?")
    assert g.path_width(0, 32 * 64, "…".encode()) == q
    assert g.path_width(0, 32 * 64, "é".encode()) == g.path_width(0, 32 * 64, b"\xe9") \
        or g.path_width(0, 32 * 64, "é".encode()) > 0


def _ink(cv):
    return sum(1 for i in range(0, len(cv._buf), 2) if cv._buf[i] or cv._buf[i + 1])


def test_text_inks_inside_its_advance_and_the_clip():
    cv = host_canvas.make_system_canvas(200, 60)
    cv.cls(0)
    adv = cv.face_text(1, 40 * 64, 10 * 64, 45 * 64, "Hello!", 7)
    assert adv == g.path_width(1, 40 * 64, b"Hello!")
    assert _ink(cv) > 300
    cv.cls(0)
    cv.clip(0, 0, 20, 60)
    cv.face_text(1, 40 * 64, 10 * 64, 45 * 64, "Hello!", 7)
    cv.clip()
    for y in range(60):
        for x in range(20, 200):
            i = 2 * (y * 200 + x)
            assert cv._buf[i] == 0 and cv._buf[i + 1] == 0


def test_a_ring_is_a_hole_where_its_inner_contour_turns_back():
    cv = host_canvas.make_system_canvas(40, 40)
    cv.cls(0)
    ring = array.array("i", [v * 64 for v in (4, 4, 36, 4, 36, 36, 4, 36,
                                              12, 12, 12, 28, 28, 28, 28, 12)])
    cv.fill_path(ring, array.array("i", [4, 8]), 7)
    at = lambda x, y: cv._buf[2 * (y * 40 + x)] | cv._buf[2 * (y * 40 + x) + 1]  # noqa: E731
    assert at(6, 20) and at(20, 6) and not at(20, 20) and not at(2, 2)

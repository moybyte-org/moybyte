"""The one slide renderer (runtime/story_render.py): a slide at any zoom, its
words in the shell's outline faces, its shapes filled, its pictures read a
row at a time at the size they draw.

Two nets: the slide drawn at each of the look's zooms is held to the
targets' own pictures of it (docs/media/look_2026-10/storybook_present*.png,
drawn by rounds/round15.py: FreeType there, moy_path here, so a pixel may
differ by its edge's shade and nothing more), and to its exact pixels at
every zoom, pinned as hashes of the frame (STORY_HASHES: a change to the
renderer or the faces moves them, and is looked at before it is taken)."""

import hashlib
import os

import pytest

from runtime import host_canvas, moy_carts
from runtime import story_doc as D
from runtime import story_render as R

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOOK = os.path.join(ROOT, "docs", "media", "look_2026-10")

ZOOMS = {(1, 1): (1024, 600), (5, 8): (640, 375), (1, 2): (512, 300), (1, 4): (256, 150),
         (5, 16): (320, 188), (1, 8): (128, 75), (5, 64): (80, 47)}


def _words_slide():
    """The Brave Dragon's slide 2 without its picture: the bubble and the line."""
    return {"bg": 15, "transition": "fade", "objects": [
        D.shape_obj("bubble", 608, 64, 352, 180, 7, 0, 8, "Hello!", 64, 0, step=1),
        D.text_obj("Once upon a time...", 64, 416, 896, 72, 1)]}


def _picture():
    """A 1024 x 600 drawing: a dark blue sky, a green body, a 1 px white line."""
    w, h = 1024, 600
    px = bytearray([1]) * (w * h)
    for y in range(200, 420):
        for x in range(300, 700):
            if ((x - 500) * 3) ** 2 + ((y - 310) * 5) ** 2 < 600 ** 2:
                px[y * w + x] = 11
    for x in range(100, 900):
        px[100 * w + x] = 7
    return w, h, bytes(px)


def _images():
    w, h, pix = _picture()
    blobs = {"sky": moy_carts.encode_moyimg(w, h, pix),
             "sky_h": moy_carts.encode_moyimg(w // 2, h // 2, bytes(R.half(pix, w, h)))}
    return blobs


def _full_slide():
    s = _words_slide()
    s["objects"].insert(0, D.picture_obj("sky", 64, 64, 0.5))
    s["objects"].append(D.shape_obj("star", 120, 420, 88, 88, 10))
    s["objects"].append(D.shape_obj("arrow", 700, 300, 200, 60, None, 8, 8))
    s["objects"].append(D.shape_obj("ellipse", 820, 420, 120, 80, 12))
    return s


def _draw(slide, n, d, blobs=None):
    w, h = R.size(n, d)
    cv = host_canvas.make_system_canvas(w, h)
    blobs = blobs or {}
    R.Renderer(R.Images(blobs.get, 1 << 24)).draw(cv, slide, 0, 0, n, d)
    return cv


def _rgb(cv, x, y):
    i = 2 * (y * cv.w + x)
    v = (cv._buf[i] << 8) | cv._buf[i + 1]
    return (((v >> 11) & 31) * 255 // 31, ((v >> 5) & 63) * 255 // 63, (v & 31) * 255 // 31)


def test_the_zooms_are_the_looks_sizes():
    for (n, d), size in ZOOMS.items():
        assert R.size(n, d) == size, (n, d)


@pytest.mark.parametrize("zoom,mockup,top", [((1, 1), "storybook_present_desk.png", 0),
                                             ((5, 16), "storybook_present.png", 26)])
def test_the_slide_is_the_targets_slide(zoom, mockup, top):
    """Everything but the picture (the target's is its own art) and the
    slide's number: the edge shades may differ, the words and shapes not."""
    from PIL import Image
    n, d = zoom
    cv = _draw(_words_slide(), n, d)
    m = Image.open(os.path.join(LOOK, mockup)).convert("RGB").load()
    w, h = R.size(n, d)
    total = count = off = 0
    for y in range(h):
        for x in range(w):
            if (x < 600 * n // d and y < 380 * n // d) or (y > h - 50 * n // d
                                                           and x > w - 90 * n // d):
                continue
            a = _rgb(cv, x, y)
            b = m[x, y + top]
            diff = max(abs(p - q) for p, q in zip(a, b))
            total += diff
            count += 1
            off += diff > 64
    assert total / count < 8, total / count
    assert off / count < 0.05, off / count


# The frame's sha256 at each zoom (the renderer's and the faces' pixels).
STORY_HASHES = {
    (1, 1): "3a5630c9a7a12b8d", (5, 8): "0df48d7a4912c535", (1, 2): "155793294f2b3b6b",
    (1, 4): "dd3e3336321f781a", (5, 16): "bd5936408de26385", (1, 8): "7b3cf3d253fcc91e",
    (5, 64): "dc84be1540d1d979",
}


@pytest.mark.parametrize("zoom", sorted(ZOOMS))
def test_the_slide_at_each_zoom_is_its_pinned_pixels(zoom):
    cv = _draw(_full_slide(), zoom[0], zoom[1], _images())
    got = hashlib.sha256(bytes(cv._buf)).hexdigest()[:16]
    want = STORY_HASHES[zoom]
    assert want is not None and got == want, (
        "the slide at %d/%d drew %s (STORY_HASHES pins it): look at it before taking it"
        % (zoom[0], zoom[1], got))


def test_a_zoom_draws_the_same_layout_smaller():
    """The words break where they break at 1:1, and their ink scales with
    the zoom: the line's ink spans about half as far at 1/2."""
    def ink_span(cv, y0, y1):
        xs = [x for y in range(y0, y1) for x in range(cv.w)
              if _rgb(cv, x, y)[0] < 100 and _rgb(cv, x, y)[2] < 150]
        return min(xs), max(xs)
    big = ink_span(_draw(_words_slide(), 1, 1), 420, 500)
    half = ink_span(_draw(_words_slide(), 1, 2), 210, 250)
    assert abs(half[0] - big[0] // 2) <= 2 and abs(half[1] - big[1] // 2) <= 2


def test_a_long_word_breaks_at_the_box_edge_and_lines_follow():
    cv = host_canvas.make_system_canvas(64, 64)
    words = R.Words()
    lines = words.lines(cv, "Supercalifragilistic is long", "sans_bold", 96, 300)
    assert len(lines) >= 3
    assert all(w <= 300 * 64 for _l, w in lines)
    assert "".join(l for l, _w in lines).replace(" ", "") == "Supercalifragilisticislong"


def test_half_keeps_a_one_pixel_line():
    w, h, pix = _picture()
    hp = R.half(pix, w, h)
    assert any(hp[50 * (w // 2) + x] == 7 for x in range(50, 450))
    assert hp[0] == 1


def test_a_clear_picture_leaves_its_corner_colour_out():
    blobs = _images()
    s = {"bg": 8, "transition": "none", "objects": [D.picture_obj("sky", 0, 0, 1, True)]}
    cv = _draw(s, 1, 4, blobs)
    assert _rgb(cv, 4, 4) == _rgb(_draw({"bg": 8, "transition": "none", "objects": []},
                                        1, 4), 4, 4)
    s["objects"][0]["clear"] = False
    assert _rgb(_draw(s, 1, 4, blobs), 4, 4) != _rgb(cv, 4, 4)


def test_steps_hold_back_what_appears_on_a_tap():
    s = _words_slide()
    at = (int(784 / 4), int(130 / 4))           # inside the bubble
    shown = _draw(s, 1, 4)
    cv = host_canvas.make_system_canvas(256, 150)
    R.Renderer(R.Images(lambda n: None, 0)).draw(cv, s, 0, 0, 1, 4, step=0)
    assert _rgb(shown, *at) != _rgb(cv, *at)


def test_images_are_read_at_their_size_and_kept_within_the_budget():
    blobs = _images()
    loads = []

    def load(name):
        loads.append(name)
        return blobs.get(name)
    cv = host_canvas.make_system_canvas(8, 8)
    im = R.Images(load, 2 * 128 * 75 + 10)
    assert im.size("sky") == (1024, 600)
    a = im.get(cv, "sky", 128, 75)
    assert (a.w, a.h) == (128, 75) and len(a.rgb) == 2 * 128 * 75
    assert im.get(cv, "sky", 128, 75) is a
    im.get(cv, "sky", 64, 37)
    assert ("sky", 128, 75) not in im._pics, "the budget let the older size go"
    assert im.get(cv, "nope", 10, 10) is None

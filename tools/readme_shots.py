#!/usr/bin/env python3
"""The README's stills, rendered from the real shell on the host.

Each shot boots a fresh workstation over the seed carts (a throwaway store, so
the developer's own ~/.moybyte never shows), opens a cart in the Editor on one
tab, and on the desk restores the Editor to a window and presses Play, so the
cart runs in the Editor's Game pane beside the tab. The OS bar's clock and the
random seed are pinned, so a rerun on an unchanged shell draws the same pixels.

    .venv/bin/python tools/readme_shots.py            # all -> docs/media/readme/
    .venv/bin/python tools/readme_shots.py hero       # just the shots named

The desk shots are the browser console's 1024x600 desk at font scale 1 (what
firmware/web_runner/web_boot.py boots); the handheld ones are the T-Deck's
320x240, written at 2x nearest so they read beside the desk's.
"""

import argparse
import os
import random
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from runtime import host_app  # noqa: E402

OUT = os.path.join(ROOT, "docs", "media", "readme")
DT = 1 / 30
CLOCK = "18:31"

# Where the handheld's Code shot scrolls to: the first line that starts so
# (Star Catcher's frame update), past the opening comment the desk's shows.
CODE_FROM = "def _update("

# name -> (cart title, Editor tab, desk?, variant)
SHOTS = {
    "hero": ("Star Catcher", "code", True, "dark"),
    "paint": ("Star Catcher", "paint", True, "dark"),
    "blocks": ("Coin Quest", "blocks", True, "dark"),
    "handheld_code": ("Star Catcher", "code", False, "dark"),
    "handheld_paint": ("Star Catcher", "paint", False, "dark"),
}


def _frames(ws, n):
    for _ in range(n):
        if ws.pointer is not None:
            ws.pointer.visible = False
        ws.toast_until = 0
        ws.show_fps = False
        ws.mark_dirty()
        ws.frame(DT)


def _image(ws):
    """The glass as an RGB image (its RGB565 is big-endian)."""
    from PIL import Image
    cv = ws.glass_canvas
    buf = bytes(cv._buf)
    sw = bytearray(len(buf))
    sw[0::2], sw[1::2] = buf[1::2], buf[0::2]
    return Image.frombytes("RGB", (cv.w, cv.h), bytes(sw), "raw", "BGR;16")


def _select(ws, title):
    cart = next((c for c in ws.home.runnable_items() if c.get("title") == title), None)
    if cart is None or not ws.home.select_cart(cart):
        sys.exit("!! no seed cart titled %r on the home" % title)


def render(title, tab, desk, variant, carts):
    """One shot: the glass after `title` opens in the Editor on `tab`; on the
    desk, in a window with its cart playing in the Game pane."""
    random.seed(1)
    kw = (dict(sys_size=(1024, 600), font_scale=1, windowed=True,
               panel_diagonal_in=7.0) if desk else {})
    ws = host_app.build_workstation(carts, **kw)
    ws.look.set_theme("moy", persist=False, variant=variant)
    ws.bar_layer.clock_text = lambda: CLOCK
    ws.ach.mute = True
    _frames(ws, 3)
    _select(ws, title)
    ws.open_in_editor()
    if tab == "code":
        ws.text.set_menu_view("code")
    else:
        getattr(ws.editor_app, "open_" + tab)()
    if tab == "code" and not desk:
        ed = ws.editor
        ed.row = ed.top = next((i for i, ln in enumerate(ed.lines)
                                if ln.startswith(CODE_FROM)), 0)
    _frames(ws, 3)
    if desk:
        win = ws.wm.windows["menu"]
        if win.full():
            ws.wm.toggle_max(win)
        ws.defer(ws.text.leave_menu)
        _frames(ws, 3)
        if not ws.editor_app.playing():
            sys.exit("!! %s did not start playing beside the Editor" % title)
    _frames(ws, 60)
    img = _image(ws)
    if not desk:
        img = img.resize((img.width * 2, img.height * 2), 0)   # nearest
    return img


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("names", nargs="*", help="shots to make (default: all of %s)"
                    % ", ".join(SHOTS))
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    names = args.names or list(SHOTS)
    bad = [n for n in names if n not in SHOTS]
    if bad:
        ap.error("no such shot: %s" % ", ".join(bad))
    os.makedirs(args.out, exist_ok=True)
    for name in names:
        with tempfile.TemporaryDirectory() as tmp:
            img = render(*SHOTS[name], carts=os.path.join(tmp, "carts"))
        path = os.path.join(args.out, name + ".png")
        img.save(path, optimize=True)
        print("%s  %dx%d  %d bytes" % (path, img.width,
                                       img.height, os.path.getsize(path)))


if __name__ == "__main__":
    main()

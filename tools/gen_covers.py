"""Generate static cartridge COVER ART for the seed games (visual identity v1
Section 11.4): run each game cart headless through the real console for a few
seconds, grab the 320x240 game canvas, and write it to the cart source's
images/cover.moyimg. The Library shelf draws it full-bleed on the card; carts
without a cover keep the deterministic sprite/glyph fallback.

Run from the repo root:

    .venv/bin/python tools/gen_covers.py                # all seed games
    .venv/bin/python tools/gen_covers.py brick_siege    # just one

Covers are committed artifacts (authored once, replaceable by hand or by Paint
art); re-running the tool refreshes them. Bump each cart's manifest "version"
after (re)generating so already-seeded devices pick the new cover up (#47) --
this tool does that automatically when it writes a new cover.
"""

import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from runtime import host_app, host_canvas, moy_carts  # noqa: E402

FRAMES = 80           # ~2.5s of play: the scene develops, nobody has died yet
COVER_TYPES = ("game", "story")


def gen_cover(slug):
    """Run system_carts/<slug>.moy headless and write its cover.moyimg.
    Returns True when a cover was written."""
    src_dir = os.path.join(ROOT, "system_carts", slug + ".moy")
    manifest_path = os.path.join(src_dir, "manifest.json")
    with open(manifest_path) as f:
        manifest = json.load(f)
    if manifest.get("type") not in COVER_TYPES:
        print("skip (not a game):", slug)
        return False
    # A throwaway store seeded from the real system carts, so the run uses the
    # exact seed content (and never mutates the sources).
    tmp = tempfile.mkdtemp(prefix="covers-")
    try:
        ws = host_app.build_workstation(os.path.join(tmp, "carts"))
        target = None
        for i, it in enumerate(ws.launcher.items):
            p = it.get("path") or ""
            if p.endswith("/" + slug + ".moy") or p.endswith(os.sep + slug + ".moy"):
                target = i
                break
        if target is None:
            print("skip (not seeded on the launcher):", slug)
            return False
        ws.launcher.sel = target
        ws.launch_selected()
        if ws.screen != "desktop" or ws.cart_error:
            print("skip (did not start):", slug, ws.cart_error)
            return False
        # Console overlays must not bake into the artwork (#86: several shipped
        # covers carried the host mouse cursor; the fps readout does the same).
        ws.show_fps = False
        if ws.pointer is not None:
            ws.pointer.visible = False
        for _ in range(FRAMES):
            ws.ach.toast = None          # keep system toasts out of the artwork
            if ws.pointer is not None:
                ws.pointer.visible = False
            ws.frame(1 / 30)
        ws.ach.toast = None
        ws._dirty = True
        ws.frame(1 / 30)                 # one clean frame after the last toast clear
        if ws.cart_error:
            print("skip (crashed during capture):", slug, ws.cart_error)
            return False
        cv = ws.canvas                       # the fixed 320x240 GAME canvas
        # .moyimg is a MOY64 INDEX bitmap (1 byte/pixel); the canvas is RGB565.
        # indices_of is the exact reduction back -- see runtime/host_canvas.py.
        blob = moy_carts.encode_moyimg(cv.w, cv.h, host_canvas.indices_of(cv))
        img_dir = os.path.join(src_dir, "images")
        os.makedirs(img_dir, exist_ok=True)
        out = os.path.join(img_dir, moy_carts.COVER_IMAGE + moy_carts.IMAGE_EXT)
        with open(out, "w") as f:
            f.write(blob)
        # Bump the manifest version so seeded devices re-seed the cover (#47).
        manifest["version"] = int(manifest.get("version", 1)) + 1
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)
            f.write("\n")
        print("wrote", out, "(manifest version ->", manifest["version"], ")")
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    picks = sys.argv[1:]
    slugs = picks or sorted(
        n[:-4] for n in os.listdir(os.path.join(ROOT, "system_carts"))
        if n.endswith(".moy"))
    done = 0
    for slug in slugs:
        try:
            done += 1 if gen_cover(slug) else 0
        except Exception as exc:  # noqa: BLE001 - keep going per cart
            print("FAILED:", slug, exc)
    print(done, "covers written")


if __name__ == "__main__":
    main()

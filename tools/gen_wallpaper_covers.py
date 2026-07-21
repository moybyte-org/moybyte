#!/usr/bin/env python3
"""Bake each wallpaper system cart's rendered frame into images/cover.moyimg.

The Appearance monitor's DEVICE fallback draws the cover through the #66/#86
cover-thumb pipeline (a build without the host Canvas can't run the live
preview runner), and the Editor's project-picker cards use the same art -- so
every wallpaper cart should ship a cover that IS its rendered 320x240 frame.

Renders through the Wallpaper preview runner (the exact code path the live
monitor uses), advancing live scenes a few seconds so the frame looks lively,
then RLE-encodes the canvas. Writes only on content change, and bumps the
cart's manifest version (#47) so already-seeded devices re-seed the new cover.

Run from the repo root after changing any wallpaper cart:

    .venv/bin/python tools/gen_wallpaper_covers.py
"""

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime import host_app, moy_image  # noqa: E402

WARMUP_S = 4.0          # advance live scenes this far before the snapshot
DT = 1 / 30


def main():
    ws = host_app.build_workstation(str(Path(tempfile.mkdtemp()) / "carts"))
    changed = 0
    for folder in sorted(ROOT.glob("system_carts/*.moy")):
        man_path = folder / "manifest.json"
        man = json.loads(man_path.read_text(encoding="utf-8"))
        if man.get("type") != "wallpaper" or man.get("title") == "My Art":
            continue                    # My Art previews via the artwork path
        slug = folder.name[:-4]
        ws.select_wallpaper(slug, persist=False)
        wp = ws.wallpaper
        if not wp._ensure_preview():
            print(f"SKIP {slug}: preview runner unavailable")
            continue
        if wp._pv_update is not None:
            t = 0.0
            while t < WARMUP_S:
                wp._pv_update(DT)
                t += DT
        if wp._pv_restore is not None:
            wp._pv_restore()
        wp._pv_draw()
        pv = wp._pv_canvas
        rs = getattr(pv, "reset_state", None)
        if rs is not None:
            rs()
        blob = moy_image.encode_moyimg(pv.w, pv.h, bytes(pv.buf))
        out = folder / "images" / "cover.moyimg"
        if out.exists() and out.read_text(encoding="utf-8") == blob:
            print(f"ok   {slug}: cover unchanged")
            continue
        out.parent.mkdir(exist_ok=True)
        out.write_text(blob, encoding="utf-8")
        man["version"] = int(man.get("version", 1)) + 1
        man_path.write_text(json.dumps(man, indent=2) + "\n", encoding="utf-8")
        print(f"WROTE {slug}: cover.moyimg, version -> {man['version']}")
        changed += 1
    print(f"{changed} cover(s) updated")


if __name__ == "__main__":
    main()

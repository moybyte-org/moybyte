#!/usr/bin/env python3
"""Build the Moybyte website into _site/ -- stdlib only, no dependencies.

Mirrors the moy-spec site generator's rule: the CANONICAL things live where they
live, and this only assembles. The playable player is the real web runner build
(firmware/web_runner/dist), copied in under player/ -- so what a visitor plays is
the same bundle the repo ships, at the commit they are reading. The page's colours
come from the Moy theme's roles (native/moy_ui/themes/moy.json, SITE_ROLES), so the
site cannot drift from the system's own look.

    python3 site/build.py                  # -> _site/
    python3 site/build.py --out /tmp/x     # somewhere else
    python3 site/build.py --no-player      # skip the ~1.6 MB player copy
    python3 site/build.py --no-stills      # skip capturing the hero's stills
    python3 site/build.py --channel dev --out _site/dev   # the dev site

The hero shows a still of the running console until the visitor clicks it, and
the build captures that still from the player it ships (capture_stills: node 22
and Google Chrome). Build the player first, or there is nothing to embed:

    firmware/web_runner/build.sh           # production (frozen, no modules.json)
    firmware/web_runner/build.sh --stage-only   # dev (fast, ships modules.json)

The page also FLASHES a board over USB (site/flash.js, esptool-js over Web
Serial). The images it writes are CI builds pulled down beforehand:

    python3 tools/fetch_ci_firmware.py --release firmware-latest \
        --out dist/ci-firmware/stable      # -> stable/<board>/
    python3 tools/fetch_ci_firmware.py --release firmware-beta \
        --out dist/ci-firmware/beta        # the picker's other option

A single flat dist/ci-firmware/<board>/ is still read, as `stable`. With no
images present, the flash section simply says there is no current build.

Everything under _site/ is generated. Edit this file, not the output.
"""

import argparse
import datetime
import glob
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PLAYER_SRC = os.path.join(ROOT, "firmware", "web_runner", "dist")
VENDOR_SRC = os.path.join(HERE, "vendor")
FIRMWARE_SRC = os.path.join(ROOT, "dist", "ci-firmware")
sys.path.insert(0, ROOT)

# Which builds the flasher offers, in the order the picker shows them. Each is a
# separate GitHub release that tools/fetch_ci_firmware.py pulls with --release
# into its own subtree of FIRMWARE_SRC, because a browser CANNOT fetch a release
# asset (no CORS headers) -- every image the page can flash has to be baked into
# the site. That is also why this list stays short: each variant costs its own
# copy of every board's image, ~13MB a variant across four boards.
#
# Older VERSIONS are deliberately not baked. They live as assets on their `v*`
# tag, and the page reaches them the only way CORS allows: the visitor downloads
# the .bin and hands it back through the file picker (site/flash.js), which
# flashes it with the same offset and parameters as a baked one.
VARIANTS = (
    ("stable", "Latest", "firmware-latest",
     "the tested release, and what the stable OTA channel serves"),
    ("beta", "Dev", "firmware-beta",
     "built from every push to dev -- newer, and not board-tested"),
)

# The sites a channel publishes BESIDE the stable one (.github/workflows/
# pages.yml builds dev's tree into _site/dev/). Such a page says what it is in a
# banner, keeps search engines out, offers its own firmware variant first, and
# its player keeps every store under the channel's own OPFS directory
# (moy_store.channelNav): the two sites share an origin, and a dev store format
# must never touch the stable site's carts.
CHANNELS = {
    "dev": {"variant": "beta", "label": "Dev build",
            "note": "the latest dev, built from every push; it may be broken"},
}

# page_tail.js's channel line, which stamp_player rewrites for a channel's site.
CHANNEL_LINE = 'const MOY_CHANNEL = "";'


# The page's colour variables, each named for the role of the OS's Moy theme it
# takes (native/moy_ui/themes/moy.json: `colors` roles, and the `look` fields a
# mode sets). Night is the dark scheme, Day the light one, so the page follows
# the visitor's scheme the way the console follows its mode, and cannot drift
# from the theme: this reads the theme's own file.
SITE_ROLES = (
    ("bg", ("colors", "panel")),            # the desk's ground
    ("surface", ("colors", "surface")),     # a window's body
    ("raised", ("colors", "surface_alt")),  # a button's fill
    ("line", ("colors", "border")),         # hairlines: frames, dividers
    ("field", ("look", "field", "border")),  # a text field's outline, 3:1
    ("ink", ("colors", "ink")),
    ("body", ("colors", "ink")),
    ("muted", ("colors", "ink_dim")),
    ("link", ("colors", "accent")),
    ("accent", ("colors", "accent")),       # Moy's one accent: focus, current, primary
    ("ok", ("colors", "code_string")),
    ("wip", ("colors", "code_number")),
    ("warn", ("look", "danger_ink")),
    ("pri-ink", ("colors", "value_ink")),   # text on an accent fill
)


# Night's colours under a fixed name, for the one control that sits over the
# console (a dark scrim in either scheme) and so must not follow the page's.
NIGHT_ROLES = (
    ("night-bg", ("colors", "panel")),
    ("night-line", ("colors", "border")),
    ("night-ink", ("colors", "ink")),
    ("night-accent", ("colors", "accent")),
)


def moy_roles(mode, roles=SITE_ROLES):
    """The `:root` variable declarations for Moy's `mode` ("dark" is Night,
    "light" is Day), as CSS."""
    with open(os.path.join(ROOT, "native", "moy_ui", "themes", "moy.json")) as f:
        theme = json.load(f)
    out = []
    for var, (top, *path) in roles:
        node = theme[top][mode]
        for key in path:
            node = node[key]
        out.append("--%s:%s;" % (var, node))
    return " ".join(out)


def palette():
    """Moybyte's own 64 colours, as #rrggbb -- the site's palette IS MOY64."""
    from runtime.palette import MOY64
    return ["#%02x%02x%02x" % tuple(c) for c in MOY64]


# Both tabs name their tier EXPLICITLY. An empty query used to be the handheld
# console; since the desk became the player's default (#73/#175) it boots the
# 1024x600 desktop, so a blank `q` here silently made both tabs the same tier.
TIERS = [
    # id, label, sub-label, query string, aspect ratio
    ("desktop", "Desktop", "1024 &times; 600 &mdash; windowed, with the editors",
     "?desktop=1", "1024 / 600"),
    ("handheld", "Handheld", "320 &times; 240 &mdash; the T-Deck tier",
     "?handheld=1", "4 / 3"),
]


# The flashable boards. This table is the ONE place the page's flasher and the
# Makefile's cable flash have to agree, so each field is the browser's copy of a
# `make firmware-flash-*` argument -- change one, change the other:
#
#   tdeck    esptool --chip esp32s3 write_flash 0x0 <full-dio image>
#   p4       esptool --chip esp32p4 write_flash 0x2000 moybyte_p4.bin
#   guition_p4  esptool --chip esp32p4 write_flash 0x2000 moybyte_guition_p4.bin
#   guition  esptool --chip esp32s3 write_flash 0x0 moybyte_guition_s3.bin
#   zero     esptool --chip esp32s3 write_flash 0x0 moybyte_zero.bin
#
# Every one writes a MERGED image (bootloader + partition table + app) whose header
# already carries the flash mode/size/frequency the build baked in, which is why
# the flasher passes "keep" for all of them rather than re-deriving them here. The
# partition tail (the VFS, and on the T-Deck otadata) is not part of the image,
# so an ordinary flash leaves the board's own storage alone.
#
# `images` is a preference list: the first name present in the board's artifact
# folder is the one published.
#
# `beta`, on a board that has it, is why that board is not ready yet: plain text
# (the release notes reuse it as Markdown), with `#N` linked to the issue on the
# page. A ready board has no `beta` key.
#
# HOW THE BOARD IS RESET IS NOT DECLARED HERE. `reset` (into the ROM loader) and
# `after` (out of it) are hardware facts, and the board that has them is the one
# that writes them down: each `firmware/<board>/board.toml` `[flash]` block, the
# same declaration the cable flash reads. They are filled in below from there,
# because a premise re-stated in this file is one that can go stale without the
# board noticing -- this table asserted a TinyUSB CDC id for the Zero, and drew
# `no_reset` out of it, for a fortnight after that board moved to
# USB-Serial/JTAG (2026-08-30).
BOARDS = [
    {
        "id": "tdeck",
        "label": "LilyGO T-Deck Plus",
        "chip": "ESP32-S3",                 # what esptool-js must report
        "images": ("moybyte_tdeck.bin",),
        "offset": 0x0,
        "baud": 460800,
        "manual": None,                     # no reset to skip: see below
        "usb_otg": True,                    # native USB, for esptool-js's sake
        # Derives to (no_reset, None): this board declares `before = usb_reset`,
        # which esptool-js does not implement, so the page cannot drive its reset
        # line at either end and the human does both.
        "done": "Written. Press <b>RST</b> on the board to start it.",
        "prep": "This board has to be put into the loader by hand. <b>Hold the "
                "trackball in</b> while you power it on, then let go. Flash, pick "
                "its port, and when the write finishes <b>press RST</b>.",
        "erase": "Erase the whole chip first. Carts on an SD card survive it; "
                 "with no card they are on internal flash, and this deletes them "
                 "and their saves.",
        "cli": "make firmware-flash-tdeck-mainline PORT=/dev/ttyACM0",
    },
    {
        "id": "p4",
        "label": "Waveshare ESP32-P4 7B",
        "chip": "ESP32-P4",
        "images": ("moybyte_p4.bin",),
        "offset": 0x2000,
        "baud": 921600,
        "usb_otg": False,                   # a CH343 bridge, not native USB
        "done": "Done &mdash; the board is rebooting into this build.",
        "prep": "Plug into the board&rsquo;s USB-C debug port. It resets into "
                "the loader and back on its own.",
        "erase": "Erase the whole chip first. This board keeps its carts on "
                 "internal flash, so this deletes them and their saves.",
        # The escape hatch for a reset that will not take: hold BOOT (GPIO35 on
        # this board), tap RESET, and the browser skips its own reset entirely.
        "manual": "Skip the reset &mdash; I have put the board in download mode "
                  "myself (hold <b>BOOT</b>, tap <b>RESET</b>, release BOOT). Try "
                  "this if connecting fails.",
        "cli": "make firmware-flash-p4 PORT=/dev/ttyACM0",
    },
    {
        # The Guition JC8012P4A1C: the Waveshare's silicon over the P4's own
        # USB-Serial/JTAG, which esptool's default reset drives in and out.
        "id": "guition_p4",
        "label": "Guition JC8012P4A1C 10.1&Prime;",
        "chip": "ESP32-P4",
        "images": ("moybyte_guition_p4.bin",),
        "offset": 0x2000,
        "baud": 921600,
        "usb_otg": True,                    # native USB, like the Guition S3
        "done": "Done &mdash; the board is rebooting into this build.",
        "prep": "Plug into the board&rsquo;s USB-C port. It resets into the "
                "loader and back on its own.",
        "erase": "Erase the whole chip first. This board keeps its carts on "
                 "internal flash, so this deletes them and their saves.",
        "manual": "Skip the reset &mdash; I have put the board in download mode "
                  "myself (hold <b>BOOT</b>, tap <b>RST</b>, release BOOT). Try "
                  "this if connecting fails.",
        "cli": "make firmware-flash-guition-p4 PORT=/dev/ttyACM0",
        "beta": "Fast games make the whole panel blink for a frame (#220), and "
                "WiFi doesn't work on this board yet.",
    },
    {
        # The Guition JC3248W535 (#202, ported 2026-08-18): the third board,
        # with its own CI matrix row, so the release publisher (which reads
        # THIS table) has an image to hand the card.
        "id": "guition_s3",
        "label": "Guition JC3248W535 3.5&Prime;",
        "chip": "ESP32-S3",
        "images": ("moybyte_guition_s3.bin",),
        "offset": 0x0,
        "baud": 460800,
        "usb_otg": True,                    # works on this one (unlike the T-Deck)
        "done": "Done &mdash; the board is rebooting into this build.",
        "prep": "Plug into the board&rsquo;s USB-C port. It resets into the "
                "loader and back on its own.",
        "erase": "Erase the whole chip first. Carts on a TF card survive it; "
                 "with no card they are on internal flash, and this deletes them "
                 "and their saves.",
        "manual": "Skip the reset &mdash; I have put the board in download "
                  "mode myself (hold <b>BOOT</b>, tap <b>RST</b>, release "
                  "BOOT). Try this if connecting fails.",
        "cli": "make firmware-flash-guition-s3 PORT=/dev/ttyACM1",
    },
    {
        # The Zero (Seeed XIAO ESP32-S3, #41): a build target since 2026-08-29.
        # HEADLESS -- no screen, no carts running on it. It is the store the
        # browser console pairs with, so whoever flashes it is holding a board
        # there is nothing to look at afterwards.
        #
        # Derives to (default_reset, None). It took the USB-Serial/JTAG
        # promotion on 2026-08-30 and enumerates 303a:1001, which is the PID
        # esptool-js picks its JTAG reset path off, so the page drives it into
        # the loader exactly as it does the Guition. Coming back out is the half
        # it cannot do: this board declares `after = watchdog_reset` (proven
        # here; `hard_reset` is an RTS wiggle with no circuit behind it), and
        # esptool-js implements no such sequence -- so the card asks for a
        # replug.
        "id": "xiao_zero",
        "label": "Seeed XIAO ESP32-S3 (Zero)",
        "chip": "ESP32-S3",
        "images": ("moybyte_zero.bin",),
        "offset": 0x0,
        "baud": 460800,
        "usb_otg": True,                    # native USB, for esptool-js's sake
        "manual": "Skip the reset &mdash; I have put the board in download mode "
                  "myself (hold <b>BOOT</b> while you plug it in). Try this if "
                  "connecting fails.",
        "done": "Written. Unplug the board and plug it back in to start it.",
        "prep": "This board has no screen. It resets into the loader on its "
                "own, but when the write finishes <b>unplug it and plug it back "
                "in</b> to start it.",
        "erase": "Erase the whole chip first. This board keeps its carts on "
                 "internal flash, so this deletes them and their saves.",
        "cli": "make firmware-flash-zero PORT=/dev/ttyACM0",
    },
]

# What esptool-js can actually perform: two entry sequences and one exit. The
# declarations it cannot serve are real and are each a board's own measured
# fact -- the T-Deck's `before = usb_reset` (the only sequence that connects to
# its wedged USB-Serial/JTAG node) and the Zero's `after = watchdog_reset` (the
# only one that gets it out of the loader). Both are honoured by the cable
# flash and neither exists in the browser.
ESPTOOL_JS_BEFORE = ("default_reset", "no_reset")
ESPTOOL_JS_AFTER = ("hard_reset",)


def declared_flash():
    """{`[board] ota` id: that board's `[flash]` block}, read from the tree.

    Derived rather than listed so board N+1 is described by its own file the
    day it lands.
    """
    from tools import board_config
    out = {}
    for path in sorted(glob.glob(os.path.join(ROOT, "firmware", "*", "board.toml"))):
        cfg = board_config.load(os.path.dirname(path))
        ota = (cfg.get("board") or {}).get("ota")
        if ota and "flash" in cfg:
            out[ota] = cfg["flash"]
    return out


def reset_pair(flash):
    """(`reset`, `after`) for the page, from one board's `[flash]` block.

    The defaults are esptool's own, which is what the cable flash falls back to
    when a board declares neither. A board whose ENTRY sequence esptool-js
    cannot perform is not asked to reset on the way out either: it is the same
    line and the same peripheral, so a page that cannot drive it in cannot
    drive it out, and asking would log a failure for something that was never
    going to work.
    """
    if not flash:                        # no declaration to follow: touch nothing
        return "no_reset", None
    before = str(flash.get("before", "default_reset"))
    after = str(flash.get("after", "hard_reset"))
    if before not in ESPTOOL_JS_BEFORE:
        return "no_reset", None
    return before, (after if after in ESPTOOL_JS_AFTER else None)


_DECLARED = declared_flash()
for _board in BOARDS:
    # A card for a board that declares no cable flash at all gets the reading
    # that touches nothing -- the human does both ends -- rather than a guess.
    _board["reset"], _board["after"] = reset_pair(_DECLARED.get(_board["id"], {}))
del _board


# The page's CONTENT mirrors README.md's "What's in it" -- same tiles, same
# claims, same order. Keep them in step: a feature that only exists in one of
# them is a bug. Each id names a tile tools/make_feature_tiles.py draws.
FEATURES = [
    ("windows", "Windows and processes",
     "The launcher, the editor and your game are all processes under one window "
     "manager: fullscreen on a handheld, overlapping windows on a desktop board."),
    ("editors", "Editors on the device",
     "Code, blocks, sprites, maps, scenes and music, on the board itself. "
     "Autosave with undo, and a crash opens the code at the failing line."),
    ("apps", "Apps",
     "Paint, Files, Notes, Storybook, Calc and Settings. Drawings and documents "
     "go into a shared store that carts can read."),
    ("languages", "Python and Lua",
     "Write carts in either language. The verbs are the same in both, so a cart "
     "ports line by line."),
    ("multiplayer", "Local multiplayer",
     "Two consoles in the same room find each other over ESP-NOW and play one "
     "game on two screens. No router, no cables."),
    ("pico8", "PICO-8 import",
     "Drop a <code>.p8</code> or <code>.p8.png</code> cart on the browser "
     "console and it becomes a Lua cart &mdash; art, map, sound and code "
     "&mdash; that opens in the editors."),
    ("folders", "Carts are folders",
     "A manifest, a script, sprites, a map and sounds. There is no build step: "
     "a folder in the cart store is a cart on the launcher."),
    ("updates", "Updates",
     "Signed over-the-air updates on a stable and a beta channel. If a new image "
     "doesn&rsquo;t boot, the board goes back to the one before."),
    ("browser", "In the browser",
     "The same console runs in a tab and keeps your carts there. A board can also "
     "serve it over WiFi, so a phone or laptop edits that board&rsquo;s carts."),
    ("compiled", "Compiled carts",
     "C, C++ or Rust compiled to WebAssembly instead of Python or Lua. A board "
     "runs it natively for its own chip; the browser console runs it on the "
     "browser&rsquo;s own engine. Doom and Jet Teapot are compiled carts."),
    ("getcarts", "Get Carts",
     "The console&rsquo;s store installs carts other people publish, over WiFi "
     "on a board or in the browser, showing each cart&rsquo;s licence before it "
     "fetches. The catalogue is moybyte-org/carts."),
    ("yours", "Make it yours",
     "Wallpapers &mdash; a cart can be one, running live &mdash; window themes "
     "and the system&rsquo;s icons are all editable on the console."),
]
FEATURE_SRC = os.path.join(ROOT, "docs", "media", "features")
TILE_W = 256


def tile_frames(name):
    """Frames in a tile's mask sheet, read from the PNG header's width."""
    import struct
    with open(os.path.join(FEATURE_SRC, name + ".png"), "rb") as f:
        head = f.read(24)
    return struct.unpack(">I", head[16:20])[0] // TILE_W


TARGETS = [
    ("LilyGO T-Deck Plus", "ESP32-S3",
     "320&times;240 handheld with keyboard, trackball and touch."),
    ("Waveshare ESP32-P4 7B", "ESP32-P4",
     "7&Prime; 1024&times;600 touch screen, windowed desktop."),
    ("Guition JC8012P4A1C", "ESP32-P4",
     "10.1&Prime; 1280&times;800 touch screen, the same desktop as the "
     "Waveshare."),
    ("Guition JC3248W535", "ESP32-S3",
     "3.5&Prime; 480&times;320 touch screen."),
    ("Seeed XIAO ESP32-S3", "ESP32-S3",
     "No screen: it stores carts and serves the browser console over WiFi."),
    ("Browser", "WebAssembly",
     "MicroPython and the same C drawing code, compiled to wasm. It is what "
     "runs on this page."),
    ("PC simulator", "Python + C",
     "The same modules the firmware freezes, running on your computer."),
]


def moy_mark(pal, scale=3):
    """The Moy mascot as a PNG data URI, rendered from the system's OWN icon art
    (runtime/chrome.py's _ICON_ART["moy"], 16x16, hex chars = MOY64 indices).
    Read out of the source text rather than imported: chrome.py pulls in the whole
    surface stack, and this script must stay importable with nothing installed."""
    import re, struct, zlib
    src = open(os.path.join(ROOT, "runtime", "chrome.py"), encoding="utf-8").read()
    m = re.search(r'"moy":\s*\((.*?)\)\s*,', src, re.S)
    if not m:
        return ""
    rows = re.findall(r'"([.0-9a-f]{16})"', m.group(1))
    if len(rows) != 16:
        return ""
    w = h = 16 * scale
    px = bytearray()
    for y in range(h):
        px.append(0)                                   # PNG filter: none
        for x in range(w):
            ch = rows[y // scale][x // scale]
            if ch == ".":
                px += b"\x00\x00\x00\x00"
                continue
            r, g, b = [int(pal[int(ch, 16)][i:i + 2], 16) for i in (1, 3, 5)]
            px += bytes((r, g, b, 255))
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(px), 9))
           + chunk(b"IEND", b""))
    import base64
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def publish_image(board, folder, out, variant, vlabel):
    """Copy one board's image for one variant into the site. -> record or None.

    The record is what site/flash.js reads to flash it: where the file is, how
    big it should be, what it should hash to, and the board's own write
    parameters.
    """
    name = next((n for n in board["images"]
                 if os.path.exists(os.path.join(folder, n))), None)
    if not name:
        return None

    blob = open(os.path.join(folder, name), "rb").read()
    # Variant in the PATH, so two channels' images can sit side by side under
    # the same board without one overwriting the other.
    dest = os.path.join(out, "firmware", variant, board["id"])
    os.makedirs(dest, exist_ok=True)
    with open(os.path.join(dest, name), "wb") as f:
        f.write(blob)

    def sidecar(name):
        path = os.path.join(folder, name)
        if not os.path.exists(path):
            return {}
        try:
            return json.load(open(path, encoding="utf-8"))
        except ValueError:
            return {}                        # a corrupt sidecar is not fatal

    source = sidecar("source.json")
    # The OTA manifest, when the fetch came from a release: the only place the
    # human version string lives.
    ota = sidecar("manifest.json")
    return {
        "id": board["id"],
        "label": board["label"],
        "chip": board["chip"],
        "variant": variant,
        "variant_label": vlabel,
        "file": name,
        "url": "firmware/%s/%s/%s" % (variant, board["id"], name),
        "size": len(blob),
        "sha256": hashlib.sha256(blob).hexdigest(),
        "offset": board["offset"],
        "baud": board["baud"],
        "reset": board["reset"],
        "usb_otg": board["usb_otg"],
        "after": board["after"],
        "done": board["done"],
        # Provenance: the page states which build it is about to write, so
        # "the latest build" is checkable rather than a claim.
        "commit": source.get("commit", ""),
        "run_url": source.get("run_url", ""),
        "run_number": source.get("run_number"),
        "built": source.get("built", ""),
        # What the DEVICE will call this once it is running -- the same string
        # the update screen shows, so the page and the board agree.
        "version": ota.get("label") or "",
    }


def firmware(src, out, prefer=None):
    """Publish every available build of every board, and describe them.

    `src` is tools/fetch_ci_firmware.py's output. Two layouts are accepted:

        <src>/<variant>/<board>/...    one subtree per VARIANTS entry
        <src>/<board>/...              the older single-channel layout

    The flat one is read as `stable`, so a checkout that ran the fetch tool the
    old way still builds a working page with one choice in the picker.

    A board with nothing anywhere is not an error -- the firmware workflow is
    dispatched by hand, artifacts expire, and the page renders the gap honestly.

    Each card gets "builds" (every variant that had an image) and "fw" (the
    first of them, which is what the page defaults to and what the older
    single-build code paths still read). `prefer` names the variant that goes
    first when a board has it -- a channel's site defaults to its own build.
    """
    cards = []
    for board in BOARDS:
        builds = []
        for variant, vlabel, _tag, _note in VARIANTS:
            folder = os.path.join(src, variant, board["id"])
            if not os.path.isdir(folder):
                continue
            got = publish_image(board, folder, out, variant, vlabel)
            if got:
                builds.append(got)
        if not builds:
            # The pre-picker layout: no variant subtrees, board folders at the top.
            folder = os.path.join(src, board["id"])
            if os.path.isdir(folder):
                got = publish_image(board, folder, out, VARIANTS[0][0], VARIANTS[0][1])
                if got:
                    builds.append(got)
        builds.sort(key=lambda b: b["variant"] != prefer)
        cards.append(dict(board, builds=builds, fw=builds[0] if builds else None))
    return cards


def when(stamp):
    """2026-07-29T19:39:21Z -> 29 Jul 2026 (and anything odd -> as given)."""
    try:
        d = datetime.datetime.strptime(stamp[:10], "%Y-%m-%d")
    except (TypeError, ValueError):
        return stamp or "an unrecorded date"
    return "%d %s %d" % (d.day, d.strftime("%b"), d.year)


def size_mb(n):
    return "%.1f MB" % (n / 1048576.0)


def font_face():
    """The system's own font as the display face. site/petme128.woff2 is the
    petme128 8x8 glyph set (MicroPython, MIT -- THIRD_PARTY.md) rendered as a
    webfont; inlined so the page stays one self-contained file."""
    import base64
    blob = os.path.join(HERE, "petme128.woff2")
    if not os.path.exists(blob):
        return ""
    b64 = base64.b64encode(open(blob, "rb").read()).decode("ascii")
    return ("@font-face{font-family:'Petme128';font-display:swap;"
            "src:url(data:font/woff2;base64,%s) format('woff2')}" % b64)


# The at-a-glance list beside the hero. Dots are role colours (ok / wip / warn).
STATUS = [
    ("ok", "Five boards", "ESP32-S3 and ESP32-P4"),
    ("ok", "Editors", "on the device itself"),
    ("ok", "OTA updates", "stable and beta"),
    ("ok", "In the browser", "nothing to install"),
]

REPO = "https://github.com/moybyte-org/moybyte"


def flash_cards(cards):
    """The board cards for the flash section -- one per BOARDS entry."""
    out = []
    for c in cards:
        fw = c["fw"]
        li = ['<li class="board" data-board="%s"><h3>%s%s</h3><p class="chip">%s</p>'
              % (c["id"], c["label"],
                 ' <span class="beta">beta</span>' if c.get("beta") else "",
                 c["chip"])]
        if c.get("beta"):
            li.append('<p class="betanote"><b>Beta:</b> %s</p>'
                      % re.sub(r"#(\d+)", r'<a href="%s/issues/\1">#\1</a>' % REPO,
                               c["beta"]))
        if not fw:
            li.append('<p class="fwmeta">no published build</p>'
                      '<p>CI has not left a live image for this board &mdash; the '
                      'firmware workflow is dispatched by hand and its artifacts '
                      'expire. Build and flash it from a checkout:</p>'
                      '<pre>%s</pre>' % c["cli"])
            out.append("\n".join(li) + "</li>")
            continue
        builds = c["builds"]
        # The picker only earns its space when there is a choice to make.
        if len(builds) > 1:
            opts = "".join(
                '<option value="%s"%s>%s%s</option>'
                % (b["variant"], " selected" if i == 0 else "",
                   b["variant_label"],
                   " &mdash; %s" % b["version"] if b["version"] else "")
                for i, b in enumerate(builds))
            li.append('<label class="pick"><span>Build</span>'
                      '<select class="variant">%s</select></label>' % opts)
        # One meta line and one download link PER build, with the unselected ones
        # hidden. Toggling beats rewriting: the meta carries links, and building
        # those in JS would mean handing innerHTML strings to the page.
        for i, b in enumerate(builds):
            bits = ["Built " + when(b["built"])]
            if b["run_url"]:
                bits.append('<a href="%s">run%s</a>'
                            % (b["run_url"],
                               " #%s" % b["run_number"] if b["run_number"] else ""))
            if b["commit"]:
                bits.append('<a href="%s/commit/%s">%s</a>'
                            % (REPO, b["commit"], b["commit"][:7]))
            bits.append("%s &rarr; 0x%x" % (size_mb(b["size"]), b["offset"]))
            li.append('<p class="fwmeta" data-variant="%s"%s>%s</p>'
                      % (b["variant"], "" if i == 0 else " hidden",
                         " &middot; ".join(bits)))
        li.append("<p>%s</p>" % c["prep"])
        dls = "".join(
            '<a class="btn dl" data-variant="%s" href="%s"%s download>'
            'Download the .bin</a>' % (b["variant"], b["url"], "" if i == 0 else " hidden")
            for i, b in enumerate(builds))
        li.append('<p class="act">'
                  '<button class="btn pri go" type="button">Flash this board</button>'
                  '%s</p>' % dls)
        # An older VERSION is not baked into the site (CORS -- see VARIANTS), so
        # the way back to one is: download it from its tag, then hand the file
        # over here. Same offset, same parameters, same flasher.
        li.append('<details class="older"><summary>Flash a different version'
                  '</summary><p>Every release keeps its images: pick a version '
                  'from <a href="%s/releases">the releases</a>, download this '
                  'board\'s <code>%s-*.bin</code>, then choose it here.</p>'
                  '<label class="file"><input type="file" accept=".bin"></label>'
                  '</details>' % (REPO, c["id"]))
        li.append('<label class="erase"><input type="checkbox">'
                  '<span>%s</span></label>' % c["erase"])
        if c["manual"]:
            li.append('<label class="erase manual"><input type="checkbox">'
                      '<span>%s</span></label>' % c["manual"])
        li.append('<p class="state"></p><div class="prog" hidden><i></i></div>'
                  '<pre class="log" hidden></pre>')
        out.append("\n".join(li) + "</li>")
    return "\n".join("      " + line for line in "\n".join(out).split("\n"))


def provenance():
    """(release name, commit, commit date) of the tree this script is in, for a
    channel banner. Any of them can be "" (no git, a tarball)."""
    name = ""
    try:
        src = open(os.path.join(ROOT, "device", "moy_ota.py"), encoding="utf-8").read()
        m = re.search(r'^FIRMWARE_NAME = "([^"]*)"', src, re.M)
        name = m.group(1) if m else ""
    except OSError:
        pass
    try:
        commit, stamp = subprocess.run(
            ["git", "-C", ROOT, "log", "-1", "--format=%H %cI"], check=True,
            capture_output=True, text=True).stdout.split()
    except (OSError, subprocess.CalledProcessError, ValueError):
        commit, stamp = "", ""
    return name, commit, stamp


def channel_banner(channel):
    """The strip a channel's page opens with: which build this is, that it is
    not the release, and the way back to the stable site."""
    ch = CHANNELS[channel]
    name, commit, stamp = provenance()
    bits = ["<b>%s</b>" % ch["label"]]
    if name:
        bits.append("after %s" % name)
    if commit:
        bits.append('<a href="%s/commit/%s">%s</a>' % (REPO, commit, commit[:7]))
    if stamp:
        bits.append(when(stamp))
    return ('<div class="chbar" role="note"><div class="wrap">'
            '<span>%s &mdash; %s.</span>'
            '<a href="../">Stable site &rarr;</a></div></div>\n'
            % (" &middot; ".join(bits), ch["note"]))


# The script that runs the console in the hero; emitted only when there is a
# player to run.
CONSOLE_JS = r"""// THE CONSOLE. Nothing of it is loaded until the visitor starts it: the stage
// holds a still (captured from the built player at site-build time) and no
// iframe, so the wasm is not fetched. The start is a click on the still (a
// button, so Enter too) or on the hero's own button. Tabs own ONE iframe and
// swap its src, so only one wasm VM is ever live (two would mean two heaps and
// two frame loops competing for the main thread).
var stage = document.getElementById("stage");
var still = document.getElementById("still");
var tabBar = document.getElementById("tabs");
var expand = document.getElementById("expand");
var shrink = document.getElementById("shrink");
var tabs = [].slice.call(document.querySelectorAll(".tab:not(.exp)"));
// What the console is asked to start as: the player skips its own play-button
// (the visitor's click was this page's), and opens Star Catcher on its Code tab
// -- the picture the still shows -- in either tier. The colour scheme rides
// along so a first-run console wears Moy Night or Moy Day to match this page.
function startQuery() {
  var dark = window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches;
  return "&start=1&edit=moybyte.star_catcher.moy&tab=code&scheme=" + (dark ? "dark" : "light");
}
// The stage is the console's aspect ratio PLUS the height the player page needs
// besides its canvas (title, the saved-in-this-browser bar): the player reports
// that height as it lays out, so the canvas fills the stage's width and the
// player's document never outgrows the frame (no scrollbar, nothing cut off).
// Until it reports, the guess below holds the place.
var chrome = 90;
function sizeStage() {
  if (stage.classList.contains("big")) {
    stage.style.aspectRatio = "auto";
    stage.style.height = "";
    return;
  }
  var p = (stage.dataset.ar || "4 / 3").split("/");
  var ratio = parseFloat(p[0]) / parseFloat(p[1]);
  // Idle, the stage is the still alone; the player's chrome joins it at the start.
  var h = Math.ceil((stage.clientWidth - 2) / ratio) +
    (stage.classList.contains("idle") ? 0 : chrome) + 2;
  stage.style.aspectRatio = "auto";
  stage.style.height = h + "px";
}
window.addEventListener("message", function (e) {
  var f = stage.querySelector("iframe");
  if (!f || e.source !== f.contentWindow || !e.data) return;
  // The player's first live frame: the still has done its job.
  if (e.data.moyLive) stage.classList.add("live");
  if (typeof e.data.moyChrome !== "number") return;
  chrome = e.data.moyChrome;
  sizeStage();
});
window.addEventListener("resize", sizeStage);
function show(tab) {
  tabs.forEach(function (t) { t.classList.toggle("on", t === tab); });
  stage.dataset.ar = tab.dataset.ar;
  stage.classList.remove("idle");
  tabBar.hidden = false;
  // The still is the desktop's; the handheld has none to hold the place.
  if (tab !== tabs[0]) stage.classList.add("live");
  sizeStage();
  // Replace the IFRAME only -- the close button is a child of the stage too, and
  // clearing innerHTML (what this used to do) would take it with them.
  var old = stage.querySelector("iframe");
  if (old) old.parentNode.removeChild(old);
  var f = document.createElement("iframe");
  f.setAttribute("title", tab.querySelector("b").textContent + " tier");
  f.setAttribute("allow", "autoplay");
  f.setAttribute("allowfullscreen", "");
  f.src = "player/index.html" + tab.dataset.q + startQuery();
  stage.appendChild(f);
}
tabs.forEach(function (t) { t.addEventListener("click", function () { show(t); }); });
function start() {
  if (stage.classList.contains("idle")) show(tabs[0]);
}
still.addEventListener("click", start);
document.getElementById("go").addEventListener("click", function () {
  start();
  stage.scrollIntoView({ behavior: "smooth", block: "center" });
});
sizeStage();

// EXPAND. The class is what actually resizes the player (a fixed overlay, which
// every browser has); real fullscreen is requested on top where it exists, for
// the browser chrome. No manual resize event is needed: resizing the iframe
// element fires `resize` inside its own document, which is what the player's
// fit() listens to, so the console rescales to whatever it is given.
function fsEl() {
  return document.fullscreenElement || document.webkitFullscreenElement || null;
}
function big(on) {
  stage.classList.toggle("big", on);
  document.body.classList.toggle("noscroll", on);
  sizeStage();
}
expand.addEventListener("click", function () {
  big(true);
  var req = stage.requestFullscreen || stage.webkitRequestFullscreen;
  // iOS Safari has no element fullscreen; the overlay above is already the
  // whole viewport there, so a rejection changes nothing the user can see.
  if (req) { try { Promise.resolve(req.call(stage)).catch(function () {}); } catch (e) {} }
});
function collapse() {
  var exit = document.exitFullscreen || document.webkitExitFullscreen;
  if (fsEl() && exit) { try { exit.call(document); } catch (e) {} }
  big(false);
}
shrink.addEventListener("click", collapse);
// Esc in real fullscreen is handled by the browser, which then fires this --
// so the overlay comes down with it instead of stranding a fixed black box.
document.addEventListener("fullscreenchange", function () { if (!fsEl()) big(false); });
document.addEventListener("webkitfullscreenchange", function () { if (!fsEl()) big(false); });
document.addEventListener("keydown", function (e) {
  if (e.key === "Escape" && stage.classList.contains("big")) collapse();
});

"""


STILL_ALT = ("The Moybyte desktop with Star Catcher's code open in the editor")


def page(pal, has_player, cards, channel=None, stills=False):
    still_html = (
        '<picture><source media="(prefers-color-scheme: dark)" '
        'srcset="media/console-night.png"><img src="media/console-day.png" '
        'width="1024" height="600" alt="%s"></picture>' % STILL_ALT)
    tabs = "\n".join(
        '        <button class="tab%s" data-tier="%s" data-q="%s" data-ar="%s">'
        '<b>%s</b><span>%s</span></button>'
        % (" on" if i == 0 else "", tid, q, ar, label, sub)
        for i, (tid, label, sub, q, ar) in enumerate(TIERS))
    missing = "" if has_player else (
        '    <p class="warnbox">The player bundle is not built yet &mdash; run '
        '<code>firmware/web_runner/build.sh</code>, then <code>make site</code>.</p>\n')
    stage = (
        '    <div class="stage idle" id="stage" data-ar="1024 / 600" '
        'style="aspect-ratio:1024 / 600">\n'
        '      <button class="still" id="still" type="button" '
        'aria-label="Start the console">%s<span class="go"><i></i>'
        'Start the console</span></button>\n'
        '      <button class="shrink" id="shrink" type="button">Close &#10005;</button>\n'
        '    </div>' % (still_html if stills else '')) if has_player else ""
    status = "\n".join(
        '      <li><i class="%s"></i><b>%s</b> %s</li>' % (k, name, note)
        for k, name, note in STATUS)
    features = "\n".join(
        '      <li><details name="feat"><summary>'
        '<span class="art" aria-hidden="true" '
        'style="--n:%d;--src:url(media/features/%s.png)"></span>'
        '<h3>%s <i aria-hidden="true">+</i></h3></summary><p><span>%s</span></p></details></li>'
        % (tile_frames(tid), tid, title, desc) for tid, title, desc in FEATURES)
    targets = "\n".join(
        '      <li><h3>%s</h3><p class="chip">%s</p><p>%s</p></li>' % (t, chip, b)
        for t, chip, b in TARGETS)
    boards = flash_cards(cards)
    device_options = "\n".join(
        '      <option value="%s">%s%s</option>'
        % (c["id"], c["label"], " (beta)" if c.get("beta") else "") for c in cards)
    # One manifest entry per BOARD, carrying every build the picker offers.
    # The default build's fields stay at the top level so a reader that
    # predates the picker still finds what it expects.
    published = [dict(c["fw"], builds=c["builds"]) for c in cards if c["fw"]]
    # The hint and the vendored esptool-js only ship when there is something
    # to write.
    flash_hint = "" if not published else (
        '  <div class="hint">\n'
        '    <span>Your carts and saves are left alone unless you tick the erase box.</span>\n'
        '    <span><b>After an erase</b> the first boot takes about half a minute.</span>\n'
        '  </div>\n')
    flash_js = ""
    if published:
        flash_js = (
            '<script type="application/json" id="fw-manifest">%s</script>\n'
            '<script type="module" src="flash.js"></script>'
            % json.dumps({"boards": published}))
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAhUlEQVR42mNgGAW4wX8y8dB3ANig5rI5ZGFqOGTAHIBi8aHDx1HwyHPAgEfBcHAAydl0cDrg/8cXKJiQOCVRNLgdgC6PS3z4OYDaeOg5gJDDaB4FA+4AQgUM3dLAgDtg5OaCEeUAFIfUqQZTFZPSUBncDsCVHalh8aBxAKGuGbXUDw4HAAAJtsp8ecvLrQAAAABJRU5ErkJggg==">
<title>%(title_pre)smoybyte &mdash; a small operating system for ESP32 boards</title>
%(robots)s
<meta name="description" content="A small operating system for ESP32 boards. Its software is cartridges you can open, change and run on the board itself. Try it in the browser.">
<style>
/* Every colour below is a role of the OS's Moy theme, read from
   native/moy_ui/themes/moy.json: Night (dark) is the default, Day (light)
   follows the visitor's scheme. See SITE_ROLES in site/build.py. */
:root{%(roles_dark)s
  %(night)s
  --w:70rem;
  --sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
}
@media (prefers-color-scheme: light){
  :root{%(roles_light)s}
}
%(font)s
*{box-sizing:border-box}
[hidden]{display:none !important}
html{-webkit-text-size-adjust:100%%;scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--body);font:16px/1.62 var(--sans)}
.wrap{width:100%%;max-width:var(--w);margin:0 auto;padding:0 24px}
a{color:var(--link);text-decoration-thickness:1px;text-underline-offset:2px}
h1,h2,h3{color:var(--ink);line-height:1.25}
/* --- pixel-native display type: the system's own font ---------------------- */
.px{font-family:'Petme128',var(--mono);letter-spacing:.02em}
/* --- top bar --------------------------------------------------------------- */
nav{position:sticky;top:0;z-index:9;background:var(--bg);border-bottom:1px solid var(--line)}
nav .wrap{display:flex;align-items:center;gap:18px;height:52px}
nav .brand{font-size:19px;color:var(--ink);text-decoration:none}
nav .brand em{font-style:normal;color:var(--accent)}
nav .sp{flex:1}
nav a.l{color:var(--body);text-decoration:none;font-size:14px}
nav a.l:hover{color:var(--accent)}
/* Narrow: the section anchors are one scroll away anyway, and keeping them
   pushed the GitHub link off the edge. */
@media (max-width:640px){nav a.l:not(:last-of-type){display:none}}
/* --- hero ------------------------------------------------------------------ */
.hero{display:grid;grid-template-columns:1.3fr .7fr;gap:44px;
  align-items:start;padding:52px 0 8px}
@media (max-width:900px){.hero{grid-template-columns:1fr;gap:28px;padding-top:34px}}
.eyebrow{font:12px/1 var(--mono);letter-spacing:.16em;text-transform:uppercase;
  color:var(--muted);margin:0 0 14px}
h1{margin:0;font-size:clamp(24px,3.2vw,36px);line-height:1.35}
h1 em{font-style:normal;color:var(--accent)}
.lead{font-size:19px;color:var(--ink);margin:18px 0 0;max-width:36em}
.sub{margin:14px 0 0;max-width:38em}
.btns{display:flex;flex-wrap:wrap;gap:10px;margin:22px 0 0}
.btn{display:inline-block;padding:9px 16px;border:1px solid var(--line);
  background:var(--surface);color:var(--ink);text-decoration:none;font-size:15px}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn.pri{background:var(--accent);border-color:var(--accent);color:var(--pri-ink);font-weight:600}
.btn.pri:hover{filter:brightness(1.08);color:var(--pri-ink)}
/* --- the console in the hero ----------------------------------------------- */
.console{margin:40px 0 0}
.console .slead{margin:0 0 4px;max-width:none}
/* Before the click the stage holds a still of the real console (site/build.py
   captures it from the built player) and nothing else: no iframe, no wasm. The
   still is PIXEL ART, so it is shown crisp where it is near its native 1024
   and smoothed where a phone scales it well below. */
.still{position:absolute;left:0;top:0;width:100%%;aspect-ratio:1024 / 600;display:block;
  appearance:none;border:0;padding:0;margin:0;background:none;cursor:pointer;font:inherit}
.still:focus-visible{outline:2px solid var(--accent);outline-offset:-3px}
.still img{display:block;width:100%%;height:100%%;image-rendering:pixelated}
@media (max-width:1100px){.still img{image-rendering:auto}}
.stage.live .still,.stage:not(.idle) .go{display:none}
.go{position:absolute;left:50%%;top:50%%;transform:translate(-50%%,-50%%);
  display:flex;align-items:center;gap:10px;padding:12px 20px;font-size:16px;
  color:var(--night-ink);background:color-mix(in srgb,var(--night-bg) 82%%,transparent);
  border:1px solid var(--night-accent);pointer-events:none;white-space:nowrap}
.go i{width:0;height:0;border-top:8px solid transparent;border-bottom:8px solid transparent;
  border-left:13px solid var(--night-accent)}
.still:hover .go{background:var(--night-accent);color:var(--night-bg)}
.still:hover .go i{border-left-color:var(--night-bg)}
/* --- the mascot ------------------------------------------------------------ */
.moy{image-rendering:pixelated;vertical-align:-4px}
nav .moy{width:22px;height:22px;margin-right:9px}
/* --- status: a column beside the hero copy --------------------------------- */
.k{font:12px/1 var(--mono);letter-spacing:.16em;text-transform:uppercase;
  color:var(--muted);margin:0 0 12px}
.status{display:flex;flex-direction:column;gap:8px;list-style:none;padding:0;margin:0}
@media (max-width:900px){.status{flex-direction:row;flex-wrap:wrap}}
.status li{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--muted);
  background:var(--surface);border:1px solid var(--line);padding:6px 11px}
.status b{color:var(--ink);font-weight:600}
.status i{flex:0 0 7px;width:7px;height:7px;display:inline-block}
.status .ok{background:var(--ok)} .status .wip{background:var(--wip)}
.status .warn{background:var(--warn)}
/* --- sections -------------------------------------------------------------- */
section{padding:52px 0 0}
section > .wrap > h2{margin:0;font-size:clamp(22px,3vw,30px)}
.slead{margin:10px 0 0;max-width:60ch;color:var(--muted)}
/* --- the player ------------------------------------------------------------ */
.tabs{display:flex;gap:10px;flex-wrap:wrap;margin:20px 0 0}
.tab{appearance:none;cursor:pointer;text-align:left;font:inherit;padding:9px 15px;
  background:var(--surface);color:var(--muted);border:1px solid var(--line)}
.tab b{display:block;font-size:14px;color:var(--ink)}
.tab span{display:block;font:12px/1.5 var(--mono);color:var(--muted)}
.tab:hover{border-color:var(--link)}
.tab.on{border-color:var(--accent)}
.tab.on b{color:var(--accent)}
.stage{margin:12px 0 0;background:#000;border:1px solid var(--line);overflow:hidden;
  position:relative}
.stage iframe{display:block;width:100%%;height:100%%;border:0}
/* --- expand: the console filling the screen -------------------------------- */
/* Two mechanisms on purpose. The Fullscreen API is the good one, but Safari on
   iPhone does not implement it for anything but <video> -- and a phone is
   exactly where the inline player is too small to use. So the class below is
   the real sizing (a fixed overlay works everywhere), and fullscreen is asked
   for on top of it where it exists, which additionally hides the browser
   chrome. Either can end first, so the JS syncs both ways. */
.exp{margin-left:auto;align-self:center}
.stage.big{position:fixed;inset:0;z-index:60;margin:0;border:0;
  aspect-ratio:auto !important;background:#000}
body.noscroll{overflow:hidden}
.shrink{position:absolute;top:8px;right:8px;z-index:2;appearance:none;cursor:pointer;
  font:13px/1 var(--sans);padding:8px 11px;color:var(--night-ink);
  background:color-mix(in srgb,var(--night-bg) 72%%,transparent);
  border:1px solid var(--night-line)}
.shrink:hover{border-color:var(--night-accent);color:var(--night-accent)}
.stage:not(.big) .shrink{display:none}
/* Landscape phone: the OS bar sits at the very top of the console, so a button
   in the corner would cover its clock. Nudge it clear of the safe area. */
@supports (padding:env(safe-area-inset-top)){
  .stage.big .shrink{top:calc(8px + env(safe-area-inset-top));
                     right:calc(8px + env(safe-area-inset-right))}
}
.hint{display:flex;gap:16px;flex-wrap:wrap;justify-content:space-between;
  color:var(--muted);font-size:13px;margin:10px 0 0}
.chbar{background:var(--warn);color:var(--pri-ink);font-size:14px}
.chbar .wrap{display:flex;flex-wrap:wrap;gap:4px 18px;justify-content:space-between;padding:8px 24px}
.chbar a{color:inherit;font-weight:600}
.warnbox{color:var(--warn);border:1px solid var(--warn);padding:10px 14px;margin:12px 0 0}
/* --- card grids ------------------------------------------------------------ */
.cards{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));
  margin:24px 0 0;padding:0;list-style:none}
.cards li{background:var(--surface);border:1px solid var(--line);padding:16px 18px}
.cards h3{margin:0 0 7px;font-size:15px;color:var(--accent)}
.cards p{margin:0;font-size:14px;color:var(--body)}
/* --- feature tiles: one-colour mask sheets, stepped through by CSS ---------- */
/* Each sheet is drawn at its display size, so nothing is resampled, and its
   colour is the accent, so it follows the theme. A tapped tile shows its text
   over the art; the text lets taps through, so tapping again closes it. */
.feats{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));
  margin:24px 0 0;padding:0;list-style:none}
.feats li{background:var(--surface);border:1px solid var(--line)}
.feats details{position:relative;height:100%%}
.feats summary{list-style:none;cursor:pointer;display:block;padding:16px 16px 12px}
.feats summary::-webkit-details-marker{display:none}
.feats summary:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}
.feats .art{display:block;width:256px;height:192px;margin:0 auto;
  background:var(--accent);transition:opacity .15s;
  -webkit-mask-image:var(--src);mask-image:var(--src);
  -webkit-mask-repeat:no-repeat;mask-repeat:no-repeat;
  -webkit-mask-size:calc(256px * var(--n)) 192px;mask-size:calc(256px * var(--n)) 192px;
  -webkit-mask-position:0 0;mask-position:0 0;
  animation:tile calc(var(--n) * 100ms) steps(var(--n)) infinite}
@keyframes tile{to{-webkit-mask-position:calc(-256px * var(--n)) 0;
  mask-position:calc(-256px * var(--n)) 0}}
@media (prefers-reduced-motion:reduce){.feats .art{animation:none}}
.feats h3{margin:12px 0 0;font-size:15px;display:flex;justify-content:space-between;
  align-items:center}
.feats h3 i{font:600 16px/1 var(--mono);font-style:normal;color:var(--muted);
  transition:transform .15s}
.feats details[open] h3 i{transform:rotate(45deg)}
.feats details > p{position:absolute;left:20px;right:20px;top:16px;height:192px;margin:0;
  display:flex;align-items:center;font-size:15px;color:var(--ink);pointer-events:none}
.feats details[open] .art{opacity:.05}
.cards .chip{display:inline-block;margin:0 0 7px;padding:2px 8px;font:11px/1.5 var(--mono);
  letter-spacing:.08em;text-transform:uppercase;color:var(--muted);
  background:var(--bg);border:1px solid var(--line)}
/* --- the flasher ----------------------------------------------------------- */
/* One card per board: what CI built, how to get the board into the loader, and
   the button that writes it. Everything below the button is progress reporting,
   hidden until a flash starts. The device dropdown shows one card at a time;
   without script every card shows. */
.devpick{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:22px 0 0;
  font-size:14px;color:var(--muted)}
.devpick select{font:15px var(--sans);color:var(--ink);background:var(--surface);
  border:1px solid var(--field);padding:8px 10px;min-width:min(20rem,100%%)}
.cards.boards{grid-template-columns:minmax(0,40rem)}
.boards li{display:flex;flex-direction:column}
/* The column stretches its children, and a full-width chip reads as a field. */
.boards .chip{align-self:flex-start}
.boards h3 .beta{margin-left:6px;padding:1px 6px;vertical-align:2px;
  font:600 11px/1.5 var(--mono);letter-spacing:.08em;text-transform:uppercase;
  color:var(--wip);border:1px solid var(--wip)}
.betanote{margin:0 0 10px;font-size:13px;color:var(--wip)}
.betanote a{color:inherit}
.boards pre{margin:12px 0 0;font-size:12px}
.fwmeta{margin:0 0 9px;font:11px/1.7 var(--mono);color:var(--muted)}
.fwmeta a{color:var(--muted)}
/* The build picker: stable vs dev, when the site was built with both. */
.pick{display:flex;gap:8px;align-items:center;margin:0 0 9px;
  font-size:12px;color:var(--muted)}
.pick select{font:12px var(--mono);color:var(--ink);background:var(--bg);
  border:1px solid var(--field);padding:3px 6px;flex:1 1 auto}
/* Flashing a version the site does not carry -- folded away, because it is the
   uncommon path and it asks the visitor to go and fetch a file first. */
.older{margin:12px 0 0;font-size:12px;color:var(--muted)}
.older summary{cursor:pointer}
.older p{margin:8px 0 0}
.older input{margin:8px 0 0;font-size:11px;color:var(--muted);max-width:100%%}
.act{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0 0}
.act .btn{font-size:14px;padding:7px 13px}
button.btn{appearance:none;cursor:pointer;font-family:inherit}
button.btn:disabled{opacity:.45;cursor:default;filter:none}
.erase{display:flex;gap:8px;align-items:flex-start;margin:12px 0 0;
  font-size:12px;color:var(--muted);cursor:pointer}
.erase input{margin:3px 0 0;flex:0 0 auto}
.state{margin:11px 0 0;font-size:13px;color:var(--ink);min-height:1.3em}
.state.ok{color:var(--ok)} .state.warn{color:var(--warn)} .state.wip{color:var(--wip)}
.prog{height:6px;margin:9px 0 0;background:var(--bg);border:1px solid var(--line)}
.prog i{display:block;height:100%%;width:0;background:var(--accent);
  transition:width .12s linear}
.log{max-height:9.5em;overflow:auto;margin:9px 0 0;padding:8px 10px;
  white-space:pre-wrap;font:11px/1.55 var(--mono);color:var(--muted);
  background:var(--bg);border:1px solid var(--line)}
pre{background:var(--surface);border:1px solid var(--line);padding:16px 18px;
  overflow-x:auto;font:13px/1.7 var(--mono);color:var(--ink);margin:20px 0 0}
pre .c{color:var(--muted)}
code{font:.92em var(--mono);background:var(--surface);border:1px solid var(--line);padding:1px 5px}
footer{margin:64px 0 0;border-top:1px solid var(--line);padding:24px 0 44px;
  color:var(--muted);font-size:13px}
footer a{margin-right:4px}
</style>
</head>
<body>
%(chbar)s<nav><div class="wrap">
  <a class="brand px" href="#top"><img class="moy" src="%(mark)s" alt="">moy<em>byte</em></a>
  <span class="sp"></span>
  <a class="l" href="#console">Try it</a>
  <a class="l" href="#flash">Flash a board</a>
  <a class="l" href="#in">What's in it</a>
  <a class="l" href="#runs">Runs on</a>
  <a class="l" href="#build">Build</a>
  <a class="l" href="https://github.com/moybyte-org/moybyte">GitHub &#8599;</a>
</div></nav>

<div class="wrap" id="top">
  <div class="hero">
    <div>
      <p class="eyebrow">Source-available firmware &middot; FSL-1.1-MIT</p>
      <h1 class="px">A small <em>operating system</em> for ESP32 boards.</h1>
      <p class="lead">Its software is cartridges &mdash; games, wallpapers and
        tools &mdash; and you can open, change and run any of them on the board
        itself, with no computer attached.</p>
      <p class="sub">It runs on five off-the-shelf ESP32 boards, on a PC, and in
        the browser below.</p>
      <div class="btns">
        <button class="btn pri" id="go" type="button">Try it in the browser &#9656;</button>
        <a class="btn" href="https://github.com/moybyte-org/moybyte">Source</a>
        <a class="btn" href="https://github.com/moybyte-org/moy-spec">The cart spec</a>
      </div>
    </div>
    <aside>
      <p class="k">Where it stands</p>
      <ul class="status">
%(status)s
      </ul>
    </aside>
  </div>

  <div class="console" id="console">
    <p class="slead">The same system the boards run, compiled to WebAssembly.
      Click it to start.</p>
    <div class="tabs" id="tabs" hidden>
%(tabs)s
      <button class="tab exp" id="expand" type="button"><b>Expand &#8663;</b><span>fill the screen</span></button>
    </div>
%(stage)s
    <div class="hint">
      <span>Click the screen, then use the arrow keys, Z and X. Pick <b>Make</b> for
        the editors.</span>
      <span>Carts you make are kept in this browser.</span>
    </div>
%(missing)s  </div>
</div>

<section id="flash"><div class="wrap">
  <h2>Put it on a board</h2>
  <p class="slead">Pick your device, plug it in over USB, and flash the latest
    release from this page. Needs Chrome, Edge or Opera on a desktop; Firefox and
    Safari have no Web Serial.</p>
  <p class="warnbox" id="fw-nowebserial" hidden>This browser has no Web Serial, so
    the flash buttons are off. Download the image instead and write it with
    <code>esptool</code>, at the offset on its card.</p>
  <label class="devpick"><span>Your device</span>
    <select id="device">
      <option value="">Pick one&hellip;</option>
%(device_options)s
    </select></label>
  <ul class="cards boards">
%(boards)s
  </ul>
%(flash_hint)s</div></section>

<section id="in"><div class="wrap">
  <h2>What's in it</h2>
  <p class="slead">Tap a tile to read about it.</p>
  <ul class="feats">
%(features)s
  </ul>
</div></section>

<section id="runs"><div class="wrap">
  <h2>What it runs on</h2>
  <p class="slead">One codebase: each firmware build freezes the same modules the
    simulator and the browser run.</p>
  <ul class="cards">
%(targets)s
  </ul>
</div></section>

<section id="build"><div class="wrap">
  <h2>Build it</h2>
  <pre><span class="c"># the system on your PC</span>
make setup &amp;&amp; make test
.venv/bin/python tools/simulate_desktop.py

<span class="c"># firmware (downloads MicroPython and ESP-IDF on the first build)</span>
make firmware-build-tdeck-mainline
make firmware-flash-tdeck-mainline PORT=/dev/ttyACM0

<span class="c"># this page's player, from source</span>
firmware/web_runner/build.sh &amp;&amp; make site</pre>
  <footer>
    <a href="https://github.com/moybyte-org/moybyte">Source</a> &middot;
    <a href="https://github.com/moybyte-org/moy-spec">The cartridge spec (moy core 0.4)</a> &middot;
    <a href="https://github.com/moybyte-org/moybyte/blob/master/docs/moy_cart_api.md">Cart API</a> &middot;
    <a href="https://github.com/moybyte-org/moy-spec">Compiled carts</a> &middot;
    <a href="https://github.com/moybyte-org/carts">Get Carts</a> &middot;
    <a href="https://github.com/moybyte-org/moybyte/issues">Issues</a>
    <br><br>
    Source-available (FSL-1.1-MIT): free to run, modify, teach with, and to make
    and sell carts. Selling hardware built on Moybyte needs a commercial licence;
    each release becomes MIT two years after it is published. The page for kids
    and parents is <a href="https://moybyte.com">moybyte.com</a>.
  </footer>
</div></section>
<script>
%(console_js)s
// The flash section's device dropdown: one board card at a time. The browser
// keeps the choice across a reload, so apply whatever it restored.
var device = document.getElementById("device");
var boardCards = [].slice.call(document.querySelectorAll(".boards .board"));
var flashHint = document.querySelector("#flash .hint");
function pickDevice() {
  boardCards.forEach(function (c) { c.hidden = c.dataset.board !== device.value; });
  if (flashHint) flashHint.hidden = !device.value;
}
device.addEventListener("change", pickDevice);
pickDevice();
</script>
%(flash_js)s
</body>
</html>
""" % {
        "roles_dark": moy_roles("dark"), "roles_light": moy_roles("light"),
        "night": moy_roles("dark", NIGHT_ROLES), "font": font_face(), "tabs": tabs, "missing": missing,
        "status": status, "features": features, "mark": moy_mark(pal),
        "targets": targets, "boards": boards, "device_options": device_options, "flash_js": flash_js,
        "flash_hint": flash_hint, "stage": stage,
        "console_js": CONSOLE_JS if has_player else "",
        "title_pre": "[dev] " if channel else "",
        "robots": '<meta name="robots" content="noindex,nofollow">' if channel else "",
        "chbar": channel_banner(channel) if channel else "",
    }


# The hero's two stills: the console's home as the real browser build draws it
# for a first-time visitor, one per colour scheme the page follows (Moy Night
# under a dark scheme, Moy Day under a light one). They are CAPTURED on every
# site build, from the player bundle this build ships (so the picture cannot
# lag the console, nor the console's theme), by the same headless-Chrome driver
# the browser tests use (firmware/web_runner/browsershot.mjs: node 22 and
# Google Chrome). The console is started the way the hero starts it (Star
# Catcher open on its Code tab), and each capture checks the console itself
# says it is in that state and that variant, so a still of the wrong thing
# fails the build rather than shipping.
STILLS = (("night", "dark"), ("day", "light"))
STILL_QUERY = "?desktop=1&start=1&edit=moybyte.star_catcher.moy&tab=code"


def capture_stills(player, media):
    """Write media/console-night.png and media/console-day.png from the built
    player bundle at `player`. Exits the build, naming the cause, when they
    cannot be made: a stale or absent still is worse than a failed build."""
    runner = os.path.join(ROOT, "firmware", "web_runner")
    os.makedirs(media, exist_ok=True)
    if shutil.which("node") is None or shutil.which(
            os.environ.get("MOY_CHROME", "google-chrome")) is None:
        sys.exit("!! the hero's stills need node 22 and Google Chrome "
                 "(MOY_CHROME names another browser); install them, or build "
                 "the page without the stills: site/build.py --no-stills")
    for name, scheme in STILLS:
        with tempfile.TemporaryDirectory() as tmp:
            scenario = os.path.join(tmp, "still.json")
            with open(scenario, "w") as f:
                json.dump({"query": STILL_QUERY, "scheme": scheme,
                           "boot_ms": 60000, "steps": [
                    {"wait": 2500},
                    {"js": "window.__moyState().then(s => 'STILL ' + "
                           "JSON.stringify([s.edit.tab, s.theme.name, s.theme.variant]))"},
                    {"shot": "still"}]}, f)
            r = subprocess.run(
                ["node", "browsershot.mjs", scenario, tmp], cwd=runner,
                env=dict(os.environ, MOY_DIST=player), capture_output=True,
                text=True, timeout=300)
            m = re.search(r'STILL (\[.*?\])', r.stdout.replace('\\"', '"'))
            want = ["code", "moy", scheme]
            if r.returncode != 0 or not m or json.loads(m.group(1)) != want:
                sys.exit("!! capturing the %s still failed (wanted %s, the console "
                         "said %s):\n%s\n%s" % (name, want, m and m.group(1),
                                                r.stdout[-2500:], r.stderr[-500:]))
            shutil.copyfile(os.path.join(tmp, "still.png"),
                            os.path.join(media, "console-%s.png" % name))


def stamp_player(player, channel):
    """Make a copied player bundle a channel's: its page tells the worker the
    channel (so every store opens under the channel's OPFS directory) and keeps
    search engines out. -> False, with the bundle removed, when the page has no
    channel line to stamp: a channel's player that shared the stable site's
    storage is worse than the page's "no player" notice."""
    index = os.path.join(player, "index.html")
    with open(index, encoding="utf-8") as f:
        html = f.read()
    if html.count(CHANNEL_LINE) != 1 or html.count("<head>") < 1:
        print("!! %s has no single %r -- the %s site publishes without its "
              "player rather than share the stable site's storage"
              % (index, CHANNEL_LINE, channel))
        shutil.rmtree(player)
        return False
    html = html.replace(CHANNEL_LINE, 'const MOY_CHANNEL = "%s";' % channel)
    html = html.replace("<head>", '<head><meta name="robots" content="noindex,nofollow">', 1)
    with open(index, "w", encoding="utf-8") as f:
        f.write(html)
    # The bundle's precompressed twin must say the same thing (a host that
    # serves .gz beside the file would otherwise serve the stable page).
    if os.path.exists(index + ".gz"):
        with gzip.open(index + ".gz", "wb", compresslevel=9) as f:
            f.write(html.encode("utf-8"))
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "_site"))
    ap.add_argument("--no-player", action="store_true",
                    help="skip copying the player bundle (page only)")
    ap.add_argument("--channel", choices=sorted(CHANNELS),
                    help="publish a channel's site (its banner, its firmware "
                         "first, its own browser storage) instead of stable")
    ap.add_argument("--no-stills", action="store_true",
                    help="build the hero without its two stills of the console "
                         "(they are captured with node and Chrome)")
    ap.add_argument("--firmware", default=FIRMWARE_SRC,
                    help="CI firmware folder for the flasher "
                         "(tools/fetch_ci_firmware.py's output)")
    args = ap.parse_args()

    out = os.path.abspath(args.out)
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)

    has_player = False
    if not args.no_player:
        if os.path.isdir(PLAYER_SRC) and os.path.exists(
                os.path.join(PLAYER_SRC, "index.html")):
            shutil.copytree(PLAYER_SRC, os.path.join(out, "player"))
            has_player = (not args.channel
                          or stamp_player(os.path.join(out, "player"), args.channel))
        else:
            print("!! no player bundle at %s -- build it first" % PLAYER_SRC)

    # The hero's stills of the running console, captured from the player this
    # build ships (capture_stills).
    stills = has_player and not args.no_stills
    if stills:
        capture_stills(os.path.join(out, "player"), os.path.join(out, "media"))

    # The flasher: the CI images the page can write, the vendored esptool-js
    # that writes them, and a manifest that says what each one is. All three
    # only ship when there is at least one image to flash.
    cards = firmware(os.path.abspath(args.firmware), out,
                     prefer=CHANNELS[args.channel]["variant"] if args.channel else None)
    # One manifest entry per BOARD, carrying every build the picker offers.
    # The default build's fields stay at the top level so a reader that
    # predates the picker still finds what it expects.
    published = [dict(c["fw"], builds=c["builds"]) for c in cards if c["fw"]]
    if published:
        with open(os.path.join(out, "firmware", "manifest.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"boards": published}, f, indent=2, sort_keys=True)
            f.write("\n")
        shutil.copyfile(os.path.join(HERE, "flash.js"),
                        os.path.join(out, "flash.js"))
        shutil.copytree(VENDOR_SRC, os.path.join(out, "vendor"))
    else:
        print("!! no firmware images under %s -- the page will say so "
              "(build them with tools/fetch_ci_firmware.py)" % args.firmware)

    shutil.copytree(FEATURE_SRC, os.path.join(out, "media", "features"),
                    ignore=shutil.ignore_patterns("*.gif"))

    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
        f.write(page(palette(), has_player, cards, args.channel, stills))

    total = sum(os.path.getsize(os.path.join(d, n))
                for d, _, ns in os.walk(out) for n in ns)
    mode = "unknown"
    if has_player:
        mode = ("dev (ships modules.json)"
                if os.path.exists(os.path.join(out, "player", "modules.json"))
                else "production (frozen)")
    print("-> %s  (%.1f MB, player: %s, flashable: %s)"
          % (out, total / 1048576.0, mode,
             ", ".join(c["id"] for c in cards if c["fw"]) or "none"))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Build the Moybyte website into _site/ -- stdlib only, no dependencies.

Mirrors the moy-spec site generator's rule: the CANONICAL things live where they
live, and this only assembles. The playable player is the real web runner build
(firmware/web_runner/dist), copied in under player/ -- so what a visitor plays is
the same bundle the repo ships, at the commit they are reading. The page's colours
come from runtime/palette.py's MOY64, so the site cannot drift from the system's
own palette.

    python3 site/build.py                  # -> _site/
    python3 site/build.py --out /tmp/x     # somewhere else
    python3 site/build.py --no-player      # skip the ~1.6 MB player copy

Build the player first, or there is nothing to embed:

    firmware/web_runner/build.sh           # production (frozen, no modules.json)
    firmware/web_runner/build.sh --stage-only   # dev (fast, ships modules.json)

The page also FLASHES a board over USB (site/flash.js, esptool-js over Web
Serial). The images it writes are CI builds pulled down beforehand:

    python3 tools/fetch_ci_firmware.py --release firmware-latest \
        --out dist/ci-firmware/stable      # -> stable/{tdeck,p4,guition_s3}/
    python3 tools/fetch_ci_firmware.py --release firmware-beta \
        --out dist/ci-firmware/beta        # the picker's other option

A single flat dist/ci-firmware/<board>/ is still read, as `stable`. With no
images present, the flash section simply says there is no current build.

Everything under _site/ is generated. Edit this file, not the output.
"""

import argparse
import datetime
import glob
import hashlib
import json
import os
import shutil
import sys

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
#   guition  esptool --chip esp32s3 write_flash 0x0 moybyte_guition_s3.bin
#   zero     esptool --chip esp32s3 write_flash 0x0 moybyte_zero.bin
#
# All four write a MERGED image (bootloader + partition table + app) whose header
# already carries the flash mode/size/frequency the build baked in, which is why
# the flasher passes "keep" for all of them rather than re-deriving them here. The
# partition tail (the VFS, and on the T-Deck otadata) is not part of the image,
# so an ordinary flash leaves the board's own storage alone.
#
# `images` is a preference list: the first name present in the board's artifact
# folder is the one published.
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


# The page's CONTENT mirrors README.md's "What's in it" -- same claims, same
# order. Keep them in step: a feature that only exists in one of them is a bug.
FEATURES = [
    ("Shell",
     "A launcher, a Player and an Editor, all processes over a window manager. "
     "Apps are fullscreen on small screens; on the ESP32-P4 boards they are "
     "resizable windows, so a game can keep running next to its editor."),
    ("Editors on the device",
     "Seven tabs per project: config, blocks, code, sprites, map, scene, music. "
     "Autosave with undo and redo. Block programs compile to Python, and once "
     "you edit that Python by hand the blocks become read-only."),
    ("Apps",
     "Paint, Files, Notes, Storybook, Calc, Appearance, Settings. Drawings and "
     "documents go into a shared file store that carts can read."),
    ("Python and Lua",
     "One verb table, the same in both languages. On the boards, Lua carts run "
     "on Lua 5.4 with a heap separate from MicroPython&rsquo;s."),
    ("Graphics and sound",
     "64 indexed colours on a 320&times;240 cart screen everywhere. Drawing and "
     "the audio mixer are C on every target."),
    ("Storage",
     "Carts are plain folders, on an SD card where the board uses one and on "
     "internal flash otherwise. Every firmware image carries the built-in carts "
     "and writes them out on first boot."),
    ("Updates",
     "Signed over-the-air updates on a stable and a beta channel, with rollback "
     "if a new image doesn&rsquo;t boot."),
    ("In the browser",
     "The console on this page keeps your carts in this browser. Served by a "
     "board over WiFi, it edits that board&rsquo;s carts instead, behind a "
     "pairing PIN. Drop a <b>PICO-8</b> cart on it and it converts to a Lua "
     "cart you can open in the editors."),
]

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


def firmware(src, out):
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
    single-build code paths still read).
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
        li = ['<li class="board" data-board="%s"><h3>%s</h3><p class="chip">%s</p>'
              % (c["id"], c["label"], c["chip"])]
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


def page(pal, has_player, cards):
    tokens = "".join("--p%d:%s;" % (i, c) for i, c in enumerate(pal))
    tabs = "\n".join(
        '        <button class="tab%s" data-tier="%s" data-q="%s" data-ar="%s">'
        '<b>%s</b><span>%s</span></button>'
        % (" on" if i == 0 else "", tid, q, ar, label, sub)
        for i, (tid, label, sub, q, ar) in enumerate(TIERS))
    missing = "" if has_player else (
        '    <p class="warnbox">The player bundle is not built yet &mdash; run '
        '<code>firmware/web_runner/build.sh</code>, then <code>make site</code>.</p>\n')
    status = "\n".join(
        '      <li><i class="%s"></i><b>%s</b> %s</li>' % (k, name, note)
        for k, name, note in STATUS)
    features = "\n".join(
        "      <li><h3>%s</h3><p>%s</p></li>" % (t, b) for t, b in FEATURES)
    targets = "\n".join(
        '      <li><h3>%s</h3><p class="chip">%s</p><p>%s</p></li>' % (t, chip, b)
        for t, chip, b in TARGETS)
    boards = flash_cards(cards)
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
<title>moybyte &mdash; an operating system for ESP32 boards</title>
<meta name="description" content="An operating system for ESP32 boards. Its software is cartridges you can open, change and run on the board itself. Try it in the browser.">
<style>
/* Every colour below is MOY64, generated from runtime/palette.py -- the site
   cannot drift from the system's own palette. Roles are named so the light
   scheme differs only in the block that follows. */
:root{%(tokens)s
  --bg:#05070d; --surface:#090d19; --raised:#0d1325; --line:#141e3a;
  --ink:var(--p7); --body:var(--p6); --muted:#828899; --link:var(--p12);
  --accent:var(--p10); --ok:var(--p11); --wip:var(--p9); --warn:var(--p8);
  --pri-ink:var(--p1);
  --w:70rem;
  --sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
}
@media (prefers-color-scheme: light){
  :root{--bg:#fff9f5; --surface:#fff4ec; --raised:#fffcfa; --line:#b3a9a1;
        --ink:var(--p1); --body:#2f323a; --muted:#4d525e; --link:#175f8c;
        --accent:#74224c; --ok:#007446; --wip:#a86c00; --warn:#c2003b;
        --pri-ink:#fff9f5;}
}
%(font)s
*{box-sizing:border-box}
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
/* --- the machine, shown rather than tabulated ------------------------------ */
/* The recording is 1024 wide and it is PIXEL ART: shrink it and the 8px glyphs
   turn to mush, so the shot gets its own full-width band and is capped at
   exactly its native size (1024 + the bezel's 2x10 padding + borders). Below
   that width it has to scale, and a non-integer downscale looks better smoothed
   than snapped -- hence the image-rendering flip. */
.shot{margin:40px auto 0}
.screen{margin:0 auto;max-width:1046px}
.bezel{background:var(--surface);border:1px solid var(--line);padding:10px 10px 26px;
  position:relative}
.bezel:after{content:"";position:absolute;left:50%%;bottom:9px;transform:translateX(-50%%);
  width:34px;height:4px;background:var(--line)}
.bezel img{display:block;width:100%%;image-rendering:pixelated;border:1px solid var(--line)}
@media (max-width:1100px){.bezel img{image-rendering:auto}}
.screen figcaption{margin:11px 2px 0;color:var(--muted);font-size:13px;max-width:74ch}
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
  font:13px/1 var(--sans);padding:8px 11px;color:var(--ink);
  background:rgba(5,7,13,.72);border:1px solid var(--line)}
.shrink:hover{border-color:var(--accent);color:var(--accent)}
.stage:not(.big) .shrink{display:none}
/* Landscape phone: the OS bar sits at the very top of the console, so a button
   in the corner would cover its clock. Nudge it clear of the safe area. */
@supports (padding:env(safe-area-inset-top)){
  .stage.big .shrink{top:calc(8px + env(safe-area-inset-top));
                     right:calc(8px + env(safe-area-inset-right))}
}
.hint{display:flex;gap:16px;flex-wrap:wrap;justify-content:space-between;
  color:var(--muted);font-size:13px;margin:10px 0 0}
.warnbox{color:var(--warn);border:1px solid var(--warn);padding:10px 14px;margin:12px 0 0}
/* --- card grids ------------------------------------------------------------ */
.cards{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));
  margin:24px 0 0;padding:0;list-style:none}
.cards li{background:var(--surface);border:1px solid var(--line);padding:16px 18px}
.cards h3{margin:0 0 7px;font-size:15px;color:var(--accent)}
.cards p{margin:0;font-size:14px;color:var(--body)}
.cards .chip{display:inline-block;margin:0 0 7px;padding:2px 8px;font:11px/1.5 var(--mono);
  letter-spacing:.08em;text-transform:uppercase;color:var(--muted);
  background:var(--bg);border:1px solid var(--line)}
/* --- the flasher ----------------------------------------------------------- */
/* One card per board: what CI built, how to get the board into the loader, and
   the button that writes it. Everything below the button is progress reporting,
   hidden until a flash starts. */
.boards li{display:flex;flex-direction:column}
/* The column stretches its children, and a full-width chip reads as a field. */
.boards .chip{align-self:flex-start}
.boards pre{margin:12px 0 0;font-size:12px}
.fwmeta{margin:0 0 9px;font:11px/1.7 var(--mono);color:var(--muted)}
.fwmeta a{color:var(--muted)}
/* The build picker: stable vs dev, when the site was built with both. */
.pick{display:flex;gap:8px;align-items:center;margin:0 0 9px;
  font-size:12px;color:var(--muted)}
.pick select{font:12px var(--mono);color:var(--ink);background:var(--bg);
  border:1px solid var(--line);padding:3px 6px;flex:1 1 auto}
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
<nav><div class="wrap">
  <a class="brand px" href="#top"><img class="moy" src="%(mark)s" alt="">moy<em>byte</em></a>
  <span class="sp"></span>
  <a class="l" href="#try">Try it</a>
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
      <h1 class="px">An <em>operating system</em> for ESP32 boards.</h1>
      <p class="lead">Its software is cartridges &mdash; games, wallpapers and
        tools &mdash; and you can open, change and run any of them on the board
        itself, with no computer attached.</p>
      <p class="sub">It runs on five off-the-shelf ESP32 boards, on a PC, and in
        the browser below.</p>
      <div class="btns">
        <a class="btn pri" href="#try">Try it in the browser &#9656;</a>
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

  <figure class="screen shot">
    <div class="bezel"><img src="media/desktop.gif" alt="The windowed desktop at night: the code editor open on Star Catcher, the same cart running in a window beside it, and the sprite scale being changed from 4 to 8 in the source" loading="lazy"></div>
    <figcaption>Change <code>SPR_SCALE</code> in the code editor, press play, and
      the game in the next window comes back bigger. The wallpaper is a cart
      too.</figcaption>
  </figure>
</div>

<section id="try"><div class="wrap">
  <h2>Try it</h2>
  <p class="slead">The same system the boards run, compiled to WebAssembly.</p>
  <div class="tabs" id="tabs">
%(tabs)s
    <button class="tab exp" id="expand" type="button"><b>Expand &#8663;</b><span>fill the screen</span></button>
  </div>
  <div class="stage" id="stage">
    <button class="shrink" id="shrink" type="button">Close &#10005;</button>
  </div>
  <div class="hint">
    <span>Click the screen, then use the arrow keys, Z and X. Pick <b>Make</b> for
      the editors.</span>
    <span>Carts you make are kept in this browser.</span>
  </div>
%(missing)s</div></section>

<section id="flash"><div class="wrap">
  <h2>Put it on a board</h2>
  <p class="slead">Plug a board in over USB and flash the latest release from this
    page. Needs Chrome, Edge or Opera on a desktop; Firefox and Safari have no
    Web Serial.</p>
  <p class="warnbox" id="fw-nowebserial" hidden>This browser has no Web Serial, so
    the flash buttons are off. Download the image instead and write it with
    <code>esptool</code>, at the offset on its card.</p>
  <ul class="cards boards">
%(boards)s
  </ul>
%(flash_hint)s</div></section>

<section id="in"><div class="wrap">
  <h2>What's in it</h2>
  <ul class="cards">
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
    <a href="https://github.com/moybyte-org/moy-spec">The cartridge spec (moy core 0.3)</a> &middot;
    <a href="https://github.com/moybyte-org/moybyte/blob/master/docs/moy_cart_api.md">Cart API</a> &middot;
    <a href="https://github.com/moybyte-org/moybyte/issues">Issues</a>
    <br><br>
    Source-available (FSL-1.1-MIT): free to run, modify, teach with, and to make
    and sell carts. Selling hardware built on Moybyte needs a commercial licence;
    each release becomes MIT two years after it is published. The page for kids
    and parents is <a href="https://moybyte.com">moybyte.com</a>.
  </footer>
</div></section>
<script>
// Tabs own ONE iframe and swap its src, so only one wasm VM is ever live (two
// would mean two heaps and two frame loops competing for the main thread). The
// first tab loads immediately; switching reboots the system for that tier.
var stage = document.getElementById("stage");
var expand = document.getElementById("expand");
var shrink = document.getElementById("shrink");
var tabs = [].slice.call(document.querySelectorAll(".tab:not(.exp)"));
function show(tab) {
  tabs.forEach(function (t) { t.classList.toggle("on", t === tab); });
  stage.dataset.ar = tab.dataset.ar;
  if (!stage.classList.contains("big")) stage.style.aspectRatio = tab.dataset.ar;
  // Replace the IFRAME only -- the close button is a child of the stage too, and
  // clearing innerHTML (what this used to do) would take it with them.
  var old = stage.querySelector("iframe");
  if (old) old.parentNode.removeChild(old);
  var f = document.createElement("iframe");
  f.setAttribute("title", tab.querySelector("b").textContent + " tier");
  f.setAttribute("allow", "autoplay");
  f.setAttribute("allowfullscreen", "");
  f.src = "player/index.html" + tab.dataset.q;
  stage.appendChild(f);
}
tabs.forEach(function (t) { t.addEventListener("click", function () { show(t); }); });

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
  stage.style.aspectRatio = on ? "auto" : (stage.dataset.ar || "");
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
show(tabs[0]);
</script>
%(flash_js)s
</body>
</html>
""" % {
        "tokens": tokens, "font": font_face(), "tabs": tabs, "missing": missing,
        "status": status, "features": features, "mark": moy_mark(pal),
        "targets": targets, "boards": boards, "flash_js": flash_js,
        "flash_hint": flash_hint,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "_site"))
    ap.add_argument("--no-player", action="store_true",
                    help="skip copying the player bundle (page only)")
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
            has_player = True
        else:
            print("!! no player bundle at %s -- build it first" % PLAYER_SRC)

    # The hero's screen: a real recording of the system, committed because this
    # script must run with nothing installed (the Pages job has no Pillow, no
    # venv). Regenerate it with `make site-hero`, which is
    #   tools/make_site_gifs.py --windowed --scene code --wallpaper moy_night
    # -- the moy_night backdrop is the point: it is the brand colorway, so the
    # shot's own pixels are the same navy/yellow/cream the page is built from.
    # Frame 0 matters more than it looks: it IS the page's first paint, so the
    # recording has to open on a composed desk, not a boot wipe.
    # The flasher: the CI images the page can write, the vendored esptool-js
    # that writes them, and a manifest that says what each one is. All three
    # only ship when there is at least one image to flash.
    cards = firmware(os.path.abspath(args.firmware), out)
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

    gif = os.path.join(HERE, "hero.gif")
    if not os.path.exists(gif):
        gif = os.path.join(ROOT, "docs", "media", "desktop", "code.gif")
    if os.path.exists(gif):
        os.makedirs(os.path.join(out, "media"), exist_ok=True)
        shutil.copyfile(gif, os.path.join(out, "media", "desktop.gif"))

    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
        f.write(page(palette(), has_player, cards))

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

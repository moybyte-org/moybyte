#!/usr/bin/env python3
"""Build the Moybyte website into _site/ -- stdlib only, no dependencies.

Mirrors the moy-spec site generator's rule: the CANONICAL things live where they
live, and this only assembles. The playable player is the real web runner build
(firmware/web_runner/dist), copied in under player/ -- so what a visitor plays is
the same bundle the repo ships, at the commit they are reading. The page's ACCENTS
come from runtime/palette.py's MOY64, so the colour the site spends cannot drift
from the system's own palette.

Since 2026-09 the page is the PAPER scheme: a light document set in Host Grotesk
and JetBrains Mono (site/fonts/, OFL -- THIRD_PARTY.md), with the console's own
8x8 pixel face kept for the wordmark and nothing else. It replaced a dark,
MOY64-yellow, pixel-headline page that read as a game rather than as an operating
system. The <style> block's header records why each part of that is the way it
is; the short version is that the PIXELS ARE THE ARTEFACT, not the frame.

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
# THE 10.1" P4 (guition_p4) IS DELIBERATELY NOT HERE, though CI builds it and
# `declared_flash()` already reads its [flash] block. tests/test_site_flash.py's
# `test_a_reset_that_will_not_take_has_a_way_out` requires every board that
# ATTEMPTS an auto-reset to offer a BOOT-button way to skip it, because that
# reset is the part most likely to fail on someone else's machine -- and that
# board's README records it has no BOOT button. There is no honest `manual`
# string to write for it, so it cannot join the page's flasher until somebody
# with the hardware decides what its escape hatch is (a power-cycle, most
# likely, which is a different affordance and a change to that invariant).
# tools/fetch_ci_firmware.py does not carry it either, so there would be no
# image to serve regardless.
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
        "prep": "Its USB port is the ESP32-S3&rsquo;s own and auto-reset does not "
                "sync on it, so you move the board in and out of the loader by "
                "hand. There is no BOOT button &mdash; <b>the trackball click is "
                "GPIO0</b>: hold the trackball in while you power the board on, "
                "then let go, and it comes up in the ROM loader instead of the "
                "console. Flash, pick its port in the dialog, and when the write "
                "finishes <b>press RST</b> &mdash; it stays in the loader until "
                "you do.",
        "erase": "Erase the whole chip first. Only needed once, when moving a "
                 "board onto the OTA layout &mdash; carts live on the SD card, so "
                 "they survive either way.",
        "cli": "make firmware-flash-lilygo-micropython-full PORT=/dev/ttyACM0",
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
        "prep": "Plug into the board&rsquo;s USB-C debug port &mdash; the CH343 "
                "bridge resets it into the loader and back out again on its own, "
                "so there is nothing to hold or press.",
        "erase": "Erase the whole chip first. This board keeps its cartridges on "
                 "internal flash, so that deletes them along with their saves.",
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
        "prep": "Plug into the board&rsquo;s USB-C port. The S3&rsquo;s own "
                "USB-Serial/JTAG handles the reset into the loader and back.",
        "erase": "Erase the whole chip first. With a TF card in the slot the "
                 "cartridges live on the card and survive it; with no card they "
                 "are on internal flash, and this deletes them and their saves.",
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
        "prep": "This one has no screen &mdash; it is the cartridge store a "
                "browser console pairs with, so there is nothing to watch it do "
                "afterwards. Its USB-Serial/JTAG port resets it into the loader "
                "on its own, so there is nothing to hold going in. What it "
                "cannot do is come back out: when the write finishes "
                "<b>unplug the board and plug it back in</b> &mdash; it stays "
                "in the loader until you do.",
        "erase": "Erase the whole chip first. There is no card slot on this "
                 "board &mdash; the cartridges are on its internal flash, so "
                 "this deletes them and their saves. Coming from the old "
                 "MicroPython layout they go either way: this firmware keeps "
                 "the filing system somewhere else, so the board comes up with "
                 "an empty store and wants setting up again. Anything made in "
                 "the browser is still in the browser and syncs back on the "
                 "next visit.",
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
# order, same honesty. Keep them in step: the README is the model, this is the
# shop window, and a feature that only exists in one of them is a bug.
#
# (title, LEAD, detail). The lead is the one line on the page; the detail is the
# paragraph this list has always carried, folded behind a disclosure. The split
# exists because ten dense paragraphs in a grid is what made the page feel like
# a manual -- but a claim that is only in the README is still a bug, so the
# detail is FOLDED, never cut.
FEATURES = [
    ("The shell",
     "A launcher, a Player and an Editor, as ordinary processes over a window manager.",
     "A launcher, a Player and an Editor, all ordinary processes over a window "
     "manager. Two presentation tiers from one implementation: a fullscreen "
     "back-stack on the handheld, a windowed desktop on the 7&Prime; board where a "
     "playtest keeps running beside the editor you are typing in."),
    ("Editors on the device itself",
     "Seven tabs over one project. No save button and no dirty star.",
     "Seven tabs over one project &mdash; config, blocks, code, sprites, tilemap, "
     "scene, music. No save button and no dirty star: autosave on a typing pause "
     "and on every exit, with undo that walks edits and then whole commits."),
    ("Blocks that graduate",
     "Block programs compile to the same Python the code tab edits.",
     "Block programs compile to the same Python the code tab edits. Edit the code "
     "directly and the project graduates &mdash; the blocks go read-only rather "
     "than silently disagreeing with the source."),
    ("Apps",
     "Paint, Files, Storybook, Calc, Settings, Appearance, WiFi.",
     "Paint, Files, Storybook, Calc, Settings, Appearance, WiFi. "
     "Drawings, documents and tables land in a shared file layer that carts can "
     "read back. They sit on the launcher as carts; their code still lives in the "
     "shell rather than in an editable cart, which is the next piece of work."),
    ("Python and Lua",
     "One verb table, valid verbatim in both languages.",
     "One verb table, valid verbatim in both languages. On device, Lua carts run "
     "on a vendored Lua 5.4 VM whose heap lives outside MicroPython&rsquo;s GC and is "
     "freed wholesale at exit."),
    ("Graphics",
     "An indexed 64-colour palette end to end, every draw verb landing in a C kernel.",
     "An indexed 64-colour palette end to end, every draw verb landing in a C "
     "kernel on device. The 7&Prime; board composites through the SoC&rsquo;s hardware "
     "PPA with the DMA overlapping the next frame&rsquo;s input poll; scrolling "
     "shifts retained pixels instead of repainting them."),
    ("Sound",
     "A C mixer on the boards and in the browser, and PICO-8 sound imports.",
     "A C mixer on the boards and in the browser. A PICO-8 import carries "
     "eight waveforms, the effect column, four-channel patterns and SFX loop "
     "ranges."),
    ("Cartridges are folders",
     "A manifest, a script, a sheet, a tilemap, a sound bank. No build step.",
     "A manifest, a script, an indexed sheet, a tilemap, a sound bank. No build "
     "step, no per-device binary: copy a folder onto the card and it is on the "
     "launcher. Every board carries the whole set inside its firmware and writes "
     "them out on first boot, so a freshly flashed board is already full of "
     "things to play &mdash; with or without a card in the slot, since a board "
     "with an empty slot keeps its cartridges in its own flash and stays just as "
     "editable. Built-in carts re-seed by version and keep your saves and tuning."),
    ("Wireless",
     "WiFi setup while a game runs, and firmware updates over the air with rollback.",
     "WiFi setup lives in Settings, so it works while a game runs. Firmware "
     "updates over the air on two channels into an inactive OTA slot, with "
     "bootloader rollback if the new image does not come up. It is how the "
     "T-Deck and the P4 get their updates: download, install and rolling a bad "
     "image back have each run on the hardware. The screenless board takes the same updates through the same "
     "Settings screen, shown in a browser instead of on glass. The Guition's "
     "updater is wired and awaits its first release."),
    ("The console in a browser",
     "The same system compiles to WebAssembly &mdash; it is what runs on this page.",
     "The same system also compiles to WebAssembly &mdash; it is what runs on "
     "this page &mdash; and every board carries that build inside its firmware. "
     "Switch it on and the board hands the console to any phone or laptop on "
     "the same WiFi: it opens in a tab and draws every pixel itself rather "
     "than mirroring the board&rsquo;s screen. Where "
     "the page came from decides where its cartridges live. Opened from a "
     "board, it edits that board&rsquo;s cartridges and writes every change "
     "back to it, behind the pairing pin the device puts on screen. Opened "
     "from an ordinary web host &mdash; this page &mdash; the cartridges and "
     "drawings are kept in your browser and are still there on your next "
     "visit. A <code>.moy</code> file carries a cartridge in or out either "
     "way, and dropping a <b>PICO-8</b> cartridge on the page converts it and "
     "plays it &mdash; art, sound, map and the game&rsquo;s own code, which "
     "you can then open and read, because this console speaks that language "
     "too. When a board served the page, its Settings can update the board "
     "itself. There is "
     "nothing to sign into and nothing leaves the machine it was made on; the "
     "trade is that a browser is not a filing cabinet, so export the ones you "
     "would mind losing."),
]

# The SET-PIECES: a claim, and the footage that proves it, side by side. This is
# where the page gets to be alive -- the reference site's own life comes from
# five <video loop muted playsinline> beside its claims, not from scripted
# motion, and the equivalent we have is better than an illustration: these are
# recordings of the real console doing the real thing, produced by
# `make site-gifs` (tools/make_site_gifs.py drives the actual shell with real
# taps), so they track the system instead of ageing away from it.
#
# GIF ON PURPOSE, and it is not the lazy choice here. The console is an indexed
# 64-colour machine, so a GIF is EXACT -- same palette, lossless, every 8px
# glyph intact. An mp4 of this would chroma-subsample the text into mush, which
# is why the reference can use video for photography and we cannot.
#
# The handheld recordings (640x480) are deliberate: the hero already shows the
# windowed desktop, so these are the OTHER tier.
#
# TWO, and the third was CUT for a reason worth keeping: tap.gif opens on the
# identical "MAKE IT MINE" screen paint.gif opens on, and ends on the same pet
# running. Side by side they read as one recording shown twice. A loop has no
# poster frame to choose -- frame 0 is simply what a visitor sees first, and two
# set-pieces that start on the same pixels are worse than one. paint.gif keeps
# the claim because its arc (edit -> PLAY -> the edit is in the game) contains
# tap.gif's.
#
# The body copy describes the WHOLE ARC for the same reason: it is a loop, not a
# still, so a caption true only of the middle of it is false half the time.
SHOWCASE = [
    ("blocks.gif", "blocks &rarr; python", "Blocks that graduate",
     "A block is snapped into the program, and the CODE tab is opened on the "
     "same edit &mdash; compiled to the Python the code tab edits.",
     "The BLOCKS tab of the Editor: a block is dragged into the program, then "
     "the CODE tab shows the same program as Python"),
    ("paint.gif", "draw it, play it", "Editors on the device itself",
     "It opens on the cards a ten-year-old starts from, goes to the sprite "
     "tab, paints a smile onto the pet&rsquo;s tile, and presses PLAY &mdash; "
     "and the pet is wearing it in the running game. No save button and no "
     "export step.",
     "The Editor on Pixel Pet: the config cards, then the SPRITES tab where a "
     "smile is painted onto the pet's tile, then the game running with the "
     "edited sprite"),
]

TARGETS = [
    ("LilyGO T-Deck Plus", "ESP32-S3",
     "MicroPython firmware with native C modules for graphics, audio, SD and the "
     "Lua VM. Native 320&times;240, keyboard and trackball, cartridges on SD, "
     "over-the-air updates."),
    ("Waveshare ESP32-P4 7B", "ESP32-P4",
     "1024&times;600 over MIPI-DSI, mainline MicroPython with a vendored panel "
     "driver. The same system as a windowed desktop, with the game composite on "
     "the hardware PPA."),
    ("Guition JC3248W535", "ESP32-S3",
     "The ~$15 3.5&Prime; smart display, and the third board: a QSPI panel of "
     "its own, touch only, landscape 480&times;320, and cartridges on the TF "
     "card when there is one in the slot."),
    ("Guition JC8012P4A1C", "ESP32-P4",
     "The 10.1&Prime; one, and the second P4: 800&times;1280 portrait glass "
     "run as a landscape desk, rotated by the same hardware PPA that "
     "composites the game. Its port is the Waveshare&rsquo;s, over a shared "
     "silicon tier rather than a copy."),
    ("Seeed XIAO ESP32-S3", "ESP32-S3",
     "The odd one, and the smallest: no screen at all. A browser is its "
     "console &mdash; it serves that same WebAssembly build off its own flash "
     "&mdash; and the board is the cartridge store behind it, on whatever "
     "screen happens to be nearby. It arrives with the cartridges already on "
     "it, joins your WiFi from a form its own setup network hands your phone, "
     "and updates itself over the air like the others."),
    ("This browser tab", "WebAssembly",
     "The system compiled to wasm &mdash; MicroPython plus the same C drawing "
     "kernels the boards run. The page draws every pixel itself, and nothing "
     "is streamed from anywhere."),
    ("PC simulator", "pure Python",
     "The host reference and the fast dev loop. A pixel that moves here moves on "
     "glass: the firmware freezes copies of the same modules."),
]

# Being straight about the state is the point of this section. Update it when
# one of these lands -- a stale honesty list is worse than none.
ROUGH = [
    "All five boards are off-the-shelf dev boards. Bespoke hardware is roadmap, not shipped.",
    "Per-cart frame rates, the frame-budget model and every lever &mdash; including "
    "the ones built, measured and reverted &mdash; are tracked in public issues, "
    "not claimed here.",
    "The system apps are not editable yet, and USB-HID keyboard and audio on "
    "the P4 are unbuilt. Both are filed.",
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


# The page's two text faces, served from site/fonts/ (SIL OFL 1.1, and
# THIRD_PARTY.md records both). They are the 2026-09 paper scheme's actual
# content: a grotesk for everything that is a sentence, a monospace for
# everything that is a label. Latin subsets of the variable builds, ~52 KB the
# pair -- small enough to ship, too big to inline into every page load, so
# unlike Petme128 they are files.
#
# Petme128 stays INLINED, because it is one word (the wordmark) and a separate
# request for a wordmark that paints in the first frame is the wrong trade.
WEBFONTS = (
    # file, family, weight range
    ("host-grotesk-latin-var.woff2", "Host Grotesk", "300 800"),
    ("jetbrains-mono-latin-var.woff2", "JetBrains Mono", "400 700"),
)


def font_face():
    """The @font-face block: Petme128 inlined, the two text faces by URL.

    site/petme128.woff2 is the petme128 8x8 glyph set (MicroPython, MIT --
    THIRD_PARTY.md) rendered as a webfont. It sets the wordmark and nothing
    else since the paper scheme landed.
    """
    import base64
    out = []
    for name, family, wght in WEBFONTS:
        if os.path.exists(os.path.join(HERE, "fonts", name)):
            out.append("@font-face{font-family:'%s';font-style:normal;"
                       "font-weight:%s;font-display:swap;"
                       "src:url(fonts/%s) format('woff2')}"
                       % (family, wght, name))
    blob = os.path.join(HERE, "petme128.woff2")
    if os.path.exists(blob):
        b64 = base64.b64encode(open(blob, "rb").read()).decode("ascii")
        out.append("@font-face{font-family:'Petme128';font-display:swap;"
                   "src:url(data:font/woff2;base64,%s) format('woff2')}" % b64)
    return "".join(out)


# The at-a-glance status list: the honest state of the machine, as data. Dots are
# role colours (ok / wip / warn), so "what works" is readable before any prose.
STATUS = [
    ("ok", "The system", "boots on five ESP32 boards"),
    ("ok", "Editors", "on the device itself"),
    ("ok", "OTA updates", "hardware-confirmed"),
    ("wip", "System apps", "not editable yet"),
    # NOT "streams". The page runs the console itself -- the feature text below
    # is explicit that nothing is mirrored from the board's screen, and a
    # one-word summary contradicting it is the kind of small lie a shop window
    # gets believed on.
    ("ok", "Runs in a browser", "off the board's own flash"),
]

REPO = "https://github.com/moybyte-org/moybyte"

# The ticker strip between the shot and the player. Every item is a fact the
# page states again in full further down -- a strip that scrolls past is not
# where a claim gets to live on its own.
TICKER = ("ESP32-S3", "320 &times; 240", "ESP32-P4", "1024 &times; 600",
          "ESP32-S3", "480 &times; 320", "WebAssembly", "64 colours",
          "MicroPython", "Lua 5.4", "C draw kernels", "OTA + rollback",
          ".moy carts", "PICO-8 import")


def ticker():
    """The strip's markup: the items TWICE, because the keyframe travels -50%.

    Ornament, so the whole thing is aria-hidden -- a screen reader has no use
    for a list of numbers it will meet again as prose two screens down.
    """
    items = "".join("<li>%s</li>" % t for t in TICKER)
    return ('<div class="ticker" aria-hidden="true"><ul>%s%s</ul></div>'
            % (items, items))


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
        '      <li class="rise"><i class="%s"></i><b>%s</b> %s</li>' % (k, name, note)
        for k, name, note in STATUS)
    features = "\n".join(
        '      <li class="rise"><h3>%s</h3><p>%s</p>'
        '<details><summary>More</summary><p>%s</p></details></li>' % (t, lead, body)
        for t, lead, body in FEATURES)
    targets = "\n".join(
        '      <li class="rise"><h3>%s</h3><p class="chip">%s</p><p>%s</p></li>'
        % (t, chip, b)
        for t, chip, b in TARGETS)
    shows = "\n".join(
        '  <section class="show rise">\n'
        '    <figure><img src="media/%s" alt="%s" loading="lazy" decoding="async">'
        '</figure>\n'
        '    <div><p class="kick nb">%s</p><h3>%s</h3><p>%s</p></div>\n'
        '  </section>' % (gif, alt, kick, title, body)
        for gif, kick, title, body, alt in SHOWCASE)
    rough = "\n".join('      <li class="rise">%s</li>' % r for r in ROUGH)
    boards = flash_cards(cards)
    # One manifest entry per BOARD, carrying every build the picker offers.
    # The default build's fields stay at the top level so a reader that
    # predates the picker still finds what it expects.
    published = [dict(c["fw"], builds=c["builds"]) for c in cards if c["fw"]]
    # The flasher's 218 KB of vendored esptool-js is only worth loading when
    # there is something to write.
    # Only worth saying when there is a button to press. The "not proven on
    # glass yet" caveat that used to sit here was retired once a P4 was flashed
    # from this page end to end -- an honesty note that has stopped being true
    # is just a lie with good intentions.
    flash_hint = "" if not published else (
        '  <div class="hint">\n'
        '    <span>The same image at the same offset the cable flash uses. Your\n'
        '      cartridges and saves are left alone unless you tick the erase box.</span>\n'
        '    <span><b>After an erase</b> the board re-seeds its cartridges before the\n'
        '      screen comes up &mdash; give it half a minute.</span>\n'
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
<meta name="description" content="An operating system that turns an ESP32 board into a small general-purpose computer. The software is cartridges -- open any of them, change it, run it, on the board itself. Try it here, no install.">
<style>
/* ---------------------------------------------------------------------------
   THE PAPER SCHEME (2026-09).

   The page used to be the console: navy ground, MOY64 yellow, the 8x8 pixel
   face carrying the headlines. It read as a game, and the thing being sold is
   an operating system -- so the chrome is now a technical document and the
   PIXELS ARE THE ARTEFACT, not the frame. Paper ground, one grotesk, a
   monospace for anything that is a label rather than a sentence, hairline
   rules instead of filled boxes, and colour spent on roughly three words a
   screen. The pixel face survives in exactly one place: the wordmark.

   Light is now the DEFAULT and dark is the variant, which is the other half of
   the same decision -- a dark page with saturated accents is the gaming cue,
   whatever the accents are.

   The accents are still MOY64 (--p2 wine, --p14 pink, generated from
   runtime/palette.py), so the page cannot drift from the system's palette. The
   neutrals are not: paper, ink and rule are a document's greys and there is no
   64-colour game palette entry for "hairline". */
:root{%(tokens)s
  --paper:#fdfcfb; --sunk:#f5f3f0; --raised:#ffffff;
  --ink:#1b1b1d; --body:#4a4b52; --muted:#84858d;
  --line:#e4e1dc; --hair:#d6d2cc;
  --accent:var(--p2); --link:var(--p2);
  --ok:#0f7a52; --wip:#9a6a00; --warn:#b4143c;
  --pri-ink:var(--paper);
  --w:68rem;
  --sans:'Host Grotesk',ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,
         "Helvetica Neue",Arial,sans-serif;
  --mono:'JetBrains Mono',ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,
         "Liberation Mono",monospace;
}
@media (prefers-color-scheme: dark){
  :root{--paper:#121214; --sunk:#181819; --raised:#1b1b1e;
        --ink:#f3f1ee; --body:#b6b4b0; --muted:#83817e;
        --line:#2a2a2d; --hair:#333336;
        --accent:var(--p14); --link:var(--p14);
        --ok:#3fbe86; --wip:#d99a1f; --warn:#f0577f;
        --pri-ink:#121214;}
}
%(font)s
*{box-sizing:border-box}
[hidden]{display:none !important}
html{-webkit-text-size-adjust:100%%;scroll-behavior:smooth}
body{margin:0;background:var(--paper);color:var(--body);
  font:17px/1.62 var(--sans);font-weight:380;
  -webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
.wrap{width:100%%;max-width:var(--w);margin:0 auto;padding:0 28px}
a{color:var(--link);text-decoration-thickness:1px;text-underline-offset:3px;
  text-decoration-color:color-mix(in srgb,currentColor 35%%,transparent)}
a:hover{text-decoration-color:currentColor}
h1,h2,h3{color:var(--ink);font-weight:500;letter-spacing:-.02em;line-height:1.1;
  text-wrap:balance}
/* --- the one place the pixel face is still allowed --------------------------
   It is the mark, not the voice. Everything it used to set is grotesk now. */
.px{font-family:'Petme128',var(--mono);letter-spacing:.02em}
/* --- mono micro-labels ------------------------------------------------------
   Every label on the page is one of these: an eyebrow, a section kicker, a
   chip, a field name. Uppercase and tracked-out so a label is never mistaken
   for a sentence. */
.eyebrow,.k,.kick,.chip,.fwmeta,.tab span{font-family:var(--mono)}
.eyebrow,.k{font-size:11px;line-height:1;letter-spacing:.15em;
  text-transform:uppercase;color:var(--muted);margin:0 0 18px;font-weight:500}
/* --- top bar --------------------------------------------------------------- */
nav{position:sticky;top:0;z-index:9;background:color-mix(in srgb,var(--paper) 88%%,transparent);
  backdrop-filter:saturate(1.4) blur(10px);border-bottom:1px solid var(--line)}
nav .wrap{display:flex;align-items:center;gap:22px;height:60px}
nav .brand{font-size:18px;color:var(--ink);text-decoration:none;display:flex;
  align-items:center}
nav .brand em{font-style:normal;color:var(--accent)}
nav .sp{flex:1}
nav a.l{color:var(--muted);text-decoration:none;font:11px/1 var(--mono);
  font-weight:500;letter-spacing:.09em;text-transform:uppercase;
  transition:color .15s}
nav a.l:hover{color:var(--ink)}
@media (max-width:760px){nav a.l:not(:last-of-type){display:none}}
/* --- hero ------------------------------------------------------------------
   One column at desk width, because the status rail beside a 60px headline
   fought it for the eye. The rail now sits UNDER the buttons as a ruled strip,
   which is also where a reader looks for "state of the thing". */
.hero{padding:104px 0 0}
h1{margin:0;font-size:clamp(40px,5.6vw,72px);line-height:1.02;
  letter-spacing:-.035em;max-width:16ch}
h1 em{font-style:normal;color:var(--accent)}
.lead{font-size:21px;line-height:1.5;color:var(--ink);margin:30px 0 0;text-wrap:pretty;
  max-width:44ch;font-weight:380;letter-spacing:-.011em}
.sub{margin:20px 0 0;max-width:62ch;color:var(--body);font-size:16px}
.btns{display:flex;flex-wrap:wrap;gap:10px;margin:36px 0 0}
.btn{display:inline-block;padding:11px 19px;border:1px solid var(--hair);
  background:transparent;color:var(--ink);text-decoration:none;
  font:14px/1.3 var(--sans);font-weight:500;letter-spacing:-.005em;
  transition:border-color .15s,color .15s,background .15s}
.btn:hover{border-color:var(--ink)}
.btn.pri{background:var(--ink);border-color:var(--ink);color:var(--pri-ink)}
.btn.pri:hover{background:var(--accent);border-color:var(--accent);
  color:var(--paper)}
/* --- status: a ruled strip, one cell per claim ------------------------------ */
.rail{margin:56px 0 0;border-top:1px solid var(--ink)}
.rail .k{margin:14px 0 16px}
.status{display:grid;gap:0 40px;list-style:none;padding:0;margin:0;
  grid-template-columns:repeat(auto-fit,minmax(290px,1fr))}
.status li{display:flex;align-items:baseline;gap:9px;font-size:14px;
  color:var(--muted);padding:11px 0;border-top:1px solid var(--line)}
.status b{color:var(--ink);font-weight:500;white-space:nowrap}
.status i{flex:0 0 6px;width:6px;height:6px;display:inline-block;
  border-radius:50%%;transform:translateY(-1px)}
.status .ok{background:var(--ok)} .status .wip{background:var(--wip)}
.status .warn{background:var(--warn)}
/* --- the shot -------------------------------------------------------------- */
/* The recording is 1024 wide and it is PIXEL ART: shrink it and the 8px glyphs
   turn to mush, so the shot gets its own band and is capped at its native size.
   Below that width a non-integer downscale looks better smoothed than snapped
   -- hence the image-rendering flip. The bezel is now a hairline frame on the
   sunk ground; the moulded chin it used to draw was the handheld-console cue. */
.shot{margin:64px auto 0}
.screen{margin:0 auto;max-width:1046px}
.bezel{background:var(--sunk);border:1px solid var(--line);padding:10px}
.bezel img{display:block;width:100%%;image-rendering:pixelated}
@media (max-width:1100px){.bezel img{image-rendering:auto}}
.screen figcaption{margin:14px 2px 0;color:var(--muted);font-size:14px;
  max-width:72ch}
/* --- the mascot ------------------------------------------------------------ */
.moy{image-rendering:pixelated;vertical-align:-4px}
nav .moy{width:20px;height:20px;margin-right:10px}
/* --- sections ---------------------------------------------------------------
   Each opens on a rule and a lowercase mono kicker, so the page reads as a
   document with numbered parts rather than a stack of panels. */
section{padding:104px 0 0;scroll-margin-top:60px}
section > .wrap > h2{margin:0;font-size:clamp(30px,3.9vw,46px);
  letter-spacing:-.03em;max-width:18ch}
.kick{margin:0 0 20px;padding:18px 0 0;border-top:1px solid var(--ink);
  font-size:11px;line-height:1;letter-spacing:.15em;text-transform:uppercase;
  color:var(--accent);font-weight:500}
.slead{margin:20px 0 0;max-width:64ch;color:var(--body)}
/* --- the player ------------------------------------------------------------ */
.tabs{display:flex;gap:0;flex-wrap:wrap;margin:34px 0 0;
  border-bottom:1px solid var(--line)}
.tab{appearance:none;cursor:pointer;text-align:left;font:inherit;
  padding:12px 20px 13px;background:transparent;color:var(--muted);
  border:0;border-bottom:2px solid transparent;margin-bottom:-1px;
  transition:color .15s,border-color .15s}
.tab b{display:block;font-size:14px;font-weight:500;color:var(--muted)}
.tab span{display:block;font-size:11px;line-height:1.7;letter-spacing:.02em;
  color:var(--muted)}
.tab:hover b{color:var(--ink)}
.tab.on{border-bottom-color:var(--ink)}
.tab.on b{color:var(--ink)}
.stage{margin:20px 0 0;background:#000;border:1px solid var(--line);
  overflow:hidden;position:relative}
.stage iframe{display:block;width:100%%;height:100%%;border:0}
/* --- expand: the console filling the screen -------------------------------- */
/* Two mechanisms on purpose. The Fullscreen API is the good one, but Safari on
   iPhone does not implement it for anything but <video> -- and a phone is
   exactly where the inline player is too small to use. So the class below is
   the real sizing (a fixed overlay works everywhere), and fullscreen is asked
   for on top of it where it exists, which additionally hides the browser
   chrome. Either can end first, so the JS syncs both ways. */
.exp{margin-left:auto;align-self:center;border-bottom-color:transparent !important}
.stage.big{position:fixed;inset:0;z-index:60;margin:0;border:0;
  aspect-ratio:auto !important;background:#000}
body.noscroll{overflow:hidden}
.shrink{position:absolute;top:8px;right:8px;z-index:2;appearance:none;
  cursor:pointer;font:13px/1 var(--sans);padding:8px 12px;color:#f3f1ee;
  background:rgba(12,12,14,.74);border:1px solid rgba(243,241,238,.28)}
.shrink:hover{border-color:#f3f1ee}
.stage:not(.big) .shrink{display:none}
/* Landscape phone: the OS bar sits at the very top of the console, so a button
   in the corner would cover its clock. Nudge it clear of the safe area. */
@supports (padding:env(safe-area-inset-top)){
  .stage.big .shrink{top:calc(8px + env(safe-area-inset-top));
                     right:calc(8px + env(safe-area-inset-right))}
}
.hint{display:flex;gap:28px;flex-wrap:wrap;justify-content:space-between;
  color:var(--muted);font-size:13px;margin:16px 0 0}
.hint b{color:var(--body);font-weight:500}
.warnbox{color:var(--warn);border-left:2px solid var(--warn);padding:2px 0 2px 14px;
  margin:20px 0 0;font-size:14px}
/* --- card grids -------------------------------------------------------------
   No fill and no box: a hairline over each entry and air around it. Twelve
   bordered panels in a row was the other half of what read as an arcade. */
.cards{display:grid;gap:0 40px;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));
  margin:40px 0 0;padding:0;list-style:none}
.cards li{background:transparent;border:0;border-top:1px solid var(--line);
  padding:20px 0 26px}
.cards h3{margin:0 0 9px;font-size:16px;font-weight:500;color:var(--ink);
  letter-spacing:-.015em}
.cards p{margin:0;font-size:14.5px;line-height:1.6;color:var(--body)}
.cards .chip{display:inline-block;margin:0 0 10px;padding:0;
  font:11px/1.5 var(--mono);font-weight:500;letter-spacing:.11em;
  text-transform:uppercase;color:var(--muted);background:transparent;border:0}
.rough{margin:36px 0 0;padding:0;list-style:none;color:var(--body);font-size:16px;
  max-width:70ch}
.rough li{margin:0;padding:18px 0;border-top:1px solid var(--line)}
/* --- the flasher ----------------------------------------------------------- */
/* One card per board: what CI built, how to get the board into the loader, and
   the button that writes it. Everything below the button is progress reporting,
   hidden until a flash starts. */
.boards li{display:flex;flex-direction:column}
.boards .chip{align-self:flex-start}
.boards pre{margin:16px 0 0;font-size:12px;padding:12px 14px}
.cards .fwmeta{margin:0 0 10px;font-size:11px;line-height:1.8;color:var(--muted)}
.cards .fwmeta a{color:var(--muted)}
/* The build picker: stable vs dev, when the site was built with both. */
.pick{display:flex;gap:10px;align-items:center;margin:0 0 10px;
  font-size:12px;color:var(--muted)}
.pick select{font:12px var(--mono);color:var(--ink);background:var(--paper);
  border:1px solid var(--hair);padding:4px 7px;flex:1 1 auto;border-radius:0}
/* Flashing a version the site does not carry -- folded away, because it is the
   uncommon path and it asks the visitor to go and fetch a file first. */
.older{margin:16px 0 0;font-size:12px;color:var(--muted)}
.older summary{cursor:pointer}
.older p{margin:8px 0 0}
.older input{margin:8px 0 0;font-size:11px;color:var(--muted);max-width:100%%}
.act{display:flex;flex-wrap:wrap;gap:8px;margin:18px 0 0}
.act .btn{font-size:13px;padding:8px 15px}
button.btn{appearance:none;cursor:pointer;font-family:var(--sans)}
button.btn:disabled{opacity:.4;cursor:default}
.erase{display:flex;gap:9px;align-items:flex-start;margin:14px 0 0;
  font-size:12px;color:var(--muted);cursor:pointer}
.erase input{margin:3px 0 0;flex:0 0 auto}
.state{margin:13px 0 0;font-size:13px;color:var(--ink);min-height:1.3em}
.state.ok{color:var(--ok)} .state.warn{color:var(--warn)} .state.wip{color:var(--wip)}
.prog{height:3px;margin:11px 0 0;background:var(--line)}
.prog i{display:block;height:100%%;width:0;background:var(--ink);
  transition:width .12s linear}
.log{max-height:9.5em;overflow:auto;margin:11px 0 0;padding:10px 12px;
  white-space:pre-wrap;font:11px/1.6 var(--mono);color:var(--muted);
  background:var(--sunk);border:1px solid var(--line)}
pre{background:var(--sunk);border:1px solid var(--line);padding:20px 22px;
  overflow-x:auto;font:13px/1.85 var(--mono);color:var(--ink);margin:36px 0 0}
pre .c{color:var(--muted)}
code{font:.9em var(--mono);background:var(--sunk);border:1px solid var(--line);
  padding:1px 5px;color:var(--ink)}
/* --- the folded half of a card ----------------------------------------------
   The lead is the page; the paragraph behind this is the claim. See FEATURES. */
details{margin:12px 0 0}
details summary{cursor:pointer;list-style:none;display:inline-block;
  font:11px/1 var(--mono);font-weight:500;letter-spacing:.13em;
  text-transform:uppercase;color:var(--muted);padding:2px 0;
  border-bottom:1px solid var(--line);transition:color .15s,border-color .15s}
details summary::-webkit-details-marker{display:none}
details summary:hover{color:var(--ink);border-bottom-color:var(--ink)}
details[open] summary{color:var(--ink);border-bottom-color:transparent}
details p{margin:12px 0 0}
/* --- the set-pieces ---------------------------------------------------------
   A claim and the footage that proves it, side by side, alternating sides down
   the page. The footage is the wide half because it is the evidence; the words
   beside it are a caption for it, not a section of their own -- which is why
   the kicker here does not draw the section rule the others do. */
.shows{margin:104px auto 0}
.show{display:grid;grid-template-columns:1.1fr .9fr;gap:48px;align-items:center;
  padding:0;margin:0 0 72px}
.show:last-child{margin-bottom:0}
.show:nth-child(even) figure{order:2}
.show figure{margin:0;min-width:0}
.show img{display:block;width:100%%;border:1px solid var(--line);
  background:var(--sunk);image-rendering:pixelated}
.show h3{margin:0;font-size:clamp(23px,2.6vw,32px);letter-spacing:-.025em}
.show p:last-child{margin:14px 0 0;color:var(--body);max-width:44ch}
.kick.nb{border-top:0;padding:0;margin:0 0 14px}
@media (max-width:820px){
  .shows{margin:64px auto 0}
  .show{grid-template-columns:1fr;gap:20px;margin:0 0 56px}
  .show:nth-child(even) figure{order:0}
}
/* --- the ticker -------------------------------------------------------------
   A quiet strip of the machine's own numbers between the shot and the player.
   Every item is a fact the page asserts further down; it is ornament, so it is
   aria-hidden and it stops dead under reduced-motion. */
.ticker{overflow:hidden;margin:72px 0 0;padding:15px 0;
  border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.ticker ul{display:flex;list-style:none;margin:0;padding:0;width:max-content;
  animation:tick 46s linear infinite}
.ticker li{padding:0 28px;white-space:nowrap;font:11px/1 var(--mono);
  font-weight:500;letter-spacing:.19em;text-transform:uppercase;color:var(--muted)}
@keyframes tick{from{transform:translateX(0)}to{transform:translateX(-50%%)}}
/* --- motion -----------------------------------------------------------------
   One gesture, used everywhere: a short rise out of nothing, staggered down a
   group. It is OPT-IN -- the rules live inside no-preference, so a reader who
   asked for less motion gets a page that was never transformed in the first
   place, not one that animates and then snaps. Elements are visible by default
   and only hidden once the observer is known to be running (the .anim class the
   script sets), so no-JS never leaves the page blank. */
@media (prefers-reduced-motion: no-preference){
  .anim .rise{opacity:0;transform:translateY(16px)}
  .anim .rise{transition:opacity .66s cubic-bezier(.22,.61,.36,1),
                         transform .66s cubic-bezier(.22,.61,.36,1);
              transition-delay:var(--d,0ms)}
  .anim .rise.in{opacity:1;transform:none}
}
@media (prefers-reduced-motion: reduce){.ticker ul{animation:none}}
footer{margin:104px 0 0;border-top:1px solid var(--ink);padding:24px 0 72px;
  color:var(--muted);font-size:13px;max-width:78ch}
footer a{margin-right:4px}
/* --- narrow -----------------------------------------------------------------
   The paper scheme's air is sized for a desk. On a phone the same gaps read as
   the page having failed to load, so every 104px band comes down to 64. */
@media (max-width:760px){
  .hero{padding:64px 0 0}
  section{padding:64px 0 0}
  footer{margin:64px 0 0}
  h1{font-size:clamp(34px,10vw,44px);max-width:none}
  .lead{font-size:19px;margin:24px 0 0}
  .shot{margin:40px auto 0}
  .rail{margin:40px 0 0}
  .ticker{margin:44px 0 0}
}
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
    <p class="eyebrow rise">Source-available firmware &middot; FSL-1.1-MIT</p>
    <h1 class="rise">An <em>operating system</em> for ESP32 boards.</h1>
    <p class="lead rise">It turns the board into a small computer you can write software
      on. The software is cartridges &mdash; games, wallpapers, tools, whatever you
      make &mdash; and you open, change and run any of them on the board itself,
      with no host computer in the loop.</p>
    <p class="sub rise">It boots on five off-the-shelf boards today &mdash; four
      with screens, one without &mdash; and the same source tree is a PC simulator
      and the browser build below.</p>
    <div class="btns rise">
      <a class="btn pri" href="#try">Try it in the browser &#9656;</a>
      <a class="btn" href="https://github.com/moybyte-org/moybyte">Source</a>
      <a class="btn" href="https://github.com/moybyte-org/moy-spec">The cart spec</a>
    </div>
    <div class="rail rise">
      <p class="k">Where it stands</p>
      <ul class="status">
%(status)s
      </ul>
    </div>
  </div>

  <figure class="screen shot rise">
    <div class="bezel"><img src="media/desktop.gif" alt="The windowed desktop at night: the code editor open on Star Catcher, the same cart running in a window beside it, and the sprite scale being changed from 4 to 8 in the source" loading="lazy"></div>
    <figcaption>The desktop tier, unedited: change <code>SPR_SCALE</code> in the
      code tab and the cart running in the window next to it comes back twice the
      size. The wallpaper is a cartridge too &mdash; that is Moy, asleep.</figcaption>
  </figure>
</div>
%(ticker)s

<section class="rise" id="try"><div class="wrap">
  <p class="kick">run it</p>
  <h2>Try it, right here</h2>
  <p class="slead">The same code the firmware freezes, compiled to
    WebAssembly.</p>
  <div class="tabs" id="tabs">
%(tabs)s
    <button class="tab exp" id="expand" type="button"><b>Expand &#8663;</b><span>fill the screen</span></button>
  </div>
  <div class="stage" id="stage">
    <button class="shrink" id="shrink" type="button">Close &#10005;</button>
  </div>
  <div class="hint">
    <span>Click the screen, then arrow keys and Z / X. Pick <b>Make</b> for the editors.
      <b>Expand</b> fills the screen &mdash; the console resizes to fit it.</span>
    <span><b>Nothing is saved.</b> Reloading resets the machine.</span>
  </div>
%(missing)s</div></section>

<section class="rise" id="flash"><div class="wrap">
  <p class="kick">on hardware</p>
  <h2>Put it on a board</h2>
  <p class="slead">Plug a board in and write the current firmware to it from this
    page &mdash; no toolchain, no checkout. Each image is the one CI built, and the
    browser writes it over USB itself. Chrome, Edge or Opera on a desktop.</p>
  <p class="warnbox" id="fw-nowebserial" hidden>This browser has no Web Serial, so
    the flash buttons are off. Download the image instead and write it with
    <code>esptool</code>, at the offset on its card.</p>
  <ul class="cards boards">
%(boards)s
  </ul>
%(flash_hint)s</div></section>

<section class="rise" id="in"><div class="wrap">
  <p class="kick">the system</p>
  <h2>What's in it</h2>
  <p class="slead">The block editor is there for a ten-year-old. Underneath it is a
    MicroPython firmware with native C kernels, a Lua VM, OTA updates and a
    windowing shell. Where something is rough, it says so.</p>
  <ul class="cards">
%(features)s
  </ul>
</div></section>

<div class="wrap shows">
%(shows)s
</div>

<section class="rise" id="runs"><div class="wrap">
  <p class="kick">targets</p>
  <h2>What it runs on</h2>
  <p class="slead">Host and device are one codebase, not a port: each firmware build
    stages copies of the same modules and freezes them.</p>
  <ul class="cards">
%(targets)s
  </ul>
</div></section>

<section class="rise" id="rough"><div class="wrap">
  <p class="kick">honestly</p>
  <h2>Where it's rough</h2>
  <ul class="rough">
%(rough)s
  </ul>
</div></section>

<section class="rise" id="build"><div class="wrap">
  <p class="kick">from source</p>
  <h2>Build it</h2>
  <pre><span class="c"># the system on your PC</span>
make setup &amp;&amp; make test
.venv/bin/python tools/simulate_desktop.py

<span class="c"># firmware (needs the ESP-IDF toolchain)</span>
make firmware-build-lilygo-micropython
make firmware-flash-lilygo-micropython PORT=/dev/ttyACM0

<span class="c"># this page's player, from source</span>
firmware/web_runner/build.sh &amp;&amp; make site</pre>
  <footer>
    <a href="https://github.com/moybyte-org/moybyte">Source</a> &middot;
    <a href="https://github.com/moybyte-org/moy-spec">The cartridge spec (moy core 0.3)</a> &middot;
    <a href="https://github.com/moybyte-org/moybyte/blob/master/docs/moy_cart_api.md">Cart API</a> &middot;
    <a href="https://github.com/moybyte-org/moybyte/issues">Issues</a>
    <br><br>
    Source-available (FSL-1.1-MIT): free to run, modify, teach with, and to author
    and sell carts; selling hardware built on Moybyte needs a commercial licence
    until each release turns MIT two years after publication &mdash; the player on
    this page included. The kid- and parent-facing site is
    <a href="https://moybyte.com">moybyte.com</a>.
  </footer>
</div></section>
<script>
// MOTION. One gesture -- a short rise out of nothing -- staggered down whatever
// group the element belongs to. Three things make it safe to ship on a page
// whose job is to be read:
//
//   * It is OPT-IN twice over. The CSS lives inside `prefers-reduced-motion:
//     no-preference`, and it only bites once this script has set `.anim` on
//     <html>. With JS off, or before this runs, every .rise element is an
//     ordinary visible element -- the page can never be left blank by a
//     transition that did not arrive.
//   * The observer UNOBSERVES on first reveal. A hundred elements watched for
//     the life of the page is a scroll cost for an effect that happens once.
//   * The stagger is per GROUP, not per page, so a ten-card grid ripples in
//     over 200ms instead of the last card waiting on the first nine sections.
(function () {
  var q = function (s) { return [].slice.call(document.querySelectorAll(s)); };
  var rise = q(".rise");
  if (!rise.length || !window.IntersectionObserver ||
      !window.matchMedia || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  document.documentElement.classList.add("anim");

  // Position within the element's own group decides its delay. A section is its
  // own group; the cells of a grid share the parent that lays them out.
  rise.forEach(function (el) {
    var sibs = el.parentNode ? [].filter.call(el.parentNode.children, function (c) {
      return c.classList && c.classList.contains("rise");
    }) : [el];
    var i = sibs.indexOf(el);
    el.style.setProperty("--d", (i < 0 ? 0 : Math.min(i, 7) * 55) + "ms");
  });

  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      if (!e.isIntersecting) return;
      e.target.classList.add("in");
      io.unobserve(e.target);           // it only ever happens once
    });
  }, { rootMargin: "0px 0px -12%% 0px", threshold: 0.01 });

  // The hero is already on screen at first paint, so waiting for an
  // intersection callback would show it blank for a frame. Reveal it directly
  // instead, and observe everything else.
  var hero = q(".hero .rise");
  rise.forEach(function (el) { if (hero.indexOf(el) < 0) io.observe(el); });
  // The reflow is LOAD-BEARING, not a superstition. Adding `.anim` and `.in`
  // inside one turn coalesces into a single style recalc: the hidden state is
  // never a computed value, so there are no two values to interpolate between
  // and the hero snaps in at full opacity. Reading a layout property forces the
  // hidden state to resolve first, which is what gives the transition a start.
  // Measured: without it the hero's opacity is 1 for every frame of the page.
  void document.documentElement.offsetHeight;
  requestAnimationFrame(function () {
    hero.forEach(function (el) { el.classList.add("in"); });
  });
})();

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
        "ticker": ticker(), "shows": shows,
        "targets": targets, "rough": rough, "boards": boards, "flash_js": flash_js,
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

    # The text faces. Petme128 rides inside the CSS; these two do not.
    fonts = os.path.join(HERE, "fonts")
    if os.path.isdir(fonts):
        shutil.copytree(fonts, os.path.join(out, "fonts"))

    # The set-pieces. Committed recordings of the real shell, regenerated by
    # `make site-gifs` -- copied rather than referenced so _site/ stands alone.
    media = os.path.join(out, "media")
    for entry in SHOWCASE:
        src = os.path.join(ROOT, "docs", "media", entry[0])
        if os.path.exists(src):
            os.makedirs(media, exist_ok=True)
            shutil.copyfile(src, os.path.join(media, entry[0]))
        else:
            print("!! no footage at %s -- that set-piece will show a gap" % src)

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

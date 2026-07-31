#!/usr/bin/env python3
"""Build the Moybyte website into _site/ -- stdlib only, no dependencies.

Mirrors the moy-spec site generator's rule: the CANONICAL things live where they
live, and this only assembles. The playable player is the real web runner build
(firmware/web_runner/dist), copied in under player/ -- so what a visitor plays is
the same bundle the repo ships, at the commit they are reading. The page's colours
come from runtime/palette.py's MOY64, so the site cannot drift from the console's
own palette.

    python3 site/build.py                  # -> _site/
    python3 site/build.py --out /tmp/x     # somewhere else
    python3 site/build.py --no-player      # skip the ~1.6 MB player copy

Build the player first, or there is nothing to embed:

    firmware/web_runner/build.sh           # production (frozen, no modules.json)
    firmware/web_runner/build.sh --stage-only   # dev (fast, ships modules.json)

Everything under _site/ is generated. Edit this file, not the output.
"""

import argparse
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PLAYER_SRC = os.path.join(ROOT, "firmware", "web_runner", "dist")
sys.path.insert(0, ROOT)


def palette():
    """The console's own 64 colours, as #rrggbb -- the site's palette IS MOY64."""
    from runtime.palette import MOY64
    return ["#%02x%02x%02x" % tuple(c) for c in MOY64]


# MOY64 indices used for the page's design tokens (the "night" colorway the
# console itself ships): 0 black, 1 dark blue, 2 dark purple, 6 light grey,
# 7 white, 8 red, 10 yellow, 11 green, 12 blue, 14 pink.
INK, PANEL, EDGE, DIM, ACCENT, HILITE = 7, 1, 2, 6, 10, 12

TIERS = [
    # id, label, sub-label, query string, aspect ratio
    ("desktop", "Desktop", "1024 &times; 600 &mdash; windowed, with the editors",
     "?desktop=1", "1024 / 600"),
    ("handheld", "Handheld", "320 &times; 240 &mdash; the T-Deck tier",
     "", "4 / 3"),
]


# The page's CONTENT mirrors README.md's "What's in it" -- same claims, same
# order, same honesty. Keep them in step: the README is the model, this is the
# shop window, and a feature that only exists in one of them is a bug.
FEATURES = [
    ("The shell",
     "A launcher, a Player and an Editor, all ordinary processes over a window "
     "manager. Two presentation tiers from one implementation: a fullscreen "
     "back-stack on the handheld, a windowed desktop on the 7&Prime; board where a "
     "playtest keeps running beside the editor you are typing in."),
    ("Editors on the device itself",
     "Seven tabs over one project &mdash; config, blocks, code, sprites, tilemap, "
     "scene, music. No save button and no dirty star: autosave on a typing pause "
     "and on every exit, with undo that walks edits and then whole commits."),
    ("Blocks that graduate",
     "Block programs compile to the same Python the code tab edits. Edit the code "
     "directly and the project graduates &mdash; the blocks go read-only rather "
     "than silently disagreeing with the source."),
    ("Apps",
     "Paint, Files, Writer, Sheets, Storybook, Calc, Settings, Appearance, WiFi. "
     "Drawings, documents and tables land in a shared file layer that carts can "
     "read back. They sit on the launcher as carts; their code still lives in the "
     "shell rather than in an editable cart, which is the next piece of work."),
    ("Python and Lua",
     "One verb table, valid verbatim in both languages. On device, Lua carts run "
     "on a vendored Lua 5.4 VM whose heap lives outside MicroPython&rsquo;s GC and is "
     "freed wholesale at exit."),
    ("Graphics",
     "An indexed 64-colour palette end to end, every draw verb landing in a C "
     "kernel on device. The 7&Prime; board composites through the SoC&rsquo;s hardware "
     "PPA with the DMA overlapping the next frame&rsquo;s input poll; scrolling "
     "shifts retained pixels instead of repainting them."),
    ("Sound",
     "A C mixer on both boards and in the browser. PICO-8 imports are "
     "full-fidelity &mdash; eight waveforms, the effect column, four-channel "
     "patterns, SFX loop ranges."),
    ("Cartridges are folders",
     "A manifest, a script, an indexed sheet, a tilemap, a sound bank. No build "
     "step, no per-device binary: copy a folder onto the SD card and it is on the "
     "launcher. Built-in carts re-seed by version and keep your saves and tuning."),
    ("Wireless",
     "WiFi setup lives in Settings, so it works while a game runs. Firmware "
     "updates over the air on two channels into an inactive OTA slot, with "
     "bootloader rollback if the new image does not come up &mdash; confirmed on "
     "hardware end to end, but stale rather than proven right now: WiFi on the "
     "T-Deck is currently broken."),
    ("Streams itself to a browser",
     "The device can serve the running console over WiFi as draw commands rather "
     "than pixels &mdash; the same protocol this page&rsquo;s player speaks. Same "
     "caveat as the OTA path: it ran on a T-Deck, it has not been exercised "
     "lately."),
]

TARGETS = [
    ("LilyGO T-Deck Plus", "ESP32-S3",
     "MicroPython firmware with native C modules for graphics, audio, SD and the "
     "Lua VM. Native 320&times;240, keyboard and trackball, cartridges on SD, "
     "over-the-air updates."),
    ("Waveshare ESP32-P4 7B", "ESP32-P4",
     "1024&times;600 over MIPI-DSI, mainline MicroPython with a vendored panel "
     "driver. The same console as a windowed desktop, with the game composite on "
     "the hardware PPA."),
    ("This browser tab", "WebAssembly",
     "The console compiled to wasm. The page is the display &mdash; the console "
     "ships draw commands and never rasterizes a pixel itself."),
    ("PC simulator", "pure Python",
     "The host reference and the fast dev loop. A pixel that moves here moves on "
     "glass: the firmware freezes copies of the same modules."),
]

# Being straight about the state is the point of this section. Update it when
# one of these lands -- a stale honesty list is worse than none.
ROUGH = [
    "Both boards are off-the-shelf dev boards. Bespoke hardware is roadmap, not shipped.",
    "Per-cart frame rates, the frame-budget model and every lever &mdash; including "
    "the ones built, measured and reverted &mdash; are tracked in public issues, "
    "not claimed here.",
    "Open holes are filed rather than hidden: WiFi on the T-Deck is broken right "
    "now, the system apps are not editable yet, and USB-HID keyboard and audio on "
    "the P4 are unbuilt.",
]


def page(pal, has_player):
    p = lambda i: pal[i]  # noqa: E731
    bg = "#0b0f1a"        # the player page's own backdrop, so the embed is seamless
    tabs = "\n".join(
        '      <button class="tab%s" data-tier="%s" data-q="%s" data-ar="%s">'
        '<b>%s</b><span>%s</span></button>'
        % (" on" if i == 0 else "", tid, q, ar, label, sub)
        for i, (tid, label, sub, q, ar) in enumerate(TIERS))
    missing = "" if has_player else (
        '  <p class="warn">The player bundle is not built yet. Run '
        '<code>firmware/web_runner/build.sh</code> (or <code>--stage-only</code>) '
        'and re-run <code>site/build.py</code>.</p>\n')
    features = "\n".join(
        "    <li><h3>%s</h3><p>%s</p></li>" % (t, b) for t, b in FEATURES)
    targets = "\n".join(
        '    <li><h3>%s</h3><p class="chip">%s</p><p>%s</p></li>' % (t, chip, b)
        for t, chip, b in TARGETS)
    rough = "\n".join("    <li>%s</li>" % r for r in ROUGH)
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>moybyte &mdash; an operating system for ESP32 boards</title>
<meta name="description" content="An operating system that turns an ESP32 board into a small general-purpose computer. The software is cartridges -- open any of them, change it, run it, on the board itself. Try it here, no install.">
<style>
:root{
  --bg:%(bg)s; --panel:%(panel)s; --edge:%(edge)s; --ink:%(ink)s;
  --dim:%(dim)s; --accent:%(accent)s; --hilite:%(hilite)s;
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 var(--mono)}
body{display:flex;flex-direction:column;min-height:100vh}
a{color:var(--hilite)}
a:hover{color:var(--accent)}
.wrap{width:100%%;max-width:1120px;margin:0 auto;padding:0 20px}
header{padding:36px 0 22px}
.logo{font-size:34px;font-weight:700;letter-spacing:-.02em;color:var(--ink);margin:0}
.logo em{font-style:normal;color:var(--accent)}
.pitch{margin:12px 0 0;max-width:70ch;color:var(--ink)}
.pitch b{color:var(--accent);font-weight:700}
.meta{margin:14px 0 0;max-width:70ch;color:var(--dim);font-size:14px}
/* --- tier tabs ------------------------------------------------------------ */
.tabs{display:flex;gap:10px;flex-wrap:wrap;margin:26px 0 0}
.tab{appearance:none;cursor:pointer;text-align:left;font:inherit;
  background:var(--panel);color:var(--dim);border:1px solid var(--edge);
  border-radius:8px;padding:10px 16px;transition:.12s}
.tab b{display:block;font-size:15px;color:var(--ink)}
.tab span{display:block;font-size:12px;color:var(--dim)}
.tab:hover{border-color:var(--hilite)}
.tab.on{border-color:var(--accent);background:#12203a}
.tab.on b{color:var(--accent)}
/* --- the stage ------------------------------------------------------------ */
.stage{margin:14px 0 0;background:#000;border:1px solid var(--edge);
  border-radius:10px;overflow:hidden;position:relative}
.stage iframe{display:block;width:100%%;height:100%%;border:0}
.frame{width:100%%}
.hint{display:flex;gap:14px;flex-wrap:wrap;justify-content:space-between;
  color:var(--dim);font-size:13px;margin:10px 0 0}
.warn{color:%(warn)s;border:1px solid %(warn)s;border-radius:8px;padding:10px 14px}
code{background:var(--panel);border:1px solid var(--edge);border-radius:4px;
  padding:1px 5px;font-size:.9em}
pre{background:var(--panel);border:1px solid var(--edge);border-radius:8px;
  padding:14px 16px;overflow-x:auto;font-size:13px;color:var(--ink)}
pre .c{color:var(--dim)}
/* --- lists ---------------------------------------------------------------- */
.cards{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));
  margin:16px 0 0;padding:0;list-style:none}
.cards li{background:var(--panel);border:1px solid var(--edge);border-radius:8px;
  padding:14px 16px}
.cards h3{margin:0 0 6px;font-size:14px;color:var(--accent)}
.cards p{margin:0;font-size:13px;color:var(--dim)}
.cards .chip{display:inline-block;margin:0 0 6px;padding:1px 7px;font-size:11px;
  color:var(--ink);background:%(bg)s;border:1px solid var(--edge);border-radius:99px}
.plain{margin:16px 0 0;padding-left:20px;color:var(--dim);font-size:14px}
.plain li{margin:0 0 8px}
footer{margin-top:auto;padding:30px 0 40px;color:var(--dim);font-size:13px}
footer a{margin-right:6px}
h2{font-size:15px;color:var(--accent);margin:40px 0 0;text-transform:uppercase;
  letter-spacing:.08em}
h2+p{margin:8px 0 0;max-width:74ch;color:var(--dim);font-size:14px}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1 class="logo">moy<em>byte</em></h1>
    <p class="pitch"><b>An operating system that turns an ESP32 board into a small
      general-purpose computer</b> &mdash; one you can also write software on, on the
      board itself. The software is cartridges: games, wallpapers, tools, whatever
      you make. Open any of them, change it, run it, with no host computer in the
      loop.</p>
    <p class="meta">It boots on two off-the-shelf boards today, and the same source
      tree is a PC simulator and the browser build running below &mdash; that is the
      real console compiled to WebAssembly, not a mock-up and not a video. It is
      approachable enough for a ten-year-old (that is what the block editor is for)
      without being only that: underneath is a MicroPython firmware with native C
      kernels, a Lua VM, OTA updates and a windowing shell.</p>
  </header>

  <h2>Try it</h2>
  <div class="tabs" id="tabs">
%(tabs)s
  </div>
  <div class="stage frame" id="stage"></div>
  <div class="hint">
    <span>Click the screen, then use the arrow keys and Z / X. Pick <b>Make</b> to open the editors.</span>
    <span><b>Nothing is saved.</b> Reloading resets the machine.</span>
  </div>
%(missing)s
  <h2>What's in it</h2>
  <p>Everything here exists and runs today. Where something is unverified or
     rough, it says so.</p>
  <ul class="cards">
%(features)s
  </ul>

  <h2>What it runs on</h2>
  <ul class="cards">
%(targets)s
  </ul>

  <h2>Where it's rough</h2>
  <ul class="plain">
%(rough)s
  </ul>

  <h2>Build it</h2>
  <pre><span class="c"># the console on your PC</span>
make setup &amp;&amp; make test
.venv/bin/python tools/simulate_desktop.py

<span class="c"># firmware (needs the ESP-IDF toolchain)</span>
make firmware-build-lilygo-micropython
make firmware-flash-lilygo-micropython PORT=/dev/ttyACM0

<span class="c"># this page's player, from source</span>
firmware/web_runner/build.sh</pre>

  <footer>
    <a href="https://github.com/moybyte-org/moybyte">Source</a> &middot;
    <a href="https://github.com/moybyte-org/moy-spec">The cartridge spec (moy core 0.1)</a> &middot;
    <a href="https://github.com/moybyte-org/moybyte/blob/master/docs/moy_cart_api.md">Cart API</a> &middot;
    <a href="https://github.com/moybyte-org/moybyte/issues">Issues</a>
    <br><br>
    Source-available: free to run, modify, teach with, and to author and sell carts;
    selling hardware built on the console needs a commercial licence until each
    release turns MIT two years after publication.
    The kid- and parent-facing site is <a href="https://moybyte.com">moybyte.com</a>.
  </footer>
</div>
<script>
// Tabs own ONE iframe and swap its src, so only one wasm VM is ever live (two
// would mean two 16 MB heaps and two frame loops competing for the main thread).
// The first tab loads immediately; switching reboots the console for that tier,
// which is ~200ms on the frozen build.
var stage = document.getElementById("stage");
var tabs = [].slice.call(document.querySelectorAll(".tab"));
function show(tab) {
  tabs.forEach(function (t) { t.classList.toggle("on", t === tab); });
  stage.style.aspectRatio = tab.dataset.ar;
  stage.innerHTML = "";
  var f = document.createElement("iframe");
  f.setAttribute("title", tab.querySelector("b").textContent + " console");
  f.setAttribute("allow", "autoplay");
  f.src = "player/index.html" + tab.dataset.q;
  stage.appendChild(f);
}
tabs.forEach(function (t) {
  t.addEventListener("click", function () { show(t); });
});
show(tabs[0]);
</script>
</body>
</html>
""" % {
        "bg": bg, "panel": p(PANEL), "edge": p(EDGE), "ink": p(INK),
        "dim": p(DIM), "accent": p(ACCENT), "hilite": p(HILITE),
        "warn": p(8), "tabs": tabs, "missing": missing,
        "features": features, "targets": targets, "rough": rough,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "_site"))
    ap.add_argument("--no-player", action="store_true",
                    help="skip copying the player bundle (page only)")
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

    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
        f.write(page(palette(), has_player))

    total = sum(os.path.getsize(os.path.join(d, n))
                for d, _, ns in os.walk(out) for n in ns)
    mode = "unknown"
    if has_player:
        mode = ("dev (ships modules.json)"
                if os.path.exists(os.path.join(out, "player", "modules.json"))
                else "production (frozen)")
    print("-> %s  (%.1f MB, player: %s)" % (out, total / 1048576.0, mode))


if __name__ == "__main__":
    main()

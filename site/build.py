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


def font_face():
    """The console's own font as the display face. site/petme128.woff2 is the
    petme128 8x8 glyph set (MicroPython, MIT -- THIRD_PARTY.md) rendered as a
    webfont; inlined so the page stays one self-contained file."""
    import base64
    blob = os.path.join(HERE, "petme128.woff2")
    if not os.path.exists(blob):
        return ""
    b64 = base64.b64encode(open(blob, "rb").read()).decode("ascii")
    return ("@font-face{font-family:'Petme128';font-display:swap;"
            "src:url(data:font/woff2;base64,%s) format('woff2')}" % b64)


# The at-a-glance status row: the honest state of the machine, as data. Dots are
# role colours (ok / wip / warn), so "what works" is readable before any prose.
STATUS = [
    ("ok", "Console", "on two ESP32 boards"),
    ("ok", "Editors", "on the device itself"),
    ("ok", "OTA updates", "hardware-confirmed"),
    ("wip", "System apps", "not editable yet"),
    ("warn", "T-Deck WiFi", "broken right now"),
]

# The spec-card facts. Left label, right value -- the machine in one table.
FACTS = [
    ("Screen", "<b>320 &times; 240</b> for a cart, palette-indexed; the shell reflows to the display"),
    ("Palette", "<b>64 colours</b>, indexed end to end &mdash; host, device and browser"),
    ("Languages", "<b>Python and Lua</b>, one verb table, valid verbatim in both"),
    ("Cartridge", "a <b>folder</b>: manifest, script, sprite sheet, tilemap, sounds"),
    ("Editors", "config, blocks, code, sprites, map, scene, music &mdash; on the board"),
    ("Storage", "<b>SD or internal flash</b>; carts re-seed by version, saves kept"),
    ("Wireless", "<b>WiFi + OTA</b> into an inactive slot, with bootloader rollback"),
    ("Firmware", "<b>MicroPython</b> + native C kernels, Lua 5.4 VM outside the GC"),
]


def page(pal, has_player):
    p = lambda i: pal[i]  # noqa: E731
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
    facts = "\n".join(
        '        <tr><th>%s</th><td>%s</td></tr>' % (k, v) for k, v in FACTS)
    features = "\n".join(
        "      <li><h3>%s</h3><p>%s</p></li>" % (t, b) for t, b in FEATURES)
    targets = "\n".join(
        '      <li><h3>%s</h3><p class="chip">%s</p><p>%s</p></li>' % (t, chip, b)
        for t, chip, b in TARGETS)
    rough = "\n".join("      <li>%s</li>" % r for r in ROUGH)
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>moybyte &mdash; an operating system for ESP32 boards</title>
<meta name="description" content="An operating system that turns an ESP32 board into a small general-purpose computer. The software is cartridges -- open any of them, change it, run it, on the board itself. Try it here, no install.">
<style>
/* Every colour below is MOY64, generated from runtime/palette.py -- the site
   cannot drift from the console's own palette. Roles are named so the light
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
/* --- pixel-native display type: the console's own font ---------------------- */
.px{font-family:'Petme128',var(--mono);letter-spacing:.02em}
/* --- top bar --------------------------------------------------------------- */
nav{position:sticky;top:0;z-index:9;background:var(--bg);border-bottom:1px solid var(--line)}
nav .wrap{display:flex;align-items:center;gap:18px;height:52px}
nav .brand{font-size:19px;color:var(--ink);text-decoration:none}
nav .brand em{font-style:normal;color:var(--accent)}
nav .sp{flex:1}
nav a.l{color:var(--body);text-decoration:none;font-size:14px}
nav a.l:hover{color:var(--accent)}
/* --- hero ------------------------------------------------------------------ */
.hero{display:grid;grid-template-columns:1.15fr .85fr;gap:40px;
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
/* --- the facts card -------------------------------------------------------- */
.card{background:var(--surface);border:1px solid var(--line)}
.facts{padding:6px 18px 12px}
.facts caption{caption-side:top;text-align:left;font:12px/1 var(--mono);
  letter-spacing:.16em;text-transform:uppercase;color:var(--muted);padding:14px 0 10px}
table{width:100%%;border-collapse:collapse;font-size:14px}
.facts th{text-align:left;vertical-align:top;font:12px/1.5 var(--mono);
  letter-spacing:.1em;text-transform:uppercase;color:var(--muted);
  font-weight:400;padding:9px 14px 9px 0;white-space:nowrap}
.facts td{padding:9px 0;border-bottom:1px solid var(--line);color:var(--body)}
.facts tr:last-child td{border-bottom:0}
.facts b{color:var(--ink)}
/* --- status chips ---------------------------------------------------------- */
.status{display:flex;flex-wrap:wrap;gap:8px;list-style:none;padding:0;margin:26px 0 0}
.status li{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--muted);
  background:var(--surface);border:1px solid var(--line);padding:5px 11px}
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
.stage{margin:12px 0 0;background:#000;border:1px solid var(--line);overflow:hidden}
.stage iframe{display:block;width:100%%;height:100%%;border:0}
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
.rough{margin:20px 0 0;padding-left:20px;color:var(--body);font-size:15px}
.rough li{margin:0 0 9px}
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
  <a class="brand px" href="#top">moy<em>byte</em></a>
  <span class="sp"></span>
  <a class="l" href="#try">Try it</a>
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
      <p class="lead">It turns the board into a small computer you can write software
        on. The software is cartridges &mdash; games, wallpapers, tools, whatever you
        make &mdash; and you open, change and run any of them on the board itself,
        with no host computer in the loop.</p>
      <p class="sub">It boots on two off-the-shelf boards today, and the same source
        tree is a PC simulator and the browser build below. Approachable enough for a
        ten-year-old (that is what the block editor is for) without being only that:
        underneath is a MicroPython firmware with native C kernels, a Lua VM, OTA
        updates and a windowing shell.</p>
      <div class="btns">
        <a class="btn pri" href="#try">Try it in the browser &#9656;</a>
        <a class="btn" href="https://github.com/moybyte-org/moybyte">Source</a>
        <a class="btn" href="https://github.com/moybyte-org/moy-spec">The cart spec</a>
      </div>
      <ul class="status">
%(status)s
      </ul>
    </div>
    <div class="card">
      <table class="facts">
        <caption>The machine</caption>
%(facts)s
      </table>
    </div>
  </div>
</div>

<section id="try"><div class="wrap">
  <h2>Try it, right here</h2>
  <p class="slead">The real console compiled to WebAssembly &mdash; the same code the
    firmware freezes, served from this page and nowhere else. Not a mock-up, not a
    video.</p>
  <div class="tabs" id="tabs">
%(tabs)s
  </div>
  <div class="stage" id="stage"></div>
  <div class="hint">
    <span>Click the screen, then arrow keys and Z / X. Pick <b>Make</b> for the editors.</span>
    <span><b>Nothing is saved.</b> Reloading resets the machine.</span>
  </div>
%(missing)s</div></section>

<section id="in"><div class="wrap">
  <h2>What's in it</h2>
  <p class="slead">Everything here exists and runs today. Where something is
    unverified or rough, it says so.</p>
  <ul class="cards">
%(features)s
  </ul>
</div></section>

<section id="runs"><div class="wrap">
  <h2>What it runs on</h2>
  <p class="slead">Host and device are one codebase, not a port: each firmware build
    stages copies of the same modules and freezes them.</p>
  <ul class="cards">
%(targets)s
  </ul>
</div></section>

<section id="rough"><div class="wrap">
  <h2>Where it's rough</h2>
  <ul class="rough">
%(rough)s
  </ul>
</div></section>

<section id="build"><div class="wrap">
  <h2>Build it</h2>
  <pre><span class="c"># the console on your PC</span>
make setup &amp;&amp; make test
.venv/bin/python tools/simulate_desktop.py

<span class="c"># firmware (needs the ESP-IDF toolchain)</span>
make firmware-build-lilygo-micropython
make firmware-flash-lilygo-micropython PORT=/dev/ttyACM0

<span class="c"># this page's player, from source</span>
firmware/web_runner/build.sh &amp;&amp; make site</pre>
  <footer>
    <a href="https://github.com/moybyte-org/moybyte">Source</a> &middot;
    <a href="https://github.com/moybyte-org/moy-spec">The cartridge spec (moy core 0.1)</a> &middot;
    <a href="https://github.com/moybyte-org/moybyte/blob/master/docs/moy_cart_api.md">Cart API</a> &middot;
    <a href="https://github.com/moybyte-org/moybyte/issues">Issues</a>
    <br><br>
    Source-available (FSL-1.1-MIT): free to run, modify, teach with, and to author
    and sell carts; selling hardware built on the console needs a commercial licence
    until each release turns MIT two years after publication. The player bundle on
    this page is MIT. The kid- and parent-facing site is
    <a href="https://moybyte.com">moybyte.com</a>.
  </footer>
</div></section>
<script>
// Tabs own ONE iframe and swap its src, so only one wasm VM is ever live (two
// would mean two heaps and two frame loops competing for the main thread). The
// first tab loads immediately; switching reboots the console for that tier.
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
tabs.forEach(function (t) { t.addEventListener("click", function () { show(t); }); });
show(tabs[0]);
</script>
</body>
</html>
""" % {
        "tokens": tokens, "font": font_face(), "tabs": tabs, "missing": missing,
        "status": status, "facts": facts, "features": features,
        "targets": targets, "rough": rough,
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

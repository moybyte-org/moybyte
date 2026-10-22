"""The SHELL pixel goldens (#UI refactor 2026-08, Phase 0).

What this is, and what it is NOT
--------------------------------
This hashes the rendered SYSTEM canvas of every shell surface against bytes
STORED in the repo (`tests/shell_goldens/hashes.json`). That is the whole
point, and it is what makes it different in kind from the checks it must not
be confused with:

  * `tests/spec_conformance/hashes.json` pins the CART raster (320x240,
    the spec's recorded verb traces). It says nothing about a single shell pixel.
  * `tests/test_responsive_editors.py` builds TWO workstations *now* and
    compares them -- a live-vs-live A/B. A refactor that moves both arms
    passes green. It also asserts layout attributes against the constants in
    the module it is guarding (`lay.pg_span == P._PG_SPAN`), which is an
    oracle derived from the thing under test.
  * `tests/test_wm_windowed.py` compares two code paths in the same commit.

Only a hash of committed bytes can catch "the whole shell moved one pixel",
which is exactly the risk of transcribing hand-rolled widgets onto a toolkit.

The matrix, and why it is this one
----------------------------------
Eleven configurations, each ONE axis away from the T-Deck row or from
the windowed row, so a red golden names the axis by itself. A row that names
no theme draws the default seed, Moy (docs/look_2026-10.md step 27), in its
variant: Night where it is dark, Day where it is light. Every configuration renders the SAME surface
list (plus the two desk surfaces that only exist on the windowed tier), which
keeps the matrix a clean product instead of a pile of special cases.

  tdeck_320x240_fs1_dark   the T-Deck: shared canvas (sys IS game), font
                           scale 1, dark -- the smallest canvas the layout
                           formulas meet, so this is the reference row.
  tdeck_320x240_fs1_light  the SAME geometry, `variant="light"`. The variant
                           axis in isolation: any difference from the row
                           above is purely the token set. Before this file,
                           `variant="light"` appeared five times in the suite
                           and was NEVER followed by a draw -- a light panel
                           could render black-on-black and the suite stayed
                           green.
  guition_480x320_fs1_dark the Guition tier (#202): a fullscreen board whose
                           system canvas is NOT the game canvas. Zero host
                           coverage before this file; asserted only in
                           `tests/test_guition_on_glass.py`, which skips
                           without the board.
  guition_tapfloor_dark    the SAME Guition tier with its panel DIAGONAL
                           declared (3.5"), which is how a board opts in to
                           the #203 tap-target floor. One axis from the row
                           above: chrome_scale 2 against font_scale 1, so
                           these two rows together are the proof the mechanism
                           was built for -- the bar, the ≡ menu and the
                           Settings rows grew, and not one glyph did.
  big_800x480_fs3_dark     font_scale 3. fs=2 renders elsewhere but is never a
                           pinned reference and fs=3 never drew a shell frame
                           at all (`ui_widgets_2026-08.md` A-m6). 800x480 is
                           the smallest canvas that gives fs=3 room.
  p4_1024x600_fs2_windowed the desktop tier: WindowedWM, font_scale 2 (so the
                           fs=2 rung is pinned here rather than in a config of
                           its own), plus the desk and a desk-with-one-window,
                           on the P4's declared 7.0" glass: a desk, laid out
                           on the desk's metric class (layout_base.CLASSES).
  p4_1024x600_cs2_fs1_windowed  the P4 AS SHIPPED: the same desk at font
                           scale 1 and chrome size 1, whose layouts run at the
                           desk's unit of 2 (the `cs2` of every P4 row's
                           name). One axis from the row above (font scale).
  p4_1024x600_fs2_windowed_light  the windowed row in the light variant: the
                           one place light chrome meets the WM's decorations.
                           One axis from the windowed row (variant).
  p4_1024x600_cs2_fs1_contrast_light  the same, under the high-contrast seed
                           `contrast` (docs/look_2026-10.md step 26c): black
                           on white in the light variant, 2 px outlines, a
                           yellow focus outlined in black, Atkinson. One axis
                           (theme) from the as-shipped row in the light
                           variant.
  tdeck_320x240_fs1_moy_day, tdeck_320x240_fs1_moy_night,
  p4_1024x600_cs2_fs1_moy_day, p4_1024x600_cs2_fs1_moy_night
                           the `moy` seed (docs/look_2026-10.md step 5) in
                           its Day (light) and Night (dark) modes, the
                           T-Deck row and the P4 as shipped: RGB roles in the
                           block at indices 64..127 on every surface. One axis
                           (theme) from each row in its variant.
  tdeck_320x240_fs1_blocks, p4_1024x600_cs2_fs1_blocks
                           the `blocks` seed (docs/look_2026-10.md step 26b),
                           one mode (light), the T-Deck row and the P4 as
                           shipped: Atkinson, the sunflower home, the Bauhaus
                           desk and its per-app tiles. One axis (theme) from
                           each row in the light variant.
  tdeck_320x240_fs1_parts_day, tdeck_320x240_fs1_parts_night
                           the public UI API's parts sheet
                           (docs/look_2026-10.md step 12): a system app
                           (tests/fixtures/ui_parts/parts_app.py) drawing the
                           Settings and Appearance targets' rows with the
                           verbs, in the `moy` seed's Day and Night, and only
                           those surfaces (a config's `plan`).
  tdeck_320x240_fs1_moy_menus, p4_1024x600_cs2_fs1_moy_menus
                           the system menu and the pause menu
                           (docs/look_2026-10.md step 25a) in the `moy` seed's
                           Day, and only those surfaces (a config's `plan`):
                           the system menu over the home (the T-Deck) or the
                           desk (the P4), and the pause menu and its Options
                           page over Star Catcher, held after a fixed run of
                           frames from a seeded `rnd` (the P4's in console
                           mode, where a game plays fullscreen).
  guition_p4_1280x800_windowed  the Guition P4's desk as shipped: the second
                           desk glass, on the same desk metrics as the P4.
                           One axis (the glass) from the as-shipped P4 row.
  p4_1024x600_render2_moy_day  the Moy Day row with `render.scale: 2`
                           (docs/look_2026-10.md step 9): the desk drawn on a
                           512x300 surface at the desk's metrics halved and
                           doubled onto the glass, which is what is hashed.
                           One axis (the render scale) from the Moy Day row.
  p4_1024x600_cs2_fs1_console  the P4 as shipped in CONSOLE mode
                           (docs/shell_layout_2026-10.md §3): the desk's PLAY,
                           then the T-Deck row's surfaces, every one
                           fullscreen at the P4's size. One axis (the mode)
                           from the as-shipped row.
  p4_1024x600_cs2_fs1_win95bar, p4_1024x600_cs2_fs1_gnomebar,
  p4_1024x600_cs2_fs1_twobars, tdeck_320x240_fs1_bottombar
                           the worked examples of docs/shell_layout_2026-10.md
                           §6 (2, 3, 4 and 6), each a package whose `layout`
                           the console picks (`layout_theme`): a bottom
                           taskbar with the chips beside the ≡, a top bar with
                           the clock centred, a top bar over a bottom verb
                           strip, and the T-Deck's bottom strip. One axis (the
                           layout) from the as-shipped P4 row and the T-Deck
                           row.
  p4_1024x600_cs2_fs1_popovers, tdeck_320x240_fs1_widget_pages
                           the bar widgets' components (§8), and only those
                           surfaces (a config's `plan`): over the as-shipped
                           P4 desk with a window, each widget's popover open
                           -- the calendar, the WIFI panel, the power rows, the
                           system menu; on the T-Deck, console mode's
                           fullscreen container for each -- the calendar page,
                           Settings at the WIFI panel, Settings at VOLUME.
  tdeck_320x240_fs1_homegrid, p4_1024x600_cs2_fs1_homegrid
                           the home in its GRID arrangement (the layout's
                           `home`, docs/shell_layout_2026-10.md §12), one
                           surface a tab -- Games, Mine, Apps, Make -- on the
                           T-Deck and on the as-shipped P4 in console mode.
                           One axis (the arrangement) from those rows.
  tdeck_320x240_fs1_homeicons, p4_1024x600_cs2_fs1_homeicons
                           the home in its ICONS arrangement, the same four
                           surfaces on the same two configs: one axis (the
                           arrangement) from the grid's rows.

The last two were added before theming's first phase
(`docs/theming_2026-09.md` §9), so the phases that hold the goldens hold
them on the tier and the variant every restyle reaches.

Determinism
-----------
Everything time-dependent is pinned rather than tolerated (the repo rule:
dt is INJECTED, never read from a clock -- `ScrollRegion` is the precedent):

  * dt is the fixed `_DT`; no surface reads a clock for its own animation.
  * `bar_layer._clock_text` is bound to a constant. The OS bar prints a live
    HH:MM, and a minute boundary between two renders is a real flake that has
    taken CI down before (see `test_responsive_editors._quiesce`).
  * every transient overlay is cleared each frame (`_quiesce`): the toast, the
    confetti/egg timers, the achievements list, the system menu, About, the
    notice banner, the FPS/perf HUDs, the cursor.
  * `cover_cache._COVER_SLICE_MS` is raised so every home and home-window cover builds
    within its frame: tests/conftest.py's `_cover_budget_unbounded` does it for
    every test of the suite, tools/look_shots.py with `_COVER_SLICE_UNBOUNDED`.
    This is load-bearing, not belt-and-braces: the cover build is a per-frame
    TIME budget, and the launcher home hashes differently at a 1ms slice than
    at 5ms. Left alone, these goldens would be a function of how fast the
    machine is.
  * each surface is rendered until two consecutive frames are byte-identical
    (cap `_MAX_FRAMES`); a surface that never settles FAILS by name rather
    than silently baking in whichever frame it was caught on.

Deliberately NOT covered
------------------------
  * A RUNNING cart. That is the game domain -- pinned by
    `tests/test_spec_conformance.py` against the spec's own goldens -- and
    seed carts are free to use `rnd()`, so it is not deterministic here.
  * The EDIT ICONS ("theme") surface and the OTA update screen: neither is in
    the Phase 0 list, and the update screen's content is a live network state
    machine.

The draw budgets
----------------
Beside each hash, `tests/shell_goldens/counts.json` pins the settled frame's
(fills, texts) through the system canvas's native gates (`gate_counts`, real
on the host since sprint 6). Pixels can hold while the work to draw them
grows; a budget that moves fails by name, and is re-pinned by the same
command as the hashes. Only the system canvas counts: a window's own canvas on
the desk is not in the number.

No frame of a surface's render draws more than one focus mark (the
engine's count, `ui_draw.marks`): a screen shows one, and a window behind draws none
(docs/media/look_2026-10/DESIGN.md, the marks). The window manager clears
the marks while a window behind draws, so a second mark is an app's.

What moves these goldens (so a red one is diagnosable)
-----------------------------------------------------
Three things, in descending order of likelihood: a shell DRAW change; a
`system_carts/` edit (the surfaces show real seeded content -- the shelf, the
home window and every Editor tab render the seeded store, and the Editor tabs
render `Star Catcher` specifically); and a font/palette change.

Every row draws through the toolkit and the `*Layout` formulas, the T-Deck's
320x240 included: the frozen 320x240 branches (`_base`) retired with theming
phase 3 step 4 (docs/theming_2026-09.md §9), so a widget or a layout formula
change moves the T-Deck rows as it moves the others.

Re-baselining is a DELIBERATE act
---------------------------------
    MOYBYTE_UPDATE_GOLDENS=1 .venv/bin/python -m pytest tests/test_shell_goldens.py -p no:xdist
or  .venv/bin/python -m pytest tests/test_shell_goldens.py --update-goldens -p no:xdist

Nothing re-baselines automatically. If a golden moves and you cannot say in
one sentence which pixel moved and why, the change is a revert, not an update
(`docs/history/ui_refactor_2026-08.md` Section 8).
"""

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from cart_store_fixtures import INDEX_URL, MemNet, snapshot
from ws_helpers import open_cart

ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = Path(__file__).resolve().parent / "shell_goldens"
GOLDEN_FILE = GOLDEN_DIR / "hashes.json"
# The draw budgets: each settled surface's (fills, texts) through the system
# canvas's native gates (`gate_counts`), pinned per config beside the hashes
# (docs/theming_2026-09.md §9, phase 2a). Pixels can hold while the work to
# draw them grows; this is the net for that, re-pinned with the hashes.
COUNTS_FILE = GOLDEN_DIR / "counts.json"

UPDATE_ENV = "MOYBYTE_UPDATE_GOLDENS"
REBASELINE_CMD = (
    "MOYBYTE_UPDATE_GOLDENS=1 .venv/bin/python -m pytest "
    "tests/test_shell_goldens.py -p no:xdist")

_DT = 1.0 / 30.0          # the injected frame delta; never a clock
_MAX_FRAMES = 6           # settle cap -- every surface settles at 2 today
_CLOCK = "00:00"          # the pinned OS-bar clock text
_COVER_SLICE_UNBOUNDED = 10 ** 9   # ms: build every cover in its own frame

# The cart whose Editor tabs are rendered. A seeded GAME with a sprite sheet,
# a tilemap, a scene and a sound bank, so all seven tabs have real content.
_EDITOR_CART = "Star Catcher"

# Seed carts the golden matrix deliberately does NOT show.
#
# The home, the home window and the desk icon column render REAL
# seeded content (see "What moves these goldens" above), so without this list
# these 116 goldens are a function of `system_carts/` and merely ADDING a cart
# turns six configurations red for a reason that is not a pixel. That is the
# worst kind of red: it trains the reader to re-baseline, which is precisely the
# laundering this file exists to prevent.
#
# So a content addition is either EXCLUDED here -- one line, with a `why`, the
# `board.toml` deny convention -- or deliberately re-baselined. Both are
# decisions; neither is a shrug. Excluding a cart costs nothing this file
# measures: no golden here renders a cart's own pixels (a running cart is the
# game domain, pinned by tests/test_spec_conformance.py), only the tile the
# shelf draws for it, and 34 tiles already exercise every shelf branch there is.
# Empty today, and deliberately kept: the mechanism is what makes the choice
# above a decision rather than a shrug. Notes lived here as "the #181 demo
# cart" until step 3 of docs/text_editing_2026-09.md made it the console's ONE
# text app -- a shipped surface with a golden of its own (`app_notes`), which
# is exactly the case an exclusion must not cover.
GOLDEN_EXCLUDE = {}

# The Editor tab ladder and the system-app roster. Both are pinned against the
# live registries below (test_tab_ladder_is_fully_covered /
# test_every_registered_app_is_covered), so adding a tab or an app without
# adding its golden is a red test rather than a silent coverage hole.
TABS = ("cards", "blocks", "code", "paint", "map", "scene", "music")
APPS = ("artwork", "myart", "appearance", "storybook", "files", "calc", "getcarts")

# Apps that are CARTS, not registered layers (#181): rendered by RUNNING them,
# so what these hash is the app bar over a cart's own 320x240 raster, composited
# and scaled by the tier. `APPS` above is pinned against `ws._apps_by_id`; these
# deliberately are not in it, and are pinned against the STORE instead
# (test_every_app_cart_is_covered), which is where a cart's identity lives.
#
# The "a running cart is not covered here" note above still holds for GAMES: a
# game is the spec's domain and free to use rnd(). An app cart draws a settled
# surface out of stored content, which is what every other row here does.
APP_CARTS = (("notes", "Notes"),)

# The public UI API's fixture page (docs/ui_api_2026-10.md), the Python app's
# (tests/test_ui_public.py holds the Lua and compiled ones to its pixels): its
# config's one surface.
UI_PAGE = (str(Path(__file__).resolve().parent / "fixtures" / "ui_page" / "page_py.moy"),
           "UI Page Py")

# The public UI API's parts sheet (docs/look_2026-10.md step 12): a system
# app registered for the `parts` configs, its pages their surfaces.
PARTS_APP = Path(__file__).resolve().parent / "fixtures" / "ui_parts" / "parts_app.py"
PARTS_PAGES = ("settings", "appearance")

# The WEB CONSOLE connection screen (#197) is rendered LAST, and with a fake
# service, for two reasons that are both about not moving the other 87 hashes:
# injecting `ws.webhost` makes the Settings row appear, and parking the glass
# calls go_home. Every other surface is already captured by then.
#
# Both the pin and the address are FIXED here. They are not decoration -- they
# are the QR's input, and a minted pin would re-encode a different matrix on
# every run, which is the one way this surface could be non-deterministic.
GOLDEN_PIN = "4821"
GOLDEN_URL = "http://192.168.1.151/"

# The TEXT CONSOLE (docs/text_editing_2026-09.md step 6), rendered as a real
# SCRIPT RUN. It is the one running cart in this matrix, and the exception is
# principled: a script has no `_draw`, so every pixel on it is the SHELL's --
# the scrollback, the prompt and the symbol palette -- and none of it reads a
# clock or `rnd()`. The script prints two fixed lines and then waits on
# `input()`, which is the state the surface is interesting in.
GOLDEN_SCRIPT_NAME = "say_hello.py"
GOLDEN_SCRIPT = (
    "print('a script is a cart with no folder')\n"
    "print('RUN wraps it on the fly')\n"
    "def _update(dt):\n"
    "    name = input('your name? ')\n"
    "    if name is not None:\n"
    "        print('hello ' + name)\n")


class _GoldenWebHost:
    """The `serving`/`start`/`stop`/`url`/`paired_url` contract the console's
    webhost verbs use, over a fixed address."""

    serving = False
    error = None

    def start(self):
        self.serving = True

    def stop(self):
        self.serving = False

    def url(self):
        return GOLDEN_URL

    def paired_url(self):
        return GOLDEN_URL + "?pin=" + GOLDEN_PIN


# `diagonal_in` is the board fact behind the #203 tap-target floor; None is
# every tier that does not declare one, i.e. chrome on the font scale.
# The default seed's bar, for a config that changes only the home's
# arrangement (a layout is set whole, its bars with it).
_DEFAULT_BARS = [{"edge": "top", "size": 18, "left": ["menu", "chips", "zone"],
                  "right": ["close", "wifi", "battery", "clock"]}]

CONFIGS = {
    "tdeck_320x240_fs1_dark": dict(
        sys_size=None, font_scale=1, windowed=False, variant="dark",
        diagonal_in=None, appearance=True),
    "tdeck_320x240_fs1_light": dict(
        sys_size=None, font_scale=1, windowed=False, variant="light",
        diagonal_in=None, appearance=True),
    "guition_480x320_fs1_dark": dict(
        sys_size=(480, 320), font_scale=1, windowed=False, variant="dark",
        diagonal_in=None),
    "guition_tapfloor_dark": dict(
        sys_size=(480, 320), font_scale=1, windowed=False, variant="dark",
        diagonal_in=3.5),
    "big_800x480_fs3_dark": dict(
        sys_size=(800, 480), font_scale=3, windowed=False, variant="dark",
        diagonal_in=None),
    "p4_1024x600_fs2_windowed": dict(
        sys_size=(1024, 600), font_scale=2, windowed=True, variant="dark",
        diagonal_in=7.0),
    "p4_1024x600_cs2_fs1_windowed": dict(
        sys_size=(1024, 600), font_scale=1, windowed=True, variant="dark",
        diagonal_in=7.0, appearance=True),
    "p4_1024x600_fs2_windowed_light": dict(
        sys_size=(1024, 600), font_scale=2, windowed=True, variant="light",
        diagonal_in=7.0),
    "p4_1024x600_cs2_fs1_contrast_light": dict(
        sys_size=(1024, 600), font_scale=1, windowed=True, variant="light",
        diagonal_in=7.0, theme="contrast"),
    "p4_1024x600_cs2_fs1_console": dict(
        sys_size=(1024, 600), font_scale=1, windowed=True, variant="dark",
        diagonal_in=7.0, console=True),
    "p4_1024x600_cs2_fs1_win95bar": dict(
        sys_size=(1024, 600), font_scale=1, windowed=True, variant="dark",
        diagonal_in=7.0, layout=[
            {"edge": "bottom", "size": 22,
             "left": ["menu", "separator", "chips", "zone"],
             "right": ["close", "wifi", "battery", "clock"]}]),
    "p4_1024x600_cs2_fs1_gnomebar": dict(
        sys_size=(1024, 600), font_scale=1, windowed=True, variant="dark",
        diagonal_in=7.0, layout=[
            {"edge": "top", "size": 18, "left": ["zone"], "center": ["clock"],
             "right": ["wifi", "battery", "close", "menu"]}]),
    "p4_1024x600_cs2_fs1_twobars": dict(
        sys_size=(1024, 600), font_scale=1, windowed=True, variant="dark",
        diagonal_in=7.0, layout=[
            {"edge": "top", "size": 18, "left": ["title"],
             "right": ["clock", "close", "menu", "wifi", "battery"]},
            {"edge": "bottom", "size": 18, "left": ["zone"]}]),
    "tdeck_320x240_fs1_bottombar": dict(
        sys_size=None, font_scale=1, windowed=False, variant="dark",
        diagonal_in=None, layout=[
            {"edge": "bottom", "size": 18, "left": ["menu", "zone"],
             "right": ["clock", "close", "battery"]}]),
    "tdeck_320x240_fs1_homegrid": dict(
        sys_size=None, font_scale=1, windowed=False, variant="dark",
        diagonal_in=None, layout=_DEFAULT_BARS, home="grid", plan="home"),
    "p4_1024x600_cs2_fs1_homegrid": dict(
        sys_size=(1024, 600), font_scale=1, windowed=True, variant="dark",
        diagonal_in=7.0, layout=_DEFAULT_BARS, home="grid", plan="home"),
    "tdeck_320x240_fs1_homeicons": dict(
        sys_size=None, font_scale=1, windowed=False, variant="dark",
        diagonal_in=None, layout=_DEFAULT_BARS, home="icons", plan="home"),
    "p4_1024x600_cs2_fs1_homeicons": dict(
        sys_size=(1024, 600), font_scale=1, windowed=True, variant="dark",
        diagonal_in=7.0, layout=_DEFAULT_BARS, home="icons", plan="home"),
    "p4_1024x600_cs2_fs1_popovers": dict(
        sys_size=(1024, 600), font_scale=1, windowed=True, variant="dark",
        diagonal_in=7.0, plan="popovers"),
    "tdeck_320x240_fs1_widget_pages": dict(
        sys_size=None, font_scale=1, windowed=False, variant="dark",
        diagonal_in=None, plan="widget_pages"),
    "tdeck_320x240_fs1_moy_day": dict(
        sys_size=None, font_scale=1, windowed=False, variant="light",
        diagonal_in=None, theme="moy"),
    "tdeck_320x240_fs1_moy_night": dict(
        sys_size=None, font_scale=1, windowed=False, variant="dark",
        diagonal_in=None, theme="moy"),
    "p4_1024x600_cs2_fs1_moy_day": dict(
        sys_size=(1024, 600), font_scale=1, windowed=True, variant="light",
        diagonal_in=7.0, theme="moy"),
    "p4_1024x600_cs2_fs1_moy_night": dict(
        sys_size=(1024, 600), font_scale=1, windowed=True, variant="dark",
        diagonal_in=7.0, theme="moy"),
    "tdeck_320x240_fs1_moy_menus": dict(
        sys_size=None, font_scale=1, windowed=False, variant="light",
        diagonal_in=None, theme="moy", plan="menus"),
    "p4_1024x600_cs2_fs1_moy_menus": dict(
        sys_size=(1024, 600), font_scale=1, windowed=True, variant="light",
        diagonal_in=7.0, theme="moy", plan="menus"),
    "tdeck_320x240_fs1_blocks": dict(
        sys_size=None, font_scale=1, windowed=False, variant="light",
        diagonal_in=None, theme="blocks"),
    "p4_1024x600_cs2_fs1_blocks": dict(
        sys_size=(1024, 600), font_scale=1, windowed=True, variant="light",
        diagonal_in=7.0, theme="blocks"),
    "guition_p4_1280x800_windowed": dict(
        sys_size=(1280, 800), font_scale=1, windowed=True, variant="dark",
        diagonal_in=10.1),
    "tdeck_320x240_fs1_parts_day": dict(
        sys_size=None, font_scale=1, windowed=False, variant="light",
        diagonal_in=None, theme="moy", plan="parts"),
    "tdeck_320x240_fs1_parts_night": dict(
        sys_size=None, font_scale=1, windowed=False, variant="dark",
        diagonal_in=None, theme="moy", plan="parts"),
    "tdeck_320x240_fs1_ui_page": dict(
        sys_size=None, font_scale=1, windowed=False, variant="dark",
        diagonal_in=None, plan="ui_page"),
    "p4_1024x600_render2_moy_day": dict(
        sys_size=(1024, 600), font_scale=1, windowed=True, variant="light",
        diagonal_in=7.0, theme="moy", render=2),
}

# The bar widgets that open a component (docs/shell_layout_2026-10.md §8), in
# the order the two `plan` configs render them: a popover each over the desk
# (`popovers`), and console mode's fullscreen page each (`widget_pages`).
WIDGETS = ("clock", "wifi", "battery", "menu")
HOME_TAB_IDS = ("games", "mine", "apps", "make")
_DATE = (2026, 10, 14)    # the pinned calendar day

# The config test_editor_tabs_render_independently sweeps. The largest one: every
# cross-tab bounding an editor layout can suffer is a big-screen feature, and #216
# showed only here.
_INDEPENDENCE_CONFIG = "big_800x480_fs3_dark"


def _axes(cfg):
    """The one-line axis description quoted in a failure message."""
    w, h = cfg["sys_size"] or (320, 240)
    return "size=%dx%d font_scale=%d variant=%s tier=%s diagonal=%s theme=%s layout=%s" % (
        w, h, cfg["font_scale"], cfg["variant"],
        ("console" if cfg.get("console") else "windowed") if cfg["windowed"]
        else "fullscreen",
        cfg["diagonal_in"] or "-", cfg.get("theme", "moy"),
        _layout_name(cfg) or "the theme's")


def _layout_name(cfg):
    """The package a config's `layout` is installed as, or None."""
    if "layout" not in cfg:
        return None
    return [k for k, v in CONFIGS.items() if v is cfg][0].rsplit("_", 1)[-1]


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------

def _quiesce(ws):
    """Strip every transient + clock-driven thing off the frame.

    Called before EVERY frame, not once per surface: opening a cart or an app
    notes achievements, which can re-arm the toast/confetti mid-sequence."""
    if ws.pointer is not None:
        ws.pointer.visible = False
    ws._toast_until = 0
    ws._egg_until = 0
    ws._confetti_until = 0
    ws.show_achievements = False
    ws.show_fps = False
    ws.perf_hud = False
    ws.perf_capture = False
    if not getattr(ws, "_golden_popover", False):
        ws.sysmenu.open = False
    ws._about = False
    ws._notice = None
    ws._notice_until = 0
    # The bar prints a live HH:MM. Bind the accessor -- pinning the per-second
    # cache does NOT work (bar_layer compares `now_s == self._clock_at`, so a
    # sentinel forces a recompute every frame).
    ws.bar_layer._clock_text = lambda: _CLOCK
    ws.bar_layer._today = lambda: _DATE


_DRAWN = {}             # (config, surface) -> (fills, texts) of the settled frame
_MARKED = {}            # (config, surface) -> the most focus marks one frame drew


def _render(ws, surface, config_name):
    """Draw until two consecutive frames are byte-identical; hash that frame.
    The settled frame's fills and texts go to `_DRAWN`."""
    prev = None
    cv = ws.sys_canvas
    marked = 0
    for _ in range(_MAX_FRAMES):
        _quiesce(ws)
        ws._dirty = True                 # defeat the #44 redraw gate
        cv.gate_counts_reset()
        _ui().marks(None, True)
        ws.frame(_DT)
        marked = max(marked, _ui().marks()[2])
        cur = bytes(ws.glass_canvas._buf)
        if cur == prev:
            fills, texts = cv.gate_counts()[:2]
            _DRAWN[(config_name, surface)] = (fills, texts)
            _MARKED[(config_name, surface)] = marked
            return hashlib.sha256(cur).hexdigest()
        prev = cur
    raise AssertionError(
        "shell golden surface %r (config %r) never settled: %d frames drawn "
        "and the last two still differ. Something on it is animating or "
        "time-dependent; pin it (inject dt) or exclude it with a comment "
        "naming what and why." % (surface, config_name, _MAX_FRAMES))


def _editor_tab_hash(ws, tab):
    """Open the Editor on the pinned cart at `tab` and hash the settled frame --
    the same two calls `_surface_plan`'s per-tab `enter` makes."""
    ws.open_in_editor(_cart_by_title(ws, _EDITOR_CART))
    ws.set_menu_view(tab)
    return _render(ws, "editor_" + tab, _INDEPENDENCE_CONFIG)


def _cart_by_title(ws, title):
    for cart in ws.carts.all:
        if cart.get("title") == title:
            return cart
    raise AssertionError("seed cart not found: " + title)


def _build(cfg, carts_dir):
    from runtime import host_app
    if cfg.get("plan") == "ui_page":
        host_app.moy_carts.ensure_dirs(str(carts_dir))
        shutil.copytree(UI_PAGE[0], os.path.join(str(carts_dir), os.path.basename(UI_PAGE[0])))
    ws = host_app.build_workstation(
        str(carts_dir), sys_size=cfg["sys_size"],
        font_scale=cfg["font_scale"], windowed=cfg["windowed"],
        panel_diagonal_in=cfg["diagonal_in"])
    pick_layout(ws, cfg)
    theme = cfg.get("theme", ws.look.theme_name)
    if cfg.get("render", 1) != 1:
        # A package of the config's theme asking for its render scale.
        import moy_ui                  # registered by the runtime package
        name = theme + "-x%d" % cfg["render"]
        rc, _notes = moy_ui.theme_install(json.dumps(
            {"format": 2, "name": name, "inherits": theme,
             "look": {"render": {"scale": cfg["render"]}}}).encode())
        assert rc == 0, name
        theme = name
    # persist=False: the two tdeck rows must differ by the token set ALONE, so
    # neither may leave a theme_variant behind in its store.
    ws.look.set_theme(theme, persist=False, variant=cfg["variant"])
    keep = [c for c in ws.carts.all if c.get("title") not in GOLDEN_EXCLUDE]
    if len(keep) != len(ws.carts.all):
        ws.carts.apply(keep)            # pin the roster -- see GOLDEN_EXCLUDE
    return ws


def pick_layout(ws, cfg):
    """A config's `layout` is a package beside the seeds that the console
    picks, the way a kid would (docs/shell_layout_2026-10.md §11); it takes
    effect at the next theme set."""
    name = _layout_name(cfg)
    if name is None:
        return
    import moy_ui                      # registered by the runtime package
    rc, _notes = moy_ui.theme_install(json.dumps(
        {"format": 2, "name": name, "layout": dict(
            {"bars": cfg["layout"]}, **({"home": cfg["home"]} if "home" in cfg else {}))}
         ).encode())
    assert rc == 0, name
    ws.system.set("layout_theme", name)


def _surface_plan(ws, cfg):
    """(name, enter) pairs in the fixed order they are rendered.

    ONE workstation renders them all, in this order, so the captured state is
    a pure function of the list -- a fresh workstation per surface would be
    ~35 seed copies each and buy nothing a fixed order does not."""
    plan = []
    if cfg.get("plan") == "popovers":
        return _popover_plan(ws)
    if cfg.get("plan") == "widget_pages":
        return _widget_page_plan(ws)
    if cfg.get("plan") == "home":
        return _home_plan(ws, cfg)
    if cfg.get("plan") == "ui_page":
        return [("ui_page", lambda: open_cart(ws, UI_PAGE[1]))]
    if cfg.get("plan") == "parts":
        return _parts_plan(ws)
    if cfg.get("plan") == "menus":
        return _menus_plan(ws, cfg)
    desktop = cfg["windowed"] and not cfg.get("console")
    if desktop:
        # The desk (desktop mode's floor), the desk with ONE app window, and
        # the desk's PROJECTS: the home in a window, on Make, alone on the desk.
        plan.append(("desk", lambda: ws.open_desk()))
        plan.append(("desk_window_calc",
                     lambda: ws.open_app(ws._apps_by_id["calc"])))

        def projects():
            ws.open_desk()
            ws.open_projects()
        plan.append(("desk_window_projects", projects))

    def home():
        # The home, with nothing parked beneath it: go_home closes the desk's
        # windows, and on the windowed tier PLAY then enters console mode.
        ws.go_home()
        if cfg["windowed"]:
            ws.open_library()

    plan.append(("launcher", home))
    plan.append(("settings", lambda: ws.open_settings()))
    cart = _cart_by_title(ws, _EDITOR_CART)
    for tab in TABS:
        def enter(tab=tab):
            # Re-open per tab. That alone does NOT buy independence -- the layouts
            # are ws singletons another tab can bind (#216); what pins it is
            # test_editor_tabs_render_independently. The desktop rows draw the
            # Editor as a window, so they come back to the desk first.
            if desktop:
                ws.wm.leave_console()
            ws.open_in_editor(cart)
            ws.set_menu_view(tab)
        plan.append(("editor_" + tab, enter))
    pages = False
    for app in APPS:
        enter = (lambda app=app: ws.open_app(ws._apps_by_id[app]))
        if app == "getcarts":
            enter = _open_get_carts(ws)
        if pages:
            enter = _after_appearance(ws, enter)
            pages = False
        plan.append(("app_" + app, enter))
        if app == "appearance" and cfg.get("appearance"):
            plan.append(("app_appearance_walls", _walls_page(ws)))
            plan.append(("app_appearance_name",
                         _after_appearance(ws, _name_page(ws))))
            pages = True
    for surface, title in APP_CARTS:
        enter = (lambda title=title: open_cart(ws, title))
        if surface == "notes":
            enter = _notes_vault(ws, enter)
        plan.append(("app_" + surface, enter))
    # Notes with a note open: its page of tools, the note in the look
    plan.append(("app_notes_note", lambda: ws.player.ns["_open"](NOTES_VAULT[0][0])))

    def park():
        ws.system.set("web_pin", GOLDEN_PIN)
        if ws.webhost is None:
            ws.webhost = _GoldenWebHost()
        if not ws.webhost_serving():
            ws.toggle_webhost()          # the real funnel: start, then park
        ws.web.ui.show_address = False

    def park_revealed():
        park()
        ws.web.ui.show_address = True

    def run_script():
        # From HOME, so the windowed tier runs it in console mode (where a
        # script takes the whole responsive surface) rather than cascaded under
        # the six app windows every surface before this one left open.
        home()
        # Written through the store the workstation was built on: the script
        # lives in the vault like any note, and RUN synthesizes its manifest.
        ws.carts_store.save_file("docs", GOLDEN_SCRIPT_NAME, GOLDEN_SCRIPT,
                                 ws.carts_root)
        ok, why = ws.run_script("docs", GOLDEN_SCRIPT_NAME)
        assert ok, "the golden script did not run: " + why

    plan.append(("script_console", run_script))
    plan.append(("web_console", park))
    plan.append(("web_console_address", park_revealed))
    return plan


# The vault the Notes surfaces show: a note with each kind of line a note
# draws (a heading, a checkbox done and one not, a link), and a script.
NOTES_VAULT = (("story_ideas", "# Story ideas\n\n- [x] A dragon\n- [ ] A map\n\n"
                               "Read [[Dragon facts]] first.\n"),
               ("hello.py", "print('hi')\n"))


def _notes_vault(ws, enter):
    def run():
        for name, body in NOTES_VAULT:
            ws.carts_store.save_file("docs", name, body, ws.carts_root)
        enter()
    return run


def _walls_page(ws):
    """The Appearance page's wallpapers (docs/look_2026-10.md step 20): the
    tabs over the cards, on the tab of the wallpaper in use."""
    def enter():
        app = ws.appearance_app
        app.view = "walls"
        app.tab, title = app._wall_kind(app.choices())
        app._focus = "Wallpaper " + title
    return enter


def _name_page(ws):
    """The Appearance page on a `custom` made from Moy with one accent, its
    NAME dialog open with a name typed (§9 phase 6 step 8)."""
    def enter():
        app = ws.appearance_app
        ws.look.change("accent", _ui().theme_accents()[9][1])
        ws.look.tick(0.0)
        assert ws.look.theme_name == "custom"
        app._refresh()
        app._open_name()
        app.name_text = "bubble"
    return enter


def _after_appearance(ws, enter):
    """`enter`, after the surface before it is put back: the page on its rows,
    no dialog, and a `custom` the page made gone, the page on Moy with the
    theme in use focused, as it opens."""
    def go():
        app = ws.appearance_app
        app.view = "rows"
        app._close_name(acted=False)
        if ws.look.theme_name == "custom":
            ws.look.set_theme("moy")
            ws.carts_store.delete({"path": os.path.join(ws.carts_root,
                                                        "custom.moy")})
            ws.carts.rescan()
            app._refresh()
        app.open()
        enter()
    return go


# The game the pause menu holds, and the frames it plays first.
_PAUSE_CART, _PAUSE_FRAMES = "Star Catcher", 30
MENU_SURFACES = ("sysmenu", "pause", "pause_options")


def _menus_plan(ws, cfg):
    """The system menu over the home (or the desk), then the pause menu and
    its Options page over a game held after a fixed run: the seed's `rnd`
    is seeded, the frames' dt injected, so the held frame is the same."""
    import random

    def sysmenu():
        ws.go_home()
        if cfg["windowed"]:
            ws.open_desk()
        ws._golden_popover = True
        ws.system_key()
        assert ws.sysmenu.open and ws.menu_ui.looked()

    def pause(options):
        def enter():
            ws._golden_popover = False
            ws.sysmenu.close()
            ws.pause.close()
            ws.go_home()
            if cfg["windowed"]:
                ws.open_library()
            random.seed(25)
            ws._open_workspace(_cart_by_title(ws, _PAUSE_CART))
            ws.run(ws.project, ws.launcher_layer)
            for _ in range(_PAUSE_FRAMES):
                _quiesce(ws)
                ws.frame(_DT)
            ws.system_key()
            assert ws.pause.open
            if options:
                ws.pause.choose(2)
        return enter

    return [("sysmenu", sysmenu), ("pause", pause(False)), ("pause_options", pause(True))]


def _popover_plan(ws):
    """The desk with a Calc window, then each widget's popover over it. The
    popover stays open through the render (`_golden_popover`)."""
    def desk():
        ws.open_desk()
        ws.open_app(ws._apps_by_id["calc"])

    def popover(widget):
        def enter():
            ws.sysmenu.close()
            ws._golden_popover = True
            ws.widget_tap(widget)
            assert ws.sysmenu.open and ws.sysmenu.widget == widget, widget
        return enter

    plan = [("desk_window_calc", desk)]
    for w in WIDGETS:
        plan.append(("popover_" + w, popover(w)))
    return plan


def _home_plan(ws, cfg):
    """The home alone, one surface a tab -- a config that changes only its
    arrangement (the layout's `home`)."""
    def tab(t):
        def enter():
            ws.go_home()
            if cfg["windowed"]:
                ws.open_library()
            ws.set_home_tab(t)
        return enter

    return [("launcher" if t == "games" else "launcher_" + t, tab(t))
            for t in HOME_TAB_IDS]


def parts_app(ws, page):
    """The parts sheet's app on `ws`, registered, drawing `page`."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("parts_app", str(PARTS_APP))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    app = mod.PartsApp(ws, page)
    ws.register_app(app)
    return app


def _parts_plan(ws):
    """Each page of the parts sheet, its app opened fresh (its focus set)."""
    app = parts_app(ws, PARTS_PAGES[0])

    def enter(page):
        def go():
            app.page = page
            app.open()
            ws.wm.goto(app.id)
        return go
    return [("parts_" + p, enter(p)) for p in PARTS_PAGES]


def _widget_page_plan(ws):
    """Console mode's container for each widget, entered from the home: the
    calendar's own page, Settings at the WIFI panel, Settings at VOLUME, and
    the system menu (a popup in both modes) over the home."""
    def page(widget):
        def enter():
            ws.sysmenu.close()
            ws.go_home()
            ws._golden_popover = widget == "menu"
            ws.widget_tap(widget)
        return enter

    return [("page_" + w, page(w)) for w in WIDGETS]


def _open_get_carts(ws):
    """Get Carts over the carts repository's index snapshot (tests/fixtures/carts)
    through an in-memory transport, so its golden is the LIST a kid sees on a
    fresh console rather than the no-network notice. The store's free space is
    the one machine-dependent number on that screen, so it is pinned. The
    check runs to its end first: its busy page settles in pixels while the
    covers are still on their way, and the golden is the shelf."""
    def enter():
        ws.cart_net = MemNet({INDEX_URL: snapshot("index.json")})
        app = ws._apps_by_id["getcarts"]
        app._inst.free = lambda: (64 * 1048576, 4096)
        ws.open_app(app)
        for _ in range(20):
            if app.phase != "checking":
                break
            _quiesce(ws)
            ws._dirty = True
            ws.frame(_DT)
    return enter


def surface_names(config_name):
    """The surfaces a config covers -- the key set its goldens must carry."""
    cfg = CONFIGS[config_name]
    if cfg.get("plan") == "popovers":
        return ["desk_window_calc"] + ["popover_" + w for w in WIDGETS]
    if cfg.get("plan") == "widget_pages":
        return ["page_" + w for w in WIDGETS]
    if cfg.get("plan") == "home":
        return ["launcher" if t == "games" else "launcher_" + t for t in HOME_TAB_IDS]
    if cfg.get("plan") == "ui_page":
        return ["ui_page"]
    if cfg.get("plan") == "parts":
        return ["parts_" + p for p in PARTS_PAGES]
    if cfg.get("plan") == "menus":
        return list(MENU_SURFACES)
    names = []
    if cfg["windowed"] and not cfg.get("console"):
        names += ["desk", "desk_window_calc", "desk_window_projects"]
    names += ["launcher", "settings"]
    names += ["editor_" + t for t in TABS]
    for a in APPS:
        names.append("app_" + a)
        if a == "appearance" and cfg.get("appearance"):
            names += ["app_appearance_walls", "app_appearance_name"]
    names += ["script_console"]
    names += ["app_" + a for a, _title in APP_CARTS]
    names += ["app_notes_note"]
    names += ["web_console", "web_console_address"]
    return names


def _ui():
    from runtime import ui_draw
    return ui_draw


def capture(config_name, carts_dir):
    """{surface: sha256} for one configuration."""
    cfg = CONFIGS[config_name]
    ws = _build(cfg, carts_dir)
    expect_bytes = ws.glass_canvas.w * ws.glass_canvas.h * 2  # RGB565
    out = {}
    try:
        for name, enter in _surface_plan(ws, cfg):
            enter()
            assert len(bytes(ws.glass_canvas._buf)) == expect_bytes, (
                "%s/%s: the glass is %d bytes, expected %d (w*h*2)"
                % (config_name, name, len(bytes(ws.glass_canvas._buf)), expect_bytes))
            out[name] = _render(ws, name, config_name)
    finally:
        _ui().use_shape(None)          # the shape is process-wide: leave none behind
        _ui().theme_uninstall()        # ...as is the installed package
    return out


# ---------------------------------------------------------------------------
# the goldens file + the explicit re-baseline
# ---------------------------------------------------------------------------

def _load_goldens():
    if not GOLDEN_FILE.exists():
        raise AssertionError(
            "%s is missing. Baseline it deliberately:\n    %s"
            % (GOLDEN_FILE, REBASELINE_CMD))
    return json.loads(GOLDEN_FILE.read_text(encoding="utf-8"))


def _updating(request):
    if os.environ.get(UPDATE_ENV):
        return True
    # The pytest flag is registered in tests/conftest.py; `getoption` with a
    # default keeps this module importable without it.
    return bool(request.config.getoption("--update-goldens", default=False))


_CAPTURED = {}          # config -> {surface: hash}, filled only while updating


@pytest.fixture(scope="module", autouse=True)
def _write_goldens_on_teardown(request):
    """Write the re-baselined file ONCE, after the last test in this module.

    Per-test writes would race under xdist, so update mode refuses to run
    distributed (below) and accumulates into `_CAPTURED` instead."""
    yield
    if not _CAPTURED:
        return
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    stored = {}
    if GOLDEN_FILE.exists():
        stored = json.loads(GOLDEN_FILE.read_text(encoding="utf-8"))
    stored.update(_CAPTURED)
    if set(_CAPTURED) == set(CONFIGS):
        # A FULL re-baseline may prune configs that no longer exist; a partial
        # one (`-k tdeck`) must never delete the rows it did not render.
        stored = {k: v for k, v in stored.items() if k in CONFIGS}
    GOLDEN_FILE.write_text(
        json.dumps(stored, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    counts = json.loads(COUNTS_FILE.read_text(encoding="utf-8")) \
        if COUNTS_FILE.exists() else {}
    for config in _CAPTURED:
        counts[config] = {s: list(_DRAWN[(config, s)]) for s in _CAPTURED[config]}
    counts = {k: v for k, v in counts.items() if k in stored}
    COUNTS_FILE.write_text("{\n%s\n}\n" % ",\n".join(
        '  "%s": {\n%s\n  }' % (c, ",\n".join(
            '    "%s": [%d, %d]' % (k, v[0], v[1]) for k, v in sorted(counts[c].items())))
        for c in sorted(counts)), encoding="utf-8")


# ---------------------------------------------------------------------------
# the tests
# ---------------------------------------------------------------------------

def test_every_cover_builds_in_its_own_frame_under_test():
    """The pin the goldens stand on is the suite's, in force for every test."""
    from runtime import cover_cache
    assert cover_cache._COVER_SLICE_MS >= _COVER_SLICE_UNBOUNDED


def _hits_peak(monkeypatch):
    """The most rects one `ui_draw.Hits` holds at once while the goldens render."""
    from runtime import ui_draw
    peak = [0]
    add = ui_draw.Hits.add

    def counting(self, rect, verb, arg=None):
        add(self, rect, verb, arg)
        if len(self._items) > peak[0]:
            peak[0] = len(self._items)

    monkeypatch.setattr(ui_draw.Hits, "add", counting)
    return peak


@pytest.mark.parametrize("config_name", sorted(CONFIGS))
def test_shell_surfaces_match_goldens(config_name, tmp_path, request,
                                     monkeypatch):
    peak = _hits_peak(monkeypatch)
    from runtime import ui
    from runtime import ui_draw
    del ui_draw.primary_warnings[:]
    ui_draw._PRIMARY_WARNED.clear()
    got = capture(config_name, tmp_path / "carts")
    assert ui_draw.primary_warnings == [], (
        "a surface draws more than one primary button in a frame (DESIGN.md, "
        "'Button kinds'): %r" % ui_draw.primary_warnings)
    many = {s: n for (c, s), n in _MARKED.items() if c == config_name and n > 1}
    assert not many, (
        "a frame draws more than one focus mark (DESIGN.md, the marks: "
        "exactly one a screen, never in a window behind): %r" % many)
    cap = json.loads((ROOT / "native" / "moy_ui" / "style.json").read_text())[
        "hits_capacity"]
    # (since Notes moved onto the verbs no golden surface registers any:
    # the engine's stops are the verbs' hit map)
    assert 2 * peak[0] <= cap, (
        "a surface registers %d hit rects in the goldens' frames; the hit "
        "buffer's capacity (style.json's hits_capacity, %d) is twice the most "
        "any surface registers (docs/kernel_toolkit_2026-10.md §3.4)"
        % (peak[0], cap))

    if _updating(request):
        assert not hasattr(request.config, "workerinput"), (
            "re-baselining under xdist would have every worker rewrite the "
            "goldens file. Run it serially:\n    " + REBASELINE_CMD)
        _CAPTURED[config_name] = got
        pytest.skip("re-baselining %s (%d surfaces)" % (config_name, len(got)))

    stored = _load_goldens()
    golden = stored.get(config_name)
    assert golden is not None, (
        "no goldens stored for config %r (axes: %s). Baseline it:\n    %s"
        % (config_name, _axes(CONFIGS[config_name]), REBASELINE_CMD))

    moved = []
    for surface in sorted(set(got) | set(golden)):
        want = golden.get(surface)
        have = got.get(surface)
        if want != have:
            moved.append((surface, want, have))
    if moved:
        lines = [
            "SHELL PIXELS MOVED -- config %r" % config_name,
            "  axes: %s" % _axes(CONFIGS[config_name]),
            "  %d surface(s) changed:" % len(moved),
        ]
        for surface, want, have in moved:
            lines.append("    %-20s golden=%s  rendered=%s"
                         % (surface,
                            (want[:16] + "..") if want else "<absent>",
                            (have[:16] + "..") if have else "<not rendered>"))
        lines.append("")
        lines.append("  Each line is one shell surface whose rendered system "
                     "canvas no longer hashes to the bytes stored in")
        lines.append("  %s." % GOLDEN_FILE.relative_to(ROOT))
        lines.append("  If the change is INTENDED and you can say which pixel "
                     "moved and why, re-baseline deliberately:")
        lines.append("    " + REBASELINE_CMD)
        raise AssertionError("\n".join(lines))

    pinned = json.loads(COUNTS_FILE.read_text(encoding="utf-8")).get(config_name, {})
    drawn = {s: list(_DRAWN[(config_name, s)]) for s in got}
    off = ["    %-24s pinned %s, drew %s (%s)"
           % (s, pinned.get(s), drawn[s],
              "DEARER" if pinned.get(s) is None or any(
                  d > p for d, p in zip(drawn[s], pinned[s])) else "cheaper")
           for s in sorted(drawn) if drawn[s] != pinned.get(s)]
    assert not off, (
        "DRAW BUDGET MOVED -- config %r, (fills, texts) per settled frame:\n%s\n"
        "  Same pixels, different work. A cheaper draw is re-pinned; a dearer "
        "one needs a reason first. Re-pin with:\n    %s"
        % (config_name, "\n".join(off), REBASELINE_CMD))


def test_goldens_file_covers_exactly_the_matrix(request):
    """No stale rows, no missing rows -- a deleted surface or a renamed config
    has to be re-baselined rather than quietly left behind in the file."""
    if _updating(request):
        pytest.skip("re-baselining: the file is rewritten at module teardown")
    stored = _load_goldens()
    assert sorted(stored) == sorted(CONFIGS), (
        "stored configs %s != matrix %s -- re-baseline:\n    %s"
        % (sorted(stored), sorted(CONFIGS), REBASELINE_CMD))
    for config_name in CONFIGS:
        assert sorted(stored[config_name]) == sorted(surface_names(config_name)), (
            "config %r: stored surfaces %s != expected %s -- re-baseline:\n    %s"
            % (config_name, sorted(stored[config_name]),
               sorted(surface_names(config_name)), REBASELINE_CMD))
        for surface, digest in stored[config_name].items():
            assert isinstance(digest, str) and len(digest) == 64, (
                "%s/%s: %r is not a sha256 hex digest"
                % (config_name, surface, digest))


def test_every_axis_is_actually_exercised():
    """The matrix must MOVE each axis, not merely name it. A config list that
    silently collapsed onto one geometry would keep every golden green while
    covering nothing."""
    sizes = {(c["sys_size"] or (320, 240)) for c in CONFIGS.values()}
    assert (320, 240) in sizes and (480, 320) in sizes
    assert {c["font_scale"] for c in CONFIGS.values()} >= {1, 2, 3}
    assert {c["variant"] for c in CONFIGS.values()} == {"dark", "light"}
    assert {c["windowed"] for c in CONFIGS.values()} == {True, False}
    assert {bool(c["diagonal_in"]) for c in CONFIGS.values()} == {True, False}
    from runtime.layout_base import class_of
    assert {class_of(c["diagonal_in"]) for c in CONFIGS.values()} == {
        "handheld", "desk"}
    assert {c.get("render", 1) for c in CONFIGS.values()} == {1, 2}


# A running APP CART on the fixed 320x240 GAME raster would draw its minimal
# bar there too -- `bar_layer._bar_canvas("tool")` IS `ws.canvas` and its
# height the flat `_STATUS_H` -- and hash the same on both sides of the
# declared diagonal, #203 working rather than missing. No golden surface does
# since Notes, the one app cart here, became responsive (it takes the whole
# surface and its bar moves with the diagonal). Asserted BOTH ways below, so
# an exemption that stops being true goes red instead of widening.
TAP_FLOOR_EXEMPT = frozenset()


def test_the_tap_floor_moves_chrome_and_leaves_text_alone(request):
    """#203's whole claim, as pixels. Two configs differ ONLY by the declared
    panel diagonal, so every surface that draws OS chrome must differ -- and the
    surfaces that draw none must not."""
    if _updating(request):
        pytest.skip("re-baselining: the file is rewritten at module teardown")
    stored = _load_goldens()
    plain = stored["guition_480x320_fs1_dark"]
    floored = stored["guition_tapfloor_dark"]
    same = [s for s in plain
            if s not in TAP_FLOOR_EXEMPT and plain[s] == floored.get(s)]
    assert not same, (
        "these surfaces hash IDENTICALLY with and without the tap-target "
        "floor, i.e. the declared diagonal reached nothing: %s" % sorted(same))
    moved = [s for s in TAP_FLOOR_EXEMPT if plain[s] != floored.get(s)]
    assert not moved, (
        "these surfaces are exempt because they draw on the scaled GAME "
        "raster, and they moved with the diagonal: %s" % sorted(moved))


def test_the_light_variant_really_draws_different_pixels(request):
    """The blind spot this harness was built for: `variant="light"` appeared
    five times in the suite and was never followed by a draw, so a light panel
    could have rendered black-on-black forever. Two configs differ ONLY by the
    variant, so every surface that reads a theme token must differ."""
    if _updating(request):
        pytest.skip("re-baselining: the file is rewritten at module teardown")
    stored = _load_goldens()
    dark = stored["tdeck_320x240_fs1_dark"]
    light = stored["tdeck_320x240_fs1_light"]
    same = [s for s in dark if dark[s] == light.get(s)]
    assert not same, (
        "these surfaces hash IDENTICALLY in dark and light, i.e. they ignore "
        "the theme variant: %s" % sorted(same))


def test_editor_tabs_render_independently(tmp_path):
    """An Editor tab renders the same pixels whatever tab was rendered before it.

    `_surface_plan` re-opens the Editor per tab and its comment claims that buys
    independence. It did not (#216): the Blocks+Scene workspace binds
    `ws.scene_ui.layout` to its right pane and nothing unbound it, so the Scene tab
    entered afterwards drew into that pane -- at this config a rect of NEGATIVE
    width, off the right edge, leaving the blocks pixels standing underneath. One
    golden was therefore a function of the surface before it, and an unrelated
    map-editor change moved `editor_scene` with a re-baseline reason that looked
    wrong.

    Every ORDERED PAIR, at the config where it showed. font_scale 3 is what
    collapses the workspace panes; at 320x240 the workspace is off, so no
    editor is ever bounded there. The pairs share one workstation, so
    the state also ACCUMULATES across them -- a stronger claim than pairwise, for
    the price of one build."""
    cfg = CONFIGS[_INDEPENDENCE_CONFIG]
    alone = {tab: _editor_tab_hash(_build(cfg, tmp_path / ("alone_" + tab)), tab)
             for tab in TABS}
    ws = _build(cfg, tmp_path / "pairs")
    leaked = []
    for before in TABS:
        for tab in TABS:
            if tab == before:
                continue
            _editor_tab_hash(ws, before)
            if _editor_tab_hash(ws, tab) != alone[tab]:
                leaked.append("%s after %s" % (tab, before))
    assert not leaked, (
        "these Editor tabs render DIFFERENT pixels depending on which tab was "
        "rendered before them (config %r, cart %r): %s. A tab must re-derive its "
        "own state on entry -- see EditorApp._relayout_tab."
        % (_INDEPENDENCE_CONFIG, _EDITOR_CART, leaked))


def test_tab_ladder_is_fully_covered():
    """Every real Editor tab has a golden (the sentinel PROJECTS/UNDO/REDO/PLAY
    zone entries are actions, not tabs)."""
    from runtime import editor_app
    live = tuple(t for t, _glyph in editor_app._ZONE_TABS
                 if t and not t.startswith("\x00"))
    assert live == TABS, (
        "the Editor tab ladder changed (%s) -- add the new tab here and "
        "re-baseline:\n    %s" % (list(live), REBASELINE_CMD))


def test_every_registered_app_is_covered(tmp_path):
    """Every registered system app has a golden. A new app that skips this
    list is a coverage hole the refactor would not see."""
    ws = _build(CONFIGS["tdeck_320x240_fs1_dark"], tmp_path / "carts")
    assert sorted(ws._apps_by_id) == sorted(APPS), (
        "the system-app registry changed (%s) -- add the new app here and "
        "re-baseline:\n    %s" % (sorted(ws._apps_by_id), REBASELINE_CMD))


def test_every_app_cart_is_covered(tmp_path):
    """The other half: an app that is a CART is claimed by no registry, so its
    coverage is pinned against the seeded store."""
    ws = _build(CONFIGS["tdeck_320x240_fs1_dark"], tmp_path / "carts")
    carts = {c.get("title") for c in ws.carts.all if ws.is_user_app(c)}
    assert carts == {title for _s, title in APP_CARTS}, (
        "the app-cart roster changed (%s) -- add it to APP_CARTS and "
        "re-baseline:\n    %s" % (sorted(carts), REBASELINE_CMD))
